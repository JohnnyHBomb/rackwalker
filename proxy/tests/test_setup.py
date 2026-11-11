"""The setup companion, offline: the packs and their validator, provider matching, the device index, part numbers and
serial rules, what may happen to a device's model, the catalogue, and the chain that takes an empty NetBox to a site
with a rack, boxes and a carrier, against a made-up NetBox that starts empty and takes writes.

Nothing here reaches the internet: imports read the few library files in tests/library (RACKWALKER_LIBRARY_DIR), and
fetching is forbidden (RACKWALKER_LIBRARY_FETCH=0)."""
from __future__ import annotations

import asyncio
import gzip
import hashlib
import json
import shutil
import sqlite3
import time
from collections import defaultdict
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

from rackwalker_proxy import changes, chains, findings, packs, perms, questroutes, quests, setup, setupops
from rackwalker_proxy import contracts_gen as c
from rackwalker_proxy import netbox as nb
from rackwalker_proxy.app import Settings, create_app
from rackwalker_proxy.changes import Rec, State, plan
from rackwalker_proxy.estate import Estate
from rackwalker_proxy.gamestore import MIGRATIONS, SCHEMA_VERSION, GameStore, migrate

from .conftest import LOCAL, REPO, schema_validator
from .empty_netbox import EmptyBox
from .test_quest_settlement import board, post, sign_in
from .test_quests import Build

LIBRARY = Path(__file__).parent / "library"
EX2300 = "Juniper/EX2300-24P.yaml"
MX204 = "Juniper/MX204.yaml"
C1111 = "Cisco/ISR-C1111-4P.yaml"
PSU = "Juniper/JPSU-650W-AC-AFO.yaml"
PANEL = "Panduit/DP24688TGY.yaml"


@pytest.fixture(autouse=True)
def offline_library(monkeypatch):
    monkeypatch.setenv("RACKWALKER_LIBRARY_DIR", str(LIBRARY))
    monkeypatch.setenv("RACKWALKER_LIBRARY_FETCH", "0")
    packs._DEFINITIONS.clear()          # a definition read by one test is not what the next one finds


# ---------------------------------------------------------------- the packs and their validator
def test_the_shipped_packs_are_valid_and_every_one_loads():
    assert packs.validate() == []
    p = packs.load()
    assert p.errors == [] and p.countries() == ["DE", "FR", "GB", "IE", "NL", "US"]
    for pack in p.providers:
        # every pack says where it came from, when it was read, what it is complete for and what it lacks
        assert pack["sources"] and pack["read"] and len(pack["coverage"]) > 20 and (pack["missing"] or not pack["partial"])
        assert all(s["terms"] and s["use"] in ("copied", "cross-check", "cited") for s in pack["sources"])
        kinds = {x["kind"] for x in pack["providers"]}
        if pack["country"]:
            assert len(pack["providers"]) >= 12 and {"national_carrier", "wholesale"} <= kinds, pack["id"]
    assert p.provider_pack("US")["partial"] and p.provider_pack("IE")["partial"] and not p.provider_pack("GB")["partial"]
    index = p.index()
    pack = p.device_pack()
    assert len(index.rows["device"]) == pack["index"]["device_types"] > 10_000 and len(index.rows["module"]) == pack["index"]["module_types"] > 1_000
    assert len(pack["library"]["commit"]) == 40 and pack["library"]["raw_base"].endswith(pack["library"]["commit"] + "/")
    assert len(p.serials[0]["vendors"]) == 9


def test_a_provider_carries_its_own_wikidata_item_or_none_and_a_shared_as_number_says_so():
    p = packs.load()
    every = p.all_providers()
    assert len(every) >= 160 and all(x.get("wikidata", "Q1").startswith("Q") for x in every)
    assert p.provider("providers-us", "ziply-fiber")["asns"] == [{"asn": 20055, "rir": "arin"}]
    assert p.provider("providers-gb", "jisc-janet")["kind"] == "research_education" and "research_education" in packs.KIND_WORDS
    # one network under two trading names in two packs: the second entry says which provider it resolves to in NetBox
    by_asn: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for x in every:
        for a in x["asns"]:
            by_asn[a["asn"]].append(x)
    shared = {asn: xs for asn, xs in by_asn.items() if len({packs.norm(x["name"]) for x in xs}) > 1}
    assert {5607, 3320, 6830} <= set(shared)
    for asn, xs in shared.items():
        assert any(f"AS{asn}" in (x["notes"] or "") or "resolves to it" in (x["notes"] or "") for x in xs), (asn, [x["name"] for x in xs])
    sky = p.provider("providers-ie", "sky-ie")
    assert packs.match_provider(sky, [{"id": 7, "name": "Sky", "slug": "sky", "asns": [5607]}])["id"] == 7


def test_a_name_that_is_only_a_number_is_a_model_and_a_name_that_says_nobody_knew_is_not():
    for real in ("1721", "300", "3916-900", "EX2300-24P", "Catalyst 9300", "Temperature Probe 2"):
        assert not packs.is_placeholder(real), real
    for nobody in ("", None, "?", "???", "Unknown", "unknown device", "TBD", "Generic box", "temp", "Temporary", "mystery box", "Other"):
        assert packs.is_placeholder(nobody), nobody


def test_the_library_files_the_tests_use_are_the_pinned_ones():
    index = packs.load().index()
    for kind, file in (("device", EX2300), ("device", MX204), ("device", C1111), ("device", PANEL), ("module", PSU)):
        row = index.by_file(kind, file)
        raw = (LIBRARY / packs.FOLDER[kind] / file).read_bytes()
        assert row is not None and hashlib.sha256(raw).hexdigest() == row["sha256"], file


def broken(tmp_path: Path, change) -> list[str]:
    root = tmp_path / "packs"
    if root.exists():
        shutil.rmtree(root)
    shutil.copytree(REPO / "packs", root)
    change(root)
    return packs.validate(root)


def edit(root: Path, rel: str, fn) -> None:
    doc = json.loads((root / rel).read_text())
    fn(doc)
    (root / rel).write_text(json.dumps(doc))


def test_the_validator_says_what_is_wrong_with_a_pack(tmp_path):
    gb = "providers/gb.json"
    cases = {
        "slug bt is used twice": lambda r: edit(r, gb, lambda d: d["providers"].append({**d["providers"][0], "name": "BT Two"})),
        "one AS number belongs to one provider": lambda r: edit(r, gb, lambda d: d["providers"][3].update(asns=[{"asn": 2856, "rir": "ripe"}])),
        "a phone number does not belong in a pack": lambda r: edit(r, gb, lambda d: d["providers"][0].update(notes="Call 0800 800 150 for faults")),
        "a mail address does not belong in a pack": lambda r: edit(r, gb, lambda d: d["providers"][0].update(notes="noc@example.net")),
        "a partial pack says what it lacks": lambda r: edit(r, "providers/us.json", lambda d: d.update(missing=[])),
        "is the file gg.json with the id providers-gg": lambda r: edit(r, gb, lambda d: d.update(country="GG", id="providers-gg")),
        "'sources' is a required property": lambda r: edit(r, gb, lambda d: d.pop("sources")),
        "no source is marked as the one the facts were taken from": lambda r: edit(r, gb, lambda d: [s.update(use="cited") for s in d["sources"]]),
        "id providers-nl is already used": lambda r: (edit(r, "providers/nl.json", lambda d: None), shutil.copy(r / "providers/nl.json", r / "providers/xx.json")),
        "is not the file the pack names": lambda r: (r / "devices/community-library.index.json.gz").write_bytes(gzip.compress(b"{}")),
        "raw_base does not point at the pinned commit": lambda r: edit(r, "devices/community-library.json", lambda d: d["library"].update(commit="0" * 40)),
        "is not a regular expression": lambda r: edit(r, "serials/network-vendors.json", lambda d: d["vendors"][0]["rules"][0].update(pattern="[A-Z")),
        "a vendor with no rule says why": lambda r: edit(r, "serials/network-vendors.json", lambda d: d["vendors"][-1].update(no_rule_reason=None)),
        "'quote' is a required property": lambda r: edit(r, "serials/network-vendors.json", lambda d: d["vendors"][0]["rules"][0]["source"].pop("quote")),
    }
    for want, change in cases.items():
        errs = broken(tmp_path, change)
        assert any(want in e for e in errs), (want, errs)
    assert broken(tmp_path, lambda r: None) == []


def test_a_new_country_is_one_data_file(tmp_path):
    def add(root: Path) -> None:
        doc = json.loads((root / "providers/ie.json").read_text())
        doc.update(country="BE", id="providers-be", title="Common providers: Belgium", providers=[
            {"name": "Example Net", "slug": "example-net", "kind": "national_carrier", "aka": ["ExNet"], "asns": [{"asn": 64500, "rir": "ripe"}],
             "website": None, "support_url": None, "notes": None}])
        (root / "providers/be.json").write_text(json.dumps(doc))
    assert broken(tmp_path, add) == []
    p = packs._load(str(tmp_path / "packs"))
    assert "BE" in p.countries() and [x["name"] for x in p.providers_of("BE")] == ["Example Net"]


def test_packs_that_do_not_validate_are_not_half_used(tmp_path):
    assert broken(tmp_path, lambda r: edit(r, "providers/gb.json", lambda d: d.pop("coverage")))
    p = packs._load(str(tmp_path / "packs"))
    assert p.errors and p.providers == [] and p.devices == []


# ---------------------------------------------------------------- providers: found by any of their names, never made twice
def test_a_provider_is_found_by_the_names_on_the_bill_and_by_its_as_number():
    p = packs.load()
    assert p.search_providers("british telecom", "GB")[0]["name"] == "BT"
    assert p.search_providers("AS1136")[0]["name"] == "KPN" and p.search_providers("3320")[0]["slug"] in ("deutsche-telekom", "dt-global-carrier")
    assert p.search_providers("vodafone", "IE")[0]["name"] == "Vodafone Ireland"      # the site's own country first
    names = [x["name"] for x in p.search_providers("colt", "NL")]
    assert names == ["Colt Technology Services"]                                    # listed in several packs, offered once
    assert p.search_providers("no such carrier at all") == [] and p.search_providers("x") == []


def test_a_pack_entry_matches_the_provider_netbox_already_holds():
    kpn = packs.load().provider("providers-nl", "kpn")
    assert packs.match_provider(kpn, [{"id": 1, "name": "kpn b.v.", "slug": "kpn-bv", "asns": []}])["id"] == 1           # the same name
    assert packs.match_provider(kpn, [{"id": 2, "name": "Royal Dutch Telecom", "slug": "rdt", "asns": [1136]}])["id"] == 2   # the same AS number
    assert packs.match_provider(kpn, [{"id": 3, "name": "Example Carrier", "slug": "example", "asns": [64500]}]) is None
    bt = packs.load().provider("providers-gb", "bt")
    assert packs.match_provider(bt, [{"id": 4, "name": "British Telecom", "slug": "british-telecom", "asns": []}])["id"] == 4   # a name it goes by


def planned(st: State, *raw: dict[str, Any]) -> list[dict[str, Any]]:
    cs = c.ChangeSet.model_validate({"ops": list(raw)})
    return [p.result().model_dump(mode="json") for p in plan(cs.ops, st)]


def T(kind: str, oid: Any = None) -> dict[str, Any]:
    return {"kind": kind, "id": oid}


def test_a_provider_netbox_holds_is_used_not_added_again_and_a_circuit_can_name_the_new_one():
    st = State()
    st.add(Rec("provider", 7, None, {"name": "Royal KPN", "slug": "royal-kpn", "asns": [1136]}))
    st.add(Rec("circuit_type", 3, None, {"name": "Internet", "slug": "internet"}))
    kpn = {"op": "create_provider", "target": T("provider"), "client_ref": "provider",
           "fields": {"name": "KPN", "slug": "kpn", "asns": [{"asn": 1136, "rir": "ripe"}], "pack": "providers-nl"}}
    circuit = {"op": "create_circuit", "target": T("circuit"), "client_ref": "circuit", "fields": {"cid": "K-1", "provider_id": "@provider", "type_id": 3}}
    out = planned(st, kpn, circuit)
    assert out[0]["status"] == "no_change" and out[0]["target"]["id"] == 7 and out[1]["status"] == "ok"
    fresh = planned(State({("circuit_type", 3): st.recs[("circuit_type", 3)]}), kpn, circuit,
                    {"op": "create_provider", "target": T("provider"), "fields": {"name": "kpn"}})
    assert [o["status"] for o in fresh] == ["ok", "ok", "no_change"]           # the second "kpn" is the one just planned
    assert fresh[0]["after"] == {"name": "KPN", "asns": [1136], "pack": "providers-nl"}


