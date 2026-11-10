"""What a quest line pays, and when: the routes that read NetBox again and settle a line, against a made-up NetBox that
also takes writes (httpx.MockTransport). A line pays only for what NetBox shows as true; something that merely went
away pays nothing; a fix pays once; and nothing is written for a player whose own NetBox rights do not cover it."""
from __future__ import annotations

import json
import threading
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

from rackwalker_proxy import perms, questroutes
from rackwalker_proxy.app import Settings, create_app
from rackwalker_proxy.game import CSRF_HEADER

from .conftest import LOCAL, TOKEN_ID, schema_validator, token_record
from .test_estate_read import FakeNetBox, cable, plug, ref, tiny, two_sites

EVERYTHING = [{"id": 1, "enabled": True, "constraints": None, "actions": ["add", "change"],
               "object_types": ["dcim.cable", "dcim.device", "dcim.site", "dcim.rack", "ipam.ipaddress", "ipam.prefix", "circuits.circuit",
                                "circuits.circuittermination"]}]


class Box(FakeNetBox):
    """A made-up NetBox with one player (user 9, whose token record is 77) that also takes the writes quests make."""

    def __init__(self, data: dict[str, list[dict[str, Any]]], permissions: list[dict[str, Any]] | None = None):
        super().__init__(data)
        for loc in data.get("dcim/locations", []):
            loc.setdefault("slug", loc["name"].lower())       # the change path reads whole rows, not the scanner's slim ones
        for rack in data.get("dcim/racks", []):
            rack.setdefault("status", {"value": "active"})
        self.permissions = EVERYTHING if permissions is None else permissions
        self.token: dict[str, Any] = {}
        self.writes: list[tuple[str, str, dict[str, Any]]] = []
        self.refuse_writes = False
        self.permission_reads = 0
        self.ticks = 0
        self.gone_sites_answer_400 = False

    def __call__(self, req: httpx.Request) -> httpx.Response:
        path = req.url.path
        if token_record(req, **self.token) is not None:
            self.calls.append((req.method, path.removeprefix("/api/").strip("/"), dict(req.url.params)))
            return token_record(req, **self.token)
        if path == "/api/authentication-check/":
            return httpx.Response(200, json={"id": 9, "username": "walker", "first_name": "", "last_name": ""})
        if path == "/api/users/users/9/":
            return httpx.Response(200, json={"id": 9, "username": "walker", "is_active": True, "groups": []})
        if path == "/api/users/permissions/":
            self.permission_reads += 1
            return httpx.Response(200, json={"count": len(self.permissions), "results": self.permissions})
        if path.endswith("/trace/"):
            return httpx.Response(200, json=[])
        if req.method != "GET":
            return self.write(req)
        if self.gone_sites_answer_400 and any(s not in {str(x["id"]) for x in self.data["dcim/sites"]} for s in req.url.params.get_list("site_id")):
            return httpx.Response(400, json={"site_id": ["Select a valid choice."]})
        return super().__call__(req)

    def stamp(self) -> str:
        self.ticks += 1
        return f"2026-11-09T12:00:{self.ticks:02d}.000000Z"

    def write(self, req: httpx.Request) -> httpx.Response:
        path = req.url.path.removeprefix("/api/").strip("/")
        body = json.loads(req.content)
        self.calls.append((req.method, path, {}))
        self.writes.append((req.method, path, body))
        if self.refuse_writes:
            return httpx.Response(400, json={"__all__": ["NetBox says no."]})
        fields = {k: v for k, v in body.items() if k != "changelog_message"}
        if req.method == "PATCH":
            table, oid = path.rsplit("/", 1)
            row = next((r for r in self.data[table] if r["id"] == int(oid)), None)
            if row is None:
                return httpx.Response(404, json={"detail": "Not found."})
            row.update(fields, last_updated=self.stamp())
            return httpx.Response(200, json=row)
        if path == "dcim/cables":
            new = cable(100 + len(self.data["dcim/cables"]), body["a_terminations"][0]["object_id"], body["b_terminations"][0]["object_id"])
            self.data["dcim/cables"].append(new)
            plug(self.data, new, body["a_terminations"][0]["object_id"], body["b_terminations"][0]["object_id"])
            return httpx.Response(201, json=new)
        return httpx.Response(405, json={"detail": "not in this fake"})


def start(tmp_path, monkeypatch, data, write_enabled=True, permissions=None, **settings):
    """(client, the fake NetBox, the app), with the player signed in and reads on request never spaced out."""
    box = Box(data, permissions)
    app = create_app(Settings("http://netbox.invalid", "nbt_x.y", write_enabled, None, cache_ttl=0, state_dir=tmp_path, estate_ttl=60, **settings),
                     transport=httpx.MockTransport(box))
    monkeypatch.setattr(questroutes, "FRESH_SITE_S", 0.0)
    monkeypatch.setattr(questroutes, "FRESH_ALL_S", 0.0)
    cl = TestClient(base_url=LOCAL, app=app, raise_server_exceptions=False)
    cl.__enter__()
    sign_in(cl)
    return cl, box, app


def sign_in(cl) -> None:
    r = cl.post("/api/game/session", json={"token": "nbt_synthetic.not-a-secret-0123456789"}, headers={CSRF_HEADER: "sign-in"})
    assert r.status_code == 200, r.text
    cl.headers[CSRF_HEADER] = r.json()["csrf_token"]


