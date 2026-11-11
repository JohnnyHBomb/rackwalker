"""The setup companion against a live dev NetBox 4.7.4. Skipped unless RACKWALKER_LIVE_NETBOX=1.

Two estates, because NetBox cannot be both empty and seeded:

* `make test-live-empty`, on a stack that was started and never seeded (`make netbox-destroy netbox-up`): the
  onboarding chain from a NetBox with nothing in it (no site, no role, no manufacturer, no custom field) to a site with
  a rack, two boxes with their ports, a carrier and a traced way out. It is timed, and the time is printed.
* `make test-live`, on the seeded stack: the same lines beside the seed at a made-up empty site, a placeholder device
  that gets its real model, one that is refused, and a serial that does not look like the vendor's. Everything made
  here is removed afterwards.

Imports read the few library files in tests/library; nothing is fetched from the internet.
"""
from __future__ import annotations

import os
import time
from pathlib import Path

import httpx
import pytest

from rackwalker_proxy import packs, questroutes

from .conftest import schema_validator
from .test_live_quests import ADMIN, URL, _board, _client

pytestmark = pytest.mark.skipif(os.environ.get("RACKWALKER_LIVE_NETBOX") != "1", reason="live NetBox tests: make test-live")
LIBRARY = Path(__file__).parent / "library"
SITE = "Quay Annex"


@pytest.fixture(autouse=True)
def offline_library(monkeypatch):
    monkeypatch.setenv("RACKWALKER_LIBRARY_DIR", str(LIBRARY))
    monkeypatch.setenv("RACKWALKER_LIBRARY_FETCH", "0")
    # the tests change NetBox behind the game's back and ask it to look again at once: reads on request are not spaced out
    monkeypatch.setattr(questroutes, "FRESH_SITE_S", 0.0)
    monkeypatch.setattr(questroutes, "FRESH_ALL_S", 0.0)


@pytest.fixture(scope="module")
def nbx():
    with httpx.Client(base_url=URL + "/api/", headers={"Authorization": f"Bearer {ADMIN}"}, timeout=30) as c:
        yield c


def rows(nbx, path: str, **params) -> list[dict]:
    return nbx.get(path, params={**params, "limit": 200}).json()["results"]


def lib(file: str, kind: str = "device") -> str:
    return f"lib:{packs.load().index().by_file(kind, file)['n']}"


class Play:
    """Answers quest lines through the proxy, and counts what a player would have had to do."""

    def __init__(self, cl):
        self.cl, self.answers, self.requests = cl, 0, 0

    def say(self, quest_id: str, *steps: tuple[str, dict]) -> dict:
        q: dict = {}
        for step, body in steps:
            r = self.cl.post("/api/game/quests/answer", json={"scope": "netbox", "quest_id": quest_id, "step": step, **body})
            assert r.status_code == 200, r.text
            q = r.json()
            schema_validator("Quest").validate(q)
            assert q["step"] is None or q["step"]["id"] != step, (step, q["step"])
            self.answers += 1
            self.requests += 1
        return q

    def apply(self, quest_id: str) -> dict:
        ref = {"scope": "netbox", "quest_id": quest_id}
        pre = self.cl.post("/api/game/quests/preview", json=ref)
        assert pre.status_code == 200 and all(o["status"] in ("ok", "no_change") for o in pre.json()["ops"]), pre.text
        r = self.cl.post("/api/game/quests/apply", json=ref)
        assert r.status_code == 200, r.text
        schema_validator("QuestVerdict").validate(r.json())
        self.requests += 2
        return r.json()


