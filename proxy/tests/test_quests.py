"""The scanner and the quest lines, without NetBox: every finding kind and gap type is built as a tiny estate, found,
turned into a quest line, answered the way a player would, and checked by analysing the estate again."""
from __future__ import annotations

import copy

import pytest

from rackwalker_proxy import findings, practice, quests
from rackwalker_proxy.estate import Estate, simulate
from rackwalker_proxy.questroutes import xp_for


class Build:
    """A small estate, one object at a time."""

    def __init__(self) -> None:
        self.e = Estate()
        self.n = 0

    def id(self) -> int:
        self.n += 1
        return self.n

    def site(self, name="Mill", **kw) -> int:
        i = self.id()
        self.e.sites[i] = {"id": i, "name": name, "slug": name.lower(), "status": "active", "latitude": 52.0, "longitude": 4.0,
                           "facility": "F1", "country": "NL", **kw}
        return i

    def room(self, site: int, name="Room") -> int:
        i = self.id()
        self.e.locations[i] = {"id": i, "name": name, "site_id": site}
        return i

    def rack(self, site: int, room: int | None, name="R1", **kw) -> int:
        i = self.id()
        self.e.racks[i] = {"id": i, "name": name, "site_id": site, "location_id": room, "u_height": 42, "iso_x": 1, "iso_y": 1, **kw}
        return i

    def dev(self, site: int, name: str, role="switch", room=None, rack=None, ports=4, power=0, outlets=0, **kw) -> int:
        i = self.id()
        self.e.devices[i] = {"id": i, "name": name, "site_id": site, "location_id": room, "rack_id": rack, "position": 10.0 if rack else None,
                             "face": "front" if rack else None, "role": role, "role_name": role.capitalize(), "status": "active",
                             "serial": f"S{i}", "asset_tag": None, "platform": None, "primary_ip": "192.0.2.1/24", "u_height": 1.0,
                             "model": "M", "manufacturer": "Example", **kw}
        for kind, count, prefix in (("interface", ports, "eth"), ("power_port", power, "PSU"), ("power_outlet", outlets, "OUT")):
            for k in range(1, count + 1):
                self.port(i, kind, f"{prefix}{k}")
        return i

    def port(self, dev: int, kind: str, name: str, **kw) -> tuple[str, int]:
        i = self.id()
        self.e.ports[(kind, i)] = {"kind": kind, "id": i, "device_id": dev, "name": name, "type": "1000base-t", "enabled": True,
                                   "mgmt_only": False, "cable_id": None, "description": "", "mark_connected": False, "rear_ids": [], **kw}
        return kind, i

    def p(self, dev: int, name: str) -> tuple[str, int]:
        return next(k for k, v in self.e.ports.items() if v["device_id"] == dev and v["name"] == name)

    def cable(self, a: tuple[str, int], b: tuple[str, int]) -> int:
        i = self.id()
        self.e.cables[i] = {"id": i, "type": "cat6", "label": None, "status": "connected", "a": [a], "b": [b]}
        for k in (a, b):
            (self.e.cterms[k[1]] if k[0] == "circuit_termination" else self.e.ports[k])["cable_id"] = i
        return i

    def circuit(self, cid="C-1", provider="Carrier") -> int:
        pid = next((p["id"] for p in self.e.providers.values() if p["name"] == provider), None) or self.provider(provider)
        i = self.id()
        self.e.circuits[i] = {"id": i, "cid": cid, "provider_id": pid, "type": "Internet", "status": "active"}
        return i

    def provider(self, name="Carrier") -> int:
        i = self.id()
        self.e.providers[i] = {"id": i, "name": name}
        self.e.circuit_types.setdefault(900, {"id": 900, "name": "Internet"})
        return i

    def term(self, circuit: int, side: str, site: int | None) -> tuple[str, int]:
        i = self.id()
        self.e.cterms[i] = {"id": i, "circuit_id": circuit, "side": side, "site_id": site, "cable_id": None}
        return "circuit_termination", i

    def ip(self, port: tuple[str, int] | None, address: str) -> int:
        i = self.id()
        self.e.ips[i] = {"id": i, "address": address, "interface_id": port[1] if port else None}
        return i


def linked_pair(b: Build, site: int, room=None, rack=None) -> tuple[int, int]:
    """A router and a switch with a cable between them: a site that has nothing wrong with its wiring."""
    r = b.dev(site, "rtr1", "router", room, rack)
    s = b.dev(site, "sw1", "switch", room, rack)
    b.cable(b.p(r, "eth1"), b.p(s, "eth1"))
    b.ip(b.p(r, "eth1"), "192.0.2.1/24")
    b.e.prefixes[1] = {"id": 1, "prefix": "192.0.2.0/24"}
    return r, s


