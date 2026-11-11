"""The setup companion: quest lines that build a NetBox up from nothing, out of the packs (docs/packs.md, docs/quests.md).

    estate_no_site          NetBox has no site: the country, then the site's name
    site_no_rack            a site with no rack and no device: its first rack
    site_no_devices         a site with no device: its first boxes, found in the device-type library by what is printed
                            on them ("What is this box?"), each with its ports
    site_more_devices       a site with a few devices: the boxes NetBox does not hold yet, three at a time, for as long
                            as the player has more (optional: it never counts against a site's completeness)
    site_no_provider        nothing in NetBox is connected yet and no carrier is on record: whose line the site is on,
                            from the provider pack of the site's country
    device_type_unknown     a device recorded as a placeholder model: what it really is
    device_type_part_number a model the library holds, with another part number in NetBox than the library lists (or
                            none): what the label says
    module_bays_unrecorded  a device with module bays and nothing recorded in any of them

The first four are the chain "Setting up <site>": a new admin gets a usable NetBox by playing it. Like every line,
each asks with real names, takes "I don't know yet", ends in one change that is previewed before it is written, and is
complete only when NetBox, read again, shows it.

Truthfulness. A model picked from the library is verified; a model the library does not hold can still be recorded,
by hand, and is tagged rw-unverified; a part number that contradicts the library's for a model it does hold is
refused, with what was expected. A serial that is already another live device's is refused; one that does not look like
the vendor's (packs/serials: sourced rules only) is asked about once and, if the player confirms it, recorded and
tagged rw-unverified. Only values that pass a check earn the bonus (`bonus`), and that is decided from NetBox as it
reads after the write, never from what the player said.
"""
from __future__ import annotations

import re
import zlib
from typing import Any

from . import packs
from .country import ISO_CODES, NAMES as COUNTRY_NAMES, fold as fold_country
from .estate import Estate, natural
from .estate import name_of as nm
from .setupops import COUNTRY_OF, RETIRED

# A NetBox that is just starting: nothing is cabled, no carrier and no circuit is on record, and it holds few devices.
STARTER_DEVICES = 12
KINDS = ("estate_no_site", "site_no_rack", "site_no_devices", "site_no_provider", "device_type_unknown", "device_type_part_number",
         "module_bays_unrecorded", "site_more_devices")
OPTIONAL_KINDS = ("site_more_devices",)       # offered beside a site's few open lines, never instead of one, never in its completeness
MORE_KEY = 1000        # the check of "more boxes" is per site and per count of devices: site id * MORE_KEY + count
CHAIN_KINDS = ("site_no_rack", "site_no_devices", "site_no_provider")
MAX_NEW = 3            # boxes added in one pass of "first devices" or "more boxes"
KNOWN_SHOWN = 8        # providers of NetBox listed before the pack's own names (the rest are found by the search)
ROUNDS = 4             # searches in one question before the line says where to look instead
ROLES = ("Router", "Switch", "Firewall", "Server", "Patch panel", "PDU", "Access point")
RACK_HEIGHTS = (("42", "A full-height rack (42U)"), ("24", "A half-height rack (24U)"), ("12", "A wall cabinet (12U)"), ("47", "A tall rack (47U)"))
CIRCUIT_TYPES = ("Internet", "Private line", "MPLS or IP VPN", "Dark fibre")
TYPE_BONUS = 20        # per box whose model the device-type library confirms
SERIAL_BONUS = 10      # per serial that fits its vendor's sourced rule
UNVERIFIED = packs.UNVERIFIED_TAG["slug"]
WORDS: dict[str, tuple[str, str, str]] = {
    "estate_no_site": ("Your first site", "Everything in NetBox hangs off a site. Let's make the first one.",
                       "You can now walk to {made} on the green, and put its racks and devices in NetBox."),
    "site_no_rack": ("A first rack at {site}", "Devices need somewhere to stand. Let's record the rack, or say there is none.",
                     "You can now place devices in a rack at {site}, unit by unit."),
    "site_no_devices": ("The first boxes at {site}", "Let's record what stands there: tell me what is printed on each box.",
                        "You can now see the devices at {site} with their ports, ready to be cabled."),
    "site_no_provider": ("Whose line {site} is on", "When the line is down, the first thing you need is the carrier's name. Let's record it.",
                         "You can now record the circuit that brings {site} online, under its carrier."),
    "site_more_devices": ("More boxes at {site}", "A box NetBox does not hold is a box nobody can plan around. Let's add what else stands there.",
                          "You can now see every box you added at {site} with its ports, ready to be cabled."),
    "device_type_unknown": ("What {subject} really is", "Without its model nobody knows its ports, its height or its support contract. Let's find out what it is.",
                            "You can now see {subject} with the ports of its real model."),
    "device_type_part_number": ("The part number of the {subject}", "A spare ordered by the wrong part number is the wrong spare. Let's check the label.",
                                "You can now order a spare for the {subject} by the part number NetBox holds."),
    "module_bays_unrecorded": ("What is fitted in {subject}", "Power supplies, fans and line cards fail one at a time. Let's record what is in the bays.",
                               "You can now look up what is fitted in {subject}, bay by bay."),
}
XP = {"estate_no_site": 60, "site_no_rack": 40, "site_no_devices": 80, "site_no_provider": 50, "device_type_unknown": 40, "device_type_part_number": 30,
      "module_bays_unrecorded": 40, "site_more_devices": 40}


def country_name(code: str | None) -> str:
    return COUNTRY_OF.get(code or "", code or "an unknown country")


# ---------------------------------------------------------------- findings
def unknown_type(manufacturer: str | None, model: str | None) -> bool:
    """A maker or model that only says "nobody knew" ("Unknown", "Generic switch", "TBD"), unless the device-type library
    holds exactly that: "Generic" is also the maker the library files patch panels and blank kit under."""
    if not (packs.is_placeholder(model) or packs.is_placeholder(manufacturer)):
        return False
    return packs.load().index().lookup(manufacturer, model) is None


def verified_type(dt: dict[str, Any] | None) -> str:
    """How a device type of NetBox stands against the library: `verified` (the library holds the maker and the model,
    and its part number does not contradict it), `mismatch` (its part number is the library's for another model) or
    `unverified` (tagged so, a placeholder, or a model the library does not hold)."""
    if dt is None or UNVERIFIED in dt.get("tags", []) or unknown_type(dt.get("manufacturer"), dt.get("model")):
        return "unverified"
    status = packs.load().index().part_check(dt.get("manufacturer"), dt.get("model"), dt.get("part_number"))["status"]
    return {"unknown": "unverified", "mismatch": "mismatch"}.get(status, "verified")


def is_starter(e: Estate) -> bool:
    return not e.providers and not e.circuits and not e.cables and len(e.devices) <= STARTER_DEVICES \
        and e.can_see("providers", "circuits", "cables", "devices")


