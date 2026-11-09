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
    """Three switches, an ISP, two rooms with a rack each; nothing interconnected. Removed again afterwards."""
    made: list[tuple[str, int]] = []

    def make(path: str, body: dict) -> dict:
        obj = _post(nbx, path, body)
        made.append((path, obj["id"]))
        return obj

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
    # what the quests created first (cables, then circuits with their ends), then the estate itself, newest first
    dev_ids = [d["id"] for d in devs.values()]
    for c in nbx.get("dcim/cables/", params=[("device_id", i) for i in dev_ids] + [("limit", 100)]).json()["results"]:
        nbx.delete(f"dcim/cables/{c['id']}/")
    for c in nbx.get("circuits/circuits/", params={"provider_id": provider["id"]}).json()["results"]:
        nbx.delete(f"circuits/circuits/{c['id']}/")
    for ip in nbx.get("ipam/ip-addresses/", params=[("device_id", i) for i in dev_ids] + [("limit", 100)]).json()["results"]:
        nbx.delete(f"ipam/ip-addresses/{ip['id']}/")
    for path, oid in reversed(made):
        r = nbx.delete(f"{path}{oid}/")
        assert r.status_code in (204, 404), (path, r.text)


def _client(tmp, token: str, write: bool = True) -> TestClient:
    app = create_app(Settings(URL, TOKEN, write, None, cache_ttl=0, state_dir=tmp, estate_ttl=0))
    cl = TestClient(base_url=LOCAL, app=app)
    r = cl.post("/api/game/session", json={"token": token}, headers={CSRF: "sign-in"})
    assert r.status_code == 200, r.text
    cl.headers[CSRF] = r.json()["csrf_token"]
    cl.player = r.json()["player"]["username"]
    return cl


@pytest.fixture(scope="module")
def admin(tmp_path_factory, harbour):
    with _client(tmp_path_factory.mktemp("quests-admin"), ADMIN) as cl:
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
                     "device_unconnected", "device_unreachable", "site_record"]
    # at most a few lines are open at one site, the most valuable first; the rest wait
    assert [q["kind"] for q in b["quests"]] == ["site_no_way_out", "sites_not_linked", "locations_not_linked"]
    assert b["queued"] == 5 and all(q["status"] == "offered" for q in b["quests"])
    assert b["quests"][0]["purpose"] == "Nobody can tell how Harbour Office reaches the internet. Let's find out."
    assert not next(s for s in b["sites"] if s["site_id"] == site)["boss_ready"]


def test_the_way_out_line_ends_in_a_trace_netbox_confirms_live(admin, harbour, nbx):
    site = harbour["site"]["id"]
    quest = _board(admin, site)["quests"][0]
    assert quest["step"]["prompt"] == "Which device at Harbour Office does the line to the outside plug into?"
    assert [c["label"] for c in quest["step"]["choices"]] == ["sw-loft-1 (switch)", "sw-quay-1 (switch)", "sw-quay-2 (switch)",
                                                              "Something not listed", "I don't know yet"]
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
    assert v["can_now"] == "You can now trace Harbour Office to the carrier's hand-off."
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
    assert v["can_now"] == "You can now trace from one room to the other."
    assert v["xp_awarded"] == 120 and v["unlocked"] == []


def test_a_repeated_kind_of_fix_pays_less_and_the_site_fills_up_live(admin, harbour):
    site = harbour["site"]["id"]
    b = _board(admin, site)
    # sw-quay-2 and sw-loft-1 are linked now and sw-quay-1 has the circuit: no device is unconnected any more, but
    # nothing joins sw-quay-1 (and with it the way out) to the other two
    offered = {q["kind"]: q for q in b["quests"] if q["status"] == "offered"}
    assert list(offered) == ["sites_not_linked", "site_island", "device_unreachable"]
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
    assert sorted(q["status"] for q in after["quests"]) == ["dismissed", "done", "done", "done", "offered", "offered"]
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
            line = next(q for q in _board(cl, harbour["site"]["id"])["quests"] if q["kind"] == "site_record")
            form = {"scope": "netbox", "quest_id": line["id"], "step": "record", "values": {"facility": "HBR-1"}}
            quest = cl.post("/api/game/quests/answer", json=form).json()
            assert quest["status"] == "ready"
            v = cl.post("/api/game/quests/apply", json={"scope": "netbox", "quest_id": line["id"]}).json()
            assert (v["mode"], v["verified"], v["xp_awarded"], v["quest"]["status"]) == ("dry_run", False, 0, "practised")
            assert "your NetBox user may not change dcim.site" in v["message"]
            assert _changelog_count(nbx) == before and _board(cl)["rewards"]["xp"] == 0
    finally:
        nbx.delete(f"users/users/{uid}/")
        if allowed:
            nbx.patch(f"dcim/sites/{harbour['site']['id']}/", json={"description": ""})


def test_the_practice_island_never_writes_to_netbox_live(admin, nbx):
    before = _changelog_count(nbx)
    admin.post("/api/game/practice/reset")
    b = _board(admin, scope="practice")
    assert b["sites"][0]["name"] == "Practice Island" and not b["can_write"]
    quest = _answer(admin, b["quests"][0], {"device": "sw-quay-1", "circuit_id": "EXF-1", "circuit_type": "Internet", "local_port": "Te1/1/1",
                                            "cable_type": "Copper"}, scope="practice")
    v = admin.post("/api/game/quests/apply", json={"scope": "practice", "quest_id": quest["id"]}).json()
    assert (v["mode"], v["complete"], v["xp_awarded"]) == ("practice", True, 0)
    assert _changelog_count(nbx) == before
    assert nbx.get("circuits/circuits/", params={"cid": "EXF-1"}).json()["count"] == 0


def test_a_dry_run_proxy_marks_a_completion_as_practice_live(tmp_path, harbour, nbx):
    with _client(tmp_path, ADMIN, write=False) as cl:
        before = _changelog_count(nbx)
        line = next(q for q in _board(cl, harbour["site"]["id"])["quests"] if q["kind"] == "site_record")
        cl.post("/api/game/quests/answer", json={"scope": "netbox", "quest_id": line["id"], "step": "record", "values": {"facility": "HBR-1"}})
        v = cl.post("/api/game/quests/apply", json={"scope": "netbox", "quest_id": line["id"]}).json()
        assert (v["mode"], v["verified"], v["xp_awarded"], v["quest"]["status"]) == ("dry_run", False, 0, "practised")
        assert "dry-run mode" in v["message"] and "No XP for a practice run" in v["message"]
        assert _changelog_count(nbx) == before