TEXT = {"circuit_id": "EXF-1", "other_name": "old-hub", "subnet_typed": "192.0.2.0/24"}


def play(e: Estate, line: dict, prefer: dict[str, str] | None = None, tools=frozenset()) -> tuple[dict, dict]:
    """Answers a line as a player who knows everything: the first real candidate, a typed value, every form field."""
    answers: dict = {}
    for _ in range(20):
        state = quests.evaluate(e, line, answers, tools)
        step = state["step"]
        if state["status"] != "asking":
            return state, answers
        if step["input"] == "choice":
            want = (prefer or {}).get(step["id"])
            pick = next((c for c in step["choices"] if want and c["label"].startswith(want)), None) or \
                next((c for c in step["choices"] if c["kind"] in ("candidate", "confirm", "skip")), None) or \
                next(c for c in step["choices"] if c["id"] == "typed")
            answers[step["id"]] = {"choice": pick["id"]}
        elif step["input"] == "text":
            answers[step["id"]] = {"text": TEXT.get(step["id"], "192.0.2.77/24")}
        else:
            answers[step["id"]] = {"values": {f["id"]: (f["options"][0]["id"] if f["type"] == "select" else 3 + i if f["type"] == "number"
                                                        else "NL" if f["id"] == "country" else f"V{i}") for i, f in enumerate(step["fields"])}}
    raise AssertionError("the line never came to an end")


def settle(e: Estate, line: dict, state: dict) -> list[str]:
    """Applies the proposal to a copy of the estate and returns the line's checks that are still open."""
    assert state["status"] == "ready", state
    trial = copy.deepcopy(e)
    results = simulate(trial, state["proposal"]["ops"])
    assert [r["error"] for r in results] == [None] * len(results)
    return quests.open_checks(findings.analyse(trial), line)


def one(e: Estate, kind: str) -> dict:
    a = findings.analyse(e)
    lines = [x for x in quests.lines(e, a) if x["kind"] == kind]
    assert lines, f"no {kind} line; findings: {[f['id'] for f in a.findings]}"
    return lines[0]


# ---------------------------------------------------------------- one tiny estate per finding kind
def device_unconnected():
    b = Build()
    s = b.site()
    linked_pair(b, s)
    b.dev(s, "sw-lonely", "switch")
    return b.e


def switch_no_uplink():
    b = Build()
    s = b.site()
    linked_pair(b, s)
    sw = b.dev(s, "sw-edge", "switch")
    srv = b.dev(s, "srv1", "server")
    b.cable(b.p(sw, "eth1"), b.p(srv, "eth1"))
    return b.e


def site_island():
    b = Build()
    s = b.site()
    linked_pair(b, s)
    core = b.dev(s, "sw-core", "switch")
    b.cable(b.p(core, "eth1"), b.p(next(i for i, d in b.e.devices.items() if d["name"] == "sw1"), "eth2"))
    x, y = b.dev(s, "sw-x", "switch"), b.dev(s, "sw-y", "switch")
    b.cable(b.p(x, "eth1"), b.p(y, "eth1"))
    return b.e


def locations_not_linked():
    b = Build()
    s = b.site()
    a, c = b.room(s, "Hall"), b.room(s, "Annex")
    linked_pair(b, s, a)
    b.dev(s, "sw-annex", "switch", c)
    return b.e


def sites_not_linked():
    b = Build()
    s1, s2 = b.site("Mill"), b.site("Wharf")
    linked_pair(b, s1)
    r = b.dev(s2, "rtr-w", "router")
    sw = b.dev(s2, "sw-w", "switch")
    b.cable(b.p(r, "eth1"), b.p(sw, "eth1"))
    b.ip(b.p(r, "eth2"), "192.0.2.9/24")
    return b.e


def site_no_way_out():
    b = Build()
    s = b.site()
    linked_pair(b, s)
    b.provider("Example Fibre")
    return b.e


def circuit_unterminated():
    b = Build()
    s = b.site()
    r, _ = linked_pair(b, s)
    ok = b.circuit("C-OK")
    b.cable(b.p(r, "eth3"), b.term(ok, "A", s))
    b.circuit("C-LOST")
    return b.e


