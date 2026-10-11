"""
Shared listing schema for Okshun, the multi-source SA car auction aggregator.

Each auction house gets one adapter (okshun/adapters/) that turns its raw data
(API response, CSV export, approved page collection) into the same `Listing`
shape. Scoring, alerts, the API and the database only ever see `Listing`,
never source-specific fields.
"""

from __future__ import annotations

import json
import re
import sqlite3
from abc import ABC, abstractmethod
from dataclasses import dataclass, field, asdict
from datetime import datetime, date
from enum import Enum
from typing import Iterable, Optional


# ---------------------------------------------------------------------------
# Controlled vocabularies (normalise every source into these)
# ---------------------------------------------------------------------------

class AuctionType(str, Enum):
    LIVE = "live"            # in-person / webcast, ends when hammer falls
    TIMED = "timed"          # online, fixed closing time
    TENDER = "tender"        # sealed offer
    BUY_NOW = "buy_now"
    UNKNOWN = "unknown"


class ListingStatus(str, Enum):
    UPCOMING = "upcoming"
    OPEN = "open"
    CLOSED = "closed"
    SOLD = "sold"
    WITHDRAWN = "withdrawn"
    UNKNOWN = "unknown"


class SaDamageCode(str, Enum):
    """SA NaTIS vehicle codes. Sub-codes (3A/3B/3C) stay in `damage_code_raw`."""
    CODE_1 = "code_1"   # new
    CODE_2 = "code_2"   # used, normal registration
    CODE_3 = "code_3"   # rebuilt (3A/3B/3C), needs police clearance + roadworthy
    CODE_4 = "code_4"   # permanently unfit for road use, parts only
    CODE_5 = "code_5"   # permanently demolished
    NONE = "none"       # source states no code
    UNKNOWN = "unknown"


class Tri(str, Enum):
    """Yes / no / unknown — sources are often silent on keys, starting, etc."""
    YES = "yes"
    NO = "no"
    UNKNOWN = "unknown"


# ---------------------------------------------------------------------------
# The normalised listing
# ---------------------------------------------------------------------------

@dataclass
class Listing:
    # identity
    source: str                         # "gobid", "parkvillage", "webuycars", ...
    source_lot_id: str                  # the house's own lot / stock reference
    url: str                            # deep link back to the house (bidding happens there)

    # vehicle
    make: Optional[str] = None
    model: Optional[str] = None
    variant: Optional[str] = None
    year: Optional[int] = None
    mileage_km: Optional[int] = None
    vin: Optional[str] = None
    fuel: Optional[str] = None
    transmission: Optional[str] = None
    body_type: Optional[str] = None

    # condition
    damage_code: SaDamageCode = SaDamageCode.UNKNOWN
    damage_code_raw: Optional[str] = None
    primary_damage: Optional[str] = None
    secondary_damage: Optional[str] = None
    runs_and_drives: Tri = Tri.UNKNOWN
    keys_available: Tri = Tri.UNKNOWN
    odometer_status: Optional[str] = None   # "actual", "not actual", "exempt", ...
    description: Optional[str] = None

    # sale
    auction_type: AuctionType = AuctionType.UNKNOWN
    status: ListingStatus = ListingStatus.UNKNOWN
    auction_start: Optional[datetime] = None
    auction_end: Optional[datetime] = None
    branch: Optional[str] = None
    province: Optional[str] = None          # normalised: "Gauteng", "Western Cape", ...

    # money (all ZAR, VAT-inclusive where the source says so)
    starting_bid: Optional[float] = None
    current_bid: Optional[float] = None
    estimated_retail: Optional[float] = None
    buyers_commission_pct: Optional[float] = None
    fixed_fees: Optional[float] = None       # admin/doc/release fees
    vat_on_hammer: Optional[bool] = None
    est_all_in_cost: Optional[float] = None  # computed, not scraped

    # media
    photo_urls: list[str] = field(default_factory=list)

    # scoring (filled by the shared risk engine, never by adapters)
    risk_score: Optional[int] = None
    risk_label: Optional[str] = None
    risk_reasons: list[str] = field(default_factory=list)

    # bookkeeping
    first_seen: Optional[datetime] = None
    last_seen: Optional[datetime] = None
    raw: dict = field(default_factory=dict)  # untouched source payload for debugging

    @property
    def key(self) -> str:
        return f"{self.source}:{self.source_lot_id}"


