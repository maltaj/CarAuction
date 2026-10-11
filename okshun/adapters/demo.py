"""
Demo data: realistic South African auction lots from clearly fictional houses.

Lets the app be used, demoed and pitched before any auction house signs.
Every house name contains "Demo", every URL uses the demo:// scheme, and the
UI labels these lots as demo data. Lots are generated from a fixed seed so
the catalogue is the same on every run; closing times are relative to now.
"""

from __future__ import annotations

import random
from datetime import datetime, timedelta
from typing import Iterable

from okshun.schema import AuctionType, Listing, ListingStatus, SourceAdapter, Tri, parse_code

# (id, display name, provinces, branch, stock profile, auction type, commission %, fixed fees R, lots)
HOUSES = [
    ("demo-highveld", "Highveld Demo Auctions", ["Gauteng"], "Midrand",
     "repo", AuctionType.TIMED, 10.0, 2500, 38),
    ("demo-coastal", "Coastal Demo Auctioneers", ["KwaZulu-Natal", "Eastern Cape"], "Durban",
     "repo", AuctionType.LIVE, 8.0, 1800, 32),
    ("demo-cape", "Cape Demo Bank Repo", ["Western Cape"], "Bellville",
     "repo", AuctionType.TIMED, 7.5, 3000, 30),
    ("demo-karoo", "Karoo Demo Salvage", ["Free State", "Northern Cape", "North West"], "Bloemfontein",
     "salvage", AuctionType.TIMED, 12.0, 1500, 36),
    ("demo-metro", "Metro Demo Fleet Disposals", ["Gauteng", "Western Cape", "Limpopo", "Mpumalanga"], "Kempton Park",
     "fleet", AuctionType.LIVE, 6.0, 2200, 28),
]

# make, model, variant, body, approx. new price (R), first and last model year sold in SA (None = still sold)
MODELS = [
    ("Toyota", "Hilux", "2.4 GD-6 Raider double cab", "Bakkie", 620_000, 2016, None),
    ("Toyota", "Corolla Cross", "1.8 Xi", "SUV", 420_000, 2021, None),
    ("Toyota", "Fortuner", "2.8 GD-6 4x4", "SUV", 780_000, 2016, None),
    ("Volkswagen", "Polo Vivo", "1.4 Trendline", "Hatchback", 290_000, 2010, None),
    ("Volkswagen", "Polo", "1.0 TSI Life", "Hatchback", 380_000, 2018, None),
    ("Ford", "Ranger", "2.0 XLT double cab", "Bakkie", 720_000, 2019, None),
    ("Suzuki", "Swift", "1.2 GL", "Hatchback", 230_000, 2018, None),
    ("Hyundai", "i20", "1.2 Motion", "Hatchback", 300_000, 2015, None),
    ("Nissan", "NP200", "1.6 8V", "Bakkie", 240_000, 2010, 2021),
    ("Isuzu", "D-Max", "1.9 LS single cab", "Bakkie", 560_000, 2021, None),
    ("Kia", "Picanto", "1.0 Street", "Hatchback", 220_000, 2017, None),
    ("Renault", "Kwid", "1.0 Climber", "Hatchback", 200_000, 2016, None),
    ("Haval", "Jolion", "1.5T Premium", "SUV", 400_000, 2021, None),
    ("BMW", "3 Series", "320i M Sport", "Sedan", 900_000, 2012, None),
    ("Mercedes-Benz", "C-Class", "C200", "Sedan", 950_000, 2012, None),
    ("Chery", "Tiggo 4 Pro", "1.5 Elite", "SUV", 340_000, 2022, None),
    ("Mahindra", "Pik Up", "2.2 mHawk single cab", "Bakkie", 400_000, 2012, None),
]

LOT_PREFIX = {"demo-highveld": "HV", "demo-coastal": "CO", "demo-cape": "CP", "demo-karoo": "KR", "demo-metro": "MT"}

COLOURS = ["white", "silver", "grey", "black", "red", "blue", "bronze"]
COSMETIC = ["front bumper scratched", "dent in left rear door", "hail damage to roof and bonnet",
            "cracked windscreen", "right mirror broken", "paint faded on bonnet", "rear panel scraped"]
STRUCTURAL = ["front-end collision, chassis rail bent", "flood damaged, water in cabin",
              "engine fire, burnt wiring loom", "rollover, roof pillars damaged",
              "gearbox not engaging", "airbags deployed after front impact"]
SALVAGE_CODES = [("Code 3A", 4), ("Code 3B", 3), ("Code 3C", 2), ("Code 4", 1)]


def _round(x: float, step: int = 500) -> float:
    return float(int(round(x / step)) * step)


