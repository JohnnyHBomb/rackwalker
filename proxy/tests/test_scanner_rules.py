"""Rules of the scanner and the quest lines that only show on estates larger or odder than the three-switch example:
what counts as a link, what a role's name does and does not decide, switch stacks, VRFs, sites without rooms, kit that
carries no label, and lists of choices that never hide what NetBox holds. No NetBox needed."""
from __future__ import annotations

import pytest

from rackwalker_proxy import estate, findings, practice, quests
from rackwalker_proxy.estate import simulate
from rackwalker_proxy.questroutes import Standing

from .test_quests import CASES, Build, linked_pair, one, settle


def kinds(e) -> list[str]:
    return [f["kind"] for f in findings.analyse(e).findings]


def ids(e, kind: str) -> list[str]:
    return [f["id"] for f in findings.analyse(e).findings if f["kind"] == kind]


def labels(step: dict) -> list[str]:
    return [c["label"] for c in step["choices"]]


def site_with_room(b: Build) -> tuple[int, int, int]:
    s = b.site()
    room = b.room(s)
    return s, room, b.rack(s, room)


# ---------------------------------------------------------------- what counts as a link
def test_two_switches_that_share_only_a_dual_homed_server_are_not_linked():
    b = Build()
    s, room, rack = site_with_room(b)
    sw1, sw2 = b.dev(s, "sw1", "switch", room, rack), b.dev(s, "sw2", "switch", room, rack)
    srv = b.dev(s, "srv1", "server", room, rack)
    b.cable(b.p(sw1, "eth1"), b.p(srv, "eth1"))
    b.cable(b.p(sw2, "eth1"), b.p(srv, "eth2"))
    assert ids(b.e, "switch_no_uplink") == [f"switch_no_uplink:device:{sw1}", f"switch_no_uplink:device:{sw2}"]
    assert ids(b.e, "site_island") == [f"site_island:device:{sw2}"]   # each switch is a group of its own
    # the same two switches with a cable between them: one network, nothing to report
    b.cable(b.p(sw1, "eth2"), b.p(sw2, "eth2"))
    b.e.touch()
    assert ids(b.e, "switch_no_uplink") == [] and ids(b.e, "site_island") == []


def test_two_rooms_joined_only_through_a_server_have_no_link_between_them():
    b = Build()
    s = b.site()
    a, c = b.room(s, "Room A"), b.room(s, "Room B")
    sw1, sw2 = b.dev(s, "sw1", "switch", a, b.rack(s, a, "A1")), b.dev(s, "sw2", "switch", c, b.rack(s, c, "B1"))
    srv = b.dev(s, "srv1", "server", a)
    b.cable(b.p(sw1, "eth1"), b.p(srv, "eth1"))
    b.cable(b.p(sw2, "eth1"), b.p(srv, "eth2"))
    assert len(ids(b.e, "locations_not_linked")) == 1


def test_a_switch_whose_only_neighbours_are_access_points_has_no_uplink():
    b = Build()
    s, room, rack = site_with_room(b)
    sw = b.dev(s, "sw1", "switch", room, rack)
    for n in (1, 2):
        ap = b.dev(s, f"ap{n}", "access-point", room, None, ports=1, role_name="Access Point")
        b.cable(b.p(sw, f"eth{n}"), b.p(ap, "eth1"))
    assert ids(b.e, "switch_no_uplink") == [f"switch_no_uplink:device:{sw}"]
    assert not estate.is_network(b.e.devices[ap]) and estate.is_end_host(b.e.devices[ap])


def test_an_island_of_devices_with_no_room_is_reported():
    b = Build()
    s, room, rack = site_with_room(b)
    main = [b.dev(s, f"sw{n}", "switch", room, rack) for n in (1, 2, 3)]
    b.cable(b.p(main[0], "eth1"), b.p(main[1], "eth1"))
    b.cable(b.p(main[1], "eth2"), b.p(main[2], "eth1"))
    loose = [b.dev(s, f"sw{n}", "switch") for n in (8, 9)]
    b.cable(b.p(loose[0], "eth1"), b.p(loose[1], "eth1"))
    f = next(f for f in findings.analyse(b.e).findings if f["kind"] == "site_island")
    assert f["title"] == "sw8, sw9 are cabled, but cut off from the rest of Mill." and f["data"]["device_ids"] == loose


def test_every_group_in_a_chain_of_rooms_that_is_cut_off_is_reported():
    b = Build()
    s = b.site()
    rooms = {n: b.room(s, f"Room {n}") for n in "ABCD"}
    pairs = []
    for name, (r1, r2) in (("x", "AB"), ("y", "BC"), ("z", "CD")):
        d1, d2 = b.dev(s, f"{name}1", "switch", rooms[r1]), b.dev(s, f"{name}2", "switch", rooms[r2])
        b.cable(b.p(d1, "eth1"), b.p(d2, "eth1"))
        pairs.append((d1, d2))
    # no room is cut off from the others (each shares a group with its neighbour), yet three groups do not meet
    assert ids(b.e, "locations_not_linked") == []
    assert ids(b.e, "site_island") == [f"site_island:device:{pairs[1][0]}", f"site_island:device:{pairs[2][0]}"]


# ---------------------------------------------------------------- what a role's name decides
@pytest.mark.parametrize("role", ["switch", "ToR", "Network", "Kit", "L2 access"])
def test_the_three_switch_example_gives_the_same_lines_whatever_the_role_is_called(role):
    e = practice.example_estate(complete=True)
    for d in e.devices.values():
        d.update(role=role.lower().replace(" ", "-"), role_name=role)
    assert kinds(e) == ["site_no_way_out", "locations_not_linked", "device_unconnected", "device_unconnected", "device_unconnected"]
    line = one(e, "site_no_way_out")
    step = quests.evaluate(e, line, {})["step"]
    assert [c["kind"] for c in step["choices"]].count("candidate") == 3   # the three switches, whatever they are called
    first = next(x for x in quests.lines(e, findings.analyse(e)) if x["kind"] == "device_unconnected")
    assert [c["kind"] for c in quests.evaluate(e, first, {})["step"]["choices"]].count("candidate") == 2


