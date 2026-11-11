"""The setup companion on a NetBox somebody has already been working in, live (NetBox 4.7.4). Skipped unless
RACKWALKER_LIVE_NETBOX=1; runs on a seeded stack and on a never-seeded one, and removes everything it made.

A real NetBox is not the game's own: its device types carry the part number of the box that was bought, its
manufacturers are spelled the long way, its country is a region under the slug NetBox's form gave it, a model was made
by hand with no port on it, a provider is written in Cyrillic. Each test here builds one such state through NetBox's
own API and plays the game against it.

Imports read the library files in tests/library; nothing is fetched from the internet.
"""
from __future__ import annotations

import os
from pathlib import Path

import httpx
import pytest

from rackwalker_proxy import packs, questroutes

from .test_live_quests import ADMIN, URL, _board, _client
from .test_live_setup import Play, lib, rows

pytestmark = pytest.mark.skipif(os.environ.get("RACKWALKER_LIVE_NETBOX") != "1", reason="live NetBox tests: make test-live")
LIBRARY = Path(__file__).parent / "library"
# everything a test may make, parents last: what is new in these lists after the module is removed again
TABLES = ("dcim/cables/", "dcim/modules/", "dcim/devices/", "dcim/racks/", "circuits/circuits/", "circuits/providers/", "ipam/asns/", "dcim/sites/",
          "dcim/device-types/", "dcim/module-types/", "dcim/manufacturers/", "dcim/device-roles/", "dcim/regions/", "extras/tags/", "ipam/rirs/")


@pytest.fixture(autouse=True)
def offline_library(monkeypatch):
    monkeypatch.setenv("RACKWALKER_LIBRARY_DIR", str(LIBRARY))
    monkeypatch.setenv("RACKWALKER_LIBRARY_FETCH", "0")
    monkeypatch.setattr(questroutes, "FRESH_SITE_S", 0.0)
    monkeypatch.setattr(questroutes, "FRESH_ALL_S", 0.0)
    packs._DEFINITIONS.clear()


@pytest.fixture(scope="module")
def nbx():
    with httpx.Client(base_url=URL + "/api/", headers={"Authorization": f"Bearer {ADMIN}"}, timeout=30) as c:
        yield c


def every(nbx, path: str) -> list[dict]:
    out, offset = [], 0
    while True:
        page = nbx.get(path, params={"limit": 500, "offset": offset, "brief": 1}).json()
        out += page["results"]
        offset += 500
        if offset >= page["count"]:
            return out


@pytest.fixture(scope="module")
def yard(nbx):
    """A site with one rack, and the means to make more. What NetBox holds before is left exactly as it was."""
    before = {path: {r["id"] for r in every(nbx, path)} for path in TABLES}

    def make(path: str, body: dict) -> dict:
        r = nbx.post(path, json=body)
        assert r.status_code == 201, (path, r.text)
        return r.json()

    def site(name: str, **more) -> dict:
        s = make("dcim/sites/", {"name": name, "slug": packs.slugify(name), "status": "active", **more})
        make("dcim/racks/", {"site": s["id"], "name": f"{name[:2].upper()}-1", "status": "active", "u_height": 42})
        return s
    try:
        yield {"make": make, "site": site}
    finally:
        for path in TABLES:
            for row in every(nbx, path):
                if row["id"] not in before[path]:
                    r = nbx.delete(f"{path}{row['id']}/")
                    assert r.status_code in (204, 404), (path, row, r.text)


@pytest.fixture(scope="module")
def player(tmp_path_factory):
    with _client(tmp_path_factory.mktemp("messy-admin"), ADMIN, estate_ttl=600) as cl:
        yield Play(cl)


def fresh(player) -> dict:
    r = player.cl.get("/api/game/quests", params={"scope": "netbox", "fresh": True})
    assert r.status_code == 200, r.text
    return r.json()


def pick(q: dict, label: str) -> dict:
    """The answer that takes the choice whose label starts with `label`."""
    hit = next((c for c in q["step"]["choices"] if c["label"].startswith(label)), None)
    assert hit is not None, (label, [c["label"] for c in q["step"]["choices"]])
    return {"choice": hit["id"]}


def box(player, kit: str, n: int, find: str, label: str, role: str, values: dict, more: str | None) -> dict:
    """One box of a "first boxes" or "more boxes" line: found, picked by its label, given a role and its details."""
    q = player.say(kit, (f"box{n}_find0", {"text": find}))
    q = player.say(kit, (f"box{n}_pick0", pick(q, label)))
    q = player.say(kit, (f"role{n}", pick(q, role)))
    q = player.say(kit, (f"box{n}_details", {"values": values}))
    return player.say(kit, (f"more{n}", {"choice": more})) if more and q["step"] and q["step"]["id"] == f"more{n}" else q


