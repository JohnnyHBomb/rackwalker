"""The setup companion's guards, offline: what a NetBox somebody has already worked in looks like to it (other
spellings, other scripts, part numbers of the box that was bought, models with no port on them), the rules a change set
meets with no quest in front of it, what is really written when something is marked unverified, what earns a bonus,
and how far a pack and an import are checked.

Nothing here reaches the internet: imports read tests/library (RACKWALKER_LIBRARY_DIR) and fetching is forbidden.
"""
from __future__ import annotations

import asyncio
import gzip
import hashlib
import json
import shutil
from pathlib import Path
from typing import Any

import httpx
import pytest

from rackwalker_proxy import changes, findings, packs, perms, questroutes, quests, setup, setupops
from rackwalker_proxy.changes import Rec, State
from rackwalker_proxy.country import ISO_CODES
from rackwalker_proxy.estate import Estate, SimError

from .conftest import REPO
from .empty_netbox import EmptyBox
from .test_quest_settlement import board, post
from .test_quests import Build
from .test_setup import CARRIER, EX2300, FIRST_SITE, KIT, LIBRARY, MX204, T, apply, audit_estate, broken, edit, game, near_empty, planned, say


@pytest.fixture(autouse=True)
def offline_library(monkeypatch):
    monkeypatch.setenv("RACKWALKER_LIBRARY_DIR", str(LIBRARY))
    monkeypatch.setenv("RACKWALKER_LIBRARY_FETCH", "0")
    packs._DEFINITIONS.clear()


def lib(maker: str, model: str, kind: str = "device") -> dict[str, Any]:
    row = packs.load().index().lookup(maker, model, kind)
    assert row is not None, (maker, model)
    return row


# ---------------------------------------------------------------- names: every script, every spelling
def test_a_name_in_another_script_is_itself_and_is_never_taken_for_another():
    held = [{"id": 1, "name": "Ростелеком", "slug": "rostelecom", "asns": []}]
    typed = {"name": "МТС", "slug": packs.slugify("МТС"), "asns": [], "aka": []}
    assert packs.norm("МТС") == "мтс" and packs.norm("Ростелеком") == "ростелеком"
    assert packs.match_provider(typed, held) is None and not packs.same_provider(typed, held[0])
    assert packs.match_provider({**typed, "name": "ростелеком"}, held)["id"] == 1               # the same name, in its own script
    assert not packs.same_name("NTT 東日本", "NTT 西日本") and packs.same_name("NTT 東日本", "ntt 東日本")
    assert packs.same_name("Télécom Zürich", "telecom zurich") and packs.same_name("Straße Netz", "strasse netz")
    # a name with nothing to compare is the same as nothing, not the same as every other such name
    assert not packs.same_name("???", "!!!") and not packs.same_name("", "") and not packs.same_name(None, "BT")
    # a slug NetBox takes, and one of its own per name
    slugs = {packs.slugify(n) for n in ("МТС", "Ростелеком", "東日本", "???")}
    assert len(slugs) == 4 and all(s.startswith("n-") and len(s) == 10 for s in slugs)
    assert packs.slugify("Harbour Wireless Co-op") == "harbour-wireless-co-op" and packs.slugify("Zürich Süd & Co") == "zurich-sud-and-co"
    # two providers typed in one change set, both without a Latin letter: two providers
    out = planned(State(), *({"op": "create_provider", "target": T("provider"), "fields": {"name": n}} for n in ("МТС", "Билайн", "мтс")))
    assert [o["status"] for o in out] == ["ok", "ok", "no_change"]


@pytest.mark.parametrize("a,b", [("AT & T", "AT&T"), ("Talk Talk", "TalkTalk"), ("TMobile", "T-Mobile"), ("City Fibre", "CityFibre"),
                                 ("Century Link", "CenturyLink"), ("1 & 1", "1&1"), ("BT Ltd.", "bt"), ("KPN B.V.", "kpn"), ("AT and T", "AT&T")])
def test_a_provider_spelled_with_other_spacing_is_the_same_provider(a, b):
    assert packs.same_name(a, b) and packs.same_name(b, a)
    assert packs.match_provider({"name": a, "slug": packs.slugify(a), "asns": [], "aka": []}, [{"id": 3, "name": b, "slug": "held", "asns": []}])["id"] == 3


@pytest.mark.parametrize("a,b", [("Sky UK", "Sky"), ("BT", "BT Ireland"), ("Three", "Three Ireland"), ("Vodafone", "Voda"), ("Canal+", "Canal")])
def test_two_providers_with_names_that_only_begin_alike_stay_two(a, b):
    assert not packs.same_name(a, b)


def test_a_provider_is_matched_by_its_slug_by_another_name_and_by_its_as_number_in_that_order():
    entry = {"name": "KPN", "slug": "kpn", "asns": [{"asn": 1136, "rir": "ripe"}], "aka": ["Royal KPN"]}
    by_slug = {"id": 1, "name": "Koninklijke", "slug": "kpn", "asns": []}
    by_aka = {"id": 2, "name": "Royal KPN N.V.", "slug": "royal", "asns": []}
    by_asn = {"id": 3, "name": "Dutch Telecom", "slug": "dt", "asns": [1136]}
    other = {"id": 4, "name": "Example Carrier", "slug": "example", "asns": [64500]}
    assert packs.match_provider(entry, [other, by_slug])["id"] == 1 and packs.same_provider(entry, by_slug)
    assert not packs.same_provider({**entry, "slug": None, "asns": []}, {**by_slug, "slug": None})       # no slug on either side is no match
    assert [x["id"] for x in packs.provider_matches(entry, [other, by_asn, by_aka, by_slug])] == [1, 2, 3]
    assert packs.provider_named(entry, [by_aka, by_asn]) and not packs.provider_named(entry, [by_asn]) and not packs.provider_named(entry, [other])
    # one network NetBox holds under two names: the one named like the entry is the nearer
    sky = packs.load().provider("providers-gb", "sky")
    two = [{"id": 8, "name": "Sky Ireland", "slug": "sky-ireland", "asns": [5607]}, {"id": 9, "name": "Sky UK", "slug": "sky-uk", "asns": [5607]}]
    assert [x["id"] for x in packs.provider_matches(sky, two)][0] == (9 if packs.provider_named(sky, two) else 8)


def way_out(box: EmptyBox, country: str = "gb") -> tuple[dict[str, Any], dict[str, Any], str]:
    site, dev = near_empty(box, country)
    return site, dev, f"q:site_no_way_out:site:{site['id']}"


def provider(box: EmptyBox, name: str, asn: int | None = None, slug: str | None = None) -> dict[str, Any]:
    ids = []
    if asn is not None:
        rir = box.data["ipam/rirs"][0] if box.data["ipam/rirs"] else box.create("ipam/rirs", {"name": "RIPE NCC", "slug": "ripe-ncc"})
        held = next((a for a in box.data["ipam/asns"] if a["asn"] == asn), None) or box.create("ipam/asns", {"asn": asn, "rir": rir["id"]})
        ids = [held["id"]]
    return box.create("circuits/providers", {"name": name, "slug": slug or packs.slugify(name), "asns": ids})


def test_a_typed_provider_in_another_script_is_made_as_itself_through_the_way_out_line(tmp_path, monkeypatch):
    box = EmptyBox()
    site, dev, way = way_out(box)
    held = provider(box, "Ростелеком", slug="rostelecom")
    cl = game(tmp_path, monkeypatch, box)
    q = say(cl, way, ("device", {"choice": f"device:{dev['id']}"}), ("provider", {"choice": "typed"}), ("provider_name", {"text": "МТС"}))
    assert q["step"]["id"] == "circuit_id" and q["step"]["prompt"].startswith("What is the circuit ID? МТС prints it")
    port = box.data["dcim/interfaces"][0]
    q = say(cl, way, ("circuit_id", {"text": "MTS-0001"}), ("circuit_type", {"choice": "new:0"}), ("local_port", {"choice": f"interface:{port['id']}"}),
            ("cable_type", {"choice": "cat6"}))
    ops = {o["op"]: o for o in q["proposal"]["ops"]}
    assert ops["create_provider"]["fields"] == {"name": "МТС"} and ops["create_circuit"]["fields"]["provider_id"] == "@provider"
    assert apply(cl, way)["complete"]
    made = next(p for p in box.data["circuits/providers"] if p["name"] == "МТС")
    assert made["id"] != held["id"] and made["slug"].startswith("n-") and box.data["circuits/circuits"][0]["provider"]["id"] == made["id"]


def test_whose_line_lists_the_admins_own_providers_before_the_packs_and_finds_them_by_any_of_their_names(tmp_path, monkeypatch):
    box = EmptyBox()
    site, dev, way = way_out(box, "us")
    for name in ("AT & T", "British Telecom", "KPN", "Talk Talk", "Ростелеком", "Zeta Net", "Yarrow Fibre"):
        provider(box, name)
    cl = game(tmp_path, monkeypatch, box)
    q = say(cl, way, ("device", {"choice": f"device:{dev['id']}"}))
    labels = [c["label"] for c in q["step"]["choices"]]
    # every provider NetBox holds is a choice (up to eight), in name order; then names of the US pack NetBox does not hold
    assert labels[:7] == ["AT & T", "British Telecom", "KPN", "Talk Talk", "Yarrow Fibre", "Zeta Net", "Ростелеком"]
    assert "AT&T" not in labels and labels[7:9] == ["Verizon", "Comcast Business"]
    assert labels[9:] == ["Another provider: search by name", "My provider is not listed", "I don't know yet"]
    # the search finds the admin's own under the pack's name for it, and does not offer the pack's entry beside it
    q = say(cl, way, ("provider", {"choice": "search"}), ("provider_find0", {"text": "BT"}))
    found = [c["label"] for c in q["step"]["choices"]]
    assert found[0] == "British Telecom (in your NetBox)" and "BT (GB)" not in found and "BT Ireland (IE)" in found
    q = say(cl, way, ("provider_pick0", {"choice": "again"}), ("provider_find1", {"text": "TalkTalk"}))
    found = [c["label"] for c in q["step"]["choices"]]
    assert found[0] == "Talk Talk (in your NetBox)" and "TalkTalk (GB)" not in found
    q = say(cl, way, ("provider_pick1", {"choice": "again"}), ("provider_find2", {"text": "at&t"}))
    assert [c["label"] for c in q["step"]["choices"]][0] == "AT & T (in your NetBox)"
    q = say(cl, way, ("provider_pick2", {"choice": q["step"]["choices"][0]["id"]}))
    assert q["step"]["id"] == "circuit_id" and "AT & T prints it" in q["step"]["prompt"]


def test_a_typed_pack_name_brings_the_packs_as_numbers_and_two_holders_of_one_as_number_are_asked_about(tmp_path, monkeypatch):
    box = EmptyBox()
    site, dev, way = way_out(box)
    cl = game(tmp_path, monkeypatch, box)
    q = say(cl, CARRIER.format(site["id"]), ("provider", {"choice": "typed"}), ("provider_name", {"text": "kpn"}))
    made = next(o for o in q["proposal"]["ops"] if o["op"] == "create_provider")["fields"]
    assert (made["name"], made["pack"], made["asns"]) == ("KPN", "providers-nl", [{"asn": 1136, "rir": "ripe"}])
    # NetBox holds one network under two names, neither of them the pack's: the player says which
    box2 = EmptyBox()
    site, dev, way = way_out(box2)
    a, b = provider(box2, "Harbour Media IE", 5607), provider(box2, "Harbour Media UK", 5607)
    cl = game(tmp_path / "two", monkeypatch, box2)
    q = say(cl, way, ("device", {"choice": f"device:{dev['id']}"}), ("provider", {"choice": "typed"}), ("provider_name", {"text": "Sky"}))
    assert q["step"]["id"] == "provider_which" and q["step"]["prompt"] == "NetBox holds 2 providers with an AS number of Sky. Which one is this line's?"
    assert [c["label"] for c in q["step"]["choices"]][:2] == ["Harbour Media IE", "Harbour Media UK"]
    q = say(cl, way, ("provider_which", {"choice": f"provider:{b['id']}"}))
    assert q["step"]["id"] == "circuit_id" and "Harbour Media UK prints it" in q["step"]["prompt"] and a["id"] != b["id"]