def test_role_words_are_matched_as_words_not_as_letters_inside_other_words():
    def dev(role: str) -> dict:
        return {"id": 1, "role": role.lower().replace(" ", "-"), "role_name": role}
    assert all(estate.is_network(dev(r)) for r in ("Switch", "Core Switches", "Firewall", "ToR", "WAN edge", "Router", "Agg"))
    # "tor" in "storage" and "monitor", "ap" in "tape", "core" in "encore": none of these is a network device
    assert not any(estate.is_network(dev(r)) for r in ("Storage", "Monitor", "Tape library", "Encore", "Server", "Access Point", "Wireless AP"))
    assert estate.is_router(dev("Firewall")) and estate.is_router(dev("Edge router")) and not estate.is_router(dev("Access switch"))
    assert estate.is_end_host(dev("Server")) and estate.is_end_host(dev("Wireless AP")) and not estate.is_end_host(dev("Top of rack"))
    assert estate.is_passive(dev("Patch Panel")) and estate.is_passive(dev("PDU")) and not estate.is_passive(dev("Switch"))


def test_a_server_with_many_ports_is_not_taken_for_a_switch():
    b = Build()
    s, room, rack = site_with_room(b)
    r, sw = linked_pair(b, s, room, rack)
    srv = b.dev(s, "srv1", "server", room, rack, ports=12)
    box = b.dev(s, "box1", "kit", room, rack, ports=12)
    few = b.dev(s, "box2", "kit", room, rack, ports=2)
    e = b.e
    assert not e.is_network(e.devices[srv]) and e.is_network(e.devices[box]) and e.is_switch(e.devices[box]) and not e.is_network(e.devices[few])


# ---------------------------------------------------------------- a switch stack is one switch
def stack() -> tuple[Build, int, int, int]:
    b = Build()
    s, room, rack = site_with_room(b)
    r, sw = linked_pair(b, s, room, rack)
    member = b.dev(s, "sw1b", "switch", room, rack, primary_ip=None)
    srv = b.dev(s, "srv1", "server", room, rack)
    b.cable(b.p(member, "eth1"), b.p(srv, "eth1"))
    return b, s, sw, member


def test_a_stack_member_is_not_cut_off_unlinked_or_unreachable_when_its_stack_is_not():
    b, s, sw, member = stack()
    # as two separate switches, the second one is an island with no uplink and no management address
    assert ids(b.e, "site_island") == [f"site_island:device:{member}"] and ids(b.e, "switch_no_uplink") == [f"switch_no_uplink:device:{member}"]
    assert ids(b.e, "device_unreachable") == [f"device_unreachable:device:{member}"]
    for d in (sw, member):
        b.e.devices[d]["vc_id"] = 5
    b.e.touch()
    a = findings.analyse(b.e)
    assert [f["kind"] for f in a.findings] == [] and a.completeness(s)["open"] == 0


def test_a_stack_with_no_address_or_no_cable_is_asked_about_once():
    b, s, sw, member = stack()
    idle = b.dev(s, "sw1c", "switch", b.e.devices[sw]["location_id"], b.e.devices[sw]["rack_id"], primary_ip=None)
    for d in (sw, member, idle):
        b.e.devices[d].update(vc_id=5, primary_ip=None)
    b.e.touch()
    a = findings.analyse(b.e)
    # the stack's first member speaks for it; a member with nothing plugged in is not "connected to nothing"
    assert [g["id"] for g in a.gaps if g["field"] == "primary_ip"] == [f"device:{sw}:primary_ip"]
    assert not [f for f in a.findings if f["kind"] in ("device_unconnected", "site_island", "switch_no_uplink")]


# ---------------------------------------------------------------- rooms, racks and passive kit
def test_a_site_that_records_no_rooms_is_not_asked_which_room_a_rack_is_in():
    b = Build()
    s = b.site()
    rack = b.rack(s, None, iso_x=None, iso_y=None)
    linked_pair(b, s, None, rack)
    a = findings.analyse(b.e)
    assert [g["id"] for g in a.gaps if g["kind"] == "rack"] == [f"rack:{rack}:iso"]
    line = one(b.e, "room_layout")
    step = quests.evaluate(b.e, line, {})["step"]
    assert [f["id"] for f in step["fields"]] == [f"x:{rack}", f"y:{rack}"]
    state = quests.evaluate(b.e, line, {"floor": {"values": {f"x:{rack}": 2, f"y:{rack}": 3}}})
    assert settle(b.e, line, state) == []
    simulate(b.e, state["proposal"]["ops"])
    assert findings.analyse(b.e).completeness(s) == {"percent": 100, "total": findings.analyse(b.e).completeness(s)["total"], "open": 0}
    # a site that does have rooms is asked
    room = b.room(s)
    b.e.touch()
    assert f"rack:{rack}:location" in [g["id"] for g in findings.analyse(b.e).gaps]


def test_the_rack_floor_gap_is_open_while_either_coordinate_is_missing():
    b = Build()
    s, room, rack = site_with_room(b)
    b.e.racks[rack]["iso_y"] = None
    assert [g["id"] for g in findings.analyse(b.e).gaps if g["kind"] == "rack"] == [f"rack:{rack}:iso"]


