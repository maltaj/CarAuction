"""
Load listings from registered sources into the database.

    python -m okshun.ingest                 # demo data
    python -m okshun.ingest --sources demo  # same, explicit
    python -m okshun.ingest --seed 7        # a different batch of demo lots

After loading, it checks saved searches and watchlists and creates alerts.

Schedule this with cron or Task Scheduler once real feeds are connected.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from okshun import alerts
from okshun.adapters import ADAPTERS
from okshun.db import DEFAULT_DB, connect, ingest


def main() -> None:
    parser = argparse.ArgumentParser(description="Load auction listings into the Okshun database.")
    parser.add_argument("--sources", nargs="+", default=["demo"], choices=sorted(ADAPTERS))
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--seed", type=int, default=None,
                        help="Demo only: replace the demo lots with a different batch (handy for trying alerts).")
    parser.add_argument("--no-alerts", action="store_true", help="Skip checking saved searches and watchlists.")
    args = parser.parse_args()

    conn = connect(args.db)
    adapters = [ADAPTERS[name](seed=args.seed) if (name == "demo" and args.seed is not None) else ADAPTERS[name]()
                for name in args.sources]
    counts = ingest(conn, adapters)
    for source, n in sorted(counts.items()):
        print(f"{source}: {n} listings")
    print(f"Saved to {args.db}")
    if not args.no_alerts:
        a = alerts.run(conn)
        print(f"Alerts: {a['new_match']} new matches, {a['closing']} closing soon, {a['emails']} emails sent")


if __name__ == "__main__":
    main()