# ---------------------------------------------------------------- the device index, part numbers, serials
def test_what_is_printed_on_the_box_finds_the_model():
    index = packs.load().index()
    assert index.label(index.search("EX2300-24P")[0]) == "Juniper EX2300-24P (1U, 32 ports)"
    assert index.search("ex 2300 24p")[0]["model"] == "EX2300-24P" and index.search("juniper mx204")[0]["model"] == "MX204"
    assert index.search("C1111-4P")[0]["file"] == C1111                              # by part number
    assert index.search("JPSU-650W-AC-AFO", kind="module")[0]["file"] == PSU and index.search("JPSU-650W-AC-AFO") == []
    assert index.search("zz no such model 9999") == [] and index.search("e") == []
    assert len(index.search("catalyst", limit=5)) == 5
    row = index.by_file("device", MX204)
    assert (row["u_height"], row["interfaces"], row["module_bays"], row["part_number"]) == (1, 13, 5, "MX204")


def test_a_part_number_is_checked_against_the_library():
    index = packs.load().index()
    assert index.part_check("Juniper", "EX2300-24P", "ex2300-24p") == {"status": "verified", "expected": "EX2300-24P", "other": None}
    # known-bad: the number is the library's for another model of that maker
    assert index.part_check("juniper", "EX2300-24P", "EX2300-48P") == {"status": "mismatch", "expected": "EX2300-24P", "other": "EX2300-48P"}
    assert index.part_check("Juniper", "EX2300-24P", "")["status"] == "known"
    assert index.part_check("Acme", "Rocket 9", "R9")["status"] == "unknown"
    # recorded by hand: a known mismatch is refused with what was expected; a model the library holds is not typed in
    by_hand = {"op": "add_device_type", "target": T("device_type"), "fields": {"manufacturer": "Juniper", "model": "EX2300-24P", "part_number": "EX2300-48P"}}
    out = planned(State(), by_hand)[0]
    assert out["status"] == "error" and out["error"]["details"] == {"field": "part_number", "expected": "EX2300-24P", "other": "EX2300-48P"}
    assert "knows EX2300-48P as the part number of the Juniper EX2300-48P, not of the EX2300-24P" in out["error"]["message"]
    for fields in ({"manufacturer": "Juniper", "model": "EX2300-24P"}, {"manufacturer": "Juniper Networks", "model": "ex2300 24p", "part_number": "EX2300-24P-TAA"}):
        known = planned(State(), {**by_hand, "fields": fields})[0]
        assert known["status"] == "error" and "pick it from the search" in known["error"]["message"], fields
    # a made-up model name under a part number the library knows: it is that model, and is not recorded as another
    alias = planned(State(), {**by_hand, "fields": {"manufacturer": "Juniper", "model": "Core switch", "part_number": "ex2300-24p"}})[0]
    assert alias["status"] == "error" and "is the part number of the Juniper EX2300-24P in the device-type library" in alias["error"]["message"]
    # a model the library does not hold is accepted, and marked unverified
    unknown = planned(State(), {**by_hand, "fields": {"manufacturer": "Acme", "model": "Rocket 9", "part_number": "R9", "u_height": 2}})[0]
    assert unknown["status"] == "ok" and unknown["after"]["unverified"] is True


def test_a_device_type_of_netbox_with_a_contradicting_part_number_is_not_built_on():
    st = State()
    st.add(Rec("site", 1, None, {"name": "Mill", "slug": "mill"}))
    st.add(Rec("device_type", 5, None, {"manufacturer": "Juniper", "model": "EX2300-24P", "part_number": "EX2300-48P", "u_height": 1.0, "full_depth": False, "tags": [], "_names": None}))
    make = {"op": "create_device", "target": T("device"), "fields": {"name": "sw1", "site_id": 1, "role": "Switch", "device_type_id": 5}}
    out = planned(st, make)[0]
    assert out["status"] == "error" and out["error"]["details"] == {"field": "device_type_id", "expected": "EX2300-24P", "other": "EX2300-48P"}
    assert out["error"]["message"] == ("NetBox holds Juniper EX2300-24P with the part number EX2300-48P, which the device-type library lists for the EX2300-48P. "
                                       "One of the two is wrong: correct the device type in NetBox (Devices > Device Types) first.")
    # set_device_type stands on the same ground
    st.add(Rec("device", 10, None, {"site_id": 1, "location_id": None, "rack_id": None, "position": None, "face": None, "name": "box1", "status": "active",
                                    "serial": "", "asset_tag": None, "_u_height": 1.0, "_full_depth": True, "_type_id": 1, "_model": "Unknown"}))
    st.add(Rec("x:components", 10, None, {key: [] for key, *_ in setupops.COMPONENTS}))
    out = planned(st, {"op": "set_device_type", "target": T("device", 10), "fields": {"device_type_id": 5}})[0]
    assert out["status"] == "error" and out["error"]["details"]["other"] == "EX2300-48P"


# Real part numbers of real kit that are not the one string the library lists for the model: a machine type of a
# family ("7X06" for the library's "7X0x"), a regional or licence SKU, a bundle. None of them is a ground to refuse.
REAL_PARTS = [("Lenovo", "ThinkSystem SR650", "7X06", "verified"), ("Juniper", "SRX300", "SRX300-SYS-JE", "differs"),
              ("Cisco", "Catalyst 9300-48P", "C9300-48P-A", "differs"), ("Cisco", "SG350-28", "SG350-28-K9-EU", "differs"),
              ("HPE", "Aruba 2930F-48G-PoE+-4SFP+", "JL256A", "differs"), ("Datacom", None, "825.4524.07", "verified")]


@pytest.mark.parametrize("maker,model,part,status", REAL_PARTS)
def test_a_real_part_number_the_library_does_not_list_is_never_a_ground_to_refuse(maker, model, part, status):
    index = packs.load().index()
    model = model or next(r["model"] for r in index.rows["device"] if r["manufacturer"] == maker and r["part_number"] == "825.4524.xx")
    check = index.part_check(maker, model, part)
    assert check["status"] == status and check["other"] is None
    assert setup.verified_type({"manufacturer": maker, "model": model, "part_number": part, "tags": []}) == "verified"
    st = State()
    st.add(Rec("site", 1, None, {"name": "Label Yard", "slug": "label-yard"}))
    st.add(Rec("device_type", 5, None, {"manufacturer": maker, "model": model, "part_number": part, "u_height": 1.0, "full_depth": False, "tags": [], "_names": None}))
    st.add(Rec("device", 10, None, {"site_id": 1, "location_id": None, "rack_id": None, "position": None, "face": None, "name": "old", "status": "active",
                                    "serial": "", "asset_tag": None, "_u_height": 1.0, "_full_depth": True, "_type_id": 1, "_model": "Unknown"}))
    st.add(Rec("x:components", 10, None, {key: [] for key, *_ in setupops.COMPONENTS}))
    st.recs[("device_type", 5)].v["_names"] = {key: [] for key, *_ in setupops.COMPONENTS}
    out = planned(st, {"op": "create_device", "target": T("device"), "fields": {"name": "ly-srv1", "site_id": 1, "role": "Server", "device_type_id": 5}},
                  {"op": "set_device_type", "target": T("device", 10), "fields": {"device_type_id": 5}})
    assert [o["status"] for o in out] == ["ok", "ok"], out
    # and nothing asks the admin to "put it right": the number in NetBox may well be the true one
    b = Build()
    s = b.site()
    one = b.dev(s, "srv1", "server", b.room(s), None, manufacturer=maker, model=model, tags=[])
    b.e.dtypes[50] = {"id": 50, "manufacturer": maker, "model": model, "slug": "t", "part_number": part, "u_height": 1.0, "tags": []}
    b.e.devices[one]["type_id"] = 50
    b.e.touch()
    assert not [f for f in findings.analyse(b.e).findings if f["kind"] == "device_type_part_number"]


def test_a_part_number_that_stands_for_several_is_compared_against_and_never_offered_or_written():
    index = packs.load().index()
    sr650 = index.lookup("Lenovo", "ThinkSystem SR650")
    assert sr650["part_number"] == "7X0x" and packs.part_pattern("7X0x").fullmatch("7x06") and not packs.part_pattern("7X0x").fullmatch("7y06")
    for one in ("EX2300-24P", "AT-x930-28GTX", "JL253A", "N9K-C93180YC-FX", "MX204", ""):
        assert packs.part_pattern(one) is None, one
    for several in ("7X0x", "825.4524.xx", "AT-x930-28GPx", "DCS-7050CX3-32S#", "AV12176DN-NL / -28 / -08"):
        assert packs.part_pattern(several) is not None, several
    wild = [r for r in index.rows["device"] if r["_wild"] is not None]
    assert 40 < len(wild) < 150                      # a few dozen of sixteen thousand: the rule has not swallowed real numbers
    # no single number to hand back, for a model held with no part number or with any
    assert index.part_check("Lenovo", "ThinkSystem SR650", "")["expected"] is None and index.part_check("Lenovo", "ThinkSystem SR650", "7Y99")["expected"] is None
    assert index.by_part("Lenovo", "7X0x") is None    # and a pattern is nobody's part number
    # so the part-number line is not opened for it, and the op that writes a part number refuses
    b = Build()
    s = b.site()
    one = b.dev(s, "srv1", "server", b.room(s), None, manufacturer="Lenovo", model="ThinkSystem SR650", tags=[])
    for held in ("", "7X06", "7Y99"):
        b.e.dtypes[50] = {"id": 50, "manufacturer": "Lenovo", "model": "ThinkSystem SR650", "slug": "sr650", "part_number": held, "u_height": 2.0, "tags": []}
        b.e.devices[one]["type_id"] = 50
        b.e.touch()
        assert not [f for f in findings.analyse(b.e).findings if f["kind"] == "device_type_part_number"], held
    st = State()
    st.add(Rec("device_type", 5, None, {"manufacturer": "Lenovo", "model": "ThinkSystem SR650", "part_number": "7X06", "u_height": 2.0, "full_depth": True,
                                        "tags": [], "_names": None}))
    for typed in ("7X0x", "7X06"):
        out = planned(st, {"op": "set_part_number", "target": T("device_type", 5), "fields": {"part_number": typed}})[0]
        assert out["status"] == "error" and "lists no single part number for Lenovo ThinkSystem SR650" in out["error"]["message"], typed
    e = Build().e
    e.dtypes[5] = {"id": 5, "manufacturer": "Lenovo", "model": "ThinkSystem SR650", "slug": "sr650", "part_number": "7X06", "u_height": 2.0, "tags": []}
    res: dict[str, Any] = {"target": {"kind": "device_type", "id": 5}}
    with pytest.raises(Exception, match="does not list 7X0x"):
        setupops.simulate(e, {"op": "set_part_number", "target": {"kind": "device_type", "id": 5}}, {"part_number": "7X0x"}, res, {}, None)
    assert e.dtypes[5]["part_number"] == "7X06"