def test_passive_kit_is_not_asked_for_a_serial_a_platform_an_address_or_a_link():
    b = Build()
    s, room, rack = site_with_room(b)
    linked_pair(b, s, room, rack)
    b.e.platforms[1] = {"id": 1, "name": "Example OS", "manufacturer": None}
    for d in b.e.devices.values():
        d["platform"] = "Example OS"
    for role in ("patch-panel", "pdu", "shelf", "blanking-panel"):
        b.dev(s, f"x-{role}", role, room, rack, serial="", primary_ip=None)
    a = findings.analyse(b.e)
    assert a.findings == [] and a.gaps == [] and a.completeness(s)["open"] == 0
    # an asset tag is the admin's own sticker: once tags are in use, passive kit is asked for one too
    b.e.devices[1 + rack]["asset_tag"] = "AT-1"
    b.e.touch()
    assert {g["field"] for g in findings.analyse(b.e).gaps} == {"asset_tag"}


def test_a_device_with_no_power_ports_is_not_unpowered_and_a_zero_u_device_is_not_unpositioned():
    b = Build()
    s, room, rack = site_with_room(b)
    r, sw = linked_pair(b, s, room, rack)
    pdu = b.dev(s, "pdu1", "pdu", room, rack, ports=0, outlets=2)
    fed = b.dev(s, "sw2", "switch", room, rack, power=1)
    b.cable(b.p(fed, "eth1"), b.p(sw, "eth2"))
    b.cable(b.p(fed, "PSU1"), b.p(pdu, "OUT1"))
    b.dev(s, "strip", "cable-management", room, rack, ports=0, position=None, u_height=0.0)
    a = findings.analyse(b.e)
    # rtr1 and sw1 have no power ports on record: that is not "no power feed"
    assert not [f for f in a.findings if f["kind"] in ("device_no_power", "rack_devices_unpositioned")]
    assert b.e.powered(r) is None and b.e.powered(fed) is True


# ---------------------------------------------------------------- expected cables
def test_ports_that_must_not_ask_for_a_cable():
    b = Build()
    s, room, rack = site_with_room(b)
    r, sw = linked_pair(b, s, room, rack)
    svi = b.port(sw, "interface", "Vlan10", type="virtual")
    lag = b.port(sw, "interface", "Po1", type="lag")
    b.port(sw, "interface", "eth9", enabled=False, description="old uplink")
    b.port(sw, "interface", "eth10", mark_connected=True, description="to the provider's box")
    for port, addr in ((svi, "192.0.2.10/24"), (lag, "192.0.2.11/24")):
        b.ip(port, addr)
    shelf = b.dev(s, "sw-shelf", "switch", room, None)          # in no rack, cabled, a described port
    b.cable(b.p(shelf, "eth1"), b.p(sw, "eth3"))
    b.e.ports[b.p(shelf, "eth2")]["description"] = "desk"
    staged = b.dev(s, "sw-staged", "switch", room, rack, status="staged")   # staged kit is expected to be wired
    b.cable(b.p(staged, "eth1"), b.p(sw, "eth4"))
    b.e.ports[b.p(staged, "eth2")]["description"] = "desk"
    planned = b.dev(s, "sw-planned", "switch", room, rack, status="planned")
    b.e.ports[b.p(planned, "eth2")]["description"] = "desk"
    a = findings.analyse(b.e)
    assert [g["label"] for g in a.gaps if g["field"] == "cable"] == ["sw-staged eth2 has a description (desk) but no cable."]
    # the port NetBox marks as connected counts as answered, not as open
    marked = b.p(sw, "eth10")[1]
    assert a.checks[s][f"interface:{marked}:cable"] is True


def test_at_most_three_ports_of_a_device_are_listed_and_its_line_still_covers_them_all():
    b = Build()
    s, room, rack = site_with_room(b)
    r, sw = linked_pair(b, s, room, rack)
    acc = b.dev(s, "acc1", "switch", room, rack, ports=14)
    b.cable(b.p(acc, "eth1"), b.p(sw, "eth2"))
    desks = [b.p(acc, f"eth{n}")[1] for n in range(2, 14)]
    for n, pid in enumerate(desks, 2):
        b.e.ports[("interface", pid)]["description"] = f"Desk {n}"
    a = findings.analyse(b.e)
    assert len([g for g in a.gaps if g["field"] == "cable"]) == 3 and a.unlisted[s] == 9   # the list is short; the count is whole
    assert a.completeness(s)["open"] == 12
    line = one(b.e, "interface_no_cable")
    assert line["id"] == f"q:interface_no_cable:device:{acc}" and quests.check_ids(line) == [f"interface:{p}:cable" for p in desks]
    assert line["title"] == "The far ends at acc1"
    step = quests.evaluate(b.e, line, {})["step"]
    assert step["prompt"].startswith("12 ports of acc1 are in use by NetBox's record, with no cable: eth2 (described as 'Desk 2'); eth3")
    assert labels(step) == ["Leads I can follow: go through them", "Desk and wall-outlet leads: not kept in NetBox", "I don't know yet"]
    # one answer for all twelve: they are access ports
    state = quests.evaluate(b.e, line, {"what": {"choice": "none"}})
    assert state["status"] == "dismissed"
    rows = {line["id"]: {"status": "dismissed", "checks": quests.check_ids(line), "answers": {"what": {"choice": "none"}},
                         "snapshot": {"kind": "interface_no_cable"}}}
    stand = Standing(a, rows)
    assert a.completeness(s, stand.resolved) == {"percent": 100, "total": a.completeness(s)["total"], "open": 0}
    assert stand.in_order(quests.lines(b.e, a), s)