def change(player, verb: str, *ops: dict) -> dict:
    r = player.cl.post(f"/api/changes/{verb}", json={"ops": list(ops), "reason": "a NetBox somebody has been working in"})
    assert r.status_code == 200, r.text
    return r.json()


def test_a_device_type_held_under_the_part_number_that_was_bought_is_built_on_live(nbx, yard, player):
    """A machine type of a family (7X06 where the library lists the pattern 7X0x) and a regional SKU (SRX300-SYS-JE
    where the library lists -JB) are real part numbers. The boxes are made, and nothing rewrites the numbers."""
    make = yard["make"]
    lenovo = make("dcim/manufacturers/", {"name": "Lenovo", "slug": "lenovo"})
    sr650 = make("dcim/device-types/", {"manufacturer": lenovo["id"], "model": "ThinkSystem SR650", "slug": "lenovo-thinksystem-sr650", "part_number": "7X06",
                                        "u_height": 2})
    juniper = rows(nbx, "dcim/manufacturers/", slug="juniper") or [make("dcim/manufacturers/", {"name": "Juniper", "slug": "juniper"})]
    srx = make("dcim/device-types/", {"manufacturer": juniper[0]["id"], "model": "SRX300", "slug": "juniper-srx300", "part_number": "SRX300-SYS-JE"})
    make("dcim/interface-templates/", {"device_type": srx["id"], "name": "ge-0/0/0", "type": "1000base-t"})
    sid = yard["site"]("Label Yard")["id"]
    fresh(player)
    kit = f"q:site_no_devices:site:{sid}"
    q = player.say(kit, ("box0_find0", {"text": "SR650"}))
    # held with no port on it: said so in the choice, and filled from the library when the box is made
    assert "Lenovo ThinkSystem SR650 (in NetBox: no ports)" in [c["label"] for c in q["step"]["choices"]]
    box(player, kit, 0, "SR650", "Lenovo ThinkSystem SR650 (in NetBox", "Server", {"name": "ly-srv1", "position": 10}, "yes")
    q = player.say(kit, ("box1_find0", {"text": "SRX300"}))
    assert "Juniper SRX300 (in NetBox: 1 port)" in [c["label"] for c in q["step"]["choices"]]
    q = box(player, kit, 1, "SRX300", "Juniper SRX300 (in NetBox", "Firewall", {"name": "ly-fw1", "position": 20}, "no")
    assert q["changes"][0].startswith("NetBox holds the device type Lenovo ThinkSystem SR650 with no ports: they are added from the device-type library (1 interface")
    pre = player.cl.post("/api/game/quests/preview", json={"scope": "netbox", "quest_id": kit}).json()
    assert [(o["op"], o["status"]) for o in pre["ops"]] == [("add_device_type", "ok"), ("create_device", "ok"), ("create_device", "ok")], pre["ops"]
    assert pre["ops"][0]["after"]["interfaces"] == 1 and pre["ops"][0]["target"]["id"] == sr650["id"]
    v = player.apply(kit)
    assert v["complete"] and v["bonus_xp"] == 40, v["message"]
    srv = rows(nbx, "dcim/devices/", name="ly-srv1")[0]
    assert srv["device_type"]["id"] == sr650["id"] and nbx.get("dcim/interfaces/", params={"device_id": srv["id"]}).json()["count"] == 1
    assert rows(nbx, "dcim/devices/", name="ly-fw1")[0]["device_type"]["id"] == srx["id"]
    # the true numbers stay, and no line asks to "put them right"
    assert nbx.get(f"dcim/device-types/{sr650['id']}/").json()["part_number"] == "7X06"
    b = fresh(player)
    assert not [x["id"] for x in b["quests"] if x["kind"] == "device_type_part_number" and x["id"].endswith((f":{sr650['id']}", f":{srx['id']}"))]
    out = change(player, "apply", {"op": "set_part_number", "target": {"kind": "device_type", "id": sr650["id"]}, "fields": {"part_number": "7X0x"}})
    assert out["ops"][0]["status"] == "error" and "no single part number" in out["ops"][0]["error"]["message"]
    assert nbx.get(f"dcim/device-types/{sr650['id']}/").json()["part_number"] == "7X06"
    cat = {(i["manufacturer"], i["model"]): i for i in player.cl.get("/api/game/catalogue", params={"fresh": True}).json()["items"]}
    assert cat[("Lenovo", "ThinkSystem SR650")]["status"] == "verified" and cat[("Juniper", "SRX300")]["status"] == "verified"
    assert "SRX300-SYS-JE" in cat[("Juniper", "SRX300")]["note"] and "neither is called wrong" in cat[("Juniper", "SRX300")]["note"]