def findings(e: Estate, a: Any, add: Any, quiet: Any, live: tuple[str, ...]) -> None:
    """The setup findings of an estate. `add(kind, obj, site_id, title, evidence, **data)` reports one; `quiet(kind,
    obj, site_id)` records that the check was made and passed, without counting it into a site's completeness (an
    established site is not "more complete" for having a rack)."""
    def sref(sid: int) -> dict[str, Any]:
        return {"kind": "site", "id": sid, "name": e.sites[sid]["name"]}
    if e.can_see("sites"):
        if not e.sites:
            add("estate_no_site", ("estate", 0), 0, "NetBox has no site yet.", [], about="estate")
        else:
            quiet("estate_no_site", ("estate", 0), 0)
    racks_at = {r["site_id"] for r in e.racks.values()}
    starter = is_starter(e)
    bays: dict[int, list[dict[str, Any]]] = {}
    for b in e.module_bays.values():
        bays.setdefault(b["device_id"], []).append(b)
    # A model the library holds whose part number in NetBox is another one, or empty: one line per model, at the site of
    # the first live device of that model (a label has to be read). A model nothing is made of is left to the catalogue.
    index = packs.load().index()
    first: dict[int, dict[str, Any]] = {}
    for d in sorted(e.devices.values(), key=lambda d: d["id"]):
        if d["status"] in live and d.get("type_id") in e.dtypes and d["site_id"] in e.sites:
            first.setdefault(d["type_id"], d)
    for tid, d in sorted(first.items()) if e.can_see("devices", "dtypes") else []:
        t = e.dtypes[tid]
        check = index.part_check(t["manufacturer"], t["model"], t["part_number"])
        wrong = check["status"] == "mismatch" or (check["status"] == "known" and check["expected"] and not t["part_number"])
        if not wrong or UNVERIFIED in t["tags"] or unknown_type(t["manufacturer"], t["model"]):
            quiet("device_type_part_number", ("device_type", tid), d["site_id"])
            continue
        model = f"{t['manufacturer']} {t['model']}"
        add("device_type_part_number", ("device_type", tid), d["site_id"],
            (f"NetBox holds the part number {t['part_number']} for the {model}; in the device-type library that is the part number of the "
             f"{check['other']}" + (f", and the {t['model']} is listed with {check['expected']}." if check["expected"] else ".")
             if t["part_number"] else f"NetBox holds no part number for the {model}; the device-type library lists {check['expected']}."),
            [{"kind": "device", "id": d["id"], "name": d["name"] or f"device {d['id']}"}],
            type_id=tid, device_id=d["id"], model=model, have=t["part_number"] or None, expected=check["expected"])
    for sid in sorted(e.sites):
        name = e.sites[sid]["name"]
        devs = e.site_devices(sid)
        if e.can_see("racks", "devices"):
            if sid not in racks_at and not devs:
                add("site_no_rack", ("site", sid), sid, f"{name} has no rack and no device in NetBox.", [sref(sid)], about="site")
            else:
                quiet("site_no_rack", ("site", sid), sid)
            if not devs:
                add("site_no_devices", ("site", sid), sid, f"NetBox holds no device at {name}.", [sref(sid)], about="site")
            else:
                quiet("site_no_devices", ("site", sid), sid)
            # More boxes: an optional line while the site is being set up (a few devices, none of them cabled yet),
            # renewed each time the count of its devices changes (the check "there is more at this site than n devices"
            # passes when there is). It is never part of a site's completeness: nobody but the admin knows whether a
            # site's kit is all in NetBox.
            n = len(devs)
            if not e.synthetic and sid < 10 ** 9 and n < MORE_KEY:
                for k in range(max(1, n - STARTER_DEVICES), n):
                    quiet("site_more_devices", ("site", sid * MORE_KEY + k), sid)
                if 1 <= n < STARTER_DEVICES and e.can_see("ports", "cables") and not any(
                        p["cable_id"] is not None for d in devs for p in e.device_ports(d["id"])):
                    add("site_more_devices", ("site", sid * MORE_KEY + n), sid,
                        f"NetBox holds {n} device{'s' if n != 1 else ''} at {name}. Only you know whether that is all of them.", [sref(sid)],
                        about="site", known=sorted(d["id"] for d in devs))
                    a.quiet.add(f"site_more_devices:site:{sid * MORE_KEY + n}")
        if e.can_see("providers", "circuits", "cables", "devices"):
            if starter:
                add("site_no_provider", ("site", sid), sid, f"NetBox holds no carrier at all, so nobody can tell from it whose line {name} is on.", [sref(sid)], about="site")
            elif e.providers:
                quiet("site_no_provider", ("site", sid), sid)
        for d in devs:
            if d["status"] not in live:
                continue
            if e.can_see("devices") and unknown_type(d.get("manufacturer"), d.get("model")):
                add("device_type_unknown", ("device", d["id"]), sid,
                    f"{nm(d)} is recorded as \"{d.get('manufacturer') or '?'} {d.get('model') or '?'}\": NetBox does not say what box it really is.",
                    [{"kind": "device", "id": d["id"], "name": d["name"] or f"device {d['id']}"}], device_id=d["id"])
            elif e.can_see("devices"):
                quiet("device_type_unknown", ("device", d["id"]), sid)
            mine = bays.get(d["id"], [])
            if mine and e.can_see("module_bays"):
                if all(b["module_id"] is None for b in mine):
                    add("module_bays_unrecorded", ("device", d["id"]), sid,
                        f"{nm(d)} has {len(mine)} module bay{'s' if len(mine) != 1 else ''}, and NetBox shows nothing in any of them.",
                        [{"kind": "device", "id": d["id"], "name": d["name"] or f"device {d['id']}"}], device_id=d["id"])
                else:
                    quiet("module_bays_unrecorded", ("device", d["id"]), sid)


# ---------------------------------------------------------------- shared question blocks
def _q():
    from . import quests

    return quests


def held_type(e: Estate, index: Any, row: dict[str, Any]) -> dict[str, Any] | None:
    """The device type of NetBox that is this library model (under any name of its maker), one with ports before one without."""
    held = [t for t in e.dtypes.values() if index.same(row, t["manufacturer"], t["model"])]
    return next((t for t in held if t.get("templates") != 0), held[0] if held else None)


def _held_label(e: Estate, index: Any, row: dict[str, Any]) -> str:
    """A library model as a choice. One NetBox already holds says so, with the ports NetBox has for it: that is what a new box gets."""
    have = held_type(e, index, row)
    if have is None:
        return index.label(row)
    if have.get("templates") == 0:
        return f"{have['manufacturer']} {have['model']} (in NetBox: no ports)"       # the preview says they are added from the library
    ports = have.get("interfaces")
    return f"{have['manufacturer']} {have['model']} (in NetBox" + (f": {ports} port{'s' if ports != 1 else ''})" if ports is not None else ")")