def test_ports_of_a_device_are_asked_about_a_few_at_a_time_and_a_port_not_in_use_is_said_once():
    b = Build()
    s, room, rack = site_with_room(b)
    r, sw = linked_pair(b, s, room, rack)
    acc = b.dev(s, "acc1", "switch", room, rack, ports=8)
    b.cable(b.p(acc, "eth1"), b.p(sw, "eth2"))
    ports = [b.p(acc, f"eth{n}")[1] for n in (2, 3, 4)]
    for pid in ports:
        b.e.ports[("interface", pid)]["description"] = "uplink?"
    line = one(b.e, "interface_no_cable")
    answers = {"what": {"choice": "each"}, f"peer_{ports[0]}": {"choice": "none"}, f"peer_{ports[1]}": {"choice": "unknown"},
               f"peer_{ports[2]}": {"choice": f"device:{r}"}}
    state = quests.evaluate(b.e, line, answers)
    assert state["status"] == "asking" and state["step"]["id"] == f"peer_port_{ports[2]}"
    assert state["waived"] == [f"interface:{ports[0]}:cable"]
    answers.update({f"peer_port_{ports[2]}": {"choice": state["step"]["choices"][0]["id"]}, f"cable_type_{ports[2]}": {"choice": "cat6"}})
    state = quests.evaluate(b.e, line, answers)
    assert state["status"] == "ready" and [op["fields"]["a_id"] for op in state["proposal"]["ops"]] == [ports[2]]
    # only the port that was really recorded is left closed by NetBox; the unknown one stays open
    assert settle(b.e, line, state) == [f"interface:{ports[0]}:cable", f"interface:{ports[1]}:cable"]
    assert quests.waived(line, answers) == [f"interface:{ports[0]}:cable"]


# ---------------------------------------------------------------- addresses
def test_an_address_in_a_vrf_is_held_only_by_a_prefix_of_that_vrf_and_the_game_writes_none_there():
    b = Build()
    s, room, rack = site_with_room(b)
    r, sw = linked_pair(b, s, room, rack)
    ip = b.ip(b.p(sw, "eth2"), "10.9.99.1/24")
    b.e.ips[ip].update(vrf_id=7, vrf="tenant-a")
    b.e.prefixes[2] = {"id": 2, "prefix": "10.9.99.0/24", "vrf_id": None}      # the same range in the global table
    f = next(f for f in findings.analyse(b.e).findings if f["kind"] == "address_no_prefix")
    assert f["title"] == "10.9.99.1/24 is in use at Mill, but NetBox has no prefix in VRF tenant-a that holds it."
    line = one(b.e, "address_no_prefix")
    step = quests.evaluate(b.e, line, {})["step"]
    assert step["id"] == "vrf" and "Yes, it is" not in " ".join(labels(step))
    state = quests.evaluate(b.e, line, {"vrf": {"choice": "later"}})
    assert state["status"] == "parked" and state["proposal"] is None
    assert state["checklist"][0] == "In NetBox, add the prefix under IPAM > Prefixes > Add and set its VRF to tenant-a (the mask says 10.9.99.0/24)."
    # the prefix in the right table settles it
    b.e.prefixes[3] = {"id": 3, "prefix": "10.9.99.0/24", "vrf_id": 7}
    b.e.touch()
    assert ids(b.e, "address_no_prefix") == [] and findings.analyse(b.e).check(f"address_no_prefix:ip:{ip}") is True


def test_host_addresses_are_asked_for_their_range_never_offered_as_a_prefix_of_one():
    b = Build()
    s, room, rack = site_with_room(b)
    r, sw = linked_pair(b, s, room, rack)
    lo = b.port(r, "interface", "lo0", type="virtual")
    for n in (1, 2):
        b.ip(lo, f"10.255.0.{n}/32")
    line = one(b.e, "address_no_prefix")
    assert line["xp"] == 50 and len(quests.check_ids(line)) == 2          # one question for both loopbacks; an answer, not a walk
    step = quests.evaluate(b.e, line, {})["step"]
    assert step["input"] == "text" and step["prompt"].startswith("10.255.0.1/32 and 10.255.0.2/32 are host addresses")
    assert quests.evaluate(b.e, line, {"subnet_typed": {"text": "10.255.0.1/32"}})["step"]["error"].startswith("10.255.0.1/32 does not hold")
    state = quests.evaluate(b.e, line, {"subnet_typed": {"text": "10.255.0.0/24"}})
    assert state["proposal"]["ops"][0]["fields"] == {"prefix": "10.255.0.0/24"} and settle(b.e, line, state) == []


def test_one_address_is_spoken_of_as_one():
    b = Build()
    s, room, rack = site_with_room(b)
    r, sw = linked_pair(b, s, room, rack)
    b.ip(b.p(sw, "eth2"), "198.51.100.11/24")
    step = quests.evaluate(b.e, one(b.e, "address_no_prefix"), {})["step"]
    assert step["prompt"] == "198.51.100.11/24 sits in no prefix. Its mask says the subnet is 198.51.100.0/24. Is that right?"


def test_a_management_address_can_go_on_any_interface_and_vlan_interfaces_come_first():
    b = Build()
    s, room, rack = site_with_room(b)
    r, sw = linked_pair(b, s, room, rack)
    big = b.dev(s, "sw24", "switch", room, rack, ports=24, primary_ip=None)
    b.cable(b.p(big, "eth1"), b.p(sw, "eth2"))
    b.port(big, "interface", "Vlan10", type="virtual")
    b.port(big, "interface", "Loopback0", type="virtual")
    line = one(b.e, "device_unreachable")
    answers = {f"mode_{big}": {"choice": "local"}, f"ip_{big}": {"choice": "typed"}, f"addr_{big}": {"text": "192.0.2.20/24"}}
    step = quests.evaluate(b.e, line, answers)["step"]
    assert labels(step)[:3] == ["Loopback0", "Vlan10", "eth1"] and "Another interface that is in NetBox" in labels(step)
    answers.update({f"port_{big}": {"choice": "named"}, f"port_{big}_name": {"text": "eth23"}})
    state = quests.evaluate(b.e, line, answers)
    assert state["status"] == "ready" and [o["op"] for o in state["proposal"]["ops"]] == ["set_management", "assign_ip"]
    assert state["proposal"]["ops"][1]["fields"]["interface_id"] == b.p(big, "eth23")[1]


