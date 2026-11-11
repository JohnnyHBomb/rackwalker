"""Quests against a live dev NetBox 4.7.4. Skipped unless RACKWALKER_LIVE_NETBOX=1 (make test-live).

The estate under test is built here, next to the seed: one site ("Harbour Office") with three switches, an internet
provider and two rooms with a rack each, and nothing recorded between any of them. The tests play its quest lines
through the proxy to cables NetBox itself can trace, check who NetBox's changelog names, and prove that a player
without the NetBox permission, and the practice island, write nothing. Everything created here is removed afterwards.
"""
from __future__ import annotations

import os

import httpx
import pytest
from fastapi.testclient import TestClient

from rackwalker_proxy import questroutes
from rackwalker_proxy.app import Settings, create_app

from .conftest import LOCAL, schema_validator

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


@pytest.fixture(scope="module")
def harbour(nbx):
    """Three switches, an ISP, two rooms with a rack each; nothing interconnected. Removed again afterwards, and
    before (here, and before the first live test of a run: conftest.py): a run that was cut short leaves it behind."""
    clear_harbour(nbx)

    def make(path: str, body: dict) -> dict:
        return _post(nbx, path, body)

    role, dtype = _first(nbx, "dcim/device-roles/", slug="switch"), _first(nbx, "dcim/device-types/", slug="ex-a2400")
    site = make("dcim/sites/", {"name": "Harbour Office", "slug": "harbour-office", "status": "active", "latitude": 53.4, "longitude": -3.0,
                                "custom_fields": {"iso_country": "GB"}})
    rooms = {n: make("dcim/locations/", {"name": n, "slug": n.lower().replace(" ", "-") + "-ho", "site": site["id"], "status": "active"})
             for n in ("Quay Room", "Loft")}
    racks = {n: make("dcim/racks/", {"name": n, "site": site["id"], "location": rooms[room]["id"], "status": "active", "u_height": 12,
                                     "custom_fields": {"iso_x": 1, "iso_y": 1}})
             for n, room in (("HO-Q1", "Quay Room"), ("HO-L1", "Loft"))}
    devs = {}
    for i, (name, room, rack, u) in enumerate((("sw-quay-1", "Quay Room", "HO-Q1", 10), ("sw-quay-2", "Quay Room", "HO-Q1", 8),
                                               ("sw-loft-1", "Loft", "HO-L1", 10)), 1):
        devs[name] = make("dcim/devices/", {"name": name, "site": site["id"], "location": rooms[room]["id"], "rack": racks[rack]["id"],
                                            "position": u, "face": "front", "role": role["id"], "device_type": dtype["id"],
                                            "status": "active", "serial": f"SYN-HO-{i:04d}"})
    provider = make("circuits/providers/", {"name": "Example Fibre", "slug": "example-fibre"})
    ctype = make("circuits/circuit-types/", {"name": "Internet", "slug": "internet"})
    yield {"site": site, "rooms": rooms, "racks": racks, "devs": devs, "provider": provider, "ctype": ctype}
    clear_harbour(nbx)


def clear_harbour(nbx) -> None:
    """Removes Harbour Office and everything the tests put around it, found by name: what the quests created first
    (cables, circuits with their ends, addresses, the hand-off prefix, the provider's account and network, the
    contacts), then the estate itself."""
    def rows(path: str, *params: tuple[str, object]) -> list[dict]:
        return nbx.get(path, params=[*params, ("limit", 200)]).json()["results"]

    def drop(path: str, *params: tuple[str, object]) -> None:
        for row in rows(path, *params):
            r = nbx.delete(f"{path}{row['id']}/")
            assert r.status_code in (204, 404), (path, r.text)

    sites = rows("dcim/sites/", ("slug", "harbour-office"))
    providers = rows("circuits/providers/", ("slug", "example-fibre"))
    for site in sites:
        devices = [("device_id", d["id"]) for d in rows("dcim/devices/", ("site_id", site["id"]))]
        if devices:
            drop("dcim/cables/", *devices)
            drop("ipam/ip-addresses/", *devices)
    for provider in providers:
        drop("circuits/circuits/", ("provider_id", provider["id"]))
    for name in (CONTACT[0], BILLING[0]):
        for contact in rows("tenancy/contacts/", ("name", name)):
            drop("tenancy/contact-assignments/", ("contact_id", contact["id"]))
        drop("tenancy/contacts/", ("name", name))
    drop("ipam/prefixes/", ("prefix", HANDOFF_PREFIX))
    for provider in providers:
        drop("circuits/provider-accounts/", ("provider_id", provider["id"]))
        drop("circuits/provider-networks/", ("provider_id", provider["id"]))
    for site in sites:
        for path in ("dcim/devices/", "dcim/racks/", "dcim/locations/"):
            drop(path, ("site_id", site["id"]))
    drop("dcim/sites/", ("slug", "harbour-office"))
    drop("circuits/providers/", ("slug", "example-fibre"))
    drop("circuits/circuit-types/", ("slug", "internet"))
    # the model's default the management line wrote goes back to "nothing on record"
    for dtype in rows("dcim/device-types/", ("slug", "ex-a2400")):
        assert nbx.patch(f"dcim/device-types/{dtype['id']}/", json={"custom_fields": {"rw_mgmt_default": None}}).status_code == 200


