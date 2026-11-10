"""The quest routes on a fixture-mode proxy (no NetBox): the gap scanner, the quest log, the practice island played
through the routes, the rewards ledger, the boss marker, the game store migration, the new change ops and the
permission reader."""
from __future__ import annotations

import asyncio
import json
import sqlite3

import httpx
import pytest

from rackwalker_proxy import changes, perms
from rackwalker_proxy import contracts_gen as c
from rackwalker_proxy.errors import ApiError
from rackwalker_proxy.game import CSRF_HEADER
from rackwalker_proxy.gamestore import MIGRATIONS, SCHEMA_VERSION, GameStore, migrate
from rackwalker_proxy.netbox import NetBox

from .conftest import schema_validator
from .plan_helpers import device, site
from .test_game import fixture_client, sign_in

ISLAND = 9000001
WAY_OUT = [("device", {"choice": "device:9000001"}), ("provider", {"choice": "provider:9000001"}),
           ("circuit_id", {"text": "EXF-20417"}), ("circuit_type", {"choice": "type:9000001"}),
           ("local_port", {"choice": "interface:9000026"}), ("cable_type", {"choice": "smf"})]


@pytest.fixture
def cl(tmp_path):
    client = fixture_client(tmp_path)
    client.headers[CSRF_HEADER] = sign_in(client)
    return client


def board(cl, scope="practice", **params) -> dict:
    r = cl.get("/api/game/quests", params={"scope": scope, **params})
    assert r.status_code == 200, r.text
    schema_validator("QuestBoard").validate(r.json())
    return r.json()


def answer(cl, quest_id: str, step: str, scope="practice", **a) -> dict:
    r = cl.post("/api/game/quests/answer", json={"scope": scope, "quest_id": quest_id, "step": step, **a})
    assert r.status_code == 200, r.text
    schema_validator("Quest").validate(r.json())
    return r.json()


def apply(cl, quest_id: str, scope="practice") -> dict:
    r = cl.post("/api/game/quests/apply", json={"scope": scope, "quest_id": quest_id})
    assert r.status_code == 200, r.text
    schema_validator("QuestVerdict").validate(r.json())
    return r.json()


def test_the_gap_scanner_is_paged_ordered_and_stable(cl):
    r = cl.get("/api/quests/gaps/1")
    assert r.status_code == 200, r.text
    full = r.json()
    schema_validator("GapList").validate(full)
    ids = [g["id"] for g in full["items"]]
    # two of the seed's ports carry an address and have no cable (an address on a management port does not count);
    # the site has no facility name
    assert full["total"] == len(ids) == 3 and ids[0] == "interface:26:cable" and ids[-1] == "site:1:facility" and full["source"] == "fixture"
    assert full["items"][0] == {"id": "interface:26:cable", "kind": "interface", "object_id": 26, "field": "cable", "site_id": 1,
                                "rank": "connectivity", "label": "nd-rtr1 ge-0/0/0 has an address but no cable."}
    assert full["completeness"]["open"] >= 1 and 0 < full["completeness"]["percent"] < 100
    page = cl.get("/api/quests/gaps/3", params={"offset": 0, "limit": 1}).json()
    assert (page["offset"], page["limit"], len(page["items"])) == (0, 1, 1)
    assert cl.get("/api/quests/gaps/3").json() == cl.get("/api/quests/gaps/3").json()
    assert cl.get("/api/quests/gaps/999").status_code == 404
    assert cl.get("/api/quests/gaps/1", params={"limit": 501}).status_code == 400


def test_the_quest_routes_need_a_signed_in_player_and_the_csrf_header(tmp_path):
    out = fixture_client(tmp_path)
    assert out.get("/api/game/quests").status_code == 401
    out.headers[CSRF_HEADER] = sign_in(out)
    ref = {"scope": "practice", "quest_id": "q:site_no_way_out:site:9000001"}
    for path, body in (("/api/game/quests/answer", {**ref, "step": "device", "choice": "unknown"}), ("/api/game/quests/preview", ref),
                       ("/api/game/quests/apply", ref), ("/api/game/practice/reset", None), ("/api/game/boss/ack", {"scope": "practice", "site_id": ISLAND})):
        r = out.post(path, json=body, headers={CSRF_HEADER: "wrong"})
        assert r.status_code == 403, (path, r.text)