def test_the_last_answer_is_the_one_taken_back():
    b = Build()
    s, room, rack = site_with_room(b)
    r, sw = linked_pair(b, s, room, rack)
    sw2 = b.dev(s, "sw2", "switch", room, rack, primary_ip=None)
    b.cable(b.p(sw2, "eth1"), b.p(sw, "eth2"))
    b.ip(b.p(sw2, "eth2"), "192.0.2.50/24")
    line = one(b.e, "device_unreachable")
    have = b.e.device_ips(sw2)[0]["id"]
    state = quests.evaluate(b.e, line, {f"mode_{sw2}": {"choice": "local"}, f"ip_{sw2}": {"choice": f"ip:{have}"}})
    assert state["answered"] == [f"mode_{sw2}", f"ip_{sw2}"]      # in the order they were asked: "back" takes the last one


# ---------------------------------------------------------------- finding ids
def test_a_group_keeps_a_check_per_member_so_its_line_can_follow_it():
    b = Build()
    s, room, rack = site_with_room(b)
    r, sw = linked_pair(b, s, room, rack)
    sw3, sw4 = b.dev(s, "sw3", "switch", room, rack), b.dev(s, "sw4", "switch", room, rack)
    b.cable(b.p(sw3, "eth1"), b.p(sw4, "eth1"))
    ips = [b.ip(b.p(sw3, "eth2"), f"10.1.2.{n}/24") for n in (1, 2, 3)]
    a = findings.analyse(b.e)
    island, orphan = one(b.e, "site_island"), one(b.e, "address_no_prefix")
    assert island["id"] == f"q:site_island:device:{sw3}" and quests.check_ids(island) == [f"site_island:device:{sw3}", f"site_island:device:{sw4}"]
    assert orphan["id"] == f"q:address_no_prefix:ip:{ips[0]}" and quests.check_ids(orphan) == [f"address_no_prefix:ip:{i}" for i in ips]
    assert a.check(f"site_island:device:{sw}") is True and a.check(f"site_island:device:{sw4}") is False
    # the first member goes: the finding has a new id, and the old line's other checks are still open under it
    b.e.devices[sw3]["status"] = "offline"
    del b.e.ips[ips[0]]
    b.e.touch()
    a2 = findings.analyse(b.e)
    assert ids(b.e, "address_no_prefix") == [f"address_no_prefix:ip:{ips[1]}"]
    assert a2.check(f"address_no_prefix:ip:{ips[0]}") is None and a2.check(f"address_no_prefix:ip:{ips[1]}") is False
    assert a2.check(f"site_island:device:{sw3}") is None     # switched off: the scanner makes no such check any more


def test_two_models_whose_names_differ_only_in_punctuation_are_two_lines():
    b = Build()
    s, room, rack = site_with_room(b)
    r, sw = linked_pair(b, s, room, rack)
    b.e.platforms[1] = {"id": 1, "name": "Example OS", "manufacturer": None}
    b.e.devices[r]["platform"] = b.e.devices[sw]["platform"] = "Example OS"
    for n, model in enumerate(("EX 4300", "EX-4300"), 2):
        d = b.dev(s, f"sw{n}", "switch", room, rack, model=model)
        b.cable(b.p(d, "eth1"), b.p(sw, f"eth{n}"))
    got = ids(b.e, "platform_survey")
    assert len(got) == len(set(got)) == 2 and all(i.startswith(f"platform_survey:site:{s}:ex-4300-") for i in got)
    # a model on its own keeps the plain id
    del b.e.devices[d]
    b.e.touch()
    assert ids(b.e, "platform_survey") == [f"platform_survey:site:{s}:ex-4300"]


# ---------------------------------------------------------------- lists of choices never hide what NetBox holds
def nine_switches() -> tuple[Build, int, list[int]]:
    b = Build()
    s, room, rack = site_with_room(b)
    sws = [b.dev(s, f"sw-{n:02d}", "switch", room, rack) for n in range(1, 10)]
    return b, s, sws


def test_any_device_in_netbox_can_be_named_as_the_far_end():
    b, s, sws = nine_switches()
    line = next(x for x in quests.lines(b.e, findings.analyse(b.e)) if x["id"] == f"q:device_unconnected:device:{sws[0]}")
    step = quests.evaluate(b.e, line, {})["step"]
    assert labels(step) == [f"sw-{n:02d} (switch)" for n in range(2, 8)] + [
        "Another device that is in NetBox", "A device that is not in NetBox yet", "Nothing: it stands alone, on purpose", "I don't know yet"]
    named = quests.evaluate(b.e, line, {"peer": {"choice": "named"}})["step"]
    assert named["input"] == "text" and named["prompt"] == "Type its name, or the start of it (8 devices in NetBox fit here)."
    state = quests.evaluate(b.e, line, {"peer": {"choice": "named"}, "peer_name": {"text": "sw-09"}})
    assert state["status"] == "asking" and state["step"]["id"] == "local_port" and state["said"][-1]["answer"] == "sw-09"
    state = quests.evaluate(b.e, line, {"peer": {"choice": "named"}, "peer_name": {"text": "sw-09"}, "local_port": {"choice": f"interface:{b.p(sws[0], 'eth4')[1]}"},
                                        "peer_port": {"choice": f"interface:{b.p(sws[8], 'eth4')[1]}"}, "cable_type": {"choice": "cat6"}})
    assert state["status"] == "ready" and settle(b.e, line, state) == []


