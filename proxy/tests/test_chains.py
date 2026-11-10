"""Quest chains: the chain model, the stages and what unlocks them, how a device is managed, a circuit's record and where
each of its facts lives in NetBox, and the example estate played from nothing to the complete record against a made-up
NetBox that takes the writes. No live NetBox is needed for any of this."""
from __future__ import annotations

import copy
import json
import logging
import sqlite3
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

from rackwalker_proxy import chains, findings, practice, questroutes, quests
from rackwalker_proxy.app import Settings, create_app
from rackwalker_proxy.changes import Rec, plan
from rackwalker_proxy.estate import Estate, simulate
from rackwalker_proxy.gamestore import MIGRATIONS, GameStore, migrate

from .conftest import LOCAL, schema_validator
from .plan_helpers import device, ops, state
from .test_estate_read import ref
from .test_game import fixture_client
from .test_game import sign_in as fixture_sign_in
from .test_quest_settlement import Box, board, post, sign_in
from .test_quests import Build, circuit_record, linked_pair, one

ISLAND = practice.PRACTICE_SITE_ID


def views(e: Estate, resolved=frozenset(), deferred=frozenset()) -> dict[str, dict[str, Any]]:
    a = findings.analyse(e)
    ls = quests.lines(e, a)
    return {c["id"]: chains.view(e, a, c, resolved, deferred, {x["id"]: x["title"] for x in ls}) for c in chains.build(e, a, ls)}


def apply_line(e: Estate, kind: str, answers: dict[str, Any]) -> dict[str, Any]:
    """Plays one line of `kind` with these answers and writes its proposal into the estate."""
    line = one(e, kind)
    st = quests.evaluate(e, line, answers)
    assert st["status"] == "ready", st
    assert [r["error"] for r in simulate(e, st["proposal"]["ops"])] == [None] * len(st["proposal"]["ops"])
    return st


# ---------------------------------------------------------------- the chain model
def test_chains_are_derived_per_subject_connectivity_before_devices_before_racks():
    e = practice.example_estate(complete=True)
    v = views(e)
    assert [(c["kind"], c["label"]) for c in v.values()] == [
        ("way_out", "1 of 3: physical"), ("room_link", "1 of 1: complete"), ("room_link", "1 of 1: physical"),
        ("device", "1 of 2: physical"), ("device", "1 of 2: physical"), ("device", "1 of 2: physical"),
        ("rack", "2 of 2: complete"), ("rack", "2 of 2: complete")]
    out = v[f"chain:way_out:site:{ISLAND}"]
    schema_validator("Chain").validate(out)
    # the way out always shows its three stages: the later ones wait for the circuit to be plugged in
    assert [(s["stage"], s["status"]) for s in out["stages"]] == [("physical", "open"), ("logical", "locked"), ("administrative", "locked")]
    assert out["title"] == "The way out of Practice Island" and out["holds"] == ("the port the line plugs into, and the circuit; your address on it; "
                                                                             "the carrier's network, your account, who to call, and what it costs")
    assert out["bonus_xp"] == 250
    assert out["stages"][0]["quest_ids"] == [f"q:site_no_way_out:site:{ISLAND}"] and out["summary"] is None
    # a stage NetBox already satisfies is done, never asked: every switch has its management address on record
    dev = v["chain:device:device:9000001"]
    assert [(s["stage"], s["status"]) for s in dev["stages"]] == [("physical", "open"), ("logical", "done")]
    assert dev["summary"] == "sw-quay-1: managed locally at 192.0.2.11/24."


def test_a_later_stage_unlocks_when_the_earlier_one_is_verified_answered_or_on_purpose():
    e = practice.example_estate(complete=True)
    cid = f"chain:way_out:site:{ISLAND}"
    a = findings.analyse(e)
    ls = quests.lines(e, a)
    # before the line is plugged in, no line of a later stage exists at all
    assert not [x for x in ls if x["kind"] in ("circuit_handoff", "circuit_record")]
    apply_line(e, "site_no_way_out", {"device": {"choice": "device:9000001"}, "provider": {"choice": "provider:9000001"},
                                      "circuit_id": {"text": "EXF-1"}, "circuit_type": {"choice": "type:9000001"},
                                      "local_port": {"choice": "interface:9000026"}, "cable_type": {"choice": "smf"}})
    v = views(e)[cid]
    assert v["label"] == "2 of 3: logical" and [s["status"] for s in v["stages"]] == ["done", "open", "locked"]
    assert v["stages"][1]["missing"] == ["The address on EXF-1"] and v["summary"] == "Practice Island: sw-quay-1 Te1/1/2 -> circuit EXF-1 (Internet), Example Fibre."
    new = max(e.circuits)   # what the island's quests create has an id above the island's own, never one NetBox uses
    assert new > 9_000_000
    hand, facts = f"circuit_handoff:circuit:{new}", [f"circuit:{new}:{f}" for f in findings.CIRCUIT_FACTS]
    # "no address there, on purpose" answers the logical stage: the administrative one unlocks
    v = views(e, resolved={hand})[cid]
    assert v["label"] == "3 of 3: administrative" and [s["status"] for s in v["stages"]] == ["done", "answered", "open"]
    # facts answered "I don't know yet" leave the chain honestly open at that stage, listed as open questions
    v = views(e, resolved={hand, *facts[:-2]}, deferred=set(facts[-2:]))[cid]
    assert (v["status"], v["label"]) == ("open_questions", "3 of 3: administrative (not known yet: the monthly charge and the one-off charge)")
    assert v["open_questions"] == ["the monthly charge", "the one-off charge"] and v["stages"][2]["status"] == "not_known"
    # everything answered: complete (but only what NetBox itself shows counts as a verified record)
    v = views(e, resolved={hand, *facts})[cid]
    assert (v["status"], v["label"]) == ("complete", "3 of 3: complete")
    a = findings.analyse(e)
    assert not chains.verified(a, chains.of_checks(chains.build(e, a, quests.lines(e, a)), [hand]))


def test_the_board_offers_only_the_stage_a_chain_has_reached(tmp_path):
    cl = fixture_client(tmp_path)
    cl.headers["X-RackWalker-CSRF"] = fixture_sign_in(cl)
    b = cl.get("/api/game/quests", params={"scope": "practice"}).json()
    schema_validator("QuestBoard").validate(b)
    # how the loft switch is managed is the logical stage of its chain: locked until what it plugs into is on record
    assert "device_unreachable" not in [q["kind"] for q in b["quests"]]
    # at most a few chains are in play at a site: those of the lines on offer; the others are counted
    assert [c["id"] for c in b["chains"]] == [f"chain:way_out:site:{ISLAND}", "chain:room_link:location:9000002", "chain:device:device:9000001"]
    assert b["chains_queued"] == 4
    loft = views(practice.island())["chain:device:device:9000003"]
    assert loft["label"] == "1 of 2: physical" and [s["status"] for s in loft["stages"]] == ["open", "locked"]
    lead = b["quests"][0]
    assert lead["chain"] == {"id": f"chain:way_out:site:{ISLAND}", "title": "The way out of Practice Island", "stage": "physical",
                             "position": 1, "total": 3, "label": "1 of 3: physical"}
    # chains that have nothing to do yet are counted, not listed
    assert {c["status"] for c in b["chains"]} == {"open"} and b["setup"] == []
    # the island pays nothing, so its chains promise no bonus; neither does a proxy that writes nothing
    assert {c["bonus_xp"] for c in b["chains"]} == {0}
    assert {c["bonus_xp"] for c in cl.get("/api/game/quests", params={"scope": "netbox"}).json()["chains"]} == {0}


# ---------------------------------------------------------------- how a device is managed
def managed() -> tuple[Build, int, int, int]:
    """Two access points of one model and a switch, cabled, none with a management address on record."""
    b = Build()
    s = b.site()
    room = b.room(s)
    rack = b.rack(s, room)
    r, sw = linked_pair(b, s, room, rack)
    aps = [b.dev(s, f"ap{n}", "access-point", room, None, ports=1, primary_ip=None, model="AP-22", type_id=7, mgmt_mode=None, mgmt_default=None)
           for n in (1, 2)]
    for n, ap in enumerate(aps):
        b.cable(b.p(ap, "eth1"), b.p(sw, f"eth{n + 2}"))
    return b, s, aps[0], aps[1]


