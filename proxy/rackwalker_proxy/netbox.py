"""Typed async NetBox client (httpx).

Why httpx and not pynetbox: httpx is already the proxy's HTTP stack, is async (FastAPI handlers do not block a
worker thread per NetBox call), and `httpx.MockTransport` lets the unit tests replay recorded NetBox responses
without a network or an extra mocking library. pynetbox is synchronous, returns untyped `Record` objects, and
hides the request (no control over v2 `Bearer` auth, pagination size or `fields=`). The proxy reads ~12 endpoints;
a thin typed layer over them is less code than adapting pynetbox.

Typing: every NetBox object the proxy reads is parsed into a pydantic model below (`extra="ignore"`: NetBox adds
fields freely; a missing or mistyped field we rely on fails loudly as `netbox_error`). Selection values
(`{"value", "label"}`, including select custom fields in 4.7) are unwrapped to the raw value.

The token is only ever placed in the Authorization header; it never appears in a log line, an error message or a
response body (errors carry NetBox's response body, which does not echo the token).
"""
from __future__ import annotations

import asyncio
import time
from collections.abc import Iterable
from typing import Annotated, Any, TypeVar

import httpx
from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, ValidationError

from .errors import ApiError

SUPPORTED_PREFIX = "4.7."
PAGE = 1000  # NetBox MAX_PAGE_SIZE default
# Ids per `?id=` lookup. Each id adds ~6 bytes to the request line; gunicorn (netbox-docker) refuses lines over 4094
# bytes, i.e. somewhere past ~450 ids. 100 keeps every line under ~1 KB.
ID_CHUNK = 100

# PortKind (contract) -> NetBox REST endpoint, in PortKind enum order. The first seven belong to a device.
PORT_ENDPOINT: dict[str, str] = {
    "interface": "dcim/interfaces",
    "front_port": "dcim/front-ports",
    "rear_port": "dcim/rear-ports",
    "console_port": "dcim/console-ports",
    "console_server_port": "dcim/console-server-ports",
    "power_port": "dcim/power-ports",
    "power_outlet": "dcim/power-outlets",
    "power_feed": "dcim/power-feeds",
    "circuit_termination": "circuits/circuit-terminations",
}
DEVICE_PORT_KINDS = [k for k in PORT_ENDPOINT if k not in ("power_feed", "circuit_termination")]
# Kinds with NetBox /trace/ (PathEndpoint); the others are pass-through and only have /paths/.
TRACE_KINDS = {"interface", "console_port", "console_server_port", "power_port", "power_outlet", "power_feed"}
_KIND_BY_SEGMENT = {ep.split("/")[1]: kind for kind, ep in PORT_ENDPOINT.items()}


def auth_header(token: str) -> str:
    """v2 tokens (NetBox >= 4.5, `nbt_<key>.<token>`) use Bearer; a legacy v1 token (40 hex) uses Token."""
    return f"Bearer {token}" if token.startswith("nbt_") else f"Token {token}"


def kind_from_url(url: str) -> str | None:
    """PortKind of a nested NetBox object from its API url (…/api/dcim/front-ports/97/ -> front_port)."""
    parts = [p for p in url.split("/") if p]
    return _KIND_BY_SEGMENT.get(parts[-2]) if len(parts) >= 2 else None


def _unwrap(v: Any) -> Any:
    return v.get("value") if isinstance(v, dict) and "value" in v else v


def _blank_none(v: Any) -> Any:
    return None if v == "" else v


Choice = Annotated[Any, BeforeValidator(_unwrap)]
BlankNone = Annotated[str | None, BeforeValidator(_blank_none)]


class NbModel(BaseModel):
    model_config = ConfigDict(extra="ignore")


class Ref(NbModel):
    """Any nested (brief) NetBox object."""
    id: int
    name: str | None = None
    slug: str | None = None


class Tag(NbModel):
    slug: str


class Ip(NbModel):
    address: str


class NbObject(NbModel):
    id: int
    display_url: str | None = None
    last_updated: str | None = None
    custom_fields: dict[str, Any] = Field(default_factory=dict)

    def cf(self, name: str) -> Any:
        return _unwrap(self.custom_fields.get(name))


class Site(NbObject):
    slug: str
    name: str
    status: Choice
    latitude: float | None = None
    longitude: float | None = None
    description: str = ""
    facility: str | None = ""
    region: Ref | None = None
    physical_address: str | None = ""


class Location(NbObject):
    site: Ref
    parent: Ref | None = None
    name: str
    slug: str
    status: Choice = None
    description: str = ""


