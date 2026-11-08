"""Game routes (/api/game/*): player sign-in and the game's own state (contracts/api.md "Game state").

Identity: the player is a NetBox user. They sign in with one of their own NetBox API tokens; the proxy asks NetBox
who owns it (GET /api/authentication-check/, NetBox >= 4.3) and forgets the token. Why a token and not a password:
  * no password ever reaches the game, so none can be stored or logged by it;
  * NetBox has no API that checks a password without side effects (token provisioning creates a token in NetBox);
  * it works the same for local, LDAP and SSO NetBox users, and a read-only token is enough.
The proxy's own least-privilege token still does every NetBox read and write; the player's token is used once.

Session: a random cookie value (HttpOnly, SameSite=Strict, Path=/api/game); only its SHA-256 is stored. Writes need
the session's CSRF token in the X-RackWalker-CSRF header (a custom header cannot be sent cross-site without a CORS
preflight, which the proxy never grants); sign-in needs the header too, and an Origin header, when sent, must name
a host the proxy serves.

RACKWALKER_LOCAL_PLAYER=1 turns on a single-user local mode (player id 0, no sign-in). It bypasses identity, so it
is off by default.
"""
from __future__ import annotations

import json
import threading
from pathlib import Path
from collections.abc import Callable
from typing import Annotated, Any
from urllib.parse import urlsplit

import httpx
from fastapi import Body, FastAPI, Request
from fastapi.concurrency import run_in_threadpool
from fastapi import Path as PathParam
from fastapi.responses import JSONResponse

from . import schema
from .errors import ApiError
from .gamestore import GameStore, Session, StoreError
from .netbox import auth_header

COOKIE = "rackwalker_session"
CSRF_HEADER = "X-RackWalker-CSRF"
LOCAL_PLAYER = {"id": 0, "username": "local", "display_name": "Local player"}
Slot = Annotated[str, PathParam(pattern=r"^(auto|1|2|3)$")]
Json = Annotated[Any, Body()]


def _valid(defname: str, body: Any) -> Any:
    errs = schema.errors(defname, body)
    if errs:
        raise ApiError("validation_failed", f"The request body does not match the {defname} schema.", {"errors": errs})
    return body


def check_bindings(settings: dict[str, Any]) -> dict[str, Any]:
    """Refuses settings that bind one key to two actions (the game could not tell which one the player meant)."""
    seen: dict[str, str] = {}
    for action, keys in settings["keys"].items():
        for k in keys:
            if k in seen and seen[k] != action:
                raise ApiError("validation_failed", f"Key {k} is bound to both {seen[k]} and {action}.",
                               {"errors": [{"path": f"/keys/{action}", "message": f"{k} is already bound to {seen[k]}"}]})
            seen[k] = action
    return settings


async def netbox_whoami(url: str, token: str, transport: httpx.AsyncBaseTransport | None = None) -> dict[str, Any]:
    """The NetBox user who owns `token`, as a Player body. The token goes only into the Authorization header."""
    if not url:
        raise ApiError("netbox_unreachable", "The proxy has no NetBox configured (NETBOX_URL is empty).")
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
        return {"id": int(u["id"]), "username": str(u["username"])[:150], "display_name": name[:300] or None}
    except (ValueError, KeyError, TypeError):
        raise ApiError("netbox_error", "NetBox's sign-in check answered in an unexpected shape.", None) from None


def fixture_whoami(fixtures_dir: Path, token: str) -> dict[str, Any]:
    """Fixture mode has no NetBox: fixtures/players.json lists synthetic tokens and the users they stand for."""
    path = fixtures_dir / "players.json"
    players = json.loads(path.read_text())["players"] if path.exists() else []
    for p in players:
        if p["token"] == token:
            return p["user"]
    raise ApiError("unauthorized", "That token is not one of the fixture players (fixtures/players.json).", None)


def install(app: FastAPI, *, open_store: Callable[[], GameStore], netbox_url: str, fixtures_dir: Path | None, local_player: bool,
            allowed_hosts: set[str], cookie_secure: bool, transport: httpx.AsyncBaseTransport | None = None) -> None:

    # Every route that touches the store is a plain `def`, which FastAPI runs in its thread pool: SQLite work (opening
    # the file, a migration, the fsync of each commit) can take seconds on a busy disk, and on the event loop it would
    # stall every other route, scenes and health included. GameStore serialises its own calls.
    opened: list[GameStore] = []
    opening = threading.Lock()

    def get_store() -> GameStore:
        # Opened on first use, so a proxy that never serves the game never creates the state file.
        with opening:
            if not opened:
                st = open_store()
                if local_player:
                    st.upsert_player(LOCAL_PLAYER["id"], LOCAL_PLAYER["username"], LOCAL_PLAYER["display_name"])
                opened.append(st)
        return opened[0]

    app.state.game_store = get_store

    def session_of(request: Request) -> Session | None:
        cookie = request.cookies.get(COOKIE)
        # No cookie, no session: answered without the store, so a signed-out page never waits for the file.
        return get_store().session(cookie) if cookie else None

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
        s = session_of(request)
        if s is None:
            if local_player:
                if write and request.headers.get(CSRF_HEADER) is None:
                    raise ApiError("forbidden", f"Game writes need the {CSRF_HEADER} header.", None)
                return LOCAL_PLAYER["id"]
            raise ApiError("unauthorized", "Sign in first (POST /api/game/session).", None)
        if write and request.headers.get(CSRF_HEADER) != s.csrf:
            raise ApiError("forbidden", f"Game writes need the session's {CSRF_HEADER} header.", None)
        return s.user_id

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
        return no_store(JSONResponse(session_body(session_of(request))))

    @app.post("/api/game/session")
    async def sign_in(request: Request, body: Json):
        if not origin_ok(request) or request.headers.get(CSRF_HEADER) is None:
            raise ApiError("forbidden", f"Sign-in needs the {CSRF_HEADER} header from the game's own page.", None)
        token = _valid("SignInRequest", body)["token"]
        user = fixture_whoami(fixtures_dir, token) if fixtures_dir else await netbox_whoami(netbox_url, token, transport)
        del token, body  # used once, never kept
        value, s, resp_body = await run_in_threadpool(start_session, request.cookies.get(COOKIE), user)
        resp = no_store(JSONResponse(resp_body))
        resp.set_cookie(COOKIE, value, max_age=int(s.expires_at - get_store().clock()), path="/api/game", httponly=True,
                        samesite="strict", secure=cookie_secure)
        return resp

    def start_session(old_cookie: str | None, user: dict[str, Any]) -> tuple[str, Session, dict[str, Any]]:
        get_store().end_session(old_cookie)
        get_store().upsert_player(user["id"], user["username"], user["display_name"])
        value, s = get_store().new_session(user["id"])
        return value, s, session_body(s)

    @app.delete("/api/game/session")
    def sign_out(request: Request):
        if session_of(request) is not None:
            require(request, write=True)
        get_store().end_session(request.cookies.get(COOKIE))
        resp = no_store(JSONResponse(session_body(None)))
        resp.delete_cookie(COOKIE, path="/api/game", httponly=True, samesite="strict", secure=cookie_secure)
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
        return no_store(JSONResponse(get_store().put_settings(uid, check_bindings(_valid("GameSettings", body)))))

    @app.patch("/api/game/flags")
    def patch_flags(request: Request, body: Json):
        uid = require(request, write=True)
        patch = _valid("FlagsPatch", body)["flags"]
        try:
            return no_store(JSONResponse(get_store().patch_flags(uid, patch)))
        except StoreError as e:
            raise ApiError("conflict", str(e), {"max_flags": 500}) from None