def test_the_management_question_comes_before_any_address_and_a_cloud_managed_device_needs_none():
    b, s, ap1, ap2 = managed()
    e = b.e
    line = one(e, "device_unreachable")
    assert line["id"] == f"q:device_unreachable:device:{ap1}" and line["title"] == "How ap1 is managed" and line["xp"] == 40
    step = quests.evaluate(e, line, {})["step"]
    assert (step["id"], step["prompt"]) == (f"mode_{ap1}", "How is ap1 (AP-22) managed?")
    answers = {f"mode_{ap1}": {"choice": "cloud"}}
    step = quests.evaluate(e, line, answers)["step"]
    # which service: any already on record is a choice, and any other can be typed
    assert step["prompt"] == "Which service manages ap1?" and [c["id"] for c in step["choices"]] == ["typed", "unknown"]
    answers.update({f"service_{ap1}": {"choice": "typed"}, f"service_name_{ap1}": {"text": "Example Cloud Portal"}})
    step = quests.evaluate(e, line, answers)["step"]
    # a model with more than one device is asked once whether that holds for all of them
    assert step["prompt"] == "NetBox has 2 AP-22 devices. Are they all managed that way?"
    answers[f"model_{ap1}"] = {"choice": "varies"}
    st = quests.evaluate(e, line, answers)
    assert st["status"] == "ready" and st["proposal"]["ops"] == [{"op": "set_management", "target": {"kind": "device", "id": ap1}, "fields": {
        "mode": "cloud", "service": "Example Cloud Portal", "model_default": "varies"}}]
    assert st["changes"] == ["ap1: managed by a cloud or controller service (Example Cloud Portal). The AP-22 model is marked as varying from device to device."]
    assert [r["error"] for r in simulate(e, st["proposal"]["ops"])] == [None]
    a = findings.analyse(e)
    # no address was asked for and none is expected: the device is complete for management
    assert a.check(f"device:{ap1}:primary_ip") is True and e.mgmt_mode(e.devices[ap1]) == "cloud"
    assert views(e)[f"chain:device:device:{ap1}"]["summary"] == "ap1: eth1 -> sw1 eth2; managed by Example Cloud Portal (no local address)."
    # the model varies, so the other one is asked for itself: without the model question, and with the known service on offer
    other = one(e, "device_unreachable")
    assert other["id"] == f"q:device_unreachable:device:{ap2}"
    answers = {f"mode_{ap2}": {"choice": "cloud"}}
    step = quests.evaluate(e, other, answers)["step"]
    assert [c["label"] for c in step["choices"]][:2] == ["Example Cloud Portal", "A service not listed: I will type its name"]
    answers[f"service_{ap2}"] = {"choice": step["choices"][0]["id"]}
    assert len(step["choices"][0]["id"]) <= 64
    st = quests.evaluate(e, other, answers)
    assert st["proposal"]["ops"][0]["fields"] == {"mode": "cloud", "service": "Example Cloud Portal"}


def test_a_model_that_is_always_managed_one_way_is_asked_once():
    b, s, ap1, ap2 = managed()
    e = b.e
    apply_line(e, "device_unreachable", {f"mode_{ap1}": {"choice": "unmanaged"}, f"model_{ap1}": {"choice": "all"}})
    a = findings.analyse(e)
    # the model's default answers for the second one: nothing left to ask, and both count as complete
    assert [f for f in a.findings if f["kind"] == "device_unreachable"] == []
    assert (a.check(f"device:{ap1}:primary_ip"), a.check(f"device:{ap2}:primary_ip")) == (True, True)
    assert e.mgmt_mode(e.devices[ap2]) == "unmanaged" and e.devices[ap2]["mgmt_mode"] is None
    # a device's own answer wins over its model's default
    e.devices[ap2]["mgmt_mode"] = "local"
    e.touch()
    assert findings.analyse(e).check(f"device:{ap2}:primary_ip") is False
    assert one(e, "device_unreachable")["finding"]["title"] == "ap2 is managed locally, but NetBox has no address to log in to it."


def test_a_locally_managed_device_names_the_port_that_carries_management_and_that_port_is_not_asked_for_a_cable():
    b = Build()
    s = b.site()
    room = b.room(s)
    rack = b.rack(s, room)
    r, sw = linked_pair(b, s, room, rack)
    sw2 = b.dev(s, "sw2", "switch", room, rack, primary_ip=None, mgmt_mode=None)   # no management port, no virtual interface
    b.cable(b.p(sw2, "eth1"), b.p(sw, "eth2"))
    e = b.e
    line = one(e, "device_unreachable")
    answers = {f"mode_{sw2}": {"choice": "local"}}
    # "I don't know yet" on the address still records what is known: it is managed locally
    soft = quests.evaluate(e, line, {**answers, f"ip_{sw2}": {"choice": "unknown"}})
    assert soft["status"] == "ready" and [o["op"] for o in soft["proposal"]["ops"]] == ["set_management"]
    assert soft["checklist"][0] == "Ask whoever looks after sw2 which address they use to log in to it."
    answers.update({f"ip_{sw2}": {"choice": "typed"}, f"addr_{sw2}": {"text": "192.0.2.40/24"}})
    step = quests.evaluate(e, line, answers)["step"]
    assert step["prompt"] == "Which interface of sw2 carries 192.0.2.40/24?" and [c["label"] for c in step["choices"]][:2] == ["eth1", "eth2"]
    answers[f"port_{sw2}"] = {"choice": f"interface:{b.p(sw2, 'eth3')[1]}"}
    st = apply_line(e, "device_unreachable", answers)
    assert st["changes"] == ["sw2: managed locally.", "sw2: 192.0.2.40/24 on eth3, as its management address (primary IP)."]
    a = findings.analyse(e)
    assert a.check(f"device:{sw2}:primary_ip") is True
    # eth3 has an address and no cable, but it is the management address the player has just named: no cable is asked for
    assert [g["id"] for g in a.gaps if g["field"] == "cable"] == [] and not [f for f in a.findings if f["kind"] == "interface_no_cable"]
    # an address that is not the management address of a locally managed device still asks for its cable
    b.ip(b.p(sw2, "eth4"), "192.0.2.41/24")
    e.touch()
    assert [g["id"] for g in findings.analyse(e).gaps if g["field"] == "cable"] == [f"interface:{b.p(sw2, 'eth4')[1]}:cable"]


def test_a_models_default_of_local_exempts_the_management_port_like_the_devices_own_answer():
    b = Build()
    s = b.site()
    room = b.room(s)
    rack = b.rack(s, room)
    r, sw = linked_pair(b, s, room, rack)
    port = {}
    # each is cabled to sw1 on eth1 and has its management address on eth3, a front-panel port with no cable
    for n, (name, own, default) in enumerate((("own", "local", None), ("model", None, "local"), ("unsaid", None, None)), 2):
        d = b.dev(s, name, "switch", room, rack, primary_ip=f"192.0.2.{50 + n}/24", type_id=n, mgmt_mode=own, mgmt_default=default)
        b.cable(b.p(d, "eth1"), b.p(sw, f"eth{n}"))
        b.ip(b.p(d, "eth3"), f"192.0.2.{50 + n}/24")
        port[name] = b.p(d, "eth3")[1]
    a = findings.analyse(b.e)
    # recorded as locally managed, on the device or as its model's default: the address says how it is reached, and no cable
    # is asked for. With nothing recorded, an addressed port is a port in use, as it was before the game asked how kit is managed.
    assert [g["id"] for g in a.gaps if g["field"] == "cable"] == [f"interface:{port['unsaid']}:cable"]
    assert [f["title"] for f in a.findings if f["kind"] == "interface_no_cable"] == ["unsaid eth3 has an address but no cable."]
    assert [b.e.mgmt_recorded(d) for d in b.e.devices.values() if d["name"] in port] == ["local", "local", None]


# ---------------------------------------------------------------- a circuit's record
def recorded(e: Estate, cid: int) -> dict[str, bool]:
    return findings.circuit_facts(e, cid)