def find_type(ctx: Any, key: str, kind: str = "device", what: str = "it", maker: str | None = None,
              closing: list[dict[str, str]] | None = None) -> tuple[str, Any]:
    """"What is this box?": the player types what is printed on it; the library's matches (and matching models NetBox
    already holds) are the choices. Returns ("library", index row), ("existing", device type id) or ("manual", fields).
    `closing`: answers that close the line instead ("it really is a generic box")."""
    q = _q()
    e = ctx.e
    index = packs.load().index()
    noun = "module" if kind == "module" else "model"
    look = ["Read the label on the front or the rear: the model and the part number are usually printed beside the serial.",
            "A photograph of the label is enough to search with later."]
    if not index:
        raise q.Stop("parked", None, ["The game's device-type index did not load (see GET /api/packs), so models cannot be searched here.",
                                      "Add the model in NetBox by hand (Devices > Device Types), then come back."],
                     note="The device-type library is not available in this game.")
    for r in range(ROUNDS):
        text = ctx.text(f"{key}_find{r}", (f"What is printed on {what}? Type the model, the part number, or what the label says."
                                           if r == 0 else "Type it another way: fewer characters, or the part number instead of the model."),
                        lambda t: (t, None) if len(re.sub(r"[^A-Za-z0-9]", "", t)) >= 2 else (None, "Type at least two letters or digits."),
                        "for example EX2300-24P, or \"catalyst 9300 48\"", look)
        hits = index.search(text, kind, limit=5, manufacturer=maker)
        words = packs._words(text).split()
        own = [] if kind == "module" else [
            t for t in e.dtypes.values() if not packs.is_placeholder(t["model"]) and index.lookup(t["manufacturer"], t["model"]) is None
            and all(w in packs._words(f"{t['manufacturer']} {t['model']} {t['part_number']}") for w in words)][:2]
        choices = [q.choice(f"lib:{row['n']}", _held_label(e, index, row) if kind == "device" else index.label(row)) for row in hits] + \
                  [q.choice(f"own:{t['id']}", f"{t['manufacturer']} {t['model']} (in your NetBox)") for t in own]
        last = r == ROUNDS - 1
        more = ([] if last else [q.choice("again", "None of these: search again", "other")]) + \
               [q.choice("manual", "It is not in the library: record it by hand", "other")] + (closing or [])
        n = len(choices)
        prompt = (f"{n} {noun}{'s' if n != 1 else ''} match{'es' if n == 1 else ''} \"{q.clip(text, 30)}\". Which is it?" if n else
                  f"Nothing in the device-type library matches \"{q.clip(text, 30)}\".")
        got = ctx.choice(f"{key}_pick{r}", prompt, choices + more, look)
        if got == "again":
            continue
        if got.startswith("lib:"):
            row = index.get(kind, int(got[4:]))
            if row is None:
                raise KeyError(got)
            have = held_type(e, index, row) if kind == "device" else None
            # a type NetBox holds with no port on it is filled from the library, so the new box gets its ports
            return ("existing", have["id"]) if have and have.get("templates") != 0 else ("library", row)
        if got.startswith("own:"):
            return "existing", int(got[4:])
        break
    else:
        raise q.Stop("parked", None, look + ["If the library does not hold it, come back and choose \"record it by hand\"."])
    fields = [{"id": "manufacturer", "label": "Manufacturer", "type": "text", "max_length": 100, "hint": "as on the label, for example Juniper", "suggestion": None, "options": []},
              {"id": "model", "label": "Model", "type": "text", "max_length": 100, "hint": "as on the label", "suggestion": None, "options": []},
              {"id": "part_number", "label": "Part number (if printed)", "type": "text", "max_length": 50, "hint": "often beside the serial", "suggestion": None, "options": []},
              {"id": "u_height", "label": "Height in rack units", "type": "number", "min": 0, "max": 100, "hint": "1 for a normal switch; 0 for a box that takes no unit", "suggestion": None, "options": []}]
    if kind == "module":
        fields.pop()    # a module takes no rack unit of its own
    sid = f"{key}_manual"
    values = ctx.form(sid, f"Record the {noun} by hand. It is marked unverified in NetBox{'' if kind == 'module' else ' (no bonus)'}, because the library cannot confirm it.", fields, look)
    assert values is not None
    if not values.get("manufacturer") or not values.get("model"):
        raise ctx.again(sid, "The manufacturer and the model are both needed.")
    check = index.part_check(values["manufacturer"], values["model"], values.get("part_number"), kind)
    if check["status"] == "mismatch":
        raise ctx.again(sid, f"The device-type library knows {values['part_number']} as the part number of the {values['manufacturer']} {check['other']}, "
                             f"not of the {values['model']}. Read the label again, or go back and pick the model from the search.")
    row = index.lookup(values["manufacturer"], values["model"], kind) or (index.by_part(values["manufacturer"], values.get("part_number"), kind)
                                                                           if values.get("part_number") else None)
    if row is not None:   # typed by hand, and the library holds it after all: the verified model is used
        have = held_type(e, index, row) if kind == "device" else None
        return ("existing", have["id"]) if have and have.get("templates") != 0 else ("library", row)
    have = next((t for t in e.dtypes.values() if packs.maker_key(t["manufacturer"]) == packs.maker_key(values["manufacturer"])
                 and packs._words(t["model"]) == packs._words(values["model"])), None) if kind == "device" else None
    return ("existing", have["id"]) if have else ("manual", values)


def type_ops(picked: tuple[str, Any], ref: str, kind: str = "device", made: dict[str, str] | None = None) -> tuple[list[dict[str, Any]], Any]:
    """The op that brings a picked model into NetBox (none for one it already holds) and what later ops name it by.
    `made` remembers the models already imported in this change set, so the same model is imported once."""
    how, what = picked
    made = made if made is not None else {}
    target = {"kind": f"{kind}_type", "id": None}
    if how == "existing":
        return [], what
    if how == "library":
        if what["file"] in made:
            return [], made[what["file"]]
        made[what["file"]] = f"@{ref}"
        return [{"op": f"add_{kind}_type", "target": target, "client_ref": ref, "fields": {"library_file": what["file"]}}], f"@{ref}"
    fields = {"manufacturer": what["manufacturer"], "model": what["model"], **({"part_number": what["part_number"]} if what.get("part_number") else {}),
              **({"u_height": what["u_height"]} if kind == "device" and what.get("u_height") is not None else {})}
    return [{"op": f"add_{kind}_type", "target": target, "client_ref": ref, "fields": fields}], f"@{ref}"


def type_facts(e: Estate, picked: tuple[str, Any]) -> dict[str, Any]:
    how, what = picked
    if how == "existing":
        t = e.dtypes[what]
        return {"manufacturer": t["manufacturer"], "model": t["model"], "u_height": t["u_height"]}
    return {"manufacturer": what["manufacturer"], "model": what["model"], "u_height": float(what.get("u_height", 1) or 0)}


def check_serial(ctx: Any, form_sid: str, key: str, maker: str | None, model: str | None, serial: str, who: str,
                 own_id: int | None = None, taken: set[str] | None = None) -> str:
    """How a typed serial stands: "ok" (fits a sourced rule), "plain" (no rule to check against), "unverified" (it does
    not look like the vendor's and the player confirmed it) or "skip" (the player leaves it out). A serial that is
    another device's is refused: the form is asked again."""
    q = _q()
    e = ctx.e
    twin = next((d for d in e.devices.values() if d["id"] != own_id and d["serial"] and d["serial"].lower() == serial.lower()
                 and d["status"] not in RETIRED), None)
    if twin is not None or serial.lower() in (taken or set()):
        other = nm(twin) if twin is not None else "another box in this list"
        raise ctx.again(form_sid, f"{serial} is the serial of {other}: a serial number belongs to one device. Read the label of {who} again.")
    result = packs.load().serial_check(maker, model, serial)
    if result["status"] == "no_rule":
        return "plain"
    if result["status"] == "ok":
        return "ok"
    got = ctx.choice(f"{key}_sure_{zlib.crc32(serial.encode()):08x}",
                     q.clip(f"Are you sure? {serial} does not look like a {result['vendor']} serial: {result['says']}. Is that what the label of {who} says?", 300),
                     [q.choice("yes", "Yes: record it, marked unverified", "confirm"), q.choice("skip", "Leave this serial out for now", "skip")],
                     ["Read the serial again: O and 0, I and 1, B and 8 are easily confused."], unknown=False)
    return "unverified" if got == "yes" else "skip"


