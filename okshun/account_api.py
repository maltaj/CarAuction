"""Account endpoints: sign-up and sign-in, watchlist, per-lot notes, saved searches and alerts."""

from __future__ import annotations

import os
import sqlite3
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field

from okshun import alerts, db, push, users
from okshun.adapters import SOURCES
from okshun.deps import SESSION_COOKIE, current_user, get_conn, require_user, same_site_request

router = APIRouter(prefix="/api")
changes = [Depends(same_site_request)]


class Credentials(BaseModel):
    email: str = Field(max_length=254)
    password: str = Field(max_length=200)


class AccountUpdate(BaseModel):
    email_reminders: Optional[bool] = None
    push_reminders: Optional[bool] = None
    reminder_offsets: Optional[list[int]] = Field(default=None, max_length=4)


class WatchBody(BaseModel):
    reminders: Optional[list[int]] = Field(default=None, max_length=4)   # None = my usual times


class PushSubscription(BaseModel):
    endpoint: str = Field(max_length=1000)
    keys: dict


class PushRemove(BaseModel):
    endpoint: str = Field(max_length=1000)


class NoteBody(BaseModel):
    data: dict


class BrowserData(BaseModel):
    watchlist: list[str] = []
    notes: dict = {}


class SearchBody(BaseModel):
    name: str = Field(default="My search", max_length=80)
    params: str = Field(default="", max_length=2000)
    email: bool = True


class SearchUpdate(BaseModel):
    name: Optional[str] = Field(default=None, max_length=80)
    email: Optional[bool] = None


def _set_cookie(response: Response, token: str) -> None:
    response.set_cookie(SESSION_COOKIE, token, max_age=users.SESSION_DAYS * 86400, httponly=True,
                        samesite="lax", secure=os.environ.get("OKSHUN_SECURE_COOKIES", "0") == "1", path="/")


def _public(user: Optional[dict]) -> Optional[dict]:
    if not user:
        return None
    return {k: user[k] for k in ("email", "email_reminders", "push_reminders", "reminder_offsets")}


def _fail(e: users.AuthError, status: int = 400):
    raise HTTPException(status_code=status, detail=str(e))


# ---------- auth ----------

@router.post("/auth/register", dependencies=changes)
def register(body: Credentials, response: Response, conn: sqlite3.Connection = Depends(get_conn)) -> dict:
    try:
        uid = users.register(conn, body.email, body.password)
    except users.AuthError as e:
        _fail(e)
    token = users.create_session(conn, uid)
    _set_cookie(response, token)
    return {"user": _public(users.user_for_token(conn, token))}


@router.post("/auth/login", dependencies=changes)
def login(body: Credentials, response: Response, conn: sqlite3.Connection = Depends(get_conn)) -> dict:
    try:
        uid = users.authenticate(conn, body.email, body.password)
    except users.AuthError as e:
        _fail(e, 401)
    token = users.create_session(conn, uid)
    _set_cookie(response, token)
    return {"user": _public(users.user_for_token(conn, token))}


@router.post("/auth/logout", dependencies=changes)
def logout(request: Request, response: Response, conn: sqlite3.Connection = Depends(get_conn)) -> dict:
    users.end_session(conn, request.cookies.get(SESSION_COOKIE))
    response.delete_cookie(SESSION_COOKIE, path="/")
    return {"ok": True}


# ---------- me ----------

@router.get("/me")
def me(user: Optional[dict] = Depends(current_user)) -> dict:
    return {"user": _public(user)}


@router.patch("/me", dependencies=changes)
def update_me(body: AccountUpdate, user: dict = Depends(require_user), conn: sqlite3.Connection = Depends(get_conn)) -> dict:
    users.set_reminder_prefs(conn, user["id"], offsets=body.reminder_offsets, email=body.email_reminders,
                             push=body.push_reminders)
    return {"ok": True}


@router.delete("/me", dependencies=changes)
def delete_me(response: Response, user: dict = Depends(require_user), conn: sqlite3.Connection = Depends(get_conn)) -> dict:
    users.delete_account(conn, user["id"])
    response.delete_cookie(SESSION_COOKIE, path="/")
    return {"ok": True}


@router.get("/me/data")
def my_data(user: dict = Depends(require_user), conn: sqlite3.Connection = Depends(get_conn)) -> dict:
    return users.user_data(conn, user["id"])


@router.post("/me/import", dependencies=changes)
def import_data(body: BrowserData, user: dict = Depends(require_user), conn: sqlite3.Connection = Depends(get_conn)) -> dict:
    return users.import_browser_data(conn, user["id"], body.watchlist, body.notes)


@router.put("/me/watchlist/{source}/{lot}", dependencies=changes)
def watch(source: str, lot: str, body: Optional[WatchBody] = None, user: dict = Depends(require_user),
          conn: sqlite3.Connection = Depends(get_conn)) -> dict:
    users.watch(conn, user["id"], source, lot, True)
    if body is not None and "reminders" in body.model_fields_set:
        users.set_watch_reminders(conn, user["id"], source, lot, body.reminders)
    return {"ok": True}


