"""
Load listings from registered sources into the database.

    python -m okshun.ingest                 # demo data
    python -m okshun.ingest --sources demo  # same, explicit

Schedule this with cron or Task Scheduler once real feeds are connected.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from okshun.adapters import ADAPTERS
from okshun.db import DEFAULT_DB, connect, ingest


def main() -> None:
    parser = argparse.ArgumentParser(description="Load auction listings into the Okshun database.")
    parser.add_argument("--sources", nargs="+", default=["demo"], choices=sorted(ADAPTERS))
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    args = parser.parse_args()

    conn = connect(args.db)
    counts = ingest(conn, [ADAPTERS[name]() for name in args.sources])
    for source, n in sorted(counts.items()):
        print(f"{source}: {n} listings")
    print(f"Saved to {args.db}")


if __name__ == "__main__":
    main()