def ask_provider(ctx: Any, site_id: int, closing: list[dict[str, str]] | None = None) -> tuple[Any, str, list[dict[str, Any]]]:
    """"Whose line is it?": the providers NetBox holds, then those of the provider pack of the site's country, by name;
    a search across every pack; a typed name for a provider no pack lists; and "I don't know yet". Returns the
    provider (its NetBox id, or "@provider" for one the change set creates), its name, and the op that creates it
    (none for one NetBox holds). A pack entry NetBox already holds, by name or by AS number, is that provider."""
    q = _q()
    e = ctx.e
    p = packs.load()
    site = e.sites[site_id]
    country = site.get("country")
    known = sorted(e.providers.values(), key=lambda x: natural(x["name"]))
    look = ["The hand-off box and the bill carry the carrier's name.", "A wholesale network's name on the box is not always who you pay: the bill decides."]
    if e.synthetic:
        # the practice island is made up: no real carrier is offered there, and nothing new is added to it
        got = q.pick(ctx, "provider", "Whose line is it?", [(f"provider:{x['id']}", x["name"], x["name"]) for x in known], look[:1],
                     [q.choice("other", "A carrier that is not in NetBox yet", "other")] + (closing or []), noun="provider")
        if got == "other":
            raise q.Stop("parked", None, ["Add the carrier to NetBox (Circuits > Providers).", "Come back: it will then be one of the choices."],
                         note="You said the line belongs to a carrier that is not in NetBox yet.")
        x = e.providers[int(got.split(":")[1])]
        return x["id"], x["name"], []

    def from_pack(entry: dict[str, Any]) -> tuple[Any, str, list[dict[str, Any]]]:
        hits = packs.provider_matches(entry, known)
        if len(hits) > 1 and not packs.provider_named(entry, known):
            # NetBox holds one network under several names (they share its AS number): the player says which, the game does not guess
            got = ctx.choice("provider_which", q.clip(f"NetBox holds {len(hits)} providers with an AS number of {entry['name']}. Which one is this line's?", 300),
                             [q.choice(f"provider:{x['id']}", x["name"]) for x in hits[:6]], look)
            hit = e.providers[int(got.split(":")[1])]
            return hit["id"], hit["name"], []
        if hits:
            return hits[0]["id"], hits[0]["name"], []
        fields = {"name": entry["name"], "slug": entry["slug"], "pack": entry["pack"], **({"asns": entry["asns"]} if entry["asns"] else {}),
                  **({"aka": entry["aka"][:12]} if entry["aka"] else {})}
        return "@provider", entry["name"], [{"op": "create_provider", "target": {"kind": "provider", "id": None}, "client_ref": "provider", "fields": fields}]

    def typed(name: str) -> tuple[Any, str, list[dict[str, Any]]]:
        hit = packs.match_provider({"name": name, "slug": packs.slugify(name), "asns": [], "aka": []}, known)
        if hit is not None:
            return hit["id"], hit["name"], []
        exact = [x for x in p.search_providers(name, country, limit=12) if packs.same_name(x["name"], name)]
        near = [x for x in exact if x["country"] in (country, None)]
        # a name of the site's own country or of the backbone names is that provider; so is a name only one pack lists.
        # A name several countries use for different networks ("Telenet", "TIM") is recorded as typed, with no AS number
        if near or len(exact) == 1:
            return from_pack((near or exact)[0])
        return "@provider", name, [{"op": "create_provider", "target": {"kind": "provider", "id": None}, "client_ref": "provider", "fields": {"name": name}}]

    offered = [x for x in p.providers_of(country) if packs.match_provider(x, known) is None]
    # the admin's own providers first, all that fit; then names of the country's pack NetBox does not hold yet
    shown_known = known[:KNOWN_SHOWN]
    room = max(2, 6 - len(shown_known))
    choices = [q.choice(f"provider:{x['id']}", x["name"]) for x in shown_known] + \
              [q.choice(f"pack:{x['slug']}", x["name"]) for x in offered[:room]]
    pack = p.provider_pack(country) if country else None
    where = f"the provider pack of {country_name(country)}" if pack else "the provider packs"       # no country, or none with a pack
    more = [q.choice("search", "Another provider: search by name", "other"), q.choice("typed", "My provider is not listed", "other")]
    hint = "" if pack else (f" (The game has no provider pack for {country_name(country)} yet: search the others, or type the name.)" if country else
                            " (This site has no country on record, so no country's providers are offered first: search, or type the name.)")
    got = ctx.choice("provider", "Whose line is it?" + hint, choices + more + (closing or []), look)
    if got.startswith("provider:"):
        x = e.providers[int(got.split(":")[1])]
        return x["id"], x["name"], []
    if got.startswith("pack:"):
        entry = next((x for x in offered if x["slug"] == got[5:]), None)
        if entry is None:
            raise KeyError(got)
        return from_pack(entry)
    if got == "search":
        for r in range(ROUNDS):
            text = ctx.text(f"provider_find{r}", f"Type the carrier's name, or its AS number. The game looks in NetBox and in {where}, then in every other pack.",
                            lambda t: (t, None) if len(t.strip()) >= 2 else (None, "Type at least two characters."), "for example the name on the bill", look)
            listed = p.search_providers(text, country, limit=6)
            mine = [x for x in known if packs.norm(text) and (packs.norm(text) in packs.norm(x["name"]) or packs.tight(text) in packs.tight(x["name"]))]
            # a pack name NetBox holds under another name ("BT" as "British Telecom") is the admin's own provider, found by either
            mine += [m for x in listed for m in packs.provider_matches(x, known)[:1] if not any(m is y for y in mine)]
            mine = list({x["id"]: x for x in mine}.values())[:4]
            hits = [x for x in listed if packs.match_provider(x, known) is None][:6 - len(mine)]
            found = [q.choice(f"provider:{x['id']}", f"{x['name']} (in your NetBox)") for x in mine] + \
                    [q.choice(f"hit:{x['pack']}:{x['slug']}", x["name"] + ("" if x["country"] == country else f" ({x['country'] or 'international'})")) for x in hits]
            last = r == ROUNDS - 1
            n = len(found)
            again = ([] if last else [q.choice("again", "None of these: search again", "other")]) + [q.choice("typed", "My provider is not listed", "other")]
            picked = ctx.choice(f"provider_pick{r}", f"{n} provider{'s' if n != 1 else ''} match{'es' if n == 1 else ''} \"{q.clip(text, 30)}\"." if n else
                                f"No pack lists a provider like \"{q.clip(text, 30)}\".", found + again, look)
            if picked == "again":
                continue
            if picked.startswith("provider:"):
                x = e.providers[int(picked.split(":")[1])]
                return x["id"], x["name"], []
            if picked.startswith("hit:"):
                _, pack_id, slug = picked.split(":", 2)
                entry = p.provider(pack_id, slug)
                if entry is None:
                    raise KeyError(picked)
                return from_pack(entry)
            break
    name = ctx.text("provider_name", "What is the carrier called? Type the name as it stands on the bill. It is recorded as you type it.",
                    lambda t: (t.strip(), None) if 2 <= len(t.strip()) <= 100 else (None, "A name of 2 to 100 characters."),
                    "exactly as on the bill or the hand-off box", look)
    return typed(name)


