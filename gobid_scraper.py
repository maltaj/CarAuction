#!/usr/bin/env python3
"""
GoBid Auction Screening Tool
=============================
Logs into gobid.co.za, scrapes current vehicle auction listings, stores them
in a local SQLite database + CSV, and emails an alert when NEW listings
appear since the last run.

This tool is READ-ONLY. It never places bids, never submits any form other
than the login form, and never clicks anything on a listing page beyond
"view details". It exists purely to help you screen deals manually.

Intended usage: run once per cron / Task Scheduler trigger (e.g. every
30-60 minutes). Do NOT loop/sleep inside the script itself -- let the OS
scheduler own the cadence. See README.md for setup.

-------------------------------------------------------------------------
IMPORTANT - SELECTORS ARE BEST-EFFORT PLACEHOLDERS
-------------------------------------------------------------------------
gobid.co.za blocks naive HTTP fetches (bot detection), so this was built
using Playwright (a real headless browser) plus publicly cached snippets
of the search-results text -- NOT a live inspection of the page's actual
HTML/CSS structure. That means the CSS selectors marked "ADJUST ME" below
are educated guesses and will very likely need correcting once you run
this against the real site.

Use `python gobid_scraper.py --debug-html` to save the fully-rendered
HTML of a search results page and one listing detail page to
./debug_output/. Open those files, find the real selectors (browser
devtools -> right click element -> Inspect), and update the CONFIG
section below. See README.md "Calibrating selectors" for a walkthrough.
-------------------------------------------------------------------------
"""

import argparse
import csv
import logging
import smtplib
import sqlite3
import sys
import time
from dataclasses import dataclass, field, fields
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path
from typing import Optional

import yaml
from playwright.sync_api import sync_playwright, Page, TimeoutError as PlaywrightTimeout

BASE_DIR = Path(__file__).parent.resolve()
STATE_FILE = BASE_DIR / "storage_state.json"   # saved login session (cookies)
DB_FILE = BASE_DIR / "gobid_listings.db"
CSV_LATEST = BASE_DIR / "output" / "latest_listings.csv"
CSV_SNAPSHOT_DIR = BASE_DIR / "output" / "snapshots"
DEBUG_DIR = BASE_DIR / "debug_output"
LOG_FILE = BASE_DIR / "gobid_scraper.log"

BASE_URL = "https://www.gobid.co.za"
LOGIN_URL = f"{BASE_URL}/login"          # ADJUST ME if the real login path differs

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.FileHandler(LOG_FILE), logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger("gobid")


# ============================================================================
# Data model
# ============================================================================

@dataclass
class Listing:
    lot_id: str                      # unique identifier -- required for dedupe
    url: str
    title: str = ""                  # e.g. "2018 Toyota Hilux 2.4 GD-6"
    year: Optional[str] = None
    make: Optional[str] = None
    model: Optional[str] = None
    variant: Optional[str] = None
    vin: Optional[str] = None
    mileage: Optional[str] = None
    damage_code: Optional[str] = None       # e.g. "Code 3A" (SA damage/title code)
    primary_damage: Optional[str] = None
    secondary_damage: Optional[str] = None
    run_and_drive: Optional[str] = None     # Yes / No / Unknown
    keys_available: Optional[str] = None    # Yes / No / Unknown
    odometer_status: Optional[str] = None   # Actual / Not Actual / Exempt / Unknown
    branch: Optional[str] = None            # auction branch/location
    province: Optional[str] = None
    auction_type: Optional[str] = None      # Live / Timed
    auction_date: Optional[str] = None
    current_bid: Optional[str] = None
    starting_bid: Optional[str] = None
    bid_count: Optional[str] = None
    reserve_status: Optional[str] = None    # Reserve met / No reserve / Unknown
    watchers: Optional[str] = None
    estimated_value: Optional[str] = None
    photo_urls: str = ""                    # semicolon-joined list
    description: Optional[str] = None
    risk_score: Optional[float] = None      # lower = lower risk. See score_listing().
    risk_label: Optional[str] = None        # "Low Risk" / "Medium Risk" / "High Risk"
    risk_reasons: Optional[str] = None      # semicolon-joined human-readable explanation
    first_seen: str = field(default_factory=lambda: datetime.now().isoformat(timespec="seconds"))
    last_seen: str = field(default_factory=lambda: datetime.now().isoformat(timespec="seconds"))
    is_new: bool = False


LISTING_FIELDS = [f.name for f in fields(Listing)]


# ============================================================================
# Config
# ============================================================================