def test_the_practice_board_offers_a_few_lines_the_most_valuable_first(cl):
    b = board(cl)
    assert (b["scope"], b["can_write"], b["all_in_order"]) == ("practice", False, False)
    assert b["write_blocked"] == "The practice island is made up: nothing you do here reaches NetBox."
    assert [q["kind"] for q in b["quests"]] == ["site_no_way_out", "locations_not_linked", "device_unconnected"]
    assert all(q["status"] == "offered" and q["step"] and 2 <= q["minutes"] <= 5 for q in b["quests"])
    assert b["queued"] == 7 and b["next_quest_id"] == b["quests"][0]["id"]
    assert all(q["write_blocked"] is None for q in b["quests"])   # nothing is proposed yet
    assert b["sites"] == [{"site_id": ISLAND, "name": "Practice Island", "completeness": {"percent": 60, "total": 30, "open": 12},
                           "open_quests": 10, "boss_ready": False, "boss_acknowledged": False, "giver": {"name": "Tansy"}}]
    # on the island both tools are on loan, so a player meets them before earning them
    assert b["rewards"] == {"xp": 0, "level": 1, "next_level_xp": 250, "potions": 0, "tools": ["label_maker", "cable_tracer"],
                            "completed": 0, "practised": 0, "lapsed": 0}
    q = b["quests"][0]
    assert q["target"] == {"site_id": ISLAND, "view": f"site:{ISLAND}", "entity": f"site:{ISLAND}", "label": "Practice Island"}


def test_a_practice_line_from_offer_to_preview_to_verified_and_no_xp(cl):
    qid = board(cl)["next_quest_id"]
    for step, a in WAY_OUT:
        q = answer(cl, qid, step, **a)
    assert q["status"] == "ready" and q["target"]["entity"] == "device:9000001"
    assert q["said"][0] == {"step": "device", "question": "Which device at Practice Island does the line to the outside plug into?",
                            "answer": "sw-quay-1 (switch)"}
    pre = cl.post("/api/game/quests/preview", json={"scope": "practice", "quest_id": qid}).json()
    schema_validator("ChangeResult").validate(pre)
    assert (pre["mode"], pre["applied"], [o["status"] for o in pre["ops"]]) == ("preview", False, ["ok", "ok", "ok"])
    assert board(cl)["sites"][0]["completeness"]["open"] == 12   # a preview changes nothing
    v = apply(cl, qid)
    assert (v["mode"], v["verified"], v["complete"], v["xp_awarded"], v["quest"]["status"]) == ("practice", True, True, 0, "done")
    assert v["can_now"] == "You can now see in NetBox where the line to the outside comes into Practice Island, and trace it." and v["result"]["applied"]
    after = board(cl)
    assert after["rewards"]["xp"] == 0 and after["rewards"]["practised"] == 1 and after["rewards"]["completed"] == 0
    assert qid not in [q["id"] for q in after["quests"] if q["status"] != "done"]
    assert after["sites"][0]["completeness"]["open"] == 10   # the site has a way out, and sw-quay-1 a cable
    # applying it twice is refused: the line is no longer open
    assert cl.post("/api/game/quests/apply", json={"scope": "practice", "quest_id": qid}).status_code == 404


def test_parked_lines_wait_beside_the_open_ones_and_resume(cl):
    b = board(cl)
    qid = b["quests"][0]["id"]
    q = answer(cl, qid, "device", choice="unknown")
    assert q["status"] == "parked" and q["checklist"][0].startswith("At Practice Island, find where the carrier's line comes in")
    b2 = board(cl)
    # the parked line stays in the log without blocking: three fresh lines are offered beside it
    assert [x["status"] for x in b2["quests"]] == ["offered", "offered", "offered", "parked"] and b2["next_quest_id"] != qid
    assert b2["rewards"]["xp"] == 0   # parking costs nothing
    q = answer(cl, qid, "device", choice="device:9000002")
    # whose line it is is asked even with one provider on record: it may be another carrier's
    assert q["status"] == "active" and q["step"]["id"] == "provider"
    assert [c["label"] for c in q["step"]["choices"]] == ["Example Fibre", "A carrier that is not in NetBox yet", "I don't know yet"]
    # an answer to a question the line is not waiting at changes nothing
    stale = answer(cl, qid, "cable_type", choice="smf")
    assert stale["step"]["id"] == "provider"
    assert answer(cl, qid, "provider", choice="provider:9000001")["step"]["id"] == "circuit_id"
    restarted = answer(cl, qid, "restart")
    assert restarted["step"]["id"] == "device" and restarted["said"] == []


def test_the_whole_island_can_be_put_in_order_then_its_confusion_is_ready(cl):
    put_island_in_order(cl)
    b = board(cl)
    assert b["all_in_order"] and b["message"] == "All in order at the practice island: nothing the game can see is missing. Well kept."
    assert {"q:site_no_way_out:site:9000001", "q:rack_audit:rack:9000001", "q:site_record:site:9000001", "q:room_layout:location:9000002",
            "q:rack_devices_unpositioned:rack:9000002"} <= {q["id"] for q in b["quests"] if q["status"] == "done"}
    assert b["sites"][0]["completeness"] == {"percent": 100, "total": b["sites"][0]["completeness"]["total"], "open": 0}
    assert b["sites"][0]["boss_ready"] and not b["sites"][0]["boss_acknowledged"]
    acked = cl.post("/api/game/boss/ack", json={"scope": "practice", "site_id": ISLAND})
    assert acked.status_code == 200 and acked.json()["sites"][0]["boss_acknowledged"]
    # practice pays nothing however much of it is done
    assert b["rewards"]["xp"] == 0 and b["rewards"]["practised"] >= 7
    reset = cl.post("/api/game/practice/reset").json()
    assert reset["sites"][0]["completeness"]["open"] == 12 and not reset["sites"][0]["boss_ready"] and reset["rewards"]["practised"] == 0