class Rack(NbObject):
    site: Ref
    location: Ref | None = None
    name: str
    status: Choice
    u_height: int
    starting_unit: int = 1
    desc_units: bool = False
    description: str = ""
    comments: str = ""


class Role(NbModel):
    id: int
    slug: str
    name: str
    color: str


class DeviceType(NbModel):
    id: int
    manufacturer: Ref
    model: str
    slug: str
    u_height: float
    is_full_depth: bool
    subdevice_role: Choice = None


class Device(NbObject):
    name: str | None = None
    role: Ref
    device_type: Ref
    site: Ref
    tenant: Ref | None = None
    location: Ref | None = None
    rack: Ref | None = None
    position: float | None = None
    face: Choice = None
    status: Choice
    primary_ip: Ip | None = None
    parent_device: Ref | None = None
    serial: str = ""
    asset_tag: str | None = None
    platform: Ref | None = None
    description: str = ""
    comments: str = ""
    tags: list[Tag] = Field(default_factory=list)


class CircuitRef(NbModel):
    id: int
    cid: str


class PortRef(NbModel):
    """A nested termination (port, circuit termination, power feed) as it appears in link_peers, cables and traces."""
    id: int
    url: str
    name: str | None = None
    device: Ref | None = None
    circuit: CircuitRef | None = None
    term_side: str | None = None

    @property
    def kind(self) -> str | None:
        return kind_from_url(self.url)


class Port(NbObject):
    device: Ref
    name: str
    type: Choice = None
    enabled: bool = True
    mgmt_only: bool = False
    description: str = ""
    cable: Ref | None = None
    link_peers: list[PortRef] = Field(default_factory=list)
    connected_endpoints: list[PortRef] | None = None


class CableTerm(NbModel):
    object_type: str
    object_id: int
    object: PortRef


class Cable(NbObject):
    label: BlankNone = None
    type: BlankNone = None
    color: BlankNone = None
    status: Choice
    length: float | None = None
    length_unit: Choice = None
    a_terminations: list[CableTerm] = Field(default_factory=list)
    b_terminations: list[CableTerm] = Field(default_factory=list)
    description: str = ""


class TraceCable(NbModel):
    id: int
    label: BlankNone = None
    type: BlankNone = None
    color: BlankNone = None
    status: Choice = None


class CircuitTermination(NbObject):
    circuit: CircuitRef
    term_side: str
    termination_type: str | None = None
    termination_id: int | None = None

    @property
    def site_id(self) -> int | None:
        """The site, when the termination is attached to one (NetBox >= 4.2 replaced `site` by a generic termination)."""
        return self.termination_id if self.termination_type == "dcim.site" else None


class Circuit(NbObject):
    cid: str
    provider: Ref
    type: Ref
    status: Choice


class CablePath(NbModel):
    """One entry of a pass-through port's /paths/: `path` alternates [near ends], [cable], [far ends]."""
    id: int
    path: list[list[dict[str, Any]]]
    is_complete: bool
    is_split: bool


M = TypeVar("M", bound=BaseModel)


def parse(model: type[M], data: Any, what: str) -> M:
    try:
        return model.model_validate(data)
    except ValidationError as e:
        raise ApiError("netbox_error", f"NetBox returned an unexpected {what} object.",
                       {"what": what, "errors": e.errors(include_url=False, include_input=False)[:5]}) from None


NOT_SENT = (httpx.ConnectError, httpx.ConnectTimeout, httpx.PoolTimeout, httpx.UnsupportedProtocol)


