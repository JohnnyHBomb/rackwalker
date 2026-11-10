"""RackWalker proxy: FastAPI app that sits between the browser and NetBox and holds the NetBox token.

Routes and bodies follow the frozen contract (contracts/api.md, contracts/openapi.yaml, contracts/scene.schema.json).
Two backends:
  * fixture mode (RACKWALKER_FIXTURES=1 or a directory): serves fixtures/*.json, no NetBox needed;
  * netbox mode (NETBOX_URL + NETBOX_TOKEN): every route reads NetBox through the typed client (netbox.py).
Apply is refused unless WRITE_ENABLED=1 (dry-run is the default), in every mode. With WRITE_ENABLED=1 the change routes
also need a signed-in player and the session's CSRF header (game.py), and each write names that player in NetBox's
changelog message.
Reads are cached for RACKWALKER_CACHE_TTL seconds (default 10; 0 = off), keyed by route path; any apply clears it.
"""
from __future__ import annotations

import json
import logging
import os
import time
import uuid
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, Any, Awaitable, Callable
from urllib.parse import urlsplit, urlunsplit

import httpx
from fastapi import Body, FastAPI, Request
from fastapi import Path as PathParam
from fastapi.concurrency import run_in_threadpool
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from starlette.exceptions import HTTPException as StarletteHTTPException

from . import changes, game, perms, practice, practice_scenes, questroutes, scene, schema
from . import contracts_gen as c
from .errors import STATUS_FOR, ApiError  # noqa: F401  (STATUS_FOR re-exported)
from .gamestore import GameStore
from .netbox import SUPPORTED_PREFIX, NetBox, auth_header

APP_VERSION = "0.1.0"
CONTRACT_VERSION = schema.CONTRACT_VERSION
SUPPORTED_NETBOX = (SUPPORTED_PREFIX,)
SLOW_SECONDS = 2.0  # /api/status/ slower than this -> health "degraded" (HealthResponse.status)
REPO = Path(__file__).resolve().parents[2]
PORT_KINDS = schema.enum("PortKind")
OP_DEF = {"move_rack": "MoveRackOp", "place_device": "PlaceDeviceOp", "unplace_device": "UnplaceDeviceOp",
          "create_rack": "CreateRackOp", "update_fields": "UpdateFieldsOp", "connect_cable": "ConnectCableOp",
          "create_circuit": "CreateCircuitOp", "create_circuit_termination": "CreateCircuitTerminationOp", "assign_ip": "AssignIpOp",
          "create_prefix": "CreatePrefixOp"}
log = logging.getLogger("rackwalker_proxy")

netbox_auth_header = auth_header  # older name, kept for callers