def test_a_site_with_no_country_says_so_and_offers_no_countrys_providers_first(tmp_path, monkeypatch):
    box = EmptyBox()
    site, dev, way = way_out(box)
    box.data["dcim/sites"][0]["region"] = None
    cl = game(tmp_path, monkeypatch, box)
    line = CARRIER.format(site["id"])
    q = next(x for x in board(cl)["quests"] if x["id"] == line)
    assert q["step"]["prompt"] == ("Whose line is it? (This site has no country on record, so no country's providers are offered first: search, "
                                   "or type the name.)")
    assert [c["label"] for c in q["step"]["choices"]] == ["Another provider: search by name", "My provider is not listed", "We do not keep carriers in NetBox",
                                                          "I don't know yet"]
    q = say(cl, line, ("provider", {"choice": "search"}))
    assert q["step"]["prompt"] == "Type the carrier's name, or its AS number. The game looks in NetBox and in the provider packs, then in every other pack."


def test_an_as_number_netbox_holds_on_no_provider_is_used_and_no_second_one_is_made(tmp_path, monkeypatch):
    box = EmptyBox()
    near_empty(box)
    rir = box.create("ipam/rirs", {"name": "RIPE", "slug": "ripe"})
    asn = box.create("ipam/asns", {"asn": 64512, "rir": rir["id"]})
    cl = game(tmp_path, monkeypatch, box)
    r = cl.post("/api/changes/apply", json={"ops": [{"op": "create_provider", "target": T("provider"),
                                                     "fields": {"name": "Probe Carrier", "asns": [{"asn": 64512, "rir": "ripe"}, {"asn": 64513, "rir": "arin"}]}}]})
    assert r.status_code == 200 and r.json()["applied"], r.text
    made = box.data["circuits/providers"][0]
    assert [a["asn"] for a in made["asns"]] == [64512, 64513] and made["asns"][0]["id"] == asn["id"]
    assert [a["asn"] for a in box.data["ipam/asns"]] == [64512, 64513] and [r["slug"] for r in box.data["ipam/rirs"]] == ["ripe", "arin"]


# ---------------------------------------------------------------- the device index: one model, one key
def test_models_that_differ_by_a_plus_sign_are_two_models():
    index = packs.load().index()
    assert index.lookup("HPE", "Aruba 2930F-24G-4SFP")["part_number"] == "JL259A" and index.lookup("HPE", "Aruba 2930F-24G-4SFP+")["part_number"] == "JL253A"
    assert index.lookup("Synology", "DS218")["model"] == "DS218" and index.lookup("Synology", "ds218+")["model"] == "DS218+"
    assert index.lookup("Cisco", "N9K-SUP-A", "module")["model"] == "N9K-SUP-A" and index.lookup("Cisco", "N9K-SUP-A+", "module")["model"] == "N9K-SUP-A+"
    # no two rows of the index share a key any more, whatever the next library commit brings: a lookup is one row
    for (kind, maker, model), rows in index._by_model.items():
        assert len(rows) == 1, (kind, maker, model, [r["model"] for r in rows])
    assert index.part_check("HPE", "Aruba 2930F-24G-4SFP", "JL259A")["status"] == "verified"
    assert index.part_check("HPE", "Aruba 2930F-24G-4SFP", "JL253A") == {"status": "mismatch", "expected": "JL259A", "other": "Aruba 2930F-24G-4SFP+"}
    assert [r["model"] for r in index.search("DS218+")][:1] == ["DS218+"] and index.search("DS218")[0]["model"] == "DS218"
    assert index.search("TS-231+")[0]["model"] == "TS-231+" and index.search("RS815+")[0]["model"] == "RS815+"
    # both in one change set: two imports, each device on its own model
    st = State()
    st.add(Rec("site", 1, None, {"name": "Plus Works", "slug": "plus-works"}))
    ops = []
    for n, model in enumerate(("Aruba 2930F-24G-4SFP", "Aruba 2930F-24G-4SFP+")):
        row = lib("HPE", model)
        st.add(Rec("x:libdef", f"device:{row['file']}", None, {"doc": asyncio.run(packs.definition(row)), "error": None}))
        ops += [{"op": "add_device_type", "target": T("device_type"), "client_ref": f"t{n}", "fields": {"library_file": row["file"]}},
                {"op": "create_device", "target": T("device"), "fields": {"name": f"pw-sw{n}", "site_id": 1, "role": "Switch", "device_type_id": f"@t{n}"}}]
    out = planned(st, *ops)
    assert [o["status"] for o in out] == ["ok"] * 4 and (out[0]["after"]["part_number"], out[2]["after"]["part_number"]) == ("JL259A", "JL253A")
    assert (out[0]["after"]["interfaces"], out[2]["after"]["interfaces"]) == (28, 29)


def test_a_maker_under_its_longer_name_and_a_model_under_its_part_number_are_the_librarys():
    index = packs.load().index()
    for maker in ("Juniper", "juniper networks", "Juniper Networks, Inc.", "JUNIPER"):
        assert index.lookup(maker, "EX2300-24P")["file"] == EX2300, maker
    assert packs.maker_key("Cisco Systems") == "cisco" and packs.maker_key("Hewlett Packard Enterprise") == "hpe" and packs.maker_key("Dell EMC") == "dell"
    assert packs.maker_key("Extreme Networks") == packs.maker_key("extreme") and packs.maker_key("Networks") == "networks"
    assert index.lookup("Arista Networks", "DCS-7050SX3-48YC8-F")["manufacturer"] == "Arista"
    assert index.lookup("Ubiquiti Networks", "USW-24-POE") is not None                  # the part number where NetBox has it as the model
    assert index.lookup("Cisco Systems", "WS-C2960X-24PS-L")["model"] == "Catalyst 2960X-24PS-L"
    assert index.lookup("HP", "Aruba 2930F-24G-4SFP+")["manufacturer"] == "HPE"         # an Aruba switch filed under HP in an older NetBox
    assert index.lookup("Juniper Networks", "No Such Model") is None and index.lookup("Nobody Networks", "EX2300-24P") is None
    assert index.lookup("Juniper", "") is None and index.lookup(None, None) is None
    # and so a type NetBox holds under such a name is the library's model, not a second one beside it
    row = lib("Juniper", "EX2300-24P")
    for held_maker, held_model, same in (("Juniper Networks", "EX2300-24P", True), ("Juniper", "ex2300 24p", True), ("Juniper", "EX2300-48P", False),
                                         ("Arista", "EX2300-24P", False)):
        rec = Rec("device_type", 5, None, {"manufacturer": held_maker, "model": held_model, "part_number": "", "u_height": 1.0, "full_depth": False,
                                           "tags": [], "_names": {"interfaces": ["ge-0/0/0"]}})
        assert setupops._same_type(rec, row["manufacturer"], row["model"], row) is same, (held_maker, held_model)
    st = State()
    st.add(Rec("x:manufacturer", 3, None, {"name": "Juniper Networks", "slug": "juniper-networks"}))
    st.add(Rec("device_type", 5, None, {"manufacturer": "Juniper Networks", "model": "EX2300-24P", "part_number": "", "u_height": 1.0, "full_depth": False,
                                        "tags": [], "_names": {"interfaces": ["ge-0/0/0"]}}))
    out = planned(st, {"op": "add_device_type", "target": T("device_type"), "fields": {"library_file": EX2300}},
                  {"op": "add_device_type", "target": T("device_type"), "fields": {"manufacturer": "juniper networks", "model": "Core-9"}})
    assert out[0]["status"] == "no_change" and out[0]["target"]["id"] == 5
    # a model recorded by hand goes under the manufacturer NetBox holds, by whichever of its names
    assert out[1]["status"] == "ok"


def test_a_model_netbox_holds_under_the_makers_longer_name_is_used_and_nothing_is_made_twice(tmp_path, monkeypatch):
    box = EmptyBox()
    site, _ = near_empty(box)
    maker = box.create("dcim/manufacturers", {"name": "Juniper Networks", "slug": "juniper-networks"})
    held = box.create("dcim/device-types", {"manufacturer": maker["id"], "model": "EX2300-24P", "slug": "ex2300-24p", "part_number": ""})
    box.create("dcim/interface-templates", {"device_type": held["id"], "name": "ge-0/0/0", "type": "1000base-t"})
    arista = box.create("dcim/manufacturers", {"name": "Arista Networks", "slug": "arista"})
    cl = game(tmp_path, monkeypatch, box)
    ops = [{"op": "add_device_type", "target": T("device_type"), "client_ref": "t", "fields": {"library_file": EX2300}},
           {"op": "add_device_type", "target": T("device_type"), "client_ref": "a", "fields": {"library_file": lib("Arista", "DCS-7050SX3-48YC8-F")["file"]}}]
    r = cl.post("/api/changes/apply", json={"ops": ops}).json()
    assert r["applied"] and [o["status"] for o in r["ops"]] == ["no_change", "ok"], r
    assert [m["name"] for m in box.data["dcim/manufacturers"]] == ["Example", "Juniper Networks", "Arista Networks"]      # no "Juniper", no second "Arista"
    made = box.data["dcim/device-types"][-1]
    assert made["manufacturer"]["id"] == arista["id"] and len([t for t in box.data["dcim/device-types"] if t["model"] == "EX2300-24P"]) == 1
    # the catalogue confirms it, and says how the library spells the maker
    cat = {i["model"]: i for i in cl.get("/api/game/catalogue", params={"fresh": True}).json()["items"]}
    assert (cat["EX2300-24P"]["status"], cat["EX2300-24P"]["note"]) == ("verified", "The device-type library files this maker as Juniper.")
    # and the search offers the one NetBox holds, as it stands there
    kit_site = box.create("dcim/sites", {"name": "Second Yard", "slug": "second-yard", "status": "active"})
    box.create("dcim/racks", {"site": kit_site["id"], "name": "S1", "status": "active", "u_height": 12})
    board(cl, fresh=True)
    q = say(cl, KIT.format(kit_site["id"]), ("box0_find0", {"text": "EX2300-24P"}))
    assert q["step"]["choices"][0]["label"] == "Juniper Networks EX2300-24P (in NetBox: 1 port)"
    q = say(cl, KIT.format(kit_site["id"]), ("box0_pick0", {"choice": q["step"]["choices"][0]["id"]}), ("role0", {"choice": f"role:{box.data['dcim/device-roles'][0]['id']}"}), ("box0_details", {"values": {"name": "sy-sw1"}}), ("more0", {"choice": "no"}))
    assert [o["op"] for o in q["proposal"]["ops"]] == ["create_device"] and q["proposal"]["ops"][0]["fields"]["device_type_id"] == held["id"]