# made-up figures for the circuit's record (the phone number is from a range reserved for fiction)
HANDOFF, HANDOFF_PREFIX = "198.18.1.2/30", "198.18.1.0/30"
ACCOUNT, CONTACT = "SYN-ACC-7001", ("Example Fibre NOC", "+44 1632 960555")
BILLING = ("Example Fibre Billing", "billing@fibre.example")


def _client(tmp, token: str, write: bool = True, estate_ttl: float = 0) -> TestClient:
    app = create_app(Settings(URL, TOKEN, write, None, cache_ttl=0, state_dir=tmp, estate_ttl=estate_ttl))
    cl = TestClient(base_url=LOCAL, app=app)
    r = cl.post("/api/game/session", json={"token": token}, headers={CSRF: "sign-in"})
    assert r.status_code == 200, r.text
    cl.headers[CSRF] = r.json()["csrf_token"]
    cl.player = r.json()["player"]["username"]
    return cl


@pytest.fixture(scope="module")
def admin(tmp_path_factory, harbour):
    # this player's proxy keeps its read of the estate, as a real one does: after each quest only the sites it changed
    # are read again, and every later test sees the estate through that
    with _client(tmp_path_factory.mktemp("quests-admin"), ADMIN, estate_ttl=600) as cl:
        yield cl


def _board(cl, site_id: int | None = None, scope: str = "netbox") -> dict:
    r = cl.get("/api/game/quests", params={"scope": scope, **({"site_id": site_id} if site_id else {})})
    assert r.status_code == 200, r.text
    schema_validator("QuestBoard").validate(r.json())
    return r.json()


def _answer(cl, quest: dict, picks: dict, scope: str = "netbox") -> dict:
    """Answers a line's questions from `picks` (step id -> a choice's label, or text) until it is ready or stops."""
    for _ in range(12):
        step = quest["step"]
        if quest["status"] not in ("offered", "active") or step is None:
            return quest
        want = picks[step["id"]]
        if step["input"] == "choice":
            body = {"choice": next(c["id"] for c in step["choices"] if c["label"].startswith(want))}
        else:
            body = {"text": want}
        r = cl.post("/api/game/quests/answer", json={"scope": scope, "quest_id": quest["id"], "step": step["id"], **body})
        assert r.status_code == 200, r.text
        quest = r.json()
        schema_validator("Quest").validate(quest)
    raise AssertionError("the line did not come to an end")


def _changelog_count(nbx) -> int:
    return nbx.get("core/object-changes/", params={"limit": 1}).json()["count"]


def test_health_reports_that_the_proxy_can_check_permissions_live(admin):
    assert admin.get("/api/health").json()["netbox"]["permission_check"] == "ok"


def test_the_gap_scanner_lists_a_seeded_site_live(admin):
    r = admin.get("/api/quests/gaps/1", params={"limit": 3})
    assert r.status_code == 200, r.text
    body = r.json()
    schema_validator("GapList").validate(body)
    assert body["total"] >= 1 and len(body["items"]) <= 3 and body["source"] == "netbox"
    assert "site:1:facility" in [g["id"] for g in admin.get("/api/quests/gaps/1").json()["items"]]


def test_the_unconnected_estate_is_found_and_ordered_live(admin, harbour):
    site = harbour["site"]["id"]
    b = _board(admin, site)
    kinds = [f["kind"] for f in b["findings"]]
    assert kinds == ["site_no_way_out", "sites_not_linked", "locations_not_linked", "device_unconnected", "device_unconnected",
                     "device_unconnected", "device_unreachable", "device_unreachable", "device_unreachable", "site_record"]
    # at most a few lines are open at one site, the most valuable first; the rest wait
    assert [q["kind"] for q in b["quests"]] == ["site_no_way_out", "sites_not_linked", "locations_not_linked"]
    assert b["queued"] == 7 and all(q["status"] == "offered" for q in b["quests"])
    # the lines on offer are the first stage of three chains; nothing of a later stage is asked yet
    assert [(c["kind"], c["label"]) for c in b["chains"] if c["site_id"] == site] == [
        ("way_out", "1 of 3: physical"), ("site_link", "1 of 1: physical"), ("room_link", "1 of 1: physical")]
    assert b["setup"] == [] and b["quests"][0]["chain"]["label"] == "1 of 3: physical"
    assert b["quests"][0]["purpose"] == "Nobody can tell from NetBox how Harbour Office reaches the outside world. Let's find out."
    assert b["quests"][2]["purpose"] == ("Loft and Quay Room at Harbour Office have no link between them. If that link fails, nobody knows "
                                         "which cable to check. Let's record it.")
    assert (b["can_write"], b["write_blocked"]) == (True, None)
    assert not next(s for s in b["sites"] if s["site_id"] == site)["boss_ready"]


