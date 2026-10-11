"""
Shared risk scoring for every auction source.

A transparent, additive point system (higher = riskier) driven by rules in
scoring_rules.yaml. It reads only shared `Listing` fields, so a GoBid lot and
a demo lot are judged by exactly the same rules. Ported from
gobid_scraper.score_listing.

The score is a screening aid, not a verdict: it can't see anything the
auction house didn't publish.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

import yaml

from okshun.schema import Listing, Tri

RULES_FILE = Path(__file__).with_name("scoring_rules.yaml")
LABELS = ("Low Risk", "Medium Risk", "High Risk")


def load_rules(path: Optional[Path] = None) -> dict:
    with open(path or RULES_FILE, encoding="utf-8") as f:
        return yaml.safe_load(f)


def _yn(section: Optional[dict]) -> dict:
    """Accept both `"yes": -5` and unquoted `yes: -5` (which YAML reads as True)."""
    out = dict(section or {})
    if True in out and "yes" not in out:
        out["yes"] = out[True]
    if False in out and "no" not in out:
        out["no"] = out[False]
    return out


def score(listing: Listing, rules: dict) -> Listing:
    total = 0.0
    reasons: list[str] = []

    def add(points: float, reason: str) -> None:
        nonlocal total
        if points:
            total += points
            reasons.append(f"{reason} ({points:+g})")

    for field, label in (("runs_and_drives", "Runs and drives"), ("keys_available", "Keys")):
        value: Tri = getattr(listing, field)
        rule = _yn(rules.get(field if field != "runs_and_drives" else "run_and_drive"))
        add(rule.get(value.value, 0), f"{label}: {value.value}")

    odo_rules = rules.get("odometer_status", {})
    odo = (listing.odometer_status or "").strip().lower()
    if "not actual" in odo:
        add(odo_rules.get("not_actual", 0), "Odometer: not actual")
    elif "exempt" in odo:
        add(odo_rules.get("exempt", 0), "Odometer: exempt")
    elif "actual" in odo:
        add(odo_rules.get("actual", 0), "Odometer: actual")
    else:
        add(odo_rules.get("unknown", 0), "Odometer: unknown")

    code = (listing.damage_code_raw or "").strip().lower()
    if code:
        for key, pts in rules.get("damage_code_points", {}).items():
            if key.lower() in code:
                add(pts, f"{listing.damage_code_raw}")
                break
        else:
            add(rules.get("damage_code_unknown_points", 0), f"Code '{listing.damage_code_raw}' not in rules")

    text = " ".join(x.lower() for x in (listing.primary_damage, listing.secondary_damage, listing.description) if x)
    for category, rule in rules.get("damage_keywords", {}).items():
        hits = [t for t in rule.get("terms", []) if t.lower() in text]
        if hits:
            add(rule.get("points", 0), f"Mentions {category} damage: {', '.join(hits)}")

    mrules = rules.get("mileage_thresholds", {})
    km = listing.mileage_km
    if km is not None:
        if mrules.get("low_max") is not None and km <= mrules["low_max"]:
            add(mrules.get("low_points", 0), f"{km:,} km (low)".replace(",", " "))
        elif mrules.get("high_min") is not None and km >= mrules["high_min"]:
            add(mrules.get("high_points", 0), f"{km:,} km (high)".replace(",", " "))

    bid = listing.current_bid or listing.starting_bid
    retail = listing.estimated_retail
    rrules = rules.get("bid_to_value_ratio", {})
    if bid and retail and retail > 0 and rrules.get("high_ratio_threshold") is not None:
        ratio = bid / retail
        if ratio >= rrules["high_ratio_threshold"]:
            add(rrules.get("high_ratio_points", 0), f"Bid is {ratio:.0%} of retail value")

    th = rules.get("thresholds", {"low_max": 0, "medium_max": 8})
    if total <= th.get("low_max", 0):
        label = LABELS[0]
    elif total <= th.get("medium_max", 8):
        label = LABELS[1]
    else:
        label = LABELS[2]

    listing.risk_score = int(round(total))
    listing.risk_label = label
    listing.risk_reasons = reasons or ["No scoring signals matched"]
    return listing