def provider_unused():
    b = Build()
    s = b.site()
    r, _ = linked_pair(b, s)
    ok = b.circuit("C-OK")
    b.cable(b.p(r, "eth3"), b.term(ok, "A", s))
    b.provider("Forgotten Telecom")
    return b.e


def router_no_address():
    b = Build()
    s = b.site()
    linked_pair(b, s)
    r2 = b.dev(s, "rtr2", "router", primary_ip=None)
    b.cable(b.p(r2, "eth1"), b.p(next(i for i, d in b.e.devices.items() if d["name"] == "sw1"), "eth2"))
    return b.e


def device_unreachable():
    b = Build()
    s = b.site()
    _, sw = linked_pair(b, s)
    b.e.devices[sw]["primary_ip"] = None
    b.ip(b.p(sw, "eth3"), "192.0.2.20/24")
    return b.e


def device_no_power():
    b = Build()
    s = b.site()
    room = b.room(s)
    rack = b.rack(s, room)
    r = b.dev(s, "rtr1", "router", room, rack, power=1)
    sw = b.dev(s, "sw1", "switch", room, rack, power=1)
    pdu = b.dev(s, "pdu1", "pdu", room, rack, ports=0, outlets=4)
    b.cable(b.p(r, "eth1"), b.p(sw, "eth1"))
    b.cable(b.p(r, "PSU1"), b.p(pdu, "OUT1"))
    b.ip(b.p(r, "eth2"), "192.0.2.1/24")
    b.e.prefixes[1] = {"id": 1, "prefix": "192.0.2.0/24"}
    return b.e


def address_no_prefix():
    b = Build()
    s = b.site()
    _, sw = linked_pair(b, s)
    b.ip(b.p(sw, "eth3"), "198.51.100.7/24")
    return b.e


def interface_no_cable():
    b = Build()
    s = b.site()
    room = b.room(s)
    rack = b.rack(s, room)
    _, sw = linked_pair(b, s, room, rack)
    b.e.ports[b.p(sw, "eth4")]["description"] = "uplink to the annex"
    b.dev(s, "sw-annex", "switch", room, rack)
    return b.e


def rack_audit():
    b = Build()
    s = b.site()
    room = b.room(s)
    rack = b.rack(s, room)
    r, sw = linked_pair(b, s, room, rack)
    b.e.platforms[1] = {"id": 1, "name": "Example OS"}
    b.e.devices[r].update(asset_tag="AT-0041", platform="Example OS")
    b.e.devices[sw].update(serial="", position=8.0)
    return b.e


def platform_survey():
    b = Build()
    s = b.site()
    room = b.room(s)
    rack = b.rack(s, room)
    r, sw = linked_pair(b, s, room, rack)
    b.e.platforms[1] = {"id": 1, "name": "Example OS"}
    b.e.devices[r].update(platform="Example OS", model="R")
    sw2 = b.dev(s, "sw2", "switch", room, rack)
    b.cable(b.p(sw, "eth2"), b.p(sw2, "eth1"))
    return b.e


def shelf_audit():
    b = Build()
    s = b.site()
    room = b.room(s, "Store")
    linked_pair(b, s)
    b.dev(s, "spare1", "server", room, ports=0, serial="", status="inventory")
    return b.e


def site_record():
    b = Build()
    s = b.site(latitude=None, longitude=None, facility=None, country=None)
    linked_pair(b, s)
    return b.e


def rack_devices_unpositioned():
    b = Build()
    s = b.site()
    room = b.room(s)
    rack = b.rack(s, room)
    _, sw = linked_pair(b, s, room, rack)
    b.e.devices[sw].update(position=None, face=None)
    return b.e


def room_layout():
    b = Build()
    s = b.site()
    room = b.room(s)
    b.rack(s, room, "R1")
    b.rack(s, None, "R2", iso_x=None, iso_y=None)
    linked_pair(b, s)
    return b.e


CASES = [device_unconnected, switch_no_uplink, site_island, locations_not_linked, sites_not_linked, site_no_way_out, circuit_unterminated,
         provider_unused, router_no_address, device_unreachable, device_no_power, address_no_prefix, interface_no_cable, rack_audit,
         shelf_audit, platform_survey, site_record, rack_devices_unpositioned, room_layout]


def test_every_finding_kind_has_a_case():
    assert sorted(c.__name__ for c in CASES) == sorted(findings.KIND_ORDER) == sorted(quests.FLOWS) == sorted(quests.WORDS)


