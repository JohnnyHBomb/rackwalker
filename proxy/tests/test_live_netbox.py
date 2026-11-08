"""Contract tests against a live dev NetBox 4.7.4 (synthetic seed). Skipped unless RACKWALKER_LIVE_NETBOX=1.

    make netbox-reset NB_PROJECT=<name> NB_PORT=<p>        # fresh seed: fixture ids == live ids
    make test-live    NB_PROJECT=<name> NB_PORT=<p>        # this file (RACKWALKER_RECORD=1 also rewrites recordings)

Every response is validated against contracts/scene.schema.json and compared with the committed fixture (volatile
fields stripped). The write tests apply real ChangeSets (WRITE_ENABLED=1), prove them in NetBox's changelog
(/api/core/object-changes/) and put the stack back exactly as seeded.
"""
from __future__ import annotations

import os

import httpx
import pytest
from fastapi.testclient import TestClient

from rackwalker_proxy.app import Settings, create_app

from .conftest import INDEX, LOCAL, load, schema_validator
from .netbox_replay import Recorder, strip

pytestmark = pytest.mark.skipif(os.environ.get("RACKWALKER_LIVE_NETBOX") != "1", reason="live NetBox tests: make test-live")
URL, TOKEN = os.environ.get("NETBOX_URL", ""), os.environ.get("NETBOX_TOKEN", "")
GETS = [f for f in INDEX if f["method"] == "GET" and f["path"] != "/api/health" and not f.get("store")]  # store: game routes


@pytest.fixture(scope="module")
def recorder():
    rec = Recorder(URL) if os.environ.get("RACKWALKER_RECORD") == "1" else None
    yield rec
    if rec:
        rec.save()


@pytest.fixture(scope="module")
def ro(recorder):
    """Read-only proxy (WRITE_ENABLED=0), cache off so every call reaches NetBox."""
    with TestClient(base_url=LOCAL, app=create_app(Settings(URL, TOKEN, False, None, cache_ttl=0), transport=recorder)) as cl:
        yield cl


@pytest.fixture(scope="module")
def rw(tmp_path_factory):
    """Write-enabled proxy with the read cache on (default TTL). A write-enabled proxy writes only for a signed-in
    player: the client signs in as the admin user (or, without NETBOX_ADMIN_TOKEN, as the proxy's own user) and sends
    the session's CSRF header on every request. `cl.player` is that user's name, which the changelog carries."""
    who = os.environ.get("NETBOX_ADMIN_TOKEN") or TOKEN
    app = create_app(Settings(URL, TOKEN, True, None, state_dir=tmp_path_factory.mktemp("rw-game")))
    with TestClient(base_url=LOCAL, app=app) as cl:
        r = cl.post("/api/game/session", json={"token": who}, headers={"X-RackWalker-CSRF": "sign-in"})
        assert r.status_code == 200, r.text
        cl.headers["X-RackWalker-CSRF"] = r.json()["csrf_token"]
        cl.player = r.json()["player"]["username"]
        yield cl


@pytest.fixture(scope="module")
def nbx():
    """Direct NetBox access, only to inspect the changelog and to undo test writes."""
    admin = os.environ.get("NETBOX_ADMIN_TOKEN") or TOKEN  # the proxy's own token cannot read the changelog
    with httpx.Client(base_url=URL + "/api/", headers={"Authorization": f"Bearer {admin}"}, timeout=30) as c:
        yield c


def test_health_live(ro):
    h = ro.get("/api/health").json()
    schema_validator("HealthResponse").validate(h)
    assert (h["status"], h["netbox"]["version"], h["netbox"]["supported"]) == ("ok", "4.7.4", True)
    assert TOKEN not in str(h)


@pytest.mark.parametrize("f", GETS, ids=[f["path"] for f in GETS])
def test_get_route_matches_fixture_live(ro, f):
    r = ro.get(f["path"])
    assert r.status_code == f["status"], r.text
    body = r.json()
    schema_validator(f["schema"]).validate(body)
    assert strip(body) == strip(load(f["file"]))
    if r.status_code == 200:
        assert (body["meta"]["source"], body["meta"]["netbox_version"]) == ("netbox", "4.7.4")
    assert TOKEN not in r.text