def test_each_fact_of_a_circuits_record_has_its_home_in_netbox():
    e = circuit_record()
    cid = next(iter(e.circuits))
    assert list(recorded(e, cid)) == list(findings.CIRCUIT_FACTS) and not any(recorded(e, cid).values())
    line = one(e, "circuit_record")
    # the sentence names every fact that is missing: none is hidden behind "and 7 more"
    assert line["purpose"].startswith("Circuit C-OK from Carrier is plugged in at Mill, but NetBox does not hold the provider's network it lands on, "
                                      "the account with the provider, who to call when it is down, who the account manager is, who sends the bill, "
                                      "the install date, the commit rate, the monthly charge or the one-off charge. When the line is down,")
    answers = {"network": {"choice": "new"}, "network_name": {"text": "Carrier Internet"},
               "account": {"choice": "new"}, "account_no": {"text": "SYN-44-1021"},
               "contact_for": {"choice": "provider"},
               "contact": {"values": {"contact:support:name": "Carrier NOC", "contact:support:phone": "+44 1632 960123",
                                      "contact:account:name": "carrier noc",
                                      "contact:billing:name": "Carrier Billing", "contact:billing:email": "bills@example.net"}},
               "terms": {"values": {"install_date": "2024-03-01", "commit_rate": 100}},
               "cost": {"values": {"mrc": 240, "nrc": 500, "currency": "gbp"}}}
    st = quests.evaluate(e, line, answers)
    assert st["status"] == "ready" and st["deferred"] == []
    pid = e.circuits[cid]["provider_id"]
    roles = quests.contact_roles(e)
    assert [(o["op"], o["target"]["kind"], o["fields"]) for o in st["proposal"]["ops"]] == [
        ("create_provider_network", "provider_network", {"provider_id": pid, "name": "Carrier Internet"}),          # circuits.ProviderNetwork
        ("create_circuit_termination", "circuit_termination", {"circuit_id": cid, "term_side": "Z", "provider_network_id": "@network"}),
        ("create_provider_account", "provider_account", {"provider_id": pid, "account": "SYN-44-1021"}),          # circuits.ProviderAccount
        ("create_contact", "contact", {"name": "Carrier NOC", "phone": "+44 1632 960123"}),                         # tenancy.Contact
        ("assign_contact", "provider", {"contact_id": "@contact_support", "role_id": roles["support"]["id"]}),      # tenancy.ContactAssignment
        # one desk in two roles is one contact, assigned twice
        ("assign_contact", "provider", {"contact_id": "@contact_support", "role_id": roles["account"]["id"]}),
        ("create_contact", "contact", {"name": "Carrier Billing", "email": "bills@example.net"}),
        ("assign_contact", "provider", {"contact_id": "@contact_billing", "role_id": roles["billing"]["id"]}),
        # the circuit's own fields, and the three custom fields for what NetBox has no field for
        ("record_circuit", "circuit", {"provider_account_id": "@account", "install_date": "2024-03-01", "commit_rate": 100000,
                                       "mrc": "240.00", "nrc": "500.00", "currency": "GBP"})]
    schema_validator("ChangeSet").validate(st["proposal"])
    assert st["changes"][-1] == "Circuit C-OK: account SYN-44-1021, installed 2024-03-01, commit rate 100 Mbps, 240.00 GBP a month, 500.00 GBP once."
    assert [r["error"] for r in simulate(e, st["proposal"]["ops"])] == [None] * 9
    assert all(recorded(e, cid).values()) and not [f for f in findings.analyse(e).findings if f["kind"] == "circuit_record"]
    # the record, read back from the estate
    assert chains.circuit_summary(e, cid) == ("rtr1 eth3 -> circuit C-OK (Internet), Carrier, network Carrier Internet, your address 192.0.2.201/24, "
                                              "account SYN-44-1021, Support / NOC: Carrier NOC +44 1632 960123, Billing: Carrier Billing bills@example.net, "
                                              "240.00 GBP a month, 500.00 GBP once, installed 2024-03-01, 100 Mbps committed")


def test_what_is_not_known_is_left_open_and_the_rest_is_written():
    e = circuit_record()
    cid = next(iter(e.circuits))
    line = one(e, "circuit_record")
    answers = {"network": {"choice": "none"}, "account": {"choice": "unknown"}, "contact_for": {"choice": "none"},
               "terms": {"values": {"install_date": "2024-03-01"}}, "cost": {"values": {}}}
    st = quests.evaluate(e, line, answers)
    # the install date is written; "none, on purpose" is an answer; what is not known is listed and waits
    assert st["status"] == "ready" and st["proposal"]["ops"] == [{"op": "record_circuit", "target": {"kind": "circuit", "id": cid},
                                                                   "fields": {"install_date": "2024-03-01"}}]
    assert st["waived"] == [f"circuit:{cid}:{f}" for f in ("network", "contact_support", "contact_account", "contact_billing")] == quests.waived(line, answers)
    not_known = [f"circuit:{cid}:{f}" for f in ("account", "commit_rate", "mrc", "nrc")]
    assert st["deferred"] == not_known and sorted(quests.deferred(line, answers)) == sorted(not_known)
    assert "The monthly charge (MRC) is on every bill" in " ".join(st["checklist"])
    simulate(e, st["proposal"]["ops"])
    # with nothing more to write, the line waits at the first question that was not known, and says so
    again = quests.evaluate(e, one(e, "circuit_record"), answers)
    assert again["status"] == "parked" and again["step"]["id"] == "account" and again["proposal"] is None
    assert again["note"] == "Not known yet for circuit C-OK: the account, the commit rate and 2 more. The line waits here; nothing is lost."
    v = views(e, resolved=set(st["waived"]), deferred=set(not_known))["chain:way_out:site:1"]
    assert v["label"] == "3 of 3: administrative (not known yet: the account, the commit rate and 2 more)"
    # A form of the player's own figures is stored only as the facts it left blank. While those are all it is asked for, it
    # is not asked again; if the proxy restarted before the figures were written, a figure is missing and it is.
    base = {"network": {"choice": "none"}, "account": {"choice": "none"}, "contact_for": {"choice": "none"}, "terms": {"choice": "none"}}
    assert quests.blank_facts("cost", {"nrc": 5, "currency": "GBP"}) == ["mrc"]
    assert quests.blank_facts("contact", {"contact:support:name": "NOC", "contact:billing:phone": "1"}) == ["contact_account", "contact_billing"]
    fresh = circuit_record()
    lost = quests.evaluate(fresh, one(fresh, "circuit_record"), {**base, "cost": {"unknown": ["mrc"]}})
    assert lost["status"] == "asking" and lost["step"]["id"] == "cost"
    fresh.circuits[cid].update(nrc="5.00", currency="GBP")
    fresh.touch()
    line = one(fresh, "circuit_record")
    kept = quests.evaluate(fresh, line, {**base, "cost": {"unknown": ["mrc"]}})
    assert kept["status"] == "parked" and kept["deferred"] == [f"circuit:{cid}:mrc"] == quests.deferred(line, {"cost": {"unknown": ["mrc"]}})
    # everything "none, on purpose": the line closes with nothing written
    closed = quests.evaluate(circuit_record(), one(circuit_record(), "circuit_record"),
                             {"network": {"choice": "none"}, "account": {"choice": "none"}, "contact_for": {"choice": "none"},
                              "terms": {"choice": "none"}, "cost": {"choice": "none"}})
    assert closed["status"] == "dismissed" and closed["proposal"] is None


