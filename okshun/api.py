"""
Okshun web app: JSON API plus the static front end.

    uvicorn okshun.api:app --reload
    open http://127.0.0.1:8000

On first start with an empty database it loads demo data, unless OKSHUN_DEMO=0.
"""

from __future__ import annotations

import asyncio
import os
import sqlite3
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from typing import Optional

import yaml
from fastapi import Depends, FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles

from okshun import alerts, calendar, db, repairs
from okshun.account_api import router as account_router
from okshun.adapters import SOURCES
from okshun.adapters.demo import DemoAdapter
from okshun.deps import db_path, get_conn

WEB_DIR = Path(__file__).with_name("web")
CHECKS_FILE = Path(__file__).with_name("vehicle_checks.yaml")


@asynccontextmanager
async def lifespan(app: FastAPI):
    conn = db.connect(db_path())
    now = datetime.now().isoformat(timespec="seconds")
    open_count = conn.execute(
        "SELECT COUNT(*) FROM listings WHERE status != 'closed' AND auction_end > ?", (now,)
    ).fetchone()[0]
    if open_count == 0 and os.environ.get("OKSHUN_DEMO", "1") != "0":
        db.ingest(conn, [DemoAdapter()])
    conn.close()
    interval = int(os.environ.get("OKSHUN_REMINDER_INTERVAL", "120"))
    task = asyncio.create_task(_reminder_loop(interval)) if interval > 0 else None
    yield
    if task:
        task.cancel()


def _check_once() -> dict:
    c = db.connect(db_path())
    try:
        return alerts.run(c)
    finally:
        c.close()


async def _reminder_loop(interval: int) -> None:
    """Check saved searches, reminders and bid limits, and send notifications, every few minutes."""
    while True:
        await asyncio.sleep(interval)
        try:
            await asyncio.to_thread(_check_once)
        except Exception as e:  # keep the loop alive; the next run tries again
            print(f"[okshun] reminder check failed: {e}")


app = FastAPI(title="Okshun", version="0.2.0", lifespan=lifespan)
app.include_router(account_router)


@app.get("/api/health")
def health() -> dict:
    return {"ok": True}


@app.get("/api/listings")
def list_listings(
    q: Optional[str] = None,
    make: list[str] = Query(default=[]),
    province: list[str] = Query(default=[]),
    source: list[str] = Query(default=[]),
    risk: list[str] = Query(default=[]),
    code: list[str] = Query(default=[]),
    body: list[str] = Query(default=[]),
    keys: list[str] = Query(default=[], description="Only these lots, as source/lot_id (watchlist, compare)"),
    runs: bool = False,
    min_year: Optional[int] = None,
    max_year: Optional[int] = None,
    max_cost: Optional[float] = None,
    max_km: Optional[int] = None,
    sort: str = Query(default="ending", pattern="^(ending|cost|risk|gap|newest|profit)$"),
    limit: int = Query(default=30, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    conn: sqlite3.Connection = Depends(get_conn),
) -> dict:
    filters = dict(q=q, make=make, province=province, source=source, risk=risk, code=code, body=body,
                   keys=keys[:100], runs=runs, min_year=min_year, max_year=max_year, max_cost=max_cost, max_km=max_km)
    return db.search(conn, filters, SOURCES, sort=sort, limit=limit, offset=offset)


@app.get("/api/listings/{source}/{lot_id}")
def get_listing(source: str, lot_id: str, conn: sqlite3.Connection = Depends(get_conn)) -> dict:
    item = db.get(conn, source, lot_id, SOURCES)
    if not item:
        raise HTTPException(status_code=404, detail="Listing not found")
    return item


@app.get("/api/listings/{source}/{lot_id}/calendar.ics")
def listing_calendar(source: str, lot_id: str, conn: sqlite3.Connection = Depends(get_conn)) -> Response:
    """A calendar event for the sale start (live) or closing time (timed), with a phone alarm."""
    item = db.get(conn, source, lot_id, SOURCES)
    if not item:
        raise HTTPException(status_code=404, detail="Listing not found")
    ics = calendar.lot_event(item, base_url=os.environ.get("OKSHUN_BASE_URL", ""))
    if not ics:
        raise HTTPException(status_code=404, detail="This lot has no published auction time.")
    return Response(ics, media_type="text/calendar; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="okshun-{lot_id}.ics"'})


@app.get("/api/facets")
def get_facets(conn: sqlite3.Connection = Depends(get_conn)) -> dict:
    return db.facets(conn, SOURCES)


@app.get("/api/vehicle-checks")
def get_vehicle_checks() -> list[dict]:
    """History/odometer services a buyer can open with the lot's VIN. Okshun never logs in for them."""
    with open(CHECKS_FILE, encoding="utf-8") as f:
        return yaml.safe_load(f)["providers"]


@app.get("/api/repair-rules")
def get_repair_rules() -> dict:
    """Verdict thresholds, so the front end can recompute profit when a buyer edits repair lines."""
    rules = repairs.load_rules()
    return {"verdict": rules.get("verdict", {})}


@app.get("/api/sources")
def get_sources() -> list[dict]:
    return list(SOURCES.values())


@app.get("/", include_in_schema=False)
def index() -> FileResponse:
    return FileResponse(WEB_DIR / "index.html")


app.mount("/", StaticFiles(directory=WEB_DIR), name="web")
