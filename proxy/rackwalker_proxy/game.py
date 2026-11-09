"""Game routes (/api/game/*): player sign-in and the game's own state (contracts/api.md "Game state").

Identity: the player is a NetBox user. They sign in with one of their own NetBox API tokens; the proxy asks NetBox
who owns it (GET /api/authentication-check/, NetBox >= 4.3) and forgets the token. Why a token and not a password:
  * no password ever reaches the game, so none can be stored or logged by it;
  * NetBox has no API that checks a password without side effects (token provisioning creates a token in NetBox);
  * it works the same for local, LDAP and SSO NetBox users, and a read-only token is enough.
The proxy's own least-privilege token still does every NetBox read and write; the player's token is used once.

Session: a random cookie value (HttpOnly, SameSite=Strict, Path=/api); only its SHA-256 is stored. Writes need
the session's CSRF token in the X-RackWalker-CSRF header (a custom header cannot be sent cross-site without a CORS
preflight, which the proxy never grants); sign-in needs the header too, and an Origin header, when sent, must name
a host the proxy serves.

NetBox writes: when the proxy runs with WRITE_ENABLED=1, the change routes (/api/changes/preview and /apply) need a
signed-in player and the session's CSRF header as well (`change_player`), and every write names that player in its
NetBox changelog message. The write itself still uses the proxy's token.

RACKWALKER_LOCAL_PLAYER=1 turns on a single-user local mode (player id 0, no sign-in). It bypasses identity, so it
is off by default.
"""
from __future__ import annotations

import json
import logging
import sqlite3
import threading
import time
from pathlib import Path
from collections.abc import Callable
from typing import Annotated, Any
from urllib.parse import urlsplit

import anyio
import httpx
from fastapi import Body, FastAPI, Request
from fastapi.concurrency import run_in_threadpool
from fastapi import Path as PathParam
from fastapi.responses import JSONResponse

from . import schema
from .errors import ApiError
from .gamestore import GameStore, Session, StoreError, duplicate_key, upgrade_settings
from .netbox import auth_header

log = logging.getLogger("rackwalker_proxy.game")

COOKIE = "rackwalker_session"
COOKIE_PATH = "/api"
CSRF_HEADER = "X-RackWalker-CSRF"
LOCAL_PLAYER = {"id": 0, "username": "local", "display_name": "Local player"}
# A session is checked against NetBox (does its user still exist, and is it active?) before every write, and on reads
# when the last good check is older than the recheck interval (RACKWALKER_SESSION_RECHECK, default RECHECK_SECONDS).
# A user NetBox no longer has (gone) or has deactivated (inactive) ends every session of that player at once, with a 401
# whose details give the reason. When NetBox cannot answer, a read keeps the session and a write is refused
# (netbox_unreachable): nothing is written for a user NetBox has not just confirmed.
RECHECK_SECONDS = 60.0
_now = time.monotonic
Slot = Annotated[str, PathParam(pattern=r"^(auto|1|2|3)$")]
Json = Annotated[Any, Body()]


def _valid(defname: str, body: Any) -> Any:
    errs = schema.errors(defname, body)
    if errs:
        raise ApiError("validation_failed", f"The request body does not match the {defname} schema.", {"errors": errs})
    return body


def check_bindings(settings: dict[str, Any]) -> dict[str, Any]:
    """Refuses settings that bind one key to two actions (the game could not tell which one the player meant)."""
    dup = duplicate_key(settings["keys"])
    if dup:
        k, first, second = dup
        raise ApiError("validation_failed", f"Key {k} is bound to both {first} and {second}.",
                       {"errors": [{"path": f"/keys/{second}", "message": f"{k} is already bound to {first}"}]})
    return settings