def ask_circuit_type(ctx: Any, cid: str) -> tuple[Any, list[dict[str, Any]]]:
    """The kind of service of a new circuit. In a NetBox with no circuit type yet, a short list of common ones is
    offered and the picked one is created with the circuit."""
    q = _q()
    e = ctx.e
    if e.circuit_types:
        types = [(f"type:{t['id']}", t["name"], t["name"]) for _, t in sorted(e.circuit_types.items())]
        tid = q.pick(ctx, "circuit_type", f"What kind of service is {cid}?", types, ["The order or the bill names the service."], noun="circuit type")
        return int(tid.split(":")[1]), []
    got = ctx.choice("circuit_type", f"What kind of service is {cid}? (NetBox has no circuit type yet: the one you pick is added.)",
                     [q.choice(f"new:{n}", name) for n, name in enumerate(CIRCUIT_TYPES)], ["The order or the bill names the service."])
    name = CIRCUIT_TYPES[int(got.split(":")[1])]
    return "@ctype", [{"op": "create_circuit_type", "target": {"kind": "circuit_type", "id": None}, "client_ref": "ctype", "fields": {"name": name}}]


def country_typed(text: str) -> tuple[Any, str | None]:
    """A typed country: its ISO 3166-1 code, or a name the game knows ("Belgium", "UK"). Two letters that are no
    country's code are not taken, so no region is made for a slip of the keyboard."""
    t = text.strip()
    code = COUNTRY_NAMES.get(fold_country(t))          # first, so "UK" reads as GB
    if code is None and re.fullmatch(r"[A-Za-z]{2}", t) and t.upper() in ISO_CODES:
        code = t.upper()
    if code is None:
        return None, ("That is not a country code the game knows. Type the two letters of its ISO code, for example BE." if len(t) == 2 else
                      "The game does not know that name. Type the country's two-letter code, for example BE.")
    return code, None


# ---------------------------------------------------------------- flows
def flow_first_site(ctx: Any) -> list[dict[str, Any]]:
    q = _q()
    e = ctx.e
    look = ["Decide which building or room is your first site: the one you can walk to today is a good one."]
    # one question, typed: with a provider pack for some fifty countries, no short list of them is the right one
    code = ctx.text("country_code", "Which country is your first site in? Type its name, or its two-letter code (BE, JP).", country_typed,
                    "for example Belgium, or BE", look)
    taken = {s["name"].lower() for s in e.sites.values()}

    def check(t: str) -> tuple[Any, str | None]:
        t = t.strip()
        if not 1 <= len(t) <= 100:
            return None, "A name of 1 to 100 characters."
        return (t, None) if t.lower() not in taken else (None, f"NetBox already has a site called {t}.")
    name = ctx.text("site_name", "What do you call the site? Use the name people say out loud.", check, "for example Mill Lane, or Head Office", look)
    return [{"op": "create_site", "target": {"kind": "site", "id": None}, "client_ref": "site", "fields": {"name": name, "country": code}}]


def flow_first_rack(ctx: Any) -> list[dict[str, Any]]:
    q = _q()
    e, f = ctx.e, ctx.line["finding"]
    site = e.sites[f["site_id"]]
    ctx.at(site_id=site["id"])
    look = [f"Go to where the network kit of {site['name']} stands.", "Note what is written on the rack or cabinet, and count its units (the numbers on the rails)."]
    taken = {r["name"].lower() for r in e.racks.values() if r["site_id"] == site["id"]}

    def check(t: str) -> tuple[Any, str | None]:
        t = t.strip()
        if not 1 <= len(t) <= 100:
            return None, "A name of 1 to 100 characters."
        return (t, None) if t.lower() not in taken else (None, f"{site['name']} already has a rack called {t}.")
    got = ctx.choice("rack_kind", f"What does the kit at {site['name']} stand in?",
                     [q.choice(f"u:{u}", label) for u, label in RACK_HEIGHTS] + [q.choice("other", "A rack of another height", "other"),
                                                                                 q.na("No rack: it stands on a shelf or on the wall")], look)
    if got == "other":
        height = ctx.text("rack_height", "How many units high is it? Read the highest number on the rails.",
                          lambda t: (int(t), None) if t.strip().isdigit() and 1 <= int(t) <= 100 else (None, "A whole number from 1 to 100."), "for example 27", look)
    else:
        height = int(got.split(":")[1])
    name = ctx.text("rack_name", "What is written on it, or what do you call it?", check, "for example R1, or Comms cabinet", look)
    return [{"op": "create_rack", "target": {"kind": "rack", "id": None}, "client_ref": "rack",
             "fields": {"site_id": site["id"], "name": name, "status": "active", "u_height": height}}]


def _free_unit(e: Estate, rack: dict[str, Any], height: float, taken: list[tuple[float, float]]) -> int | None:
    used = [(d["position"], d["u_height"] or 1) for d in e.devices.values() if d["rack_id"] == rack["id"] and d["position"] is not None] + taken
    for u in range(1, int(rack["u_height"]) - int(max(height, 1)) + 2):
        if all(not (u < p + h and p < u + max(height, 1)) for p, h in used):
            return u
    return None


