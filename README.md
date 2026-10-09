# CarAuction

Search, screening and deal analysis for South African car auctions, built for a fix-and-resell workflow.

The tool collects auction listings, normalises them into one shared format, estimates the all-in cost (bid + buyer's commission + fees + VAT) and scores each vehicle for purchase risk. It does **not** place bids. Bidding always happens on the auction house's own site.

## Status

- `gobid_scraper.py` — logs into gobid.co.za, scrapes listings, scores risk, writes SQLite + CSV, sends email alerts. Setup, selector calibration and scheduling: see [GOBID_SCRAPER.md](GOBID_SCRAPER.md). Selectors marked `# ADJUST ME` still need checking against the live site (`--debug-html`).
- `listing_schema.py` — shared `Listing` schema, adapter contract, GoBid adapter (#1, mapped to the scraper's real fields), all-in cost estimate and SQLite storage with bid history.
- Further auction houses — added only with the auction house's written permission (see Data access).

## How it fits together

```
auction house data ──► SourceAdapter (one per house) ──► Listing
                                                             │
                                  estimate_all_in() ◄────────┤
                                  risk scoring      ◄────────┤
                                                             ▼
                                              SQLite: listings + bid_history
                                                             │
                                                     CSV export / email alerts
```

Each adapter only fetches and maps data. Fee estimates, scoring, storage and alerts are shared, so a new auction house needs only a new adapter.

## Quick start

```bash
python -m venv .venv
.venv\Scripts\activate          # Windows
pip install -r requirements.txt
playwright install chromium
copy config.example.yaml config.yaml   # then add your GoBid login
python gobid_scraper.py
```

To feed scraper output into the shared schema:

```python
import sqlite3
from listing_schema import GoBidAdapter, estimate_all_in, upsert, DDL

conn = sqlite3.connect("auctions.db")
conn.executescript(DDL)

listings = GoBidAdapter(scraped).run()   # scraped = gobid_scraper.Listing objects
for l in listings:
    estimate_all_in(l)
upsert(conn, listings)
```

Set `default_commission_pct` and `default_fixed_fees` on `GoBidAdapter` to GoBid's published buyer's fees so the all-in cost estimate is complete.

## Data access

Auction houses' terms may prohibit automated data collection. Only collect data for personal use where permitted, and add a source to a shared or paid product only under a written agreement with that auction house.

## Secrets

`config.yaml`, `storage_state.json`, databases and CSV outputs are git-ignored. Copy `config.example.yaml` to `config.yaml` for your own credentials.