def test_a_boss_that_is_not_ready_cannot_be_acknowledged(cl):
    r = cl.post("/api/game/boss/ack", json={"scope": "practice", "site_id": ISLAND})
    assert r.status_code == 409 and r.json()["error"]["details"]["completeness"]["open"] == 12


def test_on_real_data_a_dry_run_completion_is_marked_practice_and_pays_nothing(cl):
    b = board(cl, "netbox", site_id=1)
    assert (b["can_write"], b["write_blocked"]) == (False, "This proxy serves canned fixture data: nothing is written.")
    q = b["quests"][0]
    assert q["kind"] == "interface_no_cable" and q["title"] == "The other end of nd-rtr1 ge-0/0/0"
    assert q["purpose"] == ("nd-rtr1 ge-0/0/0 has an address but no cable. A port in use with no far end on record cannot be traced. "
                            "Let's find its far end.")
    for _ in range(10):
        if q["status"] == "ready":
            break
        pick = next(x for x in q["step"]["choices"] if x["kind"] == "candidate")
        q = answer(cl, q["id"], q["step"]["id"], scope="netbox", choice=pick["id"])
    v = apply(cl, q["id"], "netbox")
    assert (v["mode"], v["verified"], v["xp_awarded"], v["quest"]["status"]) == ("dry_run", False, 0, "practised")
    assert v["message"].startswith("Practice run, nothing was written:") and v["can_now"] is None
    after = board(cl, "netbox", site_id=1)
    assert after["rewards"] == {**b["rewards"], "practised": 1}
    assert next(x for x in after["quests"] if x["id"] == q["id"])["status"] == "practised"
    assert cl.post("/api/game/quests/verify", json={"scope": "netbox", "quest_id": q["id"]}).status_code == 400


def test_players_keep_separate_quest_logs(tmp_path):
    a = fixture_client(tmp_path)
    a.headers[CSRF_HEADER] = sign_in(a)
    qid = board(a)["next_quest_id"]
    answer(a, qid, "device", choice="unknown")
    other = fixture_client(tmp_path)
    from .conftest import load
    other.headers[CSRF_HEADER] = sign_in(other, load("players.json")["players"][1]["token"])
    assert [q["status"] for q in board(other)["quests"]] == ["offered", "offered", "offered"]


# ---------------------------------------------------------------- the game store
def test_a_version_1_store_gains_the_quest_tables_and_keeps_its_saves(tmp_path):
    path = tmp_path / "game.sqlite3"
    db = sqlite3.connect(path, isolation_level=None)
    migrate(db, MIGRATIONS[:1])
    db.execute("INSERT INTO player VALUES (7, 'walker', NULL, '2026-11-08T10:00:00Z', '2026-11-08T10:00:00Z')")
    save = {"scene": "overworld", "overworld": {"x": 3, "y": 4, "facing": "S"}, "iso": None}
    db.execute("INSERT INTO save VALUES (7, 'auto', 5, ?, '2026-11-08T10:00:00Z')", (json.dumps(save),))
    db.execute("INSERT INTO flag VALUES (7, 'ilatea.intro.seen', 'true')")
    db.close()
    st = GameStore(path)
    assert st.version == SCHEMA_VERSION == 3
    assert st.saves(7) == [{"slot": "auto", "rev": 5, "updated_at": "2026-11-08T10:00:00Z", "data": save}]
    assert st.flags(7) == {"ilatea.intro.seen": True}
    assert st.quests(7, "netbox") == {} and st.rewards(7) == {"xp": 0, "potions": 0, "tools": [], "repeats": {}}
    row = st.put_quest(7, "netbox", {"quest_id": "q:x", "site_id": 1, "status": "parked", "answers": {"peer": {"choice": "unknown"}},
                                     "checks": ["x"], "verified": [], "snapshot": {"id": "q:x"}})
    assert st.quests(7, "netbox")["q:x"] == row
    st.add_reward(7, "q:x", "xp", 120, "device_unconnected:1")
    st.add_reward(7, "q:y", "xp", 96, "device_unconnected:1")
    st.add_reward(7, "q:x", "tool", 1, "tool", item="cable_tracer")
    st.add_reward(7, "q:x", "potion", 1, "level")
    assert st.rewards(7) == {"xp": 216, "potions": 1, "tools": ["cable_tracer"], "repeats": {"device_unconnected:1": 2}}
    st.close()


