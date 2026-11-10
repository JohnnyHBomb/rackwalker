"""Rules of the quest log that every other suite could break without a red test: which facts are asked for, what a
check confirms and pays, what a dry run leaves alone, what a permission grants, what the store keeps of an older save,
and which checks belong to which chain. Each test states one rule and plays it on a made-up NetBox."""
from __future__ import annotations

import asyncio
import json
import sqlite3

import pytest

from rackwalker_proxy import chains, findings, perms, quests
from rackwalker_proxy.gamestore import MIGRATIONS, GameStore, migrate

from .test_chains import ALL, CF_CIRCUIT, HANDOFF, RECORD, WAY_OUT, managed, mill_lane, office, old_row, say
from .test_estate_read import tiny
from .test_quest_routes import USERS, netbox_with
from .test_quest_settlement import board, post, start, wire
from .test_quests import Build, circuit_handoff, circuit_record, linked_pair, one, router_no_address
from .test_scanner_rules import stack

FULL_RECORD = (("network", {"choice": "new"}), ("network_name", {"text": "Northlight Internet"}),
               ("account", {"choice": "new"}), ("account_no", {"text": "44-1021"}), ("contact_for", {"choice": "provider"}),
               ("contact", {"values": {"contact:support:name": "NOC", "contact:account:name": "AM", "contact:billing:name": "Bills"}}),
               ("terms", {"values": {"install_date": "2024-03-01", "commit_rate": 100}}),
               ("cost", {"values": {"mrc": 10, "nrc": 20, "currency": "gbp"}}))


def play_two_stages(cl):
    say(cl, WAY_OUT, ("device", {"choice": "device:1"}), ("provider", {"choice": "provider:1"}), ("circuit_id", {"text": "NLT-7001"}),
        ("circuit_type", {"choice": "type:1"}), ("local_port", {"choice": "interface:11"}), ("cable_type", {"choice": "smf"}))
    assert post(cl, "apply", WAY_OUT)["xp_awarded"] == 120
    say(cl, HANDOFF, ("has", {"choice": "typed"}), ("addr", {"text": "203.0.113.2/30"}), ("prefix", {"choice": "yes"}))
    assert post(cl, "apply", HANDOFF)["xp_awarded"] == 60


def test_a_dry_run_of_the_last_stage_pays_no_chain_bonus(tmp_path, monkeypatch):
    cl, box = office(tmp_path, monkeypatch)
    play_two_stages(cl)
    box.permissions = []                                   # the player's NetBox permissions are taken away
    q = say(cl, RECORD, *FULL_RECORD)
    assert q["status"] == "ready"
    v = post(cl, "apply", RECORD)
    assert v["mode"] == "dry_run" and not v["verified"]
    assert (v["xp_awarded"], v["chain_xp"], v["rewards"]["xp"]) == (0, 0, 180)
    assert board(cl)["rewards"]["xp"] == 180


def test_a_dry_run_first_does_not_cost_the_xp_of_the_real_write(tmp_path, monkeypatch):
    cl, box = office(tmp_path, monkeypatch)
    box.permissions = []
    say(cl, WAY_OUT, ("device", {"choice": "device:1"}), ("provider", {"choice": "provider:1"}), ("circuit_id", {"text": "NLT-7001"}),
        ("circuit_type", {"choice": "type:1"}), ("local_port", {"choice": "interface:11"}), ("cable_type", {"choice": "smf"}))
    v = post(cl, "apply", WAY_OUT)
    assert (v["mode"], v["xp_awarded"], v["quest"]["status"]) == ("dry_run", 0, "practised")
    assert v["quest"]["progress"]["verified"] == 0
    box.permissions = ALL
    v = post(cl, "apply", WAY_OUT)
    assert (v["mode"], v["verified"], v["complete"], v["xp_awarded"]) == ("real", True, True, 120)


