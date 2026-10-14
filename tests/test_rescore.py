from okshun import db
from okshun.adapters.demo import DemoAdapter
from okshun.scoring import load_rules


def _old_rules():
    # The rules before the fix: good signs subtracted points.
    r = load_rules()
    r["run_and_drive"] = {"yes": -5, "no": 8, "unknown": 3}
    r["thresholds"] = {"low_max": 0, "medium_max": 8}
    return r


def test_start_up_rescores_a_database_scored_with_old_rules(tmp_path):
    conn = db.connect(tmp_path / "t.db")
    db.ingest(conn, [DemoAdapter()], rules=_old_rules())
    db._meta(conn, "rules_fingerprint", "old-download")
    conn.commit()
    assert conn.execute("SELECT MIN(risk_score) FROM listings").fetchone()[0] < 0

    assert db.rescore_if_rules_changed(conn) > 0
    assert conn.execute("SELECT MIN(risk_score) FROM listings").fetchone()[0] >= 0
    # nothing to do the second time
    assert db.rescore_if_rules_changed(conn) == 0


def test_rescore_matches_a_fresh_ingest(tmp_path):
    a = db.connect(tmp_path / "a.db")
    db.ingest(a, [DemoAdapter()])
    b = db.connect(tmp_path / "b.db")
    db.ingest(b, [DemoAdapter()], rules=_old_rules())
    db.rescore(b)
    q = ("SELECT source_lot_id, risk_score, risk_label, est_all_in_cost, profit_low, repair_verdict "
         "FROM listings ORDER BY source_lot_id")
    assert [tuple(r) for r in a.execute(q)] == [tuple(r) for r in b.execute(q)]


def test_index_versions_script_links():
    from fastapi.testclient import TestClient
    from okshun.api import app
    with TestClient(app) as c:
        r = c.get("/")
    assert '"/app.js?v=' in r.text and '"/styles.css?v=' in r.text
    assert r.headers["cache-control"] == "no-cache"
