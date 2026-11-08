"""Offline twin of test_live_netbox.py: the same routes, served from NetBox answers recorded on the live dev stack.

Every body must validate against the JSON Schema and equal the committed fixture (volatile fields stripped), so
the proxy's NetBox -> contract mapping is pinned without a network.
"""
from __future__ import annotations

import httpx
import pytest
from fastapi.testclient import TestClient

from rackwalker_proxy.app import Settings, create_app

from .conftest import INDEX, LOCAL, load, schema_validator
from .netbox_replay import RECORDED_HOST, Replay, load as recordings, strip

GETS = [f for f in INDEX if f["method"] == "GET" and f["path"] != "/api/health" and not f.get("store")]  # store: game routes
TOKEN = "nbt_abcdef123456.0123456789abcdef0123456789abcdef01234567"


def client(transport: httpx.AsyncBaseTransport, write: bool = False, ttl: float = 0) -> TestClient:
    """The proxy on recorded NetBox answers."""
    return TestClient(base_url=LOCAL, app=create_app(Settings(RECORDED_HOST, TOKEN, write, None, cache_ttl=ttl), transport=transport))


@pytest.fixture
def ro():
    t = Replay()
    with client(t) as cl:
        cl.transport_ = t
        yield cl


@pytest.mark.parametrize("f", GETS, ids=[f["path"] for f in GETS])
def test_route_matches_fixture_replayed(ro, f):
    r = ro.get(f["path"])
    assert r.status_code == f["status"], r.text
    schema_validator(f["schema"]).validate(r.json())
    assert strip(r.json()) == strip(load(f["file"]))


def test_preview_matches_fixture_replayed(ro):
    req = load("changes-request.json")
    del req["ops"][0]["if_unmodified_since"]
    r = ro.post("/api/changes/preview", json=req)
    schema_validator("ChangeResult").validate(r.json())
    assert r.json() == load("changes-preview.json")
    assert not [c for c in ro.transport_.calls if not c.startswith("GET ")]  # preview never writes


@pytest.mark.parametrize("path,cables", [("/api/trace/front_port/97", [30]), ("/api/trace/rear_port/97", [31, 32]),
                                         ("/api/trace/circuit_termination/1", [78])])
def test_pass_through_trace_replayed(ro, path, cables):
    body = ro.get(path).json()
    schema_validator("TraceResponse").validate(body)
    assert [h["cable"]["id"] for h in body["trace"]["hops"] if h["cable"]] == cables
    assert body["trace"]["is_complete"] is True


def test_every_request_carries_the_v2_token_and_nothing_leaks(ro):
    seen = []

    class Spy(Replay):
        async def handle_async_request(self, request):
            seen.append(request.headers["authorization"])
            return await super().handle_async_request(request)

    with client(Spy()) as cl:
        r = cl.get("/api/scene/site/1")
    assert r.status_code == 200 and set(seen) == {f"Bearer {TOKEN}"}
    assert TOKEN not in r.text and TOKEN.split(".")[1] not in r.text


def test_read_cache_serves_then_apply_invalidates():
    writes = []

    def on_write(req: httpx.Request) -> httpx.Response:
        writes.append(req)
        rec = recordings()["GET /api/dcim/racks/?id=2&limit=1000&offset=0"]["json"]["results"][0]
        return httpx.Response(200, json=rec, request=req)

    t = Replay(on_write=on_write)
    with client(t, write=True, ttl=60) as cl:
        first = cl.get("/api/scene/rack/2").json()
        n = len(t.calls)
        second = cl.get("/api/scene/rack/2").json()
        assert (first["meta"]["source"], second["meta"]["source"], len(t.calls)) == ("netbox", "cache", n)
        assert second["meta"]["generated_at"] == first["meta"]["generated_at"]  # when the body was built
        assert strip(second) == strip(first)
        res = cl.post("/api/changes/apply", json={"ops": [
            {"op": "update_fields", "target": {"kind": "rack", "id": 2}, "fields": {"description": "x"}}]}).json()
        assert res["applied"] is True and len(writes) == 1
        assert cl.get("/api/scene/rack/2").json()["meta"]["source"] == "netbox"
        assert len(t.calls) > n + 1


def test_unsupported_netbox_version_is_502():
    over = {"GET /api/status/": {"status": 200, "json": {"netbox-version": "4.1.11"}}}
    with client(Replay(overrides=over)) as cl:
        r = cl.get("/api/scene/world")
    assert (r.status_code, r.json()["error"]["code"]) == (502, "unsupported_netbox_version")
    schema_validator("ErrorResponse").validate(r.json())


def test_unreachable_netbox_is_502():
    def boom(request):
        raise httpx.ConnectError("refused", request=request)

    with client(httpx.MockTransport(boom)) as cl:
        r = cl.get("/api/scene/site/1")
    assert (r.status_code, r.json()["error"]["code"]) == (502, "netbox_unreachable")
    assert TOKEN not in r.text