def test_a_catalogue_entry_under_another_spelling_is_confirmed_and_says_what_the_library_calls_it():
    b = Build()
    s = b.site()
    types = [("Juniper Networks", "EX2300-24P", ""), ("HP", "Aruba 2930F-24G-4SFP+", "JL253A"), ("Cisco Systems", "WS-C2960X-24PS-L", ""),
             ("Ubiquiti Networks", "USW-24-POE", ""), ("Dell Inc.", "PowerEdge R740", ""), ("Harbour Works", "HX-9", ""), ("Juniper", "SRX300", "SRX300-SYS-JE")]
    for n, (maker, model, part) in enumerate(types, start=50):
        b.e.dtypes[n] = {"id": n, "manufacturer": maker, "model": model, "slug": f"t{n}", "part_number": part, "u_height": 1.0, "tags": [],
                         "templates": 0 if model == "EX2300-24P" else 5, "interfaces": 0 if model == "EX2300-24P" else 5}
    b.e.touch()
    cat = {(i["manufacturer"], i["model"]): i for i in setup.catalogue(b.e)["items"] if i["id"] >= 50}
    assert {k: v["status"] for k, v in cat.items()} == {**{(m, mo): "verified" for m, mo, _ in types}, ("Harbour Works", "HX-9"): "unverified"}
    assert cat[("Cisco Systems", "WS-C2960X-24PS-L")]["note"] == ("The device-type library calls this model Cisco Catalyst 2960X-24PS-L "
                                                                   "(WS-C2960X-24PS-L is its part number there).")
    assert cat[("HP", "Aruba 2930F-24G-4SFP+")]["note"] == "The device-type library files this maker as HPE."
    assert cat[("Juniper Networks", "EX2300-24P")]["note"] == ("The device-type library files this maker as Juniper. NetBox holds it without ports; the "
                                                               "library's has 32. Adding a box of this model brings them in.")
    assert cat[("Dell Inc.", "PowerEdge R740")]["note"] is None and cat[("Harbour Works", "HX-9")]["note"] == "Not in the device-type library."
    assert "SRX300-SYS-JE" in cat[("Juniper", "SRX300")]["note"] and "neither is called wrong" in cat[("Juniper", "SRX300")]["note"]
    assert all(len(v["note"] or "") <= 300 for v in cat.values())


@pytest.mark.parametrize("typed,first", [
    ("Cisco Catalyst 9300 Series", "Catalyst 9300"), ("EX2300-24P switch", "EX2300-24P"), ("WS-C2960X-48FPD-L V05", "Catalyst 2960X-48FPD-L"),
    ("JL256A#ABB", "Aruba 2930F-48G-PoE+-4SFP+"), ("USW-Pro-24-POE-EU", "UniFi Switch 24 Pro PoE"), ("sg350-28-k9-eu", "SG350-28"),
    ("FGT60F", "FortiGate 60F"), ("FGT-60F", "FortiGate 60F"), ("FG-60F", "FortiGate 60F"), ("Aruba Instant On 1930 24G", "Aruba 1930 24G"),
    ("3750", "ME-3750"), ("EX 2300-24P", "EX2300-24P"), ("juniper mx204", "MX204")])
def test_what_a_label_or_a_carton_says_finds_the_model(typed, first):
    hits = packs.load().index().search(typed, limit=5)
    assert hits and hits[0]["model"].startswith(first), (typed, [h["model"] for h in hits])
    assert all(h["manufacturer"] in ("Cisco", "Juniper", "HPE", "Ubiquiti", "Fortinet") for h in hits[:2])      # never a thermal camera first


def test_a_search_with_nothing_to_find_finds_nothing_and_a_providers_short_name_is_found():
    index = packs.load().index()
    assert index.search("zz no such model 9999") == [] and index.search("e") == [] and index.search("switch") != index.search("zzqq switch")
    assert index.search("series switch router") == []                                       # only words that say nothing
    p = packs.load()
    assert p.search_providers("3", "GB")[0]["name"] == "Three UK" and p.search_providers("tmobile", "US")[0]["name"] == "T-Mobile"
    assert p.search_providers("AS1136")[0]["name"] == "KPN" and p.search_providers("1136")[0]["name"] == "KPN" and p.search_providers("x") == []


# ---------------------------------------------------------------- "Generic" is a maker of the library, and a placeholder word
def test_a_model_the_library_files_under_generic_is_a_model_and_a_placeholder_is_still_a_placeholder():
    assert not setup.unknown_type("Generic", "24-port Copper Patch Panel") and setup.unknown_type("Generic", "Switch")
    assert setup.unknown_type("Generic", "Unknown") and setup.unknown_type(None, "TBD") and not setup.unknown_type("Juniper", "EX2300-24P")
    assert not setup.unknown_type("Harbour Works", "HX-9")                                  # unknown to the library, but a name somebody knew
    b = Build()
    s = b.site()
    room = b.room(s)
    panel = b.dev(s, "pw-pp1", "patch", room, None, manufacturer="Generic", model="24-port Copper Patch Panel", tags=[])
    blank = b.dev(s, "pw-box", "switch", room, None, manufacturer="Generic", model="Switch", tags=[])
    for n, d in ((60, panel), (61, blank)):
        dev = b.e.devices[d]
        b.e.dtypes[n] = {"id": n, "manufacturer": dev["manufacturer"], "model": dev["model"], "slug": f"t{n}", "part_number": "", "u_height": 1.0, "tags": []}
        dev["type_id"] = n
    b.e.touch()
    a = findings.analyse(b.e)
    assert {f["id"] for f in a.findings if f["kind"] == "device_type_unknown"} == {f"device_type_unknown:device:{blank}"}
    assert a.check(f"device_type_unknown:device:{panel}") is True
    cat = {i["model"]: i for i in setup.catalogue(b.e)["items"]}
    assert (cat["24-port Copper Patch Panel"]["status"], cat["24-port Copper Patch Panel"]["note"]) == ("verified", None)
    assert cat["Switch"]["status"] == "unverified" and cat["Switch"]["note"].startswith("A placeholder, not a model")
    line = {"kind": "site_no_devices", "site_id": s, "finding": {"data": {}}}
    assert setup.bonus(b.e, line, ["x"]) == (setup.TYPE_BONUS, "1 box of a model the device-type library confirms: +20 XP.")


# ---------------------------------------------------------------- serial rules reach as far as their source and no further
@pytest.mark.parametrize("maker,model,serial,status", [
    ("Cisco", "Meraki MR42", "Q2KD-ABCD-EFGH", "no_rule"), ("Cisco", "Meraki MX67", "Q2KY-ABCD-EFGH", "no_rule"), ("Cisco", "Meraki MS120-8", "12345", "no_rule"),
    ("Cisco", "Catalyst 9300-48P", "Q2KD-ABCD-EFGH", "suspect"), ("Cisco Systems", "Catalyst 9300-48P", "FOC2145X0ABC", "ok"),
    ("Fortinet", "FortiSwitch 248E-FPOE", "S248EFTF19004321", "no_rule"), ("Fortinet", "FortiSwitch 124E-POE", "S124EPTF18001234", "no_rule"),
    ("Fortinet", "FortiSwitch 248E-POE", "S248EPTF18001842", "no_rule"), ("Fortinet", "FortiGate 60F", "S248EPTF18001842", "suspect"),
    ("Dell", "Unity XT 380", "CKM00190800123", "no_rule"), ("Dell EMC", "PowerScale F710", "JACNT194540666", "no_rule"),
    ("Dell", "PowerSwitch S4048-ON", "CN0ABCDE1234567890AB", "no_rule"), ("Dell Inc.", "PowerEdge R740", "ABC1234", "ok"),
    ("Dell", "PowerEdge R740", "CN0ABCDE1234567890AB", "suspect"), ("Dell", "OptiPlex 7010", "1ABC234", "ok")])
def test_a_serial_rule_is_applied_only_to_the_product_lines_its_source_speaks_of(maker, model, serial, status):
    assert packs.load().serial_check(maker, model, serial)["status"] == status


def test_every_meraki_fortiswitch_and_dell_storage_model_of_the_library_is_outside_the_rules():
    p = packs.load()
    rows = p.index().rows["device"]
    outside = [r for r in rows if (r["manufacturer"] == "Cisco" and "meraki" in r["model"].lower())
               or (r["manufacturer"] == "Fortinet" and r["model"].startswith(("FortiSwitch", "FortiAP", "FortiAnalyzer")))
               or (r["manufacturer"] == "Dell" and not r["model"].startswith(("PowerEdge", "OptiPlex", "Latitude", "Precision")))]
    assert len(outside) > 100
    for r in outside:
        assert p.serial_check(r["manufacturer"], r["model"], "ANY-SERIAL-AT-ALL-12345")["status"] == "no_rule", r["model"]
    assert p.serial_check("Dell", "PowerEdge R740", "x" * 500)["status"] == "suspect"          # compared up to a bound, never at any length


def test_a_serial_rule_that_could_stall_the_proxy_is_refused_by_the_validator(tmp_path):
    serials = "serials/network-vendors.json"
    for bad in ("([A-Z0-9]+)+X", "(a|b+)*c", "([0-9]{2,}){3,}Z", "(?:(?:AB)*C)*"):
        errs = broken(tmp_path, lambda r, bad=bad: edit(r, serials, lambda d: d["vendors"][1]["rules"][0].update(pattern=bad)))
        assert any("repeats a group that itself repeats" in e for e in errs), (bad, errs)
        errs = broken(tmp_path, lambda r, bad=bad: edit(r, serials, lambda d: d["vendors"][1]["rules"][0].update(models=bad)))
        assert any("models repeats a group" in e for e in errs), (bad, errs)
    for fine in ("[A-Z0-9]{7}", "FG[A-Z0-9]{4,30}", "(?!.*Meraki)", "(.*(Smart-UPS|Back-UPS)|SMT|B[RXNC][0-9])", "[A-Z]{3}[0-9]{2}(0[1-9]|5[0-3])[A-Z0-9]{4,}"):
        assert not packs._nested_repeat(fine), fine
    # a `models` expression that does not compile is said by the validator, not found out when a player types a serial
    errs = broken(tmp_path, lambda r: edit(r, serials, lambda d: d["vendors"][0]["rules"][0].update(models="(Meraki")))
    assert any("models is not a regular expression" in e for e in errs), errs


# ---------------------------------------------------------------- the validator, rule by rule
def test_the_validator_refuses_what_only_it_can_see(tmp_path):
    gb, ie, serials, dev = "providers/gb.json", "providers/ie.json", "serials/network-vendors.json", "devices/community-library.json"

    def second_country(r: Path) -> None:
        doc = json.loads((r / gb).read_text())
        doc.update(id="providers-gbtwo")
        (r / "providers/gbtwo.json").write_text(json.dumps(doc))
    cases = {
        "is the file gb.json with the id providers-gb": lambda r: edit(r, gb, lambda d: d.update(id="providers-uk")),
        "already holds the pack of this country": second_country,
        "the name BT is used twice": lambda r: edit(r, gb, lambda d: d["providers"].append({**d["providers"][0], "slug": "bt-again", "asns": []})),
        "is listed under two vendors": lambda r: edit(r, serials, lambda d: d["vendors"][1]["manufacturers"].append("Cisco")),
        "'terms' is a required property": lambda r: edit(r, gb, lambda d: d["sources"][0].pop("terms")),
        "is not one of": lambda r: edit(r, gb, lambda d: d["providers"][0]["asns"][0].update(rir="internic")),
        "'source' is a required property": lambda r: edit(r, serials, lambda d: d["vendors"][0]["rules"][0].pop("source")),
        "does not match": lambda r: edit(r, dev, lambda d: d["library"].update(commit="main")),
        "raw_base": lambda r: edit(r, dev, lambda d: d["library"].update(raw_base="https://example.net/library/" + d["library"]["commit"] + "/")),
        "raw_base does not point at the pinned commit of the library's own repository": lambda r: edit(r, dev, lambda d: d["library"].update(
            raw_base=f"https://raw.githubusercontent.com/someone-else/not-the-library/{d['library']['commit']}/")),
    }
    for want, change in cases.items():
        errs = broken(tmp_path, change)
        assert any(want in e for e in errs), (want, errs)
    # every registry a pack may name is one the game can write
    schema = json.loads((REPO / "packs/schema/providers.schema.json").read_text())
    assert set(json.dumps(schema).split('"rir": {"enum": [')[1].split("]")[0].replace('"', "").replace(" ", "").split(",")) == set(packs.RIR_NAMES)


