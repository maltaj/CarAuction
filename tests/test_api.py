import importlib

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("OKSHUN_DB", str(tmp_path / "test.db"))
    import okshun.api as api
    importlib.reload(api)
    with TestClient(api.app) as c:
        yield c


def test_demo_data_loads_on_first_start(client):
    facets = client.get("/api/facets").json()
    assert facets["stats"]["n"] == 164
    assert len(facets["source"]) == 5
    assert {r["value"] for r in facets["risk"]} <= {"Low Risk", "Medium Risk", "High Risk"}


def test_filters_combine(client):
    all_toyota = client.get("/api/listings", params={"make": "Toyota", "limit": 100}).json()
    low_toyota = client.get("/api/listings", params={"make": "Toyota", "risk": "Low Risk", "limit": 100}).json()
    assert 0 < low_toyota["total"] <= all_toyota["total"]
    assert all(i["make"] == "Toyota" and i["risk_label"] == "Low Risk" for i in low_toyota["items"])


def test_max_cost_and_sort(client):
    res = client.get("/api/listings", params={"max_cost": 100_000, "sort": "cost", "limit": 100}).json()
    costs = [i["est_all_in_cost"] for i in res["items"]]
    assert costs == sorted(costs) and all(c <= 100_000 for c in costs)


def test_gap_hidden_for_parts_only_cars(client):
    res = client.get("/api/listings", params={"code": "code_4", "limit": 100}).json()
    assert res["total"] > 0
    assert all(i["gap_to_retail"] is None for i in res["items"])


def test_listing_detail_and_404(client):
    item = client.get("/api/listings", params={"limit": 1}).json()["items"][0]
    detail = client.get(f"/api/listings/{item['source']}/{item['source_lot_id']}").json()
    assert detail["demo"] is True and detail["risk_reasons"]
    assert client.get("/api/listings/nope/nope").status_code == 404


def test_bad_sort_is_rejected(client):
    assert client.get("/api/listings", params={"sort": "drop table"}).status_code == 422


def test_front_end_is_served(client):
    page = client.get("/")
    assert page.status_code == 200 and "Okshun" in page.text
    assert client.get("/app.js").status_code == 200