@pytest.mark.parametrize("maker,model,serial,status", [
    ("Cisco", "ISR C1111-4P", "FGL214512AB", "ok"), ("Cisco", "Catalyst 9300-48P", "FOC2145X0ABC", "ok"), ("Cisco", "x", "12345", "suspect"),
    ("Cisco", "x", "FGL219912AB", "suspect"),                    # week 99
    ("Dell", "PowerEdge R740", "ABC1234", "ok"), ("Dell EMC", "PowerEdge R640", "abc1234", "ok"), ("Dell", "PowerEdge R740", "ABC12345", "suspect"),
    ("Dell EMC", "S4048", "abc12345", "no_rule"),                # the vendor's page speaks of the Service Tag: servers and PCs, not switches or storage
    ("Fortinet", "FortiGate 60F", "FGT60FTK20001234", "ok"), ("Fortinet", "FortiGate 60F", "FS60FTK20001234", "suspect"),
    ("Fortinet", "FortiSwitch 124F", "FS1E24FTF21001234", "no_rule"), ("Fortinet", "FortiAP 231F", "anything", "no_rule"),      # the quoted page speaks of FortiGate
    ("Cisco", "AIR-AP2802I", "FCZ1128A0B1", "ok"), ("Cisco", "x", "FCZ1128A0B1CD", "suspect"),       # eleven or twelve characters, never thirteen
    ("APC", "SMT1500RMI2U", "AS1915123456", "ok"), ("APC", "Smart-UPS SRT 5000VA RM 230V", "AS1960123456", "suspect"),      # week 60
    ("APC", "AP7921", "anything", "no_rule"),                    # the vendor's page speaks of UPS units, not of power strips
    ("Juniper", "EX2300-24P", "anything at all", "no_rule"), ("HPE", "Aruba 2930F", "x", "no_rule"), ("Nobody", "n", "x", "no_rule")])
def test_a_serial_is_checked_only_against_a_rule_with_a_source(maker, model, serial, status):
    assert packs.load().serial_check(maker, model, serial)["status"] == status


def test_every_serial_rule_names_its_source_and_a_vendor_without_one_has_no_rule():
    for vendor in packs.load().serials[0]["vendors"]:
        for rule in vendor["rules"]:
            assert rule["source"]["url"].startswith("https://") and rule["source"]["quote"] and rule["source"]["read"]
        assert vendor["rules"] or vendor["no_rule_reason"]
    assert [v["vendor"] for v in packs.load().serials[0]["vendors"] if v["rules"]] == ["Cisco", "Dell", "Fortinet", "APC"]


def audit_estate() -> tuple[Estate, dict[str, Any]]:
    b = Build()
    s = b.site()
    room = b.room(s)
    rack = b.rack(s, room)
    one = b.dev(s, "rtr1", "router", room, rack, serial="", manufacturer="Cisco", model="ISR C1111-4P", tags=[])
    two = b.dev(s, "rtr2", "router", room, rack, serial="FGL214512AB", manufacturer="Cisco", model="ISR C1111-4P", tags=[], position=12.0)
    b.cable(b.p(one, "eth1"), b.p(two, "eth1"))
    line = next(x for x in quests.lines(b.e, findings.analyse(b.e)) if x["kind"] == "rack_audit")
    return b.e, line


def test_a_serial_that_is_another_devices_is_refused_and_one_that_looks_wrong_is_asked_about_once():
    e, line = audit_estate()
    one = next(d for d in e.devices.values() if d["name"] == "rtr1")
    field = f"device:{one['id']}:serial"
    # plainly impossible: the serial of another device
    state = quests.evaluate(e, line, {"labels": {"values": {field: "fgl214512ab"}}})
    assert state["status"] == "asking" and state["step"]["id"] == "labels"
    assert state["step"]["error"] == "fgl214512ab is the serial of rtr2: a serial number belongs to one device. Read the label of rtr1 again."
    # fits the vendor's sourced rule: written, nothing asked
    good = quests.evaluate(e, line, {"labels": {"values": {field: "FGL214600ZZ"}}})
    assert good["status"] == "ready" and [o["op"] for o in good["proposal"]["ops"]] == ["update_fields"]
    # does not look like a Cisco serial: asked, never refused
    answers = {"labels": {"values": {field: "12345"}}}
    state = quests.evaluate(e, line, answers)
    step = state["step"]
    assert state["status"] == "asking" and step["id"].startswith(f"serial{one['id']}_sure_") and step["prompt"].startswith(
        "Are you sure? 12345 does not look like a Cisco serial: three characters for the place it was made, two digits for the year")
    assert [(ch["label"], ch["kind"]) for ch in step["choices"]] == [("Yes: record it, marked unverified", "confirm"), ("Leave this serial out for now", "skip")]
    yes = quests.evaluate(e, line, {**answers, step["id"]: {"choice": "yes"}})
    assert [o["op"] for o in yes["proposal"]["ops"]] == ["update_fields", "mark_unverified"]
    assert yes["changes"][1] == "rtr1: marked unverified in NetBox (the serial does not look like the vendor's; the player confirmed the label)."
    skipped = quests.evaluate(e, line, {**answers, step["id"]: {"choice": "skip"}})
    assert skipped["status"] == "parked" and skipped["proposal"] is None        # nothing else was typed: nothing to write
    # a vendor with no sourced rule: the serial is taken as typed, and nothing is asked
    one.update(manufacturer="Juniper", model="EX2300-24P")
    e.touch()
    line = next(x for x in quests.lines(e, findings.analyse(e)) if x["kind"] == "rack_audit")
    assert quests.evaluate(e, line, answers)["status"] == "ready"


def test_only_what_passes_a_check_earns_the_bonus():
    e, line = audit_estate()
    one = next(d for d in e.devices.values() if d["name"] == "rtr1")
    cid = f"device:{one['id']}:serial"
    one["serial"] = "FGL214600ZZ"
    assert setup.bonus(e, line, [cid]) == (setup.SERIAL_BONUS, "1 serial fits the vendor's own pattern: +10 XP.")
    one.update(serial="12345", tags=[setup.UNVERIFIED])
    assert setup.bonus(e, line, [cid]) == (0, "No bonus for a serial marked unverified.")
    one.update(serial="FGL214600ZZ")                                        # a good serial on a device still tagged: no bonus
    assert setup.bonus(e, line, [cid])[0] == 0
    one.update(manufacturer="Juniper", tags=[])                             # no rule to pass: no bonus, and nothing said
    assert setup.bonus(e, line, [cid]) == (0, None)
    assert setup.bonus(e, line, []) == (0, None)


# ---------------------------------------------------------------- what may happen to a device's model
def type_state(have: dict[str, list[dict[str, Any]]], names: dict[str, list[str]], height: float = 1.0, **dev: Any) -> State:
    st = State()
    st.add(Rec("rack", 3, None, {"name": "R1", "site_id": 1, "location_id": None, "u_height": 12, "starting_unit": 1}))
    st.add(Rec("device", 10, None, {"site_id": 1, "location_id": None, "rack_id": 3, "position": 5.0, "face": "front", "name": "box1", "status": "active",
                                    "serial": "", "asset_tag": None, "_u_height": 1.0, "_full_depth": True, "_type_id": 1, "_model": "Unknown", **dev}))
    st.add(Rec("device", 11, None, {"site_id": 1, "location_id": None, "rack_id": 3, "position": 6.0, "face": "front", "name": "above", "status": "active",
                                    "serial": "", "asset_tag": None, "_u_height": 1.0, "_full_depth": True, "_type_id": 9, "_model": "M"}))
    st.add(Rec("x:components", 10, None, {key: have.get(key, []) for key, *_ in setupops.COMPONENTS}))
    st.add(Rec("device_type", 2, None, {"manufacturer": "Juniper", "model": "EX2300-24P", "part_number": "EX2300-24P", "u_height": height,
                                        "full_depth": False, "tags": [], "_names": {key: names.get(key, []) for key, *_ in setupops.COMPONENTS}}))
    return st


SET = {"op": "set_device_type", "target": T("device", 10), "fields": {"device_type_id": 2}}
PORT = {"name": "ge-0/0/0", "cable": True, "module": False}


def test_a_device_with_no_ports_takes_a_model_and_gets_its_ports():
    out = planned(type_state({}, {"interfaces": ["ge-0/0/0", "ge-0/0/1"], "console-ports": ["Console"]}), SET)[0]
    assert out["status"] == "ok" and out["after"] == {"device_type": "Juniper EX2300-24P", "console_ports_added": 1, "interfaces_added": 2}
    assert out["before"]["device_type"] == "Unknown"


def test_a_device_whose_ports_all_exist_in_the_model_keeps_them_and_gains_the_rest():
    st = type_state({"interfaces": [PORT]}, {"interfaces": ["ge-0/0/0", "ge-0/0/1"], "power-ports": ["PSU0"]})
    out = planned(st, SET)[0]
    # the cabled port is in the model under the same name: it stays as it is, and only what is missing is added
    assert out["status"] == "ok" and out["after"] == {"device_type": "Juniper EX2300-24P", "power_ports_added": 1, "interfaces_added": 1}


def test_a_device_with_a_port_the_model_does_not_have_is_refused_with_the_reason():
    have = {"interfaces": [PORT, {"name": "Gi0/49", "cable": True, "module": False}, {"name": "Gi0/50", "cable": False, "module": False}],
            "module-bays": [{"name": "Slot 1", "cable": False, "module": True}]}
    out = planned(type_state(have, {"interfaces": ["ge-0/0/0"]}), SET)[0]
    assert out["status"] == "error" and out["error"]["code"] == "conflict" and out["error"]["details"]["orphans"] == 3
    assert out["error"]["message"] == (
        "box1 has 3 components that Juniper EX2300-24P does not have: interface Gi0/49 (cabled), interface Gi0/50, module bay Slot 1 (a module is fitted). "
        "Changing the model would leave them recorded on a box that cannot have them, so nothing is changed. If the device really is that model, "
        "remove or rename them in NetBox first.")


def test_a_model_too_tall_for_where_the_device_sits_is_refused():
    out = planned(type_state({}, {}, height=2.0), SET)[0]
    assert out["status"] == "error" and out["error"]["message"] == "U6 of rack R1 is taken by above."
    assert planned(type_state({}, {}, height=2.0, position=11.0), SET)[0]["status"] == "ok"
    assert planned(type_state({}, {}, height=2.0, position=12.0), SET)[0]["error"]["message"] == "U12-U13 is outside rack R1 (U1-U12)."
    assert planned(type_state({}, {}, height=2.0, rack_id=None, position=None), SET)[0]["status"] == "ok"      # not racked: nothing to collide with


def test_a_model_whose_templates_could_not_be_read_is_never_judged_safe():
    st = type_state({}, {})
    st.recs[("device_type", 2)].v["_names"] = None
    assert planned(st, SET)[0]["error"]["code"] == "not_found"
    same = type_state({}, {}, _type_id=2)
    assert planned(same, SET)[0]["status"] == "no_change"


def test_a_module_goes_only_into_an_empty_bay_of_that_device():
    st = State()
    st.add(Rec("x:bay", 4, None, {"name": "PSU 0", "device_id": 10, "device": "rtr1", "module": None}))
    st.add(Rec("module_type", 6, None, {"manufacturer": "Juniper", "model": "JPSU-650W-AC-AFO", "part_number": ""}))
    op = {"op": "install_module", "target": T("module"), "fields": {"device_id": 10, "module_bay_id": 4, "module_type_id": 6, "serial": "PS1"}}
    out = planned(st, op, op, {**op, "fields": {**op["fields"], "device_id": 11}})
    assert [o["status"] for o in out] == ["ok", "error", "error"]
    assert out[1]["error"]["message"] == "Bay PSU 0 of rtr1 already holds a module." and "is not a bay of that device" in out[2]["error"]["message"]


# ---------------------------------------------------------------- against a made-up NetBox that starts empty (tests/empty_netbox.py)
def game(tmp_path, monkeypatch, box: EmptyBox, write_enabled: bool = True):
    app = create_app(Settings("http://netbox.invalid", "nbt_x.y", write_enabled, None, cache_ttl=0, state_dir=tmp_path, estate_ttl=60),
                     transport=httpx.MockTransport(box))
    monkeypatch.setattr(questroutes, "FRESH_SITE_S", 0.0)
    monkeypatch.setattr(questroutes, "FRESH_ALL_S", 0.0)
    cl = TestClient(base_url=LOCAL, app=app, raise_server_exceptions=False)
    cl.__enter__()
    sign_in(cl)
    return cl


def say(cl, quest_id: str, *answers: tuple[str, dict[str, Any]]) -> dict[str, Any]:
    q: dict[str, Any] = {}
    for step, a in answers:
        q = post(cl, "answer", quest_id, step=step, **a)
        assert q["step"] is None or q["step"]["id"] != step, (step, q["step"])
    return q