def test_a_pack_folder_with_nothing_in_it_is_not_valid_and_its_path_is_not_told_to_a_caller(tmp_path, monkeypatch, capsys):
    root = tmp_path / "secret-home-folder" / "packs"
    root.mkdir(parents=True)
    errs = packs.validate(root)
    assert len(errs) == 1 and errs[0].startswith("schema: the pack schemas could not be read") and "secret-home-folder" not in errs[0]
    shutil.copytree(REPO / "packs/schema", root / "schema")
    assert packs.validate(root) == ["no packs found in the pack folder"]
    # the command a maintainer runs says where, and fails
    assert packs.main(["packs", str(root)]) == 1 and "secret-home-folder" in capsys.readouterr().out
    assert packs.main(["packs"]) == 0
    # the route a signed-out caller may ask
    monkeypatch.setenv("RACKWALKER_PACKS_DIR", str(root))
    packs._load.cache_clear()
    try:
        box = EmptyBox()
        cl = game(tmp_path / "state", monkeypatch, box)
        body = cl.get("/api/packs").json()
        assert body["errors"] == ["no packs found in the pack folder"] and "secret-home-folder" not in json.dumps(body) and body["packs"] == []
    finally:
        monkeypatch.delenv("RACKWALKER_PACKS_DIR")
        packs._load.cache_clear()
        packs._index.cache_clear()


def reindex(root: Path, change) -> None:
    """Rewrites the device index of a copied pack folder and makes the pack name the new file (size and SHA-256), so
    only the rule under test is broken."""
    file = root / "devices/community-library.index.json.gz"
    data = json.loads(gzip.decompress(file.read_bytes()))
    change(data)
    raw = gzip.compress(json.dumps(data).encode(), mtime=0)
    file.write_bytes(raw)
    edit(root, "devices/community-library.json", lambda d: d["index"].update(bytes=len(raw), sha256=hashlib.sha256(raw).hexdigest()))


