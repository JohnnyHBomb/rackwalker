"""The quest routes at their edges, on a made-up NetBox: a record some of which NetBox held all along, a stack, two
circuits in one change, a model's default at another site, a read that fails after the write, a player who may not view
what a circuit's record holds, and how many lists are asked of NetBox at a time."""
from __future__ import annotations

import asyncio
from typing import Any

import httpx

from rackwalker_proxy import estate
from rackwalker_proxy import netbox as nb

from .test_chains import ALL, CF_CIRCUIT, CF_DEVICE, RECORD, mill_lane, office, say
from .test_estate_read import FakeNetBox, cable, plug, ref, tiny
from .test_quest_settlement import SW3, Box, answer_to_ready, board, line_of, post, start, wire


def plugged_in(held: bool = True) -> dict[str, list[dict[str, Any]]]:
    """Mill Lane with its circuit cabled and addressed. With `held`, NetBox already holds five of the nine facts of its
    record (the network, the account, the install date, the commit rate, the one-off charge)."""
    d = mill_lane()
    d["circuits/circuits"] = [{"id": 1, "cid": "NLT-7001", "provider": {"id": 1}, "provider_account": {"id": 1} if held else None,
                               "type": {"id": 1, "name": "Internet"}, "status": {"value": "active"}, "install_date": "2024-03-01" if held else None,
                               "commit_rate": 100000 if held else None,
                               "custom_fields": {**CF_CIRCUIT, **({"rw_nrc": 10, "rw_currency": "GBP"} if held else {})}}]
    d["circuits/provider-accounts"] = [{"id": 1, "provider": {"id": 1}, "account": "44-1021", "name": ""}]
    d["circuits/provider-networks"] = [{"id": 1, "provider": {"id": 1}, "name": "Northlight Internet"}]
    d["circuits/circuit-terminations"] = [{"id": 1, "circuit": {"id": 1}, "term_side": "A", "termination_type": "dcim.site", "termination_id": 1,
                                           "cable": {"id": 1}}] + \
        ([{"id": 2, "circuit": {"id": 1}, "term_side": "Z", "termination_type": "circuits.providernetwork", "termination_id": 1, "cable": None}] if held else [])
    d["dcim/cables"] = [{"id": 1, "type": "smf", "label": "", "status": {"value": "connected"},
                         "a_terminations": [{"object_type": "dcim.interface", "object_id": 11}],
                         "b_terminations": [{"object_type": "circuits.circuittermination", "object_id": 1}]}]
    next(p for p in d["dcim/interfaces"] if p["id"] == 11)["cable"] = {"id": 1}
    d["ipam/ip-addresses"] = [{"id": 1, "address": "203.0.113.2/30", "vrf": None, "assigned_object_type": "dcim.interface", "assigned_object_id": 11}]
    d["ipam/prefixes"] = [{"id": 1, "prefix": "203.0.113.0/30", "vrf": None}]
    return d


def test_a_circuits_record_never_pays_for_facts_netbox_held_all_along(tmp_path, monkeypatch):
    cl, box = office(tmp_path, monkeypatch, plugged_in())
    q = line_of(board(cl), RECORD)
    assert q["progress"] == {"total": 4, "verified": 0}        # the three contacts and the monthly charge: what is missing
    # "Check NetBox" on a line nobody has begun finds nothing new, writes nothing and pays nothing
    v = post(cl, "verify", RECORD)
    assert (v["verified"], v["xp_awarded"], v["message"]) == (False, 0, "NetBox does not show it yet.") and box.writes == []
    # one fact of the four answered pays its share of the line, not the whole line
    q = say(cl, RECORD, ("contact_for", {"choice": "unknown"}), ("cost", {"values": {"mrc": 5}}))
    assert q["status"] == "ready" and q["changes"] == ["Circuit NLT-7001: 5.00 GBP a month."]
    v = post(cl, "apply", RECORD)
    assert (v["xp_awarded"], v["message"].split(";")[0]) == (30, "NetBox confirms 1 of 4")
    # the line now waits for the contacts the player does not know: it is no longer "the next small step"
    b = board(cl)
    assert line_of(b, RECORD)["status"] == "parked" and b["next_quest_id"] != RECORD
    assert RECORD not in [x["id"] for x in b["quests"] if x["status"] in ("offered", "active")]


