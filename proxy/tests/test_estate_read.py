"""The bounded read of the estate, against a made-up NetBox (httpx.MockTransport): slim field selections, paging,
caps, lists the proxy may not view, the cache and its invalidation by a write."""
from __future__ import annotations

import asyncio
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

from rackwalker_proxy import estate, findings, questroutes
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
        if q.get_list("site_id"):
            rows = [r for r in rows if self.sites_of(path, r) & set(q.get_list("site_id"))]
        if q.get_list("device_id"):
            owner = {str(p["id"]): str(p["device"]["id"]) for p in self.data.get("dcim/interfaces", [])}
            rows = [r for r in rows if owner.get(str(r.get("assigned_object_id"))) in q.get_list("device_id")]
        offset, limit = int(q.get("offset", 0)), int(q.get("limit", 50))
        return httpx.Response(200, json={"count": len(rows), "results": rows[offset:offset + limit]})

    def sites_of(self, path: str, row: dict[str, Any]) -> set[str]:
        """The sites a row belongs to, as NetBox's `site_id` filter sees it (a cable: the sites of its ends)."""
        dev_site = {d["id"]: str(d["site"]["id"]) for d in self.data.get("dcim/devices", [])}
        port_dev = {p["id"]: p["device"]["id"] for p in self.data.get("dcim/interfaces", [])}
        if path == "dcim/cables":
            return {dev_site[port_dev[t["object_id"]]] for side in ("a_terminations", "b_terminations") for t in row[side]}
        if "device" in row:
            return {dev_site[row["device"]["id"]]}
        return {str(row["site"]["id"])} if row.get("site") else set()

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
    assert len(fake.lists("dcim/devices")) == reads            # asked again too soon: the kept read answers
    monkeypatch.setattr(questroutes, "FRESH_ALL_S", 0.0)
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


def two_sites() -> dict[str, list[dict[str, Any]]]:
    """`tiny()` at site 1, and a second site with two switches (sw4, sw5), nothing cabled there."""
    data = tiny()
    data["dcim/sites"].append({**data["dcim/sites"][0], "id": 2, "name": "Quay", "slug": "quay"})
    data["dcim/locations"].append({"id": 2, "name": "Shed", "site": ref(2)})
    data["dcim/racks"].append({**data["dcim/racks"][0], "id": 2, "name": "Q1", "site": ref(2), "location": ref(2)})
    for d in (4, 5):
        data["dcim/devices"].append({**data["dcim/devices"][0], "id": d, "name": f"sw{d}", "site": ref(2), "location": ref(2), "rack": ref(2),
                                     "serial": f"S{d}", "primary_ip": {"id": d, "address": f"192.0.2.{d}/24"}})
        data["dcim/interfaces"] += [{**data["dcim/interfaces"][5], "id": d * 10 + p, "name": f"eth{p}", "device": ref(d, name=f"sw{d}")} for p in (1, 2)]
        data["ipam/ip-addresses"].append({"id": d, "address": f"192.0.2.{d}/24", "assigned_object_type": "dcim.interface", "assigned_object_id": d * 10 + 1})
    return data


def read_part(fake: FakeNetBox, site_ids: list[int]) -> estate.Estate:
    async def go() -> estate.Estate:
        client = nb.NetBox("http://netbox.invalid", "nbt_x.y", transport=httpx.MockTransport(fake))
        try:
            return await estate.read_estate(client, site_ids)
        finally:
            await client.aclose()
    return asyncio.run(go())


def cable(i: int, a: int, b: int) -> dict[str, Any]:
    return {"id": i, "type": "cat6", "label": "", "status": {"value": "connected"},
            "a_terminations": [{"object_type": "dcim.interface", "object_id": a}], "b_terminations": [{"object_type": "dcim.interface", "object_id": b}]}


def plug(data: dict[str, list[dict[str, Any]]], c: dict[str, Any] | None, *ports: int) -> None:
    for p in data["dcim/interfaces"]:
        if p["id"] in ports:
            p["cable"] = ref(c["id"]) if c else None