@pytest.mark.parametrize("path,status,code", [
    ("/api/scene/rack/9999", 404, "not_found"), ("/api/device/9999", 404, "not_found"),
    ("/api/trace/interface/99999", 404, "not_found"), ("/api/trace/front_port/99999", 404, "not_found"),
    ("/api/scene/site/0", 400, "bad_request"), ("/api/scene/site/abc", 400, "bad_request"),
    ("/api/trace/cable/1", 400, "bad_request"), ("/api/nope", 404, "not_found")])
def test_errors_live(ro, path, status, code):
    r = ro.get(path)
    assert r.status_code == status
    schema_validator("ErrorResponse").validate(r.json())
    assert r.json()["error"]["code"] == code


@pytest.mark.parametrize("kind,pid,cables,end", [
    ("front_port", 97, [30], ("interface", 173)),             # patch panel nd-pp-nd-c1 front 1 -> access switch
    ("rear_port", 97, [31, 32], ("interface", 21)),           # its rear -> trunk -> nd-pp1 -> core
    ("circuit_termination", 1, [78], ("interface", 30)),      # EXC-1001 A side -> nd-rtr1
])
def test_pass_through_traces_live(ro, kind, pid, cables, end):
    r = ro.get(f"/api/trace/{kind}/{pid}")
    assert r.status_code == 200, r.text
    tr = r.json()["trace"]
    schema_validator("TraceResponse").validate(r.json())
    assert (tr["origin"]["kind"], tr["origin"]["id"]) == (kind, pid)
    assert [h["cable"]["id"] for h in tr["hops"] if h["cable"]] == cables
    last = tr["hops"][-1]["far_end"][0]
    assert ((last["kind"], last["id"]), tr["is_complete"], tr["is_split"]) == (end, True, False)


def test_circuit_hop_is_normalised_live(ro):
    """NetBox's /trace/ jumps from termination Z to termination A; the proxy inserts the cable-null hop."""
    hops = ro.get("/api/trace/interface/210").json()["trace"]["hops"]
    assert [(h["near_end"][0]["kind"], h["cable"] and h["cable"]["id"]) for h in hops] == [
        ("interface", 79), ("circuit_termination", None), ("circuit_termination", 78)]


def test_preview_matches_fixture_live(ro):
    req = load("changes-request.json")
    del req["ops"][0]["if_unmodified_since"]  # the fixture's lock time predates any live stack
    r = ro.post("/api/changes/preview", json=req)
    assert r.status_code == 200
    schema_validator("ChangeResult").validate(r.json())
    assert r.json() == load("changes-preview.json")


def test_preview_lock_conflict_live(ro):
    r = ro.post("/api/changes/preview", json=load("changes-request.json")).json()  # 09:00 lock < live last_updated
    assert (r["ops"][0]["status"], r["ops"][0]["error"]["code"]) == ("error", "conflict")


