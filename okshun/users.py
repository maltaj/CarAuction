"""
Buyer accounts: sign-up, sign-in sessions, watchlist, per-lot notes and saved searches.

Stores as little as possible: an email address and a salted PBKDF2 password
hash. Session tokens are random and only their SHA-256 hash is stored, so a
copy of the database can't be used to sign in. Deleting an account removes
everything linked to it (POPIA).
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import re
import secrets
import sqlite3
from datetime import datetime, timedelta
from typing import Optional

USER_DDL = """
CREATE TABLE IF NOT EXISTS users (
    id               INTEGER PRIMARY KEY,
    email            TEXT NOT NULL UNIQUE COLLATE NOCASE,
    password_hash    TEXT NOT NULL,
    created_at       TEXT NOT NULL,
    email_reminders  INTEGER NOT NULL DEFAULT 1   -- email when a watched lot is about to close
);
CREATE TABLE IF NOT EXISTS sessions (
    token_hash  TEXT PRIMARY KEY,
    user_id     INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    created_at  TEXT NOT NULL,
    expires_at  TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS watchlist (
    user_id        INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    source         TEXT NOT NULL,
    source_lot_id  TEXT NOT NULL,
    added_at       TEXT NOT NULL,
    PRIMARY KEY (user_id, source, source_lot_id)
);
-- A buyer's own data about a lot: kind = 'check' (history check) or 'repairs' (repair edits)
CREATE TABLE IF NOT EXISTS lot_notes (
    user_id        INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    source         TEXT NOT NULL,
    source_lot_id  TEXT NOT NULL,
    kind           TEXT NOT NULL,
    data           TEXT NOT NULL,
    updated_at     TEXT NOT NULL,
    PRIMARY KEY (user_id, source, source_lot_id, kind)
);
CREATE TABLE IF NOT EXISTS saved_searches (
    id               INTEGER PRIMARY KEY,
    user_id          INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    name             TEXT NOT NULL,
    params           TEXT NOT NULL,              -- the app's query string, e.g. make=Toyota&risk=Low+Risk
    email            INTEGER NOT NULL DEFAULT 1,
    created_at       TEXT NOT NULL,
    last_checked_at  TEXT NOT NULL               -- lots first seen after this are new matches
);
CREATE TABLE IF NOT EXISTS alerts (
    id               INTEGER PRIMARY KEY,
    user_id          INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    kind             TEXT NOT NULL,              -- 'new_match' or 'closing'
    saved_search_id  INTEGER REFERENCES saved_searches(id) ON DELETE CASCADE,
    source           TEXT NOT NULL,
    source_lot_id    TEXT NOT NULL,
    created_at       TEXT NOT NULL,
    read_at          TEXT,
    emailed_at       TEXT,
    UNIQUE (user_id, kind, source, source_lot_id)
);
"""

NOTE_KINDS = ("check", "repairs")
SESSION_DAYS = 30
PBKDF2_ITERATIONS = 390_000
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class AuthError(ValueError):
    """A problem the buyer can fix; the message is safe to show."""


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, PBKDF2_ITERATIONS)
    b64 = lambda b: base64.b64encode(b).decode()
    return f"pbkdf2_sha256${PBKDF2_ITERATIONS}${b64(salt)}${b64(digest)}"


def verify_password(password: str, stored: str) -> bool:
    try:
        _, iterations, salt, digest = stored.split("$")
        check = hashlib.pbkdf2_hmac("sha256", password.encode(), base64.b64decode(salt), int(iterations))
        return hmac.compare_digest(check, base64.b64decode(digest))
    except (ValueError, TypeError):
        return False


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


# ---------- accounts and sessions ----------

def register(conn: sqlite3.Connection, email: str, password: str) -> int:
    email = (email or "").strip()
    if not EMAIL_RE.match(email):
        raise AuthError("Enter a valid email address.")
    if len(password or "") < 8:
        raise AuthError("Use a password of at least 8 characters.")
    try:
        cur = conn.execute("INSERT INTO users (email, password_hash, created_at) VALUES (?, ?, ?)",
                           (email, hash_password(password), _now()))
    except sqlite3.IntegrityError:
        raise AuthError("An account with this email already exists. Sign in instead.")
    conn.commit()
    return cur.lastrowid


def authenticate(conn: sqlite3.Connection, email: str, password: str) -> int:
    row = conn.execute("SELECT id, password_hash FROM users WHERE email = ?", ((email or "").strip(),)).fetchone()
    if not row or not verify_password(password or "", row["password_hash"]):
        raise AuthError("That email and password don't match.")
    return row["id"]


def create_session(conn: sqlite3.Connection, user_id: int) -> str:
    token = secrets.token_urlsafe(32)
    now = datetime.now()
    conn.execute("INSERT INTO sessions VALUES (?, ?, ?, ?)", (
        _token_hash(token), user_id, now.isoformat(timespec="seconds"),
        (now + timedelta(days=SESSION_DAYS)).isoformat(timespec="seconds")))
    conn.execute("DELETE FROM sessions WHERE expires_at < ?", (now.isoformat(timespec="seconds"),))
    conn.commit()
    return token


def user_for_token(conn: sqlite3.Connection, token: Optional[str]) -> Optional[dict]:
    if not token:
        return None
    row = conn.execute(
        "SELECT u.id, u.email, u.email_reminders FROM sessions s JOIN users u ON u.id = s.user_id "
        "WHERE s.token_hash = ? AND s.expires_at > ?", (_token_hash(token), _now())).fetchone()
    if not row:
        return None
    return {"id": row["id"], "email": row["email"], "email_reminders": bool(row["email_reminders"])}


def end_session(conn: sqlite3.Connection, token: Optional[str]) -> None:
    if token:
        conn.execute("DELETE FROM sessions WHERE token_hash = ?", (_token_hash(token),))
        conn.commit()


def delete_account(conn: sqlite3.Connection, user_id: int) -> None:
    conn.execute("DELETE FROM users WHERE id = ?", (user_id,))
    conn.commit()


def set_email_reminders(conn: sqlite3.Connection, user_id: int, on: bool) -> None:
    conn.execute("UPDATE users SET email_reminders = ? WHERE id = ?", (int(on), user_id))
    conn.commit()


# ---------- watchlist and notes ----------

def user_data(conn: sqlite3.Connection, user_id: int) -> dict:
    watch = [f"{r['source']}/{r['source_lot_id']}" for r in conn.execute(
        "SELECT source, source_lot_id FROM watchlist WHERE user_id = ? ORDER BY added_at", (user_id,))]
    notes = {f"{r['kind']}|{r['source']}/{r['source_lot_id']}": json.loads(r["data"]) for r in conn.execute(
        "SELECT kind, source, source_lot_id, data FROM lot_notes WHERE user_id = ?", (user_id,))}
    return {"watchlist": watch, "notes": notes}


def watch(conn: sqlite3.Connection, user_id: int, source: str, lot: str, on: bool = True) -> None:
    if on:
        conn.execute("INSERT OR IGNORE INTO watchlist VALUES (?, ?, ?, ?)", (user_id, source, lot, _now()))
    else:
        conn.execute("DELETE FROM watchlist WHERE user_id = ? AND source = ? AND source_lot_id = ?", (user_id, source, lot))
    conn.commit()


def set_note(conn: sqlite3.Connection, user_id: int, kind: str, source: str, lot: str, data: Optional[dict]) -> None:
    if kind not in NOTE_KINDS:
        raise AuthError(f"Unknown note kind: {kind}")
    if data is None:
        conn.execute("DELETE FROM lot_notes WHERE user_id = ? AND source = ? AND source_lot_id = ? AND kind = ?",
                     (user_id, source, lot, kind))
    else:
        conn.execute(
            "INSERT INTO lot_notes VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT (user_id, source, source_lot_id, kind) "
            "DO UPDATE SET data = excluded.data, updated_at = excluded.updated_at",
            (user_id, source, lot, kind, json.dumps(data)[:20_000], _now()))
    conn.commit()


def import_browser_data(conn: sqlite3.Connection, user_id: int, watchlist: list, notes: dict) -> dict:
    """Merge data saved in the browser before sign-in. Never overwrites what the account already has."""
    added = {"watchlist": 0, "notes": 0}
    for key in watchlist[:500]:
        source, _, lot = str(key).partition("/")
        if source and lot:
            cur = conn.execute("INSERT OR IGNORE INTO watchlist VALUES (?, ?, ?, ?)", (user_id, source, lot, _now()))
            added["watchlist"] += cur.rowcount
    for key, data in list(notes.items())[:2000]:
        kind, _, rest = str(key).partition("|")
        source, _, lot = rest.partition("/")
        if kind in NOTE_KINDS and source and lot and isinstance(data, dict):
            cur = conn.execute("INSERT OR IGNORE INTO lot_notes VALUES (?, ?, ?, ?, ?, ?)",
                               (user_id, source, lot, kind, json.dumps(data)[:20_000], _now()))
            added["notes"] += cur.rowcount
    conn.commit()
    return added


# ---------- saved searches ----------

def list_searches(conn: sqlite3.Connection, user_id: int) -> list[dict]:
    return [dict(r) | {"email": bool(r["email"])} for r in conn.execute(
        "SELECT id, name, params, email, created_at FROM saved_searches WHERE user_id = ? ORDER BY created_at DESC",
        (user_id,))]


def save_search(conn: sqlite3.Connection, user_id: int, name: str, params: str, email: bool = True) -> int:
    name = (name or "").strip()[:80] or "My search"
    params = (params or "").lstrip("?")[:2000]
    if conn.execute("SELECT COUNT(*) FROM saved_searches WHERE user_id = ?", (user_id,)).fetchone()[0] >= 20:
        raise AuthError("You can save up to 20 searches. Delete one first.")
    now = _now()
    cur = conn.execute(
        "INSERT INTO saved_searches (user_id, name, params, email, created_at, last_checked_at) VALUES (?, ?, ?, ?, ?, ?)",
        (user_id, name, params, int(email), now, now))
    conn.commit()
    return cur.lastrowid


def update_search(conn: sqlite3.Connection, user_id: int, search_id: int, *, name: Optional[str] = None,
                  email: Optional[bool] = None) -> bool:
    sets, vals = [], []
    if name is not None:
        sets.append("name = ?"); vals.append(name.strip()[:80] or "My search")
    if email is not None:
        sets.append("email = ?"); vals.append(int(email))
    if not sets:
        return True
    cur = conn.execute(f"UPDATE saved_searches SET {', '.join(sets)} WHERE id = ? AND user_id = ?",
                       vals + [search_id, user_id])
    conn.commit()
    return cur.rowcount == 1


def delete_search(conn: sqlite3.Connection, user_id: int, search_id: int) -> bool:
    cur = conn.execute("DELETE FROM saved_searches WHERE id = ? AND user_id = ?", (search_id, user_id))
    conn.commit()
    return cur.rowcount == 1