def apply(cl, quest_id: str) -> dict[str, Any]:
    r = cl.post("/api/game/quests/preview", json={"scope": "netbox", "quest_id": quest_id})
    assert r.status_code == 200, r.text
    schema_validator("ChangeResult").validate(r.json())
    assert all(o["status"] in ("ok", "no_change") for o in r.json()["ops"]), r.json()["ops"]
    return post(cl, "apply", quest_id)


FIRST_SITE, RACK, KIT, CARRIER = "q:estate_no_site:estate:0", "q:site_no_rack:site:{}", "q:site_no_devices:site:{}", "q:site_no_provider:site:{}"


def test_an_empty_netbox_is_set_up_by_playing_from_the_first_site_to_the_way_out(tmp_path, monkeypatch):
    """The onboarding chain, end to end: country and site, a first rack, two boxes found in the device-type library by
    what is printed on them, and the carrier from the provider pack of the site's country. Every step is one previewed
    change, and the next one is offered only when NetBox, read again, shows the last."""
    box = EmptyBox()
    cl = game(tmp_path, monkeypatch, box)
    b = board(cl)
    assert b["message"].startswith("NetBox has no site yet.") and b["sites"] == [] and b["next_quest_id"] == FIRST_SITE
    q = b["quests"][0]
    assert (q["id"], q["site_id"], q["title"], q["target"], q["status"]) == (FIRST_SITE, 0, "Your first site", None, "offered")
    assert [ch["label"] for ch in q["step"]["choices"]] == ["Germany", "France", "United Kingdom", "Ireland", "Netherlands", "United States",
                                                            "Another country", "I don't know yet"]
    # ---- the site: nothing is written before the preview is applied
    q = say(cl, FIRST_SITE, ("country", {"choice": "country:NL"}), ("site_name", {"text": "Mill Lane"}))
    assert q["status"] == "ready" and q["changes"] == ["New site Mill Lane in Netherlands."] and box.writes == []
    v = apply(cl, FIRST_SITE)
    assert v["mode"] == "real" and v["verified"] and v["complete"] and v["xp_awarded"] == 60 and v["unlocked"] == []
    site = box.data["dcim/sites"][0]
    assert (site["name"], site["slug"], site["region"]["slug"]) == ("Mill Lane", "mill-lane", "nl") and box.data["dcim/regions"][0]["name"] == "Netherlands"
    sid = site["id"]
    # ---- the chain "Setting up Mill Lane": the rack first; the boxes and the carrier wait for it
    b = board(cl)
    chain = next(ch for ch in b["chains"] if ch["kind"] == "setup")
    assert (chain["title"], chain["label"], chain["bonus_xp"]) == ("Setting up Mill Lane", "1 of 3: room", 0)
    assert chain["holds"] == "the site's first rack; its first devices, each with the ports of its model; the carrier its line belongs to"
    assert [s["status"] for s in chain["stages"]] == ["open", "locked", "locked"]
    assert [x["kind"] for x in b["quests"] if x["status"] == "offered"] == ["site_no_rack", "site_record"] and b["next_quest_id"] == RACK.format(sid)
    assert b["sites"][0]["completeness"]["open"] > 0 and not b["sites"][0]["boss_ready"]
    q = say(cl, RACK.format(sid), ("rack_kind", {"choice": "u:12"}), ("rack_name", {"text": "ML-1"}))
    assert q["changes"] == ["New rack ML-1 at Mill Lane (12U)."]
    v = apply(cl, RACK.format(sid))
    assert v["complete"] and v["xp_awarded"] == 40 and "Next in the chain \"Setting up Mill Lane\": 2 of 3: kit." in v["message"]
    rack = box.data["dcim/racks"][0]
    assert (rack["name"], rack["u_height"], rack["status"]["value"]) == ("ML-1", 12, "active")
    # ---- the boxes: found by what is printed on them; the library's definition is imported with its ports
    kit = KIT.format(sid)
    q = say(cl, kit, ("box0_find0", {"text": "c1111-4p"}))
    assert q["step"]["prompt"] == '2 models match "c1111-4p". Which is it?'                # the exact part number comes first
    row = packs.load().index().by_file("device", C1111)
    assert q["step"]["choices"][0] == {"id": f"lib:{row['n']}", "label": "Cisco ISR C1111-4P (1U, 6 ports)", "kind": "candidate"}
    assert [ch["label"] for ch in q["step"]["choices"][2:]] == ["None of these: search again", "It is not in the library: record it by hand", "I don't know yet"]
    q = say(cl, kit, ("box0_pick0", {"choice": f"lib:{row['n']}"}))
    assert [ch["label"] for ch in q["step"]["choices"]][:7] == list(setup.ROLES)       # an empty NetBox has no role yet: the common ones are offered
    q = say(cl, kit, ("role0", {"choice": "new:router"}))
    assert [f["id"] for f in q["step"]["fields"]] == ["name", "position", "serial"] and "U1 is the lowest free one" in q["step"]["fields"][1]["hint"]
    q = say(cl, kit, ("box0_details", {"values": {"name": "ml-rtr1", "position": 10, "serial": "FGL214512AB"}}),
            ("more0", {"choice": "yes"}), ("box1_find0", {"text": "ex2300 24p"}))
    ex = packs.load().index().by_file("device", EX2300)
    q = say(cl, kit, ("box1_pick0", {"choice": f"lib:{ex['n']}"}), ("role1", {"choice": "new:switch"}))
    # the unit the first box took is not free for the second
    taken = post(cl, "answer", kit, step="box1_details", values={"name": "ml-sw1", "position": 10})
    assert taken["step"]["error"] == "U10 of rack ML-1 is taken by the box you added before. Read the rail again."
    q = say(cl, kit, ("box1_details", {"values": {"name": "ml-sw1", "position": 9}}), ("more1", {"choice": "no"}))
    assert q["status"] == "ready" and [o["op"] for o in q["proposal"]["ops"]] == ["add_device_type", "create_device", "add_device_type", "create_device"]
    assert q["changes"] == [
        "Import the device type Cisco ISR C1111-4P from the device-type library (part number C1111-4P; 1U, 6 interfaces, 2 console ports, 1 power port), "
        "with its manufacturer if NetBox lacks it.",
        "New device ml-rtr1: a Cisco ISR C1111-4P as Router at Mill Lane, rack ML-1 unit 10, serial FGL214512AB.",
        "Import the device type Juniper EX2300-24P from the device-type library (part number EX2300-24P; 1U, 32 interfaces, 1 console port, 1 power port), "
        "with its manufacturer if NetBox lacks it.",
        "New device ml-sw1: a Juniper EX2300-24P as Switch at Mill Lane, rack ML-1 unit 9."]
    before = len(box.writes)
    r = cl.post("/api/game/quests/preview", json={"scope": "netbox", "quest_id": kit}).json()
    assert len(box.writes) == before                                                   # a preview writes nothing
    assert r["ops"][0]["after"] == {"manufacturer": "Cisco", "model": "ISR C1111-4P", "part_number": "C1111-4P", "u_height": 1.0, "console_ports": 2,
                                    "power_ports": 1, "interfaces": 6, "source": "device-type library at 9f0e6916b8"}
    v = apply(cl, kit)
    # 80 XP for the line, and the bonus for two boxes of models the library confirms
    assert v["complete"] and v["bonus_xp"] == 2 * setup.TYPE_BONUS and v["xp_awarded"] == 80 + 40
    assert "2 boxes of a model the device-type library confirms: +40 XP." in v["message"]
    assert sorted(m["name"] for m in box.data["dcim/manufacturers"]) == ["Cisco", "Juniper"]
    assert sorted((t["model"], t["part_number"]) for t in box.data["dcim/device-types"]) == [("EX2300-24P", "EX2300-24P"), ("ISR C1111-4P", "C1111-4P")]
    assert len(box.data["dcim/interface-templates"]) == 38 and len(box.data["dcim/interfaces"]) == 38       # NetBox gave each device its ports
    assert sorted((r_["name"], r_["slug"]) for r_ in box.data["dcim/device-roles"]) == [("Router", "router"), ("Switch", "switch")]
    rtr = next(d for d in box.data["dcim/devices"] if d["name"] == "ml-rtr1")
    assert (rtr["serial"], rtr["position"], rtr["rack"]["id"], rtr["tags"]) == ("FGL214512AB", 10.0, rack["id"], [])
    # ---- whose line: the pack of the site's country, by name; nothing is on record yet, so the pack leads
    line = CARRIER.format(sid)
    b = board(cl)
    assert next(ch for ch in b["chains"] if ch["kind"] == "setup")["label"] == "3 of 3: carrier"
    q = next(x for x in b["quests"] if x["id"] == line)
    assert q["step"]["prompt"] == "Whose line is it?"
    assert [ch["label"] for ch in q["step"]["choices"]] == ["KPN", "Odido", "Ziggo", "Vodafone", "DELTA Fiber", "Eurofiber", "Another provider: search by name",
                                                            "My provider is not listed", "We do not keep carriers in NetBox", "I don't know yet"]
    q = say(cl, line, ("provider", {"choice": "pack:kpn"}))
    assert q["status"] == "ready" and q["changes"] == ["New provider KPN (AS1136), from common providers: Netherlands."]
    v = apply(cl, line)
    assert v["complete"] and v["xp_awarded"] == 50 and v["bonus_xp"] == 0
    kpn = box.data["circuits/providers"][0]
    assert (kpn["name"], kpn["slug"], kpn["asns"][0]["asn"]) == ("KPN", "kpn", 1136)
    assert box.data["ipam/rirs"][0]["name"] == "RIPE NCC" and box.data["ipam/asns"][0]["rir"]["id"] == box.data["ipam/rirs"][0]["id"]
    # ---- the site is set up, and the game goes on to what it has always asked: how the site reaches the outside
    b = board(cl)
    assert not [ch for ch in b["chains"] if ch["kind"] == "setup"]
    kinds = [x["kind"] for x in b["quests"] if x["status"] in ("offered", "active")]
    assert kinds[0] == "site_no_way_out" and b["rewards"]["xp"] == 60 + 40 + 120 + 50 and b["rewards"]["tools"] == []
    done = [x["kind"] for x in b["quests"] if x["status"] == "done"]
    assert sorted(done) == ["estate_no_site", "site_no_devices", "site_no_provider", "site_no_rack"]
    # the way out itself: the carrier is on record now, and a NetBox with no circuit type gets one with the circuit
    way = f"q:site_no_way_out:site:{sid}"
    port = next(p for p in box.data["dcim/interfaces"] if p["device"]["id"] == rtr["id"] and p["name"] == "GigabitEthernet0/0/0")
    q = say(cl, way, ("device", {"choice": f"device:{rtr['id']}"}), ("provider", {"choice": f"provider:{kpn['id']}"}), ("circuit_id", {"text": "KPN-7001"}),
            ("circuit_type", {"choice": "new:0"}), ("local_port", {"choice": f"interface:{port['id']}"}), ("cable_type", {"choice": "smf"}))
    assert [o["op"] for o in q["proposal"]["ops"]] == ["create_circuit_type", "create_circuit", "create_circuit_termination", "connect_cable"]
    v = apply(cl, way)
    assert v["complete"] and box.data["circuits/circuits"][0]["cid"] == "KPN-7001" and box.data["circuits/circuit-types"][0]["name"] == "Internet"
    assert v["unlocked"] == ["cable_tracer"]                                           # the first recorded connection, as before