@pytest.mark.parametrize("op,code", [
    ({"op": "move_rack", "target": {"kind": "rack", "id": 9999}, "fields": {"iso_x": 1}}, "not_found"),
    ({"op": "place_device", "target": {"kind": "device", "id": 9999}, "fields": {"rack_id": 1, "position": 1, "face": "front"}}, "not_found"),
    ({"op": "place_device", "target": {"kind": "device", "id": 27}, "fields": {"rack_id": 1, "position": 38, "face": "rear"}}, "conflict"),
    ({"op": "place_device", "target": {"kind": "device", "id": 27}, "fields": {"rack_id": 1, "position": 42, "face": "front"}}, "validation_failed"),
    ({"op": "place_device", "target": {"kind": "device", "id": 27}, "fields": {"rack_id": 6, "position": 1, "face": "front"}}, "validation_failed"),
    ({"op": "place_device", "target": {"kind": "device", "id": 5}, "fields": {"rack_id": 1, "position": 1, "face": "front"}}, "validation_failed"),
    ({"op": "create_rack", "target": {"kind": "rack", "id": None}, "fields": {"site_id": 1, "location_id": 1, "name": "ND-R1"}}, "validation_failed"),
    ({"op": "create_rack", "target": {"kind": "rack", "id": None}, "fields": {"site_id": 1, "location_id": 4, "name": "X"}}, "validation_failed"),
    ({"op": "update_fields", "target": {"kind": "cable", "id": 9999}, "fields": {"label": "x"}}, "not_found"),
    ({"op": "update_fields", "target": {"kind": "site", "id": 1}, "fields": {"status": "gone"}}, "validation_failed"),
    # both answered 400 by NetBox 4.7.4 when written directly (measured on NetBox 4.7.4)
    ({"op": "update_fields", "target": {"kind": "location", "id": 8}, "fields": {"name": "Hall A"}}, "validation_failed"),
    ({"op": "update_fields", "target": {"kind": "cable", "id": 1}, "fields": {"length": 2.5}}, "validation_failed"),
])
def test_preview_rejects_live(ro, op, code):
    r = ro.post("/api/changes/preview", json={"ops": [op]})
    assert r.status_code == 200
    schema_validator("ChangeResult").validate(r.json())
    res = r.json()["ops"][0]
    assert (res["status"], res["error"]["code"], res["diff"]) == ("error", code, [])


def test_apply_refused_when_dry_run_live(ro):
    r = ro.post("/api/changes/apply", json=load("changes-request.json"))
    assert (r.status_code, r.json()["error"]["code"]) == (403, "write_disabled")


def _assert_seeded(cl, *paths: str) -> None:
    """The suite proves its own clean-up: these scenes read back exactly as the committed fixtures."""
    for path in paths:
        name = path.removeprefix("/api/scene/").replace("/", "-") + ".json"
        assert strip(cl.get(path).json()) == strip(load(name)), f"{path} not back to the seed"


def _changes(nbx, otype: str, oid: int) -> list[dict]:
    r = nbx.get("core/object-changes/", params={"changed_object_type": otype, "changed_object_id": oid,
                                                  "ordering": "-time", "limit": 50})
    return r.json()["results"]


def test_apply_round_trip_live(rw, nbx):
    before_rack2 = rw.get("/api/scene/rack/2").json()
    assert rw.get("/api/scene/rack/2").json()["meta"]["source"] == "cache"  # second read within the TTL
    x0, y0 = before_rack2["rack"]["iso_x"], before_rack2["rack"]["iso_y"]
    cs = {"reason": "s1a live round trip", "ops": [
        {"op": "move_rack", "target": {"kind": "rack", "id": 2}, "fields": {"iso_x": x0 + 1, "iso_y": y0 + 1},
         "if_unmodified_since": before_rack2["rack"]["last_updated"]},
        {"op": "create_rack", "client_ref": "t", "target": {"kind": "rack", "id": None},
         "fields": {"site_id": 1, "location_id": 1, "name": "ND-S1A-TEST", "u_height": 24, "iso_x": 9, "iso_y": 2, "iso_facing": "S"}},
        {"op": "place_device", "target": {"kind": "device", "id": 27}, "fields": {"rack_id": "@t", "position": 10, "face": "front"}},
        {"op": "update_fields", "target": {"kind": "device", "id": 27}, "fields": {"description": "s1a round trip"}},
        {"op": "move_rack", "target": {"kind": "rack", "id": "@t"}, "fields": {"iso_facing": "E"}}]}
    new_id = None
    try:
        r = rw.post("/api/changes/apply", json=cs)
        assert r.status_code == 200, r.text
        res = r.json()
        schema_validator("ChangeResult").validate(res)
        assert (res["mode"], res["applied"], [o["status"] for o in res["ops"]]) == ("apply", True, ["ok"] * 5)
        new_id = res["ops"][1]["target"]["id"]
        assert isinstance(new_id, int) and res["ops"][4]["target"]["id"] == new_id  # refs resolved to the real id
        assert res["ops"][2]["after"] == {"rack_id": new_id, "position": 10, "face": "front", "location_id": 1}  # read back
        # the cache was invalidated: the next read comes from NetBox and shows the write
        after_rack2 = rw.get("/api/scene/rack/2").json()
        assert after_rack2["meta"]["source"] == "netbox"
        assert (after_rack2["rack"]["iso_x"], after_rack2["rack"]["iso_y"]) == (x0 + 1, y0 + 1)
        new_scene = rw.get(f"/api/scene/rack/{new_id}").json()
        assert [d["id"] for d in new_scene["devices"]] == [27] and new_scene["rack"]["iso_facing"] == "E"
        # the audit trail: NetBox's changelog has each write with the ChangeSet's reason as its message
        rack2 = _changes(nbx, "dcim.rack", 2)[0]
        assert rack2["action"]["value"] == "update" and rack2["message"] == f"rackwalker ({rw.player}): s1a live round trip"
        assert rack2["postchange_data"]["custom_fields"]["iso_x"] == x0 + 1
        created = _changes(nbx, "dcim.rack", new_id)
        assert [c["action"]["value"] for c in created] == ["update", "create"]
        dev = _changes(nbx, "dcim.device", 27)
        assert [c["action"]["value"] for c in dev[:2]] == ["update", "update"]
        assert dev[1]["postchange_data"]["rack"] == new_id and dev[0]["postchange_data"]["description"] == "s1a round trip"
    finally:  # reset to the seed: device 27 back to the store, test rack deleted, rack 2 back where it was
        nbx.patch("dcim/devices/27/", json={"rack": None, "position": None, "face": None, "location": 3, "description": ""})
        if new_id:
            nbx.delete(f"dcim/racks/{new_id}/")
        rw.post("/api/changes/apply", json={"ops": [
            {"op": "move_rack", "target": {"kind": "rack", "id": 2}, "fields": {"iso_x": x0, "iso_y": y0}}]})
    assert nbx.get("dcim/devices/27/").json()["description"] == ""  # no DeviceDetail fixture for device 27
    _assert_seeded(rw, "/api/scene/site/1", "/api/scene/rack/2")