def test_an_empty_live_netbox_is_set_up_by_playing_and_how_long_it_takes(nbx, tmp_path, capsys):
    if os.environ.get("RACKWALKER_LIVE_EMPTY") != "1":
        pytest.skip("needs a NetBox that was started and never seeded: make netbox-destroy netbox-up test-live-empty")
    assert rows(nbx, "dcim/sites/") == [] and rows(nbx, "dcim/device-types/") == [], "this NetBox is not empty"
    started = time.perf_counter()
    with _client(tmp_path, ADMIN, estate_ttl=600) as cl:
        play = Play(cl)
        b = _board(cl)
        assert b["message"].startswith("NetBox has no site yet.") and b["next_quest_id"] == "q:estate_no_site:estate:0" and b["can_write"]
        # ---- the site
        play.say("q:estate_no_site:estate:0", ("country", {"choice": "country:NL"}), ("site_name", {"text": "Mill Lane"}))
        v = play.apply("q:estate_no_site:estate:0")
        assert v["mode"] == "real" and v["complete"] and v["xp_awarded"] == 60, v["message"]
        site = rows(nbx, "dcim/sites/", slug="mill-lane")[0]
        assert site["region"]["slug"] == "nl" and site["status"]["value"] == "active"
        sid = site["id"]
        # the changelog names the player and the quest
        change = rows(nbx, "core/object-changes/", changed_object_type="dcim.site", changed_object_id=sid)[0]
        assert change["message"] == f"rackwalker ({cl.player}): quest: Your first site"
        # ---- the rack
        play.say(f"q:site_no_rack:site:{sid}", ("rack_kind", {"choice": "u:12"}), ("rack_name", {"text": "ML-1"}))
        assert play.apply(f"q:site_no_rack:site:{sid}")["complete"]
        rack = rows(nbx, "dcim/racks/", site_id=sid)[0]
        # ---- two boxes from the library, each with the ports of its model
        kit = f"q:site_no_devices:site:{sid}"
        play.say(kit, ("box0_find0", {"text": "C1111-4P"}), ("box0_pick0", {"choice": lib("Cisco/ISR-C1111-4P.yaml")}), ("role0", {"choice": "new:router"}),
                 ("box0_details", {"values": {"name": "ml-rtr1", "position": 10, "serial": "FGL214512AB"}}), ("more0", {"choice": "yes"}),
                 ("box1_find0", {"text": "ex2300 24p"}), ("box1_pick0", {"choice": lib("Juniper/EX2300-24P.yaml")}), ("role1", {"choice": "new:switch"}),
                 ("box1_details", {"values": {"name": "ml-sw1", "position": 9}}), ("more1", {"choice": "no"}))
        v = play.apply(kit)
        assert v["complete"] and v["bonus_xp"] == 40 and v["xp_awarded"] == 120, v["message"]
        devs = {d["name"]: d for d in rows(nbx, "dcim/devices/", site_id=sid)}
        assert devs["ml-rtr1"]["device_type"]["model"] == "ISR C1111-4P" and devs["ml-rtr1"]["serial"] == "FGL214512AB" and devs["ml-rtr1"]["position"] == 10.0
        assert devs["ml-sw1"]["rack"]["id"] == rack["id"] and devs["ml-sw1"]["role"]["name"] == "Switch"
        assert nbx.get("dcim/interfaces/", params={"device_id": devs["ml-sw1"]["id"], "limit": 1}).json()["count"] == 32
        assert nbx.get("dcim/interfaces/", params={"device_id": devs["ml-rtr1"]["id"], "limit": 1}).json()["count"] == 6
        ex = rows(nbx, "dcim/device-types/", slug="juniper-ex2300-24p")[0]
        assert (ex["part_number"], ex["manufacturer"]["name"], ex["interface_template_count"], ex["console_port_template_count"]) == ("EX2300-24P", "Juniper", 32, 1)
        # ---- whose line: KPN, from the provider pack of the Netherlands, with its AS number
        line = f"q:site_no_provider:site:{sid}"
        q = next(x for x in _board(cl)["quests"] if x["id"] == line)
        assert [c["label"] for c in q["step"]["choices"]][:3] == ["KPN", "Odido", "Ziggo"]
        play.say(line, ("provider", {"choice": "pack:kpn"}))
        assert play.apply(line)["complete"]
        kpn = rows(nbx, "circuits/providers/", slug="kpn")[0]
        assert [a["asn"] for a in kpn["asns"]] == [1136] and rows(nbx, "ipam/asns/", asn=1136)[0]["rir"]["name"] == "RIPE NCC"
        # ---- the way out: the chain the game has always had takes over, in a NetBox that had no circuit type
        way = f"q:site_no_way_out:site:{sid}"
        port = rows(nbx, "dcim/interfaces/", device_id=devs["ml-rtr1"]["id"], name="GigabitEthernet0/0/0")[0]
        play.say(way, ("device", {"choice": f"device:{devs['ml-rtr1']['id']}"}), ("provider", {"choice": f"provider:{kpn['id']}"}),
                 ("circuit_id", {"text": "KPN-7001"}), ("circuit_type", {"choice": "new:0"}), ("local_port", {"choice": f"interface:{port['id']}"}),
                 ("cable_type", {"choice": "smf"}))
        v = play.apply(way)
        assert v["complete"] and "circuit KPN-7001" in (v["trace"] or ""), (v["message"], v["trace"])
        b = _board(cl)
        assert not [c for c in b["chains"] if c["kind"] == "setup"] and b["rewards"]["xp"] >= 60 + 40 + 120 + 50 + 120
        cat = cl.get("/api/game/catalogue", params={"fresh": True}).json()
        schema_validator("Catalogue").validate(cat)
        assert (cat["verified"], cat["total"]) == (2, 2)
    took = time.perf_counter() - started
    with capsys.disabled():
        print(f"\nonboarding, empty NetBox to a traced way out: {play.answers} answers, 5 previewed changes, {play.requests} requests, {took:.1f} s of machine time")
    assert took < 120