def test_a_damaged_quest_row_is_left_out_not_served(tmp_path):
    st = GameStore(tmp_path / "game.sqlite3")
    st.upsert_player(7, "walker", None)
    st.put_quest(7, "netbox", {"quest_id": "q:ok", "site_id": 1, "status": "done", "answers": {}, "checks": [], "verified": [], "snapshot": {}})
    st._db.execute("INSERT INTO quest VALUES (7, 'netbox', 'q:bad', 1, 'done', '{not json', '[]', '[]', '{}', 'now')")
    assert list(st.quests(7, "netbox")) == ["q:ok"]


# ---------------------------------------------------------------- the change ops a quest line ends in
def ops(*raw: dict) -> list:
    return c.ChangeSet.model_validate({"ops": list(raw)}).ops


def end(kind: str, i: int, dev: int = 1, cable=None, name="eth1") -> changes.Rec:
    return changes.Rec("end:" + kind, i, None, {"cable": cable, "name": name, "device_id": dev, "device": f"d{dev}"})


def state(*recs: changes.Rec) -> changes.State:
    st = changes.State()
    for r in recs:
        st.add(r)
    return st


def cable_op(a=("interface", 1), b=("interface", 2), **f) -> dict:
    return {"op": "connect_cable", "target": {"kind": "cable", "id": None},
            "fields": {"a_kind": a[0], "a_id": a[1], "b_kind": b[0], "b_id": b[1], **f}}


def test_connect_cable_is_planned_and_refuses_a_port_that_has_one():
    st = state(end("interface", 1), end("interface", 2, dev=2), end("interface", 3, dev=3, cable=44))
    ok, taken, same, twice = changes.plan(ops(cable_op(type="cat6"), cable_op(b=("interface", 3)), cable_op(a=("interface", 9)),
                                              cable_op()), st)
    assert (ok.status, ok.after) == ("ok", {"a_kind": "interface", "a_id": 1, "b_kind": "interface", "b_id": 2, "type": "cat6"})
    assert (taken.status, taken.error.code, taken.error.details) == ("error", "conflict", {"field": "a_id", "cable_id": "#0"})
    assert (same.status, same.error.code) == ("error", "not_found")
    assert twice.error.message == "d1 eth1 already has a cable (NetBox: one cable per port)."
    mixed = changes.plan(ops(cable_op(a=("power_port", 5))), state(end("power_port", 5), end("interface", 2)))[0]
    assert mixed.error.message == "A power port connects only to a power outlet, not to a data port."


def test_a_new_circuit_gets_an_end_at_a_site_and_a_cable_in_one_set():
    st = state(site(1), end("interface", 1), changes.Rec("provider", 3, None, {"name": "P"}), changes.Rec("circuit_type", 4, None, {"name": "T"}))
    planned = changes.plan(ops(
        {"op": "create_circuit", "target": {"kind": "circuit", "id": None}, "client_ref": "c", "fields": {"cid": "X-1", "provider_id": 3, "type_id": 4}},
        {"op": "create_circuit_termination", "target": {"kind": "circuit_termination", "id": None}, "client_ref": "t",
         "fields": {"circuit_id": "@c", "term_side": "A", "site_id": 1}},
        cable_op(b=("circuit_termination", "@t")),
        {"op": "create_circuit_termination", "target": {"kind": "circuit_termination", "id": None}, "fields": {"circuit_id": "@c", "term_side": "A", "site_id": 1}},
        {"op": "create_circuit", "target": {"kind": "circuit", "id": None}, "fields": {"cid": "X-1", "provider_id": 3, "type_id": 4}},
        {"op": "create_circuit", "target": {"kind": "circuit", "id": None}, "fields": {"cid": "X-2", "provider_id": 8, "type_id": 4}}), st)
    assert [p.status for p in planned] == ["ok", "ok", "ok", "error", "error", "error"]
    assert planned[2].write["b_id"] == "@t" and planned[1].write["circuit_id"] == "@c"
    assert [p.error.code for p in planned[3:]] == ["conflict", "validation_failed", "not_found"]


def test_assign_ip_reuses_the_address_on_that_port_and_refuses_one_held_elsewhere():
    ip = changes.Rec("ip_address", 5, None, {"address": "192.0.2.9/24", "assigned_type": "dcim.interface", "assigned_id": 1})
    st = state(site(1), device(1), end("interface", 1), end("interface", 2, dev=2), ip)

    def op(address: str, iface: int = 1) -> dict:
        return {"op": "assign_ip", "target": {"kind": "device", "id": 1}, "fields": {"address": address, "interface_id": iface}}
    reuse, fresh, clash, other, bad = (changes.plan(ops(o), st)[0] for o in (op("192.0.2.9/24"), op("192.0.2.10/24"), op("192.0.2.9/25"),
                                                                           op("192.0.2.11/24", 2), op("192.0.2.300/24")))
    assert (reuse.status, reuse.write["ip_id"], reuse.after["primary_ip"]) == ("ok", 5, "192.0.2.9/24")
    assert (fresh.status, fresh.write["ip_id"], fresh.write["family"]) == ("ok", None, 4)
    assert (clash.error.code, other.error.details, bad.error.details) == ("conflict", {"field": "interface_id"}, {"field": "address"})


