"""
Alerts and reminders for signed-in buyers.

- New match: a lot first seen after a saved search was last checked, matching its filters.
- Reminders for watched lots, at the times the buyer chose (1 day, 2 hours, 30 or 15 minutes):
  before closing for timed auctions, before the sale starts for live webcast auctions.
  Only the most relevant reminder fires: a lot watched 20 minutes before closing gets the
  15-minute reminder, not a late "1 day" one.
- Over limit: bidding passed the bid limit the buyer set for a lot.

Every alert shows in the app. Phone notifications go out when the buyer allowed them, and
an email digest when SMTP is configured:

    OKSHUN_SMTP_HOST, OKSHUN_SMTP_PORT (default 587), OKSHUN_SMTP_USER,
    OKSHUN_SMTP_PASSWORD, OKSHUN_MAIL_FROM, OKSHUN_BASE_URL (for links)

The web server checks every OKSHUN_REMINDER_INTERVAL seconds (default 120). Without the
server running, check from the command line or a scheduler:

    python -m okshun.alerts              # once
    python -m okshun.alerts --every 120  # keep checking
"""

from __future__ import annotations

import argparse
import json
import os
import smtplib
import sqlite3
import time
from datetime import datetime, timedelta
from email.mime.text import MIMEText
from typing import Optional, Protocol

from okshun import push
from okshun.db import _where, filters_from_query

DEFAULT_OFFSETS = [120]
DELIVERY_MAX_AGE = timedelta(days=1)   # never send alerts older than this (e.g. after email is first set up)


class Mailer(Protocol):
    def send(self, to: str, subject: str, body: str) -> None: ...


class SmtpMailer:
    def __init__(self, host: str, port: int, user: Optional[str], password: Optional[str], sender: str):
        self.host, self.port, self.user, self.password, self.sender = host, port, user, password, sender

    @classmethod
    def from_env(cls) -> Optional["SmtpMailer"]:
        host, sender = os.environ.get("OKSHUN_SMTP_HOST"), os.environ.get("OKSHUN_MAIL_FROM")
        if not host or not sender:
            return None
        return cls(host, int(os.environ.get("OKSHUN_SMTP_PORT", "587")),
                   os.environ.get("OKSHUN_SMTP_USER"), os.environ.get("OKSHUN_SMTP_PASSWORD"), sender)

    def send(self, to: str, subject: str, body: str) -> None:
        msg = MIMEText(body, "plain", "utf-8")
        msg["From"], msg["To"], msg["Subject"] = self.sender, to, subject
        with smtplib.SMTP(self.host, self.port, timeout=30) as s:
            s.starttls()
            if self.user:
                s.login(self.user, self.password or "")
            s.sendmail(self.sender, [to], msg.as_string())


def _iso(dt: datetime) -> str:
    return dt.isoformat(timespec="seconds")


def _when(minutes: int) -> str:
    return {1440: "1 day", 120: "2 hours", 30: "30 minutes", 15: "15 minutes"}.get(minutes, f"{minutes} minutes")


def _rand(v) -> str:
    return "R " + f"{int(round(v or 0)):,}".replace(",", " ")