def test_real_names_are_the_choices_and_what_netbox_cannot_hold_is_not_asked_for():
    e = circuit_record()
    cid = next(iter(e.circuits))
    pid = e.circuits[cid]["provider_id"]
    e.networks[5] = {"id": 5, "provider_id": pid, "name": "Carrier MPLS"}
    e.networks[6] = {"id": 6, "provider_id": pid + 100, "name": "Somebody Else"}
    e.accounts[8] = {"id": 8, "provider_id": pid, "account": "SYN-0008", "name": "head office"}
    e.contacts[3] = {"id": 3, "name": "Carrier NOC", "phone": "", "email": ""}
    line = one(e, "circuit_record")
    step = quests.evaluate(e, line, {})["step"]
    assert [c["label"] for c in step["choices"]] == ["Carrier MPLS", "A network of theirs not in NetBox yet", "None to record: it is just their line",
                                                     "I don't know yet"]
    step = quests.evaluate(e, line, {"network": {"choice": "network:5"}})["step"]
    assert [c["label"] for c in step["choices"]][:2] == ["SYN-0008 (head office)", "An account not in NetBox yet"]
    answers = {"network": {"choice": "network:5"}, "account": {"choice": "account:8"}, "contact_for": {"choice": "circuit"},
               "contact": {"values": {"contact:support:name": "carrier noc"}}, "terms": {"choice": "none"}, "cost": {"choice": "none"}}
    # a name NetBox already has is never taken over in silence: the player is asked whether that contact is the one
    st = quests.evaluate(e, line, answers)
    assert st["step"]["id"] == "contact_is_3" and st["step"]["prompt"] == "NetBox already has a contact called Carrier NOC. Is that who you mean?"
    answers["contact_is_3"] = {"choice": "yes"}
    st = quests.evaluate(e, line, answers)
    # a contact NetBox already has is assigned, not created a second time; this one only to the circuit
    assert [(o["op"], o["target"], o["fields"]) for o in st["proposal"]["ops"]][1:] == [
        ("assign_contact", {"kind": "circuit", "id": cid}, {"contact_id": 3, "role_id": 1}),
        ("record_circuit", {"kind": "circuit", "id": cid}, {"provider_account_id": 8})]
    # a contact needs a name; a charge needs its currency; a date has to be a date
    for step_id, values, says in (("contact", {"contact:support:phone": "+44 1632 960123"}, "A contact needs a name"),
                                  ("terms", {"install_date": "2024-02-31"}, "not a date of the calendar"),
                                  ("cost", {"mrc": 10}, "a charge needs its currency")):
        base = {"network": {"choice": "none"}, "account": {"choice": "none"}, "contact_for": {"choice": "provider" if step_id == "contact" else "none"},
                "terms": {"choice": "none"}}
        if step_id == "cost":
            base["terms"] = {"choice": "none"}
        bad = quests.evaluate(e, line, {**base, step_id: {"values": values}})
        assert bad["status"] == "asking" and bad["step"]["id"] == step_id and says in bad["step"]["error"], (step_id, bad["step"])
    # NetBox without the cost fields or without contact roles: neither the charges nor who to call is asked for or is part
    # of the record, and the quest log says what to add
    bare = circuit_record()
    bare.no_fields += ["rw_mrc", "rw_nrc", "rw_currency"]
    bare.contact_roles.clear()
    assert list(recorded(bare, cid)) == ["network", "account", "install_date", "commit_rate"]
    st = quests.evaluate(bare, one(bare, "circuit_record"), {"network": {"choice": "none"}, "account": {"choice": "none"}})
    assert st["status"] == "asking" and st["step"]["id"] == "terms"
    notes = questroutes.setup_notes(bare)
    assert notes[0].startswith("NetBox has no custom fields rw_mrc, rw_nrc and rw_currency on circuits")
    assert notes[1].startswith("NetBox has no contact role for support, the account manager or billing")
    # one role only: that contact is the only one asked for
    bare.contact_roles[4] = {"id": 4, "name": "Accounts payable", "slug": "ap"}
    bare.touch()
    assert list(recorded(bare, cid)) == ["network", "account", "contact_billing", "install_date", "commit_rate"]
    step = quests.evaluate(bare, one(bare, "circuit_record"), {"network": {"choice": "none"}, "account": {"choice": "none"},
                                                               "contact_for": {"choice": "provider"}})["step"]
    assert [f["id"] for f in step["fields"]] == ["contact:billing:name", "contact:billing:phone", "contact:billing:email", "contact:billing:none"]


def test_the_hand_off_address_and_its_prefix():
    from .test_quests import circuit_handoff

    e = circuit_handoff()
    line = one(e, "circuit_handoff")
    step = quests.evaluate(e, line, {})["step"]
    assert step["prompt"] == "Circuit C-OK plugs into rtr1 eth3. Does that port carry an address of yours on Carrier's line?"
    assert [c["label"] for c in step["choices"]] == ["Yes: I will type it", "No address there: bridged, or theirs to hold", "I don't know yet"]
    assert quests.evaluate(e, line, {"has": {"choice": "none"}})["status"] == "dismissed"
    assert quests.evaluate(e, line, {"has": {"choice": "unknown"}})["status"] == "parked"
    answers = {"has": {"choice": "typed"}, "addr": {"text": "203.0.113.2/30"}}
    step = quests.evaluate(e, line, answers)["step"]
    assert step["prompt"] == "203.0.113.2/30 lies in no prefix NetBox has. Its mask says the subnet is 203.0.113.0/30. Record that prefix too?"
    st = quests.evaluate(e, line, {**answers, "prefix": {"choice": "yes"}})
    cid = next(iter(e.circuits))
    hand = e.circuit_handoff(cid)
    assert st["proposal"]["ops"] == [
        {"op": "assign_ip", "target": {"kind": "device", "id": hand[2]["id"]}, "fields": {"address": "203.0.113.2/30", "interface_id": hand[1]["id"], "primary": False},
         **({"if_unmodified_since": hand[2]["last_updated"]} if hand[2].get("last_updated") else {})},
        {"op": "create_prefix", "target": {"kind": "prefix", "id": None}, "fields": {"prefix": "203.0.113.0/30"}}]
    # an address inside a prefix NetBox has is not asked about its prefix
    st = quests.evaluate(e, line, {"has": {"choice": "typed"}, "addr": {"text": "192.0.2.9/24"}})
    assert st["status"] == "ready" and [o["op"] for o in st["proposal"]["ops"]] == ["assign_ip"]
    simulate(e, st["proposal"]["ops"])
    assert findings.analyse(e).check(f"circuit_handoff:circuit:{cid}") is True
    assert e.devices[hand[2]["id"]]["primary_ip"] == "192.0.2.1/24"   # the hand-off address never becomes the management address


# ---------------------------------------------------------------- the change ops against the planner's rules
def circuit_rec(i: int = 4, **v: Any) -> Rec:
    return Rec("circuit", i, None, {"cid": "C-1", "provider_id": 2, "provider_account_id": None, "install_date": None, "commit_rate": None,
                                    "mrc": None, "nrc": None, "currency": None, "_has_cost": True, **v})


def planned(st, *raw: dict[str, Any]) -> list[dict[str, Any]]:
    return [p.result().model_dump(mode="json") for p in plan(ops(*raw), st)]


def record(**fields: Any) -> dict[str, Any]:
    return {"op": "record_circuit", "target": {"kind": "circuit", "id": 4}, "fields": fields}


def test_a_circuits_record_is_filled_in_never_written_over():
    acct = Rec("provider_account", 8, None, {"provider_id": 2, "account": "SYN-8"})
    other = Rec("provider_account", 9, None, {"provider_id": 3, "account": "SYN-9"})
    st = state(circuit_rec(), acct, other, Rec("provider", 2, None, {"name": "Carrier"}))
    ok = planned(st, record(provider_account_id=8, install_date="2024-03-01", commit_rate=100000, mrc="240", nrc="0", currency="GBP"))[0]
    assert ok["status"] == "ok" and ok["after"] == {"provider_account_id": 8, "install_date": "2024-03-01", "commit_rate": 100000, "mrc": "240.00",
                                                    "nrc": "0.00", "currency": "GBP"}
    # an account of another provider; a charge with no currency; a date that is none; NetBox without the cost fields
    for fields, st2, code, says in (
            ({"provider_account_id": 9}, st, "validation_failed", "not an account with the provider"),
            ({"mrc": "10.00"}, st, "validation_failed", "needs its currency"),
            ({"install_date": "2024-02-31"}, st, "validation_failed", "is not a date"),
            ({"mrc": "10.00", "currency": "GBP"}, state(circuit_rec(_has_cost=False)), "validation_failed", "no custom fields rw_mrc"),
            # what NetBox already holds with another value is somebody's record: refused, not replaced
            ({"mrc": "10.00", "currency": "GBP"}, state(circuit_rec(mrc="12.00", currency="GBP")), "conflict", "already has a value for mrc"),
            ({"install_date": "2024-03-01"}, state(circuit_rec(install_date="2023-01-01")), "conflict", "already has a value for install date")):
        out = planned(st2, record(**fields))[0]
        assert (out["status"], out["error"]["code"]) == ("error", code) and says in out["error"]["message"], (fields, out)
    # the same value again changes nothing
    assert planned(state(circuit_rec(mrc="12.00", currency="GBP")), record(mrc="12", currency="GBP"))[0]["status"] == "no_change"
    # an account made earlier in the same change set is named by its reference
    both = planned(st, {"op": "create_provider_account", "target": {"kind": "provider_account", "id": None}, "client_ref": "account",
                        "fields": {"provider_id": 2, "account": "SYN-NEW"}}, record(provider_account_id="@account"))
    assert [o["status"] for o in both] == ["ok", "ok"]
    dup = planned(st, {"op": "create_provider_account", "target": {"kind": "provider_account", "id": None}, "fields": {"provider_id": 2, "account": "SYN-8"}})[0]
    assert dup["error"]["code"] == "conflict" and "SYN-8" not in dup["error"]["message"]   # an account number is never echoed in a message