def test_chain_bonus_by_share_recorded_in_the_ledger_and_paid_once(tmp_path, monkeypatch):
    """The physical stage is already in NetBox; the player plays the logical and administrative stages: 2 of 3."""
    data = mill_lane()
    data["circuits/circuits"].append({"id": 1, "cid": "NLT-7001", "provider": {"id": 1}, "type": {"id": 1, "name": "Internet"}, "status": {"value": "active"},
                                      "provider_account": None, "install_date": None, "commit_rate": None, "custom_fields": dict(CF_CIRCUIT)})
    data["circuits/circuit-terminations"].append({"id": 1, "circuit": {"id": 1}, "term_side": "A", "termination_type": "dcim.site", "termination_id": 1,
                                                  "cable": {"id": 1}})
    data["dcim/cables"].append({"id": 1, "type": "smf", "label": "", "status": {"value": "connected"},
                                "a_terminations": [{"object_type": "dcim.interface", "object_id": 11}],
                                "b_terminations": [{"object_type": "circuits.circuittermination", "object_id": 1}]})
    next(p for p in data["dcim/interfaces"] if p["id"] == 11)["cable"] = {"id": 1}
    cl, box = office(tmp_path, monkeypatch, data)
    b = board(cl)
    chain = next(c for c in b["chains"] if c["id"] == "chain:way_out:site:1")
    assert chain["label"] == "2 of 3: logical"
    say(cl, HANDOFF, ("has", {"choice": "typed"}), ("addr", {"text": "203.0.113.2/30"}), ("prefix", {"choice": "yes"}))
    v = post(cl, "apply", HANDOFF)
    assert (v["xp_awarded"], v["chain_xp"]) == (60, 0)
    assert say(cl, RECORD, *FULL_RECORD)["status"] == "ready"
    v = post(cl, "apply", RECORD)
    assert v["complete"] and v["chain"]["status"] == "complete"
    assert v["chain_xp"] == round(250 * 2 / 3) == 167
    assert v["xp_awarded"] == 120 + 167
    assert v["rewards"]["xp"] == 60 + 120 + 167   # the ledger holds the bonus
    assert board(cl)["rewards"]["xp"] == 60 + 120 + 167
    assert v["potions_awarded"] == 1 and v["rewards"]["potions"] == 1   # 180 -> 347 crosses 250 once: X18, X42, X44
    assert board(cl)["rewards"]["potions"] == 1


def test_managed_locally_with_no_address_is_not_answered():
    b, s, ap1, ap2 = managed()
    line = one(b.e, "device_unreachable")
    assert quests.waived(line, {f"mode_{ap1}": {"choice": "local"}}) == []
    assert quests.waived(line, {f"mode_{ap1}": {"choice": "cloud"}}) == [f"device:{ap1}:primary_ip"]


def test_a_permission_grants_only_the_actions_it_names():
    async def go():
        rows = [{"object_types": ["dcim.device"], "actions": ["view"], "constraints": None, "users": [2]},
                {"object_types": ["dcim.cable"], "actions": ["change"], "constraints": None, "users": [2]}]
        p = await perms.PermissionReader(netbox_with(USERS, rows)).of(2)
        assert [(m["object_type"], m["action"]) for m in p.missing([("dcim.device", "change"), ("dcim.cable", "add"), ("dcim.device", "add")])] == [
            ("dcim.device", "change"), ("dcim.cable", "add"), ("dcim.device", "add")]
    asyncio.run(go())


def test_each_updated_kind_needs_change_on_its_own_type():
    def need(kind):
        return perms.needs_of([{"op": "update_fields", "target": {"kind": kind, "id": 1}, "fields": {}}])
    assert [need(k) for k in ("site", "location", "rack", "device", "cable", "interface")] == [
        [("dcim.site", "change")], [("dcim.location", "change")], [("dcim.rack", "change")], [("dcim.device", "change")],
        [("dcim.cable", "change")], [("dcim.interface", "change")]]


def v3_file(tmp_path):
    path = tmp_path / "game.sqlite3"
    db = sqlite3.connect(path, isolation_level=None)
    migrate(db, MIGRATIONS[:3])
    db.execute("INSERT INTO player VALUES (1, 'walker', NULL, 'x', 'x')")
    return path, db


EV = [{"kind": "device", "id": i, "name": f"sw{i}"} for i in (2, 3, 4, 5)]
CHECKS = [f"device:{i}:primary_ip" for i in (2, 3, 4, 5)]


def test_a_parked_dont_know_stays_parked_and_is_not_made_local(tmp_path):
    path, db = v3_file(tmp_path)
    old_row(db, "q:device_unreachable:site:1", "parked", {"ip_2": {"choice": "unknown"}}, CHECKS, ["device:4:primary_ip"], EV)
    db.close()
    rows = GameStore(path).quests(1, "netbox")
    two, four = rows["q:device_unreachable:device:2"], rows["q:device_unreachable:device:4"]
    assert (two["status"], two["answers"]) == ("parked", {"ip_2": {"choice": "unknown"}})
    assert (two["verified"], four["verified"], four["status"]) == ([], ["device:4:primary_ip"], "done")
    assert two["updated_at"] == "2026-11-09T10:00:00Z"
    snap = two["snapshot"]
    assert (snap["id"], snap["xp"], snap["finding"]["id"]) == ("q:device_unreachable:device:2", 40, "device_unreachable:device:2")
    assert snap["finding"]["evidence"] == [{"kind": "device", "id": 2, "name": "sw2"}]
    assert snap["finding"]["title"] == "NetBox does not say how sw2 is managed, or how to reach it."