def test_partial_apply_is_reported_live(rw, nbx):
    """A write NetBox refuses after preview passed: earlier ops stay written, later ops are skipped."""
    cs = {"ops": [
        {"op": "update_fields", "target": {"kind": "rack", "id": 3}, "fields": {"description": "s1a partial"}},
        {"op": "update_fields", "target": {"kind": "cable", "id": 1}, "fields": {"type": "no-such-cable-type"}},
        {"op": "update_fields", "target": {"kind": "rack", "id": 4}, "fields": {"description": "never written"}}]}
    try:
        res = rw.post("/api/changes/apply", json=cs).json()
        schema_validator("ChangeResult").validate(res)
        assert res["applied"] is False
        assert [o["status"] for o in res["ops"]] == ["ok", "error", "skipped"]
        err = res["ops"][1]["error"]
        assert err["code"] == "validation_failed" and err["details"]["partial"] is True
        assert (err["details"]["written_ops"], err["details"]["skipped_ops"]) == ([0], [2])
        assert err["details"]["outcome_unknown"] is False
        assert "type" in err["details"]["netbox"]
        assert nbx.get("dcim/racks/4/").json()["description"] == ""
        assert nbx.get("dcim/racks/3/").json()["description"] == "s1a partial"
    finally:
        nbx.patch("dcim/racks/3/", json={"description": ""})
    assert nbx.get("dcim/racks/3/").json()["description"] == ""  # the contract Rack carries no description
    _assert_seeded(rw, "/api/scene/rack/3", "/api/scene/site/1")


def test_rack_names_follow_netbox_rule_live(rw, nbx):
    """NetBox 4.7.4 rack names are unique per location, case-sensitively. A rename that differs only in
    case from a sibling (rack 9 EE-A1 -> ee-a2 next to EE-A2) passes preview and is written."""
    name = nbx.get("dcim/racks/9/").json()["name"]
    try:
        res = rw.post("/api/changes/apply", json={"ops": [
            {"op": "update_fields", "target": {"kind": "rack", "id": 9}, "fields": {"name": "ee-a2"}}]}).json()
        schema_validator("ChangeResult").validate(res)
        assert res["applied"] is True and res["ops"][0]["after"] == {"name": "ee-a2"}
        assert nbx.get("dcim/racks/9/").json()["name"] == "ee-a2"
    finally:
        nbx.patch("dcim/racks/9/", json={"name": name})
    assert nbx.get("dcim/racks/9/").json()["name"] == "EE-A1"
    _assert_seeded(rw, "/api/scene/rack/9", "/api/scene/site/3")


