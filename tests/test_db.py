import sqlite3

from okshun import db


def test_old_database_gets_new_columns(tmp_path):
    path = tmp_path / "old.db"
    old = sqlite3.connect(path)
    old.execute("CREATE TABLE listings (source TEXT NOT NULL, source_lot_id TEXT NOT NULL, url TEXT NOT NULL,"
                " PRIMARY KEY (source, source_lot_id))")
    old.commit(); old.close()
    conn = db.connect(path)
    cols = {r[1] for r in conn.execute("PRAGMA table_info(listings)")}
    assert {"engine_number", "registration", "risk_score", "est_all_in_cost"} <= cols
    assert db.migrate(conn) == []
