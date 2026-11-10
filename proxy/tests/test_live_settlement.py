"""Against a live dev NetBox 4.7.4 (skipped unless RACKWALKER_LIVE_NETBOX=1: make test-live): what NetBox itself must
agree with. The read of the freshly seeded stack equals the fixture estate; a switch stack, a VRF and a device that
went away behave as documented on real NetBox objects; a read-only token writes nothing and a deleted token ends the
session; a value a colleague recorded meanwhile is not written over. Everything created here is removed afterwards.
"""
from __future__ import annotations

import asyncio
import json
import os

import httpx
import pytest
from fastapi.testclient import TestClient

from rackwalker_proxy import estate, questroutes
from rackwalker_proxy import netbox as nb
from rackwalker_proxy.app import Settings, create_app

from .conftest import LOCAL, REPO, schema_validator

pytestmark = pytest.mark.skipif(os.environ.get("RACKWALKER_LIVE_NETBOX") != "1", reason="live NetBox tests: make test-live")
URL, TOKEN = os.environ.get("NETBOX_URL", ""), os.environ.get("NETBOX_TOKEN", "")
ADMIN = os.environ.get("NETBOX_ADMIN_TOKEN", "")
CSRF = "X-RackWalker-CSRF"


@pytest.fixture(scope="module")
def nbx():
    with httpx.Client(base_url=URL + "/api/", headers={"Authorization": f"Bearer {ADMIN}"}, timeout=30) as c:
        yield c


def _post(nbx, path: str, body: dict) -> dict:
    r = nbx.post(path, json=body)
    assert r.status_code == 201, (path, r.text)
    return r.json()


def _first(nbx, path: str, **params) -> dict:
    return nbx.get(path, params=params).json()["results"][0]


def _port(nbx, device: dict, name: str) -> dict:
    return _first(nbx, "dcim/interfaces/", device_id=device["id"], name=name)


def _cable(nbx, a: dict, b: dict) -> dict:
    return _post(nbx, "dcim/cables/", {"a_terminations": [{"object_type": "dcim.interface", "object_id": a["id"]}],
                                       "b_terminations": [{"object_type": "dcim.interface", "object_id": b["id"]}], "status": "connected"})


def test_the_read_of_the_seeded_netbox_is_the_fixture_estate_live():
    """Every field the scanner maps from NetBox, on real NetBox rows: the seed read through `read_estate` is, object
    for object, what fixtures/estate.json says (the fixture is made from the seed model, not from this read)."""
    async def go() -> estate.Estate:
        client = nb.NetBox(URL, TOKEN)
        try:
            return await estate.read_estate(client)
        finally:
            await client.aclose()
    live = json.loads(json.dumps(asyncio.run(go()).to_json()))
    fixture = json.loads((REPO / "fixtures" / "estate.json").read_text())
    assert live["truncated"] == [] and live["unreadable"] == []

    def norm(table: str, row: dict) -> dict:
        row = {k: v for k, v in row.items() if k != "last_updated"}       # the fixture does not know when NetBox wrote
        if table == "ports" and row["kind"] != "interface":
            row["type"] = None                                             # NetBox gives front, rear and power ports a type of their own
        return row
    for table, rows in fixture.items():
        if not isinstance(rows, list) or table in ("truncated", "unreadable"):
            continue
        key = (lambda r: (r["kind"], r["id"])) if table == "ports" else (lambda r: r["id"])
        got = {key(r): norm(table, r) for r in live[table]}
        for want in rows:
            # other tests may have added objects of their own; every object of the seed must be there, and equal
            assert got.get(key(want)) == norm(table, want), (table, key(want))
    # the timestamps the optimistic lock uses are really read
    assert all(isinstance(r["last_updated"], str) and r["last_updated"] for t in ("sites", "racks", "devices") for r in live[t])


