# Okshun

Okshun (working name) brings South African car auctions into one place: search, screening and deal analysis, built for a fix-and-resell workflow.

It collects auction listings, normalises them into one shared format, estimates the all-in cost (bid + buyer's commission + fees + VAT) and scores each vehicle for purchase risk. It does **not** place bids. Bidding always happens on the auction house's own site.

## Status

- **Web app** (`okshun/`): search across houses, filters (risk, cost, year, mileage, condition, house, province, body, make), five sort orders, a lot drawer with the risk breakdown and cost estimate, and a link to bid on the house's site. Works on phones and desktops.
- **Data**: runs on demo data from five fictional houses (every name contains "Demo" and the app labels it). No real auction house is connected yet; each is added only with its written permission (see Data access).
- **GoBid scraper** (`gobid_scraper.py`): personal-use tool for your own GoBid login. Setup and selector calibration: [GOBID_SCRAPER.md](GOBID_SCRAPER.md). Not part of the public app.

## Run the web app

```bash
python -m venv .venv
.venv\Scripts\activate             # Windows (macOS/Linux: source .venv/bin/activate)
pip install -r requirements.txt
uvicorn okshun.api:app --reload
```

Open http://127.0.0.1:8000. On first start the app loads demo data into `okshun.db`. To reload it later: `python -m okshun.ingest`.

Run the tests with `pytest`.

## How it fits together

```
auction house data ──► SourceAdapter (one per house, okshun/adapters/) ──► Listing
                                                                              │
                                              estimate_all_in()  ◄────────────┤
                                              scoring.score()    ◄────────────┤
                                                                              ▼
                                                       SQLite: listings + bid_history
                                                                              │
                                                     FastAPI (okshun/api.py) ──► web front end
```

Adapters only fetch and map data. Fee estimates, scoring, storage and the API are shared, so connecting a new auction house means writing one adapter and registering it in `okshun/adapters/__init__.py`.

## Project layout

| Path | What it does |
| --- | --- |
| `okshun/schema.py` | Shared `Listing` dataclass, adapter contract, parsing helpers, all-in cost, SQLite schema and upsert |
| `okshun/scoring.py`, `okshun/scoring_rules.yaml` | Risk scoring used for every source; tune points and thresholds in the YAML |
| `okshun/adapters/demo.py` | Demo lots from fictional houses, with real SA model-year ranges |
| `okshun/adapters/gobid.py` | Maps GoBid scraper output to `Listing` (personal use only) |
| `okshun/adapters/__init__.py` | Which sources the app runs, and how each house is shown |
| `okshun/db.py`, `okshun/ingest.py` | Ingest pipeline and search queries |
| `okshun/api.py` | JSON API (`/api/listings`, `/api/listings/{source}/{lot}`, `/api/facets`, `/api/sources`) and the static front end |
| `okshun/web/` | Front end: plain HTML, CSS and JavaScript, no build step |
| `gobid_scraper.py` | Personal GoBid scraper with its own scoring and email alerts |

## Data access

Auction houses' terms may prohibit automated data collection. Only collect data for personal use where permitted, and add a source to the public app only under a written agreement with that auction house.

## Secrets

`config.yaml`, `storage_state.json`, databases and CSV outputs are git-ignored. Copy `config.example.yaml` to `config.yaml` for your own credentials.