# ---------------------------------------------------------------------------
# Adapter contract
# ---------------------------------------------------------------------------

class SourceAdapter(ABC):
    """One per auction house. Only fetch + map; no scoring, no storage."""

    name: str  # matches Listing.source

    # Default fee model for this house, used if a listing doesn't state its own.
    default_commission_pct: Optional[float] = None
    default_fixed_fees: Optional[float] = None

    @abstractmethod
    def fetch_raw(self) -> Iterable[dict]:
        """Yield raw records (scraped dicts, API rows, CSV rows)."""

    @abstractmethod
    def to_listing(self, raw: dict) -> Listing:
        """Map one raw record to a Listing. Leave unknowns as None/UNKNOWN."""

    def run(self) -> list[Listing]:
        out = []
        for raw in self.fetch_raw():
            try:
                lst = self.to_listing(raw)
                lst.raw = raw
                if lst.buyers_commission_pct is None:
                    lst.buyers_commission_pct = self.default_commission_pct
                if lst.fixed_fees is None:
                    lst.fixed_fees = self.default_fixed_fees
                out.append(lst)
            except Exception as e:  # one bad record shouldn't kill the run
                print(f"[{self.name}] skipped record: {e}")
        return out


# ---------------------------------------------------------------------------
# Parsing helpers shared by adapters
# ---------------------------------------------------------------------------

def parse_tri(v) -> Tri:
    if v is None:
        return Tri.UNKNOWN
    s = str(v).strip().lower()
    if s in {"yes", "y", "true", "1"}:
        return Tri.YES
    if s in {"no", "n", "false", "0"}:
        return Tri.NO
    return Tri.UNKNOWN


def parse_code(v) -> SaDamageCode:
    if not v:
        return SaDamageCode.UNKNOWN
    digits = "".join(ch for ch in str(v) if ch.isdigit())
    return {
        "1": SaDamageCode.CODE_1, "2": SaDamageCode.CODE_2,
        "3": SaDamageCode.CODE_3, "4": SaDamageCode.CODE_4,
        "5": SaDamageCode.CODE_5,
    }.get(digits[:1], SaDamageCode.UNKNOWN)


def parse_num(v) -> Optional[float]:
    if v in (None, ""):
        return None
    s = re.sub(r"[^\d.]", "", str(v))  # "R 62,500" / "98 500 km" -> digits only
    try:
        return float(s)
    except ValueError:
        return None


def parse_title(title: Optional[str]):
    """'2018 Toyota Hilux 2.4 GD-6' -> (2018, 'Toyota', 'Hilux 2.4 GD-6')."""
    m = re.match(r"\s*((?:19|20)\d{2})\s+(\S+)\s*(.*)", title or "")
    if not m:
        return None, None, None
    return int(m.group(1)), m.group(2), (m.group(3) or None)


def parse_auction_type(v) -> AuctionType:
    s = str(v or "").strip().lower()
    if "live" in s or "webcast" in s:
        return AuctionType.LIVE
    if "timed" in s or "online" in s:
        return AuctionType.TIMED
    if "tender" in s:
        return AuctionType.TENDER
    return AuctionType.UNKNOWN


# ---------------------------------------------------------------------------
# Shared derived fields (run after adapters, before scoring)
# ---------------------------------------------------------------------------

