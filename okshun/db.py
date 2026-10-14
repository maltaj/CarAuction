"""SQLite access for the app: connection, ingest of adapter output, and search queries."""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Iterable, Optional

from dataclasses import fields

from okshun.schema import (DDL, AuctionType, Listing, ListingStatus, SaDamageCode, SourceAdapter, Tri,
                           estimate_all_in, upsert)
from urllib.parse import parse_qs

from okshun import repairs
from okshun.scoring import load_rules, score
from okshun.users import USER_DDL, migrate as migrate_users

DEFAULT_DB = Path(os.environ.get("OKSHUN_DB", Path(__file__).resolve().parent.parent / "okshun.db"))

SORTS = {
    "ending": "auction_end ASC",
    "cost": "est_all_in_cost ASC",
    "risk": "risk_score ASC, auction_end ASC",
    "gap": "CASE WHEN damage_code IN ('code_4','code_5') THEN 1 ELSE 0 END, (estimated_retail - est_all_in_cost) DESC",
    "newest": "year DESC, mileage_km ASC",
    "profit": "profit_low IS NULL, profit_low DESC, profit_high DESC",
}


def connect(path: Optional[Path] = None) -> sqlite3.Connection:
    conn = sqlite3.connect(str(path or DEFAULT_DB), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(USER_DDL)
    migrate_users(conn)
    try:
        conn.executescript(DDL)
    except sqlite3.OperationalError:
        pass  # an older table lacks columns the indexes need; migrate, then retry
    migrate(conn)
    conn.executescript(DDL)
    return conn


def migrate(conn: sqlite3.Connection) -> list[str]:
    """Add any Listing columns an older database is missing, so you never have to delete okshun.db."""
    have = {r[1] for r in conn.execute("PRAGMA table_info(listings)")}
    added = []
    for f in fields(Listing):
        if f.name not in have:
            kind = "INTEGER" if f.type in ("Optional[int]", "int") else "REAL" if "float" in str(f.type) else "TEXT"
            conn.execute(f'ALTER TABLE listings ADD COLUMN "{f.name}" {kind}')
            added.append(f.name)
    conn.commit()
    return added


def ingest(conn: sqlite3.Connection, adapters: Iterable[SourceAdapter], rules: Optional[dict] = None,
           repair_rules: Optional[dict] = None) -> dict:
    """Run adapters, add derived fields, score, estimate repairs, store. Lots a source no longer lists are closed."""
    rules = rules or load_rules()
    repair_rules = repair_rules or repairs.load_rules()
    run_start = datetime.now().isoformat(timespec="seconds")
    counts: dict[str, int] = {}
    for adapter in adapters:
        listings = adapter.run()
        for lst in listings:
            estimate_all_in(lst)
            score(lst, rules)
            repairs.estimate(lst, repair_rules)
            counts[lst.source] = counts.get(lst.source, 0) + 1
        upsert(conn, listings)
    for source in counts:
        conn.execute(
            "UPDATE listings SET status = 'closed' WHERE source = ? AND last_seen < ?",
            (source, run_start),
        )
    _meta(conn, "rules_fingerprint", rules_fingerprint())
    conn.commit()
    return counts


# Columns the rules produce. Re-scoring rewrites only these, never the auction data.
DERIVED = ("est_all_in_cost", "risk_score", "risk_label", "risk_reasons", "car_class", "resale_value",
           "repair_low", "repair_high", "road_low", "road_high", "profit_low", "profit_high",
           "repair_verdict", "repair_items")
_RULE_FILES = ("scoring_rules.yaml", "scoring.py", "repair_rules.yaml", "repairs.py")
_ENUMS = {"damage_code": SaDamageCode, "runs_and_drives": Tri, "keys_available": Tri,
          "auction_type": AuctionType, "status": ListingStatus}


def rules_fingerprint() -> str:
    """Changes whenever the scoring or repair rules (or their code) change."""
    h = hashlib.sha256()
    for name in _RULE_FILES:
        h.update((Path(__file__).with_name(name)).read_bytes())
    return h.hexdigest()[:16]


def _meta(conn: sqlite3.Connection, key: str, value: Optional[str] = None) -> Optional[str]:
    conn.execute("CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT)")
    if value is None:
        row = conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
        return row[0] if row else None
    conn.execute("INSERT INTO meta VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value", (key, value))
    return value


def _listing_from_row(row: sqlite3.Row) -> Listing:
    d = dict(row)
    kw = {}
    for f in fields(Listing):
        v = d.get(f.name)
        if f.name in _ENUMS:
            try:
                v = _ENUMS[f.name](v)
            except ValueError:
                v = _ENUMS[f.name]("unknown")
        elif f.name in ("photo_urls", "risk_reasons", "repair_items"):
            v = json.loads(v or "[]")
        elif f.name == "raw":
            v = {}
        elif f.name in ("auction_start", "auction_end", "first_seen", "last_seen") and v:
            v = datetime.fromisoformat(v)
        elif f.name == "vat_on_hammer" and v is not None:
            v = bool(v)
        if v is not None or f.name in ("source", "source_lot_id", "url"):
            kw[f.name] = v
    return Listing(**kw)


def rescore(conn: sqlite3.Connection, rules: Optional[dict] = None, repair_rules: Optional[dict] = None) -> int:
    """Recalculate cost, risk and repair figures for every stored lot with the current rules."""
    rules = rules or load_rules()
    repair_rules = repair_rules or repairs.load_rules()
    n = 0
    for row in conn.execute("SELECT * FROM listings").fetchall():
        lst = _listing_from_row(row)
        estimate_all_in(lst)
        score(lst, rules)
        repairs.estimate(lst, repair_rules)
        vals = {k: getattr(lst, k) for k in DERIVED}
        for k in ("risk_reasons", "repair_items"):
            vals[k] = json.dumps(vals[k])
        conn.execute(f"UPDATE listings SET {', '.join(f'{k} = :{k}' for k in DERIVED)} "
                     "WHERE source = :source AND source_lot_id = :lot",
                     {**vals, "source": lst.source, "lot": lst.source_lot_id})
        n += 1
    _meta(conn, "rules_fingerprint", rules_fingerprint())
    conn.commit()
    return n


def rescore_if_rules_changed(conn: sqlite3.Connection) -> int:
    """Run at start-up so edited rules (or an updated download) apply without a manual step."""
    if _meta(conn, "rules_fingerprint") == rules_fingerprint():
        return 0
    return rescore(conn)


def _decode(row: sqlite3.Row, sources: dict) -> dict:
    d = dict(row)
    for key in ("photo_urls", "risk_reasons", "repair_items"):
        d[key] = json.loads(d[key] or "[]")
    d.pop("raw", None)
    src = sources.get(d["source"], {})
    d["source_name"] = src.get("name", d["source"])
    d["demo"] = bool(src.get("demo")) or d["url"].startswith("demo://")
    d["title"] = " ".join(str(x) for x in (d["year"], d["make"], d["model"]) if x)
    # Code 4/5 vehicles can never be re-registered, so a gap to retail value means nothing.
    if d.get("estimated_retail") and d.get("est_all_in_cost") and d.get("damage_code") not in ("code_4", "code_5"):
        d["gap_to_retail"] = round(d["estimated_retail"] - d["est_all_in_cost"], 2)
    else:
        d["gap_to_retail"] = None
    d["vat_on_hammer"] = None if d["vat_on_hammer"] is None else bool(d["vat_on_hammer"])
    return d


def _where(f: dict, now: str) -> tuple[str, list]:
    clauses = ["status != 'closed'", "(auction_end IS NULL OR auction_end > ?)"]
    params: list = [now]
    if f.get("q"):
        clauses.append("(make || ' ' || model || ' ' || COALESCE(variant,'') || ' ' || COALESCE(description,'')) LIKE ?")
        params.append(f"%{f['q']}%")
    for key, col in (("make", "make"), ("province", "province"), ("source", "source"),
                     ("risk", "risk_label"), ("code", "damage_code"), ("body", "body_type")):
        values = [v for v in (f.get(key) or []) if v]
        if values:
            clauses.append(f"{col} IN ({','.join('?' * len(values))})")
            params.extend(values)
    keys = [k for k in (f.get("keys") or []) if k]
    if keys:
        clauses.append(f"(source || '/' || source_lot_id) IN ({','.join('?' * len(keys))})")
        params.extend(keys)
    if f.get("runs"):
        clauses.append("runs_and_drives = 'yes'")
    if f.get("min_year"):
        clauses.append("year >= ?"); params.append(int(f["min_year"]))
    if f.get("max_year"):
        clauses.append("year <= ?"); params.append(int(f["max_year"]))
    if f.get("max_cost"):
        clauses.append("est_all_in_cost <= ?"); params.append(float(f["max_cost"]))
    if f.get("max_km"):
        clauses.append("mileage_km <= ?"); params.append(int(f["max_km"]))
    return " AND ".join(clauses), params


MULTI_FILTERS = ("make", "province", "source", "risk", "code", "body", "keys")
SINGLE_FILTERS = ("q", "min_year", "max_year", "max_cost", "max_km")


def filters_from_query(qs: str) -> dict:
    """Turn a saved search's query string (the app's own URL) into search filters."""
    parsed = parse_qs(qs.lstrip("?"))
    f: dict = {k: parsed.get(k, []) for k in MULTI_FILTERS if k != "keys"}
    for k in SINGLE_FILTERS:
        if parsed.get(k):
            f[k] = parsed[k][0]
    f["runs"] = parsed.get("runs", ["false"])[0] == "true"
    return f


def search(conn: sqlite3.Connection, filters: dict, sources: dict, sort: str = "ending",
           limit: int = 50, offset: int = 0, now: Optional[str] = None) -> dict:
    now = now or datetime.now().isoformat(timespec="seconds")
    where, params = _where(filters, now)
    order = SORTS.get(sort, SORTS["ending"])
    total = conn.execute(f"SELECT COUNT(*) FROM listings WHERE {where}", params).fetchone()[0]
    rows = conn.execute(
        f"SELECT * FROM listings WHERE {where} ORDER BY {order} LIMIT ? OFFSET ?",
        params + [limit, offset],
    ).fetchall()
    return {"total": total, "items": [_decode(r, sources) for r in rows]}


def get(conn: sqlite3.Connection, source: str, lot_id: str, sources: dict) -> Optional[dict]:
    row = conn.execute("SELECT * FROM listings WHERE source = ? AND source_lot_id = ?", (source, lot_id)).fetchone()
    if not row:
        return None
    d = _decode(row, sources)
    d["bid_history"] = [dict(r) for r in conn.execute(
        "SELECT seen_at, current_bid FROM bid_history WHERE source = ? AND source_lot_id = ? ORDER BY seen_at",
        (source, lot_id))]
    return d


def facets(conn: sqlite3.Connection, sources: dict, now: Optional[str] = None) -> dict:
    now = now or datetime.now().isoformat(timespec="seconds")
    where, params = _where({}, now)
    out: dict = {}
    for key, col in (("make", "make"), ("province", "province"), ("source", "source"),
                     ("risk", "risk_label"), ("code", "damage_code"), ("body", "body_type")):
        rows = conn.execute(
            f"SELECT {col} AS v, COUNT(*) AS n FROM listings WHERE {where} AND {col} IS NOT NULL "
            f"GROUP BY {col} ORDER BY n DESC, v", params).fetchall()
        out[key] = [{"value": r["v"], "count": r["n"],
                     **({"label": sources.get(r["v"], {}).get("name", r["v"])} if key == "source" else {})}
                    for r in rows]
    stats = conn.execute(
        f"SELECT COUNT(*) AS n, MIN(year) AS min_year, MAX(year) AS max_year, MAX(est_all_in_cost) AS max_cost "
        f"FROM listings WHERE {where}", params).fetchone()
    out["stats"] = dict(stats)
    return out
