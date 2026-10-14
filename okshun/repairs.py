"""
Repair-cost and profit estimates for each lot, from published damage details.

Rules live in repair_rules.yaml (placeholder figures until tuned with real
repair costs). The estimate is always a range. Damage a description can't
price, such as flood or fire, is marked "inspect first" instead of guessed,
and Code 4/5 cars, which can never be re-registered, get no repair estimate.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

import yaml

from okshun.schema import Listing, Tri

RULES_FILE = Path(__file__).with_name("repair_rules.yaml")

PARTS_ONLY = "Parts only"
INSPECT = "Inspect first"
WORTH = "Worth a look"
THIN = "Thin margin"
NOT_WORTH = "Not worth it at this price"


def load_rules(path: Optional[Path] = None) -> dict:
    with open(path or RULES_FILE, encoding="utf-8") as f:
        return yaml.safe_load(f)


def car_class(listing: Listing, rules: dict) -> tuple[str, float]:
    make = (listing.make or "").lower()
    model = (listing.model or "").lower()
    budget = next((c for c in rules["car_classes"] if not c.get("makes")), {"name": "Budget", "multiplier": 1.0})
    for c in rules["car_classes"]:
        if make in (m.lower() for m in c.get("makes", [])):
            if model in (m.lower() for m in c.get("budget_models", [])):
                return budget["name"], float(budget["multiplier"])
            return c["name"], float(c["multiplier"])
    return budget["name"], float(budget["multiplier"])


def _item(item_id: str, rule: dict, kind: str, mult: float = 1.0) -> dict:
    inspect = bool(rule.get("inspect_first"))
    return {
        "id": item_id, "label": rule["label"], "kind": kind, "inspect": inspect,
        "low": None if inspect else round(rule["low"] * mult, -2),
        "high": None if inspect else round(rule["high"] * mult, -2),
    }


def verdict(profit_low: Optional[float], profit_high: Optional[float], outlay_high: float,
            inspect: bool, rules: dict) -> Optional[str]:
    if profit_high is None:
        return None
    v = rules.get("verdict", {})
    min_profit = v.get("min_profit", 10_000)
    if profit_high < min_profit:
        return NOT_WORTH
    if inspect:
        return INSPECT
    if profit_low is not None and profit_low >= min_profit and profit_low >= v.get("good_margin", 0.15) * outlay_high:
        return WORTH
    return THIN


def estimate(listing: Listing, rules: dict) -> Listing:
    name, mult = car_class(listing, rules)
    listing.car_class = name

    if listing.damage_code.value in ("code_4", "code_5"):
        listing.repair_items = []
        listing.repair_low = listing.repair_high = listing.road_low = listing.road_high = None
        listing.profit_low = listing.profit_high = listing.resale_value = None
        listing.repair_verdict = PARTS_ONLY
        return listing

    text = " ".join(x.lower() for x in (listing.primary_damage, listing.secondary_damage, listing.description) if x)
    items: list[dict] = []
    groups_used: set[str] = set()
    for rule in rules.get("damage_items", []):
        if rule["group"] in groups_used:
            continue
        if any(term.lower() in text for term in rule["terms"]):
            items.append(_item(rule["id"], rule, "repair", mult))
            groups_used.add(rule["group"])
    matched = {i["id"] for i in items}
    skip = {r["id"] for r in rules.get("damage_items", []) if set(r.get("skip_if", [])) & matched}
    items = [i for i in items if i["id"] not in skip]

    cond = rules.get("condition_items", {})
    if listing.keys_available == Tri.NO and "no_keys" in cond:
        items.append(_item("no_keys", cond["no_keys"], "repair", mult))
    explains_non_runner = groups_used & {"drivetrain", "flood", "fire"}
    if listing.runs_and_drives == Tri.NO and not explains_non_runner and "non_runner" in cond:
        items.append(_item("non_runner", cond["non_runner"], "repair", mult))
    if "baseline" in cond:
        items.append(_item("baseline", cond["baseline"], "repair", mult))
    hm = cond.get("high_mileage")
    if hm and listing.mileage_km and listing.mileage_km >= hm.get("min_km", 150_000):
        items.append(_item("high_mileage", hm, "repair", mult))

    road = rules.get("road_items", {})
    moving = "towing" if listing.runs_and_drives == Tri.NO else "transport"
    for key in (moving, "roadworthy", "registration"):
        if key in road:
            items.append(_item(key, road[key], "road"))
    if listing.damage_code.value == "code_3" and "code3" in road:
        items.append(_item("code3", road["code3"], "road"))

    known = [i for i in items if not i["inspect"]]
    inspect = any(i["inspect"] for i in items)
    listing.repair_items = items
    listing.repair_low = sum(i["low"] for i in known if i["kind"] == "repair")
    listing.repair_high = sum(i["high"] for i in known if i["kind"] == "repair")
    listing.road_low = sum(i["low"] for i in known if i["kind"] == "road")
    listing.road_high = sum(i["high"] for i in known if i["kind"] == "road")

    retail, all_in = listing.estimated_retail, listing.est_all_in_cost
    if retail and listing.damage_code.value == "code_3":
        retail = round(retail * (1 - rules.get("code3_resale_discount", 0)), -2)
    listing.resale_value = retail
    if retail and all_in:
        outlay_low = all_in + listing.repair_low + listing.road_low
        outlay_high = all_in + listing.repair_high + listing.road_high
        listing.profit_high = round(retail - outlay_low, 2)
        listing.profit_low = None if inspect else round(retail - outlay_high, 2)
        listing.repair_verdict = verdict(listing.profit_low, listing.profit_high, outlay_high, inspect, rules)
    else:
        listing.profit_low = listing.profit_high = None
        listing.repair_verdict = INSPECT if inspect else None
    return listing
