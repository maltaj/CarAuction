"""
GoBid adapter: maps gobid_scraper.Listing objects (or their dicts) to the shared Listing.

Personal use only. GoBid's terms (clause 220) prohibit automated data
extraction without a written agreement, so this adapter is not registered
in the public app. Run it locally with your own login until GoBid agrees.
"""

from __future__ import annotations

from dataclasses import asdict
from typing import Iterable

from okshun.schema import (
    Listing, SourceAdapter, parse_auction_type, parse_code, parse_num,
    parse_title, parse_tri,
)


class GoBidAdapter(SourceAdapter):
    """Maps gobid_scraper.Listing objects (or their dicts) to the shared Listing."""

    name = "gobid"
    default_commission_pct = None  # ADJUST ME: GoBid's published buyer's fee (%)
    default_fixed_fees = None      # ADJUST ME: admin / release fees (R)

    def __init__(self, scraper_rows: Iterable):
        # Accepts gobid_scraper.Listing dataclass objects or plain dicts.
        self._rows = scraper_rows

    def fetch_raw(self) -> Iterable[dict]:
        for r in self._rows:
            yield r if isinstance(r, dict) else asdict(r)

    def to_listing(self, r: dict) -> Listing:
        t_year, t_make, t_model = parse_title(r.get("title"))
        year = r.get("year")
        photos = r.get("photo_urls") or ""
        risk_reasons = r.get("risk_reasons") or ""
        return Listing(
            source=self.name,
            source_lot_id=str(r["lot_id"]),
            url=r["url"],
            make=r.get("make") or t_make,
            model=r.get("model") or t_model,
            variant=r.get("variant"),
            year=int(parse_num(year)) if parse_num(year) else t_year,
            mileage_km=int(parse_num(r.get("mileage")) or 0) or None,
            vin=r.get("vin"),
            damage_code=parse_code(r.get("damage_code")),
            damage_code_raw=r.get("damage_code"),
            primary_damage=r.get("primary_damage"),
            secondary_damage=r.get("secondary_damage"),
            runs_and_drives=parse_tri(r.get("run_and_drive")),
            keys_available=parse_tri(r.get("keys_available")),
            odometer_status=r.get("odometer_status"),
            description=r.get("description"),
            auction_type=parse_auction_type(r.get("auction_type")),
            auction_end=r.get("auction_date"),
            branch=r.get("branch"),
            province=r.get("province"),
            starting_bid=parse_num(r.get("starting_bid")),
            current_bid=parse_num(r.get("current_bid")),
            estimated_retail=parse_num(r.get("estimated_value")),
            photo_urls=[p for p in photos.split(";") if p] if isinstance(photos, str) else list(photos),
            # The scraper's own score is carried over; okshun.ingest re-scores
            # with the shared engine so every source is judged the same way.
            risk_score=r.get("risk_score"),
            risk_label=r.get("risk_label"),
            risk_reasons=[x.strip() for x in risk_reasons.split(";") if x.strip()]
                         if isinstance(risk_reasons, str) else list(risk_reasons),
        )