async def netbox_whoami(url: str, token: str, transport: httpx.AsyncBaseTransport | None = None) -> dict[str, Any]:
    """The NetBox user who owns `token`, as a Player body. The token goes only into the Authorization header."""
    if not url:
        raise ApiError("netbox_unreachable", "The proxy has no NetBox configured (NETBOX_URL is empty).")
    if not (token.isascii() and token.isprintable()):
        # An HTTP header carries only printable ASCII; NetBox tokens never hold anything else.
        raise ApiError("unauthorized", "That is not a NetBox token: it has characters NetBox tokens never contain.", None)
    extra = {"transport": transport} if transport is not None else {}
    try:
        async with httpx.AsyncClient(timeout=10, **extra) as hc:
            r = await hc.get(f"{url}/api/authentication-check/", headers={"Authorization": auth_header(token), "Accept": "application/json"})
    except httpx.HTTPError as e:
        raise ApiError("netbox_unreachable", "NetBox did not answer the sign-in check.", {"type": type(e).__name__}) from None
    if r.status_code in (401, 403):
        raise ApiError("unauthorized", "NetBox does not accept that token.", None)
    if r.status_code != 200:
        raise ApiError("netbox_error", f"NetBox answered {r.status_code} to the sign-in check.", {"status": r.status_code})
    try:
        u = r.json()
        name = " ".join(p for p in (u.get("first_name") or "", u.get("last_name") or "") if p).strip()
        uid = u["id"]
        if isinstance(uid, bool) or not isinstance(uid, int) or uid < 1:
            raise TypeError("id")
        return {"id": uid, "username": str(u["username"])[:150], "display_name": name[:300] or None}
    except (ValueError, KeyError, TypeError, AttributeError):
        raise ApiError("netbox_error", "NetBox's sign-in check answered in an unexpected shape.", None) from None


async def netbox_user_status(url: str, proxy_token: str, uid: int, transport: httpx.AsyncBaseTransport | None = None) -> str | None:
    """'active', 'gone' (404) or 'inactive' for NetBox user `uid`, or None when NetBox cannot say (unreachable, an error,
    or the proxy's token may not view users). Asked with the proxy's own token: the player's token was never kept."""
    if not url or not proxy_token:
        return None
    extra = {"transport": transport} if transport is not None else {}
    try:
        async with httpx.AsyncClient(timeout=5, **extra) as hc:
            r = await hc.get(f"{url}/api/users/users/{uid}/", headers={"Authorization": auth_header(proxy_token), "Accept": "application/json"})
    except httpx.HTTPError:
        return None
    if r.status_code == 404:
        return "gone"
    if r.status_code != 200:
        if r.status_code == 403:
            log.warning("the proxy's NetBox token may not view users: game sessions cannot be rechecked")
        return None
    try:
        return "active" if r.json().get("is_active", True) is not False else "inactive"
    except (ValueError, AttributeError):
        return None


def fixture_user_status(fixtures_dir: Path, uid: int) -> str:
    """Fixture mode: a player is active while fixtures/players.json lists them without "active": false."""
    path = fixtures_dir / "players.json"
    for p in (json.loads(path.read_text())["players"] if path.exists() else []):
        if p["user"]["id"] == uid:
            return "active" if p.get("active", True) is not False else "inactive"
    return "gone"


def fixture_whoami(fixtures_dir: Path, token: str) -> dict[str, Any]:
    """Fixture mode has no NetBox: fixtures/players.json lists synthetic tokens and the users they stand for."""
    path = fixtures_dir / "players.json"
    players = json.loads(path.read_text())["players"] if path.exists() else []
    for p in players:
        if p["token"] == token:
            if p.get("active", True) is False:
                raise ApiError("unauthorized", "That NetBox user is not active.", None)
            return p["user"]
    raise ApiError("unauthorized", "That token is not one of the fixture players (fixtures/players.json).", None)