def test_practice_rows_are_migrated_in_their_own_scope(tmp_path):
    path, db = v3_file(tmp_path)
    snap = {"id": "q:device_unreachable:site:9000001", "kind": "device_unreachable", "rank": "connectivity", "site_id": 9000001, "title": "t",
            "purpose": "p", "outcome": "o", "xp": 120, "minutes": 4,
            "finding": {"id": "device_unreachable:site:9000001", "kind": "device_unreachable", "rank": "connectivity", "site_id": 9000001, "title": "t",
                        "evidence": [{"kind": "device", "id": 9000003, "name": "sw-loft-1"}], "data": {}}}
    db.execute("INSERT INTO quest VALUES (1, 'practice', ?, 9000001, 'active', ?, ?, '[]', ?, '2026-11-09T10:00:00Z')",
               (snap["id"], json.dumps({"ip_9000003": {"choice": "none"}}), json.dumps(["device:9000003:primary_ip"]), json.dumps(snap)))
    db.close()
    st = GameStore(path)
    assert st.quests(1, "netbox") == {}
    assert list(st.quests(1, "practice")) == ["q:device_unreachable:device:9000003"]


@pytest.mark.parametrize("answers", ["not json", "[]"])
def test_a_damaged_row_does_not_stop_the_store_from_opening(tmp_path, answers):
    path, db = v3_file(tmp_path)
    old_row(db, "q:device_unreachable:site:1", "active", {"ip_2": {"choice": "none"}}, CHECKS, [], EV)
    db.execute("INSERT INTO player VALUES (2, 'other', NULL, 'x', 'x')")
    db.execute("INSERT INTO quest VALUES (2, 'netbox', 'q:device_unreachable:site:1', 1, 'active', ?, '[]', '[]', '{}', '2026-11-09T10:00:00Z')", (answers,))
    db.close()
    st = GameStore(path)
    assert st.version == 4 and list(st.quests(1, "netbox")) == ["q:device_unreachable:device:2"]


def test_a_check_undone_and_redone_under_another_line_pays_nothing(tmp_path, monkeypatch):
    from .test_estate_read import ref, tiny
    from .test_quest_settlement import line_of, start, wire
    data = tiny()
    data["dcim/racks"].append({**data["dcim/racks"][0], "id": 2, "name": "R2"})
    data["dcim/devices"][0]["serial"] = ""
    wire(data)
    cl, box, app = start(tmp_path, monkeypatch, data)
    post(cl, "answer", "q:rack_audit:rack:1", step="labels", values={})
    data["dcim/devices"][0]["rack"] = ref(2)
    board(cl, fresh=True)
    data["dcim/devices"][0]["serial"] = "SN-1"
    assert post(cl, "verify", "q:rack_audit:rack:2")["xp_awarded"] == 50
    # undone in NetBox, and the device goes back to rack 1: the same check comes up under another line
    data["dcim/devices"][0]["serial"] = ""
    data["dcim/devices"][0]["rack"] = ref(1)
    b = board(cl, fresh=True)
    assert line_of(b, "q:rack_audit:rack:1") is not None
    post(cl, "answer", "q:rack_audit:rack:1", step="labels", values={})
    data["dcim/devices"][0]["serial"] = "SN-1"
    v = post(cl, "verify", "q:rack_audit:rack:1")
    assert (v["complete"], v["xp_awarded"]) == (True, 0)
    assert app.state.game_store().rewards(9)["xp"] == 50


def test_set_management_does_not_write_over_a_newer_device():
    from .plan_helpers import TS, device, state
    from .test_chains import planned
    dev = device(5, _has_mgmt=True, _has_default=True, _mgmt_mode=None, _mgmt_service=None, _mgmt_default=None, _type_id=3, _model="AP-22")
    op = {"op": "set_management", "target": {"kind": "device", "id": 5}, "fields": {"mode": "cloud"}, "if_unmodified_since": "2020-01-01T00:00:00Z"}
    out = planned(state(dev), op)[0]
    assert out["status"] == "error" and out["error"]["code"] == "conflict", out
    assert planned(state(dev), {**op, "if_unmodified_since": TS})[0]["status"] == "ok"


