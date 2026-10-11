from okshun import repairs
from okshun.schema import Listing, Tri, estimate_all_in, parse_code

RULES = repairs.load_rules()


def lot(**kw) -> Listing:
    base = dict(source="t", source_lot_id="1", url="demo://t/1", make="Kia", model="Picanto",
                runs_and_drives=Tri.YES, keys_available=Tri.YES, mileage_km=60_000,
                damage_code_raw="Code 2", damage_code=parse_code("Code 2"),
                current_bid=50_000, buyers_commission_pct=10, fixed_fees=2_000, vat_on_hammer=True,
                estimated_retail=120_000)
    base.update(kw)
    lst = Listing(**base)
    estimate_all_in(lst)
    return repairs.estimate(lst, RULES)


def ids(lst):
    return [i["id"] for i in lst.repair_items]


def test_parts_only_cars_get_no_estimate():
    lst = lot(damage_code_raw="Code 4", damage_code=parse_code("Code 4"), primary_damage="chassis bent")
    assert lst.repair_verdict == repairs.PARTS_ONLY
    assert lst.repair_items == [] and lst.profit_low is None


def test_flood_is_inspect_first_with_upper_bound_only():
    lst = lot(primary_damage="Flood damaged, water in cabin", runs_and_drives=Tri.NO)
    assert "flood" in ids(lst) and "non_runner" not in ids(lst)  # flood explains it
    assert lst.profit_low is None and lst.profit_high is not None
    assert lst.repair_verdict == repairs.INSPECT


def test_bumper_repair_includes_its_paint():
    lst = lot(primary_damage="front bumper scratched")
    assert "bumper" in ids(lst) and "paint" not in ids(lst)


def test_luxury_cars_cost_more_to_fix():
    budget = lot(primary_damage="cracked windscreen")
    luxury = lot(make="BMW", model="3 Series", primary_damage="cracked windscreen")
    w = lambda l: next(i for i in l.repair_items if i["id"] == "windscreen")
    assert luxury.car_class == "Luxury" and w(luxury)["high"] > w(budget)["high"]


def test_budget_model_of_mid_range_make():
    assert lot(make="Volkswagen", model="Polo Vivo").car_class == "Budget"
    assert lot(make="Volkswagen", model="Polo").car_class == "Mid-range"


def test_code3_resale_is_discounted_and_needs_clearance():
    lst = lot(damage_code_raw="Code 3A", damage_code=parse_code("Code 3A"))
    assert lst.resale_value == 120_000 * (1 - RULES["code3_resale_discount"])
    assert "code3" in ids(lst)


def test_profit_range_maths():
    lst = lot()
    assert lst.profit_high == lst.resale_value - (lst.est_all_in_cost + lst.repair_low + lst.road_low)
    assert lst.profit_low == lst.resale_value - (lst.est_all_in_cost + lst.repair_high + lst.road_high)
    assert lst.profit_low <= lst.profit_high


def test_verdicts_follow_margin():
    assert lot(current_bid=40_000).repair_verdict == repairs.WORTH
    assert lot(current_bid=100_000).repair_verdict == repairs.NOT_WORTH