def load_config(path: Path) -> dict:
    if not path.exists():
        log.error(f"Config file not found: {path}. Copy config.example.yaml to config.yaml and fill it in.")
        sys.exit(1)
    with open(path) as f:
        cfg = yaml.safe_load(f)
    required = ["username", "password", "search_urls"]
    missing = [k for k in required if not cfg.get(k)]
    if missing:
        log.error(f"Config missing required keys: {missing}")
        sys.exit(1)
    return cfg


# ============================================================================
# Login
# ============================================================================

def login(page: Page, username: str, password: str) -> bool:
    """
    Logs into gobid.co.za. Reuses a saved session (storage_state.json) when
    possible so we don't hit the login form on every single cron run, which
    is both faster and less likely to trip bot detection.
    """
    page.goto(BASE_URL, wait_until="domcontentloaded")

    # Quick check: are we already logged in via restored session?
    # ADJUST ME: replace with a selector that only appears when logged in,
    # e.g. an account/profile menu link.
    if page.locator("text=My Account").count() > 0 or page.locator("text=Logout").count() > 0:
        log.info("Restored session is still valid, skipping login form.")
        return True

    log.info("Session not valid / not present, logging in fresh.")
    page.goto(LOGIN_URL, wait_until="domcontentloaded")

    try:
        # ADJUST ME: these selectors are guesses. Common patterns for this
        # kind of site: input[name="email"] / input[type="email"], and
        # input[name="password"] / input[type="password"].
        page.fill('input[type="email"], input[name="email"], input[name="username"]', username)
        page.fill('input[type="password"], input[name="password"]', password)
        page.click('button[type="submit"], button:has-text("Log in"), button:has-text("Login")')
        page.wait_for_load_state("networkidle", timeout=15000)
    except PlaywrightTimeout:
        log.error("Timed out interacting with login form. Selectors likely need adjusting -- "
                   "run with --debug-html to capture the login page.")
        return False

    if page.locator("text=Logout").count() > 0 or page.locator("text=My Account").count() > 0:
        log.info("Login successful.")
        page.context.storage_state(path=str(STATE_FILE))
        return True

    log.error("Login does not appear to have succeeded. Check credentials and/or selectors.")
    return False


# ============================================================================
# Scraping
# ============================================================================

def text_or_none(locator) -> Optional[str]:
    try:
        if locator.count() == 0:
            return None
        t = locator.first.inner_text().strip()
        return t or None
    except Exception:
        return None


def scrape_search_results(page: Page, search_url: str, max_pages: int, delay_seconds: float) -> list[Listing]:
    """
    Paginates through a search-results URL (e.g. https://www.gobid.co.za/search?ac=1)
    and extracts one Listing per result card.

    ADJUST ME: the card/field selectors below are placeholders based on the
    text patterns visible in cached search snippets (lot numbers, "Code 3A",
    "Starting Bid R...", branch names). They need verification against the
    real rendered HTML -- see README.md "Calibrating selectors".
    """
    listings: list[Listing] = []
    page_num = 1

    while page_num <= max_pages:
        url = f"{search_url}{'&' if '?' in search_url else '?'}p={page_num}"
        log.info(f"Fetching search page {page_num}: {url}")
        page.goto(url, wait_until="networkidle")
        time.sleep(delay_seconds)  # be polite / reduce bot-detection risk

        # ADJUST ME: the repeating listing-card container selector.
        cards = page.locator("[class*='listing-card'], [class*='vehicle-card'], [class*='search-result']")
        count = cards.count()
        log.info(f"  Found {count} listing cards on page {page_num}")

        if count == 0:
            log.info("  No more cards found, stopping pagination.")
            break

        for i in range(count):
            card = cards.nth(i)
            try:
                listing = parse_card(card)
                if listing:
                    listings.append(listing)
            except Exception as e:
                log.warning(f"  Failed to parse card {i} on page {page_num}: {e}")

        page_num += 1

    return listings