def test_the_device_index_is_the_file_its_pack_names_row_by_row(tmp_path):
    def same_size_other_bytes(r: Path) -> None:
        file = r / "devices/community-library.index.json.gz"
        raw = bytearray(file.read_bytes())
        raw[len(raw) // 2] ^= 0xFF
        file.write_bytes(bytes(raw))
    assert any("is not the file the pack names" in e for e in broken(tmp_path, same_size_other_bytes))
    assert broken(tmp_path, lambda r: reindex(r, lambda d: None)) == []                 # the same index, written again, is still valid
    cases = {
        "built from another commit or for another pack": lambda d: d.update(commit="0" * 40),
        "built from another commit or for another pack ": lambda d: d.update(of="devices-other"),
        "the pack says": lambda d: d["device_types"].pop(),
        "the pack says ": lambda d: d["module_types"].pop(),
        "the count of manufacturers differs": lambda d: d["manufacturers"].append("Nobody"),
        "rows of device_types are not whole": lambda d: d["device_types"][0].pop(),
        "rows of device_types are not whole ": lambda d: d["device_types"][3].__setitem__(-1, "0" * 63),
        "rows of device_types are not whole  ": lambda d: d["device_types"][3].__setitem__(-2, "../../outside.yaml"),
        "rows of module_types are not whole": lambda d: d["module_types"][3].__setitem__(-2, "/etc/passwd"),
        "rows of device_types are not whole   ": lambda d: d["device_types"][3].__setitem__(0, len(d["manufacturers"])),
        "rows of device_types are not whole    ": lambda d: d["device_types"][3].__setitem__(1, "  "),
    }
    for want, change in cases.items():
        errs = broken(tmp_path, lambda r, change=change: reindex(r, change))
        assert any(want.strip() in e for e in errs), (want, errs)


def test_a_definition_is_read_only_from_inside_the_local_copy_and_never_through_a_redirect(tmp_path, monkeypatch):
    row = dict(packs.load().index().by_file("device", EX2300))
    raw = (LIBRARY / "device-types" / EX2300).read_bytes()
    # a file outside the local copy, whatever its name in a (tampered) index and even with the right SHA-256
    local = tmp_path / "library"
    (local / "device-types").mkdir(parents=True)
    (tmp_path / "outside.yaml").write_bytes(raw)
    monkeypatch.setenv("RACKWALKER_LIBRARY_DIR", str(local))
    with pytest.raises(packs.LibraryUnavailable, match="no local copy"):
        asyncio.run(packs._definition({**row, "file": "../../outside.yaml"}))
    # over HTTPS: the pinned URL only, a redirect is not followed, anything but 200 is not used
    monkeypatch.delenv("RACKWALKER_LIBRARY_DIR")
    monkeypatch.setenv("RACKWALKER_LIBRARY_FETCH", "1")
    seen: list[str] = []

    def answer(status: int, **kw: Any):
        def handler(req: httpx.Request) -> httpx.Response:
            seen.append(str(req.url))
            if req.url.host != "raw.githubusercontent.com":
                return httpx.Response(200, content=raw)                   # where a redirect would lead: the right bytes, the wrong place
            return httpx.Response(status, **kw)
        return httpx.MockTransport(handler)
    for status, kw in ((302, {"headers": {"location": "https://elsewhere.example/EX2300-24P.yaml"}}), (404, {}), (500, {})):
        seen.clear()
        with pytest.raises(packs.LibraryUnavailable, match=f"answered {status}"):
            asyncio.run(packs.definition(row, answer(status, **kw)))
        lib_ = packs.load().device_pack()["library"]
        assert seen == [f"https://raw.githubusercontent.com/netbox-community/devicetype-library/{lib_['commit']}/device-types/Juniper/EX2300-24P.yaml"]
    assert packs._DEFINITIONS == {}                                         # nothing that failed is kept
    # read once, checked once, kept by its SHA-256: a second preview of the same import reads nothing
    seen.clear()
    doc = asyncio.run(packs.definition(row, answer(200, content=raw)))
    assert doc["model"] == "EX2300-24P" and len(seen) == 1
    assert asyncio.run(packs.definition(row, answer(500))) is doc and len(seen) == 1


def test_one_change_set_imports_a_bounded_number_of_models(tmp_path, monkeypatch):
    box = EmptyBox()
    near_empty(box)
    cl = game(tmp_path, monkeypatch, box)
    reads: list[str] = []
    real = packs._definition

    async def counted(row: dict[str, Any], transport: Any = None) -> dict[str, Any]:
        reads.append(row["file"])
        return await real(row, transport)
    monkeypatch.setattr(packs, "_definition", counted)
    files = [r["file"] for r in packs.load().index().rows["device"] if r["manufacturer"] == "Arista"][:setupops.MAX_IMPORTS + 4]
    ops = [{"op": "add_device_type", "target": T("device_type"), "fields": {"library_file": f}} for f in files]
    r = cl.post("/api/changes/preview", json={"ops": ops}).json()
    assert len(reads) == setupops.MAX_IMPORTS                                 # the rest were never looked for
    over = [o for o in r["ops"][setupops.MAX_IMPORTS:]]
    assert all(o["status"] == "error" and o["error"]["code"] == "library_unavailable" and "imports at most 8 models" in o["error"]["message"] for o in over)
    assert box.writes == []


# ---------------------------------------------------------------- a model NetBox holds with no port on it
def shell_box(box: EmptyBox) -> tuple[dict[str, Any], dict[str, Any]]:
    site = box.create("dcim/sites", {"name": "Power Works", "slug": "power-works", "status": "active"})
    box.create("dcim/racks", {"site": site["id"], "name": "PW-1", "status": "active", "u_height": 42})
    maker = box.create("dcim/manufacturers", {"name": "Juniper", "slug": "juniper"})
    shell = box.create("dcim/device-types", {"manufacturer": maker["id"], "model": "EX2300-24P", "slug": "ex2300-24p", "u_height": 1})
    return site, shell


def test_a_model_held_with_no_port_on_it_is_filled_from_the_library_and_the_new_box_gets_its_ports(tmp_path, monkeypatch):
    box = EmptyBox()
    site, shell = shell_box(box)
    cl = game(tmp_path, monkeypatch, box)
    kit = KIT.format(site["id"])
    q = say(cl, kit, ("box0_find0", {"text": "EX2300-24P"}))
    assert q["step"]["choices"][0]["label"] == "Juniper EX2300-24P (in NetBox: no ports)"
    q = say(cl, kit, ("box0_pick0", {"choice": q["step"]["choices"][0]["id"]}), ("role0", {"choice": "new:switch"}),
            ("box0_details", {"values": {"name": "pw-sw3", "position": 30}}), ("more0", {"choice": "no"}))
    assert [o["op"] for o in q["proposal"]["ops"]] == ["add_device_type", "create_device"]
    assert q["changes"][0] == ("NetBox holds the device type Juniper EX2300-24P with no ports: they are added from the device-type library "
                               "(32 interfaces, 1 console port, 1 power port). Nothing it has is changed; devices made from it before keep what they have.")
    pre = cl.post("/api/game/quests/preview", json={"scope": "netbox", "quest_id": kit}).json()
    assert (pre["ops"][0]["status"], pre["ops"][0]["target"]["id"], pre["ops"][0]["before"]["interfaces"], pre["ops"][0]["after"]["interfaces"]) == (
        "ok", shell["id"], 0, 32) and box.writes == []
    v = apply(cl, kit)
    assert v["complete"] and v["bonus_xp"] == 20
    assert len(box.data["dcim/device-types"]) == 1 and len(box.data["dcim/manufacturers"]) == 1          # filled, not made again
    assert len([t for t in box.data["dcim/interface-templates"] if t["device_type"]["id"] == shell["id"]]) == 32
    dev = box.data["dcim/devices"][0]
    assert dev["device_type"]["id"] == shell["id"] and len([p for p in box.data["dcim/interfaces"] if p["device"]["id"] == dev["id"]]) == 32
    # filled once: the next box of that model finds it whole
    assert cl.post("/api/changes/preview", json={"ops": [{"op": "add_device_type", "target": T("device_type"), "fields": {"library_file": EX2300}}]}
                   ).json()["ops"][0]["status"] == "no_change"


def test_a_model_held_with_ports_of_its_own_is_used_as_it_stands_and_one_without_is_not_filled_in_a_dry_run(tmp_path, monkeypatch):
    st = State()
    names = {key: [] for key, *_ in setupops.COMPONENTS}
    doc = asyncio.run(packs.definition(lib("Juniper", "EX2300-24P")))
    st.add(Rec("x:libdef", f"device:{EX2300}", None, {"doc": doc, "error": None}))
    st.add(Rec("device_type", 5, None, {"manufacturer": "Juniper", "model": "EX2300-24P", "part_number": "", "u_height": 1.0, "full_depth": False, "tags": [],
                                        "_names": {**names, "interfaces": ["Gi1"]}}))
    op = {"op": "add_device_type", "target": T("device_type"), "fields": {"library_file": EX2300}}
    assert planned(st, op)[0]["status"] == "no_change"                      # hand-made ports stay as they are: nothing is doubled
    st.recs[("device_type", 5)].v["_names"] = None                          # its templates were not read: never taken for empty
    assert planned(st, op)[0]["status"] == "no_change"
    st.recs[("device_type", 5)].v["_names"] = dict(names)
    out = planned(st, op, op)
    assert [o["status"] for o in out] == ["ok", "no_change"] and out[0]["after"]["interfaces"] == 32 and out[0]["target"]["id"] == 5
    # a dry run says the same, from the index, and reads nothing
    box = EmptyBox()
    site, shell = shell_box(box)
    cl = game(tmp_path, monkeypatch, box, write_enabled=False)
    kit = KIT.format(site["id"])
    q = say(cl, kit, ("box0_find0", {"text": "EX2300-24P"}))
    say(cl, kit, ("box0_pick0", {"choice": q["step"]["choices"][0]["id"]}), ("role0", {"choice": "new:switch"}),
        ("box0_details", {"values": {"name": "pw-sw3"}}), ("more0", {"choice": "no"}))
    v = post(cl, "apply", kit)
    assert v["mode"] == "dry_run" and box.writes == [] and v["result"]["ops"][0]["after"]["interfaces"] == 32 and v["result"]["ops"][0]["status"] == "ok"


# ---------------------------------------------------------------- the country a NetBox already holds
@pytest.mark.parametrize("name,slug", [("United Kingdom", "united-kingdom"), ("United Kingdom", "gb"), ("united kingdom", "uk-region")])
def test_the_first_site_goes_into_the_region_netbox_holds_for_its_country(tmp_path, monkeypatch, name, slug):
    box = EmptyBox()
    region = box.create("dcim/regions", {"name": name, "slug": slug})
    cl = game(tmp_path, monkeypatch, box)
    say(cl, FIRST_SITE, ("country_code", {"text": "GB"}), ("site_name", {"text": "Harbour Office"}))
    v = apply(cl, FIRST_SITE)
    assert v["complete"] and v["mode"] == "real"
    assert len(box.data["dcim/regions"]) == 1 and box.data["dcim/sites"][0]["region"]["id"] == region["id"]


@pytest.mark.parametrize("typed,code", [("UK", "GB"), ("uk", "GB"), ("be", "BE"), ("Belgium", "BE"), ("  japan ", "JP"), ("The Netherlands", "NL"), ("ZA", "ZA")])
def test_another_country_is_typed_by_name_or_by_its_code(typed, code):
    assert setup.country_typed(typed) == (code, None)


@pytest.mark.parametrize("typed", ["ZZ", "XX", "EU", "UN", "QQ", "AA", "Narnia", "B", "BEL", "12", ""])
def test_two_letters_that_are_no_countrys_code_make_no_region(typed):
    code, why = setup.country_typed(typed)
    assert code is None and why and "BE" in why


def test_the_country_codes_are_the_assigned_ones():
    assert len(ISO_CODES) == 249 and {"GB", "NL", "US", "DE", "FR", "IE", "JP", "ZA", "BE"} <= ISO_CODES and not {"UK", "EU", "ZZ", "XX", "UN"} & ISO_CODES
    assert set(setupops.COUNTRY_OF) <= ISO_CODES


def test_uk_typed_as_a_country_makes_the_region_united_kingdom(tmp_path, monkeypatch):
    box = EmptyBox()
    cl = game(tmp_path, monkeypatch, box)
    q = next(x for x in board(cl)["quests"] if x["id"] == FIRST_SITE)
    assert q["step"]["id"] == "country_code" and q["step"]["input"] == "text"
    assert q["step"]["prompt"] == "Which country is your first site in? Type its name, or its two-letter code (BE, JP)."
    bad = post(cl, "answer", FIRST_SITE, step="country_code", text="QQ")
    assert bad["step"]["id"] == "country_code" and bad["step"]["error"].startswith("That is not a country code the game knows.")
    say(cl, FIRST_SITE, ("country_code", {"text": "UK"}), ("site_name", {"text": "Head Office"}))
    assert apply(cl, FIRST_SITE)["complete"]
    assert [(r["name"], r["slug"]) for r in box.data["dcim/regions"]] == [("United Kingdom", "gb")]


# ---------------------------------------------------------------- what a change set meets with no quest in front of it
def site_state() -> State:
    st = State()
    st.add(Rec("site", 1, None, {"name": "Mill", "slug": "mill"}))
    st.add(Rec("site", 2, None, {"name": "Quay", "slug": "quay"}))
    st.add(Rec("x:site", 1, None, {"name": "Mill", "slug": "mill"}))
    st.add(Rec("rack", 3, None, {"name": "R1", "site_id": 1, "location_id": None, "u_height": 12, "starting_unit": 1}))
    st.add(Rec("rack", 4, None, {"name": "Q1", "site_id": 2, "location_id": None, "u_height": 12, "starting_unit": 1}))
    st.add(Rec("device_type", 5, None, {"manufacturer": "Juniper", "model": "EX2300-24P", "part_number": "EX2300-24P", "u_height": 1.0, "full_depth": False,
                                        "tags": [], "_names": {key: [] for key, *_ in setupops.COMPONENTS}}))
    st.add(Rec("device", 10, "2026-11-11T12:00:00.000000Z", {
        "site_id": 1, "location_id": None, "rack_id": 3, "position": 5.0, "face": "front", "name": "sw1", "status": "active", "serial": "S-1",
        "asset_tag": None, "_u_height": 1.0, "_full_depth": True, "_type_id": 1, "_model": "Unknown"}))
    st.add(Rec("x:components", 10, None, {key: [] for key, *_ in setupops.COMPONENTS}))
    return st


def device(**fields: Any) -> dict[str, Any]:
    return {"op": "create_device", "target": T("device"), "fields": {"name": "sw2", "site_id": 1, "role": "Switch", "device_type_id": 5, **fields}}


@pytest.mark.parametrize("op,code,says", [
    ({"op": "create_site", "target": T("site"), "fields": {"name": "mill"}}, "conflict", "NetBox already has a site called Mill"),
    ({"op": "create_site", "target": T("site"), "fields": {"name": "MILL!"}}, "conflict", "a site's name is unique"),         # the same slug
    (device(name="SW1"), "conflict", "Mill already has a device called SW1."),
    (device(rack_id=4), "validation_failed", "Rack Q1 is not at that site."),
    (device(rack_id=3, position=5), "conflict", "U5 of rack R1 is taken by sw1."),
    (device(rack_id=3, position=13), "validation_failed", "U13-U13 is outside rack R1 (U1-U12)."),
    (device(position=4), "validation_failed", "A unit needs a rack."),
    (device(serial="s-1"), "conflict", "sw1 already has the serial s-1: a serial number belongs to one device."),
    (device(site_id=9), "not_found", "Site 9 does not exist."),
    (device(device_type_id=99), "not_found", "99 does not exist."),
    (device(device_type_id="@nothing"), "not_found", "does not name an earlier op"),
    ({"op": "add_device_type", "target": T("device_type"), "fields": {"library_file": EX2300, "manufacturer": "Juniper", "model": "EX2300-24P"}},
     "validation_failed", "not both"),
    ({"op": "add_device_type", "target": T("device_type"), "fields": {"library_file": "Nobody/no-such-file.yaml"}}, "not_found", "has no device type"),
    ({"op": "add_device_type", "target": T("device_type"), "fields": {"manufacturer": "Harbour Works"}}, "validation_failed", "needs its manufacturer and its model"),
    ({"op": "add_device_type", "target": T("device_type"), "fields": {"library_file": MX204}}, "library_unavailable", "was not read from the device-type library"),
    ({"op": "add_module_type", "target": T("module_type"), "fields": {"library_file": EX2300}}, "not_found", "has no module type"),
])
def test_a_change_set_is_held_to_the_same_rules_as_a_quest(op, code, says):
    out = planned(site_state(), op)[0]
    assert out["status"] == "error" and out["error"]["code"] == code and says in out["error"]["message"], out["error"]


def test_what_is_allowed_is_allowed_and_two_devices_of_one_change_set_see_each_other():
    st = site_state()
    out = planned(st, device(rack_id=3, position=6, serial="S-2"), device(name="sw3", rack_id=3, position=6), device(name="sw3", serial="s-2"),
                  device(name="sw4", rack_id=3, position=12), device(name="sw2", site_id=2, rack_id=4, position=5))
    assert [o["status"] for o in out] == ["ok", "error", "error", "ok", "ok"]
    assert "U6 of rack R1 is taken by sw2" in out[1]["error"]["message"] and "sw2 already has the serial s-2" in out[2]["error"]["message"]
    # a model NetBox holds is used, not made again; the device that names it by reference gets the held one
    out = planned(st, {"op": "add_device_type", "target": T("device_type"), "client_ref": "t", "fields": {"library_file": EX2300}}, device(device_type_id="@t"))
    assert (out[0]["status"], out[0]["target"]["id"], out[1]["status"]) == ("no_change", 5, "ok")
    # a serial still on a record that has left service may be recorded on the box that took its place
    for status, want in (("decommissioning", "ok"), ("inventory", "ok"), ("offline", "ok"), ("planned", "error"), ("staged", "error"), ("active", "error")):
        st = site_state()
        st.recs[("device", 10)].v["status"] = status
        assert planned(st, device(serial="S-1"))[0]["status"] == want, status


def test_a_device_changed_in_netbox_since_the_question_does_not_get_another_model():
    st = site_state()
    change = {"op": "set_device_type", "target": T("device", 10), "fields": {"device_type_id": 5}}
    stale = planned(st, {**change, "if_unmodified_since": "2026-11-11T11:00:00.000000Z"})[0]
    assert stale["status"] == "error" and stale["error"]["code"] == "conflict"
    assert planned(st, {**change, "if_unmodified_since": "2026-11-11T12:00:00.000000Z"})[0]["status"] == "ok"
    assert planned(site_state(), change)[0]["status"] == "ok"
    # every op a quest sends for an existing device carries that time
    e = Estate()
    e.devices[10] = {"id": 10, "last_updated": "2026-11-11T12:00:00.000000Z"}
    assert quests.stamp(e, [change])[0]["if_unmodified_since"] == "2026-11-11T12:00:00.000000Z"


def test_a_model_whose_templates_netbox_refuses_is_taken_out_again(tmp_path, monkeypatch):
    box = EmptyBox()
    near_empty(box)
    box.refuse_post = ("dcim/interface-templates",)
    cl = game(tmp_path, monkeypatch, box)
    r = cl.post("/api/changes/apply", json={"ops": [{"op": "add_device_type", "target": T("device_type"), "fields": {"library_file": EX2300}}]}).json()
    assert not r["applied"] and r["ops"][0]["status"] == "error" and "NetBox refused a template of EX2300-24P" in r["ops"][0]["error"]["message"]
    assert "could not be removed" not in r["ops"][0]["error"]["message"]
    assert [t["model"] for t in box.data["dcim/device-types"]] == ["EX-S"]          # a model with half its ports is worse than no model
    assert [w[0] for w in box.writes if w[1].startswith("dcim/device-types/")] == ["DELETE"]
    # the same import afterwards starts clean
    box.refuse_post = ()
    r = cl.post("/api/changes/apply", json={"ops": [{"op": "add_device_type", "target": T("device_type"), "fields": {"library_file": EX2300}}]}).json()
    assert r["applied"] and len([t for t in box.data["dcim/interface-templates"] if t["device_type"]["model"] == "EX2300-24P"]) == 32


def test_a_part_number_is_written_as_the_library_spells_it_whatever_was_typed(tmp_path, monkeypatch):
    box = EmptyBox()
    near_empty(box)
    maker = box.create("dcim/manufacturers", {"name": "Juniper", "slug": "juniper"})
    dtype = box.create("dcim/device-types", {"manufacturer": maker["id"], "model": "MX204", "slug": "juniper-mx204", "part_number": "EX2300-24P"})
    cl = game(tmp_path, monkeypatch, box)
    op = {"op": "set_part_number", "target": T("device_type", dtype["id"]), "fields": {"part_number": "mx-204"}}
    r = cl.post("/api/changes/apply", json={"ops": [op]}).json()
    assert r["applied"] and box.row("dcim/device-types", dtype["id"])["part_number"] == "MX204"
    assert [w for w in box.writes if w[0] == "PATCH"][0][2]["part_number"] == "MX204"
    # the maker counts: another maker's part number is no ground for anything
    index = packs.load().index()
    assert index.by_part("Juniper", "mx 204")["model"] == "MX204" and index.by_part("Cisco", "MX204") is None and index.by_part("Juniper", "") is None
    assert index.part_check("Cisco", "ISR C1111-4P", "MX204")["status"] == "differs"
    # a library row with no part number of its own calls no typed number wrong
    bare = next(r for r in index.rows["device"] if not r["part_number"])
    assert index.part_check(bare["manufacturer"], bare["model"], "ANY-123")["status"] == "known"


def test_a_module_that_brings_a_part_the_device_already_has_is_refused_before_anything_is_written():
    st = State()
    st.add(Rec("x:bay", 4, None, {"name": "PSU0", "device_id": 10, "device": "sw1", "module": None}))
    st.add(Rec("module_type", 6, None, {"manufacturer": "Juniper", "model": "JPSU-150-AC-AFO", "part_number": "640-060603",
                                        "_names": {"power-ports": ["Power Supply 0"], "interfaces": ["{module}/0"]}}))
    comps = {key: [] for key, *_ in setupops.MODULE_COMPONENTS}
    st.add(Rec("x:components", 10, None, {**comps, "power-ports": [{"name": "Power Supply 0", "cable": False, "module": False}],
                                          "interfaces": [{"name": "{module}/0", "cable": False, "module": False}]}))
    op = {"op": "install_module", "target": T("module"), "fields": {"device_id": 10, "module_bay_id": 4, "module_type_id": 6}}
    out = planned(st, op)[0]
    assert out["status"] == "error" and out["error"]["code"] == "conflict"
    assert out["error"]["message"] == ("sw1 already has a power port Power Supply 0, and the JPSU-150-AC-AFO brings one of the same name: NetBox would "
                                       "refuse the module. Rename or remove it on the device in NetBox first.")
    st.recs[("x:components", 10)].v["power-ports"] = []
    assert planned(st, op)[0]["status"] == "ok"                              # a name NetBox fills in per bay collides with nothing


# ---------------------------------------------------------------- "marked unverified" is in NetBox, not only in the preview
def audit_box(box: EmptyBox) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], str]:
    site, _ = near_empty(box)
    rack = box.create("dcim/racks", {"site": site["id"], "name": "R1", "status": "active", "u_height": 12})
    maker = box.create("dcim/manufacturers", {"name": "Cisco", "slug": "cisco"})
    dtype = box.create("dcim/device-types", {"manufacturer": maker["id"], "model": "ISR C1111-4P", "slug": "cisco-isr-c1111-4p", "part_number": "C1111-4P"})
    keep = box.create("extras/tags", {"name": "Annex keep", "slug": "annex-keep"})
    role = box.data["dcim/device-roles"][0]["id"]
    one = box.create("dcim/devices", {"name": "rtr1", "site": site["id"], "rack": rack["id"], "position": 5, "face": "front", "role": role,
                                      "device_type": dtype["id"], "status": "active", "asset_tag": "A-1", "tags": [keep["id"]]})
    two = box.create("dcim/devices", {"name": "rtr2", "site": site["id"], "rack": rack["id"], "position": 7, "face": "front", "role": role,
                                      "device_type": dtype["id"], "status": "active", "asset_tag": "A-2"})
    return one, two, keep, f"q:rack_audit:rack:{rack['id']}"