@pytest.mark.parametrize("case", CASES, ids=lambda c: c.__name__)
def test_each_finding_kind_yields_a_quest_line_that_ends_in_a_verified_change(case):
    e = case()
    kind = case.__name__
    line = one(e, kind)
    # the tiny estate shows this finding and nothing unrelated above it
    assert quests.open_checks(findings.analyse(e), line)
    assert line["purpose"] and line["outcome"].startswith("You can now") and line["rank"] == findings.KIND_RANK.get(kind, "connectivity")
    first = quests.evaluate(e, line, {})
    assert first["status"] == "asking" and first["step"]["prompt"] and first["target"] is not None
    # every question offers "I don't know yet" (a form does so by being left blank), and nothing is proposed before an answer
    assert first["proposal"] is None and any(c["kind"] == "unknown" for c in first["step"]["choices"])
    state, answers = play(e, line)
    assert settle(e, line, state) == []
    # the proposal is also said in plain words, one line per op, with names instead of ids
    assert len(state["changes"]) == len(state["proposal"]["ops"]) and all(c.endswith(".") for c in state["changes"])
    # every value in the proposal is one the player picked or typed
    said = " ".join(str(v) for a in answers.values() for v in ((a.get("values") or {}).values() if "values" in a else a.values()))
    for op in state["proposal"]["ops"]:
        for k, v in op["fields"].items():
            if k in ("serial", "asset_tag", "facility", "cid", "address", "prefix") and isinstance(v, str):
                assert v in said or v.lower() in said.lower() or kind == "address_no_prefix" or k == "address", (k, v, said)


@pytest.mark.parametrize("field", ["serial", "asset_tag", "platform", "primary_ip"])
def test_each_device_gap_type_is_listed_with_a_stable_id(field):
    b = Build()
    s = b.site()
    room = b.room(s)
    rack = b.rack(s, room)
    r, sw = linked_pair(b, s, room, rack)
    b.e.platforms[1] = {"id": 1, "name": "Example OS"}
    b.e.devices[r].update(asset_tag="AT-1", platform="Example OS")
    b.e.devices[sw].update({"serial": "S", "asset_tag": "AT-2", "platform": "Example OS", "primary_ip": "192.0.2.5/24"} |
                           {"serial": {"serial": ""}, "asset_tag": {"asset_tag": None}, "platform": {"platform": None},
                            "primary_ip": {"primary_ip": None}}[field])
    a = findings.analyse(b.e)
    assert [g["id"] for g in a.gaps] == [f"device:{sw}:{field}"]
    assert a.completeness(s)["open"] == 1 and a.completeness(s)["percent"] < 100


def test_rack_and_site_gap_types_and_the_fields_nobody_uses():
    b = Build()
    s = b.site(latitude=None, facility=None, country=None)
    room = b.room(s)
    b.rack(s, None, "R-nowhere")
    b.rack(s, room, "R-unplaced", iso_x=None, iso_y=None)
    linked_pair(b, s)
    ids = [g["id"] for g in findings.analyse(b.e).gaps]
    assert ids == [f"site:{s}:country", f"site:{s}:facility", f"site:{s}:gps", "rack:3:location", "rack:4:iso"]
    # no device anywhere has an asset tag, and NetBox has no platform: neither is a gap
    assert not [i for i in ids if "asset_tag" in i or "platform" in i]


def test_interface_gaps_only_where_netbox_says_the_port_is_in_use_and_never_a_flood():
    b = Build()
    s = b.site()
    room = b.room(s)
    rack = b.rack(s, room)
    r = b.dev(s, "rtr1", "router", room, rack, ports=2)
    big = b.dev(s, "sw-big", "switch", room, rack, ports=200)
    b.cable(b.p(r, "eth1"), b.p(big, "eth1"))
    b.ip(b.p(r, "eth2"), "192.0.2.1/24")
    b.e.prefixes[1] = {"id": 1, "prefix": "192.0.2.0/24"}
    a = findings.analyse(b.e)
    # 199 empty ports on a switch are not gaps; rtr1 eth2 carries an address and has no cable
    assert [g["id"] for g in a.gaps if g["field"] == "cable"] == [f"interface:{b.p(r, 'eth2')[1]}:cable"]
    for k in range(2, 120):
        b.e.ports[b.p(big, f"eth{k}")]["description"] = "desk"
    b.e.touch()   # the estate was changed in memory: its analysis is made again
    a = findings.analyse(b.e)
    # one device lists at most three such ports; every one of them still counts against the site
    per = findings.MAX_INTERFACE_GAPS_PER_DEVICE
    assert len([g for g in a.gaps if g["field"] == "cable"]) == 1 + per and a.unlisted[s] == 118 - per
    assert a.completeness(s)["open"] == 119
    # many devices: the site's list stops at its cap
    for n in range(30):
        d = b.dev(s, f"sw{n}", "switch", room, rack, ports=4)
        b.cable(b.p(d, "eth1"), b.p(big, f"eth{130 + n}"))
        for k in (2, 3, 4):
            b.e.ports[b.p(d, f"eth{k}")]["description"] = "desk"
    b.e.touch()
    a = findings.analyse(b.e)
    assert len([g for g in a.gaps if g["field"] == "cable"]) == findings.MAX_INTERFACE_GAPS and a.completeness(s)["open"] == 119 + 90


