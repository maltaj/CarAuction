from datetime import datetime, timedelta

import pytest

from okshun import alerts, db, users
from okshun.adapters.demo import DemoAdapter

NOW = datetime(2026, 10, 11, 8, 0)


class FakeMailer:
    def __init__(self):
        self.sent = []

    def send(self, to, subject, body):
        self.sent.append((to, subject, body))


@pytest.fixture()
def conn(tmp_path):
    c = db.connect(tmp_path / "alerts.db")
    db.ingest(c, [DemoAdapter(now=NOW)])
    return c


def iso(dt):
    return dt.isoformat(timespec="seconds")


def loaded_at(conn):
    """When the demo lots were stored (upsert stamps the real clock)."""
    return datetime.fromisoformat(conn.execute("SELECT MAX(first_seen) FROM listings").fetchone()[0])


def add_lot_like(conn, make, lot_id, first_seen):
    cols = [r[1] for r in conn.execute("PRAGMA table_info(listings)")]
    row = dict(zip(cols, conn.execute("SELECT * FROM listings WHERE make = ? LIMIT 1", (make,)).fetchone()))
    row.update(source_lot_id=lot_id, first_seen=iso(first_seen), auction_end=iso(first_seen + timedelta(days=5)))
    conn.execute(f"INSERT INTO listings ({','.join(cols)}) VALUES ({','.join('?' * len(cols))})", list(row.values()))
    conn.commit()


def test_filters_from_query():
    f = db.filters_from_query("?make=Toyota&make=Ford&risk=Low+Risk&max_cost=150000&runs=true&q=hilux")
    assert f["make"] == ["Toyota", "Ford"] and f["risk"] == ["Low Risk"]
    assert f["max_cost"] == "150000" and f["runs"] is True and f["q"] == "hilux"


def test_new_match_alert_and_single_email(conn):
    uid = users.register(conn, "b@example.com", "longenough")
    users.save_search(conn, uid, "Toyotas", "make=Toyota")
    t0 = loaded_at(conn)
    conn.execute("UPDATE saved_searches SET last_checked_at = ?", (iso(t0),))
    add_lot_like(conn, "Toyota", "HV-90001", t0 + timedelta(minutes=5))
    add_lot_like(conn, "Kia", "HV-90002", t0 + timedelta(minutes=5))     # doesn't match the search
    mailer = FakeMailer()
    counts = alerts.run(conn, now=t0 + timedelta(minutes=10), mailer=mailer, base_url="http://okshun.test", sender=lambda *a: 201)
    assert counts["new_match"] == 1 and counts["emails"] == 1
    to, subject, body = mailer.sent[0]
    assert to == "b@example.com" and "HV-90001" in body and "Toyotas" in body and "http://okshun.test" in body
    again = alerts.run(conn, now=t0 + timedelta(minutes=20), mailer=mailer)
    assert again["new_match"] == 0 and again["emails"] == 0 and len(mailer.sent) == 1


def test_email_off_still_creates_in_app_alert(conn):
    uid = users.register(conn, "b@example.com", "longenough")
    users.save_search(conn, uid, "Toyotas", "make=Toyota", email=False)
    t0 = loaded_at(conn)
    conn.execute("UPDATE saved_searches SET last_checked_at = ?", (iso(t0),))
    add_lot_like(conn, "Toyota", "HV-90001", t0 + timedelta(minutes=5))
    mailer = FakeMailer()
    assert alerts.run(conn, now=t0 + timedelta(minutes=10), mailer=mailer)["emails"] == 0
    listed = alerts.list_alerts(conn, uid, {})
    assert len(listed) == 1 and listed[0]["kind"] == "new_match" and listed[0]["read_at"] is None
    alerts.mark_read(conn, uid)
    assert alerts.list_alerts(conn, uid, {})[0]["read_at"] is not None


def test_closing_reminder_only_inside_window(conn):
    uid = users.register(conn, "b@example.com", "longenough")
    soon, later = conn.execute("SELECT source, source_lot_id FROM listings WHERE auction_type = 'timed' LIMIT 2").fetchall()
    conn.execute("UPDATE listings SET auction_end = ? WHERE source = ? AND source_lot_id = ?", (iso(NOW + timedelta(minutes=90)), *soon))
    conn.execute("UPDATE listings SET auction_end = ? WHERE source = ? AND source_lot_id = ?", (iso(NOW + timedelta(hours=5)), *later))
    users.watch(conn, uid, *soon)
    users.watch(conn, uid, *later)
    mailer = FakeMailer()
    counts = alerts.run(conn, now=NOW, mailer=mailer, sender=lambda *a: 201)
    assert counts["reminder"] == 1 and "closes in 1 hour 30 minutes" in mailer.sent[0][1].lower()
    users.set_email_reminders(conn, uid, False)
    conn.execute("UPDATE listings SET auction_end = ? WHERE source = ? AND source_lot_id = ?", (iso(NOW + timedelta(minutes=100)), *later))
    again = alerts.run(conn, now=NOW + timedelta(minutes=30), mailer=mailer)
    assert again["reminder"] == 1 and again["emails"] == 0


def test_no_mailer_configured_means_in_app_only(conn, monkeypatch):
    for k in ("OKSHUN_SMTP_HOST", "OKSHUN_MAIL_FROM"):
        monkeypatch.delenv(k, raising=False)
    assert alerts.SmtpMailer.from_env() is None
    assert alerts.run(conn, now=NOW)["emails"] == 0