def test_two_models_that_differ_by_a_plus_sign_are_two_models_live(nbx, yard, player):
    sid = yard["site"]("Plus Works")["id"]
    fresh(player)
    kit = f"q:site_no_devices:site:{sid}"
    box(player, kit, 0, "JL259A", "HPE Aruba 2930F-24G-4SFP (1U, 28 ports)", "Switch", {"name": "pw-sw1", "position": 40}, "yes")
    box(player, kit, 1, "JL253A", "HPE Aruba 2930F-24G-4SFP+ (1U, 29 ports)", "Switch", {"name": "pw-sw2", "position": 39}, "yes")
    # a model the library files under the maker "Generic" is a model, not a placeholder
    box(player, kit, 2, "24-port copper", "Generic 24-port Copper Patch Panel", "Patch panel", {"name": "pw-pp1", "position": 42}, None)
    pre = player.cl.post("/api/game/quests/preview", json={"scope": "netbox", "quest_id": kit}).json()
    assert [(o["op"], o["status"]) for o in pre["ops"]] == [("add_device_type", "ok"), ("create_device", "ok")] * 3, pre["ops"]
    v = player.apply(kit)
    assert v["complete"] and v["bonus_xp"] == 60 and "3 boxes of a model the device-type library confirms: +60 XP." in v["message"], v["message"]
    devs = {d["name"]: d for d in rows(nbx, "dcim/devices/", site_id=sid)}
    assert devs["pw-sw1"]["device_type"]["model"] == "Aruba 2930F-24G-4SFP" and devs["pw-sw2"]["device_type"]["model"] == "Aruba 2930F-24G-4SFP+"
    parts = {t["model"]: t["part_number"] for t in rows(nbx, "dcim/device-types/", manufacturer="hpe")}
    assert parts == {"Aruba 2930F-24G-4SFP": "JL259A", "Aruba 2930F-24G-4SFP+": "JL253A"}
    assert nbx.get("dcim/interfaces/", params={"device_id": devs["pw-sw2"]["id"]}).json()["count"] == 29
    b = fresh(player)
    assert f"q:device_type_unknown:device:{devs['pw-pp1']['id']}" not in [x["id"] for x in b["quests"]]
    cat = {(i["manufacturer"], i["model"]): i for i in player.cl.get("/api/game/catalogue", params={"fresh": True}).json()["items"]}
    assert (cat[("Generic", "24-port Copper Patch Panel")]["status"], cat[("Generic", "24-port Copper Patch Panel")]["note"]) == ("verified", None)
    # ---- the site is still being set up, so the next boxes are offered: a fourth box, and another model from the library
    more = next(x for x in b["quests"] if x["kind"] == "site_more_devices" and x["site_id"] == sid)
    assert more["title"] == "More boxes at Plus Works" and b["sites"] and more["status"] == "offered"
    player.say(more["id"], ("more", {"choice": "yes"}))
    box(player, more["id"], 0, "EX3400-24P", "Juniper EX3400-24P", "Switch", {"name": "pw-sw3", "position": 30}, "no")
    v = player.apply(more["id"])
    assert v["complete"] and v["xp_awarded"] > 0 and v["bonus_xp"] == 20, v["message"]
    assert nbx.get("dcim/interfaces/", params={"device_id": rows(nbx, "dcim/devices/", name="pw-sw3")[0]["id"]}).json()["count"] == 35
    again = [x for x in fresh(player)["quests"] if x["kind"] == "site_more_devices" and x["site_id"] == sid and x["status"] == "offered"]
    assert len(again) == 1 and again[0]["id"] != more["id"]            # offered again for the site as it stands now
    q = player.say(again[0]["id"], ("more", {"choice": "none"}))
    assert q["status"] == "dismissed"                                    # "NetBox holds every box here": closed, nothing written