@dataclass(frozen=True)
class Settings:
    netbox_url: str
    netbox_token: str
    write_enabled: bool
    fixtures_dir: Path | None
    cache_ttl: float = 10.0
    # Host header names the proxy answers to (port ignored). Anything else is refused, so a DNS-rebinding page
    # (attacker name -> 127.0.0.1) never becomes same-origin with the proxy and its NetBox token.
    allowed_hosts: tuple[str, ...] = ("127.0.0.1", "localhost")
    # Swagger UI / ReDoc pages load their scripts from a CDN (floating major, no SRI) on the origin that also serves
    # POST /api/changes/apply, so they are off unless RACKWALKER_DOCS=1 (local development only).
    docs: bool = False
    # The game's own state (SQLite, never NetBox): <state_dir>/game.sqlite3. RACKWALKER_STATE_DIR, default <repo>/data.
    state_dir: Path = REPO / "data"
    # Single-user local mode: no NetBox sign-in, one player (id 0). It bypasses identity, so it is off by default.
    local_player: bool = False
    # Secure flag on the session cookie: set RACKWALKER_COOKIE_SECURE=1 when the game is served over https.
    cookie_secure: bool = False
    # Seconds between checks that a signed-in player's NetBox user still exists and is active (writes always check).
    session_recheck: float = 60.0
    # Seconds the quest scanner keeps one read of the estate (any write through the proxy drops it at once).
    estate_ttl: float = 60.0

    @classmethod
    def from_env(cls, env: dict[str, str] | None = None) -> "Settings":
        env = dict(os.environ if env is None else env)
        fx = env.get("RACKWALKER_FIXTURES", "")
        fixtures = None if fx in ("", "0") else (REPO / "fixtures" if fx == "1" else Path(fx))
        return cls(netbox_url=env.get("NETBOX_URL", "").rstrip("/"), netbox_token=env.get("NETBOX_TOKEN", ""),
                   write_enabled=env.get("WRITE_ENABLED", "0") == "1", fixtures_dir=fixtures,
                   cache_ttl=float(env.get("RACKWALKER_CACHE_TTL", "10")),
                   allowed_hosts=tuple(h.strip().lower() for h in env.get("RACKWALKER_ALLOWED_HOSTS", "127.0.0.1,localhost").split(",")
                                       if h.strip()),
                   docs=env.get("RACKWALKER_DOCS", "0") == "1",
                   state_dir=Path(env["RACKWALKER_STATE_DIR"]) if env.get("RACKWALKER_STATE_DIR") else REPO / "data",
                   local_player=env.get("RACKWALKER_LOCAL_PLAYER", "0") == "1",
                   cookie_secure=env.get("RACKWALKER_COOKIE_SECURE", "0") == "1",
                   session_recheck=float(env.get("RACKWALKER_SESSION_RECHECK", "60")),
                   estate_ttl=float(env.get("RACKWALKER_ESTATE_TTL", "60")))


class ReadCache:
    """Short-TTL cache of built response bodies, keyed by route path. A hit is served with meta.source 'cache'
    (meta.generated_at stays the time the body was built). Cleared by every apply.

    `generation` counts clears: a reader takes it before it starts reading NetBox and hands it to `put`, which drops
    the body if an apply cleared the cache meanwhile (that body may hold pre-write state)."""

    def __init__(self, ttl: float, clock: Callable[[], float] = time.monotonic):
        self.ttl, self.clock = ttl, clock
        self._d: dict[str, tuple[float, str]] = {}
        self.generation = 0

    def get(self, key: str) -> dict[str, Any] | None:
        hit = self._d.get(key)
        if hit is None or self.clock() - hit[0] >= self.ttl:
            self._d.pop(key, None)
            return None
        body = json.loads(hit[1])
        body["meta"]["source"] = "cache"
        return body

    def put(self, key: str, body: dict[str, Any], generation: int) -> None:
        if self.ttl > 0 and generation == self.generation:
            self._d[key] = (self.clock(), json.dumps(body))

    def clear(self) -> None:
        self._d.clear()
        self.generation += 1


def _error(code: str, message: str, details: dict[str, Any] | None, status: int) -> JSONResponse:
    body = c.ErrorResponse(error=c.ErrorBody(code=code, message=message, details=details), request_id=uuid.uuid4().hex[:12])
    return JSONResponse(body.model_dump(mode="json"), status_code=status)


def _dump(m: BaseModel) -> dict[str, Any]:
    return m.model_dump(mode="json", exclude_unset=True)


Id = Annotated[int, PathParam(ge=1)]
ChangeBody = Annotated[Any, Body(description="A ChangeSet (contracts/scene.schema.json#/$defs/ChangeSet).")]


def changeset(body: Any) -> c.ChangeSet:
    """Strict JSON Schema validation (the generated models are lax), then the typed model. A failing op is also
    checked against its own $def, so the errors name the field rather than only 'not valid under any oneOf'."""
    errs = schema.errors("ChangeSet", body)
    if errs:
        ops = body.get("ops") if isinstance(body, dict) else None
        for i, op in enumerate(ops if isinstance(ops, list) else []):
            if isinstance(op, dict) and op.get("op") in OP_DEF:
                errs += [{**e, "path": f"/ops/{i}{e['path'].rstrip('/')}"} for e in schema.errors(OP_DEF[op["op"]], op)]
        raise ApiError("validation_failed", "The request body does not match the ChangeSet schema.", {"errors": errs})
    return c.ChangeSet.model_validate(body)