def estimate_all_in(lst: Listing) -> None:
    bid = lst.current_bid or lst.starting_bid
    if bid is None:
        return
    total = bid
    if lst.buyers_commission_pct:
        total += bid * lst.buyers_commission_pct / 100
    if lst.fixed_fees:
        total += lst.fixed_fees
    if lst.vat_on_hammer is False:  # hammer quoted ex-VAT
        total *= 1.15
    lst.est_all_in_cost = round(total, 2)


# ---------------------------------------------------------------------------
# Storage
# ---------------------------------------------------------------------------

DDL = """
CREATE TABLE IF NOT EXISTS listings (
    source              TEXT NOT NULL,
    source_lot_id       TEXT NOT NULL,
    url                 TEXT NOT NULL,
    make TEXT, model TEXT, variant TEXT, year INTEGER,
    mileage_km INTEGER, vin TEXT, fuel TEXT, transmission TEXT, body_type TEXT,
    damage_code TEXT, damage_code_raw TEXT,
    primary_damage TEXT, secondary_damage TEXT,
    runs_and_drives TEXT, keys_available TEXT, odometer_status TEXT,
    description TEXT,
    auction_type TEXT, status TEXT,
    auction_start TEXT, auction_end TEXT,
    branch TEXT, province TEXT,
    starting_bid REAL, current_bid REAL, estimated_retail REAL,
    buyers_commission_pct REAL, fixed_fees REAL, vat_on_hammer INTEGER,
    est_all_in_cost REAL,
    photo_urls TEXT,            -- JSON array
    risk_score INTEGER, risk_label TEXT, risk_reasons TEXT,  -- reasons = JSON array
    first_seen TEXT, last_seen TEXT,
    raw TEXT,                   -- JSON
    PRIMARY KEY (source, source_lot_id)
);

-- Bid snapshots over time: lets you learn typical hammer-to-estimate ratios per house.
CREATE TABLE IF NOT EXISTS bid_history (
    source        TEXT NOT NULL,
    source_lot_id TEXT NOT NULL,
    seen_at       TEXT NOT NULL,
    current_bid   REAL,
    status        TEXT,
    PRIMARY KEY (source, source_lot_id, seen_at)
);

CREATE INDEX IF NOT EXISTS ix_listings_search
    ON listings (make, model, year, province, auction_end);
CREATE INDEX IF NOT EXISTS ix_listings_vin ON listings (vin);  -- spot the same car relisted elsewhere
"""


def _row(lst: Listing) -> dict:
    d = asdict(lst)
    for k, v in d.items():
        if isinstance(v, Enum):
            d[k] = v.value
        elif isinstance(v, (datetime, date)):
            d[k] = v.isoformat()
    d["photo_urls"] = json.dumps(d["photo_urls"])
    d["risk_reasons"] = json.dumps(d["risk_reasons"])
    d["raw"] = json.dumps(d["raw"], default=str)
    if d["vat_on_hammer"] is not None:
        d["vat_on_hammer"] = int(d["vat_on_hammer"])
    return d


def upsert(conn: sqlite3.Connection, listings: Iterable[Listing]) -> None:
    now = datetime.now().isoformat(timespec="seconds")
    for lst in listings:
        d = _row(lst)
        d.pop("first_seen", None)  # set once on insert, never overwritten
        d["last_seen"] = now
        cols = list(d.keys())
        update_cols = [c for c in cols if c not in ("source", "source_lot_id")]
        conn.execute(
            f"""INSERT INTO listings ({', '.join(cols)}, first_seen)
                VALUES ({', '.join(':' + c for c in cols)}, :now)
                ON CONFLICT(source, source_lot_id) DO UPDATE SET
                {', '.join(f'{c}=excluded.{c}' for c in update_cols)}""",
            {**d, "now": now},
        )
        conn.execute(
            "INSERT OR IGNORE INTO bid_history VALUES (?, ?, ?, ?, ?)",
            (lst.source, lst.source_lot_id, now, lst.current_bid, d["status"]),
        )
    conn.commit()