# ---------------------------------------------------------------- beside the seed
def clear_annex(nbx) -> None:
    """Removes what the tests below make, found by name; a run that was cut short may have left some of it."""
    def drop(path: str, **params) -> None:
        for row in rows(nbx, path, **params):
            r = nbx.delete(f"{path}{row['id']}/")
            assert r.status_code in (204, 404), (path, r.text)
    for site in rows(nbx, "dcim/sites/", slug="quay-annex"):
        devices = rows(nbx, "dcim/devices/", site_id=site["id"])
        for d in devices:
            drop("dcim/cables/", device_id=d["id"])
            drop("dcim/modules/", device_id=d["id"])
        drop("dcim/devices/", site_id=site["id"])
        drop("dcim/racks/", site_id=site["id"])
    drop("dcim/sites/", slug="quay-annex")
    drop("dcim/regions/", slug="ie")
    for slug in ("juniper-ex2300-24p", "juniper-mx204", "annex-placeholder", "annex-panel", "harbour-works-hx-9", "panduit-dp24688tgy"):
        drop("dcim/device-types/", slug=slug)
    drop("dcim/module-types/", model="JPSU-650W-AC-AFO")
    for slug in ("juniper", "generic", "harbour-works", "panduit"):
        for maker in rows(nbx, "dcim/manufacturers/", slug=slug):      # only one that nothing else uses
            if not rows(nbx, "dcim/device-types/", manufacturer_id=maker["id"]) and not rows(nbx, "dcim/module-types/", manufacturer_id=maker["id"]):
                drop("dcim/manufacturers/", slug=slug)
    drop("dcim/device-roles/", slug="annex-kit")
    drop("extras/tags/", slug="rw-unverified")


@pytest.fixture(scope="module")
def annex(nbx):
    if not rows(nbx, "dcim/sites/", slug="north-depot"):
        pytest.skip("needs the seeded stack: make netbox-reset")
    clear_annex(nbx)

    def make(path: str, body: dict) -> dict:
        r = nbx.post(path, json=body)
        assert r.status_code == 201, (path, r.text)
        return r.json()
    region = make("dcim/regions/", {"name": "Ireland", "slug": "ie"})
    site = make("dcim/sites/", {"name": SITE, "slug": "quay-annex", "status": "active", "region": region["id"], "latitude": 53.3, "longitude": -6.2,
                                "facility": "QA1"})
    yield {"site": site, "make": make}
    clear_annex(nbx)


@pytest.fixture(scope="module")
def player(tmp_path_factory, annex):
    with _client(tmp_path_factory.mktemp("setup-admin"), ADMIN, estate_ttl=600) as cl:
        yield Play(cl)