def test_one_site_is_read_again_and_put_into_the_kept_estate():
    data = two_sites()
    fake = FakeNetBox(data)
    whole = read(fake)
    assert sorted(whole.devices) == [1, 2, 3, 4, 5] and len(whole.ips) == 5
    # in NetBox: sw3 (site 1) gets a cable to sw4 (site 2), sw5 gets a serial-less twin, and the old cable 1 is removed
    link = cable(2, 31, 41)
    data["dcim/cables"] = [link]
    plug(data, None, 11, 21)
    plug(data, link, 31, 41)
    fake.calls.clear()
    part = read_part(fake, [1])
    # only site 1 is asked for in the big lists; addresses are asked for by its devices; the small lists are read whole
    assert all(q.get("site_id") == "1" for path in ("dcim/devices", "dcim/interfaces", "dcim/cables", "dcim/racks", "dcim/locations")
               for q in fake.lists(path))
    assert sorted(part.devices) == [1, 2, 3] and len(part.ips) == 3 and sorted(part.sites) == [1, 2]
    assert [dict(q).get("device_id") for q in fake.lists("ipam/ip-addresses")] == ["3"] or len(fake.lists("ipam/ip-addresses")) == 1
    before = whole.to_json()
    e = estate.splice(whole, part, [1])
    assert whole.to_json() == before                                   # the kept estate is not changed in place
    assert sorted(e.devices) == [1, 2, 3, 4, 5] and sorted(e.cables) == [2] and len(e.ips) == 5
    assert e.ports[("interface", 11)]["cable_id"] is None and e.ports[("interface", 31)]["cable_id"] == 2
    assert e.ports[("interface", 41)]["cable_id"] == 2                 # the far end at the other site knows its cable
    assert e.components().same(3, 4) and not e.components().same(1, 2)
    # the same answer as reading everything again
    assert e.to_json() == read(fake).to_json()
    # the cable is removed again: the far end at the other site is freed too
    data["dcim/cables"] = []
    plug(data, None, 31, 41)
    e2 = estate.splice(e, read_part(fake, [1]), [1])
    assert e2.cables == {} and e2.ports[("interface", 41)]["cable_id"] is None and e2.to_json() == read(fake).to_json()


def walker(fake: FakeNetBox, monkeypatch, permissions: list[dict[str, Any]] | None = None) -> None:
    """The fake NetBox vouches for any token as user 9, with the given object permissions."""
    real = fake.__call__

    def with_user(req: httpx.Request) -> httpx.Response:
        if req.url.path == "/api/authentication-check/":
            return httpx.Response(200, json={"id": 9, "username": "walker", "first_name": "", "last_name": ""})
        if req.url.path == "/api/users/users/9/":
            return httpx.Response(200, json={"id": 9, "username": "walker", "is_active": True, "groups": []})
        if req.url.path == "/api/users/permissions/":
            return httpx.Response(200, json={"count": len(permissions or []), "results": permissions or []})
        return real(req)
    monkeypatch.setattr(FakeNetBox, "__call__", lambda self, req: with_user(req))


def sign_in_walker(cl) -> None:
    r = cl.post("/api/game/session", json={"token": "nbt_synthetic.not-a-secret-0123456789"}, headers={CSRF_HEADER: "sign-in"})
    assert r.status_code == 200, r.text
    cl.headers[CSRF_HEADER] = r.json()["csrf_token"]


SW3 = "q:device_unconnected:device:3"
SW3_ANSWERS = [("peer", "device:1"), ("local_port", "interface:31"), ("peer_port", "interface:12"), ("cable_type", "cat6")]


def answer_sw3(cl) -> dict[str, Any]:
    for step, choice in SW3_ANSWERS:
        r = cl.post("/api/game/quests/answer", json={"scope": "netbox", "quest_id": SW3, "step": step, "choice": choice})
        assert r.status_code == 200, r.text
    return r.json()


