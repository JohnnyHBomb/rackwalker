"""The provider packs as a set of some fifty countries: what must hold across packs, and how the game finds a
provider among them (the country of a site, the choices it is offered, the search, names in other scripts)."""
from __future__ import annotations

import json
from collections import defaultdict
from typing import Any

import pytest

from rackwalker_proxy import country, packs, setup, setupops
from rackwalker_proxy.country import ISO_CODES

from .empty_netbox import EmptyBox
from .test_quest_settlement import board
from .test_setup import CARRIER, FIRST_SITE, apply, broken, edit, game, say
from .test_setup_guards import provider, way_out

OTHER_SCRIPTS = ("JP", "KR", "CN", "TW", "HK", "TH", "GR", "BG")


def latin(text: str) -> bool:
    return all(ord(ch) < 0x250 for ch in text)


# ---------------------------------------------------------------- what holds across the packs
def test_every_packs_country_is_an_assigned_iso_code_and_has_a_name_the_game_reads_back():
    p = packs.load()
    assert p.errors == [] and len(p.countries()) == len(set(p.countries())) == 49
    for pack in p.providers:
        cc = pack["country"]
        if cc is None:
            assert pack["id"] == "providers-international"
            continue
        assert cc in ISO_CODES and pack["id"] == f"providers-{cc.lower()}", pack["id"]
        # the name the game shows, and gives the region it makes, reads back as that country: typed, or as a region's name
        name = setupops.COUNTRY_OF[cc]
        assert name != cc and setup.country_typed(name) == (cc, None) and country.code_of(name) == cc, cc
        assert country.site_country(None, packs.slugify(name), name, None) == cc and name in pack["title"], cc


def test_no_as_number_is_listed_under_two_registries_and_one_network_keeps_its_numbers_in_every_pack():
    every = packs.load().all_providers()
    held: dict[int, set[str]] = defaultdict(set)
    for x in every:
        for a in x["asns"]:
            held[a["asn"]].add(a["rir"])
    assert len(held) > 700 and {asn: rirs for asn, rirs in held.items() if len(rirs) > 1} == {}
    # one network that several packs list under one slug and with one AS number: every such entry is the same provider
    by_slug: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for x in every:
        by_slug[x["slug"]].append(x)
    for slug in ("colt", "starlink", "eunetworks", "eurofiber", "cogent", "gtt", "arelion", "hurricane-electric"):
        xs = by_slug[slug]
        assert len(xs) > 1 and len({json.dumps(sorted(a["asn"] for a in x["asns"])) for x in xs}) == 1 and len({x["kind"] for x in xs}) == 1, slug
    # a group that trades in several countries has a network of its own in each: no AS number is shared between them
    for word in ("Vodafone", "Orange", "Telia", "Telenor", "Tele2", "A1"):
        group = [x for x in every if x["country"] and x["name"].split()[0] == word]
        numbers = [a["asn"] for x in group for a in x["asns"]]
        assert len(group) >= 3 and len(numbers) == len(set(numbers)), word


def test_the_validator_refuses_an_unassigned_country_code_and_an_as_number_under_two_registries(tmp_path):
    def unassigned(r) -> None:
        edit(r, "providers/gb.json", lambda d: d.update(country="QQ", id="providers-qq"))
        (r / "providers/gb.json").rename(r / "providers/qq.json")
    errs = broken(tmp_path, unassigned)
    assert errs == ["providers/qq.json: QQ is not an assigned ISO 3166-1 alpha-2 country code"]
    # Starlink's AS14593 is in many packs under ARIN: one pack that says RIPE is refused, and the line names both
    errs = broken(tmp_path, lambda r: edit(r, "providers/nz.json", lambda d: next(x for x in d["providers"] if x["slug"] == "starlink")["asns"][0].update(rir="ripe")))
    assert len(errs) >= 1 and all("AS14593 is under" in e and "one registry holds an AS number" in e for e in errs), errs


# ---------------------------------------------------------------- the country of a site, and its pack
@pytest.mark.parametrize("region_name,slug,cc,first", [
    ("Japan", "japan", "JP", "NTT東日本"), ("JP", "jp", "JP", "NTT東日本"), ("South Korea", "south-korea", "KR", "KT"),
    ("Taiwan", "taiwan", "TW", "中華電信"), ("Hong Kong", "hong-kong", "HK", "HKT"), ("Türkiye", "turkiye", "TR", "Türk Telekom"),
    ("Turkey", "turkey", "TR", "Türk Telekom"), ("Bulgaria", "bulgaria", "BG", "A1 Bulgaria"), ("España", "espana", "ES", "Movistar"),
    ("Viet Nam", "viet-nam", "VN", "Viettel")])