def test_create_prefix_needs_a_network_that_does_not_exist_yet():
    st = state(changes.Rec("prefix", 1, None, {"prefix": "192.0.2.0/24"}))
    mk = lambda p: changes.plan(ops({"op": "create_prefix", "target": {"kind": "prefix", "id": None}, "fields": {"prefix": p}}), st)[0]  # noqa: E731
    assert mk("198.51.100.0/24").status == "ok" and mk("192.0.2.0/24").error.code == "conflict"
    assert mk("198.51.100.7/24").error.message.startswith("'198.51.100.7/24' is not a network")


def test_the_new_fields_follow_netbox_limits():
    st = state(site(1), device(1), changes.Rec("platform", 2, None, {"name": "OS"}))
    upd = lambda kind, **f: changes.plan(ops({"op": "update_fields", "target": {"kind": kind, "id": 1}, "fields": f}), st)[0]  # noqa: E731
    assert upd("site", facility="DC-1", country="NL").status == "ok"
    assert upd("site", country="nl").error.details["field"] == "country" and upd("site", facility="x" * 51).status == "error"
    assert upd("device", platform_id=2).status == "ok" and upd("device", platform_id=9).error.code == "not_found"


# ---------------------------------------------------------------- the player's own NetBox permissions
def netbox_with(users: dict, perm_rows: list[dict], refuse: str | None = None) -> NetBox:
    def handler(req: httpx.Request) -> httpx.Response:
        path, q = req.url.path, dict(req.url.params)
        if refuse and refuse in path:
            return httpx.Response(403, json={"detail": "You do not have permission to perform this action."})
        if path.startswith("/api/users/users/") and path != "/api/users/users/":
            uid = int(path.split("/")[-2])
            return httpx.Response(200, json=users[uid]) if uid in users else httpx.Response(404, json={})
        if path == "/api/users/users/":
            hit = [u for i, u in users.items() if str(i) == q.get("id") and u.get("_super")]
            return httpx.Response(200, json={"count": len(hit), "results": hit})
        if path == "/api/users/permissions/":
            rows = [r for r in perm_rows if (q.get("user_id") and int(q["user_id"]) in r.get("users", []))
                    or (q.get("group_id") and int(q["group_id"]) in r.get("groups", []))]
            rows = [r for r in rows if r.get("enabled", True)]
            return httpx.Response(200, json={"count": len(rows), "results": rows})
        return httpx.Response(404, json={})
    return NetBox("http://netbox.invalid", "nbt_x.y", transport=httpx.MockTransport(handler))


USERS = {1: {"id": 1, "is_active": True, "groups": [], "_super": True}, 2: {"id": 2, "is_active": True, "groups": [{"id": 5}]},
         3: {"id": 3, "is_active": False, "groups": []}}
ROWS = [{"object_types": ["dcim.device"], "actions": ["view", "change"], "constraints": None, "users": [2]},
        {"object_types": ["dcim.cable"], "actions": ["add"], "constraints": None, "groups": [5]},
        {"object_types": ["dcim.site"], "actions": ["change"], "constraints": {"slug": "north"}, "users": [2]},
        {"object_types": ["dcim.rack"], "actions": ["change"], "constraints": None, "users": [2], "enabled": False},
        {"object_types": ["ipam.prefix"], "actions": ["add"], "constraints": None, "users": [3]}]
SERIAL = {"op": "update_fields", "target": {"kind": "device", "id": 1}, "fields": {"serial": "x"}}


def test_permissions_come_from_the_players_own_grants_and_groups():
    asyncio.run(_grants_and_groups())


