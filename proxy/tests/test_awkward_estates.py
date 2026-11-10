"""Estates that are shaped unlike the tidy examples: patch-panel trunks, a carrier's line that lands on a panel, private
lines between two of the estate's own sites, sites that hang off one carrier network, stacks, circuits that are switched
off, boxes whose role says nothing. Each test builds the shape and states what the game says about it."""
from __future__ import annotations

import time

from rackwalker_proxy import chains, findings, quests
from rackwalker_proxy.estate import Components, simulate

from .test_quests import Build, circuit_record, linked_pair, one, play, settle
from .test_scanner_rules import site_with_room, stack


def kinds(e, *only: str) -> list[str]:
    return [f["id"] for f in findings.analyse(e).findings if not only or f["kind"] in only]


def panel(b: Build, site: int, name: str, room=None, rack=None, strands: int = 1) -> tuple[int, tuple[str, int], list[tuple[str, int]]]:
    """A patch panel with one rear port of `strands` positions and a front port per strand."""
    pp = b.dev(site, name, "patch-panel", room, rack, ports=0)
    rear = b.port(pp, "rear_port", "Trunk", positions=strands)
    fronts = [b.port(pp, "front_port", f"F{n}", rear_ids=[rear[1]], rear=[(rear[1], n)]) for n in range(1, strands + 1)]
    return pp, rear, fronts


# ---------------------------------------------------------------- the connectivity graph
def test_the_strands_of_a_patch_panel_trunk_are_separate_wires():
    b = Build()
    s, room, rack = site_with_room(b)
    sw1, sw2, sw3 = (b.dev(s, f"sw{n}", "switch", room, rack) for n in (1, 2, 3))
    _, rear_a, fronts_a = panel(b, s, "pp-a", room, rack, strands=2)
    _, rear_b, fronts_b = panel(b, s, "pp-b", room, rack, strands=2)
    b.cable(b.p(sw1, "eth1"), fronts_a[0])
    b.cable(b.p(sw2, "eth1"), fronts_a[1])
    comp = b.e.components()
    # two switches on two strands of one trunk are not linked by it: NetBox's own trace ends at the trunk's open end
    assert not comp.same(sw1, sw2)
    assert sorted(kinds(b.e, "switch_no_uplink")) == [f"switch_no_uplink:device:{sw1}", f"switch_no_uplink:device:{sw2}"]
    # the trunk joins strand to strand: sw3 on strand 1 of the far panel reaches sw1, never sw2
    b.cable(rear_a, rear_b)
    b.cable(b.p(sw3, "eth1"), fronts_b[0])
    b.e.touch()
    comp = b.e.components()
    assert comp.same(sw1, sw3) and not comp.same(sw2, sw3)
    assert kinds(b.e, "switch_no_uplink") == [f"switch_no_uplink:device:{sw2}"]


def test_only_a_network_device_is_a_way_through():
    for role in ("database", "appliance", "nvr", "ups", "console", "server"):
        b = Build()
        s, room, rack = site_with_room(b)
        sw1, sw2 = b.dev(s, "sw1", "switch", room, rack), b.dev(s, "sw2", "switch", room, rack)
        box = b.dev(s, "box1", role, room, rack, ports=2)
        b.cable(b.p(sw1, "eth1"), b.p(box, "eth1"))
        b.cable(b.p(sw2, "eth1"), b.p(box, "eth2"))
        # two switches that share only a dual-homed box are not linked, whatever the box's role is called
        assert not b.e.components().same(sw1, sw2), role
        assert len(kinds(b.e, "switch_no_uplink")) == 2, role