def test_a_device_netbox_has_is_never_parked_as_not_in_netbox():
    b, s, sws = nine_switches()
    line = next(x for x in quests.lines(b.e, findings.analyse(b.e)) if x["id"] == f"q:device_unconnected:device:{sws[0]}")
    state = quests.evaluate(b.e, line, {"peer": {"choice": "other"}, "other_name": {"text": "SW-09"}})
    assert state["status"] == "asking" and state["step"]["error"] == 'SW-09 is in NetBox. Change your last answer and pick "Another device that is in NetBox".'
    state = quests.evaluate(b.e, line, {"peer": {"choice": "other"}, "other_name": {"text": "old-hub"}})
    assert state["status"] == "parked" and state["note"] == "You said sw-01 connects to old-hub, which is not in NetBox yet."


def test_a_server_can_be_named_as_the_far_end_of_a_described_port():
    b = Build()
    s, room, rack = site_with_room(b)
    r, sw = linked_pair(b, s, room, rack)
    srv = b.dev(s, "db-07", "server", room, rack)
    b.e.ports[b.p(sw, "eth4")]["description"] = "database"
    line = one(b.e, "interface_no_cable")
    pid = b.p(sw, "eth4")[1]
    step = quests.evaluate(b.e, line, {})["step"]
    assert "Another device that is in NetBox" in labels(step) and "db-07 (server)" not in labels(step)
    state = quests.evaluate(b.e, line, {f"peer_{pid}": {"choice": "named"}, f"peer_{pid}_name": {"text": "db-0"}})
    assert state["step"]["id"] == f"peer_port_{pid}" and state["step"]["prompt"] == "Which port on db-07 does it land on?"


def test_a_circuit_can_be_placed_at_any_of_many_sites():
    b = Build()
    sites = [b.site(f"Site {n}") for n in range(1, 9)]
    for s in sites:
        linked_pair(b, s)
    b.circuit("C-9")
    line = one(b.e, "circuit_unterminated")
    step = quests.evaluate(b.e, line, {})["step"]
    assert labels(step) == [f"Site {n}" for n in range(1, 7)] + ["Another site that is in NetBox", "Nowhere: we no longer use it", "I don't know yet"]
    state = quests.evaluate(b.e, line, {"site": {"choice": "named"}, "site_name": {"text": "site 8"}})
    assert state["step"]["id"] == "device" and state["step"]["prompt"] == "Which device at Site 8 does circuit C-9 plug into?"


def test_candidates_for_an_access_switch_start_in_its_own_room_with_the_busiest_switch():
    b = Build()
    s = b.site()
    hall, core = b.room(s, "Hall A"), b.room(s, "Core")
    rack = b.rack(s, hall)
    routers = [b.dev(s, f"rtr{n}", "router", core) for n in (1, 2)]
    dist = b.dev(s, "dist1", "distribution-switch", hall, rack, ports=12)
    accs = [b.dev(s, f"acc{n:02d}", "access-switch", hall, rack, ports=6) for n in range(1, 9)]
    b.cable(b.p(routers[0], "eth1"), b.p(dist, "eth12"))
    for n, a in enumerate(accs[:7], 1):
        b.cable(b.p(a, "eth1"), b.p(dist, f"eth{n}"))
    srv = b.dev(s, "srv1", "server", hall, rack)
    b.cable(b.p(accs[7], "eth2"), b.p(srv, "eth1"))
    line = one(b.e, "switch_no_uplink")
    assert labels(quests.evaluate(b.e, line, {})["step"])[0] == "dist1 (distribution-switch)"


# ---------------------------------------------------------------- the way out, circuits, platforms
def test_whose_line_it_is_is_asked_and_a_netbox_with_no_circuit_type_gets_one_with_the_circuit():
    e = practice.example_estate(complete=True)
    line = one(e, "site_no_way_out")
    answers = {"device": {"choice": "device:9000001"}}
    assert quests.evaluate(e, line, answers)["step"]["id"] == "provider"
    e.circuit_types.clear()
    e.touch()
    answers |= {"provider": {"choice": "provider:9000001"}, "circuit_id": {"text": "EXF-1"}}
    state = quests.evaluate(e, line, answers)
    # no dead end: the kinds of service a site admin meets are offered, and the one picked is made with the circuit
    assert state["step"]["id"] == "circuit_type" and "NetBox has no circuit type yet" in state["step"]["prompt"]
    assert [c["label"] for c in state["step"]["choices"]] == ["Internet", "Private line", "MPLS or IP VPN", "Dark fibre", "I don't know yet"]
    state = quests.evaluate(e, line, {**answers, "circuit_type": {"choice": "new:0"}, "local_port": {"choice": "interface:9000026"},
                                      "cable_type": {"choice": "smf"}})
    ops = state["proposal"]["ops"]
    assert [o["op"] for o in ops] == ["create_circuit_type", "create_circuit", "create_circuit_termination", "connect_cable"]
    assert ops[0]["fields"] == {"name": "Internet"} and ops[1]["fields"]["type_id"] == "@ctype"
    assert state["changes"][:2] == ["New circuit type Internet.", "New circuit EXF-1 from Example Fibre (Internet)."]


def test_a_site_with_no_line_out_on_purpose_can_say_so():
    e = practice.example_estate(complete=True)
    line = one(e, "site_no_way_out")
    assert "None: this site has no line out, on purpose" in labels(quests.evaluate(e, line, {})["step"])
    assert quests.evaluate(e, line, {"device": {"choice": "none"}})["status"] == "dismissed"


def two_towns() -> tuple[Build, int, int, int, int]:
    b = Build()
    wharf, barn = b.site("Heron Wharf"), b.site("Lapwing Barn")
    r1, s1 = linked_pair(b, wharf)
    lb = b.dev(barn, "lb-sw1", "switch")
    c = b.circuit("NL-INET-HW-7", "Northlight")
    b.term(c, "A", wharf)
    return b, wharf, barn, lb, c