# ---- regressions: cache expiry, cache keys, token never logged
class FakeClock:
    def __init__(self, t: float = 1000.0):
        self.t = t

    def __call__(self) -> float:
        return self.t


def test_read_cache_entry_expires_after_ttl():
    with client(Replay(), ttl=10) as cl:
        clock = FakeClock()
        cl.app.state.cache.clock = clock
        seen = []
        for t in (1000.0, 1009.9, 1010.1):
            clock.t = t
            seen.append(cl.get("/api/scene/rack/2").json()["meta"]["source"])
    assert seen == ["netbox", "cache", "netbox"]


@pytest.mark.parametrize("a,b", [("/api/scene/rack/1", "/api/scene/rack/2"), ("/api/scene/site/1", "/api/scene/site/2"),
                                 ("/api/trace/front_port/97", "/api/trace/rear_port/97"),
                                 ("/api/trace/interface/172", "/api/trace/interface/173")])
def test_read_cache_keeps_routes_apart(a, b):
    with client(Replay(), ttl=60) as cl:
        first, second = cl.get(a).json(), cl.get(b).json()
        again = cl.get(b).json()
    plain = {p: strip(client(Replay()).get(p).json()) for p in (a, b)}
    assert strip(first) == plain[a] and strip(second) == plain[b] and strip(again) == plain[b]
    assert second["meta"]["source"] == "netbox" and again["meta"]["source"] == "cache"


def test_token_never_logged(caplog):
    caplog.set_level("DEBUG")
    with client(Replay()) as cl:
        assert cl.get("/api/scene/site/1").status_code == 200
        cl.get("/api/device/2")
        cl.get("/api/trace/interface/173")
    assert caplog.records, "expected some log output to inspect (httpx logs each request at INFO)"
    assert TOKEN not in caplog.text and TOKEN.split(".")[1] not in caplog.text


# ---- regressions: the cache after an apply
RACK2 = "GET /api/dcim/racks/?id=2&limit=1000&offset=0"
DESCRIBE_RACK2 = {"ops": [{"op": "update_fields", "target": {"kind": "rack", "id": 2}, "fields": {"description": "x"}}]}


@pytest.mark.parametrize("answer", [400, "timeout"])
def test_cache_cleared_after_a_failed_apply(answer):
    """Not only after a successful apply: a write that failed (or whose outcome is unknown) may still have changed
    NetBox, so the next read goes to NetBox (mutant `if res.applied: cache.clear()` must die)."""
    def on_write(req):
        if answer == "timeout":
            raise httpx.ReadTimeout("slow", request=req)
        return httpx.Response(answer, json={"description": ["no"]}, request=req)

    with client(Replay(on_write=on_write), write=True, ttl=60) as cl:
        assert cl.get("/api/scene/rack/2").json()["meta"]["source"] == "netbox"
        assert cl.get("/api/scene/rack/2").json()["meta"]["source"] == "cache"
        res = cl.post("/api/changes/apply", json=DESCRIBE_RACK2).json()
        assert res["applied"] is False and res["ops"][0]["status"] == "error"
        assert cl.get("/api/scene/rack/2").json()["meta"]["source"] == "netbox"


def test_read_started_before_an_apply_is_not_cached_after_it():
    """A read still running when an apply finishes must not put its pre-write body into the cache (it would be served
    as current, meta.source 'cache', for up to the TTL)."""
    import asyncio

    async def scenario():
        gate, entered = asyncio.Event(), asyncio.Event()
        state = {"held": False}

        class Slow(Replay):
            async def handle_async_request(self, request):
                if request.method == "GET" and "/dcim/racks/2/" in request.url.path and not state["held"]:
                    state["held"] = True  # hold only the first read of rack 2 (the scene read), not apply's own reads
                    entered.set()
                    await gate.wait()
                return await super().handle_async_request(request)

        def on_write(req):
            return httpx.Response(200, json=recordings()[RACK2]["json"]["results"][0], request=req)

        app = create_app(Settings(RECORDED_HOST, TOKEN, True, None, cache_ttl=60), transport=Slow(on_write=on_write))
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=LOCAL) as ac:
            slow_read = asyncio.create_task(ac.get("/api/scene/rack/2"))
            await asyncio.wait_for(entered.wait(), 5)
            res = (await ac.post("/api/changes/apply", json=DESCRIBE_RACK2)).json()
            assert res["applied"] is True
            gate.set()
            assert (await slow_read).json()["meta"]["source"] == "netbox"
            return (await ac.get("/api/scene/rack/2")).json()["meta"]["source"]

    assert asyncio.run(scenario()) == "netbox"