def test_a_private_line_between_two_of_your_sites_is_a_link_and_no_way_out():
    b = Build()
    north, south = b.site("North"), b.site("South")
    n1, s1 = b.dev(north, "n-sw1", "switch"), b.dev(south, "s-sw1", "switch")
    c = b.circuit("P2P-1")
    b.cable(b.p(n1, "eth1"), b.term(c, "A", north))
    b.cable(b.p(s1, "eth1"), b.term(c, "Z", south))
    a = findings.analyse(b.e)
    # the sites are linked, and NetBox still says nothing about how either reaches the outside world
    assert a.check(f"sites_not_linked:site:{north}") is True and a.check(f"sites_not_linked:site:{south}") is True
    assert kinds(b.e, "site_no_way_out") == [f"site_no_way_out:site:{north}", f"site_no_way_out:site:{south}"]
    # an internet line at one of them is the way out of both: the other reaches it over the private line
    out = b.circuit("INET-1")
    b.cable(b.p(n1, "eth2"), b.term(out, "A", north))
    b.e.touch()
    a = findings.analyse(b.e)
    assert kinds(b.e, "site_no_way_out") == [] and a.check(f"site_no_way_out:site:{south}") is True


def test_sites_that_hang_off_one_carrier_network_are_linked_through_it():
    b = Build()
    north, south = b.site("North"), b.site("South")
    for site, name, net in ((north, "n", 500), (south, "s", 500)):
        r = b.dev(site, f"{name}-rtr", "router")
        c = b.circuit(f"MPLS-{name}")
        b.cable(b.p(r, "eth1"), b.term(c, "A", site))
        z = b.term(c, "Z", None)
        b.e.cterms[z[1]]["network_id"] = net
        b.e.networks[net] = {"id": net, "provider_id": b.e.circuits[c]["provider_id"], "name": "Carrier MPLS"}
    assert kinds(b.e, "sites_not_linked") == []
    # two circuits on two different networks join nothing
    other = max(b.e.cterms)
    b.e.cterms[other]["network_id"] = 501
    b.e.touch()
    assert kinds(b.e, "sites_not_linked") == [f"sites_not_linked:site:{north}", f"sites_not_linked:site:{south}"]


def test_a_decommissioned_circuit_is_no_way_out_and_is_asked_nothing_about():
    b = Build()
    s, room, rack = site_with_room(b)
    r, sw = linked_pair(b, s, room, rack)
    old = b.circuit("OLD-1")
    b.cable(b.term(old, "A", s), b.p(r, "eth2"))
    b.e.circuits[old]["status"] = "decommissioned"
    gone = b.circuit("OLD-2")
    b.e.circuits[gone]["status"] = "decommissioned"
    a = findings.analyse(b.e)
    assert [f["kind"] for f in a.findings] == ["site_no_way_out"]
    assert not [c for checks in a.checks.values() for c in checks if c.startswith(("circuit:", "circuit_handoff", "circuit_unterminated"))]


# ---------------------------------------------------------------- a carrier's line that lands on a patch panel
def demarc(lead_to_router: bool) -> tuple[Build, int, int, int, tuple[str, int]]:
    b = Build()
    s, room, rack = site_with_room(b)
    r, sw = linked_pair(b, s, room, rack)
    _, rear, fronts = panel(b, s, "demarc-pp", room, rack)
    c = b.circuit("NLT-1")
    b.cable(b.term(c, "A", s), fronts[0])
    if lead_to_router:
        b.cable(rear, b.p(r, "eth2"))
    return b, s, r, c, rear


def test_a_circuit_patched_through_a_panel_is_asked_for_the_address_on_the_router_port():
    b, s, r, c, _ = demarc(lead_to_router=True)
    a = findings.analyse(b.e)
    hand = next(f for f in a.findings if f["kind"] == "circuit_handoff")
    assert hand["title"] == "Circuit NLT-1 from Carrier plugs into rtr1 eth2, but NetBox has no address on that port."
    assert hand["data"]["interface_id"] == b.p(r, "eth2")[1]
    assert a.check(f"circuit_unterminated:circuit:{c}") is True and a.check(f"site_no_way_out:site:{s}") is True
    way = next(ch for ch in chains.build(b.e, a, quests.lines(b.e, a)) if ch["kind"] == "way_out")
    v = chains.view(b.e, a, way)
    # the chain is not complete without the address, and its summary names the router port, not the panel
    assert v["label"] == "2 of 3: logical" and not chains.verified(a, way)
    assert v["summary"].startswith("Mill: rtr1 eth2 -> circuit NLT-1 (Internet), Carrier")