@pytest.fixture(scope="module")
def yard(nbx):
    """A small site: a router cabled to a switch, a second switch with a server on it, addresses with a prefix."""
    made: list[tuple[str, int]] = []

    def make(path: str, body: dict) -> dict:
        obj = _post(nbx, path, body)
        made.append((path, obj["id"]))
        return obj
    roles = {s: _first(nbx, "dcim/device-roles/", slug=s) for s in ("switch", "router", "server")}
    types = {s: _first(nbx, "dcim/device-types/", slug=s) for s in ("ex-a2400", "ex-r400", "ex-s2")}
    site = make("dcim/sites/", {"name": "Stack Yard", "slug": "stack-yard", "status": "active", "latitude": 52.9, "longitude": -1.1,
                                "facility": "SY-1", "custom_fields": {"iso_country": "GB"}})
    rack = make("dcim/racks/", {"name": "SY-R1", "site": site["id"], "status": "active", "u_height": 12, "custom_fields": {"iso_x": 1, "iso_y": 1}})
    devs = {}
    for name, role, dtype, u in (("sy-rtr1", "router", "ex-r400", 10), ("sy-sw1", "switch", "ex-a2400", 8), ("sy-sw1b", "switch", "ex-a2400", 7),
                                 ("sy-srv1", "server", "ex-s2", 4), ("sy-sw9", "switch", "ex-a2400", 2)):
        devs[name] = make("dcim/devices/", {"name": name, "site": site["id"], "rack": rack["id"], "position": u, "face": "front",
                                            "role": roles[role]["id"], "device_type": types[dtype]["id"], "status": "active", "serial": f"SYN-SY-{u:04d}"})
    made.append(("dcim/cables/", _cable(nbx, _port(nbx, devs["sy-rtr1"], "ge-0/0/1"), _port(nbx, devs["sy-sw1"], "Te1/1/1"))["id"]))
    made.append(("dcim/cables/", _cable(nbx, _port(nbx, devs["sy-sw1b"], "Gi1/0/1"), _port(nbx, devs["sy-srv1"], "eth0"))["id"]))
    made.append(("ipam/prefixes/", _post(nbx, "ipam/prefixes/", {"prefix": "10.77.0.0/24", "status": "active"})["id"]))
    for n, (name, port) in enumerate((("sy-rtr1", "ge-0/0/0"), ("sy-sw1", "mgmt0"), ("sy-srv1", "bmc"), ("sy-sw9", "mgmt0")), 1):
        ip = make("ipam/ip-addresses/", {"address": f"10.77.0.{n}/24", "status": "active", "assigned_object_type": "dcim.interface",
                                         "assigned_object_id": _port(nbx, devs[name], port)["id"]})
        assert nbx.patch(f"dcim/devices/{devs[name]['id']}/", json={"primary_ip4": ip["id"]}).status_code == 200
    yield {"site": site, "rack": rack, "devs": devs}
    for vc in nbx.get("dcim/virtual-chassis/", params={"q": "sy-stack"}).json()["results"]:
        nbx.delete(f"dcim/virtual-chassis/{vc['id']}/")
    for vrf in nbx.get("ipam/vrfs/", params={"name": "sy-tenant"}).json()["results"]:
        for path in ("ipam/ip-addresses/", "ipam/prefixes/"):
            for row in nbx.get(path, params={"vrf_id": vrf["id"]}).json()["results"]:
                nbx.delete(f"{path}{row['id']}/")
        nbx.delete(f"ipam/vrfs/{vrf['id']}/")
    for c in nbx.get("dcim/cables/", params={"site_id": site["id"], "limit": 100}).json()["results"]:
        nbx.delete(f"dcim/cables/{c['id']}/")
    for path, oid in reversed(made):
        r = nbx.delete(f"{path}{oid}/")
        assert r.status_code in (204, 404), (path, r.text)


def _client(tmp, token: str, write: bool = True) -> TestClient:
    app = create_app(Settings(URL, TOKEN, write, None, cache_ttl=0, state_dir=tmp, estate_ttl=0))
    cl = TestClient(base_url=LOCAL, app=app)
    r = cl.post("/api/game/session", json={"token": token}, headers={CSRF: "sign-in"})
    assert r.status_code == 200, r.text
    cl.headers[CSRF] = r.json()["csrf_token"]
    return cl