def test_an_address_on_a_management_port_does_not_ask_for_a_cable_but_a_description_does():
    b = Build()
    s = b.site()
    room = b.room(s)
    rack = b.rack(s, room)
    r, sw = linked_pair(b, s, room, rack)
    mgmt = b.port(sw, "interface", "mgmt0", mgmt_only=True)
    b.ip(mgmt, "192.0.2.9/24")
    assert not [g for g in findings.analyse(b.e).gaps if g["field"] == "cable"]
    assert f"interface:{mgmt[1]}:cable" not in findings.analyse(b.e).checks[s]
    b.e.ports[mgmt]["description"] = "to the out-of-band switch"
    b.e.touch()
    assert [g["id"] for g in findings.analyse(b.e).gaps if g["field"] == "cable"] == [f"interface:{mgmt[1]}:cable"]


def test_a_clean_estate_is_all_in_order_and_nothing_is_invented():
    b = Build()
    s = b.site()
    linked_pair(b, s)
    a = findings.analyse(b.e)
    assert a.findings == [] and a.gaps == [] and quests.lines(b.e, a) == []
    assert a.completeness(s) == {"percent": 100, "total": a.completeness(s)["total"], "open": 0} and a.completeness(s)["total"] > 5


def test_a_list_the_scan_could_not_read_makes_its_rules_silent():
    e = device_unconnected()
    e.unreadable.append("cables")
    assert not [f for f in findings.analyse(e).findings if f["kind"] == "device_unconnected"]
    e = address_no_prefix()
    e.truncated.append("prefixes")
    assert not [f for f in findings.analyse(e).findings if f["kind"] == "address_no_prefix"]


# ---------------------------------------------------------------- the example: three switches, an ISP, two rooms
def test_the_unconnected_example_gives_the_expected_findings_in_order():
    e = practice.example_estate(complete=True)
    a = findings.analyse(e)
    assert [f["id"] for f in a.findings] == [
        "site_no_way_out:site:9000001", "locations_not_linked:location:9000002",
        "device_unconnected:device:9000001", "device_unconnected:device:9000002", "device_unconnected:device:9000003"]
    assert all(f["rank"] == "connectivity" for f in a.findings)
    assert a.findings[0]["evidence"] == [{"kind": "site", "id": 9000001, "name": "Practice Island"},
                                         {"kind": "provider", "id": 9000001, "name": "Example Fibre"}]
    lines = quests.lines(e, a)
    assert [x["purpose"] for x in lines[:3]] == [
        "Nobody can tell from NetBox how Practice Island reaches the outside world. Let's find out.",
        "Loft and Quay Room at Practice Island have no link between them. If that link fails, nobody knows which cable to check. Let's record it.",
        "sw-quay-1 has 26 ports and nothing recorded on any of them. If it drops off the network, nobody can tell which port to check. "
        "Let's record its uplink."]
    assert lines[1]["title"] == "The link between Loft and Quay Room" and lines[1]["outcome"] == "You can now trace from Loft to Quay Room."
    q = quests.evaluate(e, lines[2], {})["step"]
    assert q["prompt"] == "sw-quay-1 has 26 ports and nothing recorded on any of them. What does it uplink to?"
    assert [c["label"] for c in q["choices"]] == ["sw-quay-2 (switch)", "sw-loft-1 (switch)", "Something not listed",
                                                  "Nothing: it stands alone, on purpose", "I don't know yet"]


def test_the_example_plays_through_until_the_whole_site_can_be_traced():
    e = practice.example_estate(complete=True)
    for expected in ("site_no_way_out", "locations_not_linked", "site_island"):
        a = findings.analyse(e)
        line = quests.lines(e, a)[0]
        assert line["kind"] == expected
        state, _ = play(e, line, {"device": "sw-quay-1", "peer": "sw-quay-2" if expected == "locations_not_linked" else "sw-loft-1"})
        assert simulate(e, state["proposal"]["ops"])[-1]["error"] is None
    a = findings.analyse(e)
    assert a.findings == [] and a.completeness(9000001)["percent"] == 100
    comp = e.components()
    assert len({comp.of_device(d) for d in e.devices}) == 1 and comp.of_circuit(next(iter(e.circuits))) == comp.of_device(9000003)