def test_checking_netbox_keeps_the_answers_and_reads_only_the_lines_site_and_not_too_often(tmp_path, monkeypatch):
    fake = FakeNetBox(two_sites())
    walker(fake, monkeypatch)
    app = create_app(Settings("http://netbox.invalid", "nbt_x.y", False, None, cache_ttl=0, state_dir=tmp_path, estate_ttl=60),
                     transport=httpx.MockTransport(fake))
    with TestClient(base_url=LOCAL, app=app) as cl:
        sign_in_walker(cl)
        q = answer_sw3(cl)
        assert q["status"] == "ready" and len(q["said"]) == 4
        monkeypatch.setattr(questroutes, "FRESH_SITE_S", 0.0)
        fake.calls.clear()
        v = cl.post("/api/game/quests/verify", json={"scope": "netbox", "quest_id": SW3}).json()
        schema_validator("QuestVerdict").validate(v)
        # nothing was written, so nothing is confirmed and nothing is paid; the line stands where it stood
        assert (v["verified"], v["complete"], v["xp_awarded"], v["message"]) == (False, False, 0, "NetBox does not show it yet.")
        assert v["quest"]["status"] == "ready" and len(v["quest"]["said"]) == 4 and v["quest"]["proposal"] == q["proposal"]
        line = next(x for x in cl.get("/api/game/quests").json()["quests"] if x["id"] == SW3)
        assert line["status"] == "ready" and [s["answer"] for s in line["said"]] == [s["answer"] for s in q["said"]]
        # the read was of site 1 only
        assert fake.lists("dcim/devices") and all(x.get("site_id") == "1" for x in fake.lists("dcim/devices") + fake.lists("dcim/interfaces"))
        # asked again at once, NetBox is not read again
        monkeypatch.setattr(questroutes, "FRESH_SITE_S", 5.0)
        n = len(fake.calls)
        for _ in range(5):
            assert cl.post("/api/game/quests/verify", json={"scope": "netbox", "quest_id": SW3}).json()["xp_awarded"] == 0
        assert not [c for c in fake.calls[n:] if c[1].startswith(("dcim/", "ipam/", "circuits/"))]
        # "Read NetBox again" for one site reads that site, and a quest that is not open is not checked
        monkeypatch.setattr(questroutes, "FRESH_SITE_S", 0.0)
        fake.calls.clear()
        assert cl.get("/api/game/quests", params={"site_id": 2, "fresh": "true"}).status_code == 200
        assert [x.get("site_id") for x in fake.lists("dcim/devices")] == ["2"]
        assert cl.post("/api/game/quests/verify", json={"scope": "netbox", "quest_id": "q:device_unconnected:device:1"}).status_code == 404


def test_a_line_fixed_in_netbox_itself_is_confirmed_by_a_read_of_its_site(tmp_path, monkeypatch):
    data = two_sites()
    fake = FakeNetBox(data)
    walker(fake, monkeypatch, [{"id": 1, "enabled": True, "object_types": ["dcim.cable"], "actions": ["add"], "constraints": None}])
    app = create_app(Settings("http://netbox.invalid", "nbt_x.y", True, None, cache_ttl=0, state_dir=tmp_path, estate_ttl=60),
                     transport=httpx.MockTransport(fake))
    monkeypatch.setattr(questroutes, "FRESH_SITE_S", 0.0)
    with TestClient(base_url=LOCAL, app=app) as cl:
        sign_in_walker(cl)
        answer_sw3(cl)
        link = cable(2, 31, 12)          # the player records the cable in NetBox itself
        data["dcim/cables"].append(link)
        plug(data, link, 31, 12)
        v = cl.post("/api/game/quests/verify", json={"scope": "netbox", "quest_id": SW3}).json()
        assert (v["verified"], v["complete"], v["xp_awarded"], v["quest"]["status"]) == (True, True, 120, "done")
        assert v["message"] == "NetBox confirms it. Mill is now 96 % complete." and v["can_now"] == "You can now trace sw3 to what it plugs into."
        again = cl.post("/api/game/quests/verify", json={"scope": "netbox", "quest_id": SW3}).json()
        assert (again["xp_awarded"], again["rewards"]["xp"]) == (0, 120)