def post(cl, route: str, quest_id: str, **more) -> dict[str, Any]:
    r = cl.post(f"/api/game/quests/{route}", json={"scope": "netbox", "quest_id": quest_id, **more})
    assert r.status_code == 200, r.text
    schema_validator("Quest" if route == "answer" else "QuestVerdict").validate(r.json())
    return r.json()


def board(cl, **params) -> dict[str, Any]:
    r = cl.get("/api/game/quests", params={"scope": "netbox", **params})
    assert r.status_code == 200, r.text
    schema_validator("QuestBoard").validate(r.json())
    return r.json()


def line_of(b: dict[str, Any], quest_id: str) -> dict[str, Any] | None:
    return next((q for q in b["quests"] if q["id"] == quest_id), None)


def link(data, device: int, n: int = 0) -> None:
    """Recorded in NetBox itself: a cable between the two ports of one device (enough for "something is connected")."""
    c = cable(50 + device + n, device * 10 + 1, device * 10 + 2)
    data["dcim/cables"].append(c)
    plug(data, c, device * 10 + 1, device * 10 + 2)


def unconnected(d: int) -> str:
    return f"q:device_unconnected:device:{d}"


def wire(data) -> None:
    """sw3 gets a real uplink (to sw1): with it, `tiny()` is a site with nothing wrong."""
    c = cable(90, 31, 12)
    data["dcim/cables"].append(c)
    plug(data, c, 31, 12)


SW3 = unconnected(3)
PARK = {"step": "peer", "choice": "unknown"}
WIRE = [("peer", "device:1"), ("local_port", "interface:31"), ("peer_port", "interface:12"), ("cable_type", "cat6")]


def answer_to_ready(cl, quest_id: str = SW3) -> dict[str, Any]:
    for step, choice in WIRE:
        q = post(cl, "answer", quest_id, step=step, choice=choice)
    assert q["status"] == "ready", q
    return q


# ---------------------------------------------------------------- only what NetBox shows as true is confirmed
@pytest.mark.parametrize("how", ["switched off", "deleted"])
def test_a_line_whose_device_went_away_closes_unpaid_and_is_forgotten(tmp_path, monkeypatch, how):
    data = tiny()
    cl, box, app = start(tmp_path, monkeypatch, data)
    assert post(cl, "answer", SW3, **PARK)["status"] == "parked"
    if how == "deleted":
        data["dcim/devices"] = [d for d in data["dcim/devices"] if d["id"] != 3]
        data["dcim/interfaces"] = [p for p in data["dcim/interfaces"] if p["device"]["id"] != 3]
        data["ipam/ip-addresses"] = [i for i in data["ipam/ip-addresses"] if i["id"] != 3]
    else:
        data["dcim/devices"][2]["status"] = {"value": "offline"}
    b = board(cl, fresh=True)
    shown = line_of(b, SW3)
    assert shown["status"] == "ready" and shown["proposal"] is None and shown["note"].startswith("This no longer applies:")
    v = post(cl, "verify", SW3)
    assert (v["verified"], v["complete"], v["xp_awarded"], v["potions_awarded"], v["unlocked"], v["can_now"]) == (False, False, 0, 0, [], None)
    assert v["message"].startswith("This no longer applies:") and v["quest"]["status"] == "dismissed"
    after = board(cl, fresh=True)
    assert line_of(after, SW3) is None and after["rewards"]["xp"] == 0 and after["rewards"]["tools"] == [] and after["rewards"]["completed"] == 0
    if how == "switched off":
        # back in service with still no cable: the same question is simply asked again
        data["dcim/devices"][2]["status"] = {"value": "active"}
        assert line_of(board(cl, fresh=True), SW3)["status"] == "offered"


def test_a_list_the_proxy_can_no_longer_read_confirms_nothing(tmp_path, monkeypatch):
    data = tiny()
    cl, box, app = start(tmp_path, monkeypatch, data)
    post(cl, "answer", SW3, **PARK)
    box.refuse = ("dcim/cables",)
    b = board(cl, fresh=True)
    assert b["incomplete"] == ["cables"]
    assert line_of(b, SW3)["note"].startswith("The game could not read everything from NetBox (cables), so this line cannot be checked now.")
    v = post(cl, "verify", SW3)
    assert (v["verified"], v["complete"], v["xp_awarded"]) == (False, False, 0) and v["message"].startswith("The game could not read everything")
    # the line is still the player's, as they left it
    box.refuse = ()
    assert line_of(board(cl, fresh=True), SW3)["status"] == "parked"


def test_a_device_that_moved_to_another_site_takes_its_line_along_and_pays_nothing_for_moving(tmp_path, monkeypatch):
    data = two_sites()
    cl, box, app = start(tmp_path, monkeypatch, data)
    post(cl, "answer", SW3, **PARK)
    data["dcim/devices"][2].update(site=ref(2), location=ref(2), rack=ref(2))
    v = post(cl, "verify", SW3)
    assert (v["verified"], v["xp_awarded"], v["message"]) == (False, 0, "NetBox does not show it yet.")
    b = board(cl, fresh=True)
    assert line_of(b, SW3)["status"] == "parked" and line_of(b, SW3)["site_id"] == 2
    assert {q["site_id"] for q in b["quests"] if q["kind"] == "device_unconnected"} == {2}      # nothing of it is left at the old site
    assert b["rewards"]["xp"] == 0