def flow_first_devices(ctx: Any) -> list[dict[str, Any]]:
    """The boxes of a site, one at a time: what it is (from the library), what it does, what it is called, where it
    sits and its serial. Up to three in one pass. While a site is small, "More boxes at <site>" offers the next pass."""
    q = _q()
    e, f = ctx.e, ctx.line["finding"]
    site = e.sites[f["site_id"]]
    ctx.at(site_id=site["id"])
    if ctx.line["kind"] == "site_more_devices":
        ctx.choice("more", f"NetBox holds {len(e.site_devices(site['id']))} of the boxes at {site['name']}. Is there one it does not hold yet?",
                   [q.choice("yes", "Yes: add it", "candidate"), q.na(f"No: NetBox holds every box at {site['name']}")],
                   [f"Walk the racks and shelves of {site['name']} with the device list of NetBox in your hand."])
    racks = sorted((r for r in e.racks.values() if r["site_id"] == site["id"]), key=lambda r: natural(r["name"]))
    ops: list[dict[str, Any]] = []
    made: dict[str, str] = {}
    names = {(d["name"] or "").lower() for d in e.site_devices(site["id"])}
    serials: set[str] = set()
    placed: dict[int, list[tuple[float, float]]] = {}
    for n in range(MAX_NEW):
        what = "the first box" if n == 0 else "the next box"
        picked = find_type(ctx, f"box{n}", what=what)
        facts = type_facts(e, picked)
        label = f"{facts['manufacturer']} {facts['model']}"
        have = sorted(e.roles.values(), key=lambda r: natural(r["name"]))
        std = [r for r in ROLES if packs.slugify(r) not in {x["slug"] for x in have} and r.lower() not in {x["name"].lower() for x in have}]
        role_items = [(f"role:{x['id']}", x["name"], x["name"]) for x in have] + [(f"new:{packs.slugify(r)}", r, r) for r in std]
        got = q.pick(ctx, f"role{n}", f"What does the {label} do at {site['name']}?", role_items, ["Its job decides how the game reads it: a switch is a way through, a server is an end."],
                     noun="role", limit=7)
        role = e.roles[int(got.split(":")[1])]["name"] if got.startswith("role:") else next(r for r in ROLES if packs.slugify(r) == got.split(":", 1)[1])
        rack = None
        if len(racks) == 1:
            rack = racks[0]
        elif racks:
            where = q.pick(ctx, f"rack{n}", f"Which rack is the {label} in?", [(f"rack:{r['id']}", r["name"], r["name"]) for r in racks],
                           ["Read the name on the rack it stands in."], [q.choice("norack", "It is not in a rack", "other")], noun="rack")
            rack = e.racks[int(where.split(":")[1])] if where.startswith("rack:") else None
        free = _free_unit(e, rack, facts["u_height"], placed.get(rack["id"], [])) if rack else None
        fields = [{"id": "name", "label": "Its name", "type": "text", "max_length": 64, "hint": "its hostname, or what the label says", "suggestion": None, "options": []},
                  {"id": "serial", "label": "Serial number (if you can read it)", "type": "text", "max_length": 50, "hint": "on the pull-out tag or the rear label",
                   "suggestion": None, "options": []}]
        if rack is not None and facts["u_height"] > 0:
            fields.insert(1, {"id": "position", "label": f"Lowest unit it takes in rack {rack['name']} (if you know)", "type": "number", "integer": True, "min": 1,
                              "max": int(rack["u_height"]), "hint": f"the number on the rail beside its bottom edge; U{free} is the lowest free one" if free else
                              "the number on the rail beside its bottom edge", "suggestion": None, "options": []})
        sid = f"box{n}_details"
        values = ctx.form(sid, f"The {label}: its name, and what you can read on it. Leave blank what you do not know.", fields,
                          [f"Find the {label} at {site['name']}.", "Its name is on a label you stuck on, or it is the hostname it shows when you log in."])
        assert values is not None
        name = str(values.get("name") or "").strip()
        if not name:
            raise ctx.again(sid, "It needs a name: its hostname, or a name of your own.")
        if name.lower() in names:
            raise ctx.again(sid, f"{site['name']} already has a device called {name}.")
        device: dict[str, Any] = {"name": name, "site_id": site["id"], "role": role}
        if rack is not None:
            device["rack_id"] = rack["id"]
            if values.get("position") is not None:
                pos, height = float(values["position"]), max(facts["u_height"], 1)
                clash = next((d for d in e.devices.values() if d["rack_id"] == rack["id"] and d["position"] is not None
                              and pos < d["position"] + (d["u_height"] or 1) and d["position"] < pos + height), None)
                if clash is not None or any(pos < p + h and p < pos + height for p, h in placed.get(rack["id"], [])):
                    raise ctx.again(sid, f"U{pos:g} of rack {rack['name']} is taken by {nm(clash) if clash else 'the box you added before'}. Read the rail again.")
                if pos + height - 1 > rack["u_height"]:
                    raise ctx.again(sid, f"Rack {rack['name']} has {rack['u_height']} units; a box of {height:g}U does not fit at U{pos:g}.")
                device.update(position=pos, face="front")
                placed.setdefault(rack["id"], []).append((pos, height))
        serial = str(values.get("serial") or "").strip()
        if serial:
            how = check_serial(ctx, sid, f"box{n}", facts["manufacturer"], facts["model"], serial, name, taken=serials)
            if how != "skip":
                device["serial"] = serial
                serials.add(serial.lower())
                if how == "unverified":
                    device["unverified"] = True
        type_op, type_ref = type_ops(picked, f"type{n}", made=made)
        device["device_type_id"] = type_ref
        ops += type_op + [{"op": "create_device", "target": {"kind": "device", "id": None}, "client_ref": f"box{n}", "fields": device}]
        names.add(name.lower())
        if n == MAX_NEW - 1:
            break
        more = ctx.choice(f"more{n}", f"Is there another box at {site['name']} to add now?",
                          [q.choice("yes", "Yes, another one", "candidate"), q.choice("no", "No, that is all for now", "confirm")], [], unknown=False)
        if more == "no":
            break
    return ops


def flow_whose_line(ctx: Any) -> list[dict[str, Any]]:
    q = _q()
    f = ctx.line["finding"]
    ctx.at(site_id=f["site_id"])
    _, _, ops = ask_provider(ctx, f["site_id"], [q.na("We do not keep carriers in NetBox")])
    if not ops:
        raise q.Stop("parked", None, ["NetBox already holds that carrier. Read NetBox again: this line closes by itself."])
    return ops


def flow_what_box(ctx: Any) -> list[dict[str, Any]]:
    q = _q()
    e, f = ctx.e, ctx.line["finding"]
    dev = e.devices[f["data"]["device_id"]]
    ctx.at(device_id=dev["id"])
    picked = find_type(ctx, "box", what=f"{nm(dev)} ({e.where(dev)})", closing=[q.na("It really is a generic box: leave it")])
    if picked[0] == "existing" and picked[1] == dev.get("type_id"):
        raise q.Stop("parked", None, ["That is the model NetBox already holds for it. Search for its real model, or say it really is a generic box."])
    type_op, type_ref = type_ops(picked, "type")
    return type_op + [{"op": "set_device_type", "target": {"kind": "device", "id": dev["id"]}, "fields": {"device_type_id": type_ref}}]


def flow_part_number(ctx: Any) -> list[dict[str, Any]]:
    """A model's part number against the label of one box of that model. The only number written is the library's own,
    and only when the player reads it off the box; a label that says something else is not written, and the line says
    which model that number belongs to when the library knows."""
    q = _q()
    e, f = ctx.e, ctx.line["finding"]
    d = f["data"]
    t, dev = e.dtypes[d["type_id"]], e.devices[d["device_id"]]
    ctx.at(device_id=dev["id"])
    index = packs.load().index()
    model, have = d["model"], d.get("have")
    # what the library lists is read from the library now, never from the finding: only one number, never a pattern
    expected = index.part_check(t["manufacturer"], t["model"], None)["expected"]
    look = [f"Find {nm(dev)}: {e.where(dev)}.", "The part number is on the same label as the serial, often after \"P/N\" or \"PID\"."]
    if not expected:
        raise q.Stop("parked", None, [f"The device-type library lists no single part number for the {model}, so the game has nothing to check a label against.",
                                      "Record the part number in NetBox itself (Devices > Device Types) if you want it there."])
    other = index.by_part(t["manufacturer"], have) if have else None
    hint = f" ({have} is the part number of the {other['model']}.)" if other is not None else ""
    got = ctx.choice("label", q.clip(f"{nm(dev)} is a {model}. What part number is on its label?{hint}", 300),
                     [q.choice("fix", f"{expected}: record that"), q.choice("else", "Something else", "other"),
                      q.na(f"Leave {have} in NetBox" if have else "Leave it empty in NetBox")], look)
    if got == "else":
        typed = ctx.text("label_says", "What does the label say? Type the part number exactly as printed.",
                         lambda v: (v.strip(), None) if 1 <= len(v.strip()) <= 50 else (None, "A part number of 1 to 50 characters."),
                         "exactly as printed", look)
        if index.part_check(t["manufacturer"], t["model"], typed)["status"] != "verified":
            belongs = index.by_part(t["manufacturer"], typed)
            if belongs is not None:
                raise q.Stop("parked", None, [f"{typed} is the part number of the {belongs['manufacturer']} {belongs['model']}. If that is what the label says, "
                                              f"{nm(dev)} is not a {model}: change its device type in NetBox (Devices > {nm(dev)} > Edit).",
                                              "Then read NetBox again: this line closes by itself when the model and the part number agree."],
                             note=f"The label of {nm(dev)} carries the part number of another model.")
            raise q.Stop("parked", None, [f"The device-type library lists {expected} for the {model}, and the label says {typed}. A model is sold under more "
                                          "than one part number (a region, a bundle), and the library lists one: the game writes only a number it can confirm.",
                                          f"If {typed} is right, record it in NetBox itself (Devices > Device Types > {t['model']})."],
                         note="The label carries a part number the device-type library does not list, so the game wrote nothing.")
    return [{"op": "set_part_number", "target": {"kind": "device_type", "id": t["id"]}, "fields": {"part_number": expected}}]