@pytest.mark.parametrize("permissions,can_write,board_says,line_says", [
    ([], False, "Your NetBox user may not change anything a quest writes", "Your NetBox user may not add dcim.cable."),
    ([{"id": 1, "enabled": True, "object_types": ["dcim.device"], "actions": ["change"], "constraints": None}], True,
     "Your NetBox user may not add dcim.cable, add circuits.circuit", "Your NetBox user may not add dcim.cable."),
    ([{"id": 1, "enabled": False, "object_types": ["dcim.cable"], "actions": ["add"], "constraints": None}], False,
     "Your NetBox user may not change anything", "Your NetBox user may not add dcim.cable."),
    ([{"id": 1, "enabled": True, "object_types": ["dcim.cable"], "actions": ["add"], "constraints": None}], True,
     "Your NetBox user may not add circuits.circuit", None),
])
def test_the_quest_log_says_up_front_what_the_players_netbox_user_may_not_write(tmp_path, monkeypatch, permissions, can_write, board_says, line_says):
    fake = FakeNetBox(tiny())
    walker(fake, monkeypatch, permissions)
    app = create_app(Settings("http://netbox.invalid", "nbt_x.y", True, None, cache_ttl=0, state_dir=tmp_path, estate_ttl=60),
                     transport=httpx.MockTransport(fake))
    with TestClient(base_url=LOCAL, app=app) as cl:
        sign_in_walker(cl)
        b = cl.get("/api/game/quests").json()
        schema_validator("QuestBoard").validate(b)
        assert b["write_enabled"] is True and b["can_write"] is can_write and b["write_blocked"].startswith(board_says)
        assert "Nothing was written" not in b["write_blocked"]
        q = answer_sw3(cl)
        # the ready line says whether its own change would be written, before the player presses Apply
        assert q["write_blocked"] is None if line_says is None else q["write_blocked"].startswith(line_says)


def test_a_proxy_that_cannot_read_permissions_says_so_in_the_quest_log(tmp_path, monkeypatch):
    fake = FakeNetBox(tiny(), refuse=("users/users",))
    real = fake.__call__

    def with_user(req: httpx.Request) -> httpx.Response:
        if req.url.path == "/api/authentication-check/":
            return httpx.Response(200, json={"id": 9, "username": "walker", "first_name": "", "last_name": ""})
        if req.url.path.startswith("/api/users/users/"):
            return httpx.Response(403, json={"detail": "You do not have permission to perform this action."})
        return real(req)
    monkeypatch.setattr(FakeNetBox, "__call__", lambda self, req: with_user(req))
    app = create_app(Settings("http://netbox.invalid", "nbt_x.y", True, None, cache_ttl=0, state_dir=tmp_path, estate_ttl=60, session_recheck=0),
                     transport=httpx.MockTransport(fake))
    with TestClient(base_url=LOCAL, app=app) as cl:
        sign_in_walker(cl)
        b = cl.get("/api/game/quests").json()
        assert b["can_write"] is False and b["write_blocked"].startswith("RackWalker cannot check what you may change, because NetBox refuses")


def test_a_list_that_was_cut_short_never_counts_as_complete(tmp_path, monkeypatch):
    monkeypatch.setitem(estate.CAPS, "circuits", 0)
    data = tiny(devices=2)                                    # nothing is wrong with what could be read
    data["circuits/providers"] = [{"id": 1, "name": "Carrier"}, {"id": 2, "name": "Other"}]
    monkeypatch.setattr(nb, "PAGE", 1)
    fake = FakeNetBox(data)
    walker(fake, monkeypatch)
    app = create_app(Settings("http://netbox.invalid", "nbt_x.y", False, None, cache_ttl=0, state_dir=tmp_path, estate_ttl=60),
                     transport=httpx.MockTransport(fake))
    with TestClient(base_url=LOCAL, app=app) as cl:
        sign_in_walker(cl)
        b = cl.get("/api/game/quests").json()
        schema_validator("QuestBoard").validate(b)
        assert b["incomplete"] == ["providers"] and not b["all_in_order"] and b["quests"] == []      # not even a spot check
        site = b["sites"][0]
        assert site["completeness"]["open"] == 0 and site["completeness"]["percent"] == 99 and not site["boss_ready"]
        assert b["message"] == ("Nothing to ask at your estate from what the game could read, but it could not read everything (providers): "
                                "the picture is not whole, so nothing here counts as complete.")
        assert cl.post("/api/game/boss/ack", json={"scope": "netbox", "site_id": 1}).status_code == 409
        r = cl.post("/api/game/quests/answer", json={"scope": "netbox", "quest_id": "q:spot_check:device:1:0", "step": "same", "choice": "yes"})
        assert r.status_code == 409