def test_a_confirmed_odd_serial_is_written_with_its_mark_and_the_devices_own_tags_stay(tmp_path, monkeypatch):
    box = EmptyBox()
    one, two, keep, audit = audit_box(box)
    cl = game(tmp_path, monkeypatch, box)
    field = f"device:{one['id']}:serial"
    q = post(cl, "answer", audit, step="labels", values={field: "54321"})
    assert q["step"]["id"].startswith(f"serial{one['id']}_sure_")
    q = say(cl, audit, (q["step"]["id"], {"choice": "yes"}))
    assert [o["op"] for o in q["proposal"]["ops"]] == ["update_fields", "mark_unverified"]
    v = apply(cl, audit)
    assert v["mode"] == "real" and v["bonus_xp"] == 0 and "No bonus for a serial marked unverified." in v["message"]
    now = box.row("dcim/devices", one["id"])
    assert now["serial"] == "54321" and sorted(t["slug"] for t in now["tags"]) == ["annex-keep", "rw-unverified"]
    patch = [w for w in box.writes if w[0] == "PATCH" and "tags" in w[2]]
    tag = next(t for t in box.data["extras/tags"] if t["slug"] == "rw-unverified")
    assert len(patch) == 1 and patch[0][1] == f"dcim/devices/{one['id']}" and sorted(patch[0][2]["tags"]) == sorted([keep["id"], tag["id"]])
    assert box.row("dcim/devices", two["id"])["tags"] == []
    # marked once: the same answer about a device that carries the mark already writes no second one
    again = planned(State({("x:tags:device", one["id"]): Rec("x:tags:device", one["id"], None, {"tags": ["annex-keep", "rw-unverified"], "ids": [1, 2], "name": "rtr1"})}),
                    {"op": "mark_unverified", "target": T("device", one["id"]), "fields": {"reason": "x"}})[0]
    assert again["status"] == "no_change"
    gone = planned(State(), {"op": "mark_unverified", "target": T("device", 77), "fields": {"reason": "x"}})[0]
    assert gone["status"] == "error" and gone["error"]["code"] == "not_found"


def test_a_new_box_with_a_confirmed_odd_serial_is_made_with_the_mark_and_earns_no_bonus(tmp_path, monkeypatch):
    box = EmptyBox()
    site = box.create("dcim/sites", {"name": "Serial Shed", "slug": "serial-shed", "status": "active"})
    box.create("dcim/racks", {"site": site["id"], "name": "S1", "status": "active", "u_height": 12})
    cl = game(tmp_path, monkeypatch, box)
    kit = KIT.format(site["id"])
    c1111 = lib("Cisco", "ISR C1111-4P")
    q = say(cl, kit, ("box0_find0", {"text": "c1111-4p"}), ("box0_pick0", {"choice": f"lib:{c1111['n']}"}), ("role0", {"choice": "new:router"}))
    q = post(cl, "answer", kit, step="box0_details", values={"name": "ss-rtr1", "serial": "12345"})
    assert q["step"]["id"].startswith("box0_sure_") and "12345 does not look like a Cisco serial" in q["step"]["prompt"]          # the serial of a new box is checked
    skip = post(cl, "answer", kit, step=q["step"]["id"], choice="skip")
    assert skip["step"]["id"] == "more0"
    left_out = say(cl, kit, ("more0", {"choice": "no"}))
    assert "serial" not in left_out["proposal"]["ops"][-1]["fields"] and "unverified" not in left_out["proposal"]["ops"][-1]["fields"]
    q = say(cl, kit, (q["step"]["id"], {"choice": "yes"}), ("more0", {"choice": "yes"}),
            ("box1_find0", {"text": "ex2300-24p"}), ("box1_pick0", {"choice": f"lib:{lib('Juniper', 'EX2300-24P')['n']}"}), ("role1", {"choice": "new:switch"}),
            ("box1_details", {"values": {"name": "ss-sw1", "serial": "anything"}}), ("more1", {"choice": "no"}))
    made = [o["fields"] for o in q["proposal"]["ops"] if o["op"] == "create_device"]
    assert (made[0]["serial"], made[0].get("unverified"), made[1].get("unverified")) == ("12345", True, None)
    assert q["changes"][1].endswith("serial 12345 (marked unverified: it does not look like the vendor's).")
    assert ("extras.tag", "add") in perms.needs_of(q["proposal"]["ops"])
    v = apply(cl, kit)
    devs = {d["name"]: d for d in box.data["dcim/devices"]}
    assert [t["slug"] for t in devs["ss-rtr1"]["tags"]] == ["rw-unverified"] and devs["ss-rtr1"]["serial"] == "12345" and devs["ss-sw1"]["tags"] == []
    # one box earns the bonus; the marked one earns none, whatever its model
    assert v["bonus_xp"] == setup.TYPE_BONUS and "1 box of a model the device-type library confirms: +20 XP. No bonus for a box marked unverified." in v["message"]


def test_a_serial_bonus_is_for_a_serial_that_fits_its_rule_and_only_for_what_this_apply_confirmed(tmp_path, monkeypatch):
    e, line = audit_estate()
    one = next(d for d in e.devices.values() if d["name"] == "rtr1")
    cid = f"device:{one['id']}:serial"
    one.update(serial="12345", tags=[])                      # fails the vendor's rule, and nobody marked it: still no bonus
    assert setup.bonus(e, line, [cid]) == (0, None)
    one.update(serial="FGL214600ZZ")
    assert setup.bonus(e, {**line, "kind": "shelf_audit"}, [cid]) == (setup.SERIAL_BONUS, "1 serial fits the vendor's own pattern: +10 XP.")
    assert setup.bonus(e, {**line, "kind": "platform_survey"}, [cid]) == (0, None)
    assert setup.bonus(e, line, [f"device:{one['id']}:asset_tag", "device:999:serial", "rack:1:serial"]) == (0, None)
    # a model tagged unverified is not "confirmed by the library", whatever its name
    t = {"manufacturer": "Juniper", "model": "EX2300-24P", "part_number": "EX2300-24P", "tags": [setup.UNVERIFIED]}
    assert setup.verified_type(t) == "unverified" and setup.verified_type({**t, "tags": []}) == "verified" and setup.verified_type(None) == "unverified"
    # two serials of one rack in two applies: each apply pays for what it confirmed, not for the line's whole history
    box = EmptyBox()
    one, two, _, audit = audit_box(box)
    cl = game(tmp_path, monkeypatch, box)
    post(cl, "answer", audit, step="labels", values={f"device:{one['id']}:serial": "FGL214600ZZ"})
    first = apply(cl, audit)
    assert first["bonus_xp"] == setup.SERIAL_BONUS and not first["complete"]
    post(cl, "answer", audit, step="labels", values={f"device:{two['id']}:serial": "FGL214600ZY"})
    second = apply(cl, audit)
    assert second["bonus_xp"] == setup.SERIAL_BONUS and second["complete"] and "1 serial fits the vendor's own pattern: +10 XP." in second["message"]


# ---------------------------------------------------------------- a practice run never writes, and a real run is called one
SITE_ONLY = [{"object_types": ["dcim.site", "dcim.region"], "actions": ["add"]}]