def test_management_is_written_only_where_netbox_can_hold_it():
    dev = device(5, _has_mgmt=True, _has_default=True, _mgmt_mode=None, _mgmt_service=None, _mgmt_default=None, _type_id=3, _model="AP-22")
    op = {"op": "set_management", "target": {"kind": "device", "id": 5}, "fields": {"mode": "cloud", "service": "Portal", "model_default": "cloud"}}
    out = planned(state(dev), op)[0]
    assert out["status"] == "ok" and out["before"] == {"mode": None, "service": None, "model_default": None} and out["after"] == op["fields"]
    assert planned(state(device(5, **{**dev.v, "_has_mgmt": False})), op)[0]["error"]["message"].startswith("This NetBox has no custom field rw_mgmt_mode")
    assert "rw_mgmt_default" in planned(state(device(5, **{**dev.v, "_has_default": False})), op)[0]["error"]["message"]
    local = {**op, "fields": {"mode": "local", "service": "Portal"}}
    assert "only for a device that a cloud or controller service manages" in planned(state(dev), local)[0]["error"]["message"]
    same = planned(state(device(5, **{**dev.v, "_mgmt_mode": "unmanaged"})), {**op, "fields": {"mode": "unmanaged"}})[0]
    assert same["status"] == "no_change"


def test_a_circuit_end_is_at_a_site_or_on_a_network_of_its_own_provider():
    st = state(Rec("circuit", 4, None, {"cid": "C-1", "provider_id": 2}), Rec("provider_network", 6, None, {"provider_id": 2, "name": "Net"}),
               Rec("provider_network", 7, None, {"provider_id": 3, "name": "Other"}))

    def end(**fields: Any) -> dict[str, Any]:
        return {"op": "create_circuit_termination", "target": {"kind": "circuit_termination", "id": None}, "fields": {"circuit_id": 4, "term_side": "Z", **fields}}
    assert planned(st, end(provider_network_id=6))[0]["status"] == "ok"
    assert "not a network of the provider" in planned(st, end(provider_network_id=7))[0]["error"]["message"]
    for bad in ({}, {"site_id": 1, "provider_network_id": 6}):
        assert list(schema_validator("ChangeSet").iter_errors({"ops": [end(**bad)]}))   # exactly one of the two
    dup = planned(st, {"op": "assign_contact", "target": {"kind": "circuit", "id": 4}, "fields": {"contact_id": 1, "role_id": 1}})[0]
    assert dup["error"]["code"] == "not_found"   # a contact and a role that NetBox does not have


# ---------------------------------------------------------------- the example estate against a NetBox that takes the writes
ALL = [{"id": 1, "enabled": True, "constraints": None, "actions": ["view", "add", "change"], "object_types": sorted({t for needs in [
    *questroutes.perms.NEEDS.values(), [("dcim.devicetype", "change"), ("dcim.site", "change")]] for t, _ in needs})}]
CF_DEVICE = {"rw_mgmt_mode": None, "rw_mgmt_service": None}
CF_CIRCUIT = {"rw_mrc": None, "rw_nrc": None, "rw_currency": None}


def mill_lane() -> dict[str, list[dict[str, Any]]]:
    """The example estate: three switches, an internet provider, two rooms with a rack each, nothing interconnected,
    nothing known about how the switches are managed, and no circuit on record."""
    devs = [(1, "ml-sw1", 1, 1), (2, "ml-sw2", 1, 1), (3, "ml-sw3", 2, 2)]
    ports = [{"id": d * 10 + p, "name": f"Te1/1/{p}", "device": ref(d, name=name), "type": {"value": "10gbase-x-sfpp"}, "enabled": True,
              "mgmt_only": False, "cable": None, "description": "", "mark_connected": False} for d, name, _, _ in devs for p in (1, 2, 3)]
    return {
        "dcim/sites": [{"id": 1, "name": "Mill Lane", "slug": "mill-lane", "status": {"value": "active"}, "latitude": 52.0, "longitude": 4.0,
                        "facility": "ML1", "region": None, "physical_address": "", "custom_fields": {"iso_country": "GB"}}],
        "dcim/locations": [{"id": 1, "name": "Comms Room", "site": ref(1)}, {"id": 2, "name": "Store Room", "site": ref(1)}],
        "dcim/racks": [{"id": r, "name": name, "site": ref(1), "location": ref(r), "u_height": 42, "custom_fields": {"iso_x": 1, "iso_y": 1}}
                       for r, name in ((1, "ML-C1"), (2, "ML-S1"))],
        "dcim/devices": [{"id": d, "name": name, "site": ref(1), "location": ref(loc), "rack": ref(rack), "position": 10.0 + d, "face": {"value": "front"},
                          "role": ref(1), "device_type": ref(1), "status": {"value": "active"}, "serial": f"SYN-ML-{d}", "asset_tag": None,
                          "platform": None, "primary_ip": None, "custom_fields": dict(CF_DEVICE)} for d, name, loc, rack in devs],
        "dcim/device-types": [{"id": 1, "manufacturer": ref(1, name="Example Networks"), "model": "EX-A2400", "slug": "ex-a2400", "u_height": 1.0,
                               "is_full_depth": False, "custom_fields": {"rw_mgmt_default": None, "rw_mgmt_service": None}}],
        "dcim/device-roles": [{"id": 1, "slug": "switch", "name": "Switch", "color": "2196f3"}],
        "dcim/interfaces": ports, "dcim/cables": [], "ipam/prefixes": [], "ipam/ip-addresses": [],
        "circuits/providers": [{"id": 1, "name": "Northlight Telecom"}], "circuits/circuit-types": [{"id": 1, "name": "Internet"}],
        "circuits/circuits": [], "circuits/circuit-terminations": [], "circuits/provider-accounts": [], "circuits/provider-networks": [],
        "tenancy/contacts": [], "tenancy/contact-assignments": [],
        "tenancy/contact-roles": [{"id": 1, "name": "Support / NOC", "slug": "support"}, {"id": 2, "name": "Account manager", "slug": "account-manager"},
                                  {"id": 3, "name": "Billing", "slug": "billing"}],
    }


class Office(Box):
    """The made-up NetBox of test_quest_settlement, taught the objects a chain writes."""

    FK = ("provider", "type", "provider_account", "circuit", "contact", "role")

    def sites_of(self, path: str, row: dict[str, Any]) -> set[str]:
        if path == "dcim/cables":
            dev_site = {d["id"]: str(d["site"]["id"]) for d in self.data["dcim/devices"]}
            port_dev = {p["id"]: p["device"]["id"] for p in self.data["dcim/interfaces"]}
            return {dev_site[port_dev[t["object_id"]]] for side in ("a_terminations", "b_terminations") for t in row[side]
                    if t["object_type"] == "dcim.interface"}
        return super().sites_of(path, row)

    def write(self, req: httpx.Request) -> httpx.Response:
        path = req.url.path.removeprefix("/api/").strip("/")
        body = json.loads(req.content)
        self.calls.append((req.method, path, {}))
        self.writes.append((req.method, path, body))
        fields = {k: ({"id": v} if k in self.FK and isinstance(v, int) else v) for k, v in body.items() if k != "changelog_message"}
        if req.method == "PATCH":
            table, oid = path.rsplit("/", 1)
            row = next(r for r in self.data[table] if r["id"] == int(oid))
            if "primary_ip4" in fields:
                primary = fields.pop("primary_ip4")
                ip = next(i for i in self.data["ipam/ip-addresses"] if i["id"] == primary)
                row["primary_ip"] = {"id": ip["id"], "address": ip["address"]}
            row["custom_fields"] = {**row.get("custom_fields", {}), **fields.pop("custom_fields", {})}   # NetBox merges a partial dict
            row.update(fields, last_updated=self.stamp())
            return httpx.Response(200, json=row)
        new = {"id": 1 + max((r["id"] for r in self.data[path]), default=0), **fields}
        if path == "dcim/cables":
            new.update(type=body.get("type"), label="", status={"value": "connected"})
            for t in body["a_terminations"] + body["b_terminations"]:
                table = "dcim/interfaces" if t["object_type"] == "dcim.interface" else "circuits/circuit-terminations"
                next(r for r in self.data[table] if r["id"] == t["object_id"])["cable"] = {"id": new["id"]}
        elif path == "circuits/circuits":
            new.update(type={**new["type"], "name": "Internet"}, status={"value": "active"}, provider_account=None, install_date=None,
                       commit_rate=None, custom_fields=dict(CF_CIRCUIT))
        elif path == "circuits/circuit-terminations":
            new["cable"] = None
        self.data[path].append(new)
        return httpx.Response(201, json=new)


def office(tmp_path, monkeypatch, data=None):
    box = Office(data or mill_lane(), ALL)
    app = create_app(Settings("http://netbox.invalid", "nbt_x.y", True, None, cache_ttl=0, state_dir=tmp_path, estate_ttl=60),
                     transport=httpx.MockTransport(box))
    monkeypatch.setattr(questroutes, "FRESH_SITE_S", 0.0)
    monkeypatch.setattr(questroutes, "FRESH_ALL_S", 0.0)
    cl = TestClient(base_url=LOCAL, app=app, raise_server_exceptions=False)
    cl.__enter__()
    sign_in(cl)
    return cl, box