def test_a_circuit_that_stops_at_a_panel_is_asked_which_device_it_goes_on_to():
    b, s, r, c, rear = demarc(lead_to_router=False)
    a = findings.analyse(b.e)
    f = next(f for f in a.findings if f["kind"] == "circuit_unterminated")
    assert f["title"] == "Circuit NLT-1 from Carrier is patched to demarc-pp F1, but NetBox does not say which device it goes on to."
    # nothing is asked about an address or a record before the line reaches a device of yours
    assert not [x for x in a.findings if x["kind"] in ("circuit_handoff", "circuit_record")]
    way = next(ch for ch in chains.build(b.e, a, quests.lines(b.e, a)) if ch["kind"] == "way_out")
    assert chains.view(b.e, a, way)["label"] == "1 of 3: physical"
    line = one(b.e, "circuit_unterminated")
    first = quests.evaluate(b.e, line, {})["step"]
    assert first["prompt"] == "Circuit NLT-1 is patched as far as demarc-pp Trunk at Mill. Which device does it go on to?"
    state, _ = play(b.e, line)
    op = state["proposal"]["ops"]
    assert [o["op"] for o in op] == ["connect_cable"] and (op[0]["fields"]["b_kind"], op[0]["fields"]["b_id"]) == rear
    assert settle(b.e, line, state) == []
    # once the lead is on record the line asks for the address, on the port the player named
    after = b.e.copy()
    simulate(after, op)
    assert [x["kind"] for x in findings.analyse(after).findings if x["kind"].startswith("circuit")] == ["circuit_handoff", "circuit_record"]


# ---------------------------------------------------------------- stacks
def test_a_stack_said_to_be_managed_locally_keeps_its_question_until_it_has_an_address():
    b, s, sw, member = stack()
    for d in (sw, member):
        b.e.devices[d].update(vc_id=5, primary_ip=None)
    b.e.devices[sw]["mgmt_mode"] = "local"
    a = findings.analyse(b.e)
    f = next(f for f in a.findings if f["kind"] == "device_unreachable")
    assert f["title"] == "sw1 is managed locally, but NetBox has no address to log in to it." and a.check(f"device:{sw}:primary_ip") is False
    assert [g["id"] for g in a.gaps if g["field"] == "primary_ip"] == [f"device:{sw}:primary_ip"]
    # the mode is on record, so the line goes straight to the address
    assert quests.evaluate(b.e, one(b.e, "device_unreachable"), {})["step"]["id"] == f"ip_{sw}"
    # an address on any member, or a member a cloud service manages, settles the stack: one passed check
    b.e.devices[member]["primary_ip"] = "192.0.2.9/24"
    b.e.touch()
    a = findings.analyse(b.e)
    assert a.check(f"device:{sw}:primary_ip") is True and not [f for f in a.findings if f["kind"] == "device_unreachable"]


def test_a_stack_whose_first_member_is_switched_off_is_still_a_switch():
    b, s, sw, member = stack()
    for d in (sw, member):
        b.e.devices[d].update(vc_id=5, primary_ip=None)
    b.e.devices[sw]["status"] = "offline"
    b.e.cables.clear()
    for p in b.e.ports.values():
        p["cable_id"] = None
    srv = next(i for i, d in b.e.devices.items() if d["name"] == "srv1")
    b.cable(b.p(member, "eth1"), b.p(srv, "eth1"))
    b.e.touch()
    # the live member speaks for the stack: its uplink and how it is managed are still asked
    assert kinds(b.e, "switch_no_uplink", "device_unreachable") == [f"switch_no_uplink:device:{member}", f"device_unreachable:device:{member}"]