class NetBox:
    """Async NetBox REST client. One instance per proxy process (shares the connection pool)."""

    VERSION_TTL = 60.0

    def __init__(self, url: str, token: str, *, timeout: float = 10.0, transport: httpx.AsyncBaseTransport | None = None):
        self.url = url.rstrip("/")
        extra = {"transport": transport} if transport is not None else {}
        self._client = httpx.AsyncClient(
            base_url=self.url + "/api/", timeout=timeout, follow_redirects=False,
            headers={"Authorization": auth_header(token), "Accept": "application/json"}, **extra)
        self._version: tuple[float, str] | None = None

    async def aclose(self) -> None:
        await self._client.aclose()

    # ---- raw HTTP ----
    async def request(self, method: str, path: str, *, params: Any = None, json: Any = None,
                      not_found: dict[str, Any] | None = None) -> Any:
        try:
            r = await self._client.request(method, path, params=params, json=json)
        except httpx.HTTPError as e:
            # `maybe_sent`: the connection was up when it failed (read timeout, connection lost mid-answer), so NetBox
            # may have received, and for a write committed, the request. Only connect/pool failures are known unsent.
            maybe_sent = not isinstance(e, NOT_SENT)
            msg = "NetBox did not answer in time." if isinstance(e, httpx.TimeoutException) else "NetBox could not be reached."
            raise ApiError("netbox_unreachable", msg, {"path": path, "reason": type(e).__name__, "maybe_sent": maybe_sent}) from None
        if r.status_code == 404 and not_found is not None:
            kind, oid = not_found["kind"], not_found["id"]
            raise ApiError("not_found", f"{kind.replace('_', ' ').capitalize()} {oid} does not exist.", not_found)
        if r.status_code >= 400:
            try:
                body: Any = r.json()
            except ValueError:
                body = r.text[:300]
            raise ApiError("netbox_error", f"NetBox answered HTTP {r.status_code} to {method} {path}.",
                           {"status": r.status_code, "path": path, "netbox": body})
        if r.status_code == 204:
            return None
        try:
            return r.json()
        except ValueError:
            raise ApiError("netbox_error", f"NetBox answered {method} {path} with a body that is not JSON.",
                           {"path": path}) from None

    async def list_raw(self, path: str, params: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        """Every row of a list endpoint, page by page (offset paging; never follows NetBox's absolute `next`)."""
        out: list[dict[str, Any]] = []
        base = [(k, x) for k, v in (params or {}).items() for x in (v if isinstance(v, (list, tuple)) else [v])]
        while True:
            page = await self.request("GET", path + "/", params=base + [("limit", PAGE), ("offset", len(out))])
            out.extend(page["results"])
            if not page["results"] or len(out) >= page["count"]:
                return out

    async def list(self, model: type[M], path: str, params: dict[str, Any] | None = None) -> list[M]:
        return [parse(model, row, path) for row in await self.list_raw(path, params)]

    async def get(self, model: type[M], path: str, oid: int, kind: str) -> M:
        data = await self.request("GET", f"{path}/{oid}/", not_found={"kind": kind, "id": oid})
        return parse(model, data, path)

    async def by_ids(self, model: type[M], path: str, ids: Iterable[int],
                     params: dict[str, Any] | None = None) -> dict[int, M]:
        """The objects with these ids (absent ids are simply missing), in `?id=` lookups of at most ID_CHUNK ids."""
        want = sorted(set(ids))
        chunks = [want[i:i + ID_CHUNK] for i in range(0, len(want), ID_CHUNK)]
        lists = await asyncio.gather(*(self.list(model, path, {**(params or {}), "id": ch}) for ch in chunks))
        return {r.id: r for rows in lists for r in rows}  # type: ignore[attr-defined]

    # ---- version ----
    async def status(self) -> dict[str, Any]:
        return await self.request("GET", "status/")

    async def version(self) -> str:
        """NetBox version (cached 60 s). Raises unsupported_netbox_version unless 4.7.x."""
        now = time.monotonic()
        if self._version is None or now - self._version[0] > self.VERSION_TTL:
            v = (await self.status()).get("netbox-version")
            self._version = (now, str(v) if v else "")
        v = self._version[1]
        if not v.startswith(SUPPORTED_PREFIX):
            raise ApiError("unsupported_netbox_version", f"NetBox {v or 'of unknown version'} is not supported; contract 1.1.0 needs 4.7.x.",
                           {"version": v or None, "supported": SUPPORTED_PREFIX + "x"})
        return v

    # ---- typed reads ----
    async def roles(self, ids: Iterable[int]) -> dict[int, Role]:
        """Only the roles asked for: a real NetBox can hold thousands of device types (library imports)."""
        return await self.by_ids(Role, "dcim/device-roles", ids)

    async def device_types(self, ids: Iterable[int]) -> dict[int, DeviceType]:
        return await self.by_ids(DeviceType, "dcim/device-types", ids)

    async def device_ports(self, device_id: int) -> list[tuple[str, Port]]:
        """Every port of a device, PortKind order, then NetBox's own list order within a kind (DeviceDetail rule)."""
        lists = await asyncio.gather(*(self.list(Port, PORT_ENDPOINT[k], {"device_id": device_id}) for k in DEVICE_PORT_KINDS))
        return [(k, p) for k, ports in zip(DEVICE_PORT_KINDS, lists) for p in ports]

    async def trace(self, kind: str, port_id: int) -> list[Any]:
        return await self.request("GET", f"{PORT_ENDPOINT[kind]}/{port_id}/trace/", not_found={"kind": kind, "id": port_id})

    async def paths(self, kind: str, port_id: int) -> list[CablePath]:
        data = await self.request("GET", f"{PORT_ENDPOINT[kind]}/{port_id}/paths/", not_found={"kind": kind, "id": port_id})
        return [parse(CablePath, p, "cable path") for p in data]