def test_a_site_whose_region_names_its_country_is_offered_that_countrys_pack(tmp_path, monkeypatch, region_name, slug, cc, first):
    box = EmptyBox()
    site, dev, way = way_out(box)
    for held in (box.data["dcim/regions"][0], box.data["dcim/sites"][0]["region"]):
        held.update(name=region_name, slug=slug)
    cl = game(tmp_path, monkeypatch, box)
    q = next(x for x in board(cl)["quests"] if x["id"] == CARRIER.format(site["id"]))
    assert q["step"]["prompt"] == "Whose line is it?"
    labels = [c["label"] for c in q["step"]["choices"]]
    pack = [x["name"] for x in packs.load().providers_of(cc)]
    assert labels[0] == first == pack[0] and labels[:6] == pack[:6]
    assert labels[6:8] == ["Another provider: search by name", "My provider is not listed"]


def test_the_choice_of_a_carrier_stays_one_short_list_whatever_the_size_of_the_pack(tmp_path, monkeypatch):
    p = packs.load()
    assert max(len(pack["providers"]) for pack in p.providers) > 30
    for n, cc in enumerate(("US", "JP", "IN", "MT")):
        box = EmptyBox()
        site, dev, way = way_out(box, cc.lower())
        cl = game(tmp_path / str(n), monkeypatch, box)
        q = next(x for x in board(cl)["quests"] if x["id"] == CARRIER.format(site["id"]))
        picks = [c for c in q["step"]["choices"] if c["id"].startswith("pack:")]
        # six names of the pack, most common first; the rest of the pack is reached by the search, never by a longer list
        assert len(picks) == min(6, len(p.providers_of(cc))) and len(q["step"]["choices"]) <= 10, cc
        assert all(len(c["label"]) <= 48 for c in q["step"]["choices"])


def test_the_first_site_asks_for_the_country_by_name_or_code_and_knows_every_country_with_a_pack(tmp_path, monkeypatch):
    box = EmptyBox()
    cl = game(tmp_path, monkeypatch, box)
    q = next(x for x in board(cl)["quests"] if x["id"] == FIRST_SITE)
    # one typed answer: no six of fifty countries are put before the others
    assert q["step"]["id"] == "country_code" and q["step"]["input"] == "text" and q["step"]["choices"] == [{"id": "unknown", "label": "I don't know yet", "kind": "unknown"}]
    q = say(cl, FIRST_SITE, ("country_code", {"text": "türkiye"}), ("site_name", {"text": "Liman Ofisi"}))
    assert q["proposal"]["ops"][0]["fields"] == {"name": "Liman Ofisi", "country": "TR"}
    assert apply(cl, FIRST_SITE)["complete"]
    assert [(r["name"], r["slug"]) for r in box.data["dcim/regions"]] == [("Türkiye", "tr")]
    for cc in packs.load().countries():
        assert setup.country_typed(cc.lower()) == (cc, None) and setup.country_typed(setupops.COUNTRY_OF[cc].upper()) == (cc, None)
    assert setup.country_typed("Korea")[0] is None       # two countries: the code says which


# ---------------------------------------------------------------- the search across fifty packs
def found(text: str, cc: str | None, limit: int = 6) -> list[str]:
    return [f"{x['name']} ({x['country'] or 'international'})" for x in packs.load().search_providers(text, cc, limit)]


def test_the_search_puts_the_sites_country_first_then_the_backbone_names_then_other_countries():
    assert found("Vodafone", "PT")[:2] == ["Vodafone Portugal (PT)", "Vodafone Carrier Services (international)"]
    assert found("Vodafone", "ZA")[0] == "Vodafone Carrier Services (international)" and len(found("Vodafone", "ZA")) == 6
    assert found("Telia", "NO")[:2] == ["Telia (NO)", "Arelion (international)"]
    # a backbone that a dozen country packs list is one line, and it is the international entry where the site's own pack lacks it
    assert found("Colt", "JP") == ["Colt Technology Services (international)"] and found("Colt", "DE") == ["Colt Technology Services (DE)"]
    assert found("Starlink", "TH") == ["Starlink (international)"] and found("AS14593", "NZ") == ["Starlink (NZ)"]
    # a domestic network and the group's backbone under one name: the site's own first, the backbone elsewhere
    assert found("Tata Communications", "IN") == ["Tata Communications (IN)"] and found("Tata Communications", "GB") == ["Tata Communications (international)"]
    assert found("AS4755", "GB") == ["Tata Communications (IN)"] and found("AS6453", "IN") == ["Tata Communications (international)"]