# ---------------------------------------------------------------- one missing fact is one line
def test_a_line_between_two_sites_with_one_end_not_plugged_in_is_one_question():
    b = Build()
    mill, kiln = b.site("Mill Lane"), b.site("Kiln Yard")
    m1, k1 = b.dev(mill, "ml-sw3", "switch"), b.dev(kiln, "ky-sw1", "switch")
    c = b.circuit("KY-MPLS-7")
    b.e.circuits[c]["type"] = "MPLS"
    b.cable(b.p(m1, "eth1"), b.term(c, "A", mill))
    b.term(c, "Z", kiln)
    a = findings.analyse(b.e)
    asked = [f for f in a.findings if f["kind"] in ("sites_not_linked", "circuit_unterminated")]
    # not "no link" at each site as well: the circuit's loose end is the one thing to find out
    assert [f["id"] for f in asked] == [f"circuit_unterminated:circuit:{c}"] and asked[0]["site_id"] == kiln
    assert asked[0]["title"] == ("Circuit KY-MPLS-7 from Carrier is plugged in at Mill Lane, but its end at Kiln Yard has no cable: "
                                 "NetBox does not say which device it plugs into there.")
    assert a.check(f"sites_not_linked:site:{mill}") is None
    state, _ = play(b.e, one(b.e, "circuit_unterminated"))
    after = b.e.copy()
    simulate(after, state["proposal"]["ops"])
    a = findings.analyse(after)
    assert a.check(f"sites_not_linked:site:{mill}") is True and a.check(f"sites_not_linked:site:{kiln}") is True


def test_two_ports_of_one_device_that_go_to_two_circuits_make_one_change_netbox_takes():
    b = Build()
    s, room, rack = site_with_room(b)
    r, sw = linked_pair(b, s, room, rack)
    for n in (2, 3):
        b.e.ports[b.p(r, f"eth{n}")]["description"] = f"WAN{n - 1}"
    c1, c2 = b.circuit("NLT-1"), b.circuit("SL-2", "Southlight")
    line = one(b.e, "interface_no_cable")
    p2, p3 = b.p(r, "eth2")[1], b.p(r, "eth3")[1]
    answers = {"what": {"choice": "each"}, f"peer_{p2}": {"choice": f"circuit:{c1}"}, f"cable_type_{p2}": {"choice": "smf"}}
    # the circuit the first port went to is not offered for the second: a circuit end takes one lead
    step = quests.evaluate(b.e, line, answers)["step"]
    assert step["id"] == f"peer_{p3}" and f"circuit:{c1}" not in [x["id"] for x in step["choices"]]
    answers |= {f"peer_{p3}": {"choice": f"circuit:{c2}"}, f"cable_type_{p3}": {"choice": "smf"}}
    state = quests.evaluate(b.e, line, answers)
    refs = [o["client_ref"] for o in state["proposal"]["ops"] if o["op"] == "create_circuit_termination"]
    assert state["status"] == "ready" and len(refs) == len(set(refs)) == 2
    assert settle(b.e, line, state) == []
    # the practice island refuses what NetBox refuses: a reference names one op
    twice = [dict(o, client_ref="handoff") if o["op"] == "create_circuit_termination" else o for o in state["proposal"]["ops"]]
    assert any("is used by an earlier op" in r["error"]["message"] for r in simulate(b.e.copy(), twice) if r["error"])


# ---------------------------------------------------------------- a circuit's record
def test_contact_roles_are_found_by_a_whole_word_never_by_a_part_of_one():
    e = circuit_record()
    e.contact_roles.clear()
    for n, name in enumerate(("Support / NOC", "Accounting", "Technical", "Emergency"), 1):
        e.contact_roles[n] = {"id": n, "name": name, "slug": name.lower().replace(" / ", "-")}
    e.touch()
    roles = findings.contact_roles(e)
    # "Accounting" is who sends the bill, not the account manager; with no account-manager role none is asked for
    assert {k: r["name"] for k, r in roles.items()} == {"support": "Support / NOC", "billing": "Accounting"}
    assert "contact_account" not in findings.circuit_facts(e, next(iter(e.circuits)))


