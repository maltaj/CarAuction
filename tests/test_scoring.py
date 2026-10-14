from okshun.schema import Listing, Tri, estimate_all_in, parse_code
from okshun.scoring import load_rules, score

RULES = load_rules()


def make(**kw) -> Listing:
    base = dict(source="t", source_lot_id="1", url="demo://t/1")
    base.update(kw)
    return Listing(**base)


def test_clean_runner_is_low_risk():
    lst = score(make(runs_and_drives=Tri.YES, keys_available=Tri.YES, odometer_status="Actual",
                     damage_code_raw="Code 2", damage_code=parse_code("Code 2"), mileage_km=60_000), RULES)
    assert lst.risk_label == "Low Risk"
    assert lst.risk_score == 0


def test_flooded_code4_non_runner_is_high_risk():
    lst = score(make(runs_and_drives=Tri.NO, keys_available=Tri.NO, damage_code_raw="Code 4",
                     primary_damage="Flood damage, water in cabin"), RULES)
    assert lst.risk_label == "High Risk"
    assert any("structural" in r for r in lst.risk_reasons)


def test_sub_code_matches_before_broad_code():
    lst = score(make(damage_code_raw="Code 3C"), RULES)
    assert any(r.startswith("Code 3C (+7)") for r in lst.risk_reasons)


def test_unquoted_yes_no_keys_still_apply():
    rules = dict(RULES, run_and_drive={True: 0, False: 8, "unknown": 3})
    lst = score(make(runs_and_drives=Tri.NO), rules)
    assert "Runs and drives: no (+8)" in lst.risk_reasons


def test_high_bid_to_retail_ratio_adds_risk():
    lst = score(make(current_bid=90_000, estimated_retail=100_000), RULES)
    assert any("90% of retail" in r for r in lst.risk_reasons)


def test_all_in_cost_includes_commission_fees_and_vat():
    lst = make(current_bid=100_000, buyers_commission_pct=10, fixed_fees=2_500, vat_on_hammer=False)
    estimate_all_in(lst)
    assert lst.est_all_in_cost == round((100_000 + 10_000 + 2_500) * 1.15, 2)


def test_hail_on_the_roof_is_cosmetic_not_structural():
    lst = score(make(primary_damage="hail damage to roof and bonnet"), RULES)
    assert not any("structural" in r for r in lst.risk_reasons)
    assert any("cosmetic" in r and "hail" in r for r in lst.risk_reasons)
    rolled = score(make(primary_damage="rollover, roof pillars damaged"), RULES)
    assert any("structural" in r for r in rolled.risk_reasons)


def test_good_signs_cannot_hide_no_keys_and_high_mileage():
    # The 2012 Polo Vivo case: runs, Code 2, actual odometer, but no keys and 285 610 km.
    lst = score(make(runs_and_drives=Tri.YES, keys_available=Tri.NO, odometer_status="Actual",
                     damage_code_raw="Code 2", damage_code=parse_code("Code 2"), mileage_km=285_610), RULES)
    assert lst.risk_score == 4 + 3
    assert lst.risk_label == "Medium Risk"


def test_no_score_is_ever_negative_with_default_rules():
    lst = score(make(runs_and_drives=Tri.YES, keys_available=Tri.YES, odometer_status="Actual",
                     damage_code_raw="Code 2", mileage_km=20_000, primary_damage="small scratch"), RULES)
    assert lst.risk_score >= 0
    assert all("(-" not in r for r in lst.risk_reasons)