@pytest.fixture
def player(tmp_path, yard, monkeypatch):
    monkeypatch.setattr(questroutes, "FRESH_SITE_S", 0.0)
    monkeypatch.setattr(questroutes, "FRESH_ALL_S", 0.0)
    with _client(tmp_path, ADMIN) as cl:
        yield cl


def _board(cl, site_id: int) -> dict:
    r = cl.get("/api/game/quests", params={"scope": "netbox", "site_id": site_id})
    assert r.status_code == 200, r.text
    schema_validator("QuestBoard").validate(r.json())
    return r.json()


def _say(cl, quest_id: str, step: str, **answer) -> dict:
    r = cl.post("/api/game/quests/answer", json={"scope": "netbox", "quest_id": quest_id, "step": step, **answer})
    assert r.status_code == 200, r.text
    schema_validator("Quest").validate(r.json())
    return r.json()


def _verdict(cl, route: str, quest_id: str) -> dict:
    r = cl.post(f"/api/game/quests/{route}", json={"scope": "netbox", "quest_id": quest_id})
    assert r.status_code == 200, r.text
    schema_validator("QuestVerdict").validate(r.json())
    return r.json()


def test_a_stack_member_raises_no_finding_of_its_own_live(player, yard, nbx):
    site, devs = yard["site"]["id"], yard["devs"]
    member = devs["sy-sw1b"]["id"]
    kinds = {(f["kind"], f["id"]) for f in _board(player, site)["findings"]}
    # two separate switches: the second one, with only a server on it, is cut off, has no uplink and no address
    assert {("site_island", f"site_island:device:{member}"), ("switch_no_uplink", f"switch_no_uplink:device:{member}"),
            ("device_unreachable", f"device_unreachable:site:{site}")} <= kinds
    vc = _post(nbx, "dcim/virtual-chassis/", {"name": "sy-stack"})
    for name, pos in (("sy-sw1", 1), ("sy-sw1b", 2)):
        assert nbx.patch(f"dcim/devices/{devs[name]['id']}/", json={"virtual_chassis": vc["id"], "vc_position": pos}).status_code == 200
    after = _board(player, site)
    assert not [f for f in after["findings"] if f["kind"] in ("site_island", "switch_no_uplink", "device_unreachable")], after["findings"]


def test_an_address_in_a_vrf_gets_no_prefix_in_the_global_table_live(player, yard, nbx):
    site, devs = yard["site"]["id"], yard["devs"]
    vrf = _post(nbx, "ipam/vrfs/", {"name": "sy-tenant"})
    ip = _post(nbx, "ipam/ip-addresses/", {"address": "10.99.9.1/24", "vrf": vrf["id"], "status": "active", "assigned_object_type": "dcim.interface",
                                           "assigned_object_id": _port(nbx, devs["sy-sw1"], "Gi1/0/9")["id"]})
    qid = f"q:address_no_prefix:ip:{ip['id']}"
    line = _say(player, qid, "back")      # the line as it stands, nothing answered (a site lists only a few lines at a time)
    assert line["purpose"].startswith("10.99.9.1/24 is in use at Stack Yard, but NetBox has no prefix in VRF sy-tenant that holds it.")
    assert [c["label"] for c in line["step"]["choices"]][0] == "Yes: I will add it to VRF sy-tenant myself"
    parked = _say(player, qid, "vrf", choice="later")
    assert parked["status"] == "parked" and parked["proposal"] is None and "set its VRF to sy-tenant" in parked["checklist"][0]
    # nothing was written, in either table
    assert nbx.get("ipam/prefixes/", params={"contains": "10.99.9.1"}).json()["count"] == 0
    # the same range in the global table does not settle it; the prefix in the VRF does
    wrong = _post(nbx, "ipam/prefixes/", {"prefix": "10.99.9.0/24", "status": "active"})
    v = _verdict(player, "verify", qid)
    assert (v["verified"], v["xp_awarded"]) == (False, 0)
    nbx.delete(f"ipam/prefixes/{wrong['id']}/")
    _post(nbx, "ipam/prefixes/", {"prefix": "10.99.9.0/24", "vrf": vrf["id"], "status": "active"})
    v = _verdict(player, "verify", qid)
    assert (v["verified"], v["complete"], v["xp_awarded"]) == (True, True, 50)
    # the port that carries the address is expected to have a cable; said as not in use, it counts as answered
    nbx.delete(f"ipam/ip-addresses/{ip['id']}/")