def say(cl, quest_id: str, *answers: tuple[str, dict[str, Any]]) -> dict[str, Any]:
    q: dict[str, Any] = {}
    for step, a in answers:
        q = post(cl, "answer", quest_id, step=step, **a)
        assert q["step"] is None or q["step"]["id"] != step or q["status"] == "parked", (step, q["step"])
    return q


WAY_OUT, HANDOFF, RECORD = "q:site_no_way_out:site:1", "q:circuit_handoff:circuit:1", "q:circuit_record:circuit:1"
CHAIN = "chain:way_out:site:1"
SECRETS = ("44-1021", "241.37", "505.5", "0800 000 0000", "Northlight NOC", "Northlight Billing", "bills@northlight.example")


def stored(tmp_path) -> bytes:
    """Everything the game has on disk: the store and its journal files."""
    return b"".join(p.read_bytes() for p in sorted(tmp_path.rglob("*")) if p.is_file())


def test_the_example_estate_plays_the_way_out_from_nothing_to_the_complete_record(tmp_path, monkeypatch, caplog):
    caplog.set_level(logging.DEBUG)
    cl, box = office(tmp_path, monkeypatch)
    b = board(cl)
    chain = next(c for c in b["chains"] if c["id"] == CHAIN)
    assert chain["bonus_xp"] == chains.CHAIN_XP["way_out"]                    # this player's changes are written: the whole record can pay
    assert chain["label"] == "1 of 3: physical" and [q["kind"] for q in b["quests"]] == ["site_no_way_out", "locations_not_linked", "device_unconnected"]
    assert b["quests"][0]["chain"]["label"] == "1 of 3: physical"
    # ---- physical: the device, the port, the circuit's end, the cable
    q = say(cl, WAY_OUT, ("device", {"choice": "device:1"}), ("provider", {"choice": "provider:1"}), ("circuit_id", {"text": "NLT-7001"}),
            ("circuit_type", {"choice": "type:1"}), ("local_port", {"choice": "interface:11"}), ("cable_type", {"choice": "smf"}))
    assert q["status"] == "ready"
    v = post(cl, "apply", WAY_OUT)
    assert (v["complete"], v["xp_awarded"], v["chain_xp"], v["chain"]["label"]) == (True, 120, 0, "2 of 3: logical")
    assert v["message"] == 'NetBox confirms it. Next in the chain "The way out of Mill Lane": 2 of 3: logical.'
    # ---- logical: the address on it; the stage that has just unlocked is the one on offer, and it comes first: a
    # connection that has been begun is carried on before the rooms' link, which ranks above an address otherwise
    b = board(cl)
    assert b["next_quest_id"] == HANDOFF and [q["id"] for q in b["quests"]][0] == HANDOFF
    assert next(q for q in b["quests"] if q["id"] == HANDOFF)["chain"]["label"] == "2 of 3: logical"
    assert RECORD not in [q["id"] for q in b["quests"]]                      # the administrative stage is still locked
    say(cl, HANDOFF, ("has", {"choice": "typed"}), ("addr", {"text": "203.0.113.2/30"}), ("prefix", {"choice": "yes"}))
    v = post(cl, "apply", HANDOFF)
    assert (v["complete"], v["xp_awarded"], v["chain"]["label"]) == (True, 60, "3 of 3: administrative")
    # ---- administrative: whose network, the account, the terms, the support desk and the one-off charge; the other two
    # contacts and the monthly charge are left blank
    b = board(cl)
    assert b["next_quest_id"] == RECORD and next(q for q in b["quests"] if q["id"] == RECORD)["step"]["id"] == "network"
    q = say(cl, RECORD, ("network", {"choice": "new"}), ("network_name", {"text": "Northlight Internet"}),
            ("account", {"choice": "new"}), ("account_no", {"text": "44-1021"}), ("contact_for", {"choice": "provider"}),
            ("contact", {"values": {"contact:support:name": "Northlight NOC", "contact:support:phone": "0800 000 0000"}}),
            ("terms", {"values": {"install_date": "2024-03-01", "commit_rate": 100}}), ("cost", {"values": {"nrc": 505.5, "currency": "gbp"}}))
    assert q["status"] == "ready" and q["changes"][-1] == "Circuit NLT-7001: account 44-1021, installed 2024-03-01, commit rate 100 Mbps, 505.50 GBP once."
    # a blank field is "not known yet" at once, before anything is written
    not_known = "not known yet: the account manager, the billing contact and the monthly charge"
    assert next(c for c in board(cl)["chains"] if c["id"] == CHAIN)["label"] == "3 of 3: administrative"   # the rest is still to be written
    # what was typed for the account, the contact and the cost is in the proxy's memory, never in the game's store
    assert not [s for s in SECRETS if s.encode() in stored(tmp_path)]
    v = post(cl, "apply", RECORD)
    assert (v["complete"], v["verified"], v["quest"]["status"], v["chain_xp"], v["xp_awarded"]) == (False, True, "parked", 0, 120)
    assert v["chain"]["status"] == "open_questions" and v["chain"]["label"] == f"3 of 3: administrative ({not_known})"
    assert v["message"] == (f'NetBox confirms 6 of 9; the rest of this line stays open. The chain "The way out of Mill Lane" stays open at '
                            f'3 of 3: administrative ({not_known}).')
    # the line waits at the first blank; it does not ask for the blanks a second time before it counts them as not known
    assert v["quest"]["step"]["id"] == "contact" and [f["id"] for f in v["quest"]["step"]["fields"]][::4] == ["contact:account:name", "contact:billing:name"]
    assert v["quest"]["note"] == "Not known yet for circuit NLT-7001: the account manager, the billing contact and the monthly charge. The line waits here; nothing is lost."
    # NetBox's own models hold it
    circ = box.data["circuits/circuits"][0]
    assert (circ["provider_account"], circ["install_date"], circ["commit_rate"]) == ({"id": 1}, "2024-03-01", 100000)
    assert circ["custom_fields"] == {"rw_mrc": None, "rw_nrc": 505.5, "rw_currency": "GBP"}   # numbers: what a decimal field takes
    assert box.data["circuits/provider-accounts"] == [{"id": 1, "provider": {"id": 1}, "account": "44-1021"}]
    assert box.data["circuits/provider-networks"] == [{"id": 1, "provider": {"id": 1}, "name": "Northlight Internet"}]
    assert box.data["circuits/circuit-terminations"][1] | {"cable": None} == {
        "id": 2, "circuit": {"id": 1}, "term_side": "Z", "termination_type": "circuits.providernetwork", "termination_id": 1, "cable": None}
    assert box.data["tenancy/contact-assignments"] == [{"id": 1, "object_type": "circuits.provider", "object_id": 1, "contact": {"id": 1}, "role": {"id": 1}}]
    # ---- three facts are not known: the chain is honestly open, and that does not hold the site's completeness back
    b = board(cl)
    site = b["sites"][0]
    waiting = next(q for q in b["quests"] if q["id"] == RECORD)
    assert waiting["status"] == "parked"
    assert next(c for c in b["chains"] if c["id"] == CHAIN)["open_questions"] == ["the account manager", "the billing contact", "the monthly charge"]
    open_checks = {c for c, ok in findings.analyse(questroutes_estate(cl)).checks[1].items() if not ok}
    assert {"circuit:1:contact_account", "circuit:1:contact_billing", "circuit:1:mrc"} <= open_checks
    assert site["completeness"]["open"] == len(open_checks) - 3
    # ---- later the bill turns up: the same desk is the account manager too (one contact, a second role), and billing is new
    q = say(cl, RECORD, ("contact", {"values": {"contact:account:name": "northlight noc", "contact:billing:name": "Northlight Billing",
                                                "contact:billing:email": "bills@northlight.example"}}),
            ("contact_is_1", {"choice": "yes"}),   # asked: "NetBox already has a contact called Northlight NOC ... Is that who you mean?"
            ("cost", {"values": {"mrc": 241.37}}))
    assert q["status"] == "ready" and q["changes"][-1] == "Circuit NLT-7001: 241.37 GBP a month."
    assert not [s for s in SECRETS if s.encode() in stored(tmp_path)]
    v = post(cl, "apply", RECORD)
    assert (v["complete"], v["chain"]["status"], v["chain"]["label"]) == (True, "complete", "3 of 3: complete")
    # the whole record pays above its stages, once: every stage was this player's
    assert v["chain_xp"] == chains.CHAIN_XP["way_out"] == v["chain"]["bonus_xp"] and v["xp_awarded"] == v["chain_xp"] + 72
    assert circ["custom_fields"] == {"rw_mrc": 241.37, "rw_nrc": 505.5, "rw_currency": "GBP"}
    assert [(a["contact"]["id"], a["role"]["id"]) for a in box.data["tenancy/contact-assignments"]] == [(1, 1), (1, 2), (2, 3)]
    summary = ("Mill Lane: ml-sw1 Te1/1/1 -> circuit NLT-7001 (Internet), Northlight Telecom, network Northlight Internet, your address 203.0.113.2/30, "
               "account 44-1021, Support / NOC: Northlight NOC 0800 000 0000, Billing: Northlight Billing bills@northlight.example, "
               "241.37 GBP a month, 505.50 GBP once, installed 2024-03-01, 100 Mbps committed.")
    assert v["chain"]["summary"] == summary and v["message"].endswith(f'That completes the chain "The way out of Mill Lane". {summary}')
    assert post(cl, "verify", RECORD)["chain_xp"] == 0                         # checked again: nothing is paid twice
    done = next(c for c in board(cl)["chains"] if c["id"] == CHAIN)
    assert done["status"] == "complete" and done["summary"] == summary
    # nothing of the player's own figures was stored or logged, at any point
    assert not [s for s in SECRETS if s.encode() in stored(tmp_path)]
    assert not [s for s in SECRETS for r in caplog.records if s in r.getMessage()]
    # every write carried the player's name for the changelog
    assert {body["changelog_message"].split(":")[0] for _, _, body in box.writes} == {"rackwalker (walker)"}