def test_a_stack_said_to_be_managed_locally_keeps_its_line_and_its_check(tmp_path, monkeypatch):
    d = mill_lane()
    for dev in d["dcim/devices"][:2]:
        dev["virtual_chassis"] = ref(1)
    for n, (a, b) in enumerate(((11, 21), (12, 31)), 1):
        c = cable(n, a, b)
        d["dcim/cables"].append(c)
        plug(d, c, a, b)
    cl, box = office(tmp_path, monkeypatch, d)
    line = "q:device_unreachable:device:1"
    before = board(cl)["sites"][0]["completeness"]
    say(cl, line, ("mode_1", {"choice": "local"}), ("model_1", {"choice": "varies"}), ("ip_1", {"choice": "unknown"}))
    v = post(cl, "apply", line)
    assert box.data["dcim/devices"][0]["custom_fields"]["rw_mgmt_mode"] == "local"
    # a write of what was known is no failed write, and the question about the address is not lost with it
    assert v["message"].startswith("NetBox took what you knew. The line stays open for the rest.") and v["quest"]["status"] != "dismissed"
    b = board(cl)
    assert b["sites"][0]["completeness"] == before
    assert "device_unreachable:device:1" in [f["id"] for f in b["findings"]] and line_of(b, line) is not None
    gaps = cl.get("/api/quests/gaps/1").json()["items"]
    assert [g["label"] for g in gaps if g["id"] == "device:1:primary_ip"] == ["ml-sw1 is managed locally, but has no management address (primary IP)."]


def test_two_ports_to_two_circuits_are_written_in_one_change(tmp_path, monkeypatch):
    d = mill_lane()
    c = cable(1, 13, 21)
    d["dcim/cables"].append(c)
    plug(d, c, 13, 21)
    for p in d["dcim/interfaces"]:
        if p["id"] in (11, 12):
            p["description"] = f"WAN{p['id'] - 10}"
    d["circuits/providers"].append({"id": 2, "name": "Southlight"})
    d["circuits/circuits"] = [{"id": n, "cid": cid, "provider": {"id": n}, "provider_account": None, "type": {"id": 1, "name": "Internet"},
                               "status": {"value": "active"}, "install_date": None, "commit_rate": None, "custom_fields": dict(CF_CIRCUIT)}
                              for n, cid in ((1, "NLT-1"), (2, "SL-2"))]
    cl, box = office(tmp_path, monkeypatch, d)
    line = "q:interface_no_cable:device:1"
    q = say(cl, line, ("what", {"choice": "each"}), ("peer_11", {"choice": "circuit:1"}), ("cable_type_11", {"choice": "smf"}),
            ("peer_12", {"choice": "circuit:2"}), ("cable_type_12", {"choice": "smf"}))
    assert q["status"] == "ready"
    r = cl.post("/api/game/quests/preview", json={"scope": "netbox", "quest_id": line})
    assert [o["status"] for o in r.json()["ops"]] == ["ok"] * 4
    v = post(cl, "apply", line)
    assert (v["result"]["applied"], v["complete"]) == (True, True)
    assert [p for _, p, _ in box.writes] == ["circuits/circuit-terminations", "dcim/cables", "circuits/circuit-terminations", "dcim/cables"]


def test_a_models_default_is_seen_at_every_site_straight_after_it_is_written(tmp_path, monkeypatch):
    d = mill_lane()
    d["dcim/sites"].append({**d["dcim/sites"][0], "id": 2, "name": "Quay", "slug": "quay"})
    d["dcim/locations"].append({"id": 3, "name": "Shed", "site": ref(2)})
    d["dcim/racks"].append({**d["dcim/racks"][0], "id": 3, "name": "Q1", "site": ref(2), "location": ref(3)})
    d["dcim/devices"].append({**d["dcim/devices"][0], "id": 4, "name": "qy-sw1", "site": ref(2), "location": ref(3), "rack": ref(3),
                              "custom_fields": dict(CF_DEVICE)})
    d["dcim/interfaces"] += [{**d["dcim/interfaces"][0], "id": 40 + p, "name": f"Te1/1/{p}", "device": ref(4, name="qy-sw1")} for p in (1, 2, 3)]
    cl, box = office(tmp_path, monkeypatch, d)
    assert "device_unreachable:device:4" in [f["id"] for f in board(cl, site_id=2)["findings"]]
    line = "q:device_unreachable:device:1"
    say(cl, line, ("mode_1", {"choice": "cloud"}), ("service_1", {"choice": "typed"}), ("service_name_1", {"text": "Example Cloud"}),
        ("model_1", {"choice": "all"}))
    assert post(cl, "apply", line)["complete"]
    # no other device of the model is asked again, also at the other site, without waiting for the kept estate to expire
    assert not [f["id"] for f in board(cl, site_id=2)["findings"] if f["kind"] == "device_unreachable"]