def public_url(url: str) -> str:
    """NETBOX_URL as the browser may see it: without user:password@ (previously: /api/health handed basic-auth
    credentials of a reverse proxy to the page, and the page built "open in NetBox" links from them)."""
    parts = urlsplit(url)
    if parts.username is None and parts.password is None:
        return url
    host = parts.hostname or ""
    if ":" in host:
        host = f"[{host}]"
    netloc = host + (f":{parts.port}" if parts.port is not None else "")
    return urlunsplit((parts.scheme, netloc, parts.path, parts.query, parts.fragment))


def create_app(settings: Settings | None = None, transport: httpx.AsyncBaseTransport | None = None) -> FastAPI:
    s = settings or Settings.from_env()
    client = NetBox(s.netbox_url, s.netbox_token, transport=transport) if s.netbox_url and not s.fixtures_dir else None
    cache = ReadCache(s.cache_ttl)

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        yield
        if client:
            await client.aclose()

    app = FastAPI(title="RackWalker proxy", version=APP_VERSION, docs_url="/api/docs" if s.docs else None,
                  redoc_url="/api/redoc" if s.docs else None, openapi_url="/api/openapi.json",
                  lifespan=lifespan)
    app.state.settings, app.state.cache, app.state.netbox = s, cache, client
    fixtures: dict[tuple[str, str], tuple[int, Any]] = {}
    if s.fixtures_dir:
        index = json.loads((s.fixtures_dir / "index.json").read_text())["fixtures"]
        for f in index:
            fixtures[(f["method"], f["path"])] = (f["status"], json.loads((s.fixtures_dir / f["file"]).read_text()))

    allowed_hosts = {h.lower() for h in s.allowed_hosts}
    perm_health: dict[str, Any] = {"at": 0.0, "value": None}

    @app.middleware("http")
    async def _trusted_host(request: Request, call_next):
        host = request.headers.get("host", "")
        name = host.split("]")[0] + "]" if host.startswith("[") else host.rsplit(":", 1)[0]
        if name.lower() not in allowed_hosts:
            return _error("bad_request", "The Host header is not one this proxy serves (RACKWALKER_ALLOWED_HOSTS).",
                          {"allowed_hosts": sorted(allowed_hosts)}, 400)
        return await call_next(request)

    @app.middleware("http")
    async def _no_framing(request: Request, call_next):
        # No other site may frame anything the proxy serves (the docs pages included). Added last, so it
        # wraps every other middleware and also marks the Host refusal.
        resp = await call_next(request)
        resp.headers["X-Frame-Options"] = "DENY"
        resp.headers["Content-Security-Policy"] = "frame-ancestors 'none'"
        return resp

    @app.exception_handler(ApiError)
    async def _api_error(_: Request, e: ApiError):
        return _error(e.code, e.message, e.details, e.status)

    @app.exception_handler(RequestValidationError)
    async def _validation(_: Request, e: RequestValidationError):
        errors = json.loads(json.dumps(e.errors(), default=str))
        if all(err.get("loc", [None])[0] in ("path", "query") for err in errors):  # e.g. /api/scene/site/abc
            return _error("bad_request", "The request path or query is malformed.", {"errors": errors}, 400)
        return _error("validation_failed", "The request body does not match the contract.", {"errors": errors}, 422)

    @app.exception_handler(StarletteHTTPException)
    async def _http(_: Request, e: StarletteHTTPException):
        code = {404: "not_found", 405: "bad_request"}.get(e.status_code, "internal")
        return _error(code, str(e.detail), None, e.status_code)

    @app.exception_handler(Exception)
    async def _unhandled(_: Request, e: Exception):
        log.exception("unhandled error")
        return _error("internal", "The proxy hit an unexpected error.", {"type": type(e).__name__}, 500)

    def fixture(method: str, path: str) -> JSONResponse:
        if (method, path) in fixtures:
            status, body = fixtures[(method, path)]
            return JSONResponse(body, status_code=status)
        raise ApiError("not_found", f"No fixture for {method} {path}.", {"path": path})

    def need_client() -> NetBox:
        if client is None:
            raise ApiError("netbox_unreachable", "The proxy has no NetBox configured (NETBOX_URL is empty).", None)
        return client

    async def read(path: str, build: Callable[[NetBox], Awaitable[BaseModel]]) -> JSONResponse:
        if s.fixtures_dir:
            return fixture("GET", path)
        body = cache.get(path)
        if body is None:
            gen = cache.generation
            body = _dump(await build(need_client()))
            cache.put(path, body, gen)
        return JSONResponse(body)

    @app.get("/api/health", response_model=c.HealthResponse)
    async def health():
        if s.fixtures_dir:
            return fixture("GET", "/api/health")
        version, reachable, slow = None, False, False
        if s.netbox_url:
            try:
                t0 = time.monotonic()
                extra = {"transport": transport} if transport is not None else {}
                async with httpx.AsyncClient(timeout=5, **extra) as hc:
                    r = await hc.get(f"{s.netbox_url}/api/status/", headers={"Authorization": auth_header(s.netbox_token)})
                reachable = r.status_code == 200
                slow = time.monotonic() - t0 > SLOW_SECONDS
                version = r.json().get("netbox-version") if reachable else None
            except (httpx.HTTPError, ValueError):
                reachable = False
        supported = bool(version) and version.startswith(SUPPORTED_NETBOX)
        # Writes need the proxy to read the player's user and object permissions: say so here when it cannot.
        permission_check = "unknown"
        if reachable and client is not None:
            # asked at most once a minute: health is polled, and the answer changes only when an admin edits permissions
            if perm_health["value"] is None or time.monotonic() - perm_health["at"] > 60:
                perm_health.update(at=time.monotonic(), value=await perms.check_health(client))
            permission_check = perm_health["value"]
        status = "ok" if reachable and supported and not slow else ("degraded" if reachable else "down")
        return c.HealthResponse(status=status, app_version=APP_VERSION, contract_version=CONTRACT_VERSION,
                                write_enabled=s.write_enabled,
                                netbox=c.HealthNetbox(url=public_url(s.netbox_url), reachable=reachable, version=version, supported=supported,
                                                      permission_check=permission_check))

    @app.get("/api/scene/world", response_model=c.WorldResponse)
    async def world():
        return await read("/api/scene/world", scene.world)

    async def island(request: Request) -> Any:
        """The practice island as this browser's player has left it (the plain island without a session). Made-up
        data: never read from NetBox, never cached with NetBox bodies."""
        def ops() -> list[dict[str, Any]]:
            try:
                return app.state.game_store().practice_ops(app.state.game_require(request, False))
            except ApiError:
                return []
        return practice.island(await run_in_threadpool(ops))

    def made_up(body: dict[str, Any]) -> JSONResponse:
        return JSONResponse(body, headers={"Cache-Control": "no-store"})

    @app.get("/api/scene/site/{site_id}", response_model=c.SiteScene)
    async def site(request: Request, site_id: Id):
        if site_id == practice.PRACTICE_SITE_ID:
            return made_up(practice_scenes.site_scene(await island(request)))
        return await read(f"/api/scene/site/{site_id}", lambda nb: scene.site_scene(nb, site_id))

    @app.get("/api/scene/rack/{rack_id}", response_model=c.RackScene)
    async def rack(request: Request, rack_id: Id):
        if practice_scenes.is_practice(rack_id):
            return made_up(practice_scenes.rack_scene(await island(request), rack_id))
        return await read(f"/api/scene/rack/{rack_id}", lambda nb: scene.rack_scene(nb, rack_id))

    @app.get("/api/device/{device_id}", response_model=c.DeviceDetail)
    async def device(request: Request, device_id: Id):
        if practice_scenes.is_practice(device_id):
            return made_up(practice_scenes.device_detail(await island(request), device_id))
        return await read(f"/api/device/{device_id}", lambda nb: scene.device_detail(nb, device_id))

    @app.get("/api/trace/{kind}/{port_id}", response_model=c.TraceResponse)
    async def trace(request: Request, kind: str, port_id: Id):
        if kind not in PORT_KINDS:
            raise ApiError("bad_request", f"Unknown port kind {kind!r}.", {"allowed": PORT_KINDS})
        if practice_scenes.is_practice(port_id):
            return made_up(practice_scenes.trace(await island(request), kind, port_id))
        return await read(f"/api/trace/{kind}/{port_id}", lambda nb: scene.trace(nb, kind, port_id))

    def refuse_island(body: Any) -> None:
        """The practice island is not in NetBox: the editor's change routes refuse its ids rather than ask NetBox about them."""
        for op in body["ops"]:
            ids = [op["target"].get("id"), *op.get("fields", {}).values()]
            if any(isinstance(v, int) and not isinstance(v, bool) and practice_scenes.is_practice(v) for v in ids):
                raise ApiError("bad_request", "That is the practice island: it is made up and is changed only through its quests, never "
                               "through NetBox.", {"reason": "practice_island"})

    async def writer(request: Request) -> str:
        """The signed-in player behind a change request (the store may touch the disk, so off the event loop)."""
        return await run_in_threadpool(app.state.change_player, request)

    @app.post("/api/changes/preview", response_model=c.ChangeResult)
    async def preview(request: Request, body: ChangeBody):
        if s.write_enabled:
            await writer(request)  # a write-enabled proxy shows NetBox's state for writes only to a signed-in player
        cs = changeset(body)
        refuse_island(body)
        # A plan for a circuit's record answers with what NetBox holds (an account that exists, a contact's name, a
        # charge already on record). In every mode it is planned only for a signed-in player whose own NetBox user may
        # view those objects: the proxy's token never shows a figure NetBox itself would refuse.
        figures = perms.view_needs_of(body["ops"])
        if figures and not s.fixtures_dir:
            await writer(request)
            uid, _ = await run_in_threadpool(app.state.change_identity, request)
            allowed = await app.state.may_write(uid)
            lacking = allowed.missing(figures) if allowed is not None else []
            if lacking:
                raise perms.view_refusal(lacking)
        if s.fixtures_dir:
            return fixture("POST", "/api/changes/preview")
        planned = await changes.preview(need_client(), cs)
        return JSONResponse(_dump(changes.result("preview", planned, s.write_enabled)))

    @app.post("/api/changes/apply", response_model=c.ChangeResult)
    async def apply(request: Request, body: ChangeBody):
        if not s.write_enabled:
            raise ApiError("write_disabled", "Apply is refused: the proxy runs in dry-run mode (WRITE_ENABLED is not 1).")
        uid, player = await run_in_threadpool(app.state.change_identity, request)
        if s.fixtures_dir:
            raise ApiError("bad_request", "Fixture mode never writes.")
        cs = changeset(body)
        refuse_island(body)
        # The proxy's token never widens what a player may change: a session started with a read-only NetBox token
        # writes nothing, and the player's own NetBox permissions (read now, not from a cache) must cover the change.
        if not await run_in_threadpool(app.state.session_may_write, request):
            raise ApiError("forbidden", "Nothing was written: you signed in with a read-only NetBox token. Sign in again with a "
                           "write-enabled token of your own to change NetBox through the game.", {"reason": "read_only_token"})
        allowed = await app.state.may_write(uid, True)
        if allowed is not None:
            missing = allowed.missing(perms.needs_of(body["ops"]))
            if missing:
                raise perms.refusal(missing)
        try:
            res = await changes.apply(need_client(), cs, s.write_enabled, player=player)
        finally:
            cache.clear()  # also after a refused or partial apply: never serve pre-write state as current
            app.state.drop_estate()
        return JSONResponse(_dump(res))

    game.install(app, open_store=lambda: GameStore(s.state_dir / "game.sqlite3"), netbox_url=s.netbox_url,
                 fixtures_dir=s.fixtures_dir, local_player=s.local_player, allowed_hosts=allowed_hosts,
                 cookie_secure=s.cookie_secure, transport=transport, netbox_token=s.netbox_token,
                 recheck_s=s.session_recheck)
    questroutes.install(app, client=client, fixtures_dir=s.fixtures_dir, write_enabled=s.write_enabled, estate_ttl=s.estate_ttl,
                        clear_cache=cache.clear)
    return app