def test_a_circuits_record_line_stands_for_the_facts_netbox_lacks_and_no_others():
    e = circuit_record()
    cid = next(iter(e.circuits))
    e.circuits[cid].update(install_date="2024-03-01", commit_rate=100000)
    e.touch()
    line = one(e, "circuit_record")
    held = {f"circuit:{cid}:install_date", f"circuit:{cid}:commit_rate"}
    assert not held & set(quests.check_ids(line)) and len(quests.check_ids(line)) == len(findings.CIRCUIT_FACTS) - 2
    assert all(findings.analyse(e).check(c) is True for c in held)   # still counted for the site, and part of the chain


def record_answers(**more) -> dict:
    return {"network": {"choice": "none"}, "account": {"choice": "none"}, "contact_for": {"choice": "circuit"}, "terms": {"choice": "none"},
            "cost": {"choice": "none"}, **more}


def test_there_is_none_is_an_answer_for_one_contact():
    e = circuit_record()
    cid = next(iter(e.circuits))
    line = one(e, "circuit_record")
    form = quests.evaluate(e, line, record_answers())["step"]
    assert form["id"] == "contact" and "Nobody else: there is no contact to record" in [c["label"] for c in form["choices"]]
    assert [f["id"] for f in form["fields"] if f["id"].endswith(":none")] == ["contact:support:none", "contact:account:none", "contact:billing:none"]
    values = {"contact:support:name": "Carrier Desk", "contact:account:none": "none"}
    answers = record_answers(contact={"values": values})
    st = quests.evaluate(e, line, answers)
    # the support desk is written, the account manager is settled as "none", billing is the one fact not known yet
    assert st["status"] == "ready" and [o["op"] for o in st["proposal"]["ops"]] == ["create_contact", "assign_contact"]
    assert f"circuit:{cid}:contact_account" in quests.waived(line, answers) and st["deferred"] == [f"circuit:{cid}:contact_billing"]
    # what the store keeps of the form (never its figures) still says so
    kept = {"unknown": quests.blank_facts("contact", values), "none": quests.none_facts(values)}
    assert kept == {"unknown": ["contact_billing"], "none": ["contact_account"]}
    assert f"circuit:{cid}:contact_account" in quests.waived(line, record_answers(contact=kept))
    # a contact is filled in or there is none, never both
    both = quests.evaluate(e, line, record_answers(contact={"values": {**values, "contact:account:name": "Someone"}}))
    assert both["step"]["id"] == "contact" and "not both" in both["step"]["error"]
    # the whole form can be answered "nobody else"
    nobody = record_answers(contact={"choice": "none"})
    assert quests.evaluate(e, line, nobody)["status"] == "dismissed" and len(quests.waived(line, nobody)) == len(findings.CIRCUIT_FACTS)


def test_a_typed_contact_never_becomes_another_contact_of_that_name_in_silence():
    e = circuit_record()
    line = one(e, "circuit_record")
    e.contacts[3] = {"id": 3, "name": "NOC", "phone": "+44 1632 960100", "email": ""}
    typed = {"contact:support:name": "noc", "contact:support:phone": "+44 1632 960999"}
    answers = record_answers(contact={"values": typed})
    st = quests.evaluate(e, line, answers)
    assert st["status"] == "asking" and st["step"]["id"] == "contact_is_3"
    assert st["step"]["prompt"] == ("NetBox already has a contact called NOC, +44 1632 960100. Is that who you mean? If so, NetBox keeps the phone "
                                    "and email it has; what you typed for them is not written.")
    # a player whose own NetBox user may not view contacts is not shown the number
    hidden = quests.evaluate(e, line, answers, hidden=frozenset({"contacts"}))["step"]["prompt"]
    assert "960100" not in hidden and hidden.startswith("NetBox already has a contact called NOC. Is that who you mean?")
    yes = quests.evaluate(e, line, {**answers, "contact_is_3": {"choice": "yes"}})
    assert [(o["op"], o["fields"].get("contact_id")) for o in yes["proposal"]["ops"]] == [("assign_contact", 3)]
    no = quests.evaluate(e, line, {**answers, "contact_is_3": {"choice": "no"}})
    assert no["status"] == "asking" and no["step"]["id"] == "contact" and "one contact per name" in no["step"]["error"]
    assert len(no["step"]["choices"]) == 2   # the form is asked again with every answer it had