def test_the_way_out_line_ends_in_a_trace_netbox_confirms_live(admin, harbour, nbx):
    site = harbour["site"]["id"]
    quest = _board(admin, site)["quests"][0]
    assert quest["step"]["prompt"] == "Which device at Harbour Office does the line to the outside plug into?"
    assert [c["label"] for c in quest["step"]["choices"]] == ["sw-loft-1 (switch)", "sw-quay-1 (switch)", "sw-quay-2 (switch)",
                                                              "A device that is not in NetBox yet",
                                                              "None: this site has no line out, on purpose", "I don't know yet"]
    quest = _answer(admin, quest, {"device": "sw-quay-1", "provider": "Example Fibre", "circuit_id": "EXF-20417", "circuit_type": "Internet",
                                   "local_port": "Te1/1/2", "cable_type": "Single-mode"})
    assert quest["status"] == "ready"
    assert [o["op"] for o in quest["proposal"]["ops"]] == ["create_circuit", "create_circuit_termination", "connect_cable"]
    ref = {"scope": "netbox", "quest_id": quest["id"]}
    pre = admin.post("/api/game/quests/preview", json=ref)
    assert pre.status_code == 200, pre.text
    schema_validator("ChangeResult").validate(pre.json())
    assert [o["status"] for o in pre.json()["ops"]] == ["ok", "ok", "ok"] and not pre.json()["applied"]
    before = _changelog_count(nbx)
    r = admin.post("/api/game/quests/apply", json=ref)
    assert r.status_code == 200, r.text
    v = r.json()
    schema_validator("QuestVerdict").validate(v)
    assert (v["mode"], v["verified"], v["complete"], v["quest"]["status"]) == ("real", True, True, "done")
    assert v["xp_awarded"] == 120 and v["unlocked"] == ["cable_tracer"]
    assert v["can_now"] == "You can now see in NetBox where the line to the outside comes into Harbour Office, and trace it."
    # the circuit that is now plugged in brings the next stage of the chain
    assert v["message"] == 'NetBox confirms it. Next in the chain "The way out of Harbour Office": 2 of 3: logical.'
    assert v["chain"]["summary"] == "Harbour Office: sw-quay-1 Te1/1/2 -> circuit EXF-20417 (Internet), Example Fibre."
    assert v["trace"] == "NetBox's cable trace from sw-quay-1 Te1/1/2 reaches circuit EXF-20417 (side A) in 1 hop."
    # NetBox itself: the circuit exists, ends at the site, and the switch port traces to it
    circuit = _first(nbx, "circuits/circuits/", cid="EXF-20417")
    term = _first(nbx, "circuits/circuit-terminations/", circuit_id=circuit["id"])
    assert (term["term_side"], term["termination_id"]) == ("A", site)
    port = _first(nbx, "dcim/interfaces/", device_id=harbour["devs"]["sw-quay-1"]["id"], name="Te1/1/2")
    trace = nbx.get(f"dcim/interfaces/{port['id']}/trace/").json()
    assert trace[0][2][0]["id"] == term["id"] and trace[0][1]["type"] == "smf"
    # the changelog names the player and the quest
    assert _changelog_count(nbx) > before
    change = _first(nbx, "core/object-changes/", changed_object_type="dcim.cable", changed_object_id=trace[0][1]["id"])
    assert change["message"] == f"rackwalker ({admin.player}): quest: The way out of Harbour Office"


def test_i_dont_know_yet_parks_the_line_and_it_resumes_where_it_stopped_live(admin, harbour):
    site = harbour["site"]["id"]
    quest = next(q for q in _board(admin, site)["quests"] if q["kind"] == "locations_not_linked")
    quest = _answer(admin, quest, {"peer": "sw-quay-2", "local_port": "I don't know yet"})
    assert quest["status"] == "parked" and quest["step"]["id"] == "local_port"
    assert quest["checklist"][0].startswith("Find sw-loft-1: rack HO-L1 U10 in Loft")
    again = next(q for q in _board(admin, site)["quests"] if q["id"] == quest["id"])
    assert again["status"] == "parked" and again["step"]["id"] == "local_port" and again["said"][0]["answer"] == "sw-quay-2 (switch)"
    # parked costs nothing and does not block the next line
    assert _board(admin, site)["rewards"]["xp"] == 120
    quest = _answer(admin, {**again, "status": "active"}, {"local_port": "Te1/1/1", "peer_port": "Te1/1/1", "cable_type": "Not sure"})
    assert quest["status"] == "ready" and "type" not in quest["proposal"]["ops"][0]["fields"]
    v = admin.post("/api/game/quests/apply", json={"scope": "netbox", "quest_id": quest["id"]}).json()
    assert (v["verified"], v["complete"]) == (True, True)
    assert v["trace"] == "NetBox's cable trace from sw-loft-1 Te1/1/1 reaches sw-quay-2 Te1/1/1 in 1 hop."
    assert v["can_now"] == "You can now trace from Loft to Quay Room."
    assert v["xp_awarded"] == 120 and v["unlocked"] == []


def test_a_repeated_kind_of_fix_pays_less_and_the_site_fills_up_live(admin, harbour):
    site = harbour["site"]["id"]
    b = _board(admin, site)
    # sw-quay-2 and sw-loft-1 are linked now and sw-quay-1 has the circuit: no device is unconnected any more, but
    # nothing joins sw-quay-1 (and with it the way out) to the other two
    offered = {q["kind"]: q for q in b["quests"] if q["status"] == "offered"}
    # the way out has been begun, so its next stage (the address on the circuit) is carried on first
    assert list(offered) == ["circuit_handoff", "sites_not_linked", "site_island"] and b["next_quest_id"] == offered["circuit_handoff"]["id"]
    quest = _answer(admin, offered["site_island"], {"peer": "sw-loft-1", "local_port": "Te1/1/1", "peer_port": "Te1/1/2", "cable_type": "Multimode"})
    assert quest["said"][0]["question"] == "What does sw-quay-1 connect to in the rest of Harbour Office?"
    v = admin.post("/api/game/quests/apply", json={"scope": "netbox", "quest_id": quest["id"]}).json()
    assert (v["verified"], v["complete"], v["xp_awarded"]) == (True, True, 120)
    # "there is no link, on purpose" is an answer too: nothing is written, and the site no longer counts it as open
    open_before = next(s for s in _board(admin, site)["sites"] if s["site_id"] == site)["completeness"]["open"]
    gone = _answer(admin, offered["sites_not_linked"], {"which": "None: there is no link"})
    assert gone["status"] == "dismissed"
    after = _board(admin, site)
    assert next(s for s in after["sites"] if s["site_id"] == site)["completeness"]["open"] == open_before - 1
    assert sorted(q["status"] for q in after["quests"]) == ["dismissed", "done", "done", "done", "offered", "offered", "offered"]
    # every switch is now plugged into something: how each is managed (the logical stage of its chain) has unlocked
    assert [q["kind"] for q in after["quests"] if q["status"] == "offered"] == ["circuit_handoff", "device_unreachable", "site_record"]
    assert after["rewards"]["xp"] == 360 and after["rewards"]["completed"] == 3 and after["rewards"]["tools"] == ["cable_tracer"]