async def _grants_and_groups():
    r = perms.PermissionReader(netbox_with(USERS, ROWS))
    assert (await r.of(1)).missing(perms.needs_of([cable_op(), SERIAL])) == []          # a superuser
    p = await r.of(2)
    assert p.missing(perms.needs_of([SERIAL, cable_op()])) == []                           # own grant, and one through a group
    site_op = {"op": "update_fields", "target": {"kind": "site", "id": 1}, "fields": {"facility": "x"}}
    rack_op = {"op": "move_rack", "target": {"kind": "rack", "id": 1}, "fields": {"iso_x": 1}}
    assert p.missing(perms.needs_of([site_op, rack_op])) == [
        {"object_type": "dcim.site", "action": "change", "constrained": True},              # constraints cannot be checked: refused
        {"object_type": "dcim.rack", "action": "change", "constrained": False}]             # a disabled permission does not count
    err = perms.refusal(p.missing(perms.needs_of([site_op])))
    assert (err.status, err.code, err.details["reason"]) == (403, "forbidden", "player_lacks_permission")
    assert "A permission with constraints does not count here" in err.message
    # an inactive user may do nothing, whatever is still assigned to them
    prefix = {"op": "create_prefix", "target": {"kind": "prefix", "id": None}, "fields": {"prefix": "192.0.2.0/24"}}
    assert (await r.of(3)).missing(perms.needs_of([prefix])) == [{"object_type": "ipam.prefix", "action": "add", "constrained": False}]
    assert perms.needs_of([{"op": "assign_ip", "target": {"kind": "device", "id": 1}, "fields": {}}]) == [("ipam.ipaddress", "add"), ("dcim.device", "change")]


@pytest.mark.parametrize("refuse,reason,health", [("users/users", "proxy_cannot_view_users", "cannot_view_users"),
                                                  ("users/permissions", "proxy_cannot_view_permissions", "cannot_view_permissions"),
                                                  (None, None, "ok")])
def test_a_proxy_that_cannot_read_users_or_permissions_refuses_writes_and_says_so_in_health(refuse, reason, health):
    asyncio.run(_blocked_proxy(refuse, reason, health))


async def _blocked_proxy(refuse, reason, health):
    client = netbox_with(USERS, ROWS, refuse)
    assert await perms.check_health(client) == health
    if reason:
        with pytest.raises(ApiError) as e:
            await perms.PermissionReader(client).of(2)
        assert (e.value.code, e.value.details) == ("netbox_error", {"reason": reason})


def put_island_in_order(cl) -> None:
    for _ in range(12):
        b = board(cl)
        if b["all_in_order"]:
            return
        q = next(x for x in b["quests"] if x["status"] in ("offered", "active"))
        for _ in range(10):
            step = q["step"]
            if q["status"] not in ("offered", "active"):
                break
            if step["input"] == "choice":
                pick = next((x for x in step["choices"] if x["kind"] in ("candidate", "confirm", "skip")), None) or \
                    next(x for x in step["choices"] if x["id"] in ("new", "typed"))
                q = answer(cl, q["id"], step["id"], choice=pick["id"])
            elif step["input"] == "text":
                q = answer(cl, q["id"], step["id"], text="EXF-1")
            else:
                q = answer(cl, q["id"], step["id"], values={f["id"]: (f["options"][0]["id"] if f["type"] == "select" else 5 if f["type"] == "number"
                                                                      else f"PI-{i:04d}") for i, f in enumerate(step["fields"], 7)})
        apply(cl, q["id"])


def test_a_site_in_order_gets_spot_checks_not_invented_work(cl):
    put_island_in_order(cl)
    b = board(cl)
    assert b["all_in_order"] and b["queued"] == 0 and b["next_quest_id"] is None and b["sites"][0]["open_quests"] == 0
    spot = [q for q in b["quests"] if q["status"] == "offered"]
    assert [(q["kind"], q["title"], q["xp"], q["minutes"]) for q in spot] == [("spot_check", "Spot check: sw-quay-1", 0, 2)]
    assert spot[0]["step"]["prompt"] == "Go and look: is sw-quay-1 still in rack PI-Q1 U10 in Quay Room, serial SYN-PI-0001?"
    assert [c["label"] for c in spot[0]["step"]["choices"]] == ["Yes, exactly as recorded", "No, something has changed", "I don't know yet"]
    # "no" is a field task: correct it, then come back
    q = answer(cl, spot[0]["id"], "same", choice="no")
    assert q["status"] == "parked" and q["note"] == "You found that sw-quay-1 no longer matches its record."
    # "yes" closes it with nothing written and no XP; the next spot check is another device
    q = answer(cl, spot[0]["id"], "same", choice="yes")
    assert q["status"] == "done" and q["proposal"] is None
    after = board(cl)
    assert after["rewards"]["xp"] == 0 and after["all_in_order"]
    assert [x["title"] for x in after["quests"] if x["status"] == "offered"] == ["Spot check: sw-quay-2"]