class DemoAdapter(SourceAdapter):
    name = "demo"

    def __init__(self, seed: int = 42, now: datetime | None = None, this_year: int = 2026):
        self.seed = seed
        self.now = now or datetime.now().replace(second=0, microsecond=0)
        self.this_year = this_year

    def fetch_raw(self) -> Iterable[dict]:
        rng = random.Random(self.seed)
        for hid, hname, provinces, branch, profile, atype, comm, fees, count in HOUSES:
            prefix = LOT_PREFIX[hid]
            for i in range(count):
                make, model, variant, body, new_price, first_year, last_year = rng.choice(MODELS)
                oldest = self.this_year - (6 if profile == "fleet" else 14)
                lo = max(oldest, first_year)
                hi = min(self.this_year - 1, last_year or self.this_year - 1)
                year = rng.randint(lo, max(lo, hi))
                age = self.this_year - year
                retail = max(new_price * (0.86 ** age), new_price * 0.18)
                km = int(age * rng.uniform(10_000, 24_000) / 100) * 100 + rng.randint(0, 99) * 10

                damage_code, primary, secondary, runs, keys, odo = "Code 2", None, None, "yes", "yes", "Actual"
                start_factor = rng.uniform(0.48, 0.66)
                if profile == "salvage":
                    damage_code = rng.choices([c for c, _ in SALVAGE_CODES], weights=[w for _, w in SALVAGE_CODES])[0]
                    if damage_code in ("Code 3A",) and rng.random() < 0.6:
                        primary = rng.choice(COSMETIC)
                    else:
                        primary = rng.choice(STRUCTURAL)
                        if rng.random() < 0.4:
                            secondary = rng.choice(COSMETIC)
                    runs = rng.choices(["yes", "no", "unknown"], weights=[4, 4, 2])[0]
                    keys = rng.choices(["yes", "no", "unknown"], weights=[6, 2, 2])[0]
                    odo = rng.choices(["Actual", "Not actual", "Exempt", None], weights=[6, 1, 1, 2])[0]
                    start_factor = {"Code 3A": rng.uniform(0.30, 0.45), "Code 3B": rng.uniform(0.2, 0.32),
                                    "Code 3C": rng.uniform(0.12, 0.22), "Code 4": rng.uniform(0.04, 0.1)}[damage_code]
                elif profile == "repo":
                    if rng.random() < 0.25:
                        primary = rng.choice(COSMETIC)
                    runs = rng.choices(["yes", "no", "unknown"], weights=[8, 1, 1])[0]
                    keys = rng.choices(["yes", "no", "unknown"], weights=[8, 1, 1])[0]
                else:  # fleet
                    if rng.random() < 0.15:
                        primary = rng.choice(COSMETIC)
                    start_factor = rng.uniform(0.55, 0.7)

                starting = _round(retail * start_factor)
                current = _round(starting * rng.uniform(1.0, 1.4)) if rng.random() < 0.7 else None
                ends = self.now + timedelta(hours=rng.randint(2, 240), minutes=rng.choice([0, 15, 30, 45]))
                colour = rng.choice(COLOURS)
                desc = f"{year} {make} {model} {variant}. Colour: {colour}. Odometer {km:,} km.".replace(",", " ")
                if primary:
                    desc += f" Damage: {primary}" + (f"; {secondary}" if secondary else "") + "."
                if runs == "no":
                    desc += " Non-runner, towing required."

                yield dict(
                    source=hid, house=hname, lot=f"{prefix}-{10200 + i * 7}",
                    make=make, model=model, variant=variant, body=body, year=year, km=km,
                    code=damage_code, primary=primary, secondary=secondary, runs=runs, keys=keys,
                    odo=odo, desc=desc, atype=atype, ends=ends, branch=branch,
                    province=rng.choice(provinces), starting=starting, current=current,
                    retail=_round(retail, 1000), comm=comm, fees=fees,
                    colour=colour,
                )

    def to_listing(self, r: dict) -> Listing:
        return Listing(
            source=r["source"],
            source_lot_id=r["lot"],
            url=f"demo://{r['source']}/{r['lot']}",
            make=r["make"], model=r["model"], variant=r["variant"], year=r["year"],
            mileage_km=r["km"], body_type=r["body"],
            damage_code=parse_code(r["code"]), damage_code_raw=r["code"],
            primary_damage=r["primary"], secondary_damage=r["secondary"],
            runs_and_drives=Tri(r["runs"]), keys_available=Tri(r["keys"]),
            odometer_status=r["odo"], description=r["desc"],
            auction_type=r["atype"], status=ListingStatus.OPEN,
            auction_end=r["ends"], branch=r["branch"], province=r["province"],
            starting_bid=r["starting"], current_bid=r["current"], estimated_retail=r["retail"],
            buyers_commission_pct=r["comm"], fixed_fees=r["fees"], vat_on_hammer=True,
        )