def _player(nbx, name: str, perms: list[dict]) -> tuple[int, str]:
    for u in nbx.get("users/users/", params={"username": name}).json()["results"]:
        nbx.delete(f"users/users/{u['id']}/")
    uid = _post(nbx, "users/users/", {"username": name, "password": "Rw-" + os.urandom(12).hex() + "X", "is_active": True})["id"]
    for i, p in enumerate(perms):
        nbx.delete  # noqa: B018
        for old in nbx.get("users/permissions/", params={"name": f"{name} {i}"}).json()["results"]:
            nbx.delete(f"users/permissions/{old['id']}/")
        _post(nbx, "users/permissions/", {"name": f"{name} {i}", "enabled": True, "users": [uid], **p})
    t = _post(nbx, "users/tokens/", {"user": uid, "version": 2, "write_enabled": True, "description": "quest test"})
    return uid, t["token"] if t["token"].startswith("nbt_") else f"nbt_{t['key']}.{t['token']}"


def _line(cl, quest_id: str) -> dict:
    """A line as it stands, whether or not the quest log has it on offer just now."""
    r = cl.post("/api/game/quests/answer", json={"scope": "netbox", "quest_id": quest_id, "step": "back"})
    assert r.status_code == 200, r.text
    return r.json()


def _apply(cl, quest_id: str) -> dict:
    r = cl.post("/api/game/quests/apply", json={"scope": "netbox", "quest_id": quest_id})
    assert r.status_code == 200, r.text
    schema_validator("QuestVerdict").validate(r.json())
    return r.json()