@router.delete("/me/watchlist/{source}/{lot}", dependencies=changes)
def unwatch(source: str, lot: str, user: dict = Depends(require_user), conn: sqlite3.Connection = Depends(get_conn)) -> dict:
    users.watch(conn, user["id"], source, lot, False)
    return {"ok": True}


@router.put("/me/notes/{kind}/{source}/{lot}", dependencies=changes)
def put_note(kind: str, source: str, lot: str, body: NoteBody, user: dict = Depends(require_user),
             conn: sqlite3.Connection = Depends(get_conn)) -> dict:
    try:
        users.set_note(conn, user["id"], kind, source, lot, body.data)
    except users.AuthError as e:
        _fail(e)
    return {"ok": True}


@router.delete("/me/notes/{kind}/{source}/{lot}", dependencies=changes)
def delete_note(kind: str, source: str, lot: str, user: dict = Depends(require_user),
                conn: sqlite3.Connection = Depends(get_conn)) -> dict:
    try:
        users.set_note(conn, user["id"], kind, source, lot, None)
    except users.AuthError as e:
        _fail(e)
    return {"ok": True}


# ---------- saved searches ----------

def _with_counts(conn, searches: list[dict]) -> list[dict]:
    now = datetime.now().isoformat(timespec="seconds")
    for s in searches:
        where, params = db._where(db.filters_from_query(s["params"]), now)
        s["matches"] = conn.execute(f"SELECT COUNT(*) FROM listings WHERE {where}", params).fetchone()[0]
    return searches


@router.get("/me/searches")
def searches(user: dict = Depends(require_user), conn: sqlite3.Connection = Depends(get_conn)) -> list[dict]:
    return _with_counts(conn, users.list_searches(conn, user["id"]))


@router.post("/me/searches", dependencies=changes)
def create_search(body: SearchBody, user: dict = Depends(require_user), conn: sqlite3.Connection = Depends(get_conn)) -> dict:
    try:
        sid = users.save_search(conn, user["id"], body.name, body.params, body.email)
    except users.AuthError as e:
        _fail(e)
    return next(s for s in _with_counts(conn, users.list_searches(conn, user["id"])) if s["id"] == sid)


@router.patch("/me/searches/{search_id}", dependencies=changes)
def edit_search(search_id: int, body: SearchUpdate, user: dict = Depends(require_user),
                conn: sqlite3.Connection = Depends(get_conn)) -> dict:
    if not users.update_search(conn, user["id"], search_id, name=body.name, email=body.email):
        raise HTTPException(status_code=404, detail="Saved search not found")
    return {"ok": True}


@router.delete("/me/searches/{search_id}", dependencies=changes)
def remove_search(search_id: int, user: dict = Depends(require_user), conn: sqlite3.Connection = Depends(get_conn)) -> dict:
    if not users.delete_search(conn, user["id"], search_id):
        raise HTTPException(status_code=404, detail="Saved search not found")
    return {"ok": True}


# ---------- alerts ----------

@router.get("/me/alerts")
def my_alerts(user: dict = Depends(require_user), conn: sqlite3.Connection = Depends(get_conn)) -> dict:
    alerts.find_new(conn, datetime.now())  # in-app alerts stay fresh between scheduled runs; emails go out from the CLI
    items = alerts.list_alerts(conn, user["id"], SOURCES)
    return {"unread": sum(1 for a in items if not a["read_at"]), "items": items}


@router.post("/me/alerts/read", dependencies=changes)
def read_alerts(user: dict = Depends(require_user), conn: sqlite3.Connection = Depends(get_conn)) -> dict:
    alerts.mark_read(conn, user["id"])
    return {"ok": True}


# ---------- phone notifications ----------

@router.get("/push/key")
def push_key(conn: sqlite3.Connection = Depends(get_conn)) -> dict:
    """The public VAPID key the browser needs to subscribe."""
    return {"key": push.vapid_keys(conn)[1]}


@router.post("/me/push", dependencies=changes)
def add_push(body: PushSubscription, user: dict = Depends(require_user), conn: sqlite3.Connection = Depends(get_conn)) -> dict:
    try:
        users.add_push_subscription(conn, user["id"], body.endpoint, str(body.keys.get("p256dh", "")), str(body.keys.get("auth", "")))
    except users.AuthError as e:
        _fail(e)
    users.set_reminder_prefs(conn, user["id"], push=True)
    return {"ok": True}


@router.delete("/me/push", dependencies=changes)
def remove_push(body: PushRemove, user: dict = Depends(require_user), conn: sqlite3.Connection = Depends(get_conn)) -> dict:
    users.remove_push_subscription(conn, user["id"], body.endpoint)
    return {"ok": True}


@router.post("/me/push/test", dependencies=changes)
def test_push(user: dict = Depends(require_user), conn: sqlite3.Connection = Depends(get_conn)) -> dict:
    sent = push.push_to_user(conn, user["id"], {
        "title": "Okshun notifications are on", "body": "You'll get reminders here before your watched lots close.",
        "url": "/", "tag": "test"})
    if not sent:
        raise HTTPException(status_code=502, detail="No device accepted the notification. Turn notifications off and on again.")
    return {"sent": sent}