def test_a_form_asked_again_after_a_wrong_value_keeps_its_answers():
    e = circuit_record()
    line = one(e, "circuit_record")
    base = {"network": {"choice": "none"}, "account": {"choice": "none"}, "contact_for": {"choice": "none"}}
    first = quests.evaluate(e, line, base)["step"]
    again = quests.evaluate(e, line, {**base, "terms": {"values": {"install_date": "2031-02-30", "commit_rate": "100"}}})["step"]
    assert again["error"].startswith("Install date: that is not a date") and again["choices"] == first["choices"]
    assert "Nothing more to record: leave it" in [c["label"] for c in again["choices"]]
    cost = quests.evaluate(e, line, {**base, "terms": {"choice": "none"}, "cost": {"values": {"mrc": "12"}}})["step"]
    assert cost["error"].startswith("Currency:") and "No charge of its own to record" in [c["label"] for c in cost["choices"]]


def test_a_currency_on_record_is_used_and_not_asked_for_again():
    e = circuit_record()
    cid = next(iter(e.circuits))
    e.circuits[cid].update(mrc="310.50", currency="GBP")
    e.touch()
    line = one(e, "circuit_record")
    base = {"network": {"choice": "none"}, "account": {"choice": "none"}, "contact_for": {"choice": "none"}, "terms": {"choice": "none"}}
    step = quests.evaluate(e, line, base)["step"]
    assert [f["id"] for f in step["fields"]] == ["nrc"] and step["prompt"].startswith("What does circuit C-OK cost, in GBP?")
    st = quests.evaluate(e, line, {**base, "cost": {"values": {"nrc": "500", "currency": "EUR"}}})
    # no second currency reaches NetBox, so the proposal cannot be refused for one
    assert st["proposal"]["ops"][-1]["fields"] == {"nrc": "500.00"} and st["changes"][-1] == "Circuit C-OK: 500.00 GBP once."


def test_what_netbox_holds_of_a_record_is_said_only_to_a_player_who_may_view_it():
    e = circuit_record()
    cid = next(iter(e.circuits))
    c = e.circuits[cid]
    e.accounts[1] = {"id": 1, "provider_id": c["provider_id"], "account": "SYN-ACC-9911", "name": ""}
    e.contacts[1] = {"id": 1, "name": "Zed Example", "phone": "+44 1632 960123", "email": ""}
    e.assignments[1] = {"id": 1, "object_type": "circuits.circuit", "object_id": cid, "contact_id": 1, "role_id": 1}
    c.update(account_id=1, mrc="4321.09", nrc="777.77", currency="GBP", install_date="2024-03-01", commit_rate=100000)
    e.touch()
    full = chains.circuit_summary(e, cid)
    assert all(x in full for x in ("SYN-ACC-9911", "Zed Example", "960123", "4321.09", "777.77", "2024-03-01"))
    shown = chains.circuit_summary(e, cid, frozenset({"accounts", "contacts", "costs"}))
    assert not [x for x in ("SYN-ACC-9911", "Zed Example", "960123", "4321.09", "777.77", "2024-03-01", "100 Mbps") if x in shown]
    assert shown.endswith("an account on record, Support / NOC: a contact on record, its charges and terms on record")
    # and the accounts NetBox holds are no choices for a player who may not view provider accounts
    e.accounts[2] = {"id": 2, "provider_id": c["provider_id"], "account": "SYN-ACC-0002", "name": ""}
    c["account_id"] = None
    e.touch()
    line = one(e, "circuit_record")
    seen = [x["label"] for x in quests.evaluate(e, line, {"network": {"choice": "none"}})["step"]["choices"]]
    unseen = [x["label"] for x in quests.evaluate(e, line, {"network": {"choice": "none"}}, hidden=frozenset({"accounts"}))["step"]["choices"]]
    assert seen[:2] == ["SYN-ACC-9911", "SYN-ACC-0002"] and unseen[0] == "An account not in NetBox yet"