def flow_modules(ctx: Any) -> list[dict[str, Any]]:
    q = _q()
    e, f = ctx.e, ctx.line["finding"]
    dev = e.devices[f["data"]["device_id"]]
    ctx.at(device_id=dev["id"])
    bays = sorted((b for b in e.module_bays.values() if b["device_id"] == dev["id"] and b["module_id"] is None), key=lambda b: natural(b["name"]))
    look = [f"Find {nm(dev)}: {e.where(dev)}.", "Look at each bay: a power supply, a fan tray and a line card each carry a label with a model and a serial."]
    got = q.pick(ctx, "bay", f"Which bay of {nm(dev)} has something fitted?", [(f"bay:{b['id']}", b["name"], b["name"]) for b in bays], look,
                 [q.na("Nothing is fitted: the bays are empty")], noun="bay")
    bay = e.module_bays[int(got.split(":")[1])]
    picked = find_type(ctx, "mod", kind="module", what=f"the module in bay {bay['name']}")
    serial = ctx.text("mod_serial", f"What serial is on the module in bay {bay['name']}?",
                      lambda t: (t.strip(), None) if 1 <= len(t.strip()) <= 50 else (None, "At most 50 characters."), "on the module's own label", look, soft=True)
    type_op, type_ref = type_ops(picked, "mtype", kind="module")
    return type_op + [{"op": "install_module", "target": {"kind": "module", "id": None}, "client_ref": "module",
                       "fields": {"device_id": dev["id"], "module_bay_id": bay["id"], "module_type_id": type_ref, **({"serial": serial} if serial else {})}}]


FLOWS = {"estate_no_site": flow_first_site, "site_no_rack": flow_first_rack, "site_no_devices": flow_first_devices, "site_no_provider": flow_whose_line,
         "site_more_devices": flow_first_devices,
         "device_type_unknown": flow_what_box, "device_type_part_number": flow_part_number, "module_bays_unrecorded": flow_modules}