def test_a_provider_netbox_already_holds_is_offered_first_and_a_pack_entry_for_it_is_not(tmp_path, monkeypatch):
    box = EmptyBox()
    region = box.create("dcim/regions", {"name": "Netherlands", "slug": "nl"})
    site = box.create("dcim/sites", {"name": "Mill Lane", "slug": "mill-lane", "status": "active", "region": region["id"]})
    role = box.create("dcim/device-roles", {"name": "Router", "slug": "router", "color": "9c27b0"})
    maker = box.create("dcim/manufacturers", {"name": "Example", "slug": "example"})
    dtype = box.create("dcim/device-types", {"manufacturer": maker["id"], "model": "EX-R", "slug": "ex-r"})
    box.create("dcim/interface-templates", {"device_type": dtype["id"], "name": "wan0", "type": "1000base-t"})
    rtr = box.create("dcim/devices", {"name": "ml-rtr1", "site": site["id"], "role": role["id"], "device_type": dtype["id"], "status": "active", "serial": "S1"})
    rir = box.create("ipam/rirs", {"name": "RIPE NCC", "slug": "ripe-ncc"})
    asn = box.create("ipam/asns", {"asn": 1136, "rir": rir["id"]})
    have = box.create("circuits/providers", {"name": "Royal Dutch Telecom", "slug": "rdt", "asns": [asn["id"]]})
    cl = game(tmp_path, monkeypatch, box)
    way = f"q:site_no_way_out:site:{site['id']}"
    q = say(cl, way, ("device", {"choice": f"device:{rtr['id']}"}))
    labels = [ch["label"] for ch in q["step"]["choices"]]
    # KPN is AS1136, which NetBox holds under another name: the pack does not offer it a second time
    assert labels[0] == "Royal Dutch Telecom" and "KPN" not in labels and labels[1:3] == ["Odido", "Ziggo"]
    # typed by its pack name all the same: it is the provider NetBox has, and nothing is added
    q = say(cl, way, ("provider", {"choice": "search"}), ("provider_find0", {"text": "kpn"}))
    assert q["step"]["prompt"] == '1 provider matches "kpn".'
    assert [ch["label"] for ch in q["step"]["choices"]][:3] == ["Royal Dutch Telecom (in your NetBox)", "None of these: search again", "My provider is not listed"]
    q = say(cl, way, ("provider_pick0", {"choice": "again"}), ("provider_find1", {"text": "odido"}))
    assert q["step"]["choices"][0]["label"] == "Odido"
    q = say(cl, way, ("provider_pick1", {"choice": "typed"}), ("provider_name", {"text": "Royal Dutch Telecom"}), ("circuit_id", {"text": "RDT-1"}))
    assert q["step"]["id"] == "circuit_type"
    port = box.data["dcim/interfaces"][0]
    q = say(cl, way, ("circuit_type", {"choice": "new:0"}), ("local_port", {"choice": f"interface:{port['id']}"}), ("cable_type", {"choice": "cat6"}))
    ops = q["proposal"]["ops"]
    assert [o["op"] for o in ops] == ["create_circuit_type", "create_circuit", "create_circuit_termination", "connect_cable"] and ops[1]["fields"]["provider_id"] == have["id"]
    assert apply(cl, way)["complete"] and len(box.data["circuits/providers"]) == 1
    assert box.data["circuits/circuits"][0]["provider"]["id"] == have["id"]


def near_empty(box: EmptyBox, country: str = "gb") -> tuple[dict[str, Any], dict[str, Any]]:
    region = box.create("dcim/regions", {"name": country.upper(), "slug": country})
    site = box.create("dcim/sites", {"name": "Quay House", "slug": "quay-house", "status": "active", "region": region["id"]})
    role = box.create("dcim/device-roles", {"name": "Switch", "slug": "switch", "color": "2196f3"})
    maker = box.create("dcim/manufacturers", {"name": "Example", "slug": "example"})
    dtype = box.create("dcim/device-types", {"manufacturer": maker["id"], "model": "EX-S", "slug": "ex-s"})
    box.create("dcim/interface-templates", {"device_type": dtype["id"], "name": "eth1", "type": "1000base-t"})
    dev = box.create("dcim/devices", {"name": "qh-sw1", "site": site["id"], "role": role["id"], "device_type": dtype["id"], "status": "active", "serial": "S1"})
    return site, dev


def test_my_provider_is_not_listed_takes_the_name_as_typed_and_a_pack_carrier_comes_with_the_circuit(tmp_path, monkeypatch):
    box = EmptyBox()
    site, dev = near_empty(box)
    cl = game(tmp_path, monkeypatch, box)
    line = CARRIER.format(site["id"])
    q = next(x for x in board(cl)["quests"] if x["id"] == line)
    assert [ch["label"] for ch in q["step"]["choices"]][:6] == ["BT", "Openreach", "Virgin Media Business", "Sky", "TalkTalk", "Vodafone UK"]
    q = say(cl, line, ("provider", {"choice": "typed"}), ("provider_name", {"text": "Harbour Wireless Co-op"}))
    assert q["changes"] == ["New provider Harbour Wireless Co-op, as you typed it (no pack lists it)."]
    assert q["proposal"]["ops"][0]["fields"] == {"name": "Harbour Wireless Co-op"}
    assert apply(cl, line)["complete"] and box.data["circuits/providers"][0]["slug"] == "harbour-wireless-co-op" and box.data["ipam/asns"] == []
    # the way out, with a carrier from the pack this time: it is added in the same previewed change as its circuit
    way = f"q:site_no_way_out:site:{site['id']}"
    q = say(cl, way, ("device", {"choice": f"device:{dev['id']}"}))
    assert [ch["label"] for ch in q["step"]["choices"]][:3] == ["Harbour Wireless Co-op", "BT", "Openreach"]
    port = box.data["dcim/interfaces"][0]
    q = say(cl, way, ("provider", {"choice": "pack:bt"}), ("circuit_id", {"text": "BT-1"}), ("circuit_type", {"choice": "new:0"}),
            ("local_port", {"choice": f"interface:{port['id']}"}), ("cable_type", {"choice": "cat6"}))
    assert [o["op"] for o in q["proposal"]["ops"]] == ["create_provider", "create_circuit_type", "create_circuit", "create_circuit_termination", "connect_cable"]
    assert q["changes"][:3] == ["New provider BT (AS2856), from common providers: United Kingdom.", "New circuit type Internet.", "New circuit BT-1 from BT (Internet)."]
    assert apply(cl, way)["complete"]
    assert sorted(p["name"] for p in box.data["circuits/providers"]) == ["BT", "Harbour Wireless Co-op"]
    assert box.data["circuits/circuits"][0]["provider"]["name"] == "BT"


def test_a_site_in_a_country_with_no_pack_says_so_and_searches_the_others(tmp_path, monkeypatch):
    box = EmptyBox()
    site, _ = near_empty(box, "be")
    cl = game(tmp_path, monkeypatch, box)
    q = next(x for x in board(cl)["quests"] if x["id"] == CARRIER.format(site["id"]))
    assert q["step"]["prompt"] == ("Whose line is it? (The game has no provider pack for Belgium yet: search the others, or type the name.)")
    assert [ch["label"] for ch in q["step"]["choices"]] == ["Another provider: search by name", "My provider is not listed", "We do not keep carriers in NetBox",
                                                            "I don't know yet"]
    line = CARRIER.format(site["id"])
    q = say(cl, line, ("provider", {"choice": "search"}), ("provider_find0", {"text": "colt"}))
    assert [ch["label"] for ch in q["step"]["choices"]][0] == "Colt Technology Services (DE)"
    parked = post(cl, "answer", line, step="provider_pick0", choice="unknown")
    assert parked["status"] == "parked" and parked["checklist"][0] == "The hand-off box and the bill carry the carrier's name."
    # "we do not keep carriers in NetBox" closes it, unpaid, and the site is not asked again
    post(cl, "answer", line, step="back")
    post(cl, "answer", line, step="restart")
    closed = post(cl, "answer", line, step="provider", choice="none")
    assert closed["status"] == "dismissed" and line not in [x["id"] for x in board(cl)["quests"] if x["status"] != "dismissed"]


def test_an_estate_that_has_begun_cabling_without_carriers_is_not_asked_whose_line_it_is():
    b = Build()
    s = b.site()
    one, two = b.dev(s, "sw1", tags=[]), b.dev(s, "sw2", tags=[])
    assert [f["kind"] for f in findings.analyse(b.e).findings if f["kind"] == "site_no_provider"] == ["site_no_provider"]
    b.cable(b.p(one, "eth1"), b.p(two, "eth1"))
    b.e.touch()
    a = findings.analyse(b.e)
    assert "site_no_provider" not in [f["kind"] for f in a.findings] and a.check(f"site_no_provider:site:{s}") is None


# ---------------------------------------------------------------- "What is this box?"
def placeholder_box(box: EmptyBox, cabled: bool = False) -> tuple[dict[str, Any], dict[str, Any]]:
    site, sw = near_empty(box)
    maker = box.create("dcim/manufacturers", {"name": "Generic", "slug": "generic"})
    dtype = box.create("dcim/device-types", {"manufacturer": maker["id"], "model": "Unknown", "slug": "unknown"})
    role = box.data["dcim/device-roles"][0]
    if cabled:
        box.create("dcim/interface-templates", {"device_type": dtype["id"], "name": "Gi0/49", "type": "1000base-t"})
    dev = box.create("dcim/devices", {"name": "qh-box", "site": site["id"], "role": role["id"], "device_type": dtype["id"], "status": "active", "serial": "S2"})
    if cabled:
        mine = next(p for p in box.data["dcim/interfaces"] if p["device"]["id"] == dev["id"])
        other = next(p for p in box.data["dcim/interfaces"] if p["device"]["id"] == sw["id"])
        box.create("dcim/cables", {"a_terminations": [{"object_type": "dcim.interface", "object_id": mine["id"]}],
                                   "b_terminations": [{"object_type": "dcim.interface", "object_id": other["id"]}], "type": "cat6"})
    return site, dev


def test_a_placeholder_device_gets_its_real_model_with_its_ports(tmp_path, monkeypatch):
    box = EmptyBox()
    site, dev = placeholder_box(box)
    cl = game(tmp_path, monkeypatch, box)
    line = f"q:device_type_unknown:device:{dev['id']}"
    q = next(x for x in board(cl)["quests"] if x["id"] == line)
    assert q["title"] == "What qh-box really is" and q["purpose"].startswith('qh-box is recorded as "Generic Unknown": NetBox does not say what box it really is.')
    assert q["step"]["prompt"].startswith("What is printed on qh-box (")
    ex = packs.load().index().by_file("device", EX2300)
    q = say(cl, line, ("box_find0", {"text": "EX2300-24P"}))
    assert q["step"]["choices"][-2] == {"id": "none", "label": "It really is a generic box: leave it", "kind": "not_applicable"}
    q = say(cl, line, ("box_pick0", {"choice": f"lib:{ex['n']}"}))
    assert [o["op"] for o in q["proposal"]["ops"]] == ["add_device_type", "set_device_type"] and q["proposal"]["ops"][1]["if_unmodified_since"]
    assert q["changes"][1] == ("qh-box: becomes a Juniper EX2300-24P. The ports it lacks are added from the model; nothing it has is removed. "
                               "NetBox refuses this if the device has a port the model does not have.")
    v = apply(cl, line)
    assert v["complete"] and v["xp_awarded"] == 40 + setup.TYPE_BONUS and v["bonus_xp"] == setup.TYPE_BONUS
    now = box.row("dcim/devices", dev["id"])
    assert now["device_type"]["model"] == "EX2300-24P" and len([p for p in box.data["dcim/interfaces"] if p["device"]["id"] == dev["id"]]) == 32
    assert len([p for p in box.data["dcim/console-ports"] if p["device"]["id"] == dev["id"]]) == 1
    cat = cl.get("/api/game/catalogue", params={"fresh": True}).json()
    schema_validator("Catalogue").validate(cat)
    assert [(i["manufacturer"], i["model"], i["status"]) for i in cat["items"]] == [
        ("Example", "EX-S", "unverified"), ("Generic", "Unknown", "unverified"), ("Juniper", "EX2300-24P", "verified")]
    assert (cat["verified"], cat["total"]) == (1, 3) and cat["message"].startswith("1 of the 3 models in your NetBox is confirmed")