def test_a_read_that_fails_after_the_write_says_that_the_change_was_written(tmp_path, monkeypatch):
    cl, box, app = start(tmp_path, monkeypatch, tiny())
    answer_to_ready(cl)
    real = Box.__call__
    wrote = []

    def flaky(self, req):
        if req.method == "POST" and req.url.path.endswith("/dcim/cables/"):
            wrote.append(1)
        elif wrote and req.method == "GET" and req.url.path.endswith("/dcim/devices/"):
            return httpx.Response(502, text="bad gateway")
        return real(self, req)
    monkeypatch.setattr(Box, "__call__", flaky)
    r = cl.post("/api/game/quests/apply", json={"scope": "netbox", "quest_id": SW3})
    err = r.json()["error"]
    assert r.status_code == 502 and err["details"]["written"] is True
    assert err["message"].startswith("NetBox took that change, but the game could not read NetBox again to confirm it") and "Check NetBox" in err["message"]
    monkeypatch.setattr(Box, "__call__", real)
    v = post(cl, "verify", SW3)
    assert (v["complete"], v["xp_awarded"]) == (True, 120) and sum(1 for _, p, _ in box.writes if p == "dcim/cables") == 1


def test_a_port_said_to_be_unused_and_later_cabled_in_netbox_leaves_its_line_closed(tmp_path, monkeypatch):
    data = tiny()
    wire(data)
    data["dcim/interfaces"][3]["description"] = "printer on the landing"      # sw2 eth2: described, no cable
    cl, box, app = start(tmp_path, monkeypatch, data)
    port = "q:interface_no_cable:device:2"
    assert post(cl, "answer", port, step="peer_22", choice="none")["status"] == "dismissed"
    c = cable(77, 22, 32)                                                     # somebody records the cable in NetBox itself
    data["dcim/cables"].append(c)
    plug(data, c, 22, 32)
    for _ in range(2):
        v = post(cl, "verify", port)
        assert (v["complete"], v["xp_awarded"], v["quest"]["status"]) == (False, 0, "dismissed")
        assert v["message"].startswith('You closed this line with "that is how it is". It stays closed')
        # not listed as a line that is ready with nothing to write
        assert [q["status"] for q in board(cl, fresh=True)["quests"] if q["id"] == port] == ["dismissed"]


def test_i_dont_know_yet_on_the_uplink_leaves_the_management_question_open_to_answer(tmp_path, monkeypatch):
    cl, box = office(tmp_path, monkeypatch)
    uplink, managed = "q:device_unconnected:device:2", "q:device_unreachable:device:2"
    b = board(cl, site_id=1)
    assert line_of(b, managed) is None
    assert post(cl, "answer", uplink, step="peer", choice="unknown")["status"] == "parked"
    chain = next(c for c in board(cl)["chains"] if c["id"] == "chain:device:device:2")
    assert [(s["stage"], s["status"]) for s in chain["stages"]] == [("physical", "not_known"), ("logical", "open")]
    # an admin who knows the switch is cloud-managed can say so before anyone has walked to the rack
    q = post(cl, "answer", managed, step="mode_2", choice="unmanaged")
    assert q["step"]["id"] == "model_2"
    q = post(cl, "answer", managed, step="model_2", choice="varies")
    assert q["status"] == "ready" and post(cl, "apply", managed)["complete"]


VIEW_ONLY_DEVICES = [{"id": 1, "enabled": True, "constraints": None, "actions": ["view"], "object_types": ["dcim.device", "dcim.site"]}]
RECORDED = ("SYN-ACC-9911", "Zed Example", "960123", "4321.09", "777.77")


def recorded() -> dict[str, list[dict[str, Any]]]:
    d = plugged_in()
    d["circuits/provider-accounts"][0]["account"] = "SYN-ACC-9911"
    d["circuits/circuits"][0]["custom_fields"] = {"rw_mrc": 4321.09, "rw_nrc": 777.77, "rw_currency": "GBP"}
    d["tenancy/contacts"] = [{"id": 1, "name": "Zed Example", "phone": "+44 1632 960123", "email": ""}]
    d["tenancy/contact-assignments"] = [{"id": 1, "object_type": "circuits.circuit", "object_id": 1, "contact": {"id": 1}, "role": {"id": 1}}]
    return d


def test_the_figures_of_a_record_are_shown_only_to_a_player_whose_own_netbox_user_may_view_them(tmp_path, monkeypatch):
    cl, box = office(tmp_path, monkeypatch, recorded())
    chain = next(c for c in board(cl)["chains"] if c["id"] == "chain:way_out:site:1")
    assert all(x in chain["summary"] for x in RECORDED)          # a player who may view them reads the whole record
    box.permissions = VIEW_ONLY_DEVICES
    cl.app.state.permission_reader.clear()
    body = cl.get("/api/game/quests", params={"scope": "netbox"}).text
    assert not [x for x in RECORDED if x in body]
    chain = next(c for c in board(cl)["chains"] if c["id"] == "chain:way_out:site:1")
    assert "an account on record" in chain["summary"] and "a contact on record" in chain["summary"] and "its charges and terms on record" in chain["summary"]
    # the plan of a change to the record is not made for that player either: it would answer guesses with what NetBox holds
    guess = {"ops": [{"op": "assign_contact", "target": {"kind": "circuit", "id": 1}, "fields": {"contact_id": 1, "role_id": 1}}]}
    r = cl.post("/api/changes/preview", json=guess)
    assert r.status_code == 403 and r.json()["error"]["details"]["reason"] == "player_lacks_permission" and "Zed" not in r.text
    for op in ({"op": "record_circuit", "target": {"kind": "circuit", "id": 1}, "fields": {"mrc": "4321.09", "currency": "GBP"}},
               {"op": "create_provider_account", "target": {"kind": "provider_account", "id": None}, "fields": {"provider_id": 1, "account": "SYN-ACC-9911"}}):
        assert cl.post("/api/changes/preview", json={"ops": [op]}).status_code == 403
    # other plans are made as before
    ok = {"ops": [{"op": "update_fields", "target": {"kind": "device", "id": 1}, "fields": {"serial": "SYN-1"}}]}
    assert cl.post("/api/changes/preview", json=ok).status_code == 200


