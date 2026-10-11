import pytest

pytest.importorskip("playwright")
pytest.importorskip("yaml")

import gobid_scraper as g  # noqa: E402

from okshun.adapters.gobid import GoBidAdapter  # noqa: E402
from okshun.scoring import load_rules, score  # noqa: E402


def test_scraper_rows_map_to_shared_listing():
    row = g.Listing(lot_id="12345", url="https://www.gobid.co.za/lot/12345", title="2018 Toyota Hilux 2.4 GD-6",
                    mileage="98 500 km", damage_code="Code 3A", primary_damage="Front bumper scratch",
                    run_and_drive="Yes", keys_available="Yes", odometer_status="Actual", auction_type="Timed",
                    current_bid="R 145 000", starting_bid="R 120 000", estimated_value="R 310 000",
                    photo_urls="https://a/1.jpg;https://a/2.jpg")
    [lst] = GoBidAdapter([row]).run()
    assert (lst.year, lst.make, lst.model) == (2018, "Toyota", "Hilux 2.4 GD-6")
    assert lst.mileage_km == 98_500 and lst.current_bid == 145_000
    assert lst.photo_urls == ["https://a/1.jpg", "https://a/2.jpg"]
    assert score(lst, load_rules()).risk_label == "Low Risk"
