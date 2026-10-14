import importlib
import json
import sqlite3
from datetime import datetime, timedelta

import pytest
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import encode_dss_signature
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from fastapi.testclient import TestClient

from okshun import alerts, calendar, db, push, users
from okshun.adapters.demo import DemoAdapter

NOW = datetime(2026, 10, 11, 8, 0)
H = {"X-Okshun": "1"}


def iso(dt):
    return dt.isoformat(timespec="seconds")


@pytest.fixture()
def conn(tmp_path):
    c = db.connect(tmp_path / "rem.db")
    db.ingest(c, [DemoAdapter(now=NOW)])
    return c


def set_time(conn, key, col, dt):
    conn.execute(f"UPDATE listings SET {col} = ? WHERE source = ? AND source_lot_id = ?", (iso(dt), *key))
    conn.commit()


def kinds(conn, uid):
    return sorted(r[0] for r in conn.execute("SELECT kind FROM alerts WHERE user_id = ?", (uid,)))


# ---------- push encryption and VAPID ----------

def _decrypt(body: bytes, ua_private, auth: bytes) -> bytes:
    """Independent receiver side of RFC 8291, as a browser would do it."""
    salt, rs, idlen = body[:16], int.from_bytes(body[16:20], "big"), body[20]
    as_public, ciphertext = body[21:21 + idlen], body[21 + idlen:]
    ua_public = ua_private.public_key().public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
    shared = ua_private.exchange(ec.ECDH(), ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), as_public))
    hk = lambda s, k, info, n: HKDF(algorithm=hashes.SHA256(), length=n, salt=s, info=info).derive(k)
    ikm = hk(auth, shared, b"WebPush: info\x00" + ua_public + as_public, 32)
    plain = AESGCM(hk(salt, ikm, b"Content-Encoding: aes128gcm\x00", 16)).decrypt(hk(salt, ikm, b"Content-Encoding: nonce\x00", 12), ciphertext, None)
    assert rs == 4096 and plain.endswith(b"\x02")
    return plain[:-1]


def test_push_payload_round_trip():
    ua = ec.generate_private_key(ec.SECP256R1())
    ua_pub = ua.public_key().public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
    auth = b"sixteen byte key"
    msg = json.dumps({"title": "Closes in 15 minutes", "body": "2021 Toyota Hilux"}).encode()
    assert _decrypt(push.encrypt(msg, push.b64url(ua_pub), push.b64url(auth)), ua, auth) == msg


def test_push_payload_interoperates_with_http_ece():
    http_ece = pytest.importorskip("http_ece")
    ua = ec.generate_private_key(ec.SECP256R1())
    ua_pub = ua.public_key().public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
    body = push.encrypt(b"hello", push.b64url(ua_pub), push.b64url(b"0123456789abcdef"))
    assert http_ece.decrypt(body, private_key=ua, auth_secret=b"0123456789abcdef", version="aes128gcm") == b"hello"


def test_vapid_token_is_signed_for_the_push_service():
    priv, pub = push.generate_keys()
    header = push.vapid_authorization("https://fcm.googleapis.com/fcm/send/abc", priv, pub, "mailto:x@y.co")
    token = header.split("t=")[1].split(",")[0]
    h, c, s = token.split(".")
    claims = json.loads(push.unb64url(c))
    assert claims["aud"] == "https://fcm.googleapis.com" and claims["sub"] == "mailto:x@y.co"
    sig = push.unb64url(s)
    key = ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), push.unb64url(pub))
    key.verify(encode_dss_signature(int.from_bytes(sig[:32], "big"), int.from_bytes(sig[32:], "big")),
               f"{h}.{c}".encode(), ec.ECDSA(hashes.SHA256()))


def test_vapid_keys_are_created_once(conn, monkeypatch):
    monkeypatch.delenv("OKSHUN_VAPID_PRIVATE", raising=False)
    assert push.vapid_keys(conn) == push.vapid_keys(conn)


def test_gone_subscriptions_are_removed(conn):
    uid = users.register(conn, "b@example.com", "longenough")
    users.add_push_subscription(conn, uid, "https://push.example/a", "k", "a")
    users.add_push_subscription(conn, uid, "https://push.example/b", "k", "a")
    statuses = {"https://push.example/a": 201, "https://push.example/b": 410}
    assert push.push_to_user(conn, uid, {"title": "x"}, sender=lambda sub, *a: statuses[sub["endpoint"]]) == 1
    left = [r[0] for r in conn.execute("SELECT endpoint FROM push_subscriptions")]
    assert left == ["https://push.example/a"]


# ---------- reminder timing ----------

