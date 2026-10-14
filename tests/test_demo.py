from datetime import datetime

from okshun.adapters import SOURCES
from okshun.adapters.demo import MODELS, DemoAdapter

NOW = datetime(2026, 10, 11, 8, 0)


def test_demo_is_deterministic_and_complete():
    a = DemoAdapter(now=NOW).run()
    b = DemoAdapter(now=NOW).run()
    assert [l.source_lot_id for l in a] == [l.source_lot_id for l in b]
    assert len(a) == 164
    assert {l.source for l in a} == set(SOURCES)


def test_demo_houses_are_clearly_fictional():
    for src in SOURCES.values():
        assert "Demo" in src["name"] and src["demo"] is True
    for lst in DemoAdapter(now=NOW).run():
        assert lst.url.startswith("demo://")


def test_model_years_are_real():
    ranges = {m[1]: (m[5], m[6]) for m in MODELS}
    for lst in DemoAdapter(now=NOW).run():
        first, last = ranges[lst.model]
        assert lst.year >= first and (last is None or lst.year <= last)


def test_lots_close_in_the_future_and_have_prices():
    for lst in DemoAdapter(now=NOW).run():
        assert lst.auction_end > NOW
        assert lst.starting_bid and lst.estimated_retail
        assert lst.current_bid is None or lst.current_bid >= lst.starting_bid