def test_a_placeholder_device_with_a_cabled_port_the_model_lacks_is_refused_and_nothing_is_written(tmp_path, monkeypatch):
    box = EmptyBox()
    site, dev = placeholder_box(box, cabled=True)
    cl = game(tmp_path, monkeypatch, box)
    line = f"q:device_type_unknown:device:{dev['id']}"
    ex = packs.load().index().by_file("device", EX2300)
    say(cl, line, ("box_find0", {"text": "EX2300-24P"}), ("box_pick0", {"choice": f"lib:{ex['n']}"}))
    before = len(box.writes)
    r = cl.post("/api/game/quests/preview", json={"scope": "netbox", "quest_id": line}).json()
    assert [o["status"] for o in r["ops"]] == ["ok", "error"]
    assert r["ops"][1]["error"]["message"].startswith("qh-box has 1 component that Juniper EX2300-24P does not have: interface Gi0/49 (cabled).")
    v = post(cl, "apply", line)
    assert not v["complete"] and v["xp_awarded"] == 0 and len(box.writes) == before and v["message"].startswith("NetBox did not take that change, and nothing was written: qh-box has 1 component")
    assert box.row("dcim/devices", dev["id"])["device_type"]["model"] == "Unknown" and len(box.data["dcim/device-types"]) == 2


def test_a_model_the_library_does_not_hold_is_recorded_by_hand_and_marked_unverified_with_no_bonus(tmp_path, monkeypatch):
    box = EmptyBox()
    site, dev = placeholder_box(box)
    cl = game(tmp_path, monkeypatch, box)
    line = f"q:device_type_unknown:device:{dev['id']}"
    q = say(cl, line, ("box_find0", {"text": "harbour hx-9"}))
    assert q["step"]["prompt"] == 'Nothing in the device-type library matches "harbour hx-9".'
    q = say(cl, line, ("box_pick0", {"choice": "manual"}))
    assert "marked unverified" in q["step"]["prompt"] and [f["id"] for f in q["step"]["fields"]] == ["manufacturer", "model", "part_number", "u_height"]
    # typed by hand with a part number that contradicts the library: refused, with what was expected
    bad = post(cl, "answer", line, step="box_manual", values={"manufacturer": "Juniper", "model": "EX2300-24P", "part_number": "EX2300-48P"})
    assert bad["step"]["error"].startswith("The device-type library knows EX2300-48P as the part number of the Juniper EX2300-48P, not of the EX2300-24P.")
    q = say(cl, line, ("box_manual", {"values": {"manufacturer": "Harbour Works", "model": "HX-9", "part_number": "HX-9-A", "u_height": 2}}))
    assert q["changes"][0] == ("New device type Harbour Works HX-9, recorded by hand and marked unverified: the device-type library does not hold it, "
                               "so it earns no bonus.")
    v = apply(cl, line)
    assert v["complete"] and v["xp_awarded"] == 40 and v["bonus_xp"] == 0 and "No bonus: the model is not one the device-type library holds" in v["message"]
    made = next(t for t in box.data["dcim/device-types"] if t["model"] == "HX-9")
    assert [t["slug"] for t in made["tags"]] == ["rw-unverified"] and box.data["extras/tags"][0]["name"] == "Unverified (RackWalker)"
    cat = cl.get("/api/game/catalogue", params={"fresh": True}).json()
    assert next(i for i in cat["items"] if i["model"] == "HX-9")["note"] == "Recorded by hand: the device-type library does not hold this model."


def test_without_the_library_the_game_plays_on_and_says_why_an_import_must_wait(tmp_path, monkeypatch):
    monkeypatch.delenv("RACKWALKER_LIBRARY_DIR")
    box = EmptyBox()
    site, dev = placeholder_box(box)
    cl = game(tmp_path, monkeypatch, box)
    assert cl.get("/api/packs").json()["library"]["import_from"] == "none"
    line = f"q:device_type_unknown:device:{dev['id']}"
    ex = packs.load().index().by_file("device", EX2300)
    q = say(cl, line, ("box_find0", {"text": "EX2300-24P"}), ("box_pick0", {"choice": f"lib:{ex['n']}"}))     # the search needs no network
    assert q["status"] == "ready"
    r = cl.post("/api/game/quests/preview", json={"scope": "netbox", "quest_id": line}).json()
    assert r["ops"][0]["status"] == "error" and r["ops"][0]["error"]["code"] == "library_unavailable"
    assert r["ops"][0]["error"]["message"].startswith("Importing a model needs its full definition from the device-type library, and this game has no local copy")
    v = post(cl, "apply", line)
    assert not v["complete"] and box.writes == [] and len(board(cl)["quests"]) >= 1      # the rest of the game is as it was