def test_a_player_who_may_only_add_sites_is_told_the_first_site_will_be_written_and_it_is(tmp_path, monkeypatch):
    box = EmptyBox()
    box.superuser, box.permissions = False, SITE_ONLY
    cl = game(tmp_path, monkeypatch, box)
    b = board(cl)
    assert b["can_write"] is True and b["write_blocked"].startswith("Your NetBox user may not add cables,") and "Lines that need that run as practice" in b["write_blocked"]
    q = say(cl, FIRST_SITE, ("country_code", {"text": "NL"}), ("site_name", {"text": "Button Yard"}))
    assert q["status"] == "ready" and q["write_blocked"] is None             # the line's own verdict: this one is written
    v = post(cl, "apply", FIRST_SITE)
    assert v["mode"] == "real" and v["xp_awarded"] == 60 and [s["name"] for s in box.data["dcim/sites"]] == ["Button Yard"]
    # the next line needs a permission this player lacks: it says so before the button is pressed, and writes nothing
    sid = box.data["dcim/sites"][0]["id"]
    rack = f"q:site_no_rack:site:{sid}"
    q = say(cl, rack, ("rack_kind", {"choice": "u:12"}), ("rack_name", {"text": "BY-1"}))
    assert q["write_blocked"].startswith("Your NetBox user may not add dcim.rack.")
    writes = len(box.writes)
    v = post(cl, "apply", rack)
    assert v["mode"] == "dry_run" and v["xp_awarded"] == 0 and len(box.writes) == writes and box.data["dcim/racks"] == []


def test_the_quest_log_calls_every_line_practice_only_when_none_would_be_written():
    def gate(*grants: tuple[str, str]) -> questroutes.Gate:
        return questroutes.Gate(None, perms.Permissions(False, set(grants), set()))
    assert gate().board()[0] is False and "every line runs as practice" in gate().board()[1]
    # one permission of any op a quest may send is enough for "some lines are written"
    for need in perms.ANY_QUEST_NEED:
        can, why = gate(need).board()
        assert can is True and (why is None or "Lines that need that run as practice" in why), need
    assert questroutes.Gate(None, perms.Permissions(True, set(), set())).board() == (True, None)
    assert questroutes.Gate("The proxy runs in dry-run mode.", None).board() == (False, "The proxy runs in dry-run mode.")
    # every op the game can send is in that list, with everything it may have to make
    ops = [{"op": name, "target": {"kind": "device", "id": 1}, "fields": {"country": "NL", "asns": [{"asn": 1, "rir": "ripe"}], "library_file": "x",
                                                                           "unverified": True, "model_default": "local"}} for name in perms.NEEDS]
    ops += [{"op": "add_device_type", "target": T("device_type"), "fields": {"manufacturer": "a", "model": "b"}},
            *({"op": "update_fields", "target": {"kind": k, "id": 1}, "fields": {}} for k in perms.UPDATE_TYPE)]
    assert set(perms.needs_of(ops)) <= set(perms.ANY_QUEST_NEED)
    assert set(setupops.SETUP_OPS) <= set(perms.NEEDS) and set(changes.NET_OPS) <= set(perms.NEEDS)


# ---------------------------------------------------------------- more boxes, while a site is being set up
def kit_box(box: EmptyBox) -> tuple[dict[str, Any], str]:
    site = box.create("dcim/sites", {"name": "Mill Lane", "slug": "mill-lane", "status": "active"})
    box.create("dcim/racks", {"site": site["id"], "name": "ML-1", "status": "active", "u_height": 12})
    return site, KIT.format(site["id"])


def add_box(cl, line: str, n: int, model: tuple[str, str], name: str, more: str, role: str = "Switch", **values: Any) -> dict[str, Any]:
    q = say(cl, line, (f"box{n}_find0", {"text": model[1]}), (f"box{n}_pick0", {"choice": f"lib:{lib(*model)['n']}"}))
    job = next(c["id"] for c in q["step"]["choices"] if c["label"] == role)
    return say(cl, line, (f"role{n}", {"choice": job}), (f"box{n}_details", {"values": {"name": name, **values}}), *([(f"more{n}", {"choice": more})] if more else []))


def test_after_the_first_boxes_the_next_ones_are_offered_until_the_player_says_that_is_all(tmp_path, monkeypatch):
    box = EmptyBox()
    site, kit = kit_box(box)
    cl = game(tmp_path, monkeypatch, box)
    add_box(cl, kit, 0, ("Juniper", "EX2300-24P"), "ml-sw1", "no")
    assert apply(cl, kit)["complete"]
    b = board(cl, fresh=True)
    more = next(q for q in b["quests"] if q["kind"] == "site_more_devices")
    key = site["id"] * setup.MORE_KEY + 1
    assert (more["id"], more["title"], more["status"], more["rank"]) == (f"q:site_more_devices:site:{key}", "More boxes at Mill Lane", "offered", "identity")
    assert more["purpose"].startswith("NetBox holds 1 device at Mill Lane. Only you know whether that is all of them.")
    assert [(c["label"], c["kind"]) for c in more["step"]["choices"]] == [("Yes: add it", "candidate"), ("No: NetBox holds every box at Mill Lane", "not_applicable"),
                                                                           ("I don't know yet", "unknown")]
    # optional: it is no part of the site's completeness
    a = findings.analyse(questroutes_estate(cl))
    assert f"site_more_devices:site:{key}" in a.quiet and a.check(f"site_more_devices:site:{key}") is False
    # ---- two more boxes, one of a model NetBox now holds and one imported
    say(cl, more["id"], ("more", {"choice": "yes"}))
    add_box(cl, more["id"], 0, ("Juniper", "EX2300-24P"), "ml-sw2", "yes")
    q = add_box(cl, more["id"], 1, ("Juniper", "MX204"), "ml-rtr1", "no", role="Router")
    assert [o["op"] for o in q["proposal"]["ops"]] == ["create_device", "add_device_type", "create_device"]
    v = apply(cl, more["id"])
    # the bonus is for the two boxes this line added, not for the one that stood there before
    assert v["complete"] and v["xp_awarded"] == 40 + 2 * setup.TYPE_BONUS and v["bonus_xp"] == 2 * setup.TYPE_BONUS
    assert "2 boxes of a model the device-type library confirms: +40 XP." in v["message"] and len(box.data["dcim/devices"]) == 3
    # ---- offered again for the site as it stands; less XP for the same kind of fix again
    b = board(cl, fresh=True)
    again = next(q for q in b["quests"] if q["kind"] == "site_more_devices" and q["status"] == "offered")
    assert again["id"] == f"q:site_more_devices:site:{site['id'] * setup.MORE_KEY + 3}" and again["xp"] == 40
    say(cl, again["id"], ("more", {"choice": "yes"}))
    add_box(cl, again["id"], 0, ("Juniper", "EX2300-24P"), "ml-sw3", "no")
    v = apply(cl, again["id"])
    assert v["complete"] and 0 < v["xp_awarded"] - v["bonus_xp"] < 40
    # ---- "that is all": closed with nothing written and nothing paid, and not asked again while nothing changes
    b = board(cl, fresh=True)
    last = next(q for q in b["quests"] if q["kind"] == "site_more_devices" and q["status"] == "offered")
    writes, xp = len(box.writes), b["rewards"]["xp"]
    q = say(cl, last["id"], ("more", {"choice": "none"}))
    assert q["status"] == "dismissed" and len(box.writes) == writes
    b = board(cl, fresh=True)
    assert b["rewards"]["xp"] == xp and not [q for q in b["quests"] if q["kind"] == "site_more_devices" and q["status"] == "offered"]


def questroutes_estate(cl) -> Estate:
    from .test_chains import questroutes_estate as get

    return get(cl)


def test_more_boxes_is_asked_only_of_a_site_that_is_still_being_set_up():
    def kinds(b: Build) -> list[str]:
        b.e.touch()
        return [f["id"] for f in findings.analyse(b.e).findings if f["kind"] == "site_more_devices"]
    b = Build()
    s = b.site()
    room = b.room(s)
    assert kinds(b) == []                                                   # no device yet: "the first boxes" asks
    devs = [b.dev(s, f"sw{n}", room=room, tags=[]) for n in range(setup.STARTER_DEVICES - 1)]
    assert kinds(b) == [f"site_more_devices:site:{s * setup.MORE_KEY + 11}"]
    a = findings.analyse(b.e)
    assert all(a.check(f"site_more_devices:site:{s * setup.MORE_KEY + k}") is True for k in range(1, 11))      # earlier counts: there is more than that
    extra = b.dev(s, "sw-last", room=room, tags=[])
    assert kinds(b) == []                                                   # twelve devices: an established site
    del b.e.devices[extra]
    cable = b.cable(b.p(devs[0], "eth1"), b.p(devs[1], "eth1"))
    assert kinds(b) == []                                                   # cabling has begun: the kit is in
    del b.e.cables[cable]
    for p in b.e.ports.values():
        p["cable_id"] = None
    assert len(kinds(b)) == 1
    b.e.synthetic = True
    assert kinds(b) == []                                                   # the practice island is made up: nothing is added to it


# ---------------------------------------------------------------- onboarding answers at their edges
def test_a_starter_netbox_is_one_with_few_devices_and_nothing_connected():
    b = Build()
    s = b.site()
    room = b.room(s)
    for n in range(setup.STARTER_DEVICES):
        b.dev(s, f"sw{n}", room=room, tags=[])
    b.e.touch()
    assert setup.STARTER_DEVICES == 12 and setup.is_starter(b.e)
    one = b.dev(s, "one-more", room=room, tags=[])
    b.e.touch()
    assert not setup.is_starter(b.e)                                        # thirteen devices is an estate, not a start
    del b.e.devices[one]
    b.e.providers[1] = {"id": 1, "name": "Carrier", "slug": "carrier", "asns": []}
    b.e.touch()
    assert not setup.is_starter(b.e)


def test_the_first_rack_and_the_first_boxes_refuse_what_cannot_be(tmp_path, monkeypatch):
    box = EmptyBox()
    site = box.create("dcim/sites", {"name": "Mill Lane", "slug": "mill-lane", "status": "active"})
    cl = game(tmp_path, monkeypatch, box)
    rack = f"q:site_no_rack:site:{site['id']}"
    say(cl, rack, ("rack_kind", {"choice": "other"}))
    for bad in ("0", "101", "-3", "twelve", "4.5"):
        q = post(cl, "answer", rack, step="rack_height", text=bad)
        assert q["step"]["id"] == "rack_height" and q["step"]["error"] == "A whole number from 1 to 100.", bad
    say(cl, rack, ("rack_height", {"text": "9"}), ("rack_name", {"text": "ML-1"}))
    assert apply(cl, rack)["complete"] and box.data["dcim/racks"][0]["u_height"] == 9
    kit = KIT.format(site["id"])
    ex = lib("Juniper", "EX2300-24P")
    say(cl, kit, ("box0_find0", {"text": "ex2300-24p"}), ("box0_pick0", {"choice": f"lib:{ex['n']}"}), ("role0", {"choice": "new:switch"}))
    for values, why in (({"name": "  ", "serial": "X"}, "It needs a name"), ({"name": "ml-sw1", "position": 10}, "ML-1"), ({"serial": "X"}, "It needs a name")):
        q = post(cl, "answer", kit, step="box0_details", values=values)
        assert q["step"]["id"] == "box0_details" and why in (q["step"]["error"] or ""), (values, q["step"]["error"], [f.get("error") for f in q["step"]["fields"]])
    say(cl, kit, ("box0_details", {"values": {"name": "ml-sw1", "position": 9, "serial": "JN-1"}}), ("more0", {"choice": "yes"}),
        ("box1_find0", {"text": "ex2300-24p"}), ("box1_pick0", {"choice": f"lib:{ex['n']}"}), ("role1", {"choice": "new:switch"}))
    # the second box of the pass: not the first one's name, unit or serial
    for values, why in (({"name": "ML-SW1"}, "Mill Lane already has a device called ML-SW1."), ({"name": "ml-sw2", "position": 9}, "is taken by the box you added before"),
                        ({"name": "ml-sw2", "serial": "jn-1"}, "is the serial of another box in this list")):
        q = post(cl, "answer", kit, step="box1_details", values=values)
        assert q["step"]["id"] == "box1_details" and why in q["step"]["error"], (values, q["step"])
    q = say(cl, kit, ("box1_details", {"values": {"name": "ml-sw2", "position": 8}}), ("more1", {"choice": "no"}))
    assert [o["op"] for o in q["proposal"]["ops"]] == ["add_device_type", "create_device", "create_device"]          # one model, imported once
    assert apply(cl, kit)["complete"]