def parse_card(card) -> Optional[Listing]:
    """Extract fields from a single search-result card element."""
    # ADJUST ME throughout this function -- every selector is a guess.

    link = card.locator("a").first
    href = link.get_attribute("href") if link.count() > 0 else None
    if not href:
        return None
    url = href if href.startswith("http") else f"{BASE_URL}{href}"

    # Try to pull a lot id out of the URL (?lot=1234 or /lot/1234 style);
    # fall back to the full URL as the unique key if that fails.
    lot_id = url.rstrip("/").split("/")[-1].split("?")[0] or url

    title = text_or_none(card.locator("[class*='title'], h2, h3"))
    branch = text_or_none(card.locator("[class*='branch'], [class*='location']"))
    damage_code = text_or_none(card.locator("text=/Code \\d/"))
    current_bid = text_or_none(card.locator("text=/Current Bid/i"))
    starting_bid = text_or_none(card.locator("text=/Starting Bid/i"))
    auction_date = text_or_none(card.locator("[class*='date'], [class*='auction-date']"))
    bid_count = text_or_none(card.locator("[class*='bid-count']"))

    return Listing(
        lot_id=lot_id,
        url=url,
        title=title or "",
        branch=branch,
        damage_code=damage_code,
        current_bid=current_bid,
        starting_bid=starting_bid,
        auction_date=auction_date,
        bid_count=bid_count,
    )


def scrape_listing_detail(page: Page, listing: Listing, delay_seconds: float) -> Listing:
    """
    Visits a single listing's detail page to fill in the richer fields
    (mileage, VIN, damage description, run & drive, keys, photos, etc.)
    that usually aren't on the search-results card.

    ADJUST ME: every selector below is a placeholder. Run --debug-html
    against one real listing URL and match these up to the real markup.
    """
    log.info(f"  Fetching detail page for lot {listing.lot_id}")
    page.goto(listing.url, wait_until="networkidle")
    time.sleep(delay_seconds)

    def field(label_regex: str) -> Optional[str]:
        # Generic "label: value" row finder -- many auction sites lay out
        # specs as a definition list or label/value pair rows.
        row = page.locator(f"xpath=//*[contains(text(), '{label_regex}')]/following-sibling::*[1]")
        return text_or_none(row)

    listing.vin = field("VIN")
    listing.mileage = field("Mileage") or field("Odometer")
    listing.odometer_status = field("Odometer Status")
    listing.primary_damage = field("Primary Damage")
    listing.secondary_damage = field("Secondary Damage")
    listing.run_and_drive = field("Run and Drive") or field("Runs and Drives")
    listing.keys_available = field("Keys")
    listing.estimated_value = field("Estimated") or field("Retail Value")
    listing.description = text_or_none(page.locator("[class*='description']"))

    photo_els = page.locator("img[class*='gallery'], img[class*='photo']")
    urls = []
    for i in range(min(photo_els.count(), 30)):
        src = photo_els.nth(i).get_attribute("src")
        if src:
            urls.append(src if src.startswith("http") else f"{BASE_URL}{src}")
    listing.photo_urls = ";".join(urls)

    return listing


def dump_debug_html(page: Page, search_url: str):
    """Save rendered HTML of the search page and (if reachable) the first
    listing's detail page, for manual selector calibration."""
    DEBUG_DIR.mkdir(exist_ok=True, parents=True)

    page.goto(search_url, wait_until="networkidle")
    (DEBUG_DIR / "search_page.html").write_text(page.content())
    page.screenshot(path=str(DEBUG_DIR / "search_page.png"), full_page=True)
    log.info(f"Saved search page HTML/screenshot to {DEBUG_DIR}")

    first_link = page.locator("a[href*='lot'], a[href*='vehicle']").first
    if first_link.count() > 0:
        href = first_link.get_attribute("href")
        detail_url = href if href.startswith("http") else f"{BASE_URL}{href}"
        page.goto(detail_url, wait_until="networkidle")
        (DEBUG_DIR / "detail_page.html").write_text(page.content())
        page.screenshot(path=str(DEBUG_DIR / "detail_page.png"), full_page=True)
        log.info(f"Saved detail page HTML/screenshot to {DEBUG_DIR}")
    else:
        log.warning("Could not find a listing link on the search page to dump a detail page.")


# ============================================================================
# Risk scoring
# ============================================================================
#
# This is a transparent, rule-based scorer -- not a machine-learning model
# and not a guarantee about any specific car. It just totals up points from
# config-defined rules so you can see *why* a lot scored the way it did
# (risk_reasons) and tune the rules yourself in config.yaml.
#
# Convention: higher score = higher risk. Most rules are 0 by default,
# meaning "this signal doesn't move the score until you configure it" --
# fill in config.yaml's `scoring` section with values that reflect how you
# actually judge deals (e.g. a flood/fire keyword might be worth +15 to you,
# or might rule the car out entirely regardless of everything else).

def _norm(s: Optional[str]) -> str:
    return (s or "").strip().lower()