def test_a_site_deleted_in_netbox_is_not_found_rather_than_a_netbox_error(tmp_path, monkeypatch):
    data = two_sites()
    cl, box, app = start(tmp_path, monkeypatch, data)
    assert board(cl, site_id=2)["site_id"] == 2
    box.gone_sites_answer_400 = True
    for table in ("dcim/sites", "dcim/locations", "dcim/racks"):
        data[table] = [r for r in data[table] if (r["id"] if table == "dcim/sites" else r["site"]["id"]) != 2]
    data["dcim/devices"] = [d for d in data["dcim/devices"] if d["site"]["id"] != 2]
    data["dcim/interfaces"] = [p for p in data["dcim/interfaces"] if p["device"]["id"] < 4]
    r = cl.get("/api/game/quests", params={"scope": "netbox", "site_id": 2, "fresh": "true"})
    assert r.status_code == 404 and r.json()["error"]["code"] == "not_found"


# ---------------------------------------------------------------- what a confirmed line pays
def test_the_same_kind_of_fix_at_one_site_pays_less_each_time_and_a_level_brings_one_potion(tmp_path, monkeypatch):
    data = tiny(devices=7)
    cl, box, app = start(tmp_path, monkeypatch, data)
    out = []
    for d in (3, 4, 5, 6, 7):
        post(cl, "answer", unconnected(d), **PARK)
        link(data, d)
        v = post(cl, "verify", unconnected(d))
        assert v["verified"] and v["complete"] and v["message"].startswith("NetBox confirms it.")
        out.append((v["xp_awarded"], v["potions_awarded"], v["unlocked"], v["rewards"]["xp"], v["rewards"]["level"]))
    assert [o[0] for o in out] == [120, 96, 72, 48, 48]
    assert [o[1] for o in out] == [0, 0, 1, 0, 0]                    # 288 XP crosses 250: level 2, one potion, once
    assert [o[2] for o in out] == [["cable_tracer"], [], [], [], []]   # the tool comes with the first confirmed connectivity line
    assert out[-1][3:] == (384, 2)


def test_repeats_are_counted_per_site_not_across_sites(tmp_path, monkeypatch):
    data = two_sites()
    cl, box, app = start(tmp_path, monkeypatch, data)
    paid = []
    for d in (3, 4, 5):       # sw3 at site 1; sw4 and sw5 at site 2
        post(cl, "answer", unconnected(d), **PARK)
        link(data, d)
        paid.append(post(cl, "verify", unconnected(d))["xp_awarded"])
    assert paid == [120, 120, 96]


def test_an_errand_pays_for_the_share_netbox_confirms_and_brings_its_tool_only_when_whole(tmp_path, monkeypatch):
    data = tiny()
    for d in data["dcim/devices"]:
        d["serial"] = ""
    wire(data)
    cl, box, app = start(tmp_path, monkeypatch, data)
    audit = "q:rack_audit:rack:1"
    post(cl, "answer", audit, step="labels", values={})                    # parked: read later
    data["dcim/devices"][0]["serial"] = "SN-1"                             # one of three read and recorded in NetBox itself
    v = post(cl, "verify", audit)
    assert (v["verified"], v["complete"], v["xp_awarded"], v["unlocked"]) == (True, False, 17, [])
    assert v["message"] == "NetBox confirms 1 more; the rest stays open." and v["quest"]["progress"] == {"total": 3, "verified": 1}
    assert v["quest"]["status"] == "active" and len(v["quest"]["step"]["fields"]) == 2 and v["can_now"] is None
    for d in data["dcim/devices"][1:]:
        d["serial"] = f"SN-{d['id']}"
    v = post(cl, "verify", audit)
    assert (v["complete"], v["xp_awarded"], v["unlocked"], v["quest"]["status"]) == (True, round(50 * 2 / 3 * 0.8), ["label_maker"], "done")
    assert v["rewards"]["completed"] == 1 and v["rewards"]["potions"] == 0


def test_a_device_nobody_logs_in_to_is_never_paid_for(tmp_path, monkeypatch):
    data = tiny()
    wire(data)
    for d in data["dcim/devices"][1:]:
        d["primary_ip"] = None
    cl, box, app = start(tmp_path, monkeypatch, data)
    # this NetBox has no custom field for how a device is managed: the answer is remembered by the game, and pays nothing
    q = post(cl, "answer", "q:device_unreachable:device:2", step="mode_2", choice="unmanaged")
    assert q["status"] == "dismissed" and q["proposal"] is None
    reach = "q:device_unreachable:device:3"
    post(cl, "answer", reach, step="mode_3", choice="local")
    q = post(cl, "answer", reach, step="ip_3", choice="unknown")
    assert q["status"] == "parked"
    data["dcim/devices"][2]["primary_ip"] = {"id": 3, "address": "192.0.2.3/24"}
    v = post(cl, "verify", reach)
    assert (v["complete"], v["xp_awarded"]) == (True, 40)            # the address NetBox now shows is paid; the waived device is not
    b = board(cl, fresh=True)
    assert b["sites"][0]["completeness"]["open"] == 0 and b["rewards"]["xp"] == 40