def test_a_definition_that_is_not_the_indexed_file_is_not_used(tmp_path, monkeypatch):
    copy = tmp_path / "library"
    shutil.copytree(LIBRARY, copy)
    (copy / "device-types" / EX2300).write_text((copy / "device-types" / EX2300).read_text().replace("u_height: 1", "u_height: 3"))
    monkeypatch.setenv("RACKWALKER_LIBRARY_DIR", str(copy))
    row = packs.load().index().by_file("device", EX2300)
    with pytest.raises(packs.LibraryUnavailable, match="its SHA-256 differs"):
        asyncio.run(packs.definition(row))
    monkeypatch.setenv("RACKWALKER_LIBRARY_DIR", str(LIBRARY))
    assert asyncio.run(packs.definition(row))["model"] == "EX2300-24P"
    # over HTTPS it is the pinned commit that is asked, and the same check applies
    monkeypatch.delenv("RACKWALKER_LIBRARY_DIR")
    monkeypatch.setenv("RACKWALKER_LIBRARY_FETCH", "1")
    asked: list[str] = []
    packs._DEFINITIONS.clear()          # (read and kept above: this asks again)

    def pinned(req: httpx.Request) -> httpx.Response:
        asked.append(str(req.url))
        return httpx.Response(200, content=(LIBRARY / "device-types" / EX2300).read_bytes())
    assert asyncio.run(packs.definition(row, transport=httpx.MockTransport(pinned)))["slug"] == "juniper-ex2300-24p"
    assert asked == [f"https://raw.githubusercontent.com/netbox-community/devicetype-library/{packs.load().device_pack()['library']['commit']}/device-types/Juniper/EX2300-24P.yaml"]
    packs._DEFINITIONS.clear()
    with pytest.raises(packs.LibraryUnavailable, match="SHA-256 differs"):
        asyncio.run(packs.definition(row, transport=httpx.MockTransport(lambda req: httpx.Response(200, content=b"model: EX2300-24P\n"))))
    assert packs._DEFINITIONS == {}                 # a definition that failed its check is never kept

    def down(req: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("no route")
    with pytest.raises(packs.LibraryUnavailable, match="could not be reached"):
        asyncio.run(packs.definition(row, transport=httpx.MockTransport(down)))


# ---------------------------------------------------------------- modules
def test_a_device_with_empty_module_bays_is_asked_what_is_fitted(tmp_path, monkeypatch):
    box = EmptyBox()
    site, _ = near_empty(box)
    maker = box.create("dcim/manufacturers", {"name": "Juniper", "slug": "juniper"})
    dtype = box.create("dcim/device-types", {"manufacturer": maker["id"], "model": "MX204", "slug": "juniper-mx204", "part_number": "MX204"})
    for name in ("Power Supply 0", "Power Supply 1"):
        box.create("dcim/module-bay-templates", {"device_type": dtype["id"], "name": name})
    role = box.data["dcim/device-roles"][0]
    rtr = box.create("dcim/devices", {"name": "qh-rtr", "site": site["id"], "role": role["id"], "device_type": dtype["id"], "status": "active", "serial": "S9"})
    cl = game(tmp_path, monkeypatch, box)
    line = f"q:module_bays_unrecorded:device:{rtr['id']}"
    q = next(x for x in board(cl)["quests"] if x["id"] == line)
    assert q["purpose"].startswith("qh-rtr has 2 module bays, and NetBox shows nothing in any of them.")
    assert [ch["label"] for ch in q["step"]["choices"]] == ["Power Supply 0", "Power Supply 1", "Nothing is fitted: the bays are empty", "I don't know yet"]
    bay = box.data["dcim/module-bays"][0]
    psu = packs.load().index().by_file("module", PSU)
    q = say(cl, line, ("bay", {"choice": f"bay:{bay['id']}"}), ("mod_find0", {"text": "JPSU-650W-AC-AFO"}))
    assert q["step"]["choices"][0]["label"] == "Juniper JPSU-650W-AC-AFO" and "It is not in the library: record it by hand" in [ch["label"] for ch in q["step"]["choices"]]
    q = say(cl, line, ("mod_pick0", {"choice": f"lib:{psu['n']}"}), ("mod_serial", {"text": "PSU-0001"}))
    assert [o["op"] for o in q["proposal"]["ops"]] == ["add_module_type", "install_module"]
    assert q["changes"][1] == "qh-rtr, bay Power Supply 0: a Juniper JPSU-650W-AC-AFO, serial PSU-0001."
    v = apply(cl, line)
    assert v["complete"] and v["xp_awarded"] == 40
    module = box.data["dcim/modules"][0]
    assert (module["serial"], module["module_bay"]["id"], module["module_type"]["model"]) == ("PSU-0001", bay["id"], "JPSU-650W-AC-AFO")
    assert box.data["dcim/module-types"][0]["part_number"] == "JPSU-650W-AC-AFO" and box.data["dcim/power-port-templates"][0]["module_type"]["id"]


def test_a_module_the_library_does_not_hold_is_recorded_by_hand_and_marked_unverified(tmp_path, monkeypatch):
    box = EmptyBox()
    site, _ = near_empty(box)
    maker = box.create("dcim/manufacturers", {"name": "Juniper", "slug": "juniper"})
    dtype = box.create("dcim/device-types", {"manufacturer": maker["id"], "model": "MX204", "slug": "juniper-mx204", "part_number": "MX204"})
    box.create("dcim/module-bay-templates", {"device_type": dtype["id"], "name": "Fan Tray 0"})
    rtr = box.create("dcim/devices", {"name": "qh-rtr", "site": site["id"], "role": box.data["dcim/device-roles"][0]["id"], "device_type": dtype["id"],
                                      "status": "active", "serial": "S9"})
    cl = game(tmp_path, monkeypatch, box)
    line = f"q:module_bays_unrecorded:device:{rtr['id']}"
    bay = box.data["dcim/module-bays"][0]
    q = say(cl, line, ("bay", {"choice": f"bay:{bay['id']}"}), ("mod_find0", {"text": "harbour fan 9"}), ("mod_pick0", {"choice": "manual"}))
    assert q["step"]["prompt"].startswith("Record the module by hand. It is marked unverified in NetBox,")
    assert [f["id"] for f in q["step"]["fields"]] == ["manufacturer", "model", "part_number"]          # a module takes no rack unit
    # a part number that contradicts the library's for a module it holds is refused, as for a device
    bad = post(cl, "answer", line, step="mod_manual", values={"manufacturer": "Juniper", "model": "JPSU-650W-AC-AFO", "part_number": "640-060603"})
    assert "knows 640-060603 as the part number of the Juniper JPSU-150-AC-AFO, not of the JPSU-650W-AC-AFO" in bad["step"]["error"]
    q = say(cl, line, ("mod_manual", {"values": {"manufacturer": "Harbour Works", "model": "HF-9", "part_number": "HF-9-A"}}), ("mod_serial", {"text": "F-77"}))
    assert [o["op"] for o in q["proposal"]["ops"]] == ["add_module_type", "install_module"]
    assert q["changes"][0] == "New module type Harbour Works HF-9, recorded by hand and marked unverified: the device-type library does not hold it."
    need = perms.needs_of(q["proposal"]["ops"])
    assert ("extras.tag", "add") in need and ("dcim.interfacetemplate", "add") not in need
    v = apply(cl, line)
    assert v["complete"] and v["xp_awarded"] == 40
    made = box.data["dcim/module-types"][0]
    assert (made["model"], made["part_number"], [t["slug"] for t in made["tags"]]) == ("HF-9", "HF-9-A", ["rw-unverified"])
    assert box.data["dcim/modules"][0]["module_type"]["id"] == made["id"] and box.data["dcim/modules"][0]["serial"] == "F-77"


# ---------------------------------------------------------------- a patch panel keeps its way through
def test_the_front_ports_of_a_definition_lead_to_its_rear_ports():
    import yaml

    doc = yaml.safe_load((LIBRARY / "device-types" / PANEL).read_text())
    got = setupops.port_mappings(doc)
    assert len(got) == 24 and got["07"] == [{"position": 1, "rear": "07", "rear_position": 1}]
    # the older form: the rear port named on the front port itself; and a splitter, several positions of one rear port
    assert setupops.port_mappings({"front-ports": [{"name": "F1", "rear_port": "R1", "rear_port_position": 3}, {"name": "F2"}]}) == {
        "F1": [{"position": 1, "rear": "R1", "rear_position": 3}]}
    fan = {"front-ports": [{"name": "A"}, {"name": "B"}], "port-mappings": [{"front_port": "A", "rear_port": "T", "rear_port_position": 1},
                                                                            {"front_port": "B", "rear_port": "T", "rear_port_position": 2}]}
    assert [m[0]["rear_position"] for m in setupops.port_mappings(fan).values()] == [1, 2]
    assert setupops.port_mappings({"interfaces": [{"name": "eth0"}]}) == {}


def test_an_imported_patch_panel_is_mapped_front_to_rear_on_the_type_and_on_the_device(tmp_path, monkeypatch):
    box = EmptyBox()
    site, dev = placeholder_box(box)
    cl = game(tmp_path, monkeypatch, box)
    line = f"q:device_type_unknown:device:{dev['id']}"
    panel = packs.load().index().by_file("device", PANEL)
    say(cl, line, ("box_find0", {"text": "DP24688TGY"}), ("box_pick0", {"choice": f"lib:{panel['n']}"}))
    r = cl.post("/api/game/quests/preview", json={"scope": "netbox", "quest_id": line}).json()
    assert (r["ops"][0]["after"]["front_ports"], r["ops"][0]["after"]["rear_ports"], r["ops"][0]["after"]["port_mappings"]) == (24, 24, 24)
    assert apply(cl, line)["complete"]
    rear_tpl = {t["name"]: t["id"] for t in box.data["dcim/rear-port-templates"]}
    front_tpl = {t["name"]: t for t in box.data["dcim/front-port-templates"]}
    assert len(front_tpl) == 24 and all(t["rear_ports"] == [{"position": 1, "rear_port": rear_tpl[name], "rear_port_position": 1}]
                                        for name, t in front_tpl.items())
    # the device that took the model: each front port leads to the device's own rear port of the same name
    rear = {p["name"]: p["id"] for p in box.data["dcim/rear-ports"] if p["device"]["id"] == dev["id"]}
    front = [p for p in box.data["dcim/front-ports"] if p["device"]["id"] == dev["id"]]
    assert len(front) == 24 and all(p["rear_ports"] == [{"position": 1, "rear_port": rear[p["name"]], "rear_port_position": 1}] for p in front)


# ---------------------------------------------------------------- a registry NetBox already holds
@pytest.mark.parametrize("held", [{"name": "RIPE", "slug": "ripe"}, {"name": "RIPE NCC", "slug": "ripe-ncc"}, {"name": "ripe ncc", "slug": "europe"}])
def test_a_registry_netbox_already_holds_under_either_name_is_used_and_no_second_one_is_made(tmp_path, monkeypatch, held):
    box = EmptyBox()
    site, _ = near_empty(box, "nl")
    box.create("ipam/rirs", {"name": "ARIN", "slug": "arin"})
    mine = box.create("ipam/rirs", held)
    cl = game(tmp_path, monkeypatch, box)
    line = CARRIER.format(site["id"])
    say(cl, line, ("provider", {"choice": "pack:kpn"}))
    assert apply(cl, line)["complete"]
    assert len(box.data["ipam/rirs"]) == 2 and box.data["ipam/asns"][0]["rir"]["id"] == mine["id"]


# ---------------------------------------------------------------- a part number NetBox holds against the label
def part_box(box: EmptyBox, part: str) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    site, _ = near_empty(box)
    maker = box.create("dcim/manufacturers", {"name": "Juniper", "slug": "juniper"})
    dtype = box.create("dcim/device-types", {"manufacturer": maker["id"], "model": "MX204", "slug": "juniper-mx204", "part_number": part})
    rtr = box.create("dcim/devices", {"name": "qh-rtr", "site": site["id"], "role": box.data["dcim/device-roles"][0]["id"], "device_type": dtype["id"],
                                      "status": "active", "serial": "S9"})
    return site, dtype, rtr


def test_a_wrong_part_number_in_netbox_is_put_right_from_the_label_in_the_librarys_spelling(tmp_path, monkeypatch):
    box = EmptyBox()
    site, dtype, rtr = part_box(box, "EX2300-24P")          # the part number of another Juniper model
    cl = game(tmp_path, monkeypatch, box)
    line = f"q:device_type_part_number:device_type:{dtype['id']}"
    q = next(x for x in board(cl)["quests"] if x["id"] == line)
    assert q["title"] == "The part number of the Juniper MX204" and q["rank"] == "identity" and q["site_id"] == site["id"]
    assert q["purpose"].startswith("NetBox holds the part number EX2300-24P for the Juniper MX204; in the device-type library that is the part number of the "
                                   "EX2300-24P, and the MX204 is listed with MX204.")
    assert q["step"]["prompt"] == "qh-rtr is a Juniper MX204. What part number is on its label? (EX2300-24P is the part number of the EX2300-24P.)"
    assert [(ch["label"], ch["kind"]) for ch in q["step"]["choices"]] == [("MX204: record that", "candidate"), ("Something else", "other"),
                                                                          ("Leave EX2300-24P in NetBox", "not_applicable"), ("I don't know yet", "unknown")]
    # the label carries the number of another model: nothing is proposed, and the line says what is really wrong
    q = say(cl, line, ("label", {"choice": "else"}), ("label_says", {"text": "ex2300 24p"}))
    assert q["status"] == "parked" and q["proposal"] is None
    assert q["checklist"][0].startswith("ex2300 24p is the part number of the Juniper EX2300-24P. If that is what the label says, qh-rtr is not a Juniper MX204")
    # typed in another spelling of the library's own number: taken, and written as the library spells it
    q = say(cl, line, ("label_says", {"text": "mx-204"}))
    assert q["status"] == "ready" and q["proposal"]["ops"] == [{"op": "set_part_number", "target": {"kind": "device_type", "id": dtype["id"]},
                                                                "fields": {"part_number": "MX204"}}]
    assert q["changes"] == ["Juniper MX204: part number EX2300-24P becomes MX204, as the device-type library lists it. Every device of that model shows it."]
    assert perms.needs_of(q["proposal"]["ops"]) == [("dcim.devicetype", "change")]
    r = cl.post("/api/game/quests/preview", json={"scope": "netbox", "quest_id": line}).json()
    assert (r["ops"][0]["before"], r["ops"][0]["after"]) == ({"part_number": "EX2300-24P"}, {"part_number": "MX204"}) and box.writes == []
    v = apply(cl, line)
    assert v["complete"] and v["xp_awarded"] == 30 and v["bonus_xp"] == 0
    assert box.row("dcim/device-types", dtype["id"])["part_number"] == "MX204"
    cat = cl.get("/api/game/catalogue", params={"fresh": True}).json()
    assert next(i for i in cat["items"] if i["model"] == "MX204")["status"] == "verified"
    assert line not in [x["id"] for x in board(cl)["quests"] if x["status"] != "done"]


def test_an_empty_part_number_is_asked_about_and_leaving_it_writes_nothing_and_pays_nothing(tmp_path, monkeypatch):
    box = EmptyBox()
    site, dtype, rtr = part_box(box, "")
    cl = game(tmp_path, monkeypatch, box)
    line = f"q:device_type_part_number:device_type:{dtype['id']}"
    q = next(x for x in board(cl)["quests"] if x["id"] == line)
    assert q["purpose"].startswith("NetBox holds no part number for the Juniper MX204; the device-type library lists MX204.")
    assert q["step"]["choices"][2] == {"id": "none", "label": "Leave it empty in NetBox", "kind": "not_applicable"}
    # a label that says something the library knows nothing of: nothing is written
    q = say(cl, line, ("label", {"choice": "else"}), ("label_says", {"text": "MX204-HW-BASE-7"}))
    assert q["status"] == "parked" and q["note"] == "The label carries a part number the device-type library does not list, so the game wrote nothing."
    assert "A model is sold under more than one part number" in q["checklist"][0] and "record it in NetBox itself" in q["checklist"][1]
    xp = board(cl)["rewards"]["xp"]
    q = say(cl, line, ("label", {"choice": "none"}))
    assert q["status"] == "dismissed" and box.writes == [] and board(cl)["rewards"]["xp"] == xp
    assert box.row("dcim/device-types", dtype["id"])["part_number"] == ""


def test_a_part_number_is_asked_about_only_where_the_library_can_answer():
    b = Build()
    s = b.site()
    room = b.room(s)
    kinds = lambda: {f["id"] for f in findings.analyse(b.e).findings if f["kind"] == "device_type_part_number"}  # noqa: E731
    one = b.dev(s, "sw1", "switch", room, None, manufacturer="Juniper", model="EX2300-24P", tags=[])
    tid = 50
    t = b.e.dtypes[tid] = {"id": tid, "manufacturer": "Juniper", "model": "EX2300-24P", "slug": "juniper-ex2300-24p", "part_number": "EX2300-24P",
                           "u_height": 1.0, "tags": []}
    b.e.devices[one]["type_id"] = tid
    b.e.touch()
    assert kinds() == set()                                           # the library's own number
    t.update(part_number="ex2300 24p")
    b.e.touch()
    assert kinds() == set()                                           # the same number, spelled another way
    t.update(part_number="EX2300-24P-TAA")
    b.e.touch()
    assert kinds() == set()                                           # a number the library does not list: nothing says it is wrong
    t.update(part_number="")
    b.e.touch()
    assert kinds() == {f"device_type_part_number:device_type:{tid}"}  # none at all, and the library has one to offer
    t.update(part_number="EX2300-48P")
    b.e.touch()
    assert kinds() == {f"device_type_part_number:device_type:{tid}"}  # the number of another model of that maker
    t.update(tags=[setup.UNVERIFIED])
    b.e.touch()
    assert kinds() == set()                                           # marked unverified on purpose: not asked again
    t.update(tags=[], manufacturer="Harbour Works", model="HX-9")
    b.e.touch()
    assert kinds() == set()                                           # a model the library does not hold: nothing to check against


def test_set_part_number_writes_only_the_librarys_number():
    st = State()
    st.add(Rec("device_type", 5, None, {"manufacturer": "Juniper", "model": "EX2300-24P", "part_number": "WRONG", "u_height": 1.0, "full_depth": False,
                                        "tags": [], "_names": None}))
    st.add(Rec("device_type", 6, None, {"manufacturer": "Harbour Works", "model": "HX-9", "part_number": "", "u_height": 1.0, "full_depth": False,
                                        "tags": [], "_names": None}))
    op = lambda tid, part: {"op": "set_part_number", "target": {"kind": "device_type", "id": tid}, "fields": {"part_number": part}}  # noqa: E731
    out = planned(st, op(5, "EX2300-48P"), op(6, "HX-9-A"), op(7, "X"), op(5, "ex2300 24p"), op(5, "EX2300-24P"))
    assert out[0]["status"] == "error" and out[0]["error"]["details"]["expected"] == "EX2300-24P"
    assert out[1]["status"] == "error" and "lists no single part number for Harbour Works HX-9" in out[1]["error"]["message"]
    assert out[2]["status"] == "error" and out[2]["error"]["code"] == "not_found"
    assert (out[3]["status"], out[3]["before"], out[3]["after"]) == ("ok", {"part_number": "WRONG"}, {"part_number": "EX2300-24P"})
    assert out[4]["status"] == "no_change"


# ---------------------------------------------------------------- the catalogue and the store
def test_the_catalogue_is_derived_from_netbox_and_the_store_only_remembers_first_sightings(tmp_path, monkeypatch):
    box = EmptyBox()
    placeholder_box(box)
    maker = box.create("dcim/manufacturers", {"name": "Juniper", "slug": "juniper"})
    box.create("dcim/device-types", {"manufacturer": maker["id"], "model": "EX2300-24P", "slug": "juniper-ex2300-24p", "part_number": "EX2300-24P"})
    wrong = box.create("dcim/device-types", {"manufacturer": maker["id"], "model": "MX204", "slug": "juniper-mx204", "part_number": "EX2300-48P"})
    cl = game(tmp_path, monkeypatch, box)
    cat = cl.get("/api/game/catalogue").json()
    schema_validator("Catalogue").validate(cat)
    by_model = {i["model"]: i for i in cat["items"]}
    assert {m: i["status"] for m, i in by_model.items()} == {"EX-S": "unverified", "Unknown": "unverified", "EX2300-24P": "verified", "MX204": "mismatch"}
    assert by_model["MX204"]["note"] == ('NetBox holds the part number EX2300-48P, which the device-type library lists for the EX2300-48P. '
                                         '"The part number of ..." asks about it.')
    assert by_model["Unknown"]["note"].startswith("A placeholder, not a model") and by_model["EX-S"]["devices"] == 1
    assert cat["manufacturers"] == [{"name": "Example", "verified": 0, "total": 1}, {"name": "Generic", "verified": 0, "total": 1},
                                    {"name": "Juniper", "verified": 1, "total": 2}]
    first = by_model["EX2300-24P"]["first_seen"]
    # corrected in NetBox: the next read shows it; a model that left NetBox leaves the catalogue; first sightings are kept
    wrong["part_number"] = "MX204"
    box.data["dcim/device-types"] = [t for t in box.data["dcim/device-types"] if t["model"] != "Unknown" or True]
    time.sleep(0.01)
    cat = cl.get("/api/game/catalogue", params={"fresh": True}).json()
    by_model = {i["model"]: i for i in cat["items"]}
    assert by_model["MX204"]["status"] == "verified" and by_model["EX2300-24P"]["first_seen"] == first and cat["verified"] == 2
    # the kept rows can be thrown away: the catalogue is rebuilt from NetBox
    db = sqlite3.connect(tmp_path / "game.sqlite3")
    assert db.execute("SELECT count(*) FROM catalogue").fetchone()[0] == 4
    db.execute("DELETE FROM catalogue")
    db.commit()
    db.close()
    again = cl.get("/api/game/catalogue").json()
    assert [(i["model"], i["status"]) for i in again["items"]] == [(i["model"], i["status"]) for i in cat["items"]]
    assert cl.get("/api/game/catalogue").status_code == 200 and TestClient(base_url=LOCAL, app=cl.app).get("/api/game/catalogue").status_code == 401


def test_a_store_from_before_the_catalogue_gains_its_table_and_keeps_everything(tmp_path):
    path = tmp_path / "game.sqlite3"
    db = sqlite3.connect(path)
    assert migrate(db, MIGRATIONS[:4]) == 4
    db.execute("INSERT INTO player(user_id, username, display_name, created_at, seen_at) VALUES (7, 'walker', NULL, 'then', 'then')")
    db.execute("INSERT INTO quest(user_id, scope, quest_id, site_id, status, answers, checks, verified, snapshot, updated_at) "
               "VALUES (7, 'netbox', 'q:rack_audit:rack:1', 1, 'done', '{}', '[\"device:1:serial\"]', '[\"device:1:serial\"]', '{}', 'then')")
    db.execute("INSERT INTO reward(user_id, quest_id, kind, amount, signature, item, at) VALUES (7, 'q:rack_audit:rack:1', 'xp', 50, 'rack_audit:1', NULL, 'then')")
    db.commit()
    assert "catalogue" not in {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    db.close()
    st = GameStore(path)
    assert st.version == SCHEMA_VERSION == 5 and st.rewards(7)["xp"] == 50 and list(st.quests(7, "netbox")) == ["q:rack_audit:rack:1"]
    items = [{"key": "juniper|ex2300 24p", "manufacturer": "Juniper", "model": "EX2300-24P", "status": "unverified"}]
    seen = st.sync_catalogue(7, items)
    assert list(seen) == ["juniper|ex2300 24p"]
    assert st.sync_catalogue(7, [{**items[0], "status": "verified"}]) == seen                # the status follows NetBox; the first sighting stays
    assert st.sync_catalogue(7, []) == {}
    st.close()


def test_the_packs_route_says_what_ships_how_complete_it_is_and_where_it_came_from(tmp_path, monkeypatch):
    cl = game(tmp_path, monkeypatch, EmptyBox())
    body = TestClient(base_url=LOCAL, app=cl.app).get("/api/packs").json()       # no sign-in needed
    schema_validator("PackList").validate(body)
    assert body["errors"] == [] and body["library"]["import_from"] == "local" and len(body["library"]["commit"]) == 40
    assert [p["id"] for p in body["packs"]] == ["providers-de", "providers-fr", "providers-gb", "providers-ie", "providers-nl", "providers-us",
                                                "providers-international", "devices-community-library", "serials-network-vendors"]
    us = next(p for p in body["packs"] if p["id"] == "providers-us")
    assert us["partial"] and us["missing"] and us["coverage"].startswith("Partial.") and all(s["terms"] for s in us["sources"])
    dev = next(p for p in body["packs"] if p["kind"] == "devices")
    assert dev["entries"] > 16_000 and "CC0" in dev["sources"][0]["terms"]


# ---------------------------------------------------------------- who may write, and dry runs
def test_a_dry_run_proxy_plays_the_setup_lines_as_practice_and_writes_nothing(tmp_path, monkeypatch):
    box = EmptyBox()
    cl = game(tmp_path, monkeypatch, box, write_enabled=False)
    say(cl, FIRST_SITE, ("country", {"choice": "other"}), ("country_code", {"text": "be"}), ("site_name", {"text": "Quay House"}))
    v = post(cl, "apply", FIRST_SITE)
    assert v["mode"] == "dry_run" and v["xp_awarded"] == 0 and box.writes == [] and v["message"].startswith("Practice run, nothing was written")
    assert v["result"]["ops"][0]["after"] == {"name": "Quay House", "country": "BE"}


def test_each_setup_op_asks_for_everything_it_may_have_to_make():
    def need(op: dict[str, Any]) -> list[tuple[str, str]]:
        return perms.needs_of([op])
    assert need({"op": "create_site", "target": T("site"), "fields": {"name": "x", "country": "NL"}}) == [("dcim.site", "add"), ("dcim.region", "add")]
    assert need({"op": "create_provider", "target": T("provider"), "fields": {"name": "x", "asns": [{"asn": 1, "rir": "ripe"}]}}) == [
        ("circuits.provider", "add"), ("ipam.asn", "add"), ("ipam.rir", "add")]
    assert need({"op": "create_provider", "target": T("provider"), "fields": {"name": "x"}}) == [("circuits.provider", "add")]
    lib = need({"op": "add_device_type", "target": T("device_type"), "fields": {"library_file": EX2300}})
    assert lib[:2] == [("dcim.devicetype", "add"), ("dcim.manufacturer", "add")] and ("dcim.interfacetemplate", "add") in lib and len(lib) == 11
    assert need({"op": "add_device_type", "target": T("device_type"), "fields": {"manufacturer": "a", "model": "b"}})[-1] == ("extras.tag", "add")
    change = need({"op": "set_device_type", "target": T("device", 1), "fields": {"device_type_id": 2}})
    assert change[0] == ("dcim.device", "change") and ("dcim.interface", "add") in change
    assert need({"op": "mark_unverified", "target": T("device_type", 1), "fields": {"reason": "x"}}) == [("extras.tag", "add"), ("dcim.devicetype", "change")]


def test_a_player_without_the_netbox_permission_plays_the_first_site_as_practice(tmp_path, monkeypatch):
    box = EmptyBox()
    box.superuser = False
    cl = game(tmp_path, monkeypatch, box)
    q = say(cl, FIRST_SITE, ("country", {"choice": "country:GB"}), ("site_name", {"text": "Quay House"}))
    assert q["write_blocked"].startswith("Your NetBox user may not add dcim.site, add dcim.region.")
    v = post(cl, "apply", FIRST_SITE)
    assert v["mode"] == "dry_run" and box.writes == [] and v["xp_awarded"] == 0


# ---------------------------------------------------------------- the lines themselves
def test_the_setup_lines_say_what_is_missing_why_it_matters_and_what_the_admin_can_do_after():
    e = Estate()
    line = quests.lines(e, findings.analyse(e))[0]
    assert (line["id"], line["rank"], line["xp"], line["site_id"]) == (FIRST_SITE, "connectivity", 60, 0)
    assert line["purpose"] == "NetBox has no site yet. Everything in NetBox hangs off a site. Let's make the first one."
    assert line["outcome"] == "You can now walk to your first site on the green, and put its racks and devices in NetBox."
    b = Build()
    s = b.site("Quay House")
    kinds = {x["kind"]: x for x in quests.lines(b.e, findings.analyse(b.e))}
    assert list(kinds)[:3] == ["site_no_rack", "site_no_devices", "site_no_provider"]
    assert kinds["site_no_rack"]["purpose"].startswith("Quay House has no rack and no device in NetBox.")
    for line in kinds.values():
        assert line["outcome"].startswith("You can now") and line["finding"]["evidence"][0]["name"] == "Quay House"
        first = quests.evaluate(b.e, line, {})
        assert first["status"] == "asking" and any(ch["kind"] == "unknown" for ch in first["step"]["choices"]) and first["target"]["site_id"] == s
    # "there is no rack here" is an answer: the stage is answered, and the boxes are asked about next
    state = quests.evaluate(b.e, kinds["site_no_rack"], {"rack_kind": {"choice": "none"}})
    assert state["status"] == "dismissed" and state["note"] == "No rack: it stands on a shelf or on the wall"
    a = findings.analyse(b.e)
    ch = next(x for x in chains.build(b.e, a, quests.lines(b.e, a)) if x["kind"] == "setup")
    view = chains.view(b.e, a, ch, resolved={f"site_no_rack:site:{s}"})
    assert [st["status"] for st in view["stages"]] == ["answered", "open", "locked"] and view["label"] == "2 of 3: kit"


def test_setup_checks_do_not_make_an_established_site_look_more_complete():
    b = Build()
    s = b.site()
    rack = b.rack(s, b.room(s))
    one, two = b.dev(s, "sw1", rack=rack, tags=[]), b.dev(s, "sw2", rack=rack, position=12.0, tags=[])
    b.cable(b.p(one, "eth1"), b.p(two, "eth1"))
    a = findings.analyse(b.e)
    quiet = {c for c in a.checks[s] if c.split(":")[0] in setup.KINDS}
    assert quiet == {f"site_no_rack:site:{s}", f"site_no_devices:site:{s}", f"device_type_unknown:device:{one}", f"device_type_unknown:device:{two}",
                     f"site_more_devices:site:{s * setup.MORE_KEY + 1}"}          # "more than one box here": true, and never asked of a cabled site
    assert all(a.check(c) is True for c in quiet) and a.quiet == quiet | {"estate_no_site:estate:0"}
    assert a.completeness(s)["total"] == len(a.checks[s]) - len(quiet)
    assert not [x for x in chains.build(b.e, a, quests.lines(b.e, a)) if x["kind"] == "setup"]


def test_the_change_ops_of_the_setup_lines_are_in_the_contract():
    ops = {o["$ref"].rsplit("/", 1)[1] for o in json.loads((REPO / "contracts" / "scene.schema.json").read_text())["$defs"]["ChangeOp"]["oneOf"]}
    names = {"".join(w.capitalize() for w in op.split("_")) + "Op" for op in setupops.SETUP_OPS}
    assert names <= ops and set(setupops.SETUP_OPS) <= set(perms.NEEDS) and set(setupops.SETUP_OPS) <= set(changes.NET_OPS)
    docs = (REPO / "docs" / "packs.md").read_text() + (REPO / "docs" / "quests.md").read_text() + (REPO / "contracts" / "api.md").read_text()
    for op in setupops.SETUP_OPS:
        assert f"`{op}`" in docs, op