def questroutes_estate(cl) -> Estate:
    """What the proxy would analyse now: a fresh read of the made-up NetBox behind this client."""
    import asyncio

    from rackwalker_proxy import estate as estate_mod

    async def go() -> Estate:
        return await estate_mod.read_estate(cl.app.state.netbox)
    return asyncio.run(go())


def test_how_a_device_is_managed_is_written_to_the_device_and_its_model(tmp_path, monkeypatch):
    data = mill_lane()
    # ml-sw1 and ml-sw2 are cabled to each other, so both are checked for ports in use with no cable
    data["dcim/cables"].append({"id": 1, "type": "smf", "label": "", "status": {"value": "connected"},
                                "a_terminations": [{"object_type": "dcim.interface", "object_id": 12}],
                                "b_terminations": [{"object_type": "dcim.interface", "object_id": 22}]})
    for p in data["dcim/interfaces"]:
        if p["id"] in (12, 22):
            p["cable"] = {"id": 1}
    cl, box = office(tmp_path, monkeypatch, data)
    line = "q:device_unreachable:device:1"
    q = say(cl, line, (f"mode_1", {"choice": "local"}), ("model_1", {"choice": "all"}), ("ip_1", {"choice": "typed"}),
            ("addr_1", {"text": "192.0.2.11/24"}), ("port_1", {"choice": "interface:13"}))
    assert q["changes"] == ["ml-sw1: managed locally. Every EX-A2400 is marked as managed locally by default.",
                            "ml-sw1: 192.0.2.11/24 on Te1/1/3, as its management address (primary IP)."]
    v = post(cl, "apply", line)
    assert (v["complete"], v["xp_awarded"]) == (True, 40)
    assert data["dcim/devices"][0]["custom_fields"] == {"rw_mgmt_mode": "local", "rw_mgmt_service": None}
    assert data["dcim/device-types"][0]["custom_fields"]["rw_mgmt_default"] == "local"
    patches = [(p, body) for m, p, body in box.writes if m == "PATCH"]
    assert [p for p, _ in patches] == ["dcim/device-types/1", "dcim/devices/1", "dcim/devices/1"]
    b = board(cl)
    # the model's default now says how the other two are managed: they are asked for their address, not for the mode again
    other = say(cl, "q:device_unreachable:device:2", ("ip_2", {"choice": "unknown"}))
    assert other["status"] == "parked" and other["purpose"].startswith("ml-sw2 is managed locally, but NetBox has no address to log in to it.")
    # ml-sw2 takes "local" from its model: only the address is written, on a front-panel port
    q = say(cl, "q:device_unreachable:device:2", ("ip_2", {"choice": "typed"}), ("addr_2", {"text": "192.0.2.12/24"}), ("port_2", {"choice": "interface:23"}))
    assert q["changes"] == ["ml-sw2: 192.0.2.12/24 on Te1/1/3, as its management address (primary IP)."]
    assert post(cl, "apply", "q:device_unreachable:device:2")["complete"]
    assert data["dcim/devices"][1]["custom_fields"]["rw_mgmt_mode"] is None
    # Te1/1/3 of each has an address and no cable, and neither is listed as an uncabled port for that: not the one whose
    # device says "local" itself, and not the one that has it from its model
    gaps = cl.get("/api/quests/gaps/1").json()
    assert [g["id"] for g in gaps["items"] if g["field"] == "cable"] == [] and b["setup"] == []
    assert "interface_no_cable" not in [f["kind"] for f in board(cl)["findings"]]


def test_netbox_without_the_custom_fields_is_told_so_and_nothing_is_asked_that_it_cannot_hold(tmp_path, monkeypatch):
    data = mill_lane()
    for d in data["dcim/devices"]:
        d["custom_fields"] = {}
    data["dcim/device-types"][0]["custom_fields"] = {}
    cl, box = office(tmp_path, monkeypatch, data)
    b = board(cl)
    assert b["setup"] == ["NetBox has no custom field rw_mgmt_mode on devices, so how a device is managed cannot be recorded there. "
                          "The setup script (devnetbox/seed.py --fields-only) adds it."]
    q = say(cl, "q:device_unreachable:device:1", ("mode_1", {"choice": "cloud"}), ("service_1", {"choice": "unknown"}))
    # remembered by the game for this player; nothing is written
    assert q["status"] == "dismissed" and "the game remembers your answer" in q["note"] and box.writes == []
    left = {c for c, ok in findings.analyse(questroutes_estate(cl)).checks[1].items() if not ok}
    assert "device:1:primary_ip" in left and board(cl)["sites"][0]["completeness"]["open"] == len(left) - 1