def test_an_empty_site_beside_the_seed_gets_its_rack_and_a_box_from_the_library_live(nbx, annex, player):
    sid = annex["site"]["id"]
    b = _board(player.cl, sid)
    chain = next(c for c in b["chains"] if c["kind"] == "setup")
    # the seed has a carrier on record: that stage is done before it is asked, and only the rack and the kit are played
    assert chain["title"] == f"Setting up {SITE}" and [(s["stage"], s["status"]) for s in chain["stages"]] == [("room", "open"), ("kit", "locked"), ("carrier", "done")]
    player.say(f"q:site_no_rack:site:{sid}", ("rack_kind", {"choice": "other"}), ("rack_height", {"text": "9"}), ("rack_name", {"text": "QA-1"}))
    assert player.apply(f"q:site_no_rack:site:{sid}")["complete"]
    kit = f"q:site_no_devices:site:{sid}"
    q = player.say(kit, ("box0_find0", {"text": "EX2300-24P"}), ("box0_pick0", {"choice": lib("Juniper/EX2300-24P.yaml")}))
    assert "Switch" in [c["label"] for c in q["step"]["choices"]]                 # the seed's own roles are the choices
    role = next(c["id"] for c in q["step"]["choices"] if c["label"] == "Switch")
    # a serial that is another device's is refused outright
    taken = rows(nbx, "dcim/devices/", site_id=1)[0]["serial"]
    player.say(kit, ("role0", {"choice": role}))
    r = player.cl.post("/api/game/quests/answer", json={"scope": "netbox", "quest_id": kit, "step": "box0_details", "values": {"name": "qa-sw1", "serial": taken}})
    assert "a serial number belongs to one device" in r.json()["step"]["error"]
    player.say(kit, ("box0_details", {"values": {"name": "qa-sw1", "position": 5, "serial": "JN-QA-0001"}}), ("more0", {"choice": "no"}))
    v = player.apply(kit)
    assert v["complete"] and v["bonus_xp"] == 20, v["message"]
    dev = rows(nbx, "dcim/devices/", name="qa-sw1")[0]
    assert dev["device_type"]["model"] == "EX2300-24P" and dev["rack"]["name"] == "QA-1" and dev["position"] == 5.0
    assert nbx.get("dcim/interfaces/", params={"device_id": dev["id"], "limit": 1}).json()["count"] == 32


def test_a_placeholder_device_gets_its_model_only_where_nothing_recorded_is_lost_live(nbx, annex, player):
    make, sid = annex["make"], annex["site"]["id"]
    maker = make("dcim/manufacturers/", {"name": "Generic", "slug": "generic"})
    dtype = make("dcim/device-types/", {"manufacturer": maker["id"], "model": "Unknown", "slug": "annex-placeholder"})
    make("dcim/interface-templates/", {"device_type": dtype["id"], "name": "ge-0/0/0", "type": "1000base-t"})
    role = make("dcim/device-roles/", {"name": "Annex kit", "slug": "annex-kit", "color": "607d8b"})
    fits = make("dcim/devices/", {"name": "qa-box1", "site": sid, "role": role["id"], "device_type": dtype["id"], "status": "active", "serial": "QA-B1"})
    odd = make("dcim/devices/", {"name": "qa-box2", "site": sid, "role": role["id"], "device_type": dtype["id"], "status": "active", "serial": "QA-B2"})
    extra = make("dcim/interfaces/", {"device": odd["id"], "name": "Gi0/49", "type": "1000base-t"})
    sw = rows(nbx, "dcim/devices/", name="qa-sw1")[0]
    a_port = rows(nbx, "dcim/interfaces/", device_id=fits["id"], name="ge-0/0/0")[0]
    make("dcim/cables/", {"a_terminations": [{"object_type": "dcim.interface", "object_id": a_port["id"]}],
                          "b_terminations": [{"object_type": "dcim.interface", "object_id": rows(nbx, "dcim/interfaces/", device_id=sw["id"], name="ge-0/0/1")[0]["id"]}]})
    make("dcim/cables/", {"a_terminations": [{"object_type": "dcim.interface", "object_id": extra["id"]}],
                          "b_terminations": [{"object_type": "dcim.interface", "object_id": rows(nbx, "dcim/interfaces/", device_id=sw["id"], name="ge-0/0/2")[0]["id"]}]})
    b = _board(player.cl, sid)
    player.cl.get("/api/game/quests", params={"scope": "netbox", "fresh": True})
    # safe: its one port, cabled, exists in the model under the same name; the cable stays and 31 ports are added
    line = f"q:device_type_unknown:device:{fits['id']}"
    player.say(line, ("box_find0", {"text": "EX2300-24P"}), ("box_pick0", {"choice": lib("Juniper/EX2300-24P.yaml")}))
    v = player.apply(line)
    assert v["complete"] and v["bonus_xp"] == 20, v["message"]
    now = nbx.get(f"dcim/devices/{fits['id']}/").json()
    assert now["device_type"]["model"] == "EX2300-24P" and nbx.get("dcim/interfaces/", params={"device_id": fits["id"], "limit": 1}).json()["count"] == 32
    assert nbx.get(f"dcim/interfaces/{a_port['id']}/").json()["cable"] is not None and now["console_port_count"] == 1
    # refused: Gi0/49 is cabled and the model has no such port; nothing is written
    line = f"q:device_type_unknown:device:{odd['id']}"
    player.say(line, ("box_find0", {"text": "EX2300-24P"}), ("box_pick0", {"choice": lib("Juniper/EX2300-24P.yaml")}))
    pre = player.cl.post("/api/game/quests/preview", json={"scope": "netbox", "quest_id": line}).json()
    assert pre["ops"][-1]["status"] == "error" and "interface Gi0/49 (cabled)" in pre["ops"][-1]["error"]["message"]
    r = player.cl.post("/api/game/quests/apply", json={"scope": "netbox", "quest_id": line}).json()
    assert not r["complete"] and r["xp_awarded"] == 0 and nbx.get(f"dcim/devices/{odd['id']}/").json()["device_type"]["model"] == "Unknown"
    assert b["sites"]