def test_a_serial_is_called_wrong_only_by_a_rule_its_source_covers_and_the_mark_is_in_netbox_live(nbx, yard, player):
    make = yard["make"]
    sid = yard["site"]("Serial Shed")["id"]
    fresh(player)
    kit = f"q:site_no_devices:site:{sid}"
    # a Meraki serial under the maker Cisco is no Cisco serial, and no rule speaks of it: taken as typed, nothing asked
    q = box(player, kit, 0, "MR42", "Cisco Meraki MR42", "Access point", {"name": "ss-ap1", "serial": "Q2KD-ABCD-EFGH"}, None)
    assert q["step"]["id"] == "more0", q["step"]
    player.say(kit, ("more0", {"choice": "yes"}))
    # a FortiGate serial that does not start with FG: asked about once, and on "yes" recorded and marked
    q = box(player, kit, 1, "FG-60F", "Fortinet FortiGate 60F", "Firewall", {"name": "ss-fw1", "serial": "X60FTK2209ABCD"}, None)
    assert q["step"]["id"].startswith("box1_sure_") and "does not look like a Fortinet serial: a serial that starts with FG" in q["step"]["prompt"]
    q = player.say(kit, (q["step"]["id"], {"choice": "yes"}), ("more1", {"choice": "no"}))
    assert any("ss-fw1" in c and "marked unverified" in c for c in q["changes"])
    v = player.apply(kit)
    assert v["complete"] and v["bonus_xp"] == 20, v["message"]            # the access point's model; nothing for the marked box
    assert "No bonus for a box marked unverified." in v["message"]
    devs = {d["name"]: d for d in rows(nbx, "dcim/devices/", site_id=sid)}
    assert [t["slug"] for t in devs["ss-fw1"]["tags"]] == ["rw-unverified"] and devs["ss-fw1"]["serial"] == "X60FTK2209ABCD"
    assert devs["ss-ap1"]["tags"] == [] and devs["ss-ap1"]["serial"] == "Q2KD-ABCD-EFGH"
    # ---- the same mark through a rack audit: the device's own tags stay, the mark is added, and no serial bonus is paid
    keep = make("extras/tags/", {"name": "Annex keep", "slug": "annex-keep"})
    cisco = rows(nbx, "dcim/manufacturers/", slug="cisco")[0]
    c1111 = rows(nbx, "dcim/device-types/", model="ISR C1111-4P") or [None]
    if c1111[0] is None:
        out = change(player, "apply", {"op": "add_device_type", "target": {"kind": "device_type", "id": None},
                                        "fields": {"library_file": "Cisco/ISR-C1111-4P.yaml"}})
        assert out["ops"][0]["status"] == "ok", out
        c1111 = rows(nbx, "dcim/device-types/", model="ISR C1111-4P")
    assert c1111[0]["manufacturer"]["id"] == cisco["id"]
    role = rows(nbx, "dcim/device-roles/", slug="firewall")[0]
    rack = rows(nbx, "dcim/racks/", site_id=sid)[0]
    rtr = make("dcim/devices/", {"name": "ss-rtr1", "site": sid, "rack": rack["id"], "position": 5, "face": "front", "role": role["id"],
                                 "device_type": c1111[0]["id"], "status": "active", "tags": [keep["id"]]})
    fresh(player)
    audit = {"id": f"q:rack_audit:rack:{rack['id']}"}
    field = f"device:{rtr['id']}:serial"
    r = player.cl.post("/api/game/quests/answer", json={"scope": "netbox", "quest_id": audit["id"], "step": "labels", "values": {field: "54321"}}).json()
    assert r["step"]["id"].startswith(f"serial{rtr['id']}_sure_"), r["step"]
    q = player.say(audit["id"], (r["step"]["id"], {"choice": "yes"}))
    assert [o["op"] for o in q["proposal"]["ops"]] == ["update_fields", "mark_unverified"]
    v = player.apply(audit["id"])
    assert v["bonus_xp"] == 0 and "No bonus for a serial marked unverified." in v["message"], v["message"]
    now = nbx.get(f"dcim/devices/{rtr['id']}/").json()
    assert now["serial"] == "54321" and sorted(t["slug"] for t in now["tags"]) == ["annex-keep", "rw-unverified"]


def test_a_country_netbox_holds_under_its_own_slug_is_used_live(nbx, yard, player):
    make = yard["make"]
    held = rows(nbx, "dcim/regions/", name="Germany") or [make("dcim/regions/", {"name": "Germany", "slug": "germany"})]
    n = len(every(nbx, "dcim/regions/"))
    op = {"op": "create_site", "target": {"kind": "site", "id": None}, "fields": {"name": "Hafen Eins", "country": "DE"}}
    assert change(player, "preview", op)["ops"][0]["status"] == "ok"
    out = change(player, "apply", op)
    assert out["applied"] and out["ops"][0]["status"] == "ok", out
    site = rows(nbx, "dcim/sites/", slug="hafen-eins")[0]
    assert site["region"]["id"] == held[0]["id"] and len(every(nbx, "dcim/regions/")) == n