def test_i_dont_know_yet_parks_with_a_checklist_and_resumes_at_the_same_question():
    e = practice.example_estate()
    line = one(e, "device_unconnected")
    answers = {"peer": {"choice": "device:9000002"}, "local_port": {"choice": "unknown"}}
    state = quests.evaluate(e, line, answers)
    assert state["status"] == "parked" and state["step"]["id"] == "local_port" and state["proposal"] is None
    assert state["checklist"] == ["Find sw-quay-1: rack PI-Q1 U10 in Quay Room.", "Read the label printed next to the port the cable is in."]
    assert state["said"] == [{"step": "peer", "question": "sw-quay-1 has 26 ports and nothing recorded on any of them. What does it uplink to?",
                              "answer": "sw-quay-2 (switch)"}]
    answers["local_port"] = {"choice": state["step"]["choices"][0]["id"]}
    assert quests.evaluate(e, line, answers)["step"]["id"] == "peer_port"


def test_something_not_listed_and_not_applicable_never_write():
    e = practice.example_estate()
    line = one(e, "device_unconnected")
    state = quests.evaluate(e, line, {"peer": {"choice": "other"}, "other_name": {"text": "old-hub"}})
    assert state["status"] == "parked" and state["proposal"] is None
    assert state["note"] == "You said sw-quay-1 connects to old-hub, which is not in NetBox yet."
    assert state["checklist"][0] == "Add old-hub to NetBox (Devices > Add, with its type and role)."
    state = quests.evaluate(e, line, {"peer": {"choice": "none"}})
    assert (state["status"], state["proposal"]) == ("dismissed", None)


def test_a_typed_value_that_does_not_fit_is_asked_again_with_the_reason():
    e = practice.example_estate()
    line = one(e, "device_unconnected")
    base = {"peer": {"choice": "device:9000002"}, "local_port": {"choice": "typed"}}
    state = quests.evaluate(e, line, {**base, "local_port_name": {"text": "Gi9/9/9"}})
    assert state["status"] == "asking" and state["step"]["error"] == "sw-quay-1 has no free port called 'Gi9/9/9'."
    assert quests.evaluate(e, line, {**base, "local_port_name": {"text": "gi1/0/17"}})["step"]["id"] == "peer_port"


def test_an_answer_that_no_longer_fits_the_estate_is_asked_again():
    e = practice.example_estate()
    line = one(e, "device_unconnected")
    answers = {"peer": {"choice": "device:9000002"}, "local_port": {"choice": "interface:9000026"}}
    assert quests.evaluate(e, line, answers)["step"]["id"] == "peer_port"
    e.ports[("interface", 9000026)]["enabled"] = False   # the port is no longer one of the choices
    e.touch()
    assert quests.evaluate(e, line, answers)["step"]["id"] == "local_port"


def test_an_audit_takes_what_the_player_read_and_leaves_the_rest_open():
    e = rack_audit()
    line = one(e, "rack_audit")
    step = quests.evaluate(e, line, {})["step"]
    assert step["input"] == "form" and step["prompt"].startswith("Walk rack R1 top to bottom")
    ids = [f["id"] for f in step["fields"]]
    sw = next(i for i, d in e.devices.items() if d["name"] == "sw1")
    assert ids == [f"device:{sw}:serial", f"device:{sw}:asset_tag"]   # what it runs is a question of its own, per model
    blank = quests.evaluate(e, line, {"labels": {"values": {}}})
    assert blank["status"] == "parked" and blank["checklist"][1] == "Start at the top unit and work down, one device at a time."
    state = quests.evaluate(e, line, {"labels": {"values": {ids[0]: "SN-77", ids[1]: "  "}}})
    assert state["proposal"]["ops"] == [{"op": "update_fields", "target": {"kind": "device", "id": sw}, "fields": {"serial": "SN-77"}}]
    assert settle(e, line, state) == [f"device:{sw}:asset_tag"]