# ---------------------------------------------------------------- a fix pays once
def test_a_fix_that_was_undone_stays_counted_and_is_never_reopened_or_paid_again(tmp_path, monkeypatch):
    data = tiny()
    cl, box, app = start(tmp_path, monkeypatch, data)
    post(cl, "answer", SW3, **PARK)
    link(data, 3)
    assert post(cl, "verify", SW3)["xp_awarded"] == 120
    paid = []
    for _ in range(3):
        gone = data["dcim/cables"].pop()
        plug(data, None, 31, 32)
        v = post(cl, "verify", SW3)
        assert v["quest"]["status"] == "done" and v["quest"]["progress"] == {"total": 1, "verified": 1} and not v["verified"]
        assert v["message"].startswith("NetBox no longer shows what you recorded here.") and v["rewards"]["completed"] == 1
        data["dcim/cables"].append(gone)
        plug(data, gone, 31, 32)
        v = post(cl, "verify", SW3)
        assert v["message"].startswith("NetBox still confirms it.")
        paid.append(v["xp_awarded"])
    assert paid == [0, 0, 0] and board(cl)["rewards"]["xp"] == 120


def test_many_checks_of_one_fix_at_the_same_moment_pay_once(tmp_path, monkeypatch):
    data = tiny()
    cl, box, app = start(tmp_path, monkeypatch, data)
    post(cl, "answer", SW3, **PARK)
    link(data, 3)
    got: list[int] = []

    def check() -> None:
        r = cl.post("/api/game/quests/verify", json={"scope": "netbox", "quest_id": SW3})
        got.append(r.json()["xp_awarded"] if r.status_code == 200 else -1)
    threads = [threading.Thread(target=check) for _ in range(12)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(30)
    assert sorted(got) == [0] * 11 + [120] and board(cl)["rewards"]["xp"] == 120


def test_a_check_pays_once_even_under_another_line(tmp_path, monkeypatch):
    data = tiny()
    data["dcim/racks"].append({**data["dcim/racks"][0], "id": 2, "name": "R2"})
    data["dcim/devices"][0]["serial"] = ""
    wire(data)
    cl, box, app = start(tmp_path, monkeypatch, data)
    post(cl, "answer", "q:rack_audit:rack:1", step="labels", values={})
    # the device moves to another rack: the begun line goes with it, and the serial pays once, under whichever id
    data["dcim/devices"][0]["rack"] = ref(2)
    b = board(cl, fresh=True)
    assert line_of(b, "q:rack_audit:rack:1") is None and line_of(b, "q:rack_audit:rack:2")["status"] == "parked"
    data["dcim/devices"][0]["serial"] = "SN-1"
    assert post(cl, "verify", "q:rack_audit:rack:2")["xp_awarded"] == 50
    assert cl.post("/api/game/quests/verify", json={"scope": "netbox", "quest_id": "q:rack_audit:rack:1"}).status_code == 404
    st = app.state.game_store()
    assert st.mark_paid(9, ["device:1:serial"]) == [] and st.rewards(9)["xp"] == 50


def test_a_line_follows_its_finding_when_the_first_member_of_its_group_goes(tmp_path, monkeypatch):
    data = tiny()
    wire(data)
    for n in (7, 8, 9):
        data["ipam/ip-addresses"].append({"id": n, "address": f"10.9.2.{n}/24", "assigned_object_type": "dcim.interface", "assigned_object_id": 32})
    cl, box, app = start(tmp_path, monkeypatch, data)
    first, second = "q:address_no_prefix:ip:7", "q:address_no_prefix:ip:8"
    assert post(cl, "answer", first, step="subnet", choice="unknown")["status"] == "parked"
    data["ipam/ip-addresses"] = [i for i in data["ipam/ip-addresses"] if i["id"] != 7]
    app.state.drop_estate()
    b = board(cl)
    # the same unfixed problem under its new id is the same begun line: not "already shown", not offered afresh
    assert line_of(b, first) is None and line_of(b, second)["status"] == "parked"
    assert cl.post("/api/game/quests/verify", json={"scope": "netbox", "quest_id": first}).status_code == 404
    v = post(cl, "verify", second)
    assert (v["verified"], v["xp_awarded"]) == (False, 0)
    data["ipam/prefixes"].append({"id": 2, "prefix": "10.9.2.0/24"})
    app.state.drop_estate()
    v = post(cl, "verify", second)
    assert (v["complete"], v["xp_awarded"]) == (True, 50) and v["quest"]["progress"]["verified"] == 2


# ---------------------------------------------------------------- the write path: apply, read again, settle
def test_a_real_write_is_read_back_from_netbox_names_the_player_and_then_pays(tmp_path, monkeypatch):
    data = tiny()
    cl, box, app = start(tmp_path, monkeypatch, data)
    answer_to_ready(cl)
    reads = len(box.lists("dcim/devices"))
    # a quest's own write is always read back, however lately the site was read on a player's request
    monkeypatch.setattr(questroutes, "FRESH_SITE_S", 3600.0)
    v = post(cl, "apply", SW3)
    assert [(m, p) for m, p, _ in box.writes] == [("POST", "dcim/cables")]
    assert box.writes[0][2]["changelog_message"] == "rackwalker (walker): quest: What sw3 plugs into"
    assert len(box.lists("dcim/devices")) == reads + 1                  # NetBox was read again after the write
    assert (v["mode"], v["verified"], v["complete"], v["xp_awarded"], v["unlocked"]) == ("real", True, True, 120, ["cable_tracer"])
    assert v["message"].startswith("NetBox confirms it. Mill is now 100 % complete.") and v["result"]["applied"] is True
    assert v["trace"] == "NetBox's cable trace from sw3 eth1 finds no cable." and v["can_now"] == "You can now trace sw3 to what it plugs into."
    assert line_of(board(cl), SW3)["status"] == "done"


def test_the_outcome_is_what_netbox_shows_not_what_the_proxy_wrote(tmp_path, monkeypatch):
    data = tiny()
    cl, box, app = start(tmp_path, monkeypatch, data)
    answer_to_ready(cl)
    real_write = box.write

    def forgetful(req: httpx.Request) -> httpx.Response:
        resp = real_write(req)                 # NetBox answers 201 ...
        gone = data["dcim/cables"].pop()       # ... and does not have the cable after all
        plug(data, None, gone["a_terminations"][0]["object_id"], gone["b_terminations"][0]["object_id"])
        return resp
    box.write = forgetful
    v = post(cl, "apply", SW3)
    assert v["result"]["applied"] is True
    assert (v["verified"], v["complete"], v["xp_awarded"], v["unlocked"]) == (False, False, 0, [])
    assert v["message"] == "Written, but NetBox does not show the outcome yet; the line stays open."
    assert board(cl)["rewards"]["xp"] == 0


def test_a_write_netbox_does_not_take_confirms_and_pays_nothing_and_says_why(tmp_path, monkeypatch):
    data = tiny()
    cl, box, app = start(tmp_path, monkeypatch, data)
    answer_to_ready(cl)
    # the device is deleted in NetBox after the last question was answered
    data["dcim/devices"] = [d for d in data["dcim/devices"] if d["id"] != 3]
    data["dcim/interfaces"] = [p for p in data["dcim/interfaces"] if p["device"]["id"] != 3]
    reads = len(box.lists("dcim/devices"))
    v = post(cl, "apply", SW3)
    assert (v["mode"], v["verified"], v["complete"], v["xp_awarded"], v["unlocked"]) == ("real", False, False, 0, [])
    assert v["message"] == "NetBox did not take that change, and nothing was written: Interface 31 does not exist."
    assert box.writes == [] and v["result"]["applied"] is False
    # what was read before the failed write is not served as current: the next look reads NetBox again
    b = board(cl)
    assert len(box.lists("dcim/devices")) == reads + 1 and line_of(b, SW3)["note"].startswith("This no longer applies:")
    assert b["rewards"]["xp"] == 0


def test_a_value_recorded_in_netbox_meanwhile_is_not_written_over(tmp_path, monkeypatch):
    data = tiny()
    for d in data["dcim/devices"]:
        d["last_updated"] = "2026-11-09T11:00:00.000000Z"
    data["dcim/devices"][0]["serial"] = ""
    wire(data)
    cl, box, app = start(tmp_path, monkeypatch, data)
    audit = "q:rack_audit:rack:1"
    q = post(cl, "answer", audit, step="labels", values={"device:1:serial": "TYPED-IN-THE-GAME"})
    assert q["status"] == "ready" and q["proposal"]["ops"][0]["if_unmodified_since"] == "2026-11-09T11:00:00.000000Z"
    # a colleague records the real serial in NetBox before the player presses apply
    data["dcim/devices"][0].update(serial="REAL-FROM-A-COLLEAGUE", last_updated="2026-11-09T11:30:00.000000Z")
    preview = cl.post("/api/game/quests/preview", json={"scope": "netbox", "quest_id": audit}).json()
    assert preview["ops"][0]["status"] == "error" and preview["ops"][0]["error"]["code"] == "conflict"
    v = post(cl, "apply", audit)
    assert box.writes == [] and data["dcim/devices"][0]["serial"] == "REAL-FROM-A-COLLEAGUE"
    assert (v["verified"], v["xp_awarded"]) == (False, 0)
    assert v["message"].startswith("NetBox did not take that change, and nothing was written: Device sw1 was changed in NetBox after you loaded it")
    # NetBox is read again; what the colleague recorded settles the line (found by other means: it is confirmed)
    assert line_of(board(cl), audit)["note"] == "NetBox already shows this. Check it against NetBox to complete the line."


def test_two_applies_of_one_line_at_the_same_moment_write_once_and_pay_once(tmp_path, monkeypatch):
    data = tiny()
    cl, box, app = start(tmp_path, monkeypatch, data)
    answer_to_ready(cl)
    got: list[tuple[int, Any]] = []

    def go() -> None:
        r = cl.post("/api/game/quests/apply", json={"scope": "netbox", "quest_id": SW3})
        got.append((r.status_code, r.json().get("xp_awarded")))
    threads = [threading.Thread(target=go) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(30)
    # the second one finds the line already done: nothing left to apply
    assert sorted(got, key=lambda g: g[0]) == [(200, 120), (404, None)]
    assert len(box.writes) == 1 and board(cl)["rewards"]["xp"] == 120


def test_a_change_with_a_question_still_open_is_not_applied(tmp_path, monkeypatch):
    cl, box, app = start(tmp_path, monkeypatch, tiny())
    post(cl, "answer", SW3, step="peer", choice="device:1")
    for route in ("preview", "apply"):
        r = cl.post(f"/api/game/quests/{route}", json={"scope": "netbox", "quest_id": SW3})
        assert r.status_code == 409 and r.json()["error"]["details"] == {"status": "asking"}
    assert box.writes == []


# ---------------------------------------------------------------- whose rights a write needs
def test_without_the_players_own_permission_nothing_is_written_by_the_editor_or_by_a_quest(tmp_path, monkeypatch):
    cl, box, app = start(tmp_path, monkeypatch, two_sites(), permissions=[])
    change = {"ops": [{"op": "update_fields", "target": {"kind": "site", "id": 1}, "fields": {"description": "x"}}], "reason": "test"}
    r = cl.post("/api/changes/apply", json=change)
    assert r.status_code == 403 and r.json()["error"]["details"]["reason"] == "player_lacks_permission"
    assert r.json()["error"]["details"]["missing"] == [{"object_type": "dcim.site", "action": "change", "constrained": False}]
    answer_to_ready(cl)
    v = post(cl, "apply", SW3)
    assert (v["mode"], v["verified"], v["xp_awarded"]) == ("dry_run", False, 0)
    assert v["message"].startswith("Practice run, nothing was written: Your NetBox user may not add dcim.cable.")
    assert not [c for c in box.calls if c[0] != "GET"]


def test_a_permission_taken_away_is_gone_at_the_next_write_not_half_a_minute_later(tmp_path, monkeypatch):
    cl, box, app = start(tmp_path, monkeypatch, tiny())
    assert board(cl)["can_write"] is True                 # the quest log has read (and kept) the permissions
    box.permissions = []                                  # an admin takes them away
    change = {"ops": [{"op": "update_fields", "target": {"kind": "device", "id": 1}, "fields": {"description": "after"}}]}
    r = cl.post("/api/changes/apply", json=change)
    assert r.status_code == 403 and r.json()["error"]["details"]["reason"] == "player_lacks_permission" and box.writes == []
    answer_to_ready(cl)
    assert post(cl, "apply", SW3)["mode"] == "dry_run" and box.writes == []


def test_the_quest_log_keeps_permissions_for_a_short_time_only(tmp_path, monkeypatch):
    cl, box, app = start(tmp_path, monkeypatch, tiny())
    reader = app.state.permission_reader
    board(cl)
    board(cl)
    assert box.permission_reads == 1                      # two looks at the log, one read
    reader.ttl = 0.0
    board(cl)
    assert box.permission_reads == 2                      # the kept answer has an age limit
    reader.ttl = 30.0
    answer_to_ready(cl)
    reads = box.permission_reads
    post(cl, "apply", SW3)                                # a write reads them fresh and leaves nothing kept behind
    board(cl)
    assert box.permission_reads == reads + 2


def test_what_each_change_needs_of_the_player():
    assert perms.NEEDS == {
        "move_rack": [("dcim.rack", "change")], "place_device": [("dcim.device", "change")], "unplace_device": [("dcim.device", "change")],
        "create_rack": [("dcim.rack", "add")], "connect_cable": [("dcim.cable", "add")], "create_circuit": [("circuits.circuit", "add")],
        "create_circuit_termination": [("circuits.circuittermination", "add")],
        "assign_ip": [("ipam.ipaddress", "add"), ("dcim.device", "change")], "create_prefix": [("ipam.prefix", "add")],
        "set_management": [("dcim.device", "change")], "record_circuit": [("circuits.circuit", "change")],
        "create_provider_account": [("circuits.provideraccount", "add")], "create_provider_network": [("circuits.providernetwork", "add")],
        "create_contact": [("tenancy.contact", "add")], "assign_contact": [("tenancy.contactassignment", "add")]}
    # a model's default lives on the device type: recording it needs that permission too
    op = {"op": "set_management", "target": {"kind": "device", "id": 1}, "fields": {"mode": "cloud", "model_default": "cloud"}}
    assert perms.needs_of([op]) == [("dcim.device", "change"), ("dcim.devicetype", "change")]
    ops = [{"op": "update_fields", "target": {"kind": "site", "id": 1}}, {"op": "create_circuit", "target": {"kind": "circuit", "id": None}},
           {"op": "something_new", "target": {"kind": "device", "id": 1}}, {"op": "update_fields", "target": {"kind": "site", "id": 2}}]
    # an op the table does not know needs a permission nobody has: it is refused, never waved through
    assert perms.needs_of(ops) == [("dcim.site", "change"), ("circuits.circuit", "add"), ("unknown", "change")]
    nobody = perms.Permissions(False, {("dcim.site", "change")}, {("circuits.circuit", "add")})
    assert nobody.missing(perms.needs_of(ops)) == [{"object_type": "circuits.circuit", "action": "add", "constrained": True},
                                                   {"object_type": "unknown", "action": "change", "constrained": False}]
    assert perms.Permissions(True, set(), set()).missing(perms.needs_of(ops)) == []


def test_the_single_user_local_mode_asks_netbox_about_no_user(tmp_path, monkeypatch):
    box = Box(tiny())
    app = create_app(Settings("http://netbox.invalid", "nbt_x.y", True, None, cache_ttl=0, state_dir=tmp_path, estate_ttl=60, local_player=True),
                     transport=httpx.MockTransport(box))
    with TestClient(base_url=LOCAL, app=app) as cl:
        cl.headers[CSRF_HEADER] = "local"
        assert board(cl)["can_write"] is True
        for step, choice in WIRE:
            post(cl, "answer", SW3, step=step, choice=choice)
        v = post(cl, "apply", SW3)
        assert v["mode"] == "real" and box.writes[0][2]["changelog_message"].startswith("rackwalker (local): ")
    assert box.permission_reads == 0 and not [c for c in box.calls if c[1].startswith("users/")]


# ---------------------------------------------------------------- a read-only token stays read-only through the game
def test_a_read_only_netbox_token_plays_in_dry_run_and_writes_nothing(tmp_path, monkeypatch):
    box = Box(tiny())
    box.token = {"write_enabled": False}
    app = create_app(Settings("http://netbox.invalid", "nbt_x.y", True, None, cache_ttl=0, state_dir=tmp_path, estate_ttl=60),
                     transport=httpx.MockTransport(box))
    with TestClient(base_url=LOCAL, app=app) as cl:
        sign_in(cl)
        b = board(cl)
        assert b["can_write"] is False and b["write_blocked"].startswith("You signed in with a read-only NetBox token")
        change = {"ops": [{"op": "update_fields", "target": {"kind": "device", "id": 1}, "fields": {"description": "through the game"}}]}
        r = cl.post("/api/changes/apply", json=change)
        assert r.status_code == 403 and r.json()["error"]["details"] == {"reason": "read_only_token"}
        answer_to_ready(cl)
        v = post(cl, "apply", SW3)
        assert (v["mode"], v["xp_awarded"]) == ("dry_run", 0) and v["message"].startswith("Practice run, nothing was written: You signed in with a read-only")
    assert box.writes == []


def test_a_token_netbox_shows_no_record_of_is_taken_as_read_only(tmp_path, monkeypatch):
    box = Box(tiny())
    app = create_app(Settings("http://netbox.invalid", "nbt_x.y", True, None, cache_ttl=0, state_dir=tmp_path, estate_ttl=60),
                     transport=httpx.MockTransport(box))
    with TestClient(base_url=LOCAL, app=app) as cl:
        # a legacy v1 token has no public key part: its record is not looked up (the secret would be in the URL)
        r = cl.post("/api/game/session", json={"token": "0123456789abcdef0123456789abcdef01234567"}, headers={CSRF_HEADER: "sign-in"})
        assert r.status_code == 200
        cl.headers[CSRF_HEADER] = r.json()["csrf_token"]
        assert board(cl)["can_write"] is False
    assert not [c for c in box.calls if c[1].startswith("users/tokens")]


@pytest.mark.parametrize("what,reason", [({"gone": True}, "token_gone"), ({"enabled": False}, "token_disabled"),
                                         ({"expires": "2020-01-01T00:00:00Z"}, "token_expired")])
def test_a_session_ends_when_its_token_is_deleted_disabled_or_expired(tmp_path, monkeypatch, what, reason):
    cl, box, app = start(tmp_path, monkeypatch, tiny())
    assert board(cl)["can_write"] is True
    box.token = what
    change = {"ops": [{"op": "update_fields", "target": {"kind": "device", "id": 1}, "fields": {"description": "after the token went"}}]}
    r = cl.post("/api/changes/apply", json=change)
    assert r.status_code == 401 and r.json()["error"]["details"] == {"reason": reason} and box.writes == []
    # the browser keeps hearing why, and the session is gone
    r = cl.get("/api/game/quests")
    assert r.status_code == 401 and r.json()["error"]["details"] == {"reason": reason}
    assert cl.get("/api/game/session").json()["signed_in"] is False


def test_a_token_made_read_only_after_sign_in_stops_writing(tmp_path, monkeypatch):
    cl, box, app = start(tmp_path, monkeypatch, tiny())
    box.token = {"write_enabled": False}
    change = {"ops": [{"op": "update_fields", "target": {"kind": "device", "id": 1}, "fields": {"description": "x"}}]}
    r = cl.post("/api/changes/apply", json=change)
    assert r.status_code == 403 and r.json()["error"]["details"] == {"reason": "read_only_token"} and box.writes == []
    assert [c[1] for c in box.calls if c[1] == f"users/tokens/{TOKEN_ID}"]     # looked up by its id, with the proxy's token


def test_a_session_never_outlives_its_token(tmp_path):
    from rackwalker_proxy.gamestore import SESSION_SECONDS, GameStore
    now = [1000.0]
    st = GameStore(tmp_path / "g.sqlite3", clock=lambda: now[0])
    st.upsert_player(7, "a", None)
    plain, s = st.new_session(7)
    assert s.expires_at == 1000.0 + SESSION_SECONDS == 1000.0 + 7 * 24 * 3600 and (s.can_write, s.token_id) == (True, None)
    cookie, s = st.new_session(7, can_write=False, token_id=77, token_expires=1600.0)
    assert (s.expires_at, s.can_write, s.token_id) == (1600.0, False, 77) and st.session(cookie) == s
    st.update_session(cookie, True, 1300.0)
    assert st.session(cookie).can_write is True and st.session(cookie).expires_at == 1300.0
    now[0] = 1300.0
    assert st.session(cookie) is None and st.session(plain) is not None


# ---------------------------------------------------------------- rules of the routes themselves
def test_checking_against_netbox_needs_the_sessions_csrf_header_and_is_not_for_spot_checks_or_practice(tmp_path, monkeypatch):
    cl, box, app = start(tmp_path, monkeypatch, tiny())
    ref_ = {"scope": "netbox", "quest_id": SW3}
    assert cl.post("/api/game/quests/verify", json=ref_, headers={CSRF_HEADER: "wrong"}).status_code == 403
    for body, says in (({"scope": "netbox", "quest_id": "q:spot_check:device:1:0"}, "A spot check is answered by looking at the device"),
                       ({"scope": "practice", "quest_id": "q:site_no_way_out:site:9000001"}, "Only quests on your own NetBox")):
        r = cl.post("/api/game/quests/verify", json=body)
        assert r.status_code == 400 and r.json()["error"]["message"].startswith(says)
    assert cl.post("/api/game/quests/verify", json={"scope": "netbox", "quest_id": "q:nothing:at:all"}).status_code == 404


def test_a_parked_spot_check_is_never_settled_by_reading_netbox(tmp_path, monkeypatch):
    data = tiny()
    wire(data)
    cl, box, app = start(tmp_path, monkeypatch, data)
    b = board(cl)
    spot = next(q for q in b["quests"] if q["kind"] == "spot_check")
    assert b["all_in_order"] and post(cl, "answer", spot["id"], step="same", choice="no")["status"] == "parked"
    assert cl.post("/api/game/quests/verify", json={"scope": "netbox", "quest_id": spot["id"]}).status_code == 400
    after = board(cl)
    assert line_of(after, spot["id"])["status"] == "parked" and after["rewards"]["tools"] == [] and after["rewards"]["completed"] == 0


def test_a_write_through_the_editor_drops_the_kept_estate(tmp_path, monkeypatch):
    data = tiny()
    cl, box, app = start(tmp_path, monkeypatch, data)
    assert cl.get("/api/quests/gaps/1").json()["source"] == "netbox" and cl.get("/api/quests/gaps/1").json()["source"] == "cache"
    change = {"ops": [{"op": "update_fields", "target": {"kind": "device", "id": 1}, "fields": {"serial": ""}}]}
    r = cl.post("/api/changes/apply", json=change)
    assert r.status_code == 200 and r.json()["applied"] is True, r.text
    after = cl.get("/api/quests/gaps/1").json()
    assert after["source"] == "netbox" and [g["id"] for g in after["items"]] == ["device:1:serial"]


def test_the_editor_never_takes_the_practice_islands_ids(tmp_path, monkeypatch):
    cl, box, app = start(tmp_path, monkeypatch, tiny())
    for change in ({"ops": [{"op": "update_fields", "target": {"kind": "device", "id": 9000001}, "fields": {"serial": "X"}}]},
                   {"ops": [{"op": "place_device", "target": {"kind": "device", "id": 1}, "fields": {"rack_id": 9000001, "position": 3, "face": "front"}}]}):
        for route in ("preview", "apply"):
            r = cl.post(f"/api/changes/{route}", json=change)
            assert r.status_code == 400 and r.json()["error"]["details"] == {"reason": "practice_island"}, (route, r.text)
    assert box.writes == []


# ---------------------------------------------------------------- completeness and the site's Confusion
def test_a_port_said_to_be_not_in_use_counts_and_the_site_can_reach_its_confusion(tmp_path, monkeypatch):
    data = tiny()
    wire(data)
    data["dcim/interfaces"][3]["description"] = "printer on the landing"      # sw2 eth2: described, no cable
    cl, box, app = start(tmp_path, monkeypatch, data)
    b = board(cl)
    port = "q:interface_no_cable:device:2"
    assert line_of(b, port)["title"] == "The other end of sw2 eth2" and b["sites"][0]["completeness"]["open"] == 1 and not b["sites"][0]["boss_ready"]
    q = post(cl, "answer", port, step="peer_22", choice="none")
    assert q["status"] == "dismissed"
    b = board(cl)
    site = b["sites"][0]
    assert (site["completeness"]["open"], site["completeness"]["percent"], site["boss_ready"], site["open_quests"]) == (0, 100, True, 0)
    assert b["message"] == "All in order at your estate: nothing the game can see is missing. Well kept."
    assert cl.post("/api/game/boss/ack", json={"scope": "netbox", "site_id": 1}).status_code == 200
    # the scanner's own list is the estate's, not the player's: the gap stays there
    assert [g["id"] for g in cl.get("/api/quests/gaps/1").json()["items"]] == ["interface:22:cable"]


def test_a_parked_or_practised_line_fills_nothing_up(tmp_path, monkeypatch):
    data = tiny(devices=4)
    cl, box, app = start(tmp_path, monkeypatch, data, write_enabled=False)
    before = board(cl)["sites"][0]["completeness"]
    assert before["open"] == 2
    post(cl, "answer", SW3, **PARK)
    for step, choice in (("peer", "device:1"), ("local_port", "interface:41"), ("peer_port", "interface:12"), ("cable_type", "cat6")):
        post(cl, "answer", unconnected(4), step=step, choice=choice)
    assert post(cl, "apply", unconnected(4))["quest"]["status"] == "practised"
    b = board(cl)
    assert sorted(q["status"] for q in b["quests"]) == ["parked", "practised"]
    assert b["sites"][0]["completeness"] == before and not b["sites"][0]["boss_ready"] and not b["all_in_order"]


def test_a_site_with_nothing_in_it_has_no_confusion_to_face(tmp_path, monkeypatch):
    data = tiny()
    wire(data)
    data["dcim/sites"].append({**data["dcim/sites"][0], "id": 2, "name": "Empty Lot", "slug": "empty-lot"})
    cl, box, app = start(tmp_path, monkeypatch, data)
    empty = next(s for s in board(cl)["sites"] if s["site_id"] == 2)
    assert empty["completeness"] == {"percent": 100, "total": 3, "open": 0} and empty["boss_ready"] is False
    r = cl.post("/api/game/boss/ack", json={"scope": "netbox", "site_id": 2})
    assert r.status_code == 409