def test_how_each_device_is_managed_is_asked_before_any_address_and_written_to_netbox_live(admin, harbour, nbx, monkeypatch):
    from rackwalker_proxy import questroutes

    monkeypatch.setattr(questroutes, "FRESH_SITE_S", 0.0)
    site = harbour["site"]["id"]
    ids = [harbour["devs"][n]["id"] for n in ("sw-quay-1", "sw-quay-2", "sw-loft-1")]

    def still_open() -> tuple[int, int]:
        """(open checks as this player's quest log counts them, open checks as the scanner reads them from NetBox)."""
        mine = next(s for s in _board(admin, site)["sites"] if s["site_id"] == site)["completeness"]["open"]
        return mine, admin.get(f"/api/quests/gaps/{site}").json()["completeness"]["open"]

    def cable_gaps() -> list[dict]:
        return [g for g in admin.get(f"/api/quests/gaps/{site}", params={"limit": 500}).json()["items"] if g["field"] == "cable"]
    assert still_open()[0] == still_open()[1] - 1          # "no link to the other sites, on purpose" was said earlier
    # a cloud-managed switch has no address to give: the service is recorded, and NetBox itself then says it is complete.
    # The model can be either, so the answer is this device's own and the model is marked as varying.
    quest = _line(admin, f"q:device_unreachable:device:{ids[1]}")
    assert quest["purpose"].startswith("NetBox does not say how sw-quay-2 is managed, or how to reach it.")
    assert quest["step"]["prompt"] == "How is sw-quay-2 (EX-A2400) managed?" and quest["chain"]["label"] == "2 of 2: logical"
    quest = _answer(admin, quest, {f"mode_{ids[1]}": "By a cloud", f"service_{ids[1]}": "A service not listed", f"service_name_{ids[1]}": "Example Cloud Portal",
                                   f"model_{ids[1]}": "No: it differs"})
    assert quest["status"] == "ready" and quest["changes"] == [
        "sw-quay-2: managed by a cloud or controller service (Example Cloud Portal). The EX-A2400 model is marked as varying from device to device."]
    v = _apply(admin, quest["id"])
    assert (v["mode"], v["verified"], v["complete"], v["xp_awarded"]) == ("real", True, True, 40)
    cf = _first(nbx, "dcim/devices/", id=ids[1])["custom_fields"]
    assert (cf["rw_mgmt_mode"]["value"], cf["rw_mgmt_service"]) == ("cloud", "Example Cloud Portal")
    assert _first(nbx, "dcim/device-types/", slug="ex-a2400")["custom_fields"]["rw_mgmt_default"]["value"] == "varies"
    # locally managed, with no management port of its own in use: the player names the front-panel port
    quest = _line(admin, f"q:device_unreachable:device:{ids[0]}")
    quest = _answer(admin, quest, {f"mode_{ids[0]}": "Locally", f"ip_{ids[0]}": "An address not in NetBox yet",
                                   f"addr_{ids[0]}": "192.0.2.61/24", f"port_{ids[0]}": "Gi1/0/1"})
    assert quest["status"] == "ready" and [o["op"] for o in quest["proposal"]["ops"]] == ["set_management", "assign_ip"]
    v = _apply(admin, quest["id"])
    assert (v["verified"], v["complete"], v["xp_awarded"]) == (True, True, 32)          # the same kind of fix a second time at this site
    dev = _first(nbx, "dcim/devices/", id=ids[0])
    assert dev["primary_ip4"]["address"] == "192.0.2.61/24" and dev["custom_fields"]["rw_mgmt_mode"]["value"] == "local"
    # Gi1/0/1 carries an address and has no cable, yet is not listed as an uncabled port: it is the management address
    assert not cable_gaps()
    gaps = [g["id"] for g in admin.get(f"/api/quests/gaps/{site}", params={"limit": 500}).json()["items"] if g["field"] == "primary_ip"]
    assert gaps == [f"device:{ids[2]}:primary_ip"]                                # only the third switch is still unknown
    assert still_open()[0] == still_open()[1] - 1                                 # what NetBox holds needs no per-player answer
    # "I don't know yet" parks the third one; it costs nothing
    quest = _answer(admin, _line(admin, f"q:device_unreachable:device:{ids[2]}"), {f"mode_{ids[2]}": "I don't know yet"})
    assert quest["status"] == "parked" and quest["checklist"][0].startswith("Ask whoever looks after sw-loft-1 how they manage it")
    # Somebody then records in NetBox itself that the model is managed locally by default. The third switch has "local"
    # from its model: the line asks for its address only, and writes nothing about the mode.
    dtype = _first(nbx, "dcim/device-types/", slug="ex-a2400")
    assert nbx.patch(f"dcim/device-types/{dtype['id']}/", json={"custom_fields": {"rw_mgmt_default": "local"}}).status_code == 200
    assert admin.get("/api/game/quests", params={"scope": "netbox", "site_id": site, "fresh": "true"}).status_code == 200
    quest = _line(admin, f"q:device_unreachable:device:{ids[2]}")
    assert quest["purpose"].startswith("sw-loft-1 is managed locally, but NetBox has no address to log in to it.")
    assert quest["step"]["prompt"] == "Which address do you log in to sw-loft-1 on?"
    # the host of sw-quay-1 under another mask is a duplicate to NetBox: the preview says so, before anything is written
    quest = _answer(admin, quest, {f"ip_{ids[2]}": "An address not in NetBox yet", f"addr_{ids[2]}": "192.0.2.61/25", f"port_{ids[2]}": "Gi1/0/3"})
    assert quest["status"] == "ready" and [o["op"] for o in quest["proposal"]["ops"]] == ["assign_ip"]
    pre = admin.post("/api/game/quests/preview", json={"scope": "netbox", "quest_id": quest["id"]}).json()
    assert pre["ops"][0]["status"] == "error" and pre["ops"][0]["error"]["code"] == "conflict", pre
    assert "192.0.2.61 is already in NetBox as 192.0.2.61/24" in pre["ops"][0]["error"]["message"]
    quest = _say(admin, quest["id"], (f"addr_{ids[2]}", {"text": "192.0.2.63/24"}))
    assert quest["status"] == "ready" and quest["changes"] == ["sw-loft-1: 192.0.2.63/24 on Gi1/0/3, as its management address (primary IP)."]
    v = _apply(admin, quest["id"])
    assert (v["verified"], v["complete"], v["xp_awarded"]) == (True, True, 24)
    dev = _first(nbx, "dcim/devices/", id=ids[2])
    assert dev["primary_ip4"]["address"] == "192.0.2.63/24" and dev["custom_fields"]["rw_mgmt_mode"] is None
    # sw-loft-1 is cabled, and Gi1/0/3 carries an address and no cable: with "local" from the model's default it is still
    # the management address, not a port in use with no far end
    assert not cable_gaps()
    b = _board(admin, site)
    assert "interface_no_cable" not in [f["kind"] for f in b["findings"]] + [q["kind"] for q in b["quests"]]
    assert not [g for g in admin.get(f"/api/quests/gaps/{site}", params={"limit": 500}).json()["items"] if g["field"] == "primary_ip"]


def _say(cl, quest_id: str, *answers: tuple[str, dict]) -> dict:
    for step, body in answers:
        r = cl.post("/api/game/quests/answer", json={"scope": "netbox", "quest_id": quest_id, "step": step, **body})
        assert r.status_code == 200, r.text
        schema_validator("Quest").validate(r.json())
    return r.json()