def test_the_label_maker_suggests_the_next_tag_of_the_sites_pattern_and_never_fills_it_in():
    e = rack_audit()
    line = one(e, "rack_audit")
    plain = quests.evaluate(e, line, {})["step"]["fields"][1]
    with_tool = quests.evaluate(e, line, {}, frozenset({"label_maker"}))["step"]["fields"][1]
    assert plain["suggestion"] is None and with_tool["suggestion"] == "AT-0042"
    assert quests.evaluate(e, line, {"labels": {"values": {}}}, frozenset({"label_maker"}))["status"] == "parked"


def test_xp_follows_rank_and_verified_share_and_repeats_pay_less():
    assert [quests.XP[r] for r in findings.RANKS] == [120, 50, 30]
    assert [xp_for(120, 1, 1, n) for n in range(5)] == [120, 96, 72, 48, 48]
    # an errand with six labels pays by what NetBox confirms, not by fields touched: two confirmed of six
    assert xp_for(50, 2, 6, 0) == 17 and xp_for(50, 6, 6, 0) == 50 and xp_for(50, 0, 6, 0) == 0


def test_a_large_estate_is_analysed_in_one_pass_not_once_per_device():
    """2,000 switches with 48 ports each (96,000 ports), half of them cabled in pairs: the scan and the first page of
    quest lines stay well under a few seconds, and asking again costs nothing (the analysis is kept with the estate)."""
    import time

    b = Build()
    s = b.site()
    room = b.room(s)
    racks = [b.rack(s, room, f"R{i}") for i in range(50)]
    devs = [b.dev(s, f"sw{i:04d}", "switch", room, racks[i % 50], ports=48) for i in range(2000)]
    for i in range(0, 1000, 2):
        b.cable(b.p(devs[i], "eth1"), b.p(devs[i + 1], "eth1"))
    t0 = time.perf_counter()
    a = findings.analyse(b.e)
    lines = quests.lines(b.e, a)
    states = [quests.evaluate(b.e, line, {}) for line in lines[:3]]
    took = time.perf_counter() - t0
    assert len(b.e.ports) == 96_000 and len([f for f in a.findings if f["kind"] == "device_unconnected"]) == 1000
    assert all(st["status"] == "asking" and len(st["step"]["choices"]) <= quests.MAX_CANDIDATES + 3 for st in states)
    assert took < 20, f"{took:.1f} s"
    t1 = time.perf_counter()
    assert findings.analyse(b.e) is a and quests.lines(b.e, a) is lines
    assert time.perf_counter() - t1 < 0.01


def test_every_opening_line_names_what_is_missing_and_where():
    """A quest never opens with "a port" or "these rooms": its first sentence is the finding, with the names NetBox has."""
    for case in CASES:
        e = case()
        line = one(e, case.__name__)
        f = line["finding"]
        assert line["purpose"].startswith(f["title"]), line["purpose"]
        named = [r["name"] for r in f["evidence"]]
        assert any(n in f["title"] for n in named), (case.__name__, f["title"], named)
        assert not any(vague in line["purpose"] for vague in ("A port is", "these rooms", "These devices", "this rack")), line["purpose"]
    b = Build()
    s = b.site("Mill Lane", facility=None)
    linked_pair(b, s)
    line = one(b.e, "site_record")
    assert line["purpose"] == "Mill Lane has no facility name on record, so it cannot be named to a carrier. Let's fill that in."
    b.e.sites[s].update(latitude=None, country=None)
    b.e.touch()
    assert one(b.e, "site_record")["finding"]["title"] == ("Mill Lane has no GPS position, facility name or country on record, so it cannot be "
                                                          "put on a map or named to a carrier.")
    port = one(interface_no_cable(), "interface_no_cable")
    assert port["purpose"].startswith("sw1 eth4 has a description (uplink to the annex) but no cable.")


def test_ports_are_offered_likeliest_uplink_first():
    e = practice.example_estate()
    line = one(e, "device_unconnected")
    step = quests.evaluate(e, line, {"peer": {"choice": "device:9000002"}})["step"]
    # the fibre cages before the copper ports, the highest-numbered first; the rest behind "Another port"
    assert [c["label"] for c in step["choices"]] == ["Te1/1/2", "Te1/1/1", "Gi1/0/24", "Gi1/0/23", "Gi1/0/22", "Gi1/0/21", "Another port",
                                                     "I don't know yet"]
    b = Build()
    s = b.site()
    d = b.dev(s, "sw", ports=3)
    b.port(d, "interface", "mgmt0", mgmt_only=True)
    assert [p["name"] for p in quests.likely_uplinks(b.e.free_ports(d))] == ["eth3", "eth2", "eth1", "mgmt0"]