# ---------------------------------------------------------------- the practice island: one whole short chain
def test_the_island_teaches_one_whole_chain_with_a_made_up_bill(tmp_path):
    cl = fixture_client(tmp_path)
    cl.headers["X-RackWalker-CSRF"] = fixture_sign_in(cl)

    def answer(quest_id: str, *answers: tuple[str, dict[str, Any]]) -> dict[str, Any]:
        for step, a in answers:
            r = cl.post("/api/game/quests/answer", json={"scope": "practice", "quest_id": quest_id, "step": step, **a})
            assert r.status_code == 200, r.text
        return r.json()

    def apply(quest_id: str) -> dict[str, Any]:
        r = cl.post("/api/game/quests/apply", json={"scope": "practice", "quest_id": quest_id})
        assert r.status_code == 200, r.text
        schema_validator("QuestVerdict").validate(r.json())
        return r.json()

    answer(f"q:site_no_way_out:site:{ISLAND}", ("device", {"choice": "device:9000001"}), ("provider", {"choice": "provider:9000001"}),
           ("circuit_id", {"text": "EXF-20417"}), ("circuit_type", {"choice": "type:9000001"}), ("local_port", {"choice": "interface:9000026"}),
           ("cable_type", {"choice": "smf"}))
    assert apply(f"q:site_no_way_out:site:{ISLAND}")["chain"]["label"] == "2 of 3: logical"
    answer("q:circuit_handoff:circuit:9000001", ("has", {"choice": "typed"}), ("addr", {"text": "203.0.113.2/30"}), ("prefix", {"choice": "yes"}))
    assert apply("q:circuit_handoff:circuit:9000001")["chain"]["label"] == "3 of 3: administrative"
    rec = "q:circuit_record:circuit:9000001"
    q = answer(rec, ("network", {"choice": "network:9000001"}), ("account", {"choice": "new"}))
    # on the island no figure is typed: the made-up bill is the only answer on offer besides "I don't know yet"
    assert q["step"]["input"] == "choice" and [c["label"] for c in q["step"]["choices"]] == ["The bill says PI-ACC-0042", "I don't know yet"]
    q = answer(rec, ("account_no", {"choice": "sample"}), ("contact_for", {"choice": "provider"}))
    assert q["step"]["prompt"] == ("Who are they? The bill names Example Fibre NOC (support), Example Fibre Accounts (account manager) and "
                                   "Example Fibre Billing (billing). (Practice: the island's made-up bill is the only paper here.)")
    assert [c["label"] for c in q["step"]["choices"]] == ["Those the bill names", "I don't know yet"]
    q = answer(rec, ("contact", {"choice": "sample"}))
    # the contract's date and speed are on the bill too: the island never asks a player to make a figure up
    assert q["step"]["id"] == "terms" and q["step"]["input"] == "choice" and q["step"]["fields"] == []
    assert q["step"]["prompt"].startswith("What does the contract for circuit EXF-20417 say? The island's bill says: installed 2024-03-01 and 100 Mbps committed.")
    assert [c["label"] for c in q["step"]["choices"]] == ["What the bill says", "Nothing more to record: leave it", "I don't know yet"]
    q = answer(rec, ("terms", {"choice": "sample"}))
    # XTS is the currency code kept for testing: the island's bill means no real money
    assert [c["label"] for c in q["step"]["choices"]] == ["The bill says 45.00 XTS a month, 120.00 XTS once", "No charge of its own to record",
                                                          "I don't know yet"]
    q = answer(rec, ("cost", {"choice": "unknown"}))
    v = apply(rec)
    assert (v["mode"], v["complete"], v["quest"]["status"], v["chain"]["status"]) == ("practice", False, "parked", "open_questions")
    # with the rest of the island put in order, its Confusion is ready although the cost is still an open question
    from .test_quest_routes import play_line
    for _ in range(20):
        b = cl.get("/api/game/quests", params={"scope": "practice"}).json()
        nxt = next((x for x in b["quests"] if x["status"] in ("offered", "active")), None)
        if nxt is None:
            break
        apply(play_line(cl, nxt)["id"])
    site = b["sites"][0]
    assert (site["completeness"]["open"], site["completeness"]["percent"], site["boss_ready"]) == (0, 100, True) and not b["all_in_order"]
    assert [q["id"] for q in b["quests"] if q["status"] == "parked"] == [rec]
    chain = next(c for c in b["chains"] if c["id"] == f"chain:way_out:site:{ISLAND}")
    assert chain["label"] == "3 of 3: administrative (not known yet: the monthly charge and the one-off charge)"
    v = (answer(rec, ("cost", {"choice": "sample"})), apply(rec))[1]
    assert (v["complete"], v["xp_awarded"], v["chain"]["status"]) == (True, 0, "complete")      # practice never pays, chain or not
    assert v["chain"]["summary"] == ("Practice Island: sw-quay-1 Te1/1/2 -> circuit EXF-20417 (Internet), Example Fibre, network Example Fibre Internet, "
                                     "your address 203.0.113.2/30, account PI-ACC-0042, Support / NOC: Example Fibre NOC +00 555 0100, "
                                     "Account manager: Example Fibre Accounts accounts@fibre.example, Billing: Example Fibre Billing billing@fibre.example, "
                                     "45.00 XTS a month, 120.00 XTS once, installed 2024-03-01, 100 Mbps committed.")
    assert cl.get("/api/game/quests", params={"scope": "practice"}).json()["all_in_order"]


# ---------------------------------------------------------------- saves made before chains
def old_row(db: sqlite3.Connection, quest_id: str, status: str, answers: dict, checks: list[str], verified: list[str], evidence: list[dict]) -> None:
    snap = {"id": quest_id, "kind": "device_unreachable", "rank": "connectivity", "site_id": 1, "title": "How to reach the kit at Mill",
            "purpose": "p", "outcome": "o", "xp": 120, "minutes": 4,
            "finding": {"id": quest_id[2:], "kind": "device_unreachable", "rank": "connectivity", "site_id": 1, "title": "t", "evidence": evidence, "data": {}}}
    db.execute("INSERT INTO quest VALUES (1, 'netbox', ?, 1, ?, ?, ?, ?, ?, '2026-11-09T10:00:00Z')",
               (quest_id, status, json.dumps(answers), json.dumps(checks), json.dumps(verified), json.dumps(snap)))


def test_a_save_from_before_chains_keeps_its_answers_one_line_per_device(tmp_path):
    path = tmp_path / "game.sqlite3"
    db = sqlite3.connect(path, isolation_level=None)
    migrate(db, MIGRATIONS[:3])
    db.execute("INSERT INTO player VALUES (1, 'walker', NULL, 'x', 'x')")
    ev = [{"kind": "device", "id": i, "name": f"sw{i}"} for i in (2, 3, 4, 5)]
    checks = [f"device:{i}:primary_ip" for i in (2, 3, 4, 5)]
    # sw2: "nobody logs in to it"; sw3: an address half given; sw4: confirmed by NetBox and paid; sw5: never asked about
    old_row(db, "q:device_unreachable:site:1", "active", {"ip_2": {"choice": "none"}, "ip_3": {"choice": "typed"}, "addr_3": {"text": "192.0.2.3/24"}},
            checks, ["device:4:primary_ip"], ev)
    old_row(db, "q:rack_audit:rack:1", "parked", {"labels": {"values": {}}}, ["device:2:serial"], [], ev[:1])
    db.close()
    store = GameStore(path)                                # opening the file migrates it
    rows = store.quests(1, "netbox")
    assert sorted(rows) == ["q:device_unreachable:device:2", "q:device_unreachable:device:3", "q:device_unreachable:device:4", "q:rack_audit:rack:1"]
    two, three, four = (rows[f"q:device_unreachable:device:{i}"] for i in (2, 3, 4))
    # "It has none: nobody logs in to it" is now the answer to how it is managed
    assert (two["status"], two["answers"], two["checks"]) == ("active", {"mode_2": {"choice": "unmanaged"}}, ["device:2:primary_ip"])
    assert (three["status"], three["answers"]) == ("active", {"mode_3": {"choice": "local"}, "ip_3": {"choice": "typed"}, "addr_3": {"text": "192.0.2.3/24"}})
    assert (four["status"], four["verified"]) == ("done", ["device:4:primary_ip"]) and four["snapshot"]["title"] == "How sw4 is managed"
    assert rows["q:rack_audit:rack:1"]["status"] == "parked"                        # every other line is untouched
    # the migrated answer still counts as answered for the player, and can now be written to the device
    b = Build()
    s = b.site()
    r, sw = linked_pair(b, s)
    b.e.devices[sw].update(primary_ip=None)
    line = one(b.e, "device_unreachable")
    answers = {f"mode_{sw}": two["answers"]["mode_2"]}
    assert quests.waived(line, answers) == [f"device:{sw}:primary_ip"]
    st = quests.evaluate(b.e, line, answers)
    assert st["status"] == "ready" and st["proposal"]["ops"][0]["fields"] == {"mode": "unmanaged"}
    # a second start changes nothing more
    store.close()
    assert sorted(GameStore(path).quests(1, "netbox")) == sorted(rows)


@pytest.mark.parametrize("kind", ["way_out", "device", "rack", "room_link"])
def test_every_chain_kind_says_what_its_record_will_hold_and_is_worth_more_than_a_stage(kind):
    assert kind in chains.KINDS and chains.holds(kind, list(chains.STAGES))
    assert chains.CHAIN_XP[kind] == 0 or chains.CHAIN_XP[kind] >= 100


def test_a_chain_promises_only_what_its_stages_record():
    from .test_quests import sites_not_linked

    e = sites_not_linked()
    link = next(v for v in views(e).values() if v["kind"] == "site_link")
    # two sites with nothing between them: one stage, so no address, carrier, account or cost is promised, and no bonus
    assert (link["label"], link["holds"], link["bonus_xp"]) == ("1 of 1: physical", "the port and the circuit that join the sites", 0)
    rack = next(v for v in views(practice.example_estate()).values() if v["kind"] == "rack")
    assert rack["holds"] == ("where the rack stands, which unit each device is in, and what powers it; the serial number and asset tag of each device")
    assert rack["bonus_xp"] == chains.CHAIN_XP["rack"]


def test_a_chain_that_was_complete_all_along_pays_nobody_and_one_stage_is_a_loose_stage():
    e = copy.deepcopy(practice.example_estate(complete=True))
    a = findings.analyse(e)
    rack = next(c for c in chains.build(e, a, quests.lines(e, a)) if c["kind"] == "rack")
    assert chains.verified(a, rack) and chains.view(e, a, rack)["status"] == "complete"
    # the bonus needs two stages confirmed for this player: see the example estate above for one that pays, and
    # test_estate_read for a device chain whose single played stage pays its own XP only
    assert chains.CHAIN_XP["room_link"] == 0