def test_the_way_out_is_played_as_a_whole_chain_to_the_complete_record_live(admin, harbour, nbx):
    site = harbour["site"]["id"]
    circuit = _first(nbx, "circuits/circuits/", cid="EXF-20417")
    hand, rec = f"q:circuit_handoff:circuit:{circuit['id']}", f"q:circuit_record:circuit:{circuit['id']}"
    chain = next(c for c in _board(admin, site)["chains"] if c["kind"] == "way_out")
    assert chain["label"] == "2 of 3: logical" and [s["status"] for s in chain["stages"]] == ["done", "open", "locked"]
    # ---- logical: the address on the port the circuit plugs into, and its prefix
    q = _say(admin, hand, ("has", {"choice": "typed"}), ("addr", {"text": HANDOFF}), ("prefix", {"choice": "yes"}))
    assert q["status"] == "ready" and q["changes"] == [f"sw-quay-1: {HANDOFF} on Te1/1/2.", f"New prefix {HANDOFF_PREFIX}."]
    v = _apply(admin, hand)
    assert (v["verified"], v["complete"], v["xp_awarded"], v["chain"]["label"]) == (True, True, 60, "3 of 3: administrative")
    port = _first(nbx, "dcim/interfaces/", device_id=harbour["devs"]["sw-quay-1"]["id"], name="Te1/1/2")
    assert [ip["address"] for ip in nbx.get("ipam/ip-addresses/", params={"interface_id": port["id"]}).json()["results"]] == [HANDOFF]
    assert _first(nbx, "dcim/devices/", id=harbour["devs"]["sw-quay-1"]["id"])["primary_ip4"]["address"] == "192.0.2.61/24"   # not replaced
    # ---- administrative: NetBox's own models, each fact "if known"; the cost is not known yet
    first = _line(admin, rec)
    assert first["chain"]["label"] == "3 of 3: administrative" and first["step"]["id"] == "network"
    q = _say(admin, rec, ("network", {"choice": "new"}), ("network_name", {"text": "Example Fibre Internet"}),
             ("account", {"choice": "new"}), ("account_no", {"text": ACCOUNT}), ("contact_for", {"choice": "provider"}),
             ("contact", {"values": {"contact:support:name": CONTACT[0], "contact:support:phone": CONTACT[1]}}),
             ("terms", {"values": {"install_date": "2025-06-02", "commit_rate": 200}}), ("cost", {"values": {"nrc": 0, "currency": "gbp"}}))
    assert q["status"] == "ready"
    assert [o["op"] for o in q["proposal"]["ops"]] == ["create_provider_network", "create_circuit_termination", "create_provider_account",
                                                       "create_contact", "assign_contact", "record_circuit"]
    pre = admin.post("/api/game/quests/preview", json={"scope": "netbox", "quest_id": rec})
    assert pre.status_code == 200 and [o["status"] for o in pre.json()["ops"]] == ["ok"] * 6, pre.text
    v = _apply(admin, rec)
    assert (v["mode"], v["verified"], v["complete"], v["quest"]["status"], v["chain_xp"]) == ("real", True, False, "parked", 0)
    assert v["chain"]["status"] == "open_questions"
    # the fields left blank (two of the three contacts, the monthly charge) are "not known yet" at once: the line waits
    # at the first of them and does not ask for them a second time
    assert v["chain"]["label"] == "3 of 3: administrative (not known yet: the account manager, the billing contact and the monthly charge)"
    assert v["quest"]["step"]["id"] == "contact" and v["quest"]["checklist"]
    assert [f["id"] for f in v["quest"]["step"]["fields"]][::4] == ["contact:account:name", "contact:billing:name"]
    # NetBox itself holds every fact where its own model has a place for it
    live = _first(nbx, "circuits/circuits/", id=circuit["id"])
    account = _first(nbx, "circuits/provider-accounts/", provider_id=harbour["provider"]["id"])
    assert (live["provider_account"]["id"], account["account"], live["install_date"], live["commit_rate"]) == (account["id"], ACCOUNT, "2025-06-02", 200000)
    network = _first(nbx, "circuits/provider-networks/", provider_id=harbour["provider"]["id"])
    z = _first(nbx, "circuits/circuit-terminations/", circuit_id=circuit["id"], term_side="Z")
    assert (network["name"], z["termination_type"], z["termination_id"]) == ("Example Fibre Internet", "circuits.providernetwork", network["id"])
    contact = _first(nbx, "tenancy/contacts/", name=CONTACT[0])
    assigned = _first(nbx, "tenancy/contact-assignments/", contact_id=contact["id"])
    assert (contact["phone"], assigned["object_type"], assigned["object_id"], assigned["role"]["slug"]) == (
        CONTACT[1], "circuits.provider", harbour["provider"]["id"], "support")
    assert live["custom_fields"]["rw_mrc"] is None and (float(live["custom_fields"]["rw_nrc"]), live["custom_fields"]["rw_currency"]) == (0.0, "GBP")
    # the changelog names the player on the circuit's record as on a cable
    change = _first(nbx, "core/object-changes/", changed_object_type="circuits.circuit", changed_object_id=circuit["id"])
    assert change["message"] == f"rackwalker ({admin.player}): quest: The record of EXF-20417"
    # ---- "cost not known" leaves the chain honestly open and does not count against the site
    b = _board(admin, site)
    chain = next(c for c in b["chains"] if c["kind"] == "way_out")
    assert chain["open_questions"] == ["the account manager", "the billing contact", "the monthly charge"] and chain["stages"][2]["status"] == "not_known"
    assert chain["bonus_xp"] == 250 and chain["holds"].endswith("the carrier's network, your account, who to call, and what it costs")
    mine = next(s for s in b["sites"] if s["site_id"] == site)["completeness"]["open"]
    scanned = admin.get(f"/api/quests/gaps/{site}").json()["completeness"]["open"]
    assert mine == scanned - 1 - 3                         # "no link, on purpose" from before, and the three facts not known yet
    # ---- the charges go to the custom fields, and the whole record is read back from NetBox
    # ---- the bill turns up: the same desk is the account manager too (one contact in NetBox, a second role)
    q = _say(admin, rec, ("contact", {"values": {"contact:account:name": CONTACT[0].lower(), "contact:billing:name": BILLING[0], "contact:billing:email": BILLING[1]}}))
    # a name NetBox already has is not taken over in silence: the player says that this contact is the one they mean
    assert q["step"]["id"].startswith("contact_is_") and q["step"]["prompt"].startswith(f"NetBox already has a contact called {CONTACT[0]}, {CONTACT[1]}. Is that who you mean?")
    q = _say(admin, rec, (q["step"]["id"], {"choice": "yes"}), ("cost", {"values": {"mrc": 245.5}}))
    assert q["status"] == "ready" and q["changes"][-1] == "Circuit EXF-20417: 245.50 GBP a month."
    assert [o["op"] for o in q["proposal"]["ops"]] == ["assign_contact", "create_contact", "assign_contact", "record_circuit"]
    v = _apply(admin, rec)
    assert (v["verified"], v["complete"], v["chain"]["status"], v["chain"]["label"]) == (True, True, "complete", "3 of 3: complete")
    assert v["chain_xp"] == 250 and v["xp_awarded"] > v["chain_xp"]            # every stage of the chain was this player's
    cf = _first(nbx, "circuits/circuits/", id=circuit["id"])["custom_fields"]
    assert (float(cf["rw_mrc"]), float(cf["rw_nrc"]), cf["rw_currency"]) == (245.5, 0.0, "GBP")
    assert v["chain"]["summary"] == (
        f"Harbour Office: sw-quay-1 Te1/1/2 -> circuit EXF-20417 (Internet), Example Fibre, network Example Fibre Internet, your address {HANDOFF}, "
        f"account {ACCOUNT}, Support / NOC: {CONTACT[0]} {CONTACT[1]}, Billing: {BILLING[0]} {BILLING[1]}, 245.50 GBP a month, 0.00 GBP once, installed 2025-06-02, 200 Mbps committed.")
    assert v["message"].endswith(v["chain"]["summary"])
    # nothing of those figures was kept by the game: its store holds the line, not the numbers
    disk = b"".join(p.read_bytes() for p in admin.app.state.settings.state_dir.rglob("*") if p.is_file())
    assert disk and not [x for x in (ACCOUNT, CONTACT[1], BILLING[1], "245.5") if x.encode() in disk]
    roles = sorted(a["role"]["slug"] for a in nbx.get("tenancy/contact-assignments/", params={"object_type": "circuits.provider",
                                                                                              "object_id": harbour["provider"]["id"]}).json()["results"])
    assert roles == ["account-manager", "billing", "support"] and nbx.get("tenancy/contacts/", params={"name": CONTACT[0]}).json()["count"] == 1


