"""Shared FastAPI dependencies: database connection, signed-in user, and a CSRF guard."""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path
from typing import Optional

from fastapi import Depends, Header, HTTPException, Request

from okshun import db, users

SESSION_COOKIE = "okshun_session"


def db_path() -> Path:
    return Path(os.environ.get("OKSHUN_DB", db.DEFAULT_DB))


def get_conn():
    conn = db.connect(db_path())
    try:
        yield conn
    finally:
        conn.close()


def current_user(request: Request, conn: sqlite3.Connection = Depends(get_conn)) -> Optional[dict]:
    return users.user_for_token(conn, request.cookies.get(SESSION_COOKIE))


def require_user(user: Optional[dict] = Depends(current_user)) -> dict:
    if not user:
        raise HTTPException(status_code=401, detail="Sign in to do this.")
    return user


def same_site_request(x_okshun: Optional[str] = Header(default=None)) -> None:
    """Changes need the X-Okshun header, which other websites can't add to a cross-site request."""
    if x_okshun != "1":
        raise HTTPException(status_code=403, detail="Missing X-Okshun header.")