def test_a_manufacturer_under_its_longer_name_is_the_librarys_manufacturer_live(nbx, yard, player):
    make = yard["make"]
    long_juniper = make("dcim/manufacturers/", {"name": "Juniper Networks", "slug": "juniper-networks"})
    shell = make("dcim/device-types/", {"manufacturer": long_juniper["id"], "model": "EX4300-48P", "slug": "ex4300-48p", "u_height": 1})
    arista = make("dcim/manufacturers/", {"name": "Arista Networks", "slug": "arista"})
    whole = make("dcim/device-types/", {"manufacturer": arista["id"], "model": "DCS-7050SX3-48YC8-F", "slug": "arista-dcs-7050sx3-48yc8-f", "u_height": 1})
    make("dcim/interface-templates/", {"device_type": whole["id"], "name": "Ethernet1", "type": "25gbase-x-sfp28"})
    makers, types = len(every(nbx, "dcim/manufacturers/")), len(every(nbx, "dcim/device-types/"))
    index = packs.load().index()
    ops = [{"op": "add_device_type", "target": {"kind": "device_type", "id": None}, "client_ref": f"t{n}",
            "fields": {"library_file": index.lookup(m, model)["file"]}} for n, (m, model) in enumerate((("Juniper", "EX4300-48P"), ("Arista", "DCS-7050SX3-48YC8-F")))]
    pre = change(player, "preview", *ops)
    # the shell under "Juniper Networks" is filled with the library's ports; the Arista type is whole and is left alone
    assert [o["status"] for o in pre["ops"]] == ["ok", "no_change"], pre["ops"]
    assert (pre["ops"][0]["target"]["id"], pre["ops"][0]["after"]["interfaces"]) == (shell["id"], 53)
    out = change(player, "apply", *ops)
    assert out["applied"] and [o["status"] for o in out["ops"]] == ["ok", "no_change"], out
    assert (len(every(nbx, "dcim/manufacturers/")), len(every(nbx, "dcim/device-types/"))) == (makers, types)
    assert nbx.get("dcim/interface-templates/", params={"device_type_id": shell["id"]}).json()["count"] == 53
    assert nbx.get("dcim/interface-templates/", params={"device_type_id": whole["id"]}).json()["count"] == 1
    assert change(player, "preview", ops[0])["ops"][0]["status"] == "no_change"            # filled once
    cat = {(i["manufacturer"], i["model"]): i for i in player.cl.get("/api/game/catalogue", params={"fresh": True}).json()["items"]}
    assert cat[("Juniper Networks", "EX4300-48P")]["status"] == "verified" and cat[("Arista Networks", "DCS-7050SX3-48YC8-F")]["status"] == "verified"
    assert cat[("Juniper Networks", "EX4300-48P")]["note"] == "The device-type library files this maker as Juniper."


def test_a_provider_in_another_script_is_itself_and_an_as_number_netbox_holds_is_used_live(nbx, yard, player):
    make = yard["make"]
    make("circuits/providers/", {"name": "Ростелеком", "slug": "rostelecom"})
    new = {"op": "create_provider", "target": {"kind": "provider", "id": None}, "fields": {"name": "МТС"}}
    out = change(player, "apply", new)
    assert out["applied"] and out["ops"][0]["status"] == "ok" and out["ops"][0]["after"]["name"] == "МТС", out
    assert sorted(p["name"] for p in rows(nbx, "circuits/providers/") if p["name"] in ("МТС", "Ростелеком")) == ["МТС", "Ростелеком"]
    assert change(player, "preview", new)["ops"][0]["status"] == "no_change"
    # an AS number that is in IPAM and on no provider: the ASN object NetBox holds is used
    rir = (rows(nbx, "ipam/rirs/", slug="ripe-ncc") or rows(nbx, "ipam/rirs/", slug="ripe") or [make("ipam/rirs/", {"name": "RIPE NCC", "slug": "ripe-ncc"})])[0]
    asn = make("ipam/asns/", {"asn": 64512, "rir": rir["id"]})
    out = change(player, "apply", {"op": "create_provider", "target": {"kind": "provider", "id": None},
                                   "fields": {"name": "Probe Carrier", "asns": [{"asn": 64512, "rir": "ripe"}]}})
    assert out["applied"] and out["ops"][0]["status"] == "ok", out
    probe = rows(nbx, "circuits/providers/", slug="probe-carrier")[0]
    assert [a["id"] for a in probe["asns"]] == [asn["id"]] and len(rows(nbx, "ipam/asns/", asn=64512)) == 1