def install(app: FastAPI, *, open_store: Callable[[], GameStore], netbox_url: str, fixtures_dir: Path | None, local_player: bool,
            allowed_hosts: set[str], cookie_secure: bool, transport: httpx.AsyncBaseTransport | None = None, netbox_token: str = "",
            recheck_s: float = RECHECK_SECONDS) -> None:

    # Every route that touches the store is a plain `def`, which FastAPI runs in its thread pool: SQLite work (opening
    # the file, a migration, the fsync of each commit) can take seconds on a busy disk, and on the event loop it would
    # stall every other route, scenes and health included. GameStore serialises its own calls.
    opened: list[GameStore] = []
    opening = threading.Lock()

    def get_store() -> GameStore:
        # Opened on first use, so a proxy that never serves the game never creates the state file.
        with opening:
            if not opened:
                try:
                    st = open_store()
                except (StoreError, sqlite3.DatabaseError) as e:
                    log.error("game state file cannot be used: %s", e)
                    newer = isinstance(e, StoreError)
                    raise ApiError("internal", "The game state file was written by a newer RackWalker: update RackWalker to play."
                                   if newer else "The game state file cannot be read (damaged or not a SQLite file); the proxy log names it.",
                                   {"store": "newer_schema" if newer else "unreadable"}) from None
                if local_player:
                    st.upsert_player(LOCAL_PLAYER["id"], LOCAL_PLAYER["username"], LOCAL_PLAYER["display_name"])
                opened.append(st)
        return opened[0]

    app.state.game_store = get_store

    checked: dict[int, float] = {}  # user id -> time of the last check NetBox answered "active"
    checked_lock = threading.Lock()

    def user_status(uid: int) -> str | None:
        if fixtures_dir:
            return fixture_user_status(fixtures_dir, uid)
        # session_of runs in a worker thread (store routes are plain `def`), so the async lookup runs on the event loop
        return anyio.from_thread.run(netbox_user_status, netbox_url, netbox_token, uid, transport)

    def session_of(request: Request, write: bool = False) -> Session | None:
        """The request's session, rechecked against NetBox before every write and on reads when the last check is older
        than `recheck_s`. A user NetBox no longer has, or has deactivated, loses every session here: 401 with
        details.reason "gone" or "inactive"."""
        cookie = request.cookies.get(COOKIE)
        # No cookie, no session: answered without the store, so a signed-out page never waits for the file.
        s = get_store().session(cookie) if cookie else None
        if s is None:
            return None
        now = _now()
        with checked_lock:
            last = checked.get(s.user_id)
        if not write and last is not None and now - last < recheck_s:
            return s
        status = user_status(s.user_id)
        if status == "active":
            with checked_lock:
                checked[s.user_id] = now
            return s
        if status is None:
            if write:
                raise ApiError("netbox_unreachable", "NetBox cannot confirm your user right now, so nothing was written; "
                               "your game keeps it and tries again.", None)
            return s
        log.info("game sessions ended: NetBox user %s is %s", s.user_id, status)
        get_store().end_user_sessions(s.user_id)
        with checked_lock:
            checked.pop(s.user_id, None)
        what = "deleted" if status == "gone" else "deactivated"
        raise ApiError("unauthorized", f"Your NetBox user was {what} in NetBox, so the game session has ended. "
                       "Sign in again with an active NetBox user.", {"reason": status})

    def origin_ok(request: Request) -> bool:
        origin = request.headers.get("origin")
        if origin is None:
            return True
        host = (urlsplit(origin).hostname or "").lower()
        return host in allowed_hosts

    def require(request: Request, write: bool) -> int:
        """The player id of this request; for writes also checks Origin and the CSRF header."""
        if write and not origin_ok(request):
            raise ApiError("forbidden", "Cross-origin game writes are refused.", None)
        s = session_of(request, write)
        if s is None:
            if local_player:
                if write and request.headers.get(CSRF_HEADER) is None:
                    raise ApiError("forbidden", f"Game writes need the {CSRF_HEADER} header.", None)
                return LOCAL_PLAYER["id"]
            raise ApiError("unauthorized", "Sign in first (POST /api/game/session).", None)
        if write and request.headers.get(CSRF_HEADER) != s.csrf:
            raise ApiError("forbidden", f"Game writes need the session's {CSRF_HEADER} header.", None)
        return s.user_id

    def change_player(request: Request) -> str:
        """The player who asks for a NetBox write (the change routes with WRITE_ENABLED=1): a signed-in session with its
        CSRF header and a same-host Origin, or the local mode with the header. Their name goes into the changelog."""
        if not origin_ok(request):
            raise ApiError("forbidden", "Cross-origin NetBox writes are refused.", None)
        s = session_of(request, write=True)
        if s is None:
            if local_player:
                if request.headers.get(CSRF_HEADER) is None:
                    raise ApiError("forbidden", f"NetBox writes need the {CSRF_HEADER} header.", None)
                return LOCAL_PLAYER["username"]
            raise ApiError("unauthorized", "NetBox writes need a signed-in player: open the game (the page without ?view=) "
                           "and sign in with your NetBox token.", None)
        if request.headers.get(CSRF_HEADER) != s.csrf:
            raise ApiError("forbidden", f"NetBox writes need the session's {CSRF_HEADER} header.", None)
        p = get_store().player(s.user_id)
        return p["username"] if p else f"user {s.user_id}"

    app.state.change_player = change_player

    def session_body(s: Session | None) -> dict[str, Any]:
        if s is not None:
            return {"signed_in": True, "player": get_store().player(s.user_id), "csrf_token": s.csrf, "local_mode": local_player}
        if local_player:
            # No cookie needed: the local mode's CSRF token is fixed, its purpose is only to force the custom header.
            return {"signed_in": True, "player": dict(LOCAL_PLAYER), "csrf_token": "local", "local_mode": True}
        return {"signed_in": False, "player": None, "csrf_token": None, "local_mode": False}

    def no_store(resp: JSONResponse) -> JSONResponse:
        resp.headers["Cache-Control"] = "no-store"
        return resp

    @app.get("/api/game/session")
    def get_session(request: Request):
        try:
            s = session_of(request)
        except ApiError as e:
            if e.code != "unauthorized":
                raise
            s = None
        resp = no_store(JSONResponse(session_body(s)))
        if s is None and request.cookies.get(COOKIE):
            resp.delete_cookie(COOKIE, path=COOKIE_PATH, httponly=True, samesite="strict", secure=cookie_secure)
        return resp

    @app.post("/api/game/session")
    async def sign_in(request: Request, body: Json):
        if not origin_ok(request) or request.headers.get(CSRF_HEADER) is None:
            raise ApiError("forbidden", f"Sign-in needs the {CSRF_HEADER} header from the game's own page.", None)
        token = _valid("SignInRequest", body)["token"]
        user = fixture_whoami(fixtures_dir, token) if fixtures_dir else await netbox_whoami(netbox_url, token, transport)
        del token, body  # used once, never kept
        value, s, resp_body = await run_in_threadpool(start_session, request.cookies.get(COOKIE), user)
        resp = no_store(JSONResponse(resp_body))
        resp.set_cookie(COOKIE, value, max_age=int(s.expires_at - get_store().clock()), path=COOKIE_PATH, httponly=True,
                        samesite="strict", secure=cookie_secure)
        return resp

    def start_session(old_cookie: str | None, user: dict[str, Any]) -> tuple[str, Session, dict[str, Any]]:
        get_store().end_session(old_cookie)
        get_store().upsert_player(user["id"], user["username"], user["display_name"])
        value, s = get_store().new_session(user["id"])
        with checked_lock:
            checked[user["id"]] = _now()  # NetBox has just vouched for this user
        return value, s, session_body(s)

    @app.delete("/api/game/session")
    def sign_out(request: Request):
        # Signing out needs the same Origin and CSRF checks as a write, but not NetBox: it only ends this session.
        cookie = request.cookies.get(COOKIE)
        s = get_store().session(cookie) if cookie else None
        if s is not None:
            if not origin_ok(request):
                raise ApiError("forbidden", "Cross-origin game writes are refused.", None)
            if request.headers.get(CSRF_HEADER) != s.csrf:
                raise ApiError("forbidden", f"Game writes need the session's {CSRF_HEADER} header.", None)
        get_store().end_session(cookie)
        resp = no_store(JSONResponse(session_body(None)))
        resp.delete_cookie(COOKIE, path=COOKIE_PATH, httponly=True, samesite="strict", secure=cookie_secure)
        return resp

    def state_of(uid: int) -> dict[str, Any]:
        player = get_store().player(uid) or dict(LOCAL_PLAYER)
        return {"contract_version": schema.CONTRACT_VERSION, "player": player, "settings": get_store().settings(uid),
                "flags": get_store().flags(uid), "saves": get_store().saves(uid)}

    @app.get("/api/game/state")
    def get_state(request: Request):
        return no_store(JSONResponse(state_of(require(request, write=False))))

    @app.put("/api/game/saves/{slot}")
    def put_save(request: Request, slot: Slot, body: Json):
        uid = require(request, write=True)
        return no_store(JSONResponse(get_store().put_save(uid, slot, _valid("SaveData", body))))

    @app.put("/api/game/settings")
    def put_settings(request: Request, body: Json):
        uid = require(request, write=True)
        # Keys the player sent must not clash; actions they did not send (a client from before 2.2.0) get defaults.
        return no_store(JSONResponse(get_store().put_settings(uid, upgrade_settings(check_bindings(_valid("GameSettings", body))))))

    @app.patch("/api/game/flags")
    def patch_flags(request: Request, body: Json):
        uid = require(request, write=True)
        patch = _valid("FlagsPatch", body)["flags"]
        try:
            return no_store(JSONResponse(get_store().patch_flags(uid, patch)))
        except StoreError as e:
            raise ApiError("conflict", str(e), {"max_flags": 500}) from None