def test_a_site_offers_different_kinds_of_line_before_a_second_of_one_kind(cl):
    from rackwalker_proxy.questroutes import offer

    def L(kind: str, n: int) -> dict:
        return {"id": f"{kind}:{n}", "kind": kind}
    power = [L("device_no_power", i) for i in range(5)]
    assert [x["id"] for x in offer(power)] == ["device_no_power:0", "device_no_power:1", "device_no_power:2"]
    mixed = [L("interface_no_cable", 1), L("interface_no_cable", 2), L("device_no_power", 1), L("device_no_power", 2), L("site_record", 1)]
    # most valuable first, one of each kind; never more than three
    assert [x["id"] for x in offer(mixed)] == ["interface_no_cable:1", "device_no_power:1", "site_record:1"]
    assert [x["id"] for x in offer(mixed[:3])] == ["interface_no_cable:1", "interface_no_cable:2", "device_no_power:1"]
    # the seed's North Depot has two addressed ports with no cable and four unpowered racks: it offers one of each
    b = board(cl, "netbox", site_id=1)
    assert [q["kind"] for q in b["quests"]] == ["interface_no_cable", "device_no_power", "address_no_prefix"]
    assert b["next_quest_id"] == b["quests"][0]["id"] and b["queued"] == len(b["findings"]) - 3


def play_first(cl, q: dict, scope: str, knows: int = 1) -> dict:
    """Answers a bundle line as a player who knows its first `knows` items and says "I don't know yet" to the rest."""
    known: set[str] = set()
    for _ in range(20):
        if q["status"] not in ("offered", "active"):
            return q
        step = q["step"]
        item = step["id"].rsplit("_", 1)[-1]
        if item not in known and len(known) < knows:
            known.add(item)
        pick = next(x for x in step["choices"] if x["kind"] == ("candidate" if item in known else "unknown"))
        q = answer(cl, q["id"], step["id"], scope=scope, choice=pick["id"])
    raise AssertionError("the line never came to an end")


def test_a_dry_run_of_part_of_an_errand_keeps_every_answer(cl):
    b = board(cl, "netbox", site_id=1)
    q = play_first(cl, next(x for x in b["quests"] if x["kind"] == "device_no_power"), "netbox")
    assert q["status"] == "ready" and len(q["proposal"]["ops"]) == 1 and len(q["said"]) == 2
    assert q["write_blocked"] == "This proxy serves canned fixture data: nothing is written."
    v = apply(cl, q["id"], "netbox")
    # nothing was written, so the line stands exactly where it stood: same answers, same change ready
    assert (v["mode"], v["verified"], v["complete"], v["xp_awarded"]) == ("dry_run", False, False, 0)
    assert v["message"] == "Practice run, nothing was written: This proxy serves canned fixture data: nothing is written. No XP for a practice run."
    assert (v["quest"]["status"], v["quest"]["said"], v["quest"]["proposal"]) == ("ready", q["said"], q["proposal"])
    again = next(x for x in board(cl, "netbox", site_id=1)["quests"] if x["id"] == q["id"])
    assert (again["status"], again["said"], again["proposal"]) == ("ready", q["said"], q["proposal"])


def test_part_of_an_errand_confirmed_on_the_island_goes_on_with_the_rest_only(cl):
    put_island_in_order_until(cl, "site_record")
    q = next(x for x in board(cl)["quests"] if x["kind"] == "site_record")
    assert [f["id"] for f in q["step"]["fields"]] == ["latitude", "longitude", "facility"]
    q = answer(cl, q["id"], "record", values={"facility": "PI-1"})
    v = apply(cl, q["id"])
    assert (v["complete"], v["quest"]["status"], v["quest"]["progress"]) == (False, "active", {"total": 2, "verified": 1})
    # the facility is confirmed and not asked again; the position is still to be found out
    assert [f["id"] for f in v["quest"]["step"]["fields"]] == ["latitude", "longitude"]
    assert v["quest"]["purpose"] == "Practice Island has no GPS position on record, so it cannot be put on a map. Let's fill that in."


def put_island_in_order_until(cl, kind: str) -> None:
    for _ in range(12):
        b = board(cl)
        if any(q["kind"] == kind for q in b["quests"]):
            return
        q = next(x for x in b["quests"] if x["status"] in ("offered", "active"))
        for _ in range(10):
            step = q["step"]
            if q["status"] not in ("offered", "active"):
                break
            if step["input"] == "choice":
                pick = next((x for x in step["choices"] if x["kind"] in ("candidate", "confirm", "skip")), None) or \
                    next(x for x in step["choices"] if x["id"] in ("new", "typed"))
                q = answer(cl, q["id"], step["id"], choice=pick["id"])
            elif step["input"] == "text":
                q = answer(cl, q["id"], step["id"], text="EXF-1")
            else:
                q = answer(cl, q["id"], step["id"], values={f["id"]: f"PI-{i:04d}" for i, f in enumerate(step["fields"], 7)})
        apply(cl, q["id"])
    raise AssertionError(f"no {kind} line was offered")


