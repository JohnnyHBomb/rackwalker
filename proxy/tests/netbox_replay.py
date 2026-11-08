"""Record NetBox traffic from the live dev stack and replay it in unit tests (httpx transports, no extra library).

Recordings (`recorded/netbox-4.7.4.json.gz`; gzip with mtime 0, so re-recording the same answers is byte-identical)
are keyed "<METHOD> <path>?<query>" with the NetBox host replaced by RECORDED_HOST, so the file carries no real
hostnames and no auth header (the token is a request header and is never recorded).
Regenerate with `make test-live RACKWALKER_RECORD=1` against a freshly seeded stack.
"""
from __future__ import annotations

import gzip
import json
from pathlib import Path
from typing import Any

import httpx

RECORDINGS = Path(__file__).resolve().parent / "recorded" / "netbox-4.7.4.json.gz"
RECORDED_HOST = "http://netbox.test"
VOLATILE = {"generated_at", "netbox_version", "source", "last_updated", "netbox_url", "request_id", "fetched_at"}


def key(req: httpx.Request) -> str:
    q = req.url.query.decode() if isinstance(req.url.query, bytes) else str(req.url.query)
    return f"{req.method} {req.url.path}" + (f"?{q}" if q else "")


class Recorder(httpx.AsyncBaseTransport):
    """Pass-through transport that keeps every GET answer (body, status) for the replay file."""

    def __init__(self, base_url: str):
        self.inner = httpx.AsyncHTTPTransport()
        self.base = base_url.rstrip("/")
        self.seen: dict[str, dict[str, Any]] = {}

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        resp = await self.inner.handle_async_request(request)
        body = await resp.aread()
        if request.method == "GET" and request.url.path.startswith("/api/"):
            text = body.decode().replace(self.base, RECORDED_HOST)
            try:
                self.seen[key(request)] = {"status": resp.status_code, "json": json.loads(text)}
            except ValueError:
                self.seen[key(request)] = {"status": resp.status_code, "json": None}
        return httpx.Response(resp.status_code, headers=resp.headers, content=body, request=request)

    def save(self, path: Path = RECORDINGS) -> None:
        old = read(path) if path.exists() else {}
        path.parent.mkdir(exist_ok=True)
        text = json.dumps({**old, **self.seen}, indent=None, sort_keys=True, separators=(",", ":")) + "\n"
        path.write_bytes(gzip.compress(text.encode(), compresslevel=9, mtime=0))


class Replay(httpx.AsyncBaseTransport):
    """Serves recorded GETs; writes go to `on_write` (or fail). Unknown GETs fail loudly so a test never silently
    depends on a request that was not recorded."""

    def __init__(self, recordings: dict[str, Any] | None = None, on_write=None, overrides: dict[str, Any] | None = None):
        self.rec = recordings if recordings is not None else load()
        self.overrides = overrides or {}
        self.on_write = on_write
        self.calls: list[str] = []

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        k = key(request)
        self.calls.append(k)
        if request.method != "GET":
            if self.on_write is None:
                raise AssertionError(f"unexpected write {k}")
            return self.on_write(request)
        hit = self.overrides.get(k) or self.rec.get(k)
        if hit is None:
            raise AssertionError(f"no recording for {k}")
        return httpx.Response(hit["status"], json=hit["json"], request=request)


_cache: dict[str, Any] | None = None


def load() -> dict[str, Any]:
    global _cache
    if _cache is None:
        _cache = read(RECORDINGS)
    return _cache


def read(path: Path) -> dict[str, Any]:
    return json.loads(gzip.decompress(path.read_bytes()))


def strip(x: Any) -> Any:
    """Drop fields that legitimately differ between fixtures and a live answer; 38.0 == 38."""
    if isinstance(x, dict):
        return {k: strip(v) for k, v in x.items() if k not in VOLATILE}
    if isinstance(x, list):
        return [strip(v) for v in x]
    return x