def test_one_answer_says_what_every_device_of_a_model_runs_unless_they_differ():
    e = platform_survey()
    line = one(e, "platform_survey")
    ids = line["finding"]["data"]["device_ids"]
    assert line["title"] == "What the M devices run" and line["finding"]["title"] == "NetBox does not say what sw1 and sw2 (M) at Mill run."
    step = quests.evaluate(e, line, {})["step"]
    assert step["prompt"] == "2 M devices have no platform on record: sw1 and sw2. What do they run?"
    assert [c["label"] for c in step["choices"]] == ["Example OS", "They do not all run the same", "Something not listed", "I don't know yet"]
    state = quests.evaluate(e, line, {"platform": {"choice": "platform:1"}})
    assert state["proposal"]["ops"] == [{"op": "update_fields", "target": {"kind": "device", "id": i}, "fields": {"platform_id": 1}} for i in ids]
    assert state["changes"] == ["sw1: platform Example OS.", "sw2: platform Example OS."] and settle(e, line, state) == []
    # where they differ, each device is asked about; a blank is left open
    each = quests.evaluate(e, line, {"platform": {"choice": "mixed"}})["step"]
    assert each["input"] == "form" and [f["id"] for f in each["fields"]] == [f"device:{i}:platform" for i in ids]
    state = quests.evaluate(e, line, {"platform": {"choice": "mixed"}, "each": {"values": {f"device:{ids[0]}:platform": "1"}}})
    assert settle(e, line, state) == [f"device:{ids[1]}:platform"]
    # a platform NetBox does not have yet is never invented
    other = quests.evaluate(e, line, {"platform": {"choice": "other"}})
    assert other["status"] == "parked" and other["checklist"][0] == "Add the platform to NetBox (Devices > Platforms > Add)."


def test_a_device_nobody_logs_in_to_is_said_once_and_never_asked_or_paid():
    e = device_unreachable()
    line = one(e, "device_unreachable")
    ids = line["finding"]["data"]["device_ids"]
    first = quests.evaluate(e, line, {})["step"]
    assert first["choices"][-2] == {"id": "none", "label": "It has none: nobody logs in to it", "kind": "not_applicable"}
    answers = {f"ip_{ids[0]}": {"choice": "none"}}
    state = quests.evaluate(e, line, answers)
    assert state["waived"] == [f"device:{ids[0]}:primary_ip"] == quests.waived(line, answers)
    if len(ids) == 1:
        assert state["status"] == "dismissed" and state["proposal"] is None
    else:
        assert state["status"] == "asking" and state["step"]["id"] == f"ip_{ids[1]}"
    # a router's address is not something to wave away
    router = one(router_no_address(), "router_no_address")
    assert "none" not in [c["id"] for c in quests.evaluate(router_no_address(), router, {})["step"]["choices"]]
    assert quests.waived(router, {"ip_1": {"choice": "none"}}) == []


def test_answers_about_what_netbox_confirms_are_dropped_and_the_rest_are_kept():
    answers = {"labels": {"values": {"device:5:serial": "S5", "device:6:serial": "S6", "device:6:asset_tag": "T6"}},
               "ip_7": {"choice": "typed"}, "addr_7": {"text": "192.0.2.7/24"}, "port_7": {"choice": "interface:70"},
               "ip_17": {"choice": "ip:3"}, "ip_8": {"choice": "none"}, "pdu_9": {"choice": "device:2"}, "outlet_9": {"choice": "power_outlet:4"},
               "floor": {"values": {"room:3": "1", "x:3": 2, "y:3": 4, "x:4": 1, "y:4": 1}}, "units": {"values": {"u:5": 3, "u:6": 4}},
               "record": {"values": {"latitude": 52.1, "longitude": 4.3, "facility": "F"}}, "peer": {"choice": "device:2"}}
    assert quests.prune(answers, []) == answers
    left = quests.prune(answers, ["device:5:serial", "device:7:primary_ip", "device:8:primary_ip", "device:9:power", "rack:3:iso", "site:1:gps"])
    assert left == {"labels": {"values": {"device:6:serial": "S6", "device:6:asset_tag": "T6"}}, "ip_17": {"choice": "ip:3"},
                    "ip_8": {"choice": "none"},                                   # "it has none" is remembered
                    "floor": {"values": {"room:3": "1", "x:4": 1, "y:4": 1}}, "units": {"values": {"u:6": 4}},
                    "record": {"values": {"facility": "F"}}, "peer": {"choice": "device:2"}}
    assert quests.prune({"labels": {"values": {"device:5:serial": "S5"}}}, ["device:5:serial"]) == {}