def test_only_the_most_relevant_reminder_fires(conn):
    uid = users.register(conn, "b@example.com", "longenough")
    users.set_reminder_prefs(conn, uid, offsets=[1440, 120, 15])
    key = tuple(conn.execute("SELECT source, source_lot_id FROM listings WHERE auction_type = 'timed' LIMIT 1").fetchone())
    users.watch(conn, uid, *key)
    set_time(conn, key, "auction_end", NOW + timedelta(minutes=50))   # watched late: 1 day and 2 hours are past
    alerts.find_new(conn, NOW)
    assert kinds(conn, uid) == ["closing_120"]
    alerts.find_new(conn, NOW + timedelta(minutes=40))               # 10 minutes left
    assert kinds(conn, uid) == ["closing_120", "closing_15"]
    alerts.find_new(conn, NOW + timedelta(minutes=45))
    assert kinds(conn, uid) == ["closing_120", "closing_15"]          # nothing repeats


def test_live_sales_remind_before_the_start(conn):
    uid = users.register(conn, "b@example.com", "longenough")
    key = tuple(conn.execute("SELECT source, source_lot_id FROM listings WHERE auction_type = 'live' LIMIT 1").fetchone())
    set_time(conn, key, "auction_start", NOW + timedelta(minutes=100))
    set_time(conn, key, "auction_end", NOW + timedelta(hours=6))
    users.watch(conn, uid, *key)
    alerts.find_new(conn, NOW)
    assert kinds(conn, uid) == ["starting_120"]
    title, body = alerts.list_alerts(conn, uid, {})[0]["headline"], alerts.list_alerts(conn, uid, {})[0]["detail"]
    assert title == "Live sale starts in 1 hour 40 minutes" and "lot" in body


def test_per_lot_reminders_override_usual_times(conn):
    uid = users.register(conn, "b@example.com", "longenough")
    key = tuple(conn.execute("SELECT source, source_lot_id FROM listings WHERE auction_type = 'timed' LIMIT 1").fetchone())
    users.set_watch_reminders(conn, uid, *key, [15])
    set_time(conn, key, "auction_end", NOW + timedelta(minutes=60))
    alerts.find_new(conn, NOW)
    assert kinds(conn, uid) == []                       # usual 2-hour reminder overridden
    users.set_watch_reminders(conn, uid, *key, [])      # no reminders at all for this lot
    alerts.find_new(conn, NOW + timedelta(minutes=50))
    assert kinds(conn, uid) == []
    users.set_watch_reminders(conn, uid, *key, None)    # back to usual times
    alerts.find_new(conn, NOW + timedelta(minutes=51))
    assert kinds(conn, uid) == ["closing_120"]


def test_over_limit_alert_and_new_limit_can_alert_again(conn):
    uid = users.register(conn, "b@example.com", "longenough")
    key = tuple(conn.execute("SELECT source, source_lot_id FROM listings WHERE current_bid IS NOT NULL LIMIT 1").fetchone())
    bid = conn.execute("SELECT current_bid FROM listings WHERE source = ? AND source_lot_id = ?", key).fetchone()[0]
    users.set_note(conn, uid, "limit", *key, {"bid": bid + 5000})
    alerts.find_new(conn, NOW)
    assert kinds(conn, uid) == []
    conn.execute("UPDATE listings SET current_bid = ? WHERE source = ? AND source_lot_id = ?", (bid + 6000, *key))
    alerts.find_new(conn, NOW)
    assert kinds(conn, uid) == ["over_limit"]
    a = alerts.list_alerts(conn, uid, {})[0]
    assert a["headline"] == "Bidding passed your limit" and "limit" in a["detail"]
    users.set_note(conn, uid, "limit", *key, {"bid": bid + 10000})   # raised limit clears the old alert
    alerts.find_new(conn, NOW)
    assert kinds(conn, uid) == []


def test_pushes_group_new_matches_and_send_reminders_individually(conn):
    uid = users.register(conn, "b@example.com", "longenough")
    users.add_push_subscription(conn, uid, "https://push.example/a", "k", "a")
    sid = users.save_search(conn, uid, "Anything", "")
    conn.execute("UPDATE saved_searches SET last_checked_at = ?", (iso(NOW - timedelta(days=30)),))
    key = tuple(conn.execute("SELECT source, source_lot_id FROM listings WHERE auction_type = 'timed' LIMIT 1").fetchone())
    users.watch(conn, uid, *key)
    set_time(conn, key, "auction_end", NOW + timedelta(minutes=10))
    sent = []
    counts = alerts.run(conn, now=NOW, mailer=None, sender=lambda sub, payload, *a: sent.append(payload) or 201, sources={})
    assert counts["new_match"] > 1 and counts["reminder"] == 1
    titles = [p["title"] for p in sent]
    assert any(t.startswith("Closes in 10 minutes") for t in titles)
    assert any("new lots match your saved searches" in t for t in titles) and len(sent) == 2
    assert alerts.run(conn, now=NOW, mailer=None, sender=lambda *a: sent.append(1) or 201, sources={})["pushes"] == 0


def test_push_off_means_no_pushes(conn):
    uid = users.register(conn, "b@example.com", "longenough")
    users.add_push_subscription(conn, uid, "https://push.example/a", "k", "a")
    users.set_reminder_prefs(conn, uid, push=False)
    key = tuple(conn.execute("SELECT source, source_lot_id FROM listings WHERE auction_type = 'timed' LIMIT 1").fetchone())
    users.watch(conn, uid, *key)
    set_time(conn, key, "auction_end", NOW + timedelta(minutes=10))
    assert alerts.run(conn, now=NOW, mailer=None, sender=lambda *a: 201, sources={})["pushes"] == 0