def _parse_number(s: Optional[str]) -> Optional[float]:
    """Pulls the first number out of strings like 'R 45 000' or '123,456 km'."""
    if not s:
        return None
    digits = "".join(ch for ch in s if ch.isdigit() or ch == ".")
    try:
        return float(digits) if digits else None
    except ValueError:
        return None


def _yn_rules(section: dict) -> dict:
    """YAML reads unquoted yes/no keys as booleans True/False. Accept both
    forms so `yes: -5` and `"yes": -5` in config.yaml behave the same."""
    out = dict(section or {})
    if True in out and "yes" not in out:
        out["yes"] = out[True]
    if False in out and "no" not in out:
        out["no"] = out[False]
    return out


def score_listing(listing: Listing, rules: dict) -> Listing:
    if not rules.get("enabled", False):
        return listing

    score = 0.0
    reasons: list[str] = []

    def add(points: float, reason: str):
        nonlocal score
        if points:
            score += points
            reasons.append(f"{reason} ({points:+g})")

    # --- run and drive ---
    rad_rules = _yn_rules(rules.get("run_and_drive", {}))
    rad = _norm(listing.run_and_drive)
    if rad in ("yes", "y", "true"):
        add(rad_rules.get("yes", 0), "Run & drive: yes")
    elif rad in ("no", "n", "false"):
        add(rad_rules.get("no", 0), "Run & drive: no")
    else:
        add(rad_rules.get("unknown", 0), "Run & drive: unknown")

    # --- keys available ---
    keys_rules = _yn_rules(rules.get("keys_available", {}))
    keys = _norm(listing.keys_available)
    if keys in ("yes", "y", "true"):
        add(keys_rules.get("yes", 0), "Keys available: yes")
    elif keys in ("no", "n", "false"):
        add(keys_rules.get("no", 0), "Keys available: no")
    else:
        add(keys_rules.get("unknown", 0), "Keys available: unknown")

    # --- odometer status ---
    odo_rules = rules.get("odometer_status", {})
    odo = _norm(listing.odometer_status)
    if "not actual" in odo:
        add(odo_rules.get("not_actual", 0), "Odometer: not actual")
    elif "exempt" in odo:
        add(odo_rules.get("exempt", 0), "Odometer: exempt")
    elif "actual" in odo:
        add(odo_rules.get("actual", 0), "Odometer: actual")
    else:
        add(odo_rules.get("unknown", 0), "Odometer: unknown")

    # --- damage / condition code (SA Code 1-5 system) ---
    code_points = rules.get("damage_code_points", {})
    code = _norm(listing.damage_code)
    matched_code = False
    for code_key, pts in code_points.items():
        if _norm(code_key) in code:
            add(pts, f"Damage code matches '{code_key}'")
            matched_code = True
            break
    if not matched_code and code:
        add(rules.get("damage_code_unknown_points", 0), f"Damage code '{listing.damage_code}' not in rules")

    # --- keyword scan over primary/secondary damage + description ---
    damage_text = " ".join(_norm(x) for x in (listing.primary_damage, listing.secondary_damage, listing.description))
    kw_rules = rules.get("damage_keywords", {})
    for category, cat_rules in kw_rules.items():
        terms = cat_rules.get("terms", [])
        points = cat_rules.get("points", 0)
        hits = [t for t in terms if t.lower() in damage_text]
        if hits:
            add(points, f"Damage mentions {category} term(s): {', '.join(hits)}")

    # --- mileage thresholds ---
    mileage_rules = rules.get("mileage_thresholds", {})
    mileage = _parse_number(listing.mileage)
    if mileage is not None:
        low_max = mileage_rules.get("low_max")
        high_min = mileage_rules.get("high_min")
        if low_max is not None and mileage <= low_max:
            add(mileage_rules.get("low_points", 0), f"Mileage {mileage:.0f} <= {low_max:.0f}")
        elif high_min is not None and mileage >= high_min:
            add(mileage_rules.get("high_points", 0), f"Mileage {mileage:.0f} >= {high_min:.0f}")

    # --- bid vs estimated value, if GoBid actually publishes an estimate ---
    est_value = _parse_number(listing.estimated_value)
    bid = _parse_number(listing.current_bid) or _parse_number(listing.starting_bid)
    ratio_rules = rules.get("bid_to_value_ratio", {})
    if est_value and bid and est_value > 0:
        ratio = bid / est_value
        threshold = ratio_rules.get("high_ratio_threshold")
        if threshold is not None and ratio >= threshold:
            add(ratio_rules.get("high_ratio_points", 0),
                f"Bid is {ratio:.0%} of estimated value (>= {threshold:.0%})")

    # --- final label ---
    thresholds = rules.get("thresholds", {"low_max": 0, "medium_max": 6})
    if score <= thresholds.get("low_max", 0):
        label = "Low Risk"
    elif score <= thresholds.get("medium_max", 6):
        label = "Medium Risk"
    else:
        label = "High Risk"

    listing.risk_score = round(score, 1)
    listing.risk_label = label
    listing.risk_reasons = "; ".join(reasons) if reasons else "No scoring signals matched"
    return listing