def test_a_line_whose_device_is_switched_off_or_deleted_pays_nothing_live(player, yard, nbx):
    site, devs = yard["site"]["id"], yard["devs"]
    lone = devs["sy-sw9"]["id"]
    qid = f"q:device_unconnected:device:{lone}"
    assert _say(player, qid, "peer", choice="unknown")["status"] == "parked"
    assert nbx.patch(f"dcim/devices/{lone}/", json={"status": "offline"}).status_code == 200
    shown = next(q for q in _board(player, site)["quests"] if q["id"] == qid)
    assert shown["status"] == "ready" and shown["note"].startswith("This no longer applies:")
    v = _verdict(player, "verify", qid)
    assert (v["verified"], v["complete"], v["xp_awarded"], v["unlocked"], v["quest"]["status"]) == (False, False, 0, [], "dismissed")
    assert v["message"].startswith("This no longer applies:") and v["rewards"]["tools"] == [] and v["rewards"]["completed"] == 0
    assert nbx.get("dcim/cables/", params={"device_id": lone}).json()["count"] == 0
    # back in service, still with no cable: the question is asked again, and a real cable is what completes it
    assert nbx.patch(f"dcim/devices/{lone}/", json={"status": "active"}).status_code == 200
    again = next(q for q in _board(player, site)["quests"] if q["id"] == qid)
    assert again["status"] == "offered"
    q = _say(player, qid, "peer", choice=f"device:{devs['sy-sw1']['id']}")
    q = _say(player, qid, "local_port", choice=q["step"]["choices"][0]["id"])
    q = _say(player, qid, "peer_port", choice=q["step"]["choices"][0]["id"])
    q = _say(player, qid, "cable_type", choice="mmf")
    assert q["status"] == "ready"
    # ... but the device is deleted in NetBox before the player presses Apply
    assert nbx.delete(f"dcim/devices/{lone}/").status_code == 204
    # this proxy reads NetBox for every request, so it sees that at once (one that keeps its read for a minute gets
    # NetBox's refusal instead: the serial test below, and the offline settlement tests)
    r = player.post("/api/game/quests/apply", json={"scope": "netbox", "quest_id": qid})
    assert r.status_code == 404 and r.json()["error"]["code"] == "not_found", r.text
    b = _board(player, site)
    assert b["rewards"]["xp"] == 0 and b["rewards"]["tools"] == []          # nothing for a device that went away, twice
    v = _verdict(player, "verify", qid)
    assert (v["verified"], v["complete"], v["xp_awarded"], v["quest"]["status"]) == (False, False, 0, "dismissed")


def test_a_serial_recorded_in_netbox_meanwhile_is_not_written_over_live(tmp_path, yard, nbx):
    site, rack, devs = yard["site"]["id"], yard["rack"]["id"], yard["devs"]
    target = devs["sy-srv1"]["id"]
    assert nbx.patch(f"dcim/devices/{target}/", json={"serial": ""}).status_code == 200
    app = create_app(Settings(URL, TOKEN, True, None, cache_ttl=0, state_dir=tmp_path, estate_ttl=600))      # a kept estate, as in real use
    with TestClient(base_url=LOCAL, app=app) as cl:
        r = cl.post("/api/game/session", json={"token": ADMIN}, headers={CSRF: "sign-in"})
        cl.headers[CSRF] = r.json()["csrf_token"]
        qid = f"q:rack_audit:rack:{rack}"
        q = _say(cl, qid, "labels", values={f"device:{target}:serial": "TYPED-IN-THE-GAME"})
        assert q["status"] == "ready" and q["proposal"]["ops"][0]["if_unmodified_since"]
        assert nbx.patch(f"dcim/devices/{target}/", json={"serial": "REAL-FROM-A-COLLEAGUE"}).status_code == 200
        pre = cl.post("/api/game/quests/preview", json={"scope": "netbox", "quest_id": qid}).json()
        assert pre["ops"][0]["status"] == "error" and pre["ops"][0]["error"]["code"] == "conflict"
        v = _verdict(cl, "apply", qid)
        assert (v["verified"], v["xp_awarded"]) == (False, 0) and "was changed in NetBox after you loaded it" in v["message"]
        assert _first(nbx, "dcim/devices/", id=target)["serial"] == "REAL-FROM-A-COLLEAGUE"
        # NetBox is read again: the serial is there, found by other means; checking confirms it
        v = _verdict(cl, "verify", qid)
        assert (v["complete"], v["message"][:18]) == (True, "NetBox confirms it")