def test_guards_a_change_set_reaches_with_no_quest_in_front_live(nbx, yard, player):
    """A ChangeSet sent straight to the proxy: a part number is written in the library's spelling, a serial of a device
    that has left service may be recorded again, and a module whose parts the device already has is refused before
    anything is written."""
    make = yard["make"]
    sid = yard["site"]("Guard Post")["id"]
    index = packs.load().index()
    out = change(player, "apply", {"op": "add_device_type", "target": {"kind": "device_type", "id": None}, "client_ref": "t",
                                   "fields": {"library_file": index.lookup("Juniper", "EX3400-24P")["file"]}},
                 {"op": "create_device", "target": {"kind": "device", "id": None}, "client_ref": "d",
                  "fields": {"name": "gp-sw1", "site_id": sid, "role": "Switch", "device_type_id": "@t", "serial": "GP-OLD-1"}})
    assert out["applied"], out
    sw = rows(nbx, "dcim/devices/", name="gp-sw1")[0]
    tid = sw["device_type"]["id"]
    # ---- part number: only the library's, in its spelling
    assert nbx.patch(f"dcim/device-types/{tid}/", json={"part_number": "EX2300-24P"}).status_code == 200
    out = change(player, "apply", {"op": "set_part_number", "target": {"kind": "device_type", "id": tid}, "fields": {"part_number": "ex3400 24p"}})
    assert out["applied"] and nbx.get(f"dcim/device-types/{tid}/").json()["part_number"] == "EX3400-24P"
    # ---- a serial: one live device, but a record that has left service does not hold it for ever
    twin = {"op": "create_device", "target": {"kind": "device", "id": None}, "fields": {"name": "gp-sw2", "site_id": sid, "role": "Switch", "device_type_id": tid,
                                                                                           "serial": "gp-old-1"}}
    pre = change(player, "preview", twin)
    assert pre["ops"][0]["status"] == "error" and "a serial number belongs to one device" in pre["ops"][0]["error"]["message"]
    assert nbx.patch(f"dcim/devices/{sw['id']}/", json={"status": "decommissioning"}).status_code == 200
    assert change(player, "preview", twin)["ops"][0]["status"] == "ok"
    # ---- a module that brings a power port the device already has under that name
    bay = rows(nbx, "dcim/module-bays/", device_id=sw["id"])[0]
    psu = index.lookup("Juniper", "JPSU-150-AC-AFO", "module")
    doc_port = "Power Supply 0"
    make("dcim/power-ports/", {"device": sw["id"], "name": doc_port, "type": "iec-60320-c14"})
    out = change(player, "apply", {"op": "add_module_type", "target": {"kind": "module_type", "id": None}, "client_ref": "m", "fields": {"library_file": psu["file"]}})
    assert out["applied"], out
    mtid = rows(nbx, "dcim/module-types/", model="JPSU-150-AC-AFO")[0]["id"]
    tpl = rows(nbx, "dcim/power-port-templates/", module_type_id=mtid)
    fit = {"op": "install_module", "target": {"kind": "module", "id": None}, "fields": {"device_id": sw["id"], "module_bay_id": bay["id"], "module_type_id": mtid}}
    pre = change(player, "preview", fit)
    if tpl and "{module" not in tpl[0]["name"] and tpl[0]["name"] != doc_port:
        make("dcim/power-ports/", {"device": sw["id"], "name": tpl[0]["name"], "type": "iec-60320-c14"})
        pre = change(player, "preview", fit)
    if tpl and "{module" not in tpl[0]["name"]:
        assert pre["ops"][0]["status"] == "error" and pre["ops"][0]["error"]["code"] == "conflict", pre
        assert f"already has a power port {tpl[0]['name']}" in pre["ops"][0]["error"]["message"]
        assert nbx.get("dcim/modules/", params={"device_id": sw["id"]}).json()["count"] == 0
    else:
        assert pre["ops"][0]["status"] == "ok"            # its ports are named after the bay: nothing to collide with
    assert _board(player.cl, sid)["sites"]
