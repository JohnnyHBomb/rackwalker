"""The bounded read of the estate, against a made-up NetBox (httpx.MockTransport): slim field selections, paging,
caps, lists the proxy may not view, the cache and its invalidation by a write."""
from __future__ import annotations

import asyncio
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

from rackwalker_proxy import estate, findings
from rackwalker_proxy import netbox as nb
from rackwalker_proxy.app import Settings, create_app
from rackwalker_proxy.game import CSRF_HEADER

from .conftest import LOCAL, schema_validator


def ref(i: int, **extra: Any) -> dict[str, Any]:
    return {"id": i, **extra}


def tiny(devices: int = 3) -> dict[str, list[dict[str, Any]]]:
    """A NetBox with one site, one room, one rack and `devices` switches of 2 ports each; switch 1 and 2 are cabled."""
    ports = [{"id": d * 10 + p, "name": f"eth{p}", "device": ref(d, name=f"sw{d}"), "type": {"value": "1000base-t"}, "enabled": True,
              "mgmt_only": False, "cable": None, "description": "", "mark_connected": False} for d in range(1, devices + 1) for p in (1, 2)]
    ports[0]["cable"] = ports[2]["cable"] = ref(1)
    return {
        "dcim/sites": [{"id": 1, "name": "Mill", "slug": "mill", "status": {"value": "active"}, "latitude": 52.0, "longitude": 4.0, "facility": "M1",
                        "region": None, "physical_address": "", "custom_fields": {"iso_country": "NL"}}],
        "dcim/locations": [{"id": 1, "name": "Room", "site": ref(1)}],
        "dcim/racks": [{"id": 1, "name": "R1", "site": ref(1), "location": ref(1), "u_height": 42, "custom_fields": {"iso_x": 1, "iso_y": 2}}],
        "dcim/devices": [{"id": d, "name": f"sw{d}", "site": ref(1), "location": ref(1), "rack": ref(1), "position": 10.0 + d, "face": {"value": "front"},
                          "role": ref(1), "device_type": ref(1), "status": {"value": "active"}, "serial": f"S{d}", "asset_tag": None,
                          "platform": None, "primary_ip": {"id": d, "address": f"192.0.2.{d}/24"}} for d in range(1, devices + 1)],
        "dcim/device-types": [{"id": 1, "manufacturer": ref(1, name="Example"), "model": "EX", "slug": "ex", "u_height": 1.0, "is_full_depth": False}],
        "dcim/device-roles": [{"id": 1, "slug": "switch", "name": "Switch", "color": "2196f3"}],
        "dcim/interfaces": ports,
        "dcim/cables": [{"id": 1, "type": "cat6", "label": "", "status": {"value": "connected"},
                         "a_terminations": [{"object_type": "dcim.interface", "object_id": 11}],
                         "b_terminations": [{"object_type": "dcim.interface", "object_id": 21}]}],
        "ipam/prefixes": [{"id": 1, "prefix": "192.0.2.0/24"}],
        "ipam/ip-addresses": [{"id": d, "address": f"192.0.2.{d}/24", "assigned_object_type": "dcim.interface", "assigned_object_id": d * 10 + 1}
                              for d in range(1, devices + 1)],
    }


class FakeNetBox:
    def __init__(self, data: dict[str, list[dict[str, Any]]], refuse: tuple[str, ...] = ()):
        self.data, self.refuse, self.calls = data, refuse, []

    def __call__(self, req: httpx.Request) -> httpx.Response:
        path = req.url.path.removeprefix("/api/").strip("/")
        q = req.url.params
        self.calls.append((req.method, path, dict(q)))
        if path == "status":
            return httpx.Response(200, json={"netbox-version": "4.7.4"})
        if path in self.refuse:
            return httpx.Response(403, json={"detail": "You do not have permission to perform this action."})
        rows = self.data.get(path, [])
        ids = q.get_list("id")
        if ids:
            rows = [r for r in rows if str(r["id"]) in ids]
        offset, limit = int(q.get("offset", 0)), int(q.get("limit", 50))
        return httpx.Response(200, json={"count": len(rows), "results": rows[offset:offset + limit]})

    def lists(self, path: str) -> list[dict[str, str]]:
        return [q for m, p, q in self.calls if p == path and "fields" in q]


def read(fake: FakeNetBox) -> estate.Estate:
    async def go() -> estate.Estate:
        client = nb.NetBox("http://netbox.invalid", "nbt_x.y", transport=httpx.MockTransport(fake))
        try:
            return await estate.read_estate(client)
        finally:
            await client.aclose()
    return asyncio.run(go())