def test_a_dry_run_proxy_plans_no_change_to_a_record_for_somebody_who_is_not_signed_in(tmp_path, monkeypatch):
    box = Box(recorded(), ALL)
    from fastapi.testclient import TestClient
    from rackwalker_proxy.app import Settings, create_app
    from .conftest import LOCAL
    app = create_app(Settings("http://netbox.invalid", "nbt_x.y", False, None, cache_ttl=0, state_dir=tmp_path, estate_ttl=60),
                     transport=httpx.MockTransport(box))
    with TestClient(base_url=LOCAL, app=app, raise_server_exceptions=False) as cl:
        guess = {"ops": [{"op": "assign_contact", "target": {"kind": "provider", "id": 1}, "fields": {"contact_id": 1, "role_id": 1}}]}
        r = cl.post("/api/changes/preview", json=guess)
        assert r.status_code == 401 and "Zed" not in r.text
        # the viewer's own previews (a rack moved, a field edited) need no sign-in on a dry-run proxy, as before
        ok = {"ops": [{"op": "update_fields", "target": {"kind": "device", "id": 1}, "fields": {"serial": "SYN-1"}}]}
        assert cl.post("/api/changes/preview", json=ok).status_code == 200


def test_a_circuit_end_recorded_on_a_room_is_at_that_rooms_site(tmp_path, monkeypatch):
    d = mill_lane()
    d["dcim/sites"].append({**d["dcim/sites"][0], "id": 2, "name": "Quay", "slug": "quay"})
    d["dcim/locations"].append({"id": 3, "name": "Shed", "site": ref(2)})
    d["dcim/racks"].append({**d["dcim/racks"][0], "id": 3, "name": "Q1", "site": ref(2), "location": ref(3)})
    d["dcim/devices"].append({**d["dcim/devices"][0], "id": 4, "name": "qy-sw1", "site": ref(2), "location": ref(3), "rack": ref(3)})
    d["dcim/interfaces"] += [{**d["dcim/interfaces"][0], "id": 40 + p, "name": f"Te1/1/{p}", "device": ref(4, name="qy-sw1")} for p in (1, 2, 3)]
    d["circuits/circuits"] = [{"id": 1, "cid": "LOC-1", "provider": {"id": 1}, "provider_account": None, "type": {"id": 1, "name": "Internet"},
                               "status": {"value": "active"}, "install_date": None, "commit_rate": None, "custom_fields": dict(CF_CIRCUIT)}]
    d["circuits/circuit-terminations"] = [{"id": 1, "circuit": {"id": 1}, "term_side": "A", "termination_type": "dcim.location", "termination_id": 3,
                                           "cable": None}]
    cl, box = office(tmp_path, monkeypatch, d)
    where = {f["id"]: f["site_id"] for f in board(cl)["findings"] if f["kind"] in ("circuit_unterminated", "site_no_way_out")}
    # asked once, at the site that holds the room; not at the first site, and not as a second "no way out" beside it
    assert where == {"circuit_unterminated:circuit:1": 2, "site_no_way_out:site:1": 1}


def test_an_estate_read_asks_netbox_for_a_few_lists_at_a_time():
    fake = FakeNetBox(tiny())
    now, most = [0], [0]

    async def slow(req: httpx.Request) -> httpx.Response:
        now[0] += 1
        most[0] = max(most[0], now[0])
        await asyncio.sleep(0.002)
        now[0] -= 1
        return fake(req)

    async def go() -> estate.Estate:
        client = nb.NetBox("http://netbox.invalid", "nbt_x.y", transport=httpx.MockTransport(slow))
        try:
            return await estate.read_estate(client)
        finally:
            await client.aclose()
    e = asyncio.run(go())
    assert len(e.devices) == 3
    # twenty-odd lists make up one read; a NetBox with a small database pool is never asked for all of them at once
    assert 1 < most[0] <= estate.READ_CONCURRENCY + 2, most