def test_the_management_line_stamps_its_op():
    b, s, ap1, ap2 = managed()
    b.e.devices[ap1]["last_updated"] = "2026-11-01T00:00:00Z"
    line = one(b.e, "device_unreachable")
    st = quests.evaluate(b.e, line, {f"mode_{ap1}": {"choice": "unmanaged"}, f"model_{ap1}": {"choice": "varies"}})
    assert st["status"] == "ready" and st["proposal"]["ops"][0]["op"] == "set_management"
    assert st["proposal"]["ops"][0].get("if_unmodified_since") == "2026-11-01T00:00:00Z"


def test_chain_stages_hold_the_checks_the_docs_name():
    from rackwalker_proxy import practice
    e = practice.example_estate()
    a = findings.analyse(e)
    by_id = {c["id"]: c for c in chains.build(e, a, quests.lines(e, a))}
    made = {c for checks in a.checks.values() for c in checks}
    for c in by_id.values():
        stages = {s["stage"]: set(s["checks"]) for s in c["stages"]}
        oid = c["object"]["id"]
        if c["kind"] == "rack":
            inside = [d["id"] for d in e.devices.values() if d["rack_id"] == oid]
            want_p = {f"rack:{oid}:location", f"rack:{oid}:iso", f"rack_devices_unpositioned:rack:{oid}", *(f"device:{i}:power" for i in inside)} & made
            want_a = {f"device:{i}:{f}" for i in inside for f in ("serial", "asset_tag")} & made
            assert stages.get("physical", set()) == want_p, (c["id"], "physical")
            assert stages.get("administrative", set()) == want_a, (c["id"], "administrative")
        if c["kind"] == "device":
            ports = {x for x in made if x.startswith("interface:") and x.endswith(":cable") and e.ports[("interface", int(x.split(":")[1]))]["device_id"] == oid}
            want_p = ({f"device_unconnected:device:{oid}", f"switch_no_uplink:device:{oid}"} & made) | ports
            want_l = {f"router_no_address:device:{oid}", f"device:{oid}:primary_ip"} & made
            assert stages.get("physical", set()) == want_p, (c["id"], "physical")
            assert stages.get("logical", set()) == want_l, (c["id"], "logical")
    kinds = [c["kind"] for c in by_id.values()]
    assert "rack" in kinds and "device" in kinds


# ---------------------------------------------------------------- gap rules
def test_a_stack_one_of_whose_members_says_how_it_is_managed_is_not_asked():
    b, s, sw, member = stack()
    for d in (sw, member):
        b.e.devices[d].update(vc_id=5, primary_ip=None)
    b.e.devices[member]["mgmt_mode"] = "cloud"
    b.e.touch()
    a = findings.analyse(b.e)
    assert [g["id"] for g in a.gaps if g["field"] == "primary_ip"] == []
    assert not [f for f in a.findings if f["kind"] == "device_unreachable"]


def test_a_commit_rate_of_zero_is_on_record():
    e = circuit_record()
    cid = next(iter(e.circuits))
    e.circuits[cid]["commit_rate"] = 0
    assert findings.circuit_facts(e, cid)["commit_rate"] is True


def test_no_charge_is_asked_for_when_netbox_has_no_currency_field():
    e = circuit_record()
    cid = next(iter(e.circuits))
    e.no_fields.append("rw_currency")
    facts = findings.circuit_facts(e, cid)
    assert "mrc" not in facts and "nrc" not in facts


def test_an_unreadable_address_list_raises_no_missing_address():
    e = circuit_handoff()
    assert [f["kind"] for f in findings.analyse(e).findings] == ["circuit_handoff"]
    e.unreadable.append("ips")
    e.touch()
    a = findings.analyse(e)
    cid = next(iter(e.circuits))
    assert "circuit_handoff" not in [f["kind"] for f in a.findings]
    assert a.check(f"circuit_handoff:circuit:{cid}") is None


def test_a_site_in_an_estate_with_no_carrier_at_all_has_no_way_out_check():
    b = Build()
    s = b.site()
    linked_pair(b, s)
    a = findings.analyse(b.e)
    assert a.check(f"site_no_way_out:site:{s}") is None


def test_what_cannot_be_read_is_not_asked_for():
    for lists, gone in ((["accounts"], {"account"}), (["contacts"], {"contact_support", "contact_account", "contact_billing"})):
        e = circuit_record()
        cid = next(iter(e.circuits))
        before = set(findings.circuit_facts(e, cid))
        assert gone <= before
        e.unreadable.extend(lists)
        e.touch()
        assert set(findings.circuit_facts(e, cid)) == before - gone, lists


