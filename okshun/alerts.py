"""
Alerts for signed-in buyers.

- New match: a lot first seen after a saved search was last checked, matching its filters.
- Closing soon: a watched lot closes within CLOSING_WINDOW.

Alerts always appear in the app. If SMTP is configured (environment variables
below), each buyer also gets one email digest per run with their new alerts.

    OKSHUN_SMTP_HOST, OKSHUN_SMTP_PORT (default 587), OKSHUN_SMTP_USER,
    OKSHUN_SMTP_PASSWORD, OKSHUN_MAIL_FROM, OKSHUN_BASE_URL (for links)

Runs automatically after `python -m okshun.ingest`, or on its own:

    python -m okshun.alerts
"""

from __future__ import annotations

import os
import smtplib
import sqlite3
from datetime import datetime, timedelta
from email.mime.text import MIMEText
from typing import Optional, Protocol

from okshun.db import _where, filters_from_query

CLOSING_WINDOW = timedelta(hours=2)
EMAIL_MAX_AGE = timedelta(days=1)   # never email alerts older than this (e.g. after SMTP is first set up)


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


def find_new(conn: sqlite3.Connection, now: datetime) -> dict:
    now_s = _iso(now)
    counts = {"new_match": 0, "closing": 0}
    for s in conn.execute("SELECT id, user_id, params, last_checked_at FROM saved_searches").fetchall():
        where, params = _where(filters_from_query(s["params"]), now_s)
        rows = conn.execute(f"SELECT source, source_lot_id FROM listings WHERE {where} AND first_seen > ?",
                            params + [s["last_checked_at"]]).fetchall()
        for r in rows:
            cur = conn.execute(
                "INSERT OR IGNORE INTO alerts (user_id, kind, saved_search_id, source, source_lot_id, created_at) "
                "VALUES (?, 'new_match', ?, ?, ?, ?)", (s["user_id"], s["id"], r["source"], r["source_lot_id"], now_s))
            counts["new_match"] += cur.rowcount
        conn.execute("UPDATE saved_searches SET last_checked_at = ? WHERE id = ?", (now_s, s["id"]))

    rows = conn.execute(
        "SELECT w.user_id, w.source, w.source_lot_id FROM watchlist w JOIN listings l "
        "ON l.source = w.source AND l.source_lot_id = w.source_lot_id "
        "WHERE l.status != 'closed' AND l.auction_end > ? AND l.auction_end <= ?",
        (now_s, _iso(now + CLOSING_WINDOW))).fetchall()
    for r in rows:
        cur = conn.execute(
            "INSERT OR IGNORE INTO alerts (user_id, kind, source, source_lot_id, created_at) VALUES (?, 'closing', ?, ?, ?)",
            (r["user_id"], r["source"], r["source_lot_id"], now_s))
        counts["closing"] += cur.rowcount
    conn.commit()
    return counts


def list_alerts(conn: sqlite3.Connection, user_id: int, sources: dict, limit: int = 50) -> list[dict]:
    rows = conn.execute(
        "SELECT a.id, a.kind, a.source, a.source_lot_id, a.created_at, a.read_at, s.name AS search_name, "
        "l.year, l.make, l.model, l.est_all_in_cost, l.auction_end, l.status, l.risk_label "
        "FROM alerts a LEFT JOIN saved_searches s ON s.id = a.saved_search_id "
        "LEFT JOIN listings l ON l.source = a.source AND l.source_lot_id = a.source_lot_id "
        "WHERE a.user_id = ? ORDER BY a.created_at DESC, a.id DESC LIMIT ?", (user_id, limit)).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["title"] = " ".join(str(x) for x in (r["year"], r["make"], r["model"]) if x) or r["source_lot_id"]
        d["source_name"] = sources.get(r["source"], {}).get("name", r["source"])
        out.append(d)
    return out


def mark_read(conn: sqlite3.Connection, user_id: int) -> None:
    conn.execute("UPDATE alerts SET read_at = ? WHERE user_id = ? AND read_at IS NULL", (_iso(datetime.now()), user_id))
    conn.commit()


def send_digests(conn: sqlite3.Connection, mailer: Mailer, now: datetime, base_url: str = "") -> int:
    cutoff = _iso(now - EMAIL_MAX_AGE)
    rows = conn.execute(
        "SELECT a.id, a.kind, a.source, a.source_lot_id, u.id AS uid, u.email, s.name AS search_name, "
        "l.year, l.make, l.model, l.est_all_in_cost, l.auction_end "
        "FROM alerts a JOIN users u ON u.id = a.user_id "
        "LEFT JOIN saved_searches s ON s.id = a.saved_search_id "
        "LEFT JOIN listings l ON l.source = a.source AND l.source_lot_id = a.source_lot_id "
        "WHERE a.emailed_at IS NULL AND a.created_at >= ? "
        "AND ((a.kind = 'new_match' AND s.email = 1) OR (a.kind = 'closing' AND u.email_reminders = 1)) "
        "ORDER BY u.id, a.kind, a.id", (cutoff,)).fetchall()
    by_user: dict[int, list] = {}
    for r in rows:
        by_user.setdefault(r["uid"], []).append(r)
    sent = 0
    for uid, items in by_user.items():
        lines = []
        closing = [r for r in items if r["kind"] == "closing"]
        new = [r for r in items if r["kind"] == "new_match"]
        fmt = lambda r: (f"- {r['year']} {r['make']} {r['model']} ({r['source_lot_id']}), all-in about "
                         f"R {int(r['est_all_in_cost'] or 0):,}".replace(",", " ")
                         + (f", closes {r['auction_end'].replace('T', ' ')[:16]}" if r["auction_end"] else ""))
        if closing:
            lines += ["Closing within 2 hours on your watchlist:"] + [fmt(r) for r in closing] + [""]
        if new:
            lines += ["New lots matching your saved searches:"]
            for name in dict.fromkeys(r["search_name"] for r in new):
                lines += [f"  {name}:"] + ["  " + fmt(r) for r in new if r["search_name"] == name]
            lines += [""]
        if base_url:
            lines += [f"Open Okshun: {base_url}"]
        lines += ["", "You get this because you saved searches or watched lots on Okshun. "
                  "Turn emails off in your account or per saved search."]
        subject = f"Okshun: {len(closing)} closing soon, {len(new)} new" if closing else f"Okshun: {len(new)} new lots for you"
        mailer.send(items[0]["email"], subject, "\n".join(lines))
        conn.executemany("UPDATE alerts SET emailed_at = ? WHERE id = ?", [(_iso(now), r["id"]) for r in items])
        sent += 1
    conn.commit()
    return sent


def run(conn: sqlite3.Connection, now: Optional[datetime] = None, mailer: Optional[Mailer] = None,
        base_url: Optional[str] = None) -> dict:
    now = now or datetime.now()
    counts = find_new(conn, now)
    mailer = mailer if mailer is not None else SmtpMailer.from_env()
    counts["emails"] = send_digests(conn, mailer, now, base_url or os.environ.get("OKSHUN_BASE_URL", "")) if mailer else 0
    return counts


def main() -> None:
    from okshun.db import DEFAULT_DB, connect
    counts = run(connect(DEFAULT_DB))
    print(f"New matches: {counts['new_match']}, closing soon: {counts['closing']}, emails sent: {counts['emails']}")


if __name__ == "__main__":
    main()