# ------------------------------------------------------------------------------------------- game sign-in (live)


def _count(nbx, path: str) -> int:
    r = nbx.get(path, params={"limit": 1})
    r.raise_for_status()
    return r.json()["count"]


def test_game_sign_in_as_netbox_users_live(tmp_path, nbx):
    """Two NetBox users sign in with their own tokens and get their own game state; the sign-in and every game
    write leave NetBox untouched (no new token, no changelog entry)."""
    admin = os.environ.get("NETBOX_ADMIN_TOKEN")
    if not admin:
        pytest.skip("needs NETBOX_ADMIN_TOKEN (make test-live sets it)")
    changes0, tokens0 = _count(nbx, "core/object-changes/"), _count(nbx, "users/tokens/")
    app = create_app(Settings(URL, TOKEN, False, None, state_dir=tmp_path))
    with TestClient(base_url=LOCAL, app=app) as a, TestClient(base_url=LOCAL, app=app) as b:
        sa = a.post("/api/game/session", json={"token": admin}, headers={"X-RackWalker-CSRF": "x"})
        sb = b.post("/api/game/session", json={"token": TOKEN}, headers={"X-RackWalker-CSRF": "x"})
        assert sa.status_code == sb.status_code == 200, (sa.text, sb.text)
        for s in (sa, sb):
            schema_validator("GameSession").validate(s.json())
            assert admin not in s.text and TOKEN not in s.text
        pa, pb = sa.json()["player"], sb.json()["player"]
        assert pa["username"] == "admin" and pb["username"] == "rackwalker-proxy" and pa["id"] != pb["id"]
        save = {"scene": "overworld", "overworld": {"x": 5, "y": 6, "facing": "E"}, "iso": None}
        assert a.put("/api/game/saves/auto", json=save, headers={"X-RackWalker-CSRF": sa.json()["csrf_token"]}).status_code == 200
        b.patch("/api/game/flags", json={"flags": {"ilatea.intro.seen": True}}, headers={"X-RackWalker-CSRF": sb.json()["csrf_token"]})
        assert a.get("/api/game/state").json()["saves"][0]["data"] == save
        assert b.get("/api/game/state").json()["saves"] == [] and a.get("/api/game/state").json()["flags"] == {}
        bad = a.post("/api/game/session", json={"token": "nbt_notakey.notasecret"}, headers={"X-RackWalker-CSRF": "x"})
        assert bad.status_code == 401 and bad.json()["error"]["code"] == "unauthorized"
    db = (tmp_path / "game.sqlite3").read_bytes()
    assert admin.encode() not in db and TOKEN.encode() not in db
    assert (_count(nbx, "core/object-changes/"), _count(nbx, "users/tokens/")) == (changes0, tokens0)


def test_a_write_enabled_proxy_refuses_writes_without_a_signed_in_player_live(tmp_path, ro):
    """No session, no CSRF header, a foreign Origin: the change routes answer 401 and NetBox is untouched."""
    before = ro.get("/api/scene/rack/1").json()["rack"]
    app = create_app(Settings(URL, TOKEN, True, None, cache_ttl=0, state_dir=tmp_path))
    cs = {"ops": [{"op": "move_rack", "target": {"kind": "rack", "id": 1}, "fields": {"iso_y": (before["iso_y"] or 0) + 1}}],
          "reason": "no session"}
    with TestClient(base_url=LOCAL, app=app) as cl:
        for path in ("/api/changes/apply", "/api/changes/preview"):
            r = cl.post(path, json=cs, headers={"Origin": "http://localhost:9999"})
            assert r.status_code == 401 and r.json()["error"]["code"] == "unauthorized", r.text
    after = ro.get("/api/scene/rack/1").json()["rack"]
    assert (after["iso_y"], after["last_updated"]) == (before["iso_y"], before["last_updated"])