def test_a_router_with_no_address_is_asked_for_that_only_and_its_check_stays_open():
    e = router_no_address()
    a = findings.analyse(e)
    r2 = next(i for i, d in e.devices.items() if d["name"] == "rtr2")
    assert [f["kind"] for f in a.findings] == ["router_no_address"]
    assert a.check(f"router_no_address:device:{r2}") is False
    assert a.check(f"device:{r2}:primary_ip") is None


def test_a_circuits_address_and_record_are_reported_where_it_plugs_in():
    b = Build()
    first, second = b.site("First"), b.site("Second")
    linked_pair(b, first)
    r2 = b.dev(second, "s2-rtr1", "router")
    b.ip(b.p(r2, "eth2"), "192.0.2.9/24")
    c = b.circuit("C-2")
    b.cable(b.p(r2, "eth3"), b.term(c, "A", second))
    a = findings.analyse(b.e)
    got = {f["kind"]: f["site_id"] for f in a.findings if f["kind"] in ("circuit_handoff", "circuit_record")}
    assert got == {"circuit_handoff": second, "circuit_record": second}
    assert a.check(f"circuit_handoff:circuit:{c}", second) is False and f"circuit_handoff:circuit:{c}" not in a.checks.get(first, {})


# ---------------------------------------------------------------- chains
def test_the_bonus_of_each_chain_kind_is_the_documented_one():
    # docs/quests.md: "250 XP for a way out or a link between sites, 150 for a device, 100 for a rack"
    assert chains.CHAIN_XP == {"way_out": 250, "site_link": 250, "room_link": 0, "device": 150, "rack": 100}


def two_sites_and_circuits():
    """Site one: a router with an internet circuit plugged in (C-OUT), a circuit to site two plugged in at both ends (C-LINK),
    a second internet circuit plugged in (C-OUT2), and one loose internet circuit end (C-LOOSE)."""
    b = Build()
    one_, two = b.site("One"), b.site("Two")
    r1, s1 = linked_pair(b, one_)
    r1b = b.dev(one_, "rtr1b", "router", ports=6)
    b.ip(b.p(r1b, "eth6"), "192.0.2.50/24")
    b.cable(b.p(r1b, "eth5"), b.p(s1, "eth2"))
    r2 = b.dev(two, "rtr2", "router")
    b.ip(b.p(r2, "eth2"), "192.0.2.60/24")
    out = b.circuit("C-OUT")
    b.cable(b.p(r1, "eth3"), b.term(out, "A", one_))
    link = b.circuit("C-LINK")
    b.cable(b.p(r1, "eth4"), b.term(link, "A", one_))
    b.cable(b.p(r2, "eth1"), b.term(link, "Z", two))
    out2 = b.circuit("C-OUT2")
    b.cable(b.p(r1b, "eth1"), b.term(out2, "A", one_))
    loose = b.circuit("C-LOOSE")
    b.term(loose, "A", one_)
    return b, one_, two, out, link, out2, loose


def test_which_circuits_are_in_the_way_out_and_which_in_the_link_between_sites():
    b, one_, two, out, link, out2, loose = two_sites_and_circuits()
    e = b.e
    a = findings.analyse(e)
    by_id = {c["id"]: c for c in chains.build(e, a, quests.lines(e, a))}
    way = {s["stage"]: s["checks"] for s in by_id[f"chain:way_out:site:{one_}"]["stages"]}
    circuits_in = lambda checks: sorted({int(c.split(":")[-1] if c.startswith("circuit_") else c.split(":")[1]) for c in checks if c.startswith("circuit")})  # noqa: E731
    # the way out: both internet circuits' addresses and records (C35), the loose internet circuit (C34), never the link (C09)
    assert circuits_in(way["logical"]) == [out, out2]
    assert circuits_in(way["administrative"]) == [out, out2]
    assert f"circuit_unterminated:circuit:{loose}" in way["physical"]
    assert not [c for checks in way.values() for c in checks if c.split(":")[-1] == str(link) or c.startswith(f"circuit:{link}:")]
    # the link between the sites holds the circuit between them and not the loose internet circuit (C16)
    sl = by_id.get(f"chain:site_link:site:{one_}")
    assert sl is not None
    link_checks = [c for s in sl["stages"] for c in s["checks"]]
    assert f"circuit_unterminated:circuit:{loose}" not in link_checks
    assert circuits_in([c for c in link_checks if c.startswith("circuit")]) == [link]