# ============================================================================
# Storage: SQLite + CSV
# ============================================================================

def init_db(conn: sqlite3.Connection):
    cols_sql = ", ".join(f'"{f}" TEXT' for f in LISTING_FIELDS if f != "lot_id")
    conn.execute(f'''
        CREATE TABLE IF NOT EXISTS listings (
            lot_id TEXT PRIMARY KEY,
            {cols_sql}
        )
    ''')
    conn.commit()
    migrate_db(conn)


def migrate_db(conn: sqlite3.Connection):
    """Adds any newly-introduced Listing columns (e.g. risk_score) to an
    existing database from an earlier version of this script, so you don't
    have to delete gobid_listings.db when the schema grows."""
    cur = conn.cursor()
    cur.execute("PRAGMA table_info(listings)")
    existing_cols = {row[1] for row in cur.fetchall()}
    added = []
    for f in LISTING_FIELDS:
        if f not in existing_cols:
            cur.execute(f'ALTER TABLE listings ADD COLUMN "{f}" TEXT')
            added.append(f)
    if added:
        log.info(f"Migrated database, added columns: {added}")
    conn.commit()


def upsert_listings(conn: sqlite3.Connection, listings: list[Listing]) -> list[Listing]:
    """
    Inserts new listings, updates existing ones (refreshing bid/price
    fields and last_seen), and marks which ones are new-this-run.
    Returns the list of listings that are new since the last run.
    """
    cur = conn.cursor()
    new_listings = []

    for listing in listings:
        cur.execute("SELECT lot_id, first_seen FROM listings WHERE lot_id = ?", (listing.lot_id,))
        row = cur.fetchone()

        if row is None:
            listing.is_new = True
            new_listings.append(listing)
            placeholders = ", ".join("?" for _ in LISTING_FIELDS)
            col_names = ", ".join(f'"{f}"' for f in LISTING_FIELDS)
            values = [str(getattr(listing, f)) if not isinstance(getattr(listing, f), bool) else str(getattr(listing, f))
                      for f in LISTING_FIELDS]
            cur.execute(f'INSERT INTO listings ({col_names}) VALUES ({placeholders})', values)
        else:
            listing.first_seen = row[1]
            listing.is_new = False
            set_clause = ", ".join(f'"{f}" = ?' for f in LISTING_FIELDS if f != "lot_id")
            values = [str(getattr(listing, f)) for f in LISTING_FIELDS if f != "lot_id"]
            values.append(listing.lot_id)
            cur.execute(f'UPDATE listings SET {set_clause} WHERE lot_id = ?', values)

    conn.commit()
    return new_listings