def test_a_model_the_library_does_not_hold_and_a_module_in_a_bay_live(nbx, annex, player):
    sid = annex["site"]["id"]
    odd = rows(nbx, "dcim/devices/", name="qa-box2")[0]
    for cable in rows(nbx, "dcim/cables/", device_id=odd["id"]):
        nbx.delete(f"dcim/cables/{cable['id']}/")
    for port in rows(nbx, "dcim/interfaces/", device_id=odd["id"]):
        nbx.delete(f"dcim/interfaces/{port['id']}/")
    player.cl.get("/api/game/quests", params={"scope": "netbox", "fresh": True})
    line = f"q:device_type_unknown:device:{odd['id']}"
    player.cl.post("/api/game/quests/answer", json={"scope": "netbox", "quest_id": line, "step": "restart"})
    player.say(line, ("box_find0", {"text": "harbour hx-9"}), ("box_pick0", {"choice": "manual"}),
               ("box_manual", {"values": {"manufacturer": "Harbour Works", "model": "HX-9", "part_number": "HX-9-A", "u_height": 2}}))
    v = player.apply(line)
    assert v["complete"] and v["bonus_xp"] == 0 and "marked unverified" in v["message"], v["message"]
    made = rows(nbx, "dcim/device-types/", slug="harbour-works-hx-9")[0]
    assert [t["slug"] for t in made["tags"]] == ["rw-unverified"] and float(made["u_height"]) == 2.0
    # a device with module bays and nothing in them: the power supply from the library goes into the bay the player names
    juniper = rows(nbx, "dcim/manufacturers/", slug="juniper")[0]
    mx = annex["make"]("dcim/device-types/", {"manufacturer": juniper["id"], "model": "MX204", "slug": "juniper-mx204", "part_number": "MX204"})
    annex["make"]("dcim/module-bay-templates/", {"device_type": mx["id"], "name": "Power Supply 0", "position": "0"})
    role = rows(nbx, "dcim/device-roles/", slug="annex-kit")[0]
    rtr = annex["make"]("dcim/devices/", {"name": "qa-rtr1", "site": sid, "role": role["id"], "device_type": mx["id"], "status": "active", "serial": "QA-R1"})
    player.cl.get("/api/game/quests", params={"scope": "netbox", "fresh": True})
    bay = rows(nbx, "dcim/module-bays/", device_id=rtr["id"])[0]
    line = f"q:module_bays_unrecorded:device:{rtr['id']}"
    player.say(line, ("bay", {"choice": f"bay:{bay['id']}"}), ("mod_find0", {"text": "JPSU-650W-AC-AFO"}),
               ("mod_pick0", {"choice": lib("Juniper/JPSU-650W-AC-AFO.yaml", "module")}), ("mod_serial", {"text": "QA-PSU-1"}))
    v = player.apply(line)
    assert v["complete"], v["message"]
    module = rows(nbx, "dcim/modules/", device_id=rtr["id"])[0]
    assert (module["serial"], module["module_bay"]["id"], module["module_type"]["model"]) == ("QA-PSU-1", bay["id"], "JPSU-650W-AC-AFO")
    cat = player.cl.get("/api/game/catalogue", params={"fresh": True}).json()
    by_model = {i["model"]: i["status"] for i in cat["items"]}
    assert by_model["EX2300-24P"] == "verified" and by_model["HX-9"] == "unverified" and by_model["MX204"] == "verified"