def test_a_circuit_with_its_end_at_another_site_is_not_offered_as_an_uplink_but_is_as_the_link_between_sites():
    b, wharf, barn, lb, c = two_towns()
    b.e.circuits[c].update(cid="NL-WAN-HW-7", type="Ethernet private line")
    uplink = next(x for x in quests.lines(b.e, findings.analyse(b.e)) if x["id"] == f"q:device_unconnected:device:{lb}")
    assert not [x for x in labels(quests.evaluate(b.e, uplink, {})["step"]) if x.startswith("circuit")]
    between = next(x for x in quests.lines(b.e, findings.analyse(b.e)) if x["id"] == f"q:sites_not_linked:site:{barn}")
    step = quests.evaluate(b.e, between, {})["step"]
    assert step["prompt"] == "What does lb-sw1 connect to on the way to your other sites?"
    # the circuit comes first and says where its other end is; devices of other sites carry their site's name
    assert labels(step)[:2] == ["circuit NL-WAN-HW-7 to Heron Wharf (Northlight)", "rtr1 (router, Heron Wharf)"]
    # the other site's internet line is no candidate for the link between the sites, whatever ends it still lacks
    b.e.circuits[c].update(cid="NL-INET-HW-7", type="Internet")
    b.e.touch()
    between = next(x for x in quests.lines(b.e, findings.analyse(b.e)) if x["id"] == f"q:sites_not_linked:site:{barn}")
    assert labels(quests.evaluate(b.e, between, {})["step"])[0] == "rtr1 (router, Heron Wharf)"
    # at the site where its end is on record, it is that site's own line: one question, not two lines for one fact
    here = [f["kind"] for f in findings.analyse(b.e).findings if f["site_id"] == wharf]
    assert "circuit_unterminated" in here and "site_no_way_out" not in here


def test_a_circuit_with_an_end_left_loose_is_no_way_out_and_is_still_asked_about():
    b, wharf, barn, lb, c = two_towns()
    z = b.term(c, "Z", barn)
    b.cable(b.p(lb, "eth1"), z)
    a = findings.analyse(b.e)
    assert a.check(f"circuit_unterminated:circuit:{c}") is False            # its A end at the wharf is cabled to nothing
    assert [f["id"] for f in a.findings if f["kind"] == "site_no_way_out"] == [f"site_no_way_out:site:{barn}"]


def test_a_site_linked_to_one_with_a_way_out_has_a_way_out():
    b = Build()
    hq, branch = b.site("HQ"), b.site("Branch")
    r1, s1 = linked_pair(b, hq)
    r2 = b.dev(branch, "br-rtr1", "router")
    b.ip(b.p(r2, "eth2"), "192.0.2.9/24")
    b.cable(b.p(r2, "eth1"), b.p(r1, "eth2"))
    c = b.circuit("C-1")
    b.cable(b.p(r1, "eth3"), b.term(c, "A", hq))
    a = findings.analyse(b.e)
    assert not [f for f in a.findings if f["kind"] in ("site_no_way_out", "sites_not_linked", "circuit_unterminated")]
    assert a.check(f"site_no_way_out:site:{branch}") is True and a.check(f"sites_not_linked:site:{branch}") is True


def test_only_platforms_netbox_allows_for_the_manufacturer_are_offered():
    b = Build()
    s, room, rack = site_with_room(b)
    r, sw = linked_pair(b, s, room, rack)
    b.e.platforms.update({1: {"id": 1, "name": "Example OS", "manufacturer": "Example Networks"}, 2: {"id": 2, "name": "Corvid NOS", "manufacturer": "Corvid"},
                          3: {"id": 3, "name": "Example Linux", "manufacturer": None}})
    for d in (r, sw):
        b.e.devices[d].update(manufacturer="Corvid", model="AP-3")
    step = quests.evaluate(b.e, one(b.e, "platform_survey"), {})["step"]
    assert labels(step)[:2] == ["Corvid NOS", "Example Linux"] and "Example OS" not in labels(step)


# ---------------------------------------------------------------- answers and proposals
def test_every_kind_of_line_but_a_routers_address_can_be_answered_as_how_it_is_on_purpose():
    without = []
    for case in CASES:
        e = case()
        line = one(e, case.__name__)
        seen, answers = False, {}
        for _ in range(12):
            state = quests.evaluate(e, line, answers)
            step = state["step"]
            if step is None:
                break
            assert not [c["label"] for c in step["choices"] if c["label"].endswith("…")], step["choices"]   # no label is cut short
            if any(c["kind"] == "not_applicable" for c in step["choices"]):
                seen = True
                break
            pick = next((c for c in step["choices"] if c["kind"] in ("candidate", "confirm")), None)
            if pick is None:
                break
            answers[step["id"]] = {"choice": pick["id"]}
        if not seen:
            without.append(case.__name__)
    # a router routes on some address: that one is found out, not waved away. How a device is managed has its own
    # answer for "nobody logs in to it" ("it is not managed at all"), which is recorded rather than waved away.
    assert without == ["router_no_address", "device_unreachable"]


def test_a_form_can_be_closed_as_nothing_to_record_and_that_is_not_a_blank_answer():
    b = Build()
    s, room, rack = site_with_room(b)
    r, sw = linked_pair(b, s, room, rack)
    b.e.devices[sw]["serial"] = ""
    line = one(b.e, "rack_audit")
    step = quests.evaluate(b.e, line, {})["step"]
    assert labels(step) == ["No such label on them: leave it as it is", "I don't know yet"]
    assert quests.evaluate(b.e, line, {"labels": {"values": {}}})["status"] == "parked"
    assert quests.evaluate(b.e, line, {"labels": {"choice": "unknown"}})["status"] == "parked"
    closed = quests.evaluate(b.e, line, {"labels": {"choice": "none"}})
    assert (closed["status"], closed["proposal"]) == ("dismissed", None)