def audit_serials(ctx: Any, form_sid: str, values: dict[str, Any]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """The serials of an audit form against the vendors' rules: the values to write, and the ops that tag a device
    whose serial the player confirmed although it does not look like the vendor's."""
    e = ctx.e
    out, marks = dict(values), []
    seen: set[str] = set()
    for fid, value in values.items():
        _, did, fld = fid.split(":")
        if fld != "serial":
            continue
        d = e.devices[int(did)]
        how = check_serial(ctx, form_sid, f"serial{did}", d.get("manufacturer"), d.get("model"), str(value), nm(d), own_id=d["id"], taken=seen)
        seen.add(str(value).lower())
        if how == "skip":
            out.pop(fid)
        elif how == "unverified" and UNVERIFIED not in d.get("tags", []):
            marks.append({"op": "mark_unverified", "target": {"kind": "device", "id": d["id"]},
                          "fields": {"reason": "the serial does not look like the vendor's; the player confirmed the label"}})
    return out, marks


# ---------------------------------------------------------------- words
def describe_op(e: Estate, op: dict[str, Any], refs: dict[str, str]) -> str | None:
    """One setup op in plain words, for the preview."""
    f, name, target = op.get("fields") or {}, op["op"], op["target"]
    index = packs.load().index()
    ref = "@" + op["client_ref"] if op.get("client_ref") else None

    def type_name(v: Any) -> str:
        if isinstance(v, str):
            return refs.get(v, "the new model")
        t = e.dtypes.get(v)
        return f"{t['manufacturer']} {t['model']}" if t else f"device type {v}"
    if name == "create_rack":
        return f"New rack {f['name']} at {e.sites.get(f['site_id'], {}).get('name', 'the site')} ({f.get('u_height', 42)}U)."
    if name == "create_site":
        return f"New site {f['name']}" + (f" in {country_name(f['country'])}." if f.get("country") else ".")
    if name == "create_provider":
        hit = packs.match_provider({"name": f["name"], "slug": f.get("slug"), "asns": f.get("asns") or [], "aka": f.get("aka") or []}, list(e.providers.values()))
        if ref:
            refs[ref] = (hit or f)["name"]
        if hit is not None:
            return f"{f['name']} is already in NetBox as {hit['name']}: that provider is used, none is added."
        asns = ", ".join(f"AS{a['asn']}" for a in f.get("asns") or [])
        pack = next((p for p in packs.load().providers if p["id"] == f.get("pack")), None)
        src = f", from {pack['title'][0].lower() + pack['title'][1:]}" if pack else ", as you typed it (no pack lists it)"
        return f"New provider {f['name']}" + (f" ({asns})" if asns else "") + src + "."
    if name == "create_circuit_type":
        return f"New circuit type {f['name']}."
    if name in ("add_device_type", "add_module_type"):
        kind = "device" if name == "add_device_type" else "module"
        row = index.by_file(kind, f["library_file"]) if f.get("library_file") else None
        label = f"{row['manufacturer']} {row['model']}" if row else f"{f.get('manufacturer')} {f.get('model')}"
        if ref:
            refs[ref] = label
        if row is None and kind == "module":
            return f"New module type {label}, recorded by hand and marked unverified: the device-type library does not hold it."
        if row is None:
            return f"New device type {label}, recorded by hand and marked unverified: the device-type library does not hold it, so it earns no bonus."
        bits = ([f"{row['u_height']:g}U"] if kind == "device" else []) + [f"{row[k]} {w if row[k] != 1 else w[:-1]}" for k, w in (
            ("interfaces", "interfaces"), ("console_ports", "console ports"), ("power_ports", "power ports"), ("front_ports", "front ports"),
            ("rear_ports", "rear ports"), ("module_bays", "module bays")) if row.get(k)]
        have = held_type(e, index, row) if kind == "device" else None
        if have is not None and have.get("templates") == 0:
            return (f"NetBox holds the device type {have['manufacturer']} {have['model']} with no ports: they are added from the device-type library "
                    f"({', '.join(bits[1:]) or 'no ports'}). Nothing it has is changed; devices made from it before keep what they have.")
        part = row["part_number"] if row["_wild"] is None else ""
        return f"Import the {kind} type {label} from the device-type library (part number {part or 'not given'}; {', '.join(bits) or 'no ports'}), with its manufacturer if NetBox lacks it."
    if name == "create_device":
        rack = e.racks.get(f.get("rack_id") or 0)
        where = (f", rack {rack['name']}" + (f" unit {f['position']:g}" if f.get("position") is not None else " (unit not known yet)")) if rack else ", in no rack"
        serial = (f", serial {f['serial']}" + (" (marked unverified: it does not look like the vendor's)" if f.get("unverified") else "")) if f.get("serial") else ""
        if ref:
            refs[ref] = f["name"]
        return f"New device {f['name']}: a {type_name(f['device_type_id'])} as {f['role']} at {e.sites.get(f['site_id'], {}).get('name', 'the site')}{where}{serial}."
    if name == "set_device_type":
        return (f"{e.device_name(target['id'])}: becomes a {type_name(f['device_type_id'])}. The ports it lacks are added from the model; "
                "nothing it has is removed. NetBox refuses this if the device has a port the model does not have.")
    if name == "install_module":
        bay = e.module_bays.get(f["module_bay_id"], {}).get("name", "a bay")
        return f"{e.device_name(f['device_id'])}, bay {bay}: a {refs.get(f['module_type_id'], 'module') if isinstance(f['module_type_id'], str) else 'module'}" + \
               (f", serial {f['serial']}." if f.get("serial") else ".")
    if name == "set_part_number":
        t = e.dtypes.get(target["id"], {})
        return (f"{type_name(target['id'])}: part number {t.get('part_number') or '(none)'} becomes {f['part_number']}, as the device-type library lists it. "
                "Every device of that model shows it.")
    if name == "mark_unverified":
        who = e.device_name(target["id"]) if target["kind"] == "device" else type_name(target["id"])
        return f"{who}: marked unverified in NetBox ({f['reason']})."
    return None


def words(e: Estate, f: dict[str, Any]) -> dict[str, str]:
    return {"made": "it"}


# ---------------------------------------------------------------- the bonus (design rule: only for values that pass the checks)
def bonus(after: Estate, line: dict[str, Any], newly: list[str]) -> tuple[int, str | None]:
    """XP on top of a line's own for what NetBox, read after the write, shows as passing a check: a box whose model the
    device-type library confirms, a serial that fits its vendor's sourced rule. `newly`: the checks this apply was paid
    for, so nothing is counted twice. A device tagged unverified earns none, whatever else is right about it."""
    kind = line["kind"]
    p = packs.load()
    if not newly:
        return 0, None
    if kind in ("rack_audit", "shelf_audit"):
        good = bad = 0
        for cid in newly:
            parts = cid.split(":")
            d = after.devices.get(int(parts[1])) if len(parts) == 3 and parts[0] == "device" and parts[2] == "serial" and parts[1].isdigit() else None
            if d is None or not d["serial"]:
                continue
            ok = UNVERIFIED not in d.get("tags", []) and p.serial_check(d.get("manufacturer"), d.get("model"), d["serial"])["status"] == "ok"
            good, bad = good + ok, bad + (UNVERIFIED in d.get("tags", []))
        xp = good * SERIAL_BONUS
        return xp, (f"{good} serial{'s' if good != 1 else ''} fit{'s' if good == 1 else ''} the vendor's own pattern: +{xp} XP." if good else
                    "No bonus for a serial marked unverified." if bad else None)
    if kind in ("site_no_devices", "site_more_devices", "device_type_unknown"):
        data = line["finding"].get("data", {})
        if kind == "site_no_devices":
            devs = after.site_devices(line["site_id"])[:MAX_NEW]
        elif kind == "site_more_devices":
            devs = [d for d in after.site_devices(line["site_id"]) if d["id"] not in set(data.get("known") or [])][:MAX_NEW]
        else:
            devs = [after.devices[i] for i in [data.get("device_id")] if i in after.devices]
            if not devs:
                ids = [int(c.split(":")[2]) for c in newly if c.startswith("device_type_unknown:device:")]
                devs = [after.devices[i] for i in ids if i in after.devices]
        marked = [d for d in devs if UNVERIFIED in d.get("tags", [])]
        good = sum(1 for d in devs if d not in marked and verified_type(after.dtypes.get(d.get("type_id") or 0)) == "verified")
        xp = good * TYPE_BONUS
        if good:
            return xp, (f"{good} box{'es' if good != 1 else ''} of a model the device-type library confirms: +{xp} XP."
                        + (" No bonus for a box marked unverified." if marked else ""))
        if marked:
            return 0, "No bonus for a box marked unverified."
        return 0, "No bonus: the model is not one the device-type library holds (it is marked unverified)." if devs else None
    return 0, None


# ---------------------------------------------------------------- the catalogue (design rule 5)
def catalogue(e: Estate) -> dict[str, Any]:
    """The device types of the player's own NetBox as a collection: one entry per type, verified where the device-type
    library confirms the maker, the model and the part number. Derived from the estate alone."""
    index = packs.load().index()
    counts: dict[int, int] = {}
    for d in e.devices.values():
        if d.get("type_id") is not None:
            counts[d["type_id"]] = counts.get(d["type_id"], 0) + 1
    items = []
    for t in sorted(e.dtypes.values(), key=lambda t: (natural(t["manufacturer"] or ""), natural(t["model"]))):
        status = verified_type(t)
        check = index.part_check(t["manufacturer"], t["model"], t["part_number"])
        row = index.lookup(t["manufacturer"], t["model"])
        named = f"{row['manufacturer']} {row['model']}" if row else ""
        if unknown_type(t["manufacturer"], t["model"]):
            note = "A placeholder, not a model: \"What is this box?\" asks about its devices."
        elif status == "mismatch":
            note = (f"NetBox holds the part number {t['part_number']}, which the device-type library lists for the {check['other']}. "
                    "\"The part number of ...\" asks about it.")
        elif UNVERIFIED in t["tags"]:
            note = "Recorded by hand: the device-type library does not hold this model."
        elif status == "unverified":
            note = "Not in the device-type library."
        else:
            # confirmed; what an admin may still want to know about it is said, never held against it
            notes = []
            if row is not None and packs._words(t["model"]) != row["_model"]:
                notes.append(f"The device-type library calls this model {named} ({t['model']} is its part number there).")
            elif row is not None and not packs.same_name(t["manufacturer"], row["manufacturer"]):
                notes.append(f"The device-type library files this maker as {row['manufacturer']}.")
            if check["status"] == "differs":
                notes.append(f"NetBox holds the part number {t['part_number']}; the library lists " + (check["expected"] or "a pattern") +
                             " for this model. A model is sold under more than one number, so neither is called wrong.")
            if t.get("templates") == 0 and row is not None and row["interfaces"]:
                notes.append(f"NetBox holds it without ports; the library's has {row['interfaces']}. Adding a box of this model brings them in.")
            note = " ".join(notes) or None
        items.append({"id": t["id"], "manufacturer": t["manufacturer"] or "", "model": t["model"], "part_number": t["part_number"] or None,
                      "status": status, "note": note, "devices": counts.get(t["id"], 0)})
    makers: dict[str, dict[str, Any]] = {}
    for it in items:
        m = makers.setdefault(it["manufacturer"], {"name": it["manufacturer"], "verified": 0, "total": 0})
        m["total"] += 1
        m["verified"] += it["status"] == "verified"
    return {"items": items, "manufacturers": sorted(makers.values(), key=lambda m: natural(m["name"])),
            "verified": sum(1 for it in items if it["status"] == "verified"), "total": len(items)}