def test_the_estate_is_read_with_slim_field_selections_and_becomes_plain_data():
    fake = FakeNetBox(tiny())
    e = read(fake)
    assert (len(e.sites), len(e.devices), len(e.ports), len(e.cables), e.truncated, e.unreadable) == (1, 3, 6, 1, [], [])
    assert e.sites[1] == {"id": 1, "name": "Mill", "slug": "mill", "status": "active", "latitude": 52.0, "longitude": 4.0, "facility": "M1",
                          "country": "NL"}
    assert e.devices[2]["role"] == "switch" and e.devices[2]["primary_ip"] == "192.0.2.2/24" and e.racks[1]["iso_x"] == 1
    assert e.cables[1]["a"] == [("interface", 11)] and e.ports[("interface", 21)]["cable_id"] == 1
    # every list asks NetBox only for the fields the scanner uses
    assert fake.lists("dcim/devices")[0]["fields"] == "id,name,site,location,rack,position,face,role,device_type,status,serial,asset_tag,platform,primary_ip"
    assert fake.lists("dcim/interfaces")[0]["fields"] == "id,name,device,type,enabled,mgmt_only,cable,description,mark_connected"
    # the estate survives a round trip through JSON (the fixture-mode estate is stored that way)
    again = estate.Estate.from_json(e.to_json())
    assert again.cables == e.cables and again.ports == e.ports and again.devices == e.devices
    # sw3 has two ports and no cable; nothing else is wrong with this estate
    assert [f["id"] for f in findings.analyse(e).findings] == ["device_unconnected:device:3"]


def test_a_long_list_is_read_page_by_page(monkeypatch):
    monkeypatch.setattr(nb, "PAGE", 4)
    fake = FakeNetBox(tiny(devices=9))
    e = read(fake)
    assert len(e.devices) == 9 and len(e.ports) == 18 and e.truncated == []
    assert [(q["offset"], q["limit"]) for q in fake.lists("dcim/devices")] == [("0", "4"), ("4", "4"), ("8", "4")]


def test_a_list_past_its_cap_is_cut_and_named_and_its_rules_stay_silent(monkeypatch):
    monkeypatch.setattr(nb, "PAGE", 4)
    monkeypatch.setitem(estate.CAPS, "ports", 8)
    fake = FakeNetBox(tiny(devices=9))
    e = read(fake)
    assert e.truncated == ["ports"] and len(e.ports) == 8
    assert len(fake.lists("dcim/interfaces")) == 2      # it stops asking at the cap
    a = findings.analyse(e)
    # with only part of the ports in hand, nothing is said about connections, power or addresses
    assert not [f for f in a.findings if f["rank"] == "connectivity"]


def test_a_list_the_proxy_may_not_view_is_named_not_reported_as_missing():
    e = read(FakeNetBox(tiny(), refuse=("ipam/prefixes", "ipam/ip-addresses", "circuits/providers")))
    assert sorted(e.unreadable) == ["ips", "prefixes", "providers"]
    a = findings.analyse(e)
    # no "address in no prefix", no "no management address": the game could not look
    assert not [f for f in a.findings if f["kind"] in ("address_no_prefix", "device_unreachable", "router_no_address")]
    assert not [g for g in a.gaps if g["field"] == "primary_ip"]


@pytest.fixture
def live_like(tmp_path):
    fake = FakeNetBox(tiny())
    app = create_app(Settings("http://netbox.invalid", "nbt_x.y", False, None, cache_ttl=0, state_dir=tmp_path, estate_ttl=60),
                     transport=httpx.MockTransport(fake))
    with TestClient(base_url=LOCAL, app=app) as cl:
        yield cl, fake, app


def test_the_gap_scan_is_cached_and_a_write_through_the_proxy_drops_it(live_like):
    cl, fake, app = live_like
    first = cl.get("/api/quests/gaps/1").json()
    schema_validator("GapList").validate(first)
    assert (first["source"], first["total"], first["incomplete"]) == ("netbox", 0, [])
    reads = len(fake.lists("dcim/devices"))
    second = cl.get("/api/quests/gaps/1").json()
    assert second["source"] == "cache" and len(fake.lists("dcim/devices")) == reads      # no new read of NetBox
    assert {**second, "source": "netbox"} == first                                          # the same answer, in the same order
    app.state.drop_estate()                                                                 # what every apply does
    assert cl.get("/api/quests/gaps/1").json()["source"] == "netbox" and len(fake.lists("dcim/devices")) == reads + 1