def test_a_change_to_something_that_exists_carries_the_time_netbox_last_changed_it():
    b = Build()
    s, room, rack = site_with_room(b)
    r, sw = linked_pair(b, s, room, rack)
    b.e.devices[sw].update(serial="", last_updated="2026-11-09T10:00:00.123456Z")
    line = one(b.e, "rack_audit")
    state = quests.evaluate(b.e, line, {"labels": {"values": {f"device:{sw}:serial": "SN-1"}}})
    assert state["proposal"]["ops"] == [{"op": "update_fields", "target": {"kind": "device", "id": sw}, "fields": {"serial": "SN-1"},
                                         "if_unmodified_since": "2026-11-09T10:00:00.123456Z"}]
    # an estate that does not know the time (the practice island) proposes without it
    b.e.devices[sw]["last_updated"] = None
    assert "if_unmodified_since" not in quests.evaluate(b.e, line, {"labels": {"values": {f"device:{sw}:serial": "SN-1"}}})["proposal"]["ops"][0]


@pytest.mark.parametrize("value", ["nan", "inf", "-inf", float("nan")])
def test_a_number_that_is_not_finite_is_a_field_error(value):
    b = Build()
    s = b.site("Mill", latitude=None, longitude=None)
    linked_pair(b, s)
    line = one(b.e, "site_record")
    state = quests.evaluate(b.e, line, {"record": {"values": {"latitude": value, "longitude": 1}}})
    assert state["status"] == "asking" and state["step"]["error"] == "Latitude (decimal degrees): not a number"


def test_a_device_with_no_name_is_called_by_what_it_is_never_none():
    b = Build()
    s, room, rack = site_with_room(b)
    r = b.dev(s, None, "router", room, rack, model="EX-R400", primary_ip=None, serial="")
    sw = b.dev(s, None, "switch", room, rack, model="EX-A2400", primary_ip=None)
    lone = b.dev(s, None, "switch", room, rack, model="EX-A2400")
    srv = b.dev(s, "srv1", "server", room, rack)
    b.cable(b.p(sw, "eth1"), b.p(srv, "eth1"))
    a = findings.analyse(b.e)
    texts = [f["title"] for f in a.findings] + [g["label"] for g in a.gaps]
    for line in quests.lines(b.e, a):
        state = quests.evaluate(b.e, line, {})
        texts += [line["title"], line["purpose"], line["outcome"], state["step"]["prompt"] if state["step"] else ""] + \
                 [c["label"] for c in (state["step"] or {}).get("choices", [])] + [f["label"] for f in (state["step"] or {}).get("fields", [])]
    assert not [t for t in texts if "None" in t or "none has" in t.lower()], [t for t in texts if "None" in t]
    assert f"The unnamed EX-A2400 (device {lone}) has 4 ports and nothing recorded on any of them." in texts
    assert f"What is the unnamed EX-A2400 (device {lone}) (rack R1 U10 in Room) plugged into?" in texts


def test_a_list_the_scan_could_not_read_never_breaks_the_analysis():
    b = Build()
    s = b.site()
    a_room, b_room = b.room(s, "A"), b.room(s, "B")
    rack = b.rack(s, a_room)
    d1 = b.dev(s, "sw1", "switch", a_room, rack, serial="")
    d2 = b.dev(s, "sw2", "switch", b_room)
    for table in ("racks", "locations"):
        e = b.e.copy()
        getattr(e, table).clear()
        e.unreadable.append(table)
        a = findings.analyse(e)      # no KeyError
        assert not [f for f in a.findings if f["kind"] in ({"racks": ("rack_audit", "room_layout"), "locations": ("locations_not_linked",)}[table])]
        for line in quests.lines(e, a):
            quests.evaluate(e, line, {})


def test_percent_never_rounds_up_to_complete():
    a = findings.Analysis()
    a.checks[1] = {f"c{n}": True for n in range(199)} | {"open": False}
    assert a.completeness(1) == {"percent": 99, "total": 200, "open": 1}
    assert findings.Analysis().completeness(1) == {"percent": 100, "total": 0, "open": 0}


def test_survey_and_bundle_sizes():
    b = Build()
    s, room, rack = site_with_room(b)
    r, sw = linked_pair(b, s, room, rack)
    b.e.platforms[1] = {"id": 1, "name": "Example OS", "manufacturer": None}
    b.e.devices[r]["platform"] = b.e.devices[sw]["platform"] = "Example OS"
    hub = b.dev(s, "hub", "switch", room, rack, ports=30, platform="Example OS")
    b.cable(b.p(hub, "eth30"), b.p(sw, "eth2"))
    for n in range(1, 26):
        d = b.dev(s, f"acc{n:02d}", "switch", room, rack, serial="", model="AC-1")
        b.cable(b.p(d, "eth1"), b.p(hub, f"eth{n}"))
    survey = one(b.e, "platform_survey")
    assert len(survey["finding"]["data"]["device_ids"]) == 20 and "(5 more after these)" in survey["finding"]["title"]
    audit = one(b.e, "rack_audit")
    assert len(audit["finding"]["data"]["device_ids"]) == 25 and len(quests.evaluate(b.e, audit, {})["step"]["fields"]) == 4
    assert (findings.MAX_SURVEY, quests.MAX_BUNDLE, findings.MAX_INTERFACE_GAPS_PER_DEVICE, findings.MAX_INTERFACE_GAPS) == (20, 4, 3, 50)


def test_a_router_with_a_management_address_but_no_interface_address_still_has_none():
    b = Build()
    s, room, rack = site_with_room(b)
    r = b.dev(s, "rtr1", "router", room, rack)          # primary_ip is set on the device row, no address on any interface
    fw = b.dev(s, "fw1", "firewall", room, rack)
    b.cable(b.p(r, "eth1"), b.p(fw, "eth1"))
    assert ids(b.e, "router_no_address") == [f"router_no_address:device:{r}", f"router_no_address:device:{fw}"]
    assert b.e.is_network(b.e.devices[fw])
