# GoBid Auction Screening Tool

A read-only tool that logs into gobid.co.za, pulls current vehicle auction
listings (make/model/year, mileage, VIN, damage code & description,
run-and-drive status, keys available, odometer status, branch/location,
current bid, auction date, photos, etc.), stores them in a local SQLite
database + CSV, and emails you when **new** listings appear. It never
bids or submits anything other than the login form.

## 1. Setup

```bash
cd gobid_scraper
python3 -m venv venv
source venv/bin/activate        # Windows: venv\Scripts\activate
pip install -r requirements.txt
playwright install chromium
```

Copy the config template and fill in your details:

```bash
cp config.example.yaml config.yaml
```

Edit `config.yaml`:
- `username` / `password` -- your GoBid login.
- `search_urls` -- copy these straight from your browser's address bar
  after applying whatever filters you want (province, price, damage type,
  keywords) on gobid.co.za. You can list more than one.
- `email.enabled: true` plus SMTP details if you want email alerts. For
  Gmail, use an [app password](https://myaccount.google.com/apppasswords),
  not your real password.

## 2. Calibrating selectors (important, do this first)

I built this without being able to browse gobid.co.za directly (it blocks
plain HTTP requests as a bot-detection measure), so the CSS/XPath
selectors in `gobid_scraper.py` are educated placeholders, not verified
against the real page. Before relying on this, run:

```bash
python gobid_scraper.py --debug-html
```

This logs in, saves the rendered search-results page and one listing's
detail page as HTML + screenshots into `debug_output/`. Open the
screenshots to see what loaded, and open the `.html` files (or use your
browser's "Inspect Element" directly on gobid.co.za) to find the real
class names / structure for:

- the repeating listing card container (`parse_card` in the script)
- title, branch, damage code, current bid, starting bid, auction date
- on the detail page: VIN, mileage, primary/secondary damage, run &
  drive, keys available, odometer status, photos (`scrape_listing_detail`)

Send me the saved HTML (or just the relevant snippet) and I can update
the selectors precisely, or you can edit them yourself -- they're all
marked `# ADJUST ME` in the script with notes on what each one is for.

## 3. Running it

```bash
python gobid_scraper.py
```

Each run:
1. Logs in (reuses a saved session in `storage_state.json` when possible)
2. Scrapes every URL in `search_urls`, paginating up to `max_pages`
3. Optionally visits each listing's detail page for the full field set
4. Upserts everything into `gobid_listings.db` (SQLite) and marks which
   listings are new since the last run
5. Writes `output/latest_listings.csv` (current full pull) and a timestamped
   snapshot in `output/snapshots/`
6. Emails you a summary if there are new listings and email is enabled

Logs go to `gobid_scraper.log`.

## 4. Scheduling

Don't add a loop/sleep inside the script -- let cron or Task Scheduler own
the timing. That's more reliable and makes failures visible per-run.

**Linux/macOS (cron)** -- run every 30 minutes:

```bash
crontab -e
```

Add:

```
*/30 * * * * cd /full/path/to/gobid_scraper && ./venv/bin/python gobid_scraper.py >> cron.log 2>&1
```

**Windows (Task Scheduler)**:

1. Open Task Scheduler → Create Task
2. General tab: name it, "Run whether user is logged on or not"
3. Triggers tab: New → Daily, recur every 1 day, repeat task every 30
   minutes for a duration of 1 day
4. Actions tab: New →
   - Program/script: `C:\full\path\to\gobid_scraper\venv\Scripts\python.exe`
   - Arguments: `gobid_scraper.py`
   - Start in: `C:\full\path\to\gobid_scraper`
5. Save, enter your Windows password if prompted

## 5. A few things worth doing before you rely on this

- **Check GoBid's Terms of Service** for anything about automated access
  / scraping frequency. `delay_seconds` and `max_pages` in the config are
  there to keep the request rate gentle -- raise `delay_seconds` if you
  want to be extra conservative.
- **South African "Code" system**: fields like `damage_code` capture SA's
  Code 1-5 vehicle condition classification rather than a US-style title
  status, since GoBid operates in South Africa.
- **`estimated_value`** depends on GoBid actually publishing a retail/market
  value on the listing page -- if they don't, that field will just stay
  empty, which is fine, it's not something the scraper can invent.
## 6. Low-risk scoring

Every listing now gets a `risk_score`, `risk_label` (`Low Risk` /
`Medium Risk` / `High Risk`), and `risk_reasons` (a plain-English list of
what drove the score) -- written to the CSV, the database, and the email
alert, and used to sort everything low-risk-first.

It's a **transparent, additive point system**, not a black box: each
signal (run & drive, keys available, odometer status, damage code,
damage-description keywords, mileage, bid-to-estimated-value ratio) adds
or subtracts points based on rules you control in `config.yaml`'s
`scoring` section. Nothing about the default numbers is authoritative --
they're a reasonable starting point for you to adjust against your own
judgment of what makes a lot safe to buy, fix, and resell. Turn it off
entirely with `scoring.enabled: false`.

Two things that affect scoring quality:

- **`scrape_details: true` is required** for good scoring -- run &
  drive status, keys, odometer status, and damage description usually
  only live on a listing's own page, not the search-results card.
- If your database already exists from before this feature, the script
  auto-migrates it (adds the new columns) the next time it runs -- no
  need to delete `gobid_listings.db`.

You can also point `email.alert_risk_labels` at just `["Low Risk"]` (or
whichever labels you want) so alerts only fire for the deals worth your
attention, while the CSV/database still keep everything.

## 7. Reminder: this is a screening aid, not a purchase decision

The risk score is a mechanical sum of rules you configured -- it can't see
things like paint-match quality, undisclosed prior repairs, or anything
not captured in GoBid's published fields. Treat "Low Risk" as "worth a
closer manual look," not "safe to buy sight unseen."