# ---------------------------------------------------------------- what a check confirms and pays, through the routes
def test_a_port_answered_not_in_use_is_not_paid_when_netbox_later_shows_a_cable(tmp_path, monkeypatch):
    from .test_estate_read import cable, plug
    data = tiny()
    wire(data)
    data["dcim/interfaces"][3]["description"] = "printer on the landing"      # sw2 eth2: described, no cable
    cl, box, app = start(tmp_path, monkeypatch, data)
    port = "q:interface_no_cable:device:2"
    assert post(cl, "answer", port, step="peer_22", choice="none")["status"] == "dismissed"
    # somebody else records the cable in NetBox itself
    c = cable(77, 22, 32)
    data["dcim/cables"].append(c)
    plug(data, c, 22, 32)
    v = post(cl, "verify", port)
    assert v["xp_awarded"] == 0 and v["rewards"]["xp"] == 0
    assert board(cl, fresh=True)["rewards"]["xp"] == 0


def test_a_line_whose_check_moved_to_another_line_is_closed_by_check_netbox(tmp_path, monkeypatch):
    from .test_estate_read import ref
    data = tiny()
    data["dcim/racks"].append({**data["dcim/racks"][0], "id": 2, "name": "R2"})
    data["dcim/devices"][0]["serial"] = ""
    wire(data)
    cl, box, app = start(tmp_path, monkeypatch, data)
    post(cl, "answer", "q:rack_audit:rack:1", step="labels", values={})
    data["dcim/devices"][0]["rack"] = ref(2)
    v = post(cl, "verify", "q:rack_audit:rack:1")
    assert v["message"].startswith("This no longer applies:") and v["quest"]["status"] == "dismissed"
    assert "q:rack_audit:rack:1" not in app.state.game_store().quests(9, "netbox")


class Flaky:
    """Mixed into the made-up NetBox: refuses, or breaks on, the n-th write."""
    fail_at = None
    how = "refuse"

    def write(self, req):
        import httpx
        if self.fail_at is not None and (len(self.writes) + 1 == self.fail_at or self.how == "refuse_all"):
            self.writes.append((req.method, req.url.path, {}))
            if self.how == "break":
                raise RuntimeError("the proxy's own code broke in the middle of a write")
            return httpx.Response(400, json={"__all__": ["NetBox says no."]})
        return super().write(req)


def flaky_office(tmp_path, monkeypatch, data=None):
    import httpx
    from fastapi.testclient import TestClient
    from rackwalker_proxy import questroutes
    from rackwalker_proxy.app import Settings, create_app
    from .conftest import LOCAL
    from .test_chains import Office
    from .test_quest_settlement import sign_in

    class FlakyOffice(Flaky, Office):
        pass
    box = FlakyOffice(data or mill_lane(), ALL)
    app = create_app(Settings("http://netbox.invalid", "nbt_x.y", True, None, cache_ttl=0, state_dir=tmp_path, estate_ttl=60),
                     transport=httpx.MockTransport(box))
    monkeypatch.setattr(questroutes, "FRESH_SITE_S", 0.0)
    monkeypatch.setattr(questroutes, "FRESH_ALL_S", 0.0)
    cl = TestClient(base_url=LOCAL, app=app, raise_server_exceptions=False)
    cl.__enter__()
    sign_in(cl)
    return cl, box


WAY_OUT_ANSWERS = (("device", {"choice": "device:1"}), ("provider", {"choice": "provider:1"}), ("circuit_id", {"text": "NLT-7001"}),
                   ("circuit_type", {"choice": "type:1"}), ("local_port", {"choice": "interface:11"}), ("cable_type", {"choice": "smf"}))


def test_a_change_netbox_took_only_part_of_is_read_back_and_said_so(tmp_path, monkeypatch):
    cl, box = flaky_office(tmp_path, monkeypatch)
    assert say(cl, WAY_OUT, *WAY_OUT_ANSWERS)["status"] == "ready"
    box.fail_at = 3                                    # the circuit and its end are written; the cable is refused
    reads = len(box.lists("circuits/circuits"))
    v = post(cl, "apply", WAY_OUT)
    assert [o["status"] for o in v["result"]["ops"]][:2] == ["ok", "ok"] and v["result"]["applied"] is False
    assert v["message"].startswith("NetBox took only part of that change:")
    assert len(box.lists("circuits/circuits")) > reads              # NetBox was read again, not the proxy's own word
    assert (v["verified"], v["xp_awarded"]) == (False, 0)