def test_the_kept_estate_matches_a_whole_new_read_of_netbox_live(admin, harbour, monkeypatch):
    """Every write above was read back one site at a time into the kept estate. A whole new read must say the same."""
    kept = _board(admin)
    monkeypatch.setattr(questroutes, "FRESH_ALL_S", 0.0)
    r = admin.get("/api/game/quests", params={"scope": "netbox", "fresh": "true"})
    assert r.status_code == 200, r.text
    whole = r.json()
    for key in ("findings", "sites", "quests", "queued", "incomplete", "message"):
        assert kept[key] == whole[key], key
    gaps = admin.get(f"/api/quests/gaps/{harbour['site']['id']}").json()
    assert gaps["source"] == "cache" and gaps["completeness"]["total"] > 0


def test_a_fix_deleted_in_netbox_reopens_nothing_and_is_counted_live(admin, harbour, nbx, monkeypatch):
    site = harbour["site"]["id"]
    line = next(q for q in _board(admin, site)["quests"] if q["kind"] == "locations_not_linked")
    assert line["status"] == "done" and not line["lapsed"]
    xp = _board(admin)["rewards"]["xp"]
    for name in ("Te1/1/1", "Te1/1/2"):                                               # someone removes sw-loft-1's cables in NetBox
        port = _first(nbx, "dcim/interfaces/", device_id=harbour["devs"]["sw-loft-1"]["id"], name=name)
        assert nbx.delete(f"dcim/cables/{port['cable']['id']}/").status_code == 204
    monkeypatch.setattr(questroutes, "FRESH_SITE_S", 0.0)
    r = admin.get("/api/game/quests", params={"scope": "netbox", "site_id": site, "fresh": "true"})    # "Read NetBox again"
    assert r.status_code == 200, r.text
    b = r.json()
    schema_validator("QuestBoard").validate(b)
    again = next(q for q in b["quests"] if q["id"] == line["id"])
    assert (again["status"], again["lapsed"], again["step"]) == ("done", True, None)
    assert b["rewards"]["xp"] == xp and b["rewards"]["lapsed"] == 1
    assert not [q for q in b["quests"] if q["kind"] == "locations_not_linked" and q["status"] != "done"]
    # what NetBox does not know now is asked as lines of their own: the switches that lost their only cable
    assert "device_unconnected" in [q["kind"] for q in b["quests"] if q["status"] == "offered"]
    assert {"device_unconnected:device:%d" % harbour["devs"][n]["id"] for n in ("sw-loft-1", "sw-quay-2")} <= {f["id"] for f in b["findings"]}