def test_two_countries_networks_of_one_name_are_two_lines_and_a_typed_name_takes_neither(tmp_path, monkeypatch):
    assert found("Telenet", "ZA") == ["Telenet (BE)", "Telenet (LV)"] and found("Telenet", "LV")[0] == "Telenet (LV)"
    assert found("TIM", "AR")[:2] == ["TIM (BR)", "TIM (IT)"] and found("Epic", None) == ["Epic (CY)", "Epic (MT)"]
    assert {"GlobalConnect (DK)", "GlobalConnect (NO)", "GlobalConnect (SE)"} <= set(found("GlobalConnect", "DE"))
    for n, (cc, name, want) in enumerate((
            ("ar", "TIM", {"name": "TIM"}),                                                     # Brazil's or Italy's: nobody can say
            ("it", "tim", {"name": "TIM", "slug": "tim", "pack": "providers-it", "asns": [{"asn": 3269, "rir": "ripe"}]}),
            ("ar", "kpn", {"name": "KPN", "slug": "kpn", "pack": "providers-nl", "asns": [{"asn": 1136, "rir": "ripe"}]}),   # one pack lists it
            ("lv", "Telenet", {"name": "Telenet", "slug": "telenet-lv", "pack": "providers-lv", "asns": [{"asn": 24589, "rir": "ripe"}]}))):
        box = EmptyBox()
        site, dev, way = way_out(box, cc)
        cl = game(tmp_path / str(n), monkeypatch, box)
        q = say(cl, CARRIER.format(site["id"]), ("provider", {"choice": "typed"}), ("provider_name", {"text": name}))
        made = next(o for o in q["proposal"]["ops"] if o["op"] == "create_provider")["fields"]
        assert {k: v for k, v in made.items() if k != "aka"} == want, (cc, name)


def test_two_or_three_latin_letters_find_the_start_of_a_word_and_not_the_middle_of_fifty_countries_names():
    assert found("AIS", "TH") == ["AIS (TH)"] and found("KT", "KR")[0] == "KT (KR)" and all("(KR)" in x for x in found("KT", "KR"))
    assert found("ote", "GR") == ["Telekom (GR)", "OTEGlobe (GR)"]
    assert found("telekom", "DE")[0] == "Deutsche Telekom (DE)" and "Magyar Telekom (HU)" in found("telekom", "DE")
    assert "Deutsche Glasfaser (DE)" in found("glasfaser", "DE") and found("net", "GB") != []        # a longer text is found inside a word
    assert found("3", "HK")[0] == "3 Hong Kong (HK)" and found("x", "GB") == []


# ---------------------------------------------------------------- names in other scripts
@pytest.mark.parametrize("cc,typed,name", [
    ("JP", "ソフトバンク", "ソフトバンク"), ("JP", "softbank", "ソフトバンク"), ("JP", "SoftBank", "ソフトバンク"), ("JP", "ｿﾌﾄﾊﾞﾝｸ", "ソフトバンク"),
    ("JP", "ドコモ", "NTTドコモ"), ("JP", "docomo", "NTTドコモ"), ("JP", "ntt東日本", "NTT東日本"), ("JP", "NTT East", "NTT東日本"),
    ("KR", "케이티", "KT"), ("KR", "유플러스", "LG유플러스"), ("KR", "lg u+", "LG유플러스"), ("KR", "SK Broadband", "SK브로드밴드"),
    ("CN", "中国电信", "中国电信"), ("CN", "电信", "中国电信"), ("CN", "China Telecom", "中国电信"), ("CN", "联通", "中国联通"),
    ("TW", "中華電信", "中華電信"), ("TW", "Chunghwa Telecom", "中華電信"), ("TW", "hinet", "中華電信"),
    ("HK", "香港寬頻", "HKBN"), ("HK", "hkbn", "HKBN"), ("HK", "數碼通", "SmarTone"),
    ("GR", "ΟΤΕ", "Telekom"), ("GR", "οτε", "Telekom"), ("GR", "cosmote", "Telekom"),
    ("BG", "Виваком", "Vivacom"), ("BG", "виваком", "Vivacom"), ("BG", "йеттел", "Yettel Bulgaria"),
    ("TH", "true", "True"), ("TH", "3bb", "3BB")])