def test_figures_typed_for_a_write_netbox_refused_need_not_be_typed_again(tmp_path, monkeypatch):
    cl, box = flaky_office(tmp_path, monkeypatch)
    say(cl, WAY_OUT, *WAY_OUT_ANSWERS)
    assert post(cl, "apply", WAY_OUT)["xp_awarded"] == 120
    say(cl, HANDOFF, ("has", {"choice": "typed"}), ("addr", {"text": "203.0.113.2/30"}), ("prefix", {"choice": "yes"}))
    assert post(cl, "apply", HANDOFF)["xp_awarded"] == 60
    assert say(cl, RECORD, *FULL_RECORD)["status"] == "ready"
    box.fail_at, box.how = 1, "refuse_all"
    v = post(cl, "apply", RECORD)
    assert v["result"]["applied"] is False and v["xp_awarded"] == 0 and v["message"].startswith("NetBox did not take that change")
    box.fail_at = None
    q = next(q for q in board(cl)["quests"] if q["id"] == RECORD)
    assert q["status"] == "ready"                       # the account number and the charges are still held for the next try
    v = post(cl, "apply", RECORD)
    assert v["complete"] and v["chain"]["status"] == "complete"


def test_a_write_that_breaks_half_way_is_followed_by_a_fresh_read(tmp_path, monkeypatch):
    cl, box = flaky_office(tmp_path, monkeypatch)
    assert say(cl, WAY_OUT, *WAY_OUT_ANSWERS)["status"] == "ready"
    box.fail_at, box.how = 2, "break"                  # the circuit is written, then the connection is lost
    r = cl.post("/api/game/quests/apply", json={"scope": "netbox", "quest_id": WAY_OUT})
    assert r.status_code >= 500
    box.fail_at = None
    assert len(box.data["circuits/circuits"]) == 1
    reads = len(box.lists("circuits/circuits"))
    b = board(cl)
    assert len(box.lists("circuits/circuits")) == reads + 1         # what was read before the write is not served as current
    assert "circuit_unterminated" in [f["kind"] for f in b["findings"]]


def test_a_dry_run_says_nothing_about_the_chain_or_the_sites_completeness(tmp_path, monkeypatch):
    cl, box = office(tmp_path, monkeypatch)
    box.permissions = []
    say(cl, WAY_OUT, *WAY_OUT_ANSWERS)
    v = post(cl, "apply", WAY_OUT)
    assert v["mode"] == "dry_run"
    assert v["message"].endswith("No XP for a practice run.") and "chain" not in v["message"] and "% complete" not in v["message"]
    # a line that is no stage of a longer chain: the room link
    rooms = next(q for q in board(cl)["quests"] if q["kind"] == "locations_not_linked")
    q = rooms
    for _ in range(8):
        if q["status"] == "ready":
            break
        step = q["step"]
        pick = next(c for c in step["choices"] if c["kind"] in ("candidate", "confirm"))
        q = post(cl, "answer", rooms["id"], step=step["id"], choice=pick["id"])
    assert q["status"] == "ready", q
    v = post(cl, "apply", rooms["id"])
    assert v["mode"] == "dry_run" and v["message"].endswith("No XP for a practice run.")
    assert "% complete" not in v["message"] and "chain" not in v["message"]


def test_the_quest_log_keeps_a_permission_answer_for_half_a_minute_at_most():
    assert perms.PermissionReader.TTL <= 30.0 if hasattr(perms.PermissionReader, "TTL") else perms.TTL <= 30.0


def play_device(cl, d: int, peer_port: int):
    """A switch from nothing to its whole record: what it plugs into, then how it is managed (a cloud service)."""
    line = f"q:device_unconnected:device:{d}"
    q = say(cl, line, ("peer", {"choice": "device:1"}), ("local_port", {"choice": f"interface:{d}3"}), ("peer_port", {"choice": f"interface:{peer_port}"}),
            ("cable_type", {"choice": "smf"}))
    assert q["status"] == "ready", q
    v1 = post(cl, "apply", line)
    line = f"q:device_unreachable:device:{d}"
    q = post(cl, "answer", line, step=f"mode_{d}", choice="cloud")
    for _ in range(4):
        if q["status"] == "ready":
            break
        q = post(cl, "answer", line, step=q["step"]["id"], choice="varies" if q["step"]["id"].startswith("model_") else "unknown")
    assert q["status"] == "ready", q
    return v1, post(cl, "apply", line)