def test_health_reports_a_proxy_token_that_cannot_read_permissions_live(tmp_path, nbx):
    uid, token = _player(nbx, "quest-blind-proxy", [VIEW_ALL])
    try:
        app = create_app(Settings(URL, token, False, None, cache_ttl=0, state_dir=tmp_path))
        with TestClient(base_url=LOCAL, app=app) as cl:
            assert cl.get("/api/health").json()["netbox"]["permission_check"] == "cannot_view_users"
    finally:
        nbx.delete(f"users/users/{uid}/")


VIEW_ALL = {"object_types": ["dcim.site", "dcim.device", "dcim.rack"], "actions": ["view"]}


@pytest.mark.parametrize("name,extra,allowed", [
    ("quest-viewer", [], False),
    ("quest-constrained", [{"object_types": ["dcim.site"], "actions": ["change"], "constraints": {"slug": "north-depot"}}], False),
    ("quest-editor", [{"object_types": ["dcim.site"], "actions": ["change"]}], True)])
def test_a_write_needs_the_players_own_netbox_permission_live(tmp_path, nbx, harbour, name, extra, allowed):
    uid, token = _player(nbx, name, [VIEW_ALL] + extra)
    try:
        with _client(tmp_path, token) as cl:
            before = _changelog_count(nbx)
            change = {"ops": [{"op": "update_fields", "target": {"kind": "site", "id": harbour["site"]["id"]},
                               "fields": {"description": f"seen by {name}"}}], "reason": "permission check"}
            r = cl.post("/api/changes/apply", json=change)
            if allowed:
                assert r.status_code == 200 and r.json()["applied"], r.text
                assert _changelog_count(nbx) == before + 1
                return
            assert r.status_code == 403, r.text
            err = r.json()["error"]
            assert (err["code"], err["details"]["reason"]) == ("forbidden", "player_lacks_permission")
            assert err["details"]["missing"] == [{"object_type": "dcim.site", "action": "change", "constrained": bool(extra)}]
            assert "you can still play in practice mode" in err["message"]
            # the same player on a quest line: the change is shown as a practice run, nothing is written, no XP
            b = _board(cl, harbour["site"]["id"])
            # the quest log says it before the first answer, not after the last
            assert b["write_enabled"] and not b["can_write"]
            assert b["write_blocked"].startswith("Your NetBox user may not change anything a quest writes")
            line = {"id": f"q:site_record:site:{harbour['site']['id']}"}
            form = {"scope": "netbox", "quest_id": line["id"], "step": "record", "values": {"facility": "HBR-1"}}
            quest = cl.post("/api/game/quests/answer", json=form).json()
            assert quest["status"] == "ready" and quest["write_blocked"].startswith("Your NetBox user may not change dcim.site.")
            v = cl.post("/api/game/quests/apply", json={"scope": "netbox", "quest_id": line["id"]}).json()
            assert (v["mode"], v["verified"], v["xp_awarded"], v["quest"]["status"]) == ("dry_run", False, 0, "practised")
            assert v["message"].startswith("Practice run, nothing was written: Your NetBox user may not change dcim.site. Ask a NetBox admin")
            assert v["message"].lower().count("nothing was written") == 1 and v["message"].endswith("No XP for a practice run.")
            assert _changelog_count(nbx) == before and _board(cl)["rewards"]["xp"] == 0
            # the practice run threw nothing away: the line still has the answer and its change
            kept = next(q for q in _board(cl, harbour["site"]["id"])["quests"] if q["id"] == line["id"])   # a practised line waits in the log
            assert kept["status"] == "practised" and kept["said"] and kept["proposal"]["ops"][0]["fields"] == {"facility": "HBR-1"}
    finally:
        nbx.delete(f"users/users/{uid}/")
        if allowed:
            nbx.patch(f"dcim/sites/{harbour['site']['id']}/", json={"description": ""})


def test_the_practice_island_never_writes_to_netbox_live(admin, nbx):
    before = _changelog_count(nbx)
    admin.post("/api/game/practice/reset")
    b = _board(admin, scope="practice")
    assert b["sites"][0]["name"] == "Practice Island" and not b["can_write"]
    quest = _answer(admin, b["quests"][0], {"device": "sw-quay-1", "provider": "Example Fibre", "circuit_id": "EXF-1", "circuit_type": "Internet", "local_port": "Te1/1/1",
                                            "cable_type": "Copper"}, scope="practice")
    v = admin.post("/api/game/quests/apply", json={"scope": "practice", "quest_id": quest["id"]}).json()
    assert (v["mode"], v["complete"], v["xp_awarded"]) == ("practice", True, 0)
    assert _changelog_count(nbx) == before
    assert nbx.get("circuits/circuits/", params={"cid": "EXF-1"}).json()["count"] == 0


def test_a_dry_run_proxy_marks_a_completion_as_practice_live(tmp_path, harbour, nbx):
    with _client(tmp_path, ADMIN, write=False) as cl:
        before = _changelog_count(nbx)
        line = {"id": f"q:site_record:site:{harbour['site']['id']}"}
        cl.post("/api/game/quests/answer", json={"scope": "netbox", "quest_id": line["id"], "step": "record", "values": {"facility": "HBR-1"}})
        v = cl.post("/api/game/quests/apply", json={"scope": "netbox", "quest_id": line["id"]}).json()
        assert (v["mode"], v["verified"], v["xp_awarded"], v["quest"]["status"]) == ("dry_run", False, 0, "practised")
        assert "dry-run mode" in v["message"] and "No XP for a practice run" in v["message"]
        assert _changelog_count(nbx) == before