def test_a_spot_check_is_refused_at_a_site_that_still_has_something_open(cl):
    spot = {"scope": "practice", "quest_id": "q:spot_check:device:9000001:0", "step": "same", "choice": "yes"}
    r = cl.post("/api/game/quests/answer", json=spot)
    assert r.status_code == 409 and r.json()["error"]["code"] == "conflict"
    assert r.json()["error"]["message"] == "A spot check is offered only at a site with nothing open; this site still has things to find out."
    b = board(cl)
    assert b["rewards"]["completed"] == 0 and b["rewards"]["practised"] == 0 and not [q for q in b["quests"] if q["kind"] == "spot_check"]
    # at a site in order the same request is the spot check the giver offers
    put_island_in_order(cl)
    assert cl.post("/api/game/quests/answer", json=spot).json()["status"] == "done"
    # a spot check is answered by the player; "Check NetBox" never settles one
    assert cl.post("/api/game/quests/verify", json={"scope": "netbox", "quest_id": spot["quest_id"]}).status_code == 400


def test_one_answer_can_be_taken_back(cl):
    qid = board(cl)["next_quest_id"]
    for step, a in WAY_OUT:
        q = answer(cl, qid, step, **a)
    assert q["status"] == "ready"
    q = answer(cl, qid, "back")
    assert (q["status"], q["step"]["id"], [s["step"] for s in q["said"]]) == ("active", "cable_type", ["device", "provider", "circuit_id", "circuit_type", "local_port"])
    q = answer(cl, qid, "back")
    assert q["step"]["id"] == "local_port" and len(q["said"]) == 4
    # a parked line: the question it waits at is opened again, the answers before it stay
    q = answer(cl, qid, "local_port", choice="unknown")
    assert q["status"] == "parked"
    q = answer(cl, qid, "back")
    assert (q["status"], q["step"]["id"], len(q["said"])) == ("active", "local_port", 4)
    # with nothing answered there is nothing to take back
    fresh = answer(cl, qid, "restart")
    assert answer(cl, qid, "back")["step"] == fresh["step"]
    other = next(x for x in board(cl)["quests"] if x["status"] == "offered")
    assert answer(cl, other["id"], "back")["status"] == "offered"
    assert next(x for x in board(cl)["quests"] if x["id"] == other["id"])["status"] == "offered"   # a line never begun stays on offer


def test_a_closed_line_is_offered_again_only_for_something_it_never_covered():
    from rackwalker_proxy import findings, quests
    from rackwalker_proxy.questroutes import Standing, base_checks

    from .test_quests import Build, linked_pair

    b = Build()
    s = b.site()
    room = b.room(s)
    rack = b.rack(s, room)
    r, sw = linked_pair(b, s, room, rack)
    b.e.devices[sw]["serial"] = ""
    a = findings.analyse(b.e)
    line = next(x for x in quests.lines(b.e, a) if x["kind"] == "rack_audit")
    snap = {"kind": line["kind"]}
    done = {line["id"]: {"status": "done", "checks": quests.check_ids(line), "verified": quests.check_ids(line), "answers": {}, "snapshot": snap}}
    # the serial the player once recorded was removed in NetBox: counted elsewhere as undone, never reopened
    st = Standing(a, done)
    assert st.closed(line) and st.open_lines(quests.lines(b.e, a), s) == [] and not st.in_order(quests.lines(b.e, a), s)
    # a new device with no serial is put in the audited rack: the line is offered again, and stands for both devices
    new = b.dev(s, "sw-new", "switch", room, rack, serial="")
    b.cable(b.p(sw, "eth2"), b.p(new, "eth1"))
    b.e.touch()
    a = findings.analyse(b.e)
    line = next(x for x in quests.lines(b.e, a) if x["kind"] == "rack_audit")
    assert not Standing(a, done).closed(line)
    assert base_checks(line, done[line["id"]]) == [f"device:{sw}:serial", f"device:{new}:serial"]


def test_a_device_nobody_logs_in_to_counts_as_answered_for_that_player():
    from rackwalker_proxy import findings, quests
    from rackwalker_proxy.questroutes import Standing

    from .test_quests import device_unreachable

    e = device_unreachable()
    a = findings.analyse(e)
    line = next(x for x in quests.lines(e, a) if x["kind"] == "device_unreachable")
    ids = line["finding"]["data"]["device_ids"]
    site = line["site_id"]
    before = a.completeness(site)
    rows = {line["id"]: {"status": "active", "checks": quests.check_ids(line), "verified": [], "snapshot": {"kind": line["kind"]},
                         "answers": {f"ip_{ids[0]}": {"choice": "none"}}}}
    st = Standing(a, rows)
    assert st.resolved == {f"device:{ids[0]}:primary_ip"}
    assert a.completeness(site, st.resolved)["open"] == before["open"] - 1


def test_every_finding_kind_is_in_the_admin_docs_table():
    from pathlib import Path

    from rackwalker_proxy import findings

    text = (Path(__file__).resolve().parents[2] / "docs" / "quests.md").read_text()
    for kind in findings.KIND_ORDER:
        assert f"| `{kind}` |" in text, f"docs/quests.md does not describe {kind}"