def test_a_device_chain_pays_150_then_less_at_the_same_site_and_once(tmp_path, monkeypatch):
    cl, box = office(tmp_path, monkeypatch)
    v1, v2 = play_device(cl, 3, 13)
    assert (v1["xp_awarded"], v1["chain_xp"]) == (120, 0)
    assert (v2["chain"]["id"], v2["chain"]["status"]) == ("chain:device:device:3", "complete")
    assert (v2["xp_awarded"], v2["chain_xp"]) == (40 + 150, 150)   # a device's whole record is worth 150
    v1, v2 = play_device(cl, 2, 12)
    assert (v1["xp_awarded"], v1["chain_xp"]) == (96, 0)
    assert (v2["chain"]["id"], v2["chain"]["status"]) == ("chain:device:device:2", "complete")
    assert (v2["xp_awarded"], v2["chain_xp"]) == (32 + 120, 120)   # the second such chain at the site pays 80 %
    total = 120 + 190 + 96 + 152
    assert board(cl)["rewards"]["xp"] == total
    # the first chain gains something it never covered (a port in use with no cable); cabling it completes the chain again
    next(p for p in box.data["dcim/interfaces"] if p["id"] == 31)["description"] = "to the store room panel"
    b = board(cl, fresh=True)
    line = "q:interface_no_cable:device:3"
    q = next(q for q in b["quests"] if q["id"] == line)
    for _ in range(8):
        if q["status"] == "ready":
            break
        step = q["step"]
        pick = next(c for c in step["choices"] if c["kind"] in ("candidate", "confirm"))
        q = post(cl, "answer", line, step=step["id"], choice=pick["id"])
    assert q["status"] == "ready", q
    v = post(cl, "apply", line)
    assert v["complete"] and v["chain"]["id"] == "chain:device:device:3" and v["chain"]["status"] == "complete"
    assert v["chain_xp"] == 0 and v["xp_awarded"] == 120   # the whole record pays once per player
    assert board(cl)["rewards"]["xp"] == total + 120


def test_a_cable_to_another_site_is_read_back_at_both_sites(tmp_path, monkeypatch):
    from .test_estate_read import two_sites
    data = two_sites()
    wire(data)
    cl, box, app = start(tmp_path, monkeypatch, data)
    b = board(cl)
    line = "q:sites_not_linked:site:2"
    q = next((q for q in b["quests"] if q["id"] == line), None) or post(cl, "answer", line, step="restart")
    for _ in range(8):
        if q["status"] == "ready":
            break
        step = q["step"]
        pick = next((c for c in step["choices"] if c["id"] == "device:1"), None) or next(c for c in step["choices"] if c["kind"] in ("candidate", "confirm"))
        q = post(cl, "answer", line, step=step["id"], choice=pick["id"])
    assert q["status"] == "ready", q
    op = q["proposal"]["ops"][-1]
    assert op["op"] == "connect_cable"
    from .test_quest_settlement import Box
    seen: list[list[str]] = []
    inner = Box.__call__

    def spy(self, req):
        if req.method == "GET" and req.url.path == "/api/dcim/interfaces/":
            seen.append(sorted(req.url.params.get_list("site_id")))
        return inner(self, req)
    monkeypatch.setattr(Box, "__call__", spy)
    v = post(cl, "apply", line)
    assert (v["complete"], v["xp_awarded"]) == (True, 120), v["message"]
    assert [x for x in seen if x] == [["1", "2"]]      # the read after the write is of both sites the cable reaches


def test_a_bonus_that_crosses_no_level_brings_no_potion(tmp_path, monkeypatch):
    cl, box = office(tmp_path, monkeypatch)
    for d, peer in ((3, 13), (2, 12)):
        line = f"q:device_unconnected:device:{d}"
        say(cl, line, ("peer", {"choice": "device:1"}), ("local_port", {"choice": f"interface:{d}3"}), ("peer_port", {"choice": f"interface:{peer}"}),
            ("cable_type", {"choice": "smf"}))
        post(cl, "apply", line)
    assert board(cl)["rewards"]["xp"] == 216
    line = "q:device_unreachable:device:3"
    q = post(cl, "answer", line, step="mode_3", choice="cloud")
    for _ in range(4):
        if q["status"] == "ready":
            break
        q = post(cl, "answer", line, step=q["step"]["id"], choice="varies" if q["step"]["id"].startswith("model_") else "unknown")
    v = post(cl, "apply", line)
    # 216 + 40 crosses 250 (one potion for the line); + 150 for the whole record reaches 406 and crosses nothing
    assert (v["xp_awarded"], v["chain_xp"], v["potions_awarded"], v["rewards"]["potions"]) == (190, 150, 1, 1)


def test_only_the_device_that_was_parked_stays_parked(tmp_path):
    path, db = v3_file(tmp_path)
    old_row(db, "q:device_unreachable:site:1", "parked", {"ip_2": {"choice": "unknown"}, "ip_3": {"choice": "typed"}, "addr_3": {"text": "192.0.2.3/24"}},
            CHECKS, [], EV)
    db.close()
    rows = GameStore(path).quests(1, "netbox")
    assert (rows["q:device_unreachable:device:2"]["status"], rows["q:device_unreachable:device:3"]["status"]) == ("parked", "active")