def test_a_placeholder_name_is_made_of_words_that_say_nobody_knew():
    for word in sorted(packs.PLACEHOLDER_WORDS):
        assert packs.is_placeholder(word) and packs.is_placeholder(word.upper() + " device") and packs.is_placeholder(f"{word} model 2"), word
    assert packs.is_placeholder("Unknown Catalyst") and packs.is_placeholder("vendor") and packs.is_placeholder("box")
    assert not packs.is_placeholder("Catalyst unknown") and not packs.is_placeholder("Temperature Probe 2") and not packs.is_placeholder("9300")


# ---------------------------------------------------------------- a dry run keeps to the rules of a real one
def sim(e: Estate, op: str, fields: dict[str, Any], target: dict[str, Any] | None = None) -> dict[str, Any]:
    res: dict[str, Any] = {"status": "ok", "target": dict(target or {"kind": "x", "id": None})}
    assert setupops.simulate(e, {"op": op, "target": target or {"kind": "x", "id": None}}, fields, res, {}, lambda v, kind: v)
    return res


def test_a_dry_run_refuses_what_a_real_run_refuses():
    b = Build()
    s = b.site()
    e = b.e
    e.providers[7] = {"id": 7, "name": "Royal KPN", "slug": "royal-kpn", "asns": [1136]}
    kpn = {"name": "KPN", "slug": "kpn", "asns": [{"asn": 1136, "rir": "ripe"}]}
    res = sim(e, "create_provider", kpn)
    assert res["status"] == "no_change" and res["target"]["id"] == 7 and len(e.providers) == 1          # the provider NetBox holds, not a new one
    assert sim(e, "create_provider", {"name": "МТС"})["status"] == "ok" and len(e.providers) == 2
    e.dtypes[5] = {"id": 5, "manufacturer": "Juniper", "model": "MX204", "slug": "mx204", "part_number": "EX2300-24P", "u_height": 1.0, "tags": []}
    with pytest.raises(SimError, match="does not list WRONG for Juniper MX204"):
        sim(e, "set_part_number", {"part_number": "WRONG"}, {"kind": "device_type", "id": 5})
    assert e.dtypes[5]["part_number"] == "EX2300-24P"
    assert sim(e, "set_part_number", {"part_number": "mx 204"}, {"kind": "device_type", "id": 5})["after"] == {"part_number": "MX204"}
    assert sim(e, "set_part_number", {"part_number": "MX204"}, {"kind": "device_type", "id": 5})["status"] == "no_change"
    hand = {"manufacturer": "Juniper", "model": "EX2300-24P", "part_number": "EX2300-48P"}
    with pytest.raises(SimError, match="as the part number of the Juniper EX2300-48P"):
        sim(e, "add_device_type", hand)
    with pytest.raises(SimError, match="pick it from the search"):
        sim(e, "add_device_type", {**hand, "part_number": ""})
    with pytest.raises(SimError, match="is the part number of the Juniper EX2300-24P"):
        sim(e, "add_device_type", {"manufacturer": "Juniper", "model": "Core switch", "part_number": "EX2300-24P"})
    n = len(e.dtypes)
    assert sim(e, "add_device_type", {"manufacturer": "Harbour Works", "model": "HX-9"})["after"]["unverified"] is True and len(e.dtypes) == n + 1
    # a device is not built on a type held under another model's part number, in a dry run either
    e.dtypes[5]["part_number"] = "EX2300-24P"
    with pytest.raises(SimError, match="which the device-type library lists for the EX2300-24P"):
        sim(e, "create_device", {"name": "x1", "site_id": s, "role": "Router", "device_type_id": 5})
    with pytest.raises(SimError, match="already has a device called"):
        b.dev(s, "sw1", tags=[])
        e.dtypes[5]["part_number"] = "MX204"
        sim(e, "create_device", {"name": "SW1", "site_id": s, "role": "Router", "device_type_id": 5})


# ---------------------------------------------------------------- the packs keep up with who owns what
@pytest.mark.parametrize("typed,country,name", [
    ("Asiera", "IE", "Asiera"), ("HEAnet", "IE", "Asiera"), ("AS1213", "IE", "Asiera"), ("Quantum Fiber", "US", "AT&T"), ("GFiber", "US", "Google Fiber"),
    ("Uniti", "US", "Kinetic (Windstream)"), ("XS4ALL", "NL", "KPN"), ("Speed Fibre Group", "IE", "Enet"), ("AS19901", "US", "Brightspeed"),
    ("AS34177", "FR", "Celeste"), ("703", None, "Verizon Business")])
def test_a_provider_is_found_under_the_name_it_goes_by_now_and_the_one_it_went_by(typed, country, name):
    hits = packs.load().search_providers(typed, country)
    assert hits and hits[0]["name"] == name, (typed, [h["name"] for h in hits])


def test_a_provider_that_changed_hands_says_so_where_the_admin_reads_it():
    p = packs.load()
    said = {("providers-us", "frontier"): "Verizon", ("providers-us", "cox-business"): "Charter", ("providers-us", "crown-castle-fiber"): "Zayo",
            ("providers-us", "lumen"): "AT&T", ("providers-us", "kinetic-windstream"): "Uniti", ("providers-ie", "heanet"): "HEAnet",
            ("providers-ie", "bt-ireland"): "Speed Fibre Group", ("providers-ie", "enet"): "BT Ireland", ("providers-gb", "three-uk"): "Vodafone"}
    for (pack, slug), word in said.items():
        entry = p.provider(pack, slug)
        assert entry is not None and word in (entry["notes"] or ""), (pack, slug, entry and entry["notes"])
    # an AS number that is registered and not announced is said to be so, not passed off as the network's own
    quiet = {("providers-gb", "ee"): 12576, ("providers-nl", "ziggo"): 9143, ("providers-nl", "vodafone-nl"): 15480, ("providers-de", "vodafone-de"): 31334,
             ("providers-fr", "sfr"): 12626, ("providers-ie", "three-ie"): 34218}
    for (pack, slug), asn in quiet.items():
        entry = p.provider(pack, slug)
        assert f"AS{asn}" in entry["notes"] and "not announced" in entry["notes"], (slug, entry["notes"])
    assert "four mobile network operators" not in p.provider_pack("GB")["coverage"]
    assert not any("HEAnet" in m or "Brightspeed" in m or "Celeste" in m for pack in p.providers for m in pack["missing"])


def test_the_schema_alone_keeps_the_library_pinned_to_a_commit_of_a_raw_file_host(tmp_path):
    validator = packs._validators(REPO / "packs")["devices"]
    doc = json.loads((REPO / "packs/devices/community-library.json").read_text())
    assert list(validator.iter_errors(doc)) == []
    for field, bad in (("raw_base", "https://example.net/library/" + doc["library"]["commit"] + "/"), ("raw_base", "http://raw.githubusercontent.com/a/b/main/"),
                       ("commit", "main"), ("commit", doc["library"]["commit"][:12]), ("repo", "https://gitlab.com/a/b")):
        assert list(validator.iter_errors({**doc, "library": {**doc["library"], field: bad}})), (field, bad)


def test_the_port_a_maker_names_for_the_way_out_is_offered_first():
    def port(n: int, name: str, kind: str = "1000base-t", mgmt: bool = False) -> dict[str, Any]:
        return {"id": n, "name": name, "type": kind, "mgmt_only": mgmt}
    mx67 = [port(1, "Internet 1"), port(2, "Port 2"), port(3, "Port 3"), port(4, "Port 4"), port(5, "Port 5"), port(6, "USB", "other")]
    assert [p["name"] for p in quests.likely_uplinks(mx67)] == ["Internet 1", "Port 5", "Port 4", "Port 3", "Port 2", "USB"]
    edge = [port(1, "ge-0/0/0"), port(2, "ge-0/0/1"), port(3, "xe-0/1/0", "10gbase-x-sfpp"), port(4, "WAN2"), port(5, "WAN1"), port(6, "fxp0", mgmt=True),
            port(7, "Console"), port(8, "wlan0"), port(9, "GigabitEthernet0/1 uplink")]
    assert [p["name"] for p in quests.likely_uplinks(edge)] == ["WAN2", "WAN1", "GigabitEthernet0/1 uplink", "xe-0/1/0", "wlan0", "ge-0/0/1", "ge-0/0/0",
                                                                "Console", "fxp0"]


# ---------------------------------------------------------------- what an import carries over
def test_an_import_keeps_bridge_membership_and_comments_and_a_pack_provider_says_what_kind_it_is(tmp_path, monkeypatch):
    box = EmptyBox()
    near_empty(box)
    cl = game(tmp_path, monkeypatch, box)
    pdu = lib("Eaton", "EMIB08")
    kpn = packs.load().provider("providers-nl", "kpn")
    r = cl.post("/api/changes/apply", json={"ops": [
        {"op": "add_device_type", "target": T("device_type"), "fields": {"library_file": pdu["file"]}},
        {"op": "create_provider", "target": T("provider"), "fields": {"name": kpn["name"], "slug": kpn["slug"], "asns": kpn["asns"], "pack": "providers-nl"}},
        {"op": "create_provider", "target": T("provider"), "fields": {"name": "Harbour Wireless Co-op"}}]}).json()
    assert r["applied"], r
    made = next(t for t in box.data["dcim/device-types"] if t["model"] == "EMIB08")
    assert made["comments"].startswith("Eaton Metered Input rack PDU")
    ifaces = {t["name"]: t for t in box.data["dcim/interface-templates"] if t["device_type"]["id"] == made["id"]}
    assert set(ifaces) == {"Network", "Daisy Chain", "Bridge 0"}
    assert ifaces["Network"]["bridge"] == ifaces["Bridge 0"]["id"] == ifaces["Daisy Chain"]["bridge"] and ifaces["Bridge 0"].get("bridge") is None
    by_name = {p["name"]: p for p in box.data["circuits/providers"]}
    assert by_name["KPN"]["description"] == "National carrier" and "description" not in by_name["Harbour Wireless Co-op"]
    assert r["ops"][1]["after"]["description"] == "National carrier"


def test_a_tall_box_is_not_placed_where_it_would_stick_out_of_the_rack(tmp_path, monkeypatch):
    box = EmptyBox()
    site, kit = kit_box(box)                    # a rack of 12 units
    cl = game(tmp_path, monkeypatch, box)
    q = say(cl, kit, ("box0_find0", {"text": "SR650"}), ("box0_pick0", {"choice": f"lib:{lib('Lenovo', 'ThinkSystem SR650')['n']}"}))
    say(cl, kit, ("role0", {"choice": next(c["id"] for c in q["step"]["choices"] if c["label"] == "Server")}))
    q = post(cl, "answer", kit, step="box0_details", values={"name": "ml-srv1", "position": 12})
    assert q["step"]["id"] == "box0_details" and q["step"]["error"] == "Rack ML-1 has 12 units; a box of 2U does not fit at U12."
    q = say(cl, kit, ("box0_details", {"values": {"name": "ml-srv1", "position": 11}}), ("more0", {"choice": "no"}))
    assert q["proposal"]["ops"][-1]["fields"]["position"] == 11
    # the library lists this model's part number as a pattern: the import leaves the part number empty
    assert "part number not given" in q["changes"][0]
    assert apply(cl, kit)["complete"] and box.data["dcim/device-types"][0]["part_number"] == ""
