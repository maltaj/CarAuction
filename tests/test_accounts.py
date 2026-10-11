import importlib

import pytest
from fastapi.testclient import TestClient

from okshun import users

H = {"X-Okshun": "1"}


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("OKSHUN_DB", str(tmp_path / "acct.db"))
    import okshun.api as api
    importlib.reload(api)
    with TestClient(api.app) as c:
        yield c


def signup(c, email="buyer@example.com", password="correct-horse"):
    r = c.post("/api/auth/register", json={"email": email, "password": password}, headers=H)
    assert r.status_code == 200, r.text
    return r


def lot_keys(c, n=2):
    items = c.get("/api/listings", params={"limit": n}).json()["items"]
    return [f"{i['source']}/{i['source_lot_id']}" for i in items]


def test_password_hashing():
    h = users.hash_password("s3cret-pass")
    assert "s3cret-pass" not in h and users.verify_password("s3cret-pass", h)
    assert not users.verify_password("wrong", h) and not users.verify_password("x", "garbage")


def test_signup_rules(client):
    bad = client.post("/api/auth/register", json={"email": "nope", "password": "longenough"}, headers=H)
    assert bad.status_code == 400 and "valid email" in bad.json()["detail"]
    short = client.post("/api/auth/register", json={"email": "a@b.co", "password": "short"}, headers=H)
    assert "8 characters" in short.json()["detail"]
    signup(client, "a@b.co")
    dup = client.post("/api/auth/register", json={"email": "A@B.CO", "password": "longenough"}, headers=H)
    assert dup.status_code == 400 and "already exists" in dup.json()["detail"]


def test_changes_need_the_okshun_header(client):
    r = client.post("/api/auth/register", json={"email": "a@b.co", "password": "longenough"})
    assert r.status_code == 403


def test_session_cookie_and_logout(client):
    r = signup(client)
    assert r.json()["user"] == {"email": "buyer@example.com", "email_reminders": True,
                                "push_reminders": True, "reminder_offsets": [120]}
    cookie = client.cookies.get("okshun_session")
    assert cookie and "httponly" in r.headers["set-cookie"].lower()
    assert client.get("/api/me").json()["user"]["email"] == "buyer@example.com"
    client.post("/api/auth/logout", headers=H)
    assert client.get("/api/me").json()["user"] is None
    assert client.get("/api/me/data").status_code == 401


def test_login_error_doesnt_reveal_which_part_is_wrong(client):
    signup(client)
    client.post("/api/auth/logout", headers=H)
    wrong_pw = client.post("/api/auth/login", json={"email": "buyer@example.com", "password": "nope-nope"}, headers=H)
    no_user = client.post("/api/auth/login", json={"email": "who@example.com", "password": "nope-nope"}, headers=H)
    assert wrong_pw.status_code == no_user.status_code == 401
    assert wrong_pw.json() == no_user.json()


def test_watchlist_notes_and_import_without_overwrite(client):
    signup(client)
    a, b = lot_keys(client)
    client.put(f"/api/me/watchlist/{a}", headers=H)
    client.put(f"/api/me/notes/check/{a}", json={"data": {"km": 100}}, headers=H)
    moved = client.post("/api/me/import", headers=H, json={
        "watchlist": [a, b], "notes": {f"check|{a}": {"km": 999}, f"repairs|{b}": {"skip": ["baseline"]}, "bogus|x/y": {}}})
    assert moved.json() == {"watchlist": 1, "notes": 1}
    data = client.get("/api/me/data").json()
    assert set(data["watchlist"]) == {a, b}
    assert data["notes"][f"check|{a}"] == {"km": 100}          # account copy kept
    assert data["notes"][f"repairs|{b}"] == {"skip": ["baseline"]}
    assert client.put(f"/api/me/notes/nonsense/{a}", json={"data": {}}, headers=H).status_code == 400
    client.delete(f"/api/me/watchlist/{a}", headers=H)
    assert client.get("/api/me/data").json()["watchlist"] == [b]


def test_keys_filter_returns_only_those_lots(client):
    a, b = lot_keys(client)
    res = client.get("/api/listings", params={"keys": [a, b]}).json()
    assert res["total"] == 2 and {f"{i['source']}/{i['source_lot_id']}" for i in res["items"]} == {a, b}


def test_saved_searches(client):
    signup(client)
    s = client.post("/api/me/searches", json={"name": "Toyotas", "params": "make=Toyota&risk=Low+Risk"}, headers=H).json()
    assert s["matches"] > 0 and s["email"] is True
    client.patch(f"/api/me/searches/{s['id']}", json={"email": False}, headers=H)
    assert client.get("/api/me/searches").json()[0]["email"] is False
    assert client.delete(f"/api/me/searches/{s['id']}", headers=H).json() == {"ok": True}
    assert client.delete(f"/api/me/searches/{s['id']}", headers=H).status_code == 404


def test_users_cant_touch_each_others_searches(client):
    signup(client, "one@example.com")
    sid = client.post("/api/me/searches", json={"name": "Mine", "params": "make=Kia"}, headers=H).json()["id"]
    client.post("/api/auth/logout", headers=H)
    signup(client, "two@example.com")
    assert client.delete(f"/api/me/searches/{sid}", headers=H).status_code == 404
    assert client.get("/api/me/searches").json() == []


def test_delete_account_removes_everything(client, tmp_path):
    signup(client)
    a, _ = lot_keys(client)
    client.put(f"/api/me/watchlist/{a}", headers=H)
    client.post("/api/me/searches", json={"name": "x", "params": "make=Kia"}, headers=H)
    assert client.delete("/api/me", headers=H).json() == {"ok": True}
    assert client.get("/api/me").json()["user"] is None
    import sqlite3
    conn = sqlite3.connect(tmp_path / "acct.db")
    for table in ("users", "sessions", "watchlist", "lot_notes", "saved_searches", "alerts"):
        assert conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 0, table