def test_an_imported_patch_panel_lets_a_trace_through_and_a_wrong_part_number_is_put_right_live(nbx, annex, player):
    make, sid = annex["make"], annex["site"]["id"]
    # ---- a placeholder that is really a patch panel: imported with the library's front-to-rear mappings
    generic = rows(nbx, "dcim/manufacturers/", slug="generic")[0]
    dtype = make("dcim/device-types/", {"manufacturer": generic["id"], "model": "Unknown panel", "slug": "annex-panel"})
    role = rows(nbx, "dcim/device-roles/", slug="annex-kit")[0]
    panel = make("dcim/devices/", {"name": "qa-pp1", "site": sid, "role": role["id"], "device_type": dtype["id"], "status": "active", "serial": "QA-P1"})
    player.cl.get("/api/game/quests", params={"scope": "netbox", "fresh": True})
    line = f"q:device_type_unknown:device:{panel['id']}"
    player.say(line, ("box_find0", {"text": "DP24688TGY"}), ("box_pick0", {"choice": lib("Panduit/DP24688TGY.yaml")}))
    pre = player.cl.post("/api/game/quests/preview", json={"scope": "netbox", "quest_id": line}).json()
    assert pre["ops"][0]["after"]["port_mappings"] == 24, pre["ops"][0]
    assert player.apply(line)["complete"]
    made = rows(nbx, "dcim/device-types/", slug="panduit-dp24688tgy")[0]
    templates = rows(nbx, "dcim/front-port-templates/", device_type_id=made["id"])
    assert len(templates) == 24 and all(len(t["rear_ports"]) == 1 for t in templates)
    front = {p["name"]: p for p in rows(nbx, "dcim/front-ports/", device_id=panel["id"])}
    rear = {p["name"]: p for p in rows(nbx, "dcim/rear-ports/", device_id=panel["id"])}
    assert len(front) == 24 and all(p["rear_ports"] == [{"position": 1, "rear_port": rear[name]["id"], "rear_port_position": 1}] for name, p in front.items())
    # a lead from the switch into the front of the panel: NetBox's own trace now comes out of the rear port behind it
    sw = rows(nbx, "dcim/devices/", name="qa-sw1")[0]
    port = rows(nbx, "dcim/interfaces/", device_id=sw["id"], name="ge-0/0/5")[0]
    make("dcim/cables/", {"a_terminations": [{"object_type": "dcim.interface", "object_id": port["id"]}],
                          "b_terminations": [{"object_type": "dcim.frontport", "object_id": front["03"]["id"]}]})
    trace = nbx.get(f"dcim/interfaces/{port['id']}/trace/").json()
    assert [t["id"] for t in trace[-1][0]] == [rear["03"]["id"]], trace[-1]
    # ---- a part number in NetBox that the library contradicts: the label decides, and the library's spelling is written
    mx = rows(nbx, "dcim/device-types/", slug="juniper-mx204")[0]
    assert nbx.patch(f"dcim/device-types/{mx['id']}/", json={"part_number": "EX2300-24P"}).status_code == 200
    player.cl.get("/api/game/quests", params={"scope": "netbox", "fresh": True})
    line = f"q:device_type_part_number:device_type:{mx['id']}"
    q = player.say(line, ("label", {"choice": "else"}), ("label_says", {"text": "mx 204"}))
    assert q["proposal"]["ops"][0]["fields"] == {"part_number": "MX204"}
    v = player.apply(line)
    assert v["complete"] and v["xp_awarded"] == 30, v["message"]
    assert nbx.get(f"dcim/device-types/{mx['id']}/").json()["part_number"] == "MX204"
    change = rows(nbx, "core/object-changes/", changed_object_type="dcim.devicetype", changed_object_id=mx["id"])[0]
    assert change["message"].endswith("quest: The part number of the Juniper MX204")