# ---------------------------------------------------------------- how a device is managed
def test_a_management_address_on_an_interface_netbox_does_not_have_is_an_answer():
    from .test_chains import managed
    b, s, ap1, ap2 = managed()
    line = one(b.e, "device_unreachable")
    answers = {f"mode_{ap1}": {"choice": "local"}, f"model_{ap1}": {"choice": "varies"}, f"ip_{ap1}": {"choice": "typed"},
               f"addr_{ap1}": {"text": "192.0.2.12/24"}}
    step = quests.evaluate(b.e, line, answers)["step"]
    assert step["id"] == f"port_{ap1}" and ("other", "A VLAN or management interface not in NetBox yet") in [(c["id"], c["label"]) for c in step["choices"]]
    st = quests.evaluate(b.e, line, {**answers, f"port_{ap1}": {"choice": "other"}})
    # what was known is written (managed locally); the address is pinned on no port that does not carry it
    assert st["status"] == "ready" and [o["op"] for o in st["proposal"]["ops"]] == ["set_management"]
    assert st["checklist"][-2].startswith("Add the interface that carries 192.0.2.12/24 to ap1 in NetBox")


def test_a_long_service_name_and_a_name_that_does_not_show_are_still_choices():
    from .test_chains import managed
    b, s, ap1, ap2 = managed()
    long = "S" * 100
    b.e.devices[ap2].update(mgmt_mode="cloud", mgmt_service=long)
    b.e.touch()
    line = one(b.e, "device_unreachable")
    step = quests.evaluate(b.e, line, {f"mode_{ap1}": {"choice": "cloud"}})["step"]
    pick = step["choices"][0]
    assert len(pick["id"]) <= 64 and pick["label"].startswith("SSSS")
    st = quests.evaluate(b.e, line, {f"mode_{ap1}": {"choice": "cloud"}, f"service_{ap1}": {"choice": pick["id"]}, f"model_{ap1}": {"choice": "varies"}})
    assert st["proposal"]["ops"][0]["fields"]["service"] == long
    assert quests.choice("interface:1", "﻿")["label"] == "(a name that does not show)"


# ---------------------------------------------------------------- chains
def test_i_dont_know_yet_on_one_stage_locks_nothing_after_it():
    b = Build()
    s, room, rack = site_with_room(b)
    r, sw = linked_pair(b, s, room, rack)
    lone = b.dev(s, "sw2", "switch", room, rack, primary_ip=None)
    a = findings.analyse(b.e)
    ch = next(c for c in chains.build(b.e, a, quests.lines(b.e, a)) if c["id"] == f"chain:device:device:{lone}")
    v = chains.view(b.e, a, ch)
    assert [(x["stage"], x["status"]) for x in v["stages"]] == [("physical", "open"), ("logical", "locked")]
    # the uplink was answered "I don't know yet": how the switch is managed can be said before anyone walks to the rack
    titles = {x["id"]: x["title"] for x in quests.lines(b.e, a)}
    v = chains.view(b.e, a, ch, deferred={f"device_unconnected:device:{lone}"}, titles=titles)
    assert [(x["stage"], x["status"]) for x in v["stages"]] == [("physical", "not_known"), ("logical", "open")]
    assert (v["status"], v["position"], v["label"]) == ("open", 2, "2 of 2: logical (not known yet: What sw2 plugs into)")