def write_csv(listings: list[Listing]):
    CSV_LATEST.parent.mkdir(exist_ok=True, parents=True)
    CSV_SNAPSHOT_DIR.mkdir(exist_ok=True, parents=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    snapshot_path = CSV_SNAPSHOT_DIR / f"listings_{timestamp}.csv"

    for path in (CSV_LATEST, snapshot_path):
        with open(path, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=LISTING_FIELDS)
            writer.writeheader()
            for listing in listings:
                writer.writerow({f: getattr(listing, f) for f in LISTING_FIELDS})

    log.info(f"Wrote CSV: {CSV_LATEST} and snapshot {snapshot_path}")


# ============================================================================
# Email alert
# ============================================================================

def send_email_alert(cfg: dict, new_listings: list[Listing]):
    if not new_listings:
        log.info("No new listings, skipping email.")
        return

    email_cfg = cfg.get("email", {})
    if not email_cfg.get("enabled", False):
        log.info("Email alerts disabled in config, skipping.")
        return

    # Optionally only alert on listings at or below a given risk level, e.g.
    # config: email.alert_risk_labels: ["Low Risk", "Medium Risk"]
    # If scoring is off, or a listing has no risk_label, it's always included.
    allowed_labels = email_cfg.get("alert_risk_labels")
    if allowed_labels:
        to_alert = [l for l in new_listings if l.risk_label is None or l.risk_label in allowed_labels]
    else:
        to_alert = new_listings

    if not to_alert:
        log.info("New listings exist but none meet alert_risk_labels, skipping email.")
        return

    # Lowest risk first
    to_alert.sort(key=lambda l: (l.risk_score is None, l.risk_score))

    subject = f"GoBid: {len(to_alert)} new listing(s) found"
    if allowed_labels:
        subject += f" (filtered to {', '.join(allowed_labels)})"

    lines = [f"{len(to_alert)} new listing(s) since the last check "
             f"(of {len(new_listings)} new total):\n"]
    for l in to_alert:
        risk_bit = f" | {l.risk_label} (score {l.risk_score})" if l.risk_label else ""
        lines.append(
            f"- {l.title or l.lot_id} | {l.damage_code or 'code unknown'} | "
            f"{l.branch or 'branch unknown'} | {l.current_bid or l.starting_bid or 'bid unknown'}"
            f"{risk_bit} | {l.url}"
        )
        if l.risk_reasons:
            lines.append(f"    reasons: {l.risk_reasons}")
    lines.append(f"\nFull CSV: {CSV_LATEST}")
    body = "\n".join(lines)

    msg = MIMEMultipart()
    msg["From"] = email_cfg["from_address"]
    msg["To"] = ", ".join(email_cfg["to_addresses"])
    msg["Subject"] = subject
    msg.attach(MIMEText(body, "plain"))

    try:
        with smtplib.SMTP(email_cfg["smtp_host"], email_cfg.get("smtp_port", 587)) as server:
            server.starttls()
            server.login(email_cfg["smtp_username"], email_cfg["smtp_password"])
            server.sendmail(email_cfg["from_address"], email_cfg["to_addresses"], msg.as_string())
        log.info(f"Sent email alert for {len(to_alert)} new listing(s).")
    except Exception as e:
        log.error(f"Failed to send email alert: {e}")


# ============================================================================
# Main
# ============================================================================

def run(cfg: dict, debug_html: bool):
    conn = sqlite3.connect(DB_FILE)
    init_db(conn)

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=cfg.get("headless", True))
        context_kwargs = {}
        if STATE_FILE.exists():
            context_kwargs["storage_state"] = str(STATE_FILE)
        context = browser.new_context(**context_kwargs)
        page = context.new_page()

        if debug_html:
            dump_debug_html(page, cfg["search_urls"][0])
            browser.close()
            return

        if not login(page, cfg["username"], cfg["password"]):
            log.error("Aborting run: login failed.")
            browser.close()
            sys.exit(1)

        all_listings: list[Listing] = []
        for search_url in cfg["search_urls"]:
            log.info(f"Scraping search URL: {search_url}")
            results = scrape_search_results(
                page, search_url,
                max_pages=cfg.get("max_pages", 5),
                delay_seconds=cfg.get("delay_seconds", 2),
            )
            all_listings.extend(results)

        if cfg.get("scrape_details", True):
            for listing in all_listings:
                try:
                    scrape_listing_detail(page, listing, delay_seconds=cfg.get("delay_seconds", 2))
                except Exception as e:
                    log.warning(f"Failed to scrape detail page for {listing.lot_id}: {e}")

        browser.close()

    log.info(f"Scraped {len(all_listings)} total listings.")

    scoring_rules = cfg.get("scoring", {})
    if scoring_rules.get("enabled", False):
        for listing in all_listings:
            score_listing(listing, scoring_rules)
        all_listings.sort(key=lambda l: (l.risk_score is None, l.risk_score))
        counts = {}
        for l in all_listings:
            counts[l.risk_label] = counts.get(l.risk_label, 0) + 1
        log.info(f"Risk breakdown: {counts}")

    new_listings = upsert_listings(conn, all_listings)
    log.info(f"{len(new_listings)} are new since the last run.")

    write_csv(all_listings)
    send_email_alert(cfg, new_listings)

    conn.close()


def main():
    parser = argparse.ArgumentParser(description="GoBid auction screening scraper (read-only, no bidding).")
    parser.add_argument("--config", default=str(BASE_DIR / "config.yaml"), help="Path to config.yaml")
    parser.add_argument("--debug-html", action="store_true",
                         help="Save rendered HTML/screenshots for selector calibration instead of scraping.")
    args = parser.parse_args()

    cfg = load_config(Path(args.config))
    run(cfg, debug_html=args.debug_html)


if __name__ == "__main__":
    main()