def test_old_database_gets_reminder_columns(tmp_path):
    path = tmp_path / "old.db"
    old = sqlite3.connect(path)
    old.executescript("""
        CREATE TABLE users (id INTEGER PRIMARY KEY, email TEXT NOT NULL UNIQUE COLLATE NOCASE, password_hash TEXT NOT NULL,
                            created_at TEXT NOT NULL, email_reminders INTEGER NOT NULL DEFAULT 1);
        CREATE TABLE watchlist (user_id INTEGER NOT NULL, source TEXT NOT NULL, source_lot_id TEXT NOT NULL, added_at TEXT NOT NULL,
                                PRIMARY KEY (user_id, source, source_lot_id));
        CREATE TABLE alerts (id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL, kind TEXT NOT NULL, saved_search_id INTEGER,
                             source TEXT NOT NULL, source_lot_id TEXT NOT NULL, created_at TEXT NOT NULL, read_at TEXT,
                             emailed_at TEXT, UNIQUE (user_id, kind, source, source_lot_id));
    """)
    old.commit(); old.close()
    c = db.connect(path)
    cols = lambda t: {r[1] for r in c.execute(f"PRAGMA table_info({t})")}
    assert {"push_reminders", "reminder_offsets"} <= cols("users")
    assert "reminders" in cols("watchlist") and "pushed_at" in cols("alerts")


# ---------- calendar ----------

def test_calendar_event_for_live_and_timed():
    live = {"source": "demo-x", "source_lot_id": "CO-1", "title": "2020 Kia Picanto", "auction_type": "live",
            "auction_start": "2026-10-12T10:00:00", "auction_end": "2026-10-12T14:00:00", "lot_number": 7,
            "source_name": "Coastal Demo", "est_all_in_cost": 85000, "url": "demo://x"}
    ics = calendar.lot_event(live, base_url="https://okshun.test")
    assert "DTSTART:20261012T080000Z" in ics            # 10:00 SAST
    assert "Live sale: 2020 Kia Picanto (lot 7)" in ics and "TRIGGER:-PT30M" in ics
    assert all(len(line.encode()) <= 75 for line in ics.split("\r\n"))
    timed = dict(live, auction_type="timed", auction_start=None)
    assert "DTSTART:20261012T120000Z" in calendar.lot_event(timed) and "Closes:" in calendar.lot_event(timed)
    assert calendar.lot_event(dict(timed, auction_end=None)) is None


# ---------- API ----------

@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("OKSHUN_DB", str(tmp_path / "api.db"))
    monkeypatch.setenv("OKSHUN_REMINDER_INTERVAL", "0")
    import okshun.api as api
    importlib.reload(api)
    with TestClient(api.app) as c:
        yield c


def test_reminder_and_push_endpoints(client):
    client.post("/api/auth/register", json={"email": "b@example.com", "password": "longenough"}, headers=H)
    assert client.get("/api/me").json()["user"]["reminder_offsets"] == [120]
    client.patch("/api/me", json={"reminder_offsets": [15, 1440, 7]}, headers=H)
    assert client.get("/api/me").json()["user"]["reminder_offsets"] == [1440, 15]
    item = client.get("/api/listings", params={"limit": 1}).json()["items"][0]
    key = f"{item['source']}/{item['source_lot_id']}"
    client.put(f"/api/me/watchlist/{key}", json={"reminders": [30]}, headers=H)
    data = client.get("/api/me/data").json()
    assert data["reminders"] == {key: [30]} and key in data["watchlist"] and data["push_devices"] == 0
    assert client.get("/api/push/key").json()["key"]
    assert client.post("/api/me/push", json={"endpoint": "http://not-https", "keys": {}}, headers=H).status_code == 400
    assert client.post("/api/me/push", json={"endpoint": "https://push.example/x", "keys": {"p256dh": "k", "auth": "a"}},
                       headers=H).json() == {"ok": True}
    assert client.get("/api/me/data").json()["push_devices"] == 1
    client.request("DELETE", "/api/me/push", json={"endpoint": "https://push.example/x"}, headers=H)
    assert client.post("/api/me/push/test", headers=H).status_code == 502   # no devices left


def test_calendar_endpoint(client):
    item = client.get("/api/listings", params={"limit": 1}).json()["items"][0]
    r = client.get(f"/api/listings/{item['source']}/{item['source_lot_id']}/calendar.ics")
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/calendar") and "BEGIN:VEVENT" in r.text


def test_app_files_for_phones_are_served(client):
    assert client.get("/sw.js").status_code == 200
    manifest = client.get("/manifest.webmanifest").json()
    assert manifest["short_name"] == "Okshun" and manifest["display"] == "standalone"
    assert client.get("/icon-192.png").headers["content-type"] == "image/png"