def _left(a: dict, moment_key: str) -> str:
    """Time left when the alert was created, e.g. '20 minutes', '2 hours', '1 day'."""
    try:
        mins = (datetime.fromisoformat(a[moment_key]) - datetime.fromisoformat(a["created_at"])).total_seconds() / 60
    except (TypeError, ValueError, KeyError):
        return _when(int(a["kind"].split("_")[1]))
    if mins < 60:
        return f"{max(1, round(mins))} minutes"
    if mins < 180:
        h, m = int(mins // 60), int(round((mins % 60) / 5) * 5)
        if m == 60:
            h, m = h + 1, 0
        return f"{h} hour{'s' if h != 1 else ''}" + (f" {m} minutes" if m else "")
    if mins < 36 * 60:
        h = round(mins / 60)
        return f"{h} hours"
    d = round(mins / 1440)
    return f"{d} day{'s' if d != 1 else ''}"


def describe(a: dict) -> tuple[str, str]:
    """Title and one-line body for an alert, shared by the app, phone notifications and email."""
    car = " ".join(str(x) for x in (a.get("year"), a.get("make"), a.get("model")) if x) or a["source_lot_id"]
    kind = a["kind"]
    if kind == "new_match":
        return f"New match: {a.get('search_name') or 'saved search'}", f"{car} ({a['source_lot_id']}), all-in about {_rand(a.get('est_all_in_cost'))}"
    if kind == "closing":  # alerts from before reminder times were configurable
        return "Closes within 2 hours", f"{car} ({a['source_lot_id']}) at {a.get('source_name') or a['source']}"
    if kind.startswith("closing_"):
        return f"Closes in {_left(a, 'auction_end')}", f"{car} ({a['source_lot_id']}) at {a.get('source_name') or a['source']}"
    if kind.startswith("starting_"):
        lot = f", lot {a['lot_number']}" if a.get("lot_number") else ""
        return f"Live sale starts in {_left(a, 'auction_start')}", f"{car}{lot} at {a.get('source_name') or a['source']}"
    if kind == "over_limit":
        return "Bidding passed your limit", f"{car} is at {_rand(a.get('current_bid'))}, above your {_rand(a.get('limit_bid'))} limit"
    return "Okshun alert", car


# ---------- finding alerts ----------

def find_new(conn: sqlite3.Connection, now: datetime) -> dict:
    now_s = _iso(now)
    counts = {"new_match": 0, "reminder": 0, "over_limit": 0}

    for s in conn.execute("SELECT id, user_id, params, last_checked_at FROM saved_searches").fetchall():
        where, params = _where(filters_from_query(s["params"]), now_s)
        for r in conn.execute(f"SELECT source, source_lot_id FROM listings WHERE {where} AND first_seen > ?",
                              params + [s["last_checked_at"]]).fetchall():
            counts["new_match"] += conn.execute(
                "INSERT OR IGNORE INTO alerts (user_id, kind, saved_search_id, source, source_lot_id, created_at) "
                "VALUES (?, 'new_match', ?, ?, ?, ?)", (s["user_id"], s["id"], r["source"], r["source_lot_id"], now_s)).rowcount
        conn.execute("UPDATE saved_searches SET last_checked_at = ? WHERE id = ?", (now_s, s["id"]))

    rows = conn.execute(
        "SELECT w.user_id, w.source, w.source_lot_id, w.reminders, u.reminder_offsets, "
        "l.auction_type, l.auction_start, l.auction_end FROM watchlist w "
        "JOIN users u ON u.id = w.user_id "
        "JOIN listings l ON l.source = w.source AND l.source_lot_id = w.source_lot_id "
        "WHERE l.status != 'closed'").fetchall()
    for r in rows:
        offsets = json.loads(r["reminders"]) if r["reminders"] is not None else json.loads(r["reminder_offsets"] or "null") or DEFAULT_OFFSETS
        live = r["auction_type"] == "live" and r["auction_start"]
        moment = r["auction_start"] if live else r["auction_end"]
        if not moment or not offsets:
            continue
        minutes_left = (datetime.fromisoformat(moment) - now).total_seconds() / 60
        due = [o for o in offsets if 0 < minutes_left <= o]
        if due:
            kind = f"{'starting' if live else 'closing'}_{min(due)}"
            counts["reminder"] += conn.execute(
                "INSERT OR IGNORE INTO alerts (user_id, kind, source, source_lot_id, created_at) VALUES (?, ?, ?, ?, ?)",
                (r["user_id"], kind, r["source"], r["source_lot_id"], now_s)).rowcount

    for r in conn.execute(
            "SELECT n.user_id, n.source, n.source_lot_id FROM lot_notes n JOIN listings l "
            "ON l.source = n.source AND l.source_lot_id = n.source_lot_id "
            "WHERE n.kind = 'limit' AND l.status != 'closed' AND l.current_bid IS NOT NULL "
            "AND l.current_bid > CAST(json_extract(n.data, '$.bid') AS REAL)").fetchall():
        counts["over_limit"] += conn.execute(
            "INSERT OR IGNORE INTO alerts (user_id, kind, source, source_lot_id, created_at) VALUES (?, 'over_limit', ?, ?, ?)",
            (r["user_id"], r["source"], r["source_lot_id"], now_s)).rowcount
    conn.commit()
    return counts


_ALERT_SELECT = (
    "SELECT a.id, a.user_id, a.kind, a.source, a.source_lot_id, a.created_at, a.read_at, a.emailed_at, a.pushed_at, "
    "s.name AS search_name, s.email AS search_email, u.email, u.email_reminders, u.push_reminders, "
    "l.year, l.make, l.model, l.est_all_in_cost, l.current_bid, l.auction_start, l.auction_end, l.auction_type, "
    "l.lot_number, l.status, l.risk_label, CAST(json_extract(n.data, '$.bid') AS REAL) AS limit_bid "
    "FROM alerts a JOIN users u ON u.id = a.user_id "
    "LEFT JOIN saved_searches s ON s.id = a.saved_search_id "
    "LEFT JOIN listings l ON l.source = a.source AND l.source_lot_id = a.source_lot_id "
    "LEFT JOIN lot_notes n ON n.user_id = a.user_id AND n.source = a.source AND n.source_lot_id = a.source_lot_id AND n.kind = 'limit' ")


def _enrich(row: sqlite3.Row, sources: dict) -> dict:
    d = dict(row)
    d["source_name"] = sources.get(d["source"], {}).get("name", d["source"])
    d["title"] = " ".join(str(x) for x in (d["year"], d["make"], d["model"]) if x) or d["source_lot_id"]
    d["headline"], d["detail"] = describe(d)
    for k in ("email", "email_reminders", "push_reminders", "search_email", "user_id", "emailed_at", "pushed_at"):
        d.pop(k, None)
    return d


def list_alerts(conn: sqlite3.Connection, user_id: int, sources: dict, limit: int = 50) -> list[dict]:
    rows = conn.execute(_ALERT_SELECT + "WHERE a.user_id = ? ORDER BY a.created_at DESC, a.id DESC LIMIT ?",
                        (user_id, limit)).fetchall()
    return [_enrich(r, sources) for r in rows]


def mark_read(conn: sqlite3.Connection, user_id: int) -> None:
    conn.execute("UPDATE alerts SET read_at = ? WHERE user_id = ? AND read_at IS NULL", (_iso(datetime.now()), user_id))
    conn.commit()


# ---------- delivery ----------

def _lot_url(base_url: str, a) -> str:
    return f"{base_url.rstrip('/')}/?lot={a['source']}/{a['source_lot_id']}"


def send_pushes(conn: sqlite3.Connection, now: datetime, sources: dict, sender: Optional[push.Sender] = None,
                base_url: str = "") -> int:
    rows = conn.execute(_ALERT_SELECT + "WHERE a.pushed_at IS NULL AND a.created_at >= ? AND u.push_reminders = 1 "
                        "AND EXISTS (SELECT 1 FROM push_subscriptions p WHERE p.user_id = a.user_id) "
                        "ORDER BY a.user_id, a.id", (_iso(now - DELIVERY_MAX_AGE),)).fetchall()
    by_user: dict[int, list] = {}
    for r in rows:
        by_user.setdefault(r["user_id"], []).append(r)
    sent = 0
    for uid, items in by_user.items():
        urgent = [r for r in items if r["kind"] != "new_match"]
        new = [r for r in items if r["kind"] == "new_match"]
        messages = []
        for r in urgent:
            title, body = describe(_enrich(r, sources))
            messages.append({"title": title, "body": body, "url": _lot_url(base_url, r), "tag": f"{r['kind']}:{r['source_lot_id']}"})
        if len(new) == 1:
            title, body = describe(_enrich(new[0], sources))
            messages.append({"title": title, "body": body, "url": _lot_url(base_url, new[0]), "tag": "new_match"})
        elif new:
            messages.append({"title": f"{len(new)} new lots match your saved searches", "body": "Open Okshun to see them.",
                             "url": f"{base_url.rstrip('/')}/?alerts=1", "tag": "new_match"})
        for m in messages:
            sent += push.push_to_user(conn, uid, m, sender=sender)
        conn.executemany("UPDATE alerts SET pushed_at = ? WHERE id = ?", [(_iso(now), r["id"]) for r in items])
    conn.commit()
    return sent


def send_digests(conn: sqlite3.Connection, mailer: Mailer, now: datetime, base_url: str = "", sources: Optional[dict] = None) -> int:
    rows = conn.execute(_ALERT_SELECT + "WHERE a.emailed_at IS NULL AND a.created_at >= ? AND ("
                        "(a.kind = 'new_match' AND s.email = 1) OR (a.kind != 'new_match' AND u.email_reminders = 1)) "
                        "ORDER BY a.user_id, a.kind, a.id", (_iso(now - DELIVERY_MAX_AGE),)).fetchall()
    by_user: dict[int, list] = {}
    for r in rows:
        by_user.setdefault(r["user_id"], []).append(r)
    sent = 0
    for uid, items in by_user.items():
        urgent = [r for r in items if r["kind"] != "new_match"]
        new = [r for r in items if r["kind"] == "new_match"]
        lines = []
        if urgent:
            lines += ["On your watchlist:"]
            for r in urgent:
                title, body = describe(_enrich(r, sources or {}))
                lines.append(f"- {title}: {body}" + (f"\n  {_lot_url(base_url, r)}" if base_url else ""))
            lines += [""]
        if new:
            lines += ["New lots matching your saved searches:"]
            for name in dict.fromkeys(r["search_name"] for r in new):
                lines += [f"  {name}:"]
                for r in (x for x in new if x["search_name"] == name):
                    lines.append("  - " + describe(_enrich(r, sources or {}))[1] + (f"\n    {_lot_url(base_url, r)}" if base_url else ""))
            lines += [""]
        lines += ["You get this because you saved searches or watched lots on Okshun. "
                  "Change reminders and emails in your account."]
        subject = (f"Okshun: {describe(_enrich(urgent[0], sources or {}))[0].lower()}" if len(urgent) == 1 and not new
                   else f"Okshun: {len(urgent)} reminder{'s' if len(urgent) != 1 else ''}, {len(new)} new" if urgent
                   else f"Okshun: {len(new)} new lot{'s' if len(new) != 1 else ''} for you")
        mailer.send(items[0]["email"], subject, "\n".join(lines))
        conn.executemany("UPDATE alerts SET emailed_at = ? WHERE id = ?", [(_iso(now), r["id"]) for r in items])
        sent += 1
    conn.commit()
    return sent


def run(conn: sqlite3.Connection, now: Optional[datetime] = None, mailer: Optional[Mailer] = None,
        base_url: Optional[str] = None, sender: Optional[push.Sender] = None, sources: Optional[dict] = None) -> dict:
    if sources is None:
        from okshun.adapters import SOURCES as sources
    now = now or datetime.now()
    base_url = base_url if base_url is not None else os.environ.get("OKSHUN_BASE_URL", "")
    counts = find_new(conn, now)
    counts["pushes"] = send_pushes(conn, now, sources, sender=sender, base_url=base_url)
    mailer = mailer if mailer is not None else SmtpMailer.from_env()
    counts["emails"] = send_digests(conn, mailer, now, base_url, sources) if mailer else 0
    return counts


def main() -> None:
    from okshun.db import DEFAULT_DB, connect
    parser = argparse.ArgumentParser(description="Check saved searches, reminders and bid limits; send notifications.")
    parser.add_argument("--every", type=int, default=0, help="Keep running, checking every N seconds.")
    args = parser.parse_args()
    while True:
        c = run(connect(DEFAULT_DB))
        print(f"{datetime.now():%H:%M} new matches {c['new_match']}, reminders {c['reminder']}, over limit {c['over_limit']}, "
              f"phone notifications {c['pushes']}, emails {c['emails']}")
        if not args.every:
            break
        time.sleep(args.every)


if __name__ == "__main__":
    main()