def _player(nbx, name: str, write_enabled: bool) -> tuple[int, int, str]:
    for u in nbx.get("users/users/", params={"username": name}).json()["results"]:
        nbx.delete(f"users/users/{u['id']}/")
    uid = _post(nbx, "users/users/", {"username": name, "password": "Rw-" + os.urandom(12).hex() + "X", "is_active": True})["id"]
    for old in nbx.get("users/permissions/", params={"name": f"{name} sites"}).json()["results"]:
        nbx.delete(f"users/permissions/{old['id']}/")
    _post(nbx, "users/permissions/", {"name": f"{name} sites", "enabled": True, "users": [uid], "object_types": ["dcim.site"], "actions": ["view", "change"]})
    t = _post(nbx, "users/tokens/", {"user": uid, "version": 2, "write_enabled": write_enabled, "description": "token test"})
    return uid, t["id"], t["token"] if t["token"].startswith("nbt_") else f"nbt_{t['key']}.{t['token']}"


def _changes(nbx) -> int:
    return nbx.get("core/object-changes/", params={"limit": 1}).json()["count"]


def test_a_read_only_token_writes_nothing_through_the_game_live(tmp_path, yard, nbx):
    site = yard["site"]["id"]
    uid, _, token = _player(nbx, "token-reader", write_enabled=False)
    try:
        # NetBox itself refuses this token a write ...
        direct = httpx.patch(f"{URL}/api/dcim/sites/{site}/", json={"description": "direct"}, headers={"Authorization": f"Bearer {token}"}, timeout=30)
        assert direct.status_code == 403
        with _client(tmp_path, token) as cl:
            before = _changes(nbx)
            b = _board(cl, site)
            assert b["write_enabled"] and not b["can_write"] and b["write_blocked"].startswith("You signed in with a read-only NetBox token")
            change = {"ops": [{"op": "update_fields", "target": {"kind": "site", "id": site}, "fields": {"description": "through the game"}}]}
            r = cl.post("/api/changes/apply", json=change)
            # ... and so does the game, although the user's permissions and the proxy's token would allow it
            assert r.status_code == 403 and r.json()["error"]["details"] == {"reason": "read_only_token"}
            assert _changes(nbx) == before and _first(nbx, "dcim/sites/", id=site)["description"] == ""
    finally:
        nbx.delete(f"users/users/{uid}/")


def test_a_deleted_token_ends_the_session_before_the_next_write_live(tmp_path, yard, nbx):
    site = yard["site"]["id"]
    uid, token_id, token = _player(nbx, "token-writer", write_enabled=True)
    try:
        with _client(tmp_path, token) as cl:
            change = {"ops": [{"op": "update_fields", "target": {"kind": "site", "id": site}, "fields": {"description": "while the token lived"}}]}
            r = cl.post("/api/changes/apply", json=change)
            assert r.status_code == 200 and r.json()["applied"], r.text
            assert nbx.delete(f"users/tokens/{token_id}/").status_code == 204
            before = _changes(nbx)
            change["ops"][0]["fields"]["description"] = "after the token was deleted"
            r = cl.post("/api/changes/apply", json=change)
            assert r.status_code == 401 and r.json()["error"]["details"] == {"reason": "token_gone"}, r.text
            assert _changes(nbx) == before and _first(nbx, "dcim/sites/", id=site)["description"] == "while the token lived"
            assert cl.get("/api/game/session").json()["signed_in"] is False
    finally:
        nbx.delete(f"users/users/{uid}/")
        nbx.patch(f"dcim/sites/{site}/", json={"description": ""})