def test_a_rack_summary_counts_only_kit_that_takes_up_a_unit():
    b = Build()
    s, room, rack = site_with_room(b)
    linked_pair(b, s, room, rack)
    b.dev(s, "pdu-a", "pdu", room, rack, ports=0, position=None, u_height=0.0)
    a = findings.analyse(b.e)
    ch = next(c for c in chains.build(b.e, a, quests.lines(b.e, a)) if c["kind"] == "rack")
    assert "2 of 2 devices in a unit" in chains.summary(b.e, ch)


# ---------------------------------------------------------------- wording
def test_sentences_say_what_they_mean():
    b = Build()
    s, room, rack = site_with_room(b)
    linked_pair(b, s, room, rack)
    b.dev(s, "ap-06", "wireless", room, rack, ports=1)
    assert [f["title"] for f in findings.analyse(b.e).findings if f["kind"] == "device_unconnected"] == ["ap-06 has 1 port and nothing recorded on it."]
    assert quests.poss("Coastline Networks") == "Coastline Networks'" and quests.poss("Northlight") == "Northlight's"
    e = circuit_record()
    cid = next(iter(e.circuits))
    c = e.circuits[cid]
    e.accounts[1] = {"id": 1, "provider_id": c["provider_id"], "account": "A-1", "name": ""}
    e.contacts[1] = {"id": 1, "name": "Desk", "phone": "", "email": ""}
    e.assignments[1] = {"id": 1, "object_type": "circuits.circuit", "object_id": cid, "contact_id": 1, "role_id": 1}
    c["account_id"] = 1
    e.touch()
    # who to call and what to quote are on record: the line no longer says they are what is needed first
    purpose = one(e, "circuit_record")["purpose"]
    assert purpose.endswith("Let's complete its record with what you know.") and "that is what you need first" not in purpose
    # a parked line's checklist does not send the player to the same paper twice
    line = one(e, "circuit_record")
    st = quests.evaluate(e, line, {"network": {"choice": "none"}, "contact_for": {"choice": "unknown"}, "terms": {"values": {}}, "cost": {"values": {}}})
    assert st["status"] == "parked" and len(st["checklist"]) == len(set(st["checklist"]))


# ---------------------------------------------------------------- a large site
def test_one_site_with_many_rooms_is_analysed_in_one_pass_not_room_pair_by_room_pair(monkeypatch):
    b = Build()
    s = b.site()
    for n in range(40):
        room = b.room(s, f"Room {n}")
        rack = b.rack(s, room, f"R{n}")
        for k in range(25):
            b.dev(s, f"sw-{n}-{k}", "switch", room, rack, ports=4)
    calls = {"same": 0}
    real = Components.same

    def counted(self, x, y):
        calls["same"] += 1
        return real(self, x, y)
    monkeypatch.setattr(Components, "same", counted)
    t0 = time.perf_counter()
    a = findings.analyse(b.e)
    ls = quests.lines(b.e, a)
    built = chains.build(b.e, a, ls)
    took = time.perf_counter() - t0
    assert len([f for f in a.findings if f["kind"] == "locations_not_linked"]) == 39 and len(built) >= 1000
    # 1,000 devices in 40 rooms: every pair of rooms times every pair of devices would be 487,500 comparisons
    assert calls["same"] <= 1000, calls
    assert took < 20, took
    # a question about one device does not sort the ports of every other device again
    line = next(x for x in ls if x["kind"] == "device_unconnected")
    quests.evaluate(b.e, line, {})
    t0 = time.perf_counter()
    for _ in range(5):
        quests.evaluate(b.e, line, {})
    assert time.perf_counter() - t0 < 5