def test_health_says_whether_the_proxy_can_check_permissions(tmp_path):
    for refuse, want in (((), "ok"), (("users/permissions",), "cannot_view_permissions"), (("users/users",), "cannot_view_users")):
        fake = FakeNetBox(tiny(), refuse=refuse)
        app = create_app(Settings("http://netbox.invalid", "nbt_x.y", False, None, state_dir=tmp_path), transport=httpx.MockTransport(fake))
        with TestClient(base_url=LOCAL, app=app) as cl:
            h = cl.get("/api/health").json()
            schema_validator("HealthResponse").validate(h)
            assert h["netbox"]["permission_check"] == want, refuse
            cl.get("/api/health")
            assert len([c for c in fake.calls if c[1] == "users/users"]) == 1    # asked once, then remembered for a minute
    assert CSRF_HEADER


def test_the_quest_log_can_ask_for_a_fresh_read_of_netbox(live_like, monkeypatch):
    cl, fake, app = live_like
    # a signed-in player (the fake NetBox vouches for any token as user 9)
    real = fake.__call__

    def with_user(req: httpx.Request) -> httpx.Response:
        if req.url.path == "/api/authentication-check/":
            return httpx.Response(200, json={"id": 9, "username": "walker", "first_name": "", "last_name": ""})
        if req.url.path == "/api/users/users/9/":
            return httpx.Response(200, json={"id": 9, "username": "walker", "is_active": True, "groups": []})
        return real(req)
    monkeypatch.setattr(FakeNetBox, "__call__", lambda self, req: with_user(req))
    r = cl.post("/api/game/session", json={"token": "nbt_synthetic.not-a-secret-0123456789"}, headers={CSRF_HEADER: "sign-in"})
    assert r.status_code == 200, r.text
    b = cl.get("/api/game/quests").json()
    schema_validator("QuestBoard").validate(b)
    assert [q["kind"] for q in b["quests"]] == ["device_unconnected"] and b["can_write"] is False
    reads = len(fake.lists("dcim/devices"))
    cl.get("/api/game/quests")
    assert len(fake.lists("dcim/devices")) == reads            # kept
    cl.get("/api/game/quests", params={"fresh": "true"})
    assert len(fake.lists("dcim/devices")) == reads + 1        # read again on request


def test_a_fix_that_was_later_undone_in_netbox_reopens_nothing_and_is_counted(live_like, monkeypatch):
    cl, fake, app = live_like
    real = fake.__call__

    def with_user(req: httpx.Request) -> httpx.Response:
        if req.url.path == "/api/authentication-check/":
            return httpx.Response(200, json={"id": 9, "username": "walker", "first_name": "", "last_name": ""})
        if req.url.path == "/api/users/users/9/":
            return httpx.Response(200, json={"id": 9, "username": "walker", "is_active": True, "groups": []})
        return real(req)
    monkeypatch.setattr(FakeNetBox, "__call__", lambda self, req: with_user(req))
    assert cl.post("/api/game/session", json={"token": "nbt_synthetic.not-a-secret-0123456789"}, headers={CSRF_HEADER: "sign-in"}).status_code == 200
    offered = cl.get("/api/game/quests").json()["quests"][0]
    assert (offered["id"], offered["status"]) == ("q:device_unconnected:device:3", "offered")
    # the player completed this line once (NetBox confirmed it then, and paid); since then the cable was removed in NetBox
    st = app.state.game_store()
    st.put_quest(9, "netbox", {"quest_id": offered["id"], "site_id": 1, "status": "done", "answers": {}, "checks": ["device_unconnected:device:3"],
                               "verified": ["device_unconnected:device:3"],
                               "snapshot": {k: offered[k] for k in ("id", "kind", "rank", "site_id", "title", "purpose", "outcome", "xp", "minutes", "finding")}})
    st.add_reward(9, offered["id"], "xp", 120, "device_unconnected:1")
    b = cl.get("/api/game/quests").json()
    schema_validator("QuestBoard").validate(b)
    line = next(q for q in b["quests"] if q["id"] == offered["id"])
    # nothing is reopened and nothing is taken away; the log says so, and the site's completeness tells the truth
    assert (line["status"], line["lapsed"], line["step"]) == ("done", True, None)
    assert not [q for q in b["quests"] if q["status"] == "offered"] and not b["all_in_order"]
    assert b["message"] == "Nothing new to ask at your estate. 1 thing you recorded before is no longer in NetBox; nothing is reopened."
    assert b["rewards"]["xp"] == 120 and b["rewards"]["lapsed"] == 1 and b["rewards"]["completed"] == 1
    assert b["sites"][0]["completeness"]["open"] == 1 and not b["sites"][0]["boss_ready"]