def test_a_provider_is_found_in_its_own_script_and_in_latin_letters(cc, typed, name):
    hits = packs.load().search_providers(typed, cc)
    assert hits and (hits[0]["name"], hits[0]["country"]) == (name, cc), (typed, [x["name"] for x in hits])
    # and from a site in another country, by the same text
    assert name in [x["name"] for x in packs.load().search_providers(typed, "GB")]


def test_every_entry_of_a_pack_in_another_script_has_a_latin_slug_a_latin_name_to_search_by_and_is_its_own_first_hit():
    p = packs.load()
    seen = 0
    for cc in OTHER_SCRIPTS:
        for x in p.providers_of(cc):
            names = [x["name"], *x["aka"]]
            assert latin(x["slug"]) and any(latin(n) for n in names), (cc, x["name"])
            for n in names:
                seen += not latin(n)
                assert packs.norm(n) and packs.same_name(n, n), n         # a name in any script compares as itself, never as nothing
                hits = p.search_providers(n, cc, limit=12)
                assert any(h["slug"] == x["slug"] and h["country"] == cc for h in hits), (cc, n, [h["name"] for h in hits])
            assert p.search_providers(x["name"], cc)[0]["slug"] == x["slug"], (cc, x["name"])
    assert seen > 100
    # no two entries of one pack fold to one name: accents and sound marks are let go when comparing, the letters are not
    for pack in p.providers:
        names = [packs.norm(x["name"]) for x in pack["providers"]]
        assert len(names) == len(set(names)), pack["id"]


def test_a_provider_netbox_holds_under_its_latin_name_is_the_packs_entry_in_its_own_script(tmp_path, monkeypatch):
    box = EmptyBox()
    site, dev, way = way_out(box, "jp")
    held = provider(box, "SoftBank")
    kddi = provider(box, "ＫＤＤＩ株式会社")                 # full-width letters, with the company form: the pack lists that name
    cl = game(tmp_path, monkeypatch, box)
    q = say(cl, way, ("device", {"choice": f"device:{dev['id']}"}))
    labels = [c["label"] for c in q["step"]["choices"]]
    # the two NetBox holds come first, and the pack does not offer them a second time in another script
    assert labels[:2] == ["SoftBank", "ＫＤＤＩ株式会社"] and "ソフトバンク" not in labels and "KDDI" not in labels and "NTT東日本" in labels
    q = say(cl, way, ("provider", {"choice": "search"}), ("provider_find0", {"text": "ソフトバンク"}))
    assert [c["label"] for c in q["step"]["choices"]][0] == "SoftBank (in your NetBox)"
    q = say(cl, way, ("provider_pick0", {"choice": "again"}), ("provider_find1", {"text": "楽天"}))
    assert q["step"]["prompt"] == '1 provider matches "楽天".' and q["step"]["choices"][0]["label"] == "楽天モバイル"
    q = say(cl, way, ("provider_pick1", {"choice": q["step"]["choices"][0]["id"]}))
    assert q["step"]["id"] == "circuit_id" and "楽天モバイル prints it" in q["step"]["prompt"]
    port = box.data["dcim/interfaces"][0]
    say(cl, way, ("circuit_id", {"text": "RM-0001"}), ("circuit_type", {"choice": "new:0"}), ("local_port", {"choice": f"interface:{port['id']}"}),
        ("cable_type", {"choice": "cat6"}))
    assert apply(cl, way)["complete"]
    made = next(x for x in box.data["circuits/providers"] if x["name"] == "楽天モバイル")
    entry = packs.load().provider("providers-jp", "rakuten-mobile")
    assert made["slug"] == "rakuten-mobile" and [a["asn"] for a in made["asns"]] == [a["asn"] for a in entry["asns"]]
    assert {r["slug"] for r in box.data["ipam/rirs"]} == {"apnic"} and held["id"] != made["id"] != kddi["id"]
