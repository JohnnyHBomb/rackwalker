"""Quest lines: a finding becomes a short conversation that ends in one previewed change to NetBox.

Pure functions of an `Estate` and its `Analysis` (findings.py); docs/quests.md describes them for a NetBox admin.

`lines(e, a)` gives one quest line per finding, most valuable first. `evaluate(e, line, answers, tools)` plays the
line against the answers given so far and returns where it stands:

    asking     the next question (`step`): choices built from real objects, a typed value, or a short form
    ready      every question is answered: `proposal` is the ChangeSet to preview and apply
    parked     the player said "I don't know yet": `checklist` says what to look for on site; the line stays open
               without penalty and asks the same question again when the player comes back
    dismissed  the player said the finding is how things really are (no link on purpose): nothing to write
    confirmed  a spot check the player answered "yes, as recorded": nothing to write, the line is done

A flow is plain Python: it calls `ctx.choice(...)`, `ctx.text(...)` or `ctx.form(...)`, which returns the stored
answer or stops the flow at that question. A stored answer that no longer fits the estate (the port now has a cable)
is asked again. Nothing is ever filled in for the player: every value in a proposal is one they picked or typed.

A line is complete when a fresh analysis of NetBox shows every one of its checks as passed, never because the client
says so, and never because the thing it was about has gone (a deleted device completes nothing).

No list of choices hides what NetBox holds: a question shows the likeliest few objects and, when there are more,
takes the name of any other one (`pick`). Every line has an answer for "that is how it is, on purpose", which closes
it with nothing written and no XP.
"""
from __future__ import annotations

import ipaddress
import math
import re
from typing import Any

from .estate import Estate, is_passive, is_physical, is_router, natural
from .estate import name_of as nm
from .findings import Analysis
from .findings import _some as some

XP = {"connectivity": 120, "identity": 50, "layout": 30}
# Kinds worth less than their rank: confirming a subnet is one answer, not a walk to the rack.
KIND_XP = {"address_no_prefix": 50}
MINUTES = {"connectivity": 4, "identity": 5, "layout": 3}
MAX_CANDIDATES = 6
MAX_PORTS = 6
MAX_BUNDLE = 4
CABLE_TYPES = [("cat6", "Copper patch lead (Cat 6)"), ("mmf", "Multimode fibre"), ("smf", "Single-mode fibre"),
               ("dac-passive", "Direct-attach copper (DAC)")]
UNKNOWN = {"id": "unknown", "label": "I don't know yet", "kind": "unknown"}
OTHER = {"id": "other", "label": "Something not listed", "kind": "other"}


def clip(text: str, n: int) -> str:
    return text if len(text) <= n else text[: n - 1].rstrip() + "…"


def choice(cid: str, label: str, kind: str = "candidate") -> dict[str, str]:
    return {"id": cid, "label": clip(label, 48), "kind": kind}


class Stop(Exception):
    """The flow cannot go further: it waits for an answer, is parked or is dismissed."""

    def __init__(self, status: str, step: dict[str, Any] | None = None, checklist: list[str] | None = None, note: str | None = None):
        self.status, self.step, self.checklist, self.note = status, step, checklist or [], note


class Ctx:
    def __init__(self, e: Estate, line: dict[str, Any], answers: dict[str, Any], tools: frozenset[str]):
        self.e, self.line, self.answers, self.tools = e, line, answers, tools
        self.said: list[dict[str, str]] = []      # what the player has answered, in words (the quest log shows it)
        self.target: dict[str, Any] | None = None  # the object the marker points at
        self.error: str | None = None
        self.skipped: list[str] = []               # checklist lines for bundle items the player did not know
        self.answered: list[str] = []              # ids of the steps answered so far, in the order they were asked
        self.waived: list[str] = []                # checks the player answered with "there is none, on purpose"

    def at(self, device_id: int | None = None, rack_id: int | None = None, site_id: int | None = None) -> None:
        e = self.e
        if device_id is not None and device_id in e.devices:
            d = e.devices[device_id]
            self.target = {"site_id": d["site_id"], "view": f"rack:{d['rack_id']}" if d["rack_id"] else f"site:{d['site_id']}",
                           "entity": f"device:{device_id}", "label": f"{d['name'] or 'device'} ({e.where(d)})"}
        elif rack_id is not None and rack_id in e.racks:
            r = e.racks[rack_id]
            self.target = {"site_id": r["site_id"], "view": f"rack:{rack_id}", "entity": f"rack:{rack_id}", "label": f"rack {r['name']}"}
        elif site_id is not None and site_id in e.sites:
            self.target = {"site_id": site_id, "view": f"site:{site_id}", "entity": f"site:{site_id}", "label": e.sites[site_id]["name"]}

    def _step(self, sid: str, prompt: str, input_: str, choices: list[dict[str, str]] | None = None,
              fields: list[dict[str, Any]] | None = None, hint: str | None = None) -> dict[str, Any]:
        return {"id": sid, "prompt": prompt, "input": input_, "choices": choices or [], "fields": fields or [], "hint": hint,
                "error": self.error}

    def choice(self, sid: str, prompt: str, choices: list[dict[str, str]], checklist: list[str], unknown: bool = True,
               soft: bool = False) -> str:
        """The id of the picked choice. "I don't know yet" parks the line here (or, with `soft`, returns "unknown" so
        a bundle can go on to its next item)."""
        all_choices = choices + ([UNKNOWN] if unknown else [])
        step = self._step(sid, prompt, "choice", all_choices)
        ans = self.answers.get(sid)
        picked = ans.get("choice") if isinstance(ans, dict) else None
        found = next((c for c in all_choices if c["id"] == picked), None)
        if found is None:
            raise Stop("asking", step)
        if found["kind"] == "unknown":
            if soft:
                self.answered.append(sid)
                self.skipped.extend(checklist)
                return "unknown"
            raise Stop("parked", step, checklist)
        self.answered.append(sid)
        if found["kind"] == "not_applicable":
            raise Stop("dismissed", None, note=found["label"])
        self.said.append({"step": sid, "question": prompt, "answer": found["label"]})
        return found["id"]

    def text(self, sid: str, prompt: str, check: Any, hint: str, checklist: list[str]) -> Any:
        """A typed value, passed through `check(text) -> (value, error)`. An empty answer parks the line."""
        ans = self.answers.get(sid)
        raw = ans.get("text") if isinstance(ans, dict) else None
        if isinstance(ans, dict) and ans.get("choice") == "unknown":
            raise Stop("parked", self._step(sid, prompt, "text", [UNKNOWN], hint=hint), checklist)
        if isinstance(raw, str) and raw.strip():
            value, err = check(raw.strip())
            if err is None:
                self.answered.append(sid)
                self.said.append({"step": sid, "question": prompt, "answer": raw.strip()})
                return value
            self.error = err
        raise Stop("asking", self._step(sid, prompt, "text", [UNKNOWN], hint=hint))

    def form(self, sid: str, prompt: str, fields: list[dict[str, Any]], checklist: list[str], hint: str | None = None,
             na: str | None = None) -> dict[str, Any]:
        """The filled-in fields of a form (blank ones left out). A form with nothing filled in parks the line; `na` is
        the label of the answer "there is nothing to fill in, on purpose", which closes the line."""
        extra = ([choice("none", na, "not_applicable")] if na else []) + [UNKNOWN]
        step = self._step(sid, prompt, "form", extra, fields, hint)
        ans = self.answers.get(sid)
        if not isinstance(ans, dict):
            raise Stop("asking", step)
        if na and ans.get("choice") == "none":
            self.answered.append(sid)
            raise Stop("dismissed", None, note=na)
        values = ans.get("values") if isinstance(ans.get("values"), dict) else {}
        ids = {f["id"]: f for f in fields}
        out: dict[str, Any] = {}
        errors: list[str] = []
        for fid, raw in values.items():
            f = ids.get(fid)
            if f is None or raw is None or (isinstance(raw, str) and not raw.strip()):
                continue
            value, err = check_field(f, raw)
            if err:
                errors.append(f"{f['label']}: {err}")
            else:
                out[fid] = value
        if errors:
            self.error = " ".join(errors)[:300]
            raise Stop("asking", self._step(sid, prompt, "form", extra, fields, hint))
        if not out:
            raise Stop("parked", step, checklist)
        self.answered.append(sid)
        self.said.append({"step": sid, "question": prompt, "answer": f"{len(out)} of {len(fields)} filled in"})
        return out


def check_field(f: dict[str, Any], raw: Any) -> tuple[Any, str | None]:
    if f["type"] == "number":
        try:
            v = float(raw)
        except (TypeError, ValueError):
            return None, "not a number"
        if not math.isfinite(v):
            return None, "not a number"
        if f.get("min") is not None and v < f["min"] or f.get("max") is not None and v > f["max"]:
            return None, f"between {f.get('min')} and {f.get('max')}"
        return (int(v) if v == int(v) and f.get("integer") else v), None
    if f["type"] == "select":
        opt = next((o for o in f.get("options", []) if o["id"] == str(raw)), None)
        return (opt["id"], None) if opt else (None, "not one of the options")
    text = str(raw).strip()
    if len(text) > f.get("max_length", 50):
        return None, f"at most {f.get('max_length', 50)} characters"
    if f.get("pattern") and not re.fullmatch(f["pattern"], text):
        return None, f.get("pattern_hint") or "not in the expected form"
    return text, None


# ---------------------------------------------------------------- shared question blocks
Item = tuple[str, str, str]   # (choice id, label, the name a player would type)
NOT_IN_NETBOX = {"id": "other", "label": "A device that is not in NetBox yet", "kind": "other"}


def na(label: str) -> dict[str, str]:
    """The answer "that is how it is, on purpose": it closes the line with nothing written and no XP."""
    return choice("none", label, "not_applicable")


def match_name(text: str, items: list[Item], noun: str) -> tuple[Any, str | None]:
    """The one item a typed name means: its exact name, else the only one that starts with it, else the only one that
    holds it. Several matches, or none, are said back so the player can type more."""
    t = text.strip().lower()
    for test in (lambda n: n == t, lambda n: n.startswith(t), lambda n: t in n):
        hits = [it for it in items if test(it[2].lower())]
        if len(hits) == 1:
            return hits[0][0], None
        if hits:
            return None, clip(f"{len(hits)} {noun}s match {text!r}: {', '.join(h[2] for h in hits[:4])}{' ...' if len(hits) > 4 else ''}. Type more of the name.", 200)
    return None, f"NetBox has no {noun} called {text!r} that fits here. Check the spelling, or go back and say it is not in NetBox yet."


def pick(ctx: Ctx, sid: str, prompt: str, items: list[Item], checklist: list[str], extra: list[dict[str, str]] | None = None,
         noun: str = "device", soft: bool = False, unknown: bool = True, wider: list[Item] | None = None,
         limit: int | None = None) -> str:
    """One of `items`, which may be many: the likeliest few are shown as choices; when there are more (or `wider`
    names further objects that fit), "another one that is in NetBox" asks for its name. Nothing that exists in NetBox is
    out of reach because a list was short."""
    limit = MAX_CANDIDATES if limit is None else limit
    short = items[:limit]
    seen = {it[0] for it in items}
    rest = items + [it for it in (wider or []) if it[0] not in seen]
    choices = [choice(i, label) for i, label, _ in short]
    if len(rest) > len(short):
        choices.append(choice("named", f"Another {noun} that is in NetBox", "other"))
    picked = ctx.choice(sid, prompt, choices + (extra or []), checklist, unknown=unknown, soft=soft)
    if picked != "named":
        return picked
    more = [it for it in rest if it not in short]
    return ctx.text(sid + "_name", f"Type its name, or the start of it ({len(rest)} {noun}s in NetBox fit here).",
                    lambda t: match_name(t, rest, noun), f"for example {more[0][2] if more else rest[0][2]}", checklist)


def _dev_label(e: Estate, d: dict[str, Any], site: bool = False) -> str:
    role = d.get("role_name") or d.get("role") or "device"
    return f"{nm(d)} ({role.lower()}{', ' + e.sites[d['site_id']]['name'] if site and d['site_id'] in e.sites else ''})"


def dev_item(e: Estate, d: dict[str, Any], site: bool = False) -> Item:
    return f"device:{d['id']}", _dev_label(e, d, site), nm(d)


def circuit_items(e: Estate, site_id: int, between_sites: bool, provider_id: int | None = None) -> list[Item]:
    """Circuits that can still get an end at a site: one with a free end recorded here, or with a side nobody has
    recorded yet. For an uplink or the way out, a circuit whose other end is at another of the estate's sites is left
    out (it is that site's line); for the link between sites those come first and say where the other end is."""
    out: list[tuple[int, Item]] = []
    for cid, c in sorted(e.circuits.items()):
        if provider_id is not None and c["provider_id"] != provider_id:
            continue
        terms = [t for t in e.cterms.values() if t["circuit_id"] == cid]
        here_free = any(t["site_id"] == site_id and t["cable_id"] is None for t in terms)
        here_done = any(t["site_id"] == site_id and t["cable_id"] is not None for t in terms)
        elsewhere = sorted({t["site_id"] for t in terms if t["site_id"] not in (None, site_id)})
        if here_done or not (here_free or len(terms) < 2):
            continue
        if elsewhere and not between_sites:
            continue
        prov = e.providers.get(c["provider_id"] or 0, {}).get("name", "provider")
        far = f" to {e.sites[elsewhere[0]]['name']}" if elsewhere and elsewhere[0] in e.sites else ""
        out.append((0 if elsewhere else 1, (f"circuit:{cid}", f"circuit {c['cid']}{far} ({prov})", c["cid"])))
    return [it for _, it in sorted(out, key=lambda x: x[0])]


def peer_items(e: Estate, dev: dict[str, Any], among: list[int] | None = None, circuits: bool = True,
               linked_too: bool = False, between_sites: bool = False) -> tuple[list[Item], list[Item]]:
    """Plausible far ends for a link from `dev`, likeliest first, and (second) every other device with a free port
    that the player may name. Likeliest: the same room before other rooms; for a switch or a host, switches before
    routers (the busiest first: that is where uplinks gather); for a router or firewall, and for a link to another site,
    routers and firewalls first.
    Then circuits that can still land here, then patch panels in the same rack. Devices `dev` already reaches are left
    out, unless `linked_too` (a second link, such as a described port)."""
    comp = e.components()
    pool = [e.devices[i] for i in among if i in e.devices] if among is not None else [d for d in e.site_devices(dev["site_id"]) if e.is_network(d)]
    pool = [d for d in pool if d["id"] != dev["id"] and (linked_too or not comp.same(d["id"], dev["id"])) and e.free_ports(d["id"])]
    to_switch = e.is_switch(dev) or not e.is_network(dev)
    if linked_too:
        pool.sort(key=lambda d: (d["rack_id"] != dev["rack_id"], d["location_id"] != dev["location_id"], not is_router(d),
                                 natural(d["name"] or ""), d["id"]))
    else:
        pool.sort(key=lambda d: (d["site_id"] != dev["site_id"], d["location_id"] != dev["location_id"],
                                 (not e.is_switch(d)) if to_switch and not between_sites else (not is_router(d)), -comp.degree(d["id"]),
                                 natural(d["name"] or ""), d["id"]))
    out = [dev_item(e, d, site=between_sites) for d in pool]
    if circuits:
        out += circuit_items(e, dev["site_id"], between_sites)
    if among is None:
        panels = [d for d in e.site_devices(dev["site_id"]) if is_passive(d) and d["rack_id"] == dev["rack_id"] and d["rack_id"] is not None
                  and any(p["cable_id"] is None for p in e.device_ports(d["id"], ("front_port",)))]
        out += [(f"device:{d['id']}", f"{nm(d)} (patch panel)", nm(d)) for d in panels]
    # whatever else has a free port, here first: a server, a device whose role says nothing, a device at another site
    listed = {it[0] for it in out}
    others = sorted((d for d in e.devices.values() if d["id"] != dev["id"] and f"device:{d['id']}" not in listed and not is_passive(d)
                     and (between_sites or d["site_id"] == dev["site_id"]) and e.free_ports(d["id"])),
                    key=lambda d: (d["site_id"] != dev["site_id"], natural(d["name"] or ""), d["id"]))
    return out, [dev_item(e, d, site=between_sites) for d in others]


def not_in_netbox(ctx: Ctx, sid: str, dev: dict[str, Any], checklist: list[str]) -> None:
    """The far end is a device NetBox does not have: its name is kept, and the line waits until it is added. A name
    NetBox does have is not accepted here: that device can be picked."""
    e = ctx.e
    known = {nm(d).lower() for d in e.devices.values()}

    def check(t: str) -> tuple[Any, str | None]:
        if len(t) > 64:
            return None, "At most 64 characters."
        if t.lower() in known:
            return None, f"{t} is in NetBox. Change your last answer and pick \"Another device that is in NetBox\"."
        return t, None
    name = ctx.text(sid, "What is it called? The game cannot add a device for you yet, but it will remember.", check,
                    "the name on its label", checklist)
    raise Stop("parked", None, [f"Add {name} to NetBox (Devices > Add, with its type and role).", "Come back: it will then be one of the choices."],
               note=f"You said {nm(dev)} connects to {name}, which is not in NetBox yet.")


def look_checklist(e: Estate, dev: dict[str, Any]) -> list[str]:
    return [f"Find {nm(dev)}: {e.where(dev)}.",
            "Follow the cable from its uplink port (often the highest-numbered port, or a fibre port).",
            "At the far end, read the device's name and the port's label.",
            "Note what kind of cable it is (the jacket says Cat 6, OM4 or OS2)."]


def ask_port(ctx: Ctx, sid: str, dev: dict[str, Any], prompt: str, kinds: tuple[str, ...] = ("interface",), soft: bool = False,
             used: set[tuple[str, int]] | None = None) -> tuple[str, int] | None:
    """Which free port of a device: the likeliest few, or any other free port by its name."""
    e = ctx.e
    if kinds == ("interface",):
        free = likely_uplinks(e.free_ports(dev["id"]))
    else:
        free = [p for p in e.device_ports(dev["id"], kinds) if p["cable_id"] is None]
    free = [p for p in free if (p["kind"], p["id"]) not in (used or set())]
    checklist = [f"Find {nm(dev)}: {e.where(dev)}.", "Read the label printed next to the port the cable is in."]
    picked = pick(ctx, sid, prompt, [(f"{p['kind']}:{p['id']}", p["name"], p["name"]) for p in free], checklist, noun="free port", soft=soft,
                  limit=MAX_PORTS)
    if picked == "unknown":
        return None
    kind, _, oid = picked.rpartition(":")
    return kind, int(oid)


FAST_PORT = ("sfp", "base-x", "10g", "25g", "40g", "50g", "100g", "200g", "400g")


def likely_uplinks(free: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Free ports, the likeliest carrier of a link between devices first: fibre cages and fast ports before copper
    access ports, the highest-numbered first inside each kind (uplinks sit at the end of the front panel), and a
    management-only port last."""
    by_name = sorted(free, key=lambda p: (natural(p["name"]), p["id"]), reverse=True)
    return sorted(by_name, key=lambda p: (p["mgmt_only"], not any(w in (p["type"] or "") for w in FAST_PORT)))


def ask_cable_type(ctx: Ctx, sid: str = "cable_type") -> str | None:
    picked = ctx.choice(sid, "What kind of cable is it?",
                        [choice(v, label) for v, label in CABLE_TYPES] + [choice("skip", "Not sure: leave the type empty", "skip")],
                        [], unknown=False)
    return None if picked == "skip" else picked


def cable_op(a: tuple[str, Any], b: tuple[str, Any], ctype: str | None) -> dict[str, Any]:
    f: dict[str, Any] = {"a_kind": a[0], "a_id": a[1], "b_kind": b[0], "b_id": b[1]}
    if ctype:
        f["type"] = ctype
    return {"op": "connect_cable", "target": {"kind": "cable", "id": None}, "fields": f}


def land_circuit(ctx: Ctx, circuit_id: int | str, site_id: int, new_circuit: dict[str, Any] | None) -> tuple[list[dict[str, Any]], tuple[str, Any]]:
    """Ops that give a circuit an end at a site (unless it has a free one there), and that end as a cable target."""
    e = ctx.e
    ops: list[dict[str, Any]] = []
    if new_circuit is not None:
        ops.append({"op": "create_circuit", "target": {"kind": "circuit", "id": None}, "client_ref": "circuit", "fields": new_circuit})
        sides: set[str] = set()
    else:
        terms = [t for t in e.cterms.values() if t["circuit_id"] == circuit_id]
        free = next((t for t in sorted(terms, key=lambda t: t["side"]) if t["site_id"] == site_id and t["cable_id"] is None), None)
        if free is not None:
            return ops, ("circuit_termination", free["id"])
        sides = {t["side"] for t in terms}
    side = "A" if "A" not in sides else "Z"
    if side in sides:
        raise Stop("parked", None, ["Both ends of that circuit are already recorded elsewhere; check it in NetBox (Circuits)."])
    ops.append({"op": "create_circuit_termination", "target": {"kind": "circuit_termination", "id": None}, "client_ref": "handoff",
                "fields": {"circuit_id": circuit_id, "term_side": side, "site_id": site_id}})
    return ops, ("circuit_termination", "@handoff")


def ask_new_circuit(ctx: Ctx, provider_id: int) -> dict[str, Any]:
    e = ctx.e
    prov = e.providers[provider_id]["name"]
    if not e.circuit_types and e.can_see("circuit_types"):
        # said before the player reads a label for nothing: a circuit cannot be recorded without a type
        raise Stop("parked", None, ["NetBox has no circuit type yet: add one under Circuits > Circuit Types (for example Internet), then come back.",
                                    f"While you are there, read the circuit ID on {prov}'s hand-off box or bill."],
                   note="A new circuit needs a circuit type, and NetBox has none yet.")
    taken = {c["cid"].lower() for c in e.circuits.values() if c["provider_id"] == provider_id}

    def check(t: str) -> tuple[Any, str | None]:
        if len(t) > 100:
            return None, "A circuit ID has at most 100 characters."
        return (t, None) if t.lower() not in taken else (None, f"{prov} already has a circuit {t} in NetBox.")
    cid = ctx.text("circuit_id", f"What is the circuit ID? {prov} prints it on the bill and on the hand-off box.", check,
                   "exactly as printed, for example EXF-20417",
                   [f"Find the hand-off box or modem of {prov} (often by the incoming duct).",
                    "Read the circuit ID on its label; the bill and the order mail carry it too."])
    types = [(f"type:{t['id']}", t["name"], t["name"]) for _, t in sorted(e.circuit_types.items())]
    tid = pick(ctx, "circuit_type", f"What kind of service is {cid}?", types, ["The order or the bill names the service."], noun="circuit type")
    return {"cid": cid, "provider_id": provider_id, "type_id": int(tid.split(":")[1])}


# ---------------------------------------------------------------- flows (one per finding kind)
def flow_link(ctx: Ctx) -> list[dict[str, Any]]:
    """A device (or a group of them) with no link: what does it connect to, on which ports, with what cable."""
    e, f = ctx.e, ctx.line["finding"]
    data, kind = f["data"], f["kind"]
    ids = [i for i in (data.get("device_ids") or [data["device_id"]]) if i in e.devices]
    site = e.sites[f["site_id"]]["name"]
    among: list[int] | None = data.get("main_device_ids")
    between = kind == "sites_not_linked"
    if between:
        among = [d["id"] for s in data["other_site_ids"] for d in e.site_devices(s) if e.is_network(d)]
    room = e.locations.get(data.get("location_id") or 0, {}).get("name", "that room")
    main_room = e.locations.get(data.get("main_location_id") or 0, {}).get("name", "the other room")
    no_link = {"locations_not_linked": "None: there is no link, on purpose", "sites_not_linked": "None: there is no link, on purpose",
               "site_island": "None: a separate network, on purpose", "device_unconnected": "Nothing: it stands alone, on purpose",
               "switch_no_uplink": "Nothing: a standalone switch, on purpose"}[kind]
    if len(ids) > 1:
        comp = e.components()
        group = sorted((e.devices[i] for i in ids), key=lambda d: (not is_router(d) if between else False, -comp.degree(d["id"]), natural(d["name"] or ""), d["id"]))
        prompt, look = {
            "locations_not_linked": (f"Which device in {room} has the link to {main_room}?", f"In {room}, look for the cable that leaves the room."),
            "sites_not_linked": (f"Which device at {site} has the link to your other sites?",
                                 f"At {site}, find where the line to your other sites comes in (a carrier's box, a fibre tray, a radio link)."),
            "site_island": (f"Which of {some([nm(d) for d in group])} has the link to the rest of {site}?",
                            f"Start at {nm(group[0])} ({e.where(group[0])}) and look for the cable that leaves that group of devices."),
        }[kind]
        picked = pick(ctx, "which", prompt, [dev_item(e, d) for d in group], [look, "Note which device and which port it starts from."], [na(no_link)])
        dev = e.devices[int(picked.split(":")[1])]
    else:
        dev = e.devices[ids[0]]
    ctx.at(device_id=dev["id"])
    name = nm(dev)
    question = {
        "device_unconnected": f"What is {name} ({e.where(dev)}) plugged into?",
        "switch_no_uplink": f"What does {name} uplink to?",
        "site_island": f"What does {name} connect to in the rest of {site}?",
        "locations_not_linked": f"What does {name} connect to in {main_room}?",
        "sites_not_linked": f"What does {name} connect to on the way to your other sites?",
    }[kind]
    items, wider = peer_items(e, dev, among, circuits=kind in ("device_unconnected", "switch_no_uplink", "sites_not_linked"), between_sites=between)
    if between:
        circs = [it for it in items if it[0].startswith("circuit:")]
        items = circs + [it for it in items if it not in circs]   # sites are mostly joined by a carrier's circuit
    peer = pick(ctx, "peer", question, items, look_checklist(e, dev), [NOT_IN_NETBOX] + ([na(no_link)] if len(ids) == 1 else []), wider=wider)
    if peer == "other":
        not_in_netbox(ctx, "other_name", dev, look_checklist(e, dev))
    local = ask_port(ctx, "local_port", dev, f"Which port on {name} carries that link?")
    assert local is not None
    if peer.startswith("circuit:"):
        ops, end = land_circuit(ctx, int(peer.split(":")[1]), dev["site_id"], None)
        return ops + [cable_op(local, end, ask_cable_type(ctx))]
    far = e.devices[int(peer.split(":")[1])]
    kinds = ("front_port",) if is_passive(far) else ("interface",)
    remote = ask_port(ctx, "peer_port", far, f"And which port on {nm(far)} does it land on?", kinds)
    assert remote is not None
    return [cable_op(local, remote, ask_cable_type(ctx))]


def flow_way_out(ctx: Ctx) -> list[dict[str, Any]]:
    """The line to the outside: which device it plugs into, whose line it is, which circuit, which port."""
    e, f = ctx.e, ctx.line["finding"]
    data, kind = f["data"], f["kind"]
    site_id = f["site_id"]
    circuit_id: int | None = data.get("circuit_id")
    provider_id: int | None = data.get("provider_id")
    unused = "Nowhere: we no longer use it"
    if kind in ("provider_unused", "circuit_unterminated"):
        what = f"{e.providers[provider_id]['name']}'s line" if kind == "provider_unused" else f"circuit {e.circuits[circuit_id]['cid']}"  # type: ignore[index]
        fixed = next((t["site_id"] for _, t in sorted(e.cterms.items()) if t["circuit_id"] == circuit_id and t["site_id"] is not None and t["cable_id"] is None), None)
        if fixed is None:
            taken = {t["site_id"] for t in e.cterms.values() if t["circuit_id"] == circuit_id}
            sites = [(f"site:{s}", e.sites[s]["name"], e.sites[s]["name"]) for s in sorted(e.sites, key=lambda s: (s != site_id, natural(e.sites[s]["name"])))
                     if s not in taken]
            picked = pick(ctx, "site", f"Which site does {what} come into?", sites,
                          ["Ask whoever pays the bill which address the service is delivered to."], [na(unused)], noun="site")
            site_id = int(picked.split(":")[1])
        else:
            site_id = fixed
    site = e.sites[site_id]
    ctx.at(site_id=site_id)
    devs = sorted((d for d in e.site_devices(site_id) if e.is_network(d) and e.free_ports(d["id"])),
                  key=lambda d: (not is_router(d), natural(d["name"] or ""), d["id"]))
    wider = [dev_item(e, d) for d in e.site_devices(site_id) if not is_passive(d) and e.free_ports(d["id"])]
    line_name = "the line to the outside" if kind == "site_no_way_out" else what
    checklist = [f"At {site['name']}, find where the carrier's line comes in (a hand-off box, modem or fibre tray).",
                 "Follow its cable to the first device of yours and note that device's name.", "Note the port it is plugged into."]
    closing = [na("None: this site has no line out, on purpose")] if kind == "site_no_way_out" else \
              [na(unused)] if kind == "circuit_unterminated" and "site" not in ctx.answers else []
    picked = pick(ctx, "device", f"Which device at {site['name']} does {line_name} plug into?", [dev_item(e, d) for d in devs], checklist,
                  [NOT_IN_NETBOX] + closing, wider=wider)
    if picked == "other":
        raise Stop("parked", None, ["Add that device to NetBox (Devices > Add, with its type and role).", "Come back: it will then be one of the choices."],
                   note="You said the line plugs into a device that is not in NetBox yet.")
    dev = e.devices[int(picked.split(":")[1])]
    ctx.at(device_id=dev["id"])
    new_circuit: dict[str, Any] | None = None
    add_carrier = ["Add the carrier to NetBox (Circuits > Providers).", "Come back: it will then be one of the choices."]
    if circuit_id is None:
        if provider_id is None:
            # always asked, also with one provider on record: the line may be another carrier's
            provs = [(f"provider:{i}", pr["name"], pr["name"]) for i, pr in sorted(e.providers.items())]
            p = pick(ctx, "provider", "Whose line is it?", provs, ["The hand-off box and the bill carry the carrier's name."],
                     [choice("other", "A carrier that is not in NetBox yet", "other")], noun="provider")
            if p == "other":
                raise Stop("parked", None, add_carrier, note="You said the line belongs to a carrier that is not in NetBox yet.")
            provider_id = int(p.split(":")[1])
        open_circuits = circuit_items(e, site_id, False, provider_id)
        prov_name = e.providers[provider_id]["name"]
        if open_circuits:
            c = pick(ctx, "circuit", f"Which circuit of {prov_name} is it?", open_circuits, ["Read the circuit ID on the hand-off box's label."],
                     [choice("new", "A circuit not in NetBox yet", "other")], noun="circuit")
            if c != "new":
                circuit_id = int(c.split(":")[1])
        if circuit_id is None:
            new_circuit = ask_new_circuit(ctx, provider_id)
    local = ask_port(ctx, "local_port", dev, f"Which port on {nm(dev)} is the line plugged into?")
    assert local is not None
    ops, end = land_circuit(ctx, "@circuit" if new_circuit else circuit_id, site_id, new_circuit)  # type: ignore[arg-type]
    return ops + [cable_op(local, end, ask_cable_type(ctx))]


def flow_address(ctx: Ctx) -> list[dict[str, Any]]:
    """How to reach a device: one of the addresses it already has, or one the player types, on an interface they pick."""
    e, f = ctx.e, ctx.line["finding"]
    ctx.waived = waived(ctx.line, ctx.answers)   # devices nobody logs in to: said once, not asked again
    every = [i for i in (f["data"].get("device_ids") or [f["data"]["device_id"]]) if i in e.devices]
    bundle = f["kind"] == "device_unreachable"
    ops: list[dict[str, Any]] = []
    asked = 0
    for did in every:
        if f"device:{did}:primary_ip" in ctx.waived:
            ctx.answered.append(f"ip_{did}")
            continue
        if asked >= MAX_BUNDLE:
            break
        asked += 1
        dev = e.devices[did]
        name = nm(dev)
        if ctx.target is None:
            ctx.at(device_id=did)
        have = e.device_ips(did)
        checklist = [f"Ask whoever looks after {name} which address they use to log in to it.",
                     "Or read it from the device: its management interface, or the label on the front."]
        # a device nobody logs in to can be said; a router's address is not something to wave away
        none = [na("It has none: nobody logs in to it")] if bundle else []
        # with one device left to ask about, "I don't know yet" parks the line at this question
        soft = bundle and len([i for i in every if f"device:{i}:primary_ip" not in ctx.waived]) > 1
        picked = pick(ctx, f"ip_{did}", f"How do you reach {name} to manage it?" if bundle else f"Which address does {name} route on?",
                      [(f"ip:{ip['id']}", ip["address"], ip["address"]) for ip in have], checklist,
                      [choice("typed", "An address not in NetBox yet", "other")] + none, noun="address", soft=soft, limit=MAX_PORTS)
        if picked == "unknown":
            continue
        if picked != "typed":
            ip = e.ips[int(picked.split(":")[1])]
            ops.append({"op": "assign_ip", "target": {"kind": "device", "id": did},
                        "fields": {"address": ip["address"], "interface_id": ip["interface_id"], "primary": True}})
            continue

        def check(t: str) -> tuple[Any, str | None]:
            try:
                i = ipaddress.ip_interface(t)
            except ValueError:
                return None, "Type an address with its mask, for example 192.0.2.10/24."
            if "/" not in t:
                return None, "Add the mask, for example /24: NetBox stores an address with its subnet size."
            return str(i), None
        addr = ctx.text(f"addr_{did}", f"What is the address of {name}, with its mask?", check, "for example 192.0.2.10/24", checklist)
        # where a management address lives, likeliest first: a management port, a VLAN or loopback interface (they take
        # no cable), then the physical ports; every interface of the device can be named
        ports = sorted(e.device_ports(did, ("interface",)), key=lambda p: (not p["mgmt_only"], is_physical(p), natural(p["name"]), p["id"]))
        port = pick(ctx, f"port_{did}", f"Which interface of {name} carries {addr}?", [(f"interface:{p['id']}", p["name"], p["name"]) for p in ports],
                    checklist + ["On a switch it is usually a VLAN interface (Vlan1, Vlan10) or the management port, not a front-panel port."],
                    noun="interface", soft=soft, limit=MAX_PORTS)
        if port == "unknown":
            continue
        ops.append({"op": "assign_ip", "target": {"kind": "device", "id": did},
                    "fields": {"address": addr, "interface_id": int(port.split(":")[1]), "primary": True}})
    if not ops:
        if ctx.waived and not ctx.skipped:
            raise Stop("dismissed", None, note="None of these devices is managed over the network.")
        raise Stop("parked", None, ctx.skipped)
    return ops


def flow_power(ctx: Ctx) -> list[dict[str, Any]]:
    """What powers each device of a rack: which power strip, which outlet."""
    e, f = ctx.e, ctx.line["finding"]
    ctx.waived = waived(ctx.line, ctx.answers)
    every = [i for i in f["data"]["device_ids"] if i in e.devices]
    ops: list[dict[str, Any]] = []
    used: set[tuple[str, int]] = set()
    asked = 0
    for did in every:
        if f"device:{did}:power" in ctx.waived:
            ctx.answered.append(f"pdu_{did}")
            continue
        if asked >= MAX_BUNDLE:
            break
        asked += 1
        dev = e.devices[did]
        name = nm(dev)
        if ctx.target is None:
            ctx.at(device_id=did)
        feeders = [d for d in e.site_devices(dev["site_id"]) if d["id"] != did
                   and any(p["cable_id"] is None and (p["kind"], p["id"]) not in used for p in e.device_ports(d["id"], ("power_outlet",)))]
        feeders.sort(key=lambda d: (d["rack_id"] != dev["rack_id"], d["location_id"] != dev["location_id"], natural(d["name"] or "")))
        checklist = [f"Find {name}: {e.where(dev)}.", "Follow its power lead to the power strip (PDU) and read the strip's name.",
                     "Read the number printed next to the outlet."]
        soft = len([i for i in every if f"device:{i}:power" not in ctx.waived]) > 1
        picked = pick(ctx, f"pdu_{did}", f"Which power strip is {name} plugged into?",
                      [(f"device:{d['id']}", f"{nm(d)} ({e.where(d)})", nm(d)) for d in feeders], checklist,
                      [na("None in NetBox: it is fed another way")], noun="power strip", soft=soft)
        if picked == "unknown":
            continue
        pdu = e.devices[int(picked.split(":")[1])]
        free = [p for p in e.device_ports(pdu["id"], ("power_outlet",)) if p["cable_id"] is None and (p["kind"], p["id"]) not in used]
        out = pick(ctx, f"outlet_{did}", f"Which outlet on {nm(pdu)} feeds {name}?", [(f"power_outlet:{p['id']}", p["name"], p["name"]) for p in free],
                   checklist, noun="free outlet", soft=soft)
        if out == "unknown":
            continue
        outlet = ("power_outlet", int(out.split(":")[1]))
        used.add(outlet)
        inlets = [p for p in e.device_ports(did, ("power_port",)) if p["cable_id"] is None]
        ops.append(cable_op(("power_port", inlets[0]["id"]), outlet, "power"))
    if not ops:
        if ctx.waived and not ctx.skipped:
            raise Stop("dismissed", None, note="None of these devices has a power feed to record.")
        raise Stop("parked", None, ctx.skipped)
    return ops


def flow_prefix(ctx: Ctx) -> list[dict[str, Any]]:
    """The subnet a group of addresses sits in. A host address (/32) names no subnet, so its range is asked for, never
    guessed; for an address in a VRF the prefix has to go into that VRF, which the game does not write."""
    e, f = ctx.e, ctx.line["finding"]
    data = f["data"]
    network = data.get("network")
    addrs = [e.ips[i]["address"] for i in data["ip_ids"] if i in e.ips]
    names = some(addrs)
    one = len(addrs) == 1
    ask = ["Ask whoever runs the addressing plan, or read the mask on one of the devices."]
    apart = na("None: that range is not ours to record")
    if data.get("vrf_id"):
        vrf = data.get("vrf") or f"VRF {data['vrf_id']}"
        hint = f" (their mask says {network})" if network else ""
        picked = ctx.choice("vrf", f"{names} {'sits' if one else 'sit'} in VRF {vrf}, which has no prefix that holds {'it' if one else 'them'}{hint}. "
                            "The game writes prefixes to the global table only. Will you add it in NetBox?",
                            [choice("later", f"Yes: I will add it to VRF {vrf} myself", "confirm"), apart], ask)
        assert picked == "later"
        raise Stop("parked", None, [f"In NetBox, add the prefix under IPAM > Prefixes > Add and set its VRF to {vrf}"
                                    + (f" (the mask says {network})." if network else "."),
                                    "Come back and choose \"Check NetBox\": the line completes when NetBox shows it."],
                   note=f"The prefix belongs in VRF {vrf}; the game does not write there.")

    def check(t: str) -> tuple[Any, str | None]:
        try:
            net = ipaddress.ip_network(t, strict=True)
        except ValueError:
            return None, "Type a network with its mask, for example 192.0.2.0/24."
        if not all(ipaddress.ip_interface(a).ip in net for a in addrs):
            return None, f"{net} does not hold {names}."
        if net.prefixlen == net.max_prefixlen:
            return None, "That is a single address, not a range: type the range these addresses are handed out from."
        return str(net), None
    if network is None:
        # loopbacks and other host addresses: the range they are numbered from is something only the admin knows
        net = ctx.text("subnet_typed", f"{names} {'is a host address' if one else 'are host addresses'} (a /32 or /128 names no subnet). "
                       f"Which range {'is it' if one else 'are they'} numbered from?", check, "for example 10.255.0.0/24", ask)
        return [{"op": "create_prefix", "target": {"kind": "prefix", "id": None}, "fields": {"prefix": net}}]
    picked = ctx.choice("subnet", f"{names} {'sits' if one else 'sit'} in no prefix. {'Its' if one else 'Their'} mask says the subnet is {network}. Is that right?",
                        [choice("yes", f"Yes, it is {network}", "confirm"), choice("typed", "No, the subnet is another size", "other"), apart], ask)
    net = network if picked == "yes" else ctx.text("subnet_typed", "What is the subnet, with its mask?", check, f"for example {network}", ask)
    return [{"op": "create_prefix", "target": {"kind": "prefix", "id": None}, "fields": {"prefix": net}}]


def name_pattern(e: Estate, site_id: int) -> str | None:
    """The asset-tag pattern in use at a site: the common prefix of its tags and the next number, or None."""
    tags = [d["asset_tag"] for d in e.devices.values() if d["site_id"] == site_id and d["asset_tag"]] or \
           [d["asset_tag"] for d in e.devices.values() if d["asset_tag"]]
    parsed = [m for m in (re.fullmatch(r"(.*?)(\d+)", t) for t in tags) if m]
    if not parsed:
        return None
    prefix = max({m.group(1) for m in parsed}, key=lambda p: sum(1 for m in parsed if m.group(1) == p))
    nums = [m.group(2) for m in parsed if m.group(1) == prefix]
    return f"{prefix}{max(int(n) for n in nums) + 1:0{len(nums[0])}d}"


def flow_audit(ctx: Ctx) -> list[dict[str, Any]]:
    """Walk one rack top to bottom (or the kit outside the racks in one room): the labels that tell each device apart."""
    e, f = ctx.e, ctx.line["finding"]
    data = f["data"]
    ids = [i for i in data["device_ids"] if i in e.devices][:MAX_BUNDLE]
    open_ = set(data["gap_ids"])
    if data.get("rack_id"):
        ctx.at(rack_id=data["rack_id"])
        prompt = f"Walk rack {e.racks[data['rack_id']]['name']} top to bottom and read each label. Leave blank what you cannot see."
    else:
        ctx.at(device_id=ids[0])
        room = e.locations.get(data.get("location_id") or 0, {}).get("name") or e.sites[f["site_id"]]["name"]
        prompt = f"Find each of these devices in {room} (they are in no rack) and read its label. Leave blank what you cannot see."
    fields: list[dict[str, Any]] = []
    suggestion = name_pattern(e, f["site_id"]) if "label_maker" in ctx.tools else None
    for did in ids:
        d = e.devices[did]
        where = f"U{d['position']:g}" if d["position"] is not None else "not in a unit"
        head = f"{nm(d)} ({where})"
        if f"device:{did}:serial" in open_:
            fields.append({"id": f"device:{did}:serial", "label": f"{head}: serial number", "type": "text", "max_length": 50,
                           "hint": "on the pull-out tag or the rear label", "suggestion": None, "options": []})
        if f"device:{did}:asset_tag" in open_:
            fields.append({"id": f"device:{did}:asset_tag", "label": f"{head}: asset tag", "type": "text", "max_length": 50,
                           "hint": "your own sticker" + (f"; label maker: the next free tag here is {suggestion}" if suggestion else ""),
                           "suggestion": suggestion, "options": []})
            suggestion = None  # one suggestion per form: the player confirms each tag they stick on
    checklist = [f"Go to {ctx.target['label'] if ctx.target else 'the rack'}.", "Start at the top unit and work down, one device at a time.",
                 "Serial numbers are on a pull-out tag at the front or a label at the rear; photograph them if the light is poor.",
                 "An asset tag is your own sticker: if a device has none, that is worth knowing too."]
    values = ctx.form("labels", prompt, fields, checklist, na="No such label on them: leave it as it is")
    return _per_device(values)


def _per_device(values: dict[str, Any]) -> list[dict[str, Any]]:
    """Form values keyed `device:<id>:<field>` as one update per device."""
    per_dev: dict[int, dict[str, Any]] = {}
    for fid, v in values.items():
        _, did, fld = fid.split(":")
        per_dev.setdefault(int(did), {})["platform_id" if fld == "platform" else fld] = int(v) if fld == "platform" else v
    return [{"op": "update_fields", "target": {"kind": "device", "id": did}, "fields": fs} for did, fs in sorted(per_dev.items())]


def flow_platform(ctx: Ctx) -> list[dict[str, Any]]:
    """What the devices of one model run: one answer for all of them, or one each where they differ. Only platforms
    NetBox allows for the model's manufacturer are offered (a platform may be tied to one manufacturer)."""
    e, f = ctx.e, ctx.line["finding"]
    ids, model = [i for i in f["data"]["device_ids"] if i in e.devices], f["data"]["model"]
    ctx.at(device_id=ids[0])
    names = some([e.device_name(i) for i in ids])
    maker = e.devices[ids[0]].get("manufacturer")
    # platforms that devices of this model already run come first, then the most used
    used: dict[str, int] = {}
    for d in e.devices.values():
        if d["platform"]:
            used[d["platform"]] = used.get(d["platform"], 0) + (1000 if d["model"] == model else 1)
    plats = sorted((p for p in e.platforms.values() if p.get("manufacturer") in (None, maker)),
                   key=lambda p: (-used.get(p["name"], 0), natural(p["name"]), p["id"]))
    checklist = [f"Log in to {e.device_name(ids[0])}, or read its login banner.", "Note the operating system's name (\"show version\" says)."]
    question = (f"What does {names} run?" if len(ids) == 1 else f"{len(ids)} {model} devices have no platform on record: {names}. What do they run?")
    picked = pick(ctx, "platform", question, [(f"platform:{p['id']}", p["name"], p["name"]) for p in plats], checklist,
                  ([choice("mixed", "They do not all run the same", "other")] if len(ids) > 1 and plats else [])
                  + [choice("other", "A platform that is not in NetBox yet", "other"), na("Nothing we track: no system to record")],
                  noun="platform", limit=2 * MAX_CANDIDATES)
    if picked == "other":
        tied = f" (for {maker}, or for any manufacturer)" if maker and len(plats) < len(e.platforms) else ""
        raise Stop("parked", None, [f"Add the platform to NetBox{tied} (Devices > Platforms > Add).", "Come back: it will then be one of the choices."],
                   note=f"You said {names} run{'s' if len(ids) == 1 else ''} something that is not in NetBox yet.")
    if picked == "mixed":
        options = [{"id": str(p["id"]), "label": p["name"]} for p in plats[:40]]
        fields = [{"id": f"device:{i}:platform", "label": f"{e.device_name(i)}: platform (operating system)", "type": "select",
                   "hint": "what it runs", "suggestion": None, "options": options} for i in ids[:MAX_BUNDLE]]
        return _per_device(ctx.form("each", "What does each of them run? Leave blank what you do not know.", fields, checklist))
    pid = int(picked.split(":")[1])
    return [{"op": "update_fields", "target": {"kind": "device", "id": i}, "fields": {"platform_id": pid}} for i in ids]


def flow_site(ctx: Ctx) -> list[dict[str, Any]]:
    e, f = ctx.e, ctx.line["finding"]
    site = e.sites[f["site_id"]]
    ctx.at(site_id=site["id"])
    fields: list[dict[str, Any]] = []
    base = {"suggestion": None, "options": []}
    if "gps" in f["data"]["fields"]:
        fields += [{"id": "latitude", "label": "Latitude (decimal degrees)", "type": "number", "min": -90, "max": 90, "hint": "for example 52.37", **base},
                   {"id": "longitude", "label": "Longitude (decimal degrees)", "type": "number", "min": -180, "max": 180, "hint": "for example 4.89", **base}]
    if "facility" in f["data"]["fields"]:
        fields.append({"id": "facility", "label": "Facility (what the carrier or landlord calls the building)", "type": "text", "max_length": 50,
                       "hint": "for example the data centre's code", **base})
    if "country" in f["data"]["fields"]:
        fields.append({"id": "country", "label": "Country (two-letter code)", "type": "text", "max_length": 2, "pattern": "[A-Za-z]{2}",
                       "pattern_hint": "two letters, for example NL or GB", "hint": "for example NL", **base})
    prompt = f"Where is {site['name']}? Fill in what you know; leave the rest blank."
    values = ctx.form("record", prompt, fields,
                      ["Look the address up on a map and note the latitude and longitude of the building.",
                       "The carrier's order or the landlord's contract names the facility."], na="There is no more to record: leave it as it is")
    out = {k: (round(float(v), 6) if k in ("latitude", "longitude") else (v.upper() if k == "country" else v)) for k, v in values.items()}
    if ("latitude" in out) != ("longitude" in out) and "gps" in f["data"]["fields"]:
        ctx.error = "Latitude and longitude go together: fill in both or neither."
        raise Stop("asking", ctx._step("record", prompt, "form", [UNKNOWN], fields))
    return [{"op": "update_fields", "target": {"kind": "site", "id": site["id"]}, "fields": out}]


def flow_units(ctx: Ctx) -> list[dict[str, Any]]:
    e, f = ctx.e, ctx.line["finding"]
    rack = e.racks[f["data"]["rack_id"]]
    ctx.at(rack_id=rack["id"])
    fields = []
    for did in [i for i in f["data"]["device_ids"] if i in e.devices][:MAX_BUNDLE]:
        d = e.devices[did]
        fields.append({"id": f"u:{did}", "label": f"{nm(d)}: lowest rack unit it occupies", "type": "number", "integer": True,
                       "min": 1, "max": rack["u_height"], "hint": f"1 to {rack['u_height']}, counted as printed on the rack rail", "suggestion": None, "options": []})
    values = ctx.form("units", f"Walk rack {rack['name']} top to bottom: which unit is each of these devices in?", fields,
                      [f"Go to rack {rack['name']}.", "Read the unit number on the rail next to the bottom edge of each device."],
                      na="In no unit (on a shelf, at the side): leave it")
    return [{"op": "place_device", "target": {"kind": "device", "id": int(fid.split(":")[1])},
             "fields": {"rack_id": rack["id"], "position": v, "face": "front"}} for fid, v in sorted(values.items())]


def flow_layout(ctx: Ctx) -> list[dict[str, Any]]:
    e, f = ctx.e, ctx.line["finding"]
    ops: list[dict[str, Any]] = []
    fields = []
    open_ = set(f["data"]["gap_ids"])
    rooms = [{"id": str(loc["id"]), "label": loc["name"]} for _, loc in sorted(e.locations.items()) if loc["site_id"] == f["site_id"]]
    for rid in [r for r in f["data"]["rack_ids"] if r in e.racks][:MAX_BUNDLE]:
        r = e.racks[rid]
        if ctx.target is None:
            ctx.at(rack_id=rid)
        if f"rack:{rid}:location" in open_ and rooms:
            fields.append({"id": f"room:{rid}", "label": f"Rack {r['name']}: which room is it in?", "type": "select", "options": rooms, "hint": None, "suggestion": None})
        if f"rack:{rid}:iso" in open_:
            for axis, word in (("x", "column, counted from the room's left wall"), ("y", "row, counted from the room's far wall")):
                fields.append({"id": f"{axis}:{rid}", "label": f"Rack {r['name']}: floor tile {word}", "type": "number", "integer": True,
                               "min": 0, "max": 500, "hint": "one tile is one rack width (about 60 cm)", "suggestion": None, "options": []})
    prompt = "Where do these racks stand? Count floor tiles from the corner of the room, or drag each rack in the room view."
    values = ctx.form("floor", prompt, fields, ["Stand in the doorway and sketch the room: one square per floor tile.",
                                                "Mark each rack's square and which way its front faces."], na="They are not on a floor plan: leave it")
    per: dict[int, dict[str, Any]] = {}
    for fid, v in values.items():
        key, rid = fid.split(":")
        per.setdefault(int(rid), {})[{"room": "location_id", "x": "iso_x", "y": "iso_y"}[key]] = int(v)
    for rid, fs in sorted(per.items()):
        if ("iso_x" in fs) != ("iso_y" in fs):
            ctx.error = "A rack needs both its column and its row."
            raise Stop("asking", ctx._step("floor", prompt, "form", [UNKNOWN], fields))
        ops.append({"op": "move_rack", "target": {"kind": "rack", "id": rid}, "fields": fs})
    return ops


def flow_port(ctx: Ctx) -> list[dict[str, Any]]:
    """The ports of one device that NetBox says are in use, with no cable: what is on the other end of each. Access
    ports to desks and wall outlets are answered in one go ("nothing NetBox should hold"), port by port otherwise."""
    e, f = ctx.e, ctx.line["finding"]
    data = f["data"]
    dev = e.devices[data["device_id"]]
    name = nm(dev)
    ctx.at(device_id=dev["id"])
    ctx.waived = waived(ctx.line, ctx.answers)
    every = [e.ports[("interface", i)] for i in data["interface_ids"] if ("interface", i) in e.ports]
    many = len(every) > 1

    def said_of(p: dict[str, Any], also_address: bool = False) -> str:
        if p["description"].strip():
            return f" (described as {p['description'].strip()[:40]!r})"
        return " (it has an address)" if also_address else ""
    if many:
        listing = "; ".join(f"{p['name']}{said_of(p, True)}" for p in every[:4]) + (f"; and {len(every) - 4} more" if len(every) > 4 else "")
        ctx.choice("what", clip(f"{len(every)} ports of {name} are in use by NetBox's record, with no cable: {listing}. What are they?", 400),
                   [choice("each", "Leads I can follow: go through them"),
                    na("Desk and wall-outlet leads: not kept in NetBox")],
                   [f"Find {name}: {e.where(dev)}.", "Look where the leads in those ports go: into a patch panel, to another device, or into the wall."])
    items, wider = peer_items(e, dev, linked_too=True)
    many = len([p for p in every if f"interface:{p['id']}:cable" not in ctx.waived]) > 1
    ops: list[dict[str, Any]] = []
    used: set[tuple[str, int]] = set()
    asked = 0
    for port in every:
        pid = port["id"]
        if f"interface:{pid}:cable" in ctx.waived:
            ctx.answered.append(f"peer_{pid}")
            continue
        if asked >= MAX_BUNDLE:
            break
        asked += 1
        checklist = [f"Find {name}: {e.where(dev)}.", f"Follow the lead in port {port['name']} to its far end."] + look_checklist(e, dev)[2:]
        peer = pick(ctx, f"peer_{pid}", f"What is on the other end of {name} {port['name']}{said_of(port)}?", items, checklist,
                    [NOT_IN_NETBOX, na("Nothing: the port is not in use")], soft=many, wider=wider)
        if peer == "unknown":
            continue
        if peer == "other":
            if not many:
                raise Stop("parked", None, ["Add the far-end device to NetBox (Devices > Add).", "Come back: it will then be one of the choices."])
            ctx.skipped.append(f"Add the device at the far end of {name} {port['name']} to NetBox (Devices > Add).")
            continue
        if peer.startswith("circuit:"):
            more, end = land_circuit(ctx, int(peer.split(":")[1]), dev["site_id"], None)
            ops += more + [cable_op(("interface", pid), end, ask_cable_type(ctx, f"cable_type_{pid}"))]
            continue
        far = e.devices[int(peer.split(":")[1])]
        remote = ask_port(ctx, f"peer_port_{pid}", far, f"Which port on {nm(far)} does it land on?", ("front_port",) if is_passive(far) else ("interface",),
                          soft=many, used=used)
        if remote is None:
            continue
        used.add(remote)
        ops.append(cable_op(("interface", pid), remote, ask_cable_type(ctx, f"cable_type_{pid}")))
    if not ops:
        if ctx.waived and not ctx.skipped:
            raise Stop("dismissed", None, note="None of these ports has a far end to record.")
        raise Stop("parked", None, ctx.skipped)
    return ops


FLOWS = {
    "device_unconnected": flow_link, "switch_no_uplink": flow_link, "site_island": flow_link, "locations_not_linked": flow_link,
    "sites_not_linked": flow_link, "site_no_way_out": flow_way_out, "circuit_unterminated": flow_way_out, "provider_unused": flow_way_out,
    "router_no_address": flow_address, "device_unreachable": flow_address, "device_no_power": flow_power, "address_no_prefix": flow_prefix,
    "rack_audit": flow_audit, "shelf_audit": flow_audit, "platform_survey": flow_platform, "site_record": flow_site, "rack_devices_unpositioned": flow_units,
    "room_layout": flow_layout, "interface_no_cable": flow_port,
}

# Per finding kind: the quest's title, why it matters (said after the finding's own sentence, which names the objects),
# and what the admin can do once it is true.
WORDS: dict[str, tuple[str, str, str]] = {
    "site_no_way_out": ("The way out of {site}", "Let's find out.", "You can now see in NetBox where the line to the outside comes into {site}, and trace it."),
    "sites_not_linked": ("{site} and the other sites", "Let's find the link.", "You can now trace from {site} to another site."),
    "locations_not_linked": ("The link between {room} and {main_room}", "If that link fails, nobody knows which cable to check. Let's record it.",
                             "You can now trace from {room} to {main_room}."),
    "site_island": ("Cut off from the rest", "Let's find the link to the rest.", "You can now trace from {names} to the rest of {site}."),
    "switch_no_uplink": ("An uplink for {subject}", "When it goes quiet, nobody can tell what sits above it. Let's record its uplink.",
                         "You can now trace {subject} towards the core."),
    "circuit_unterminated": ("Where {subject} lands", "A circuit you pay for should end somewhere. Let's record where it lands.",
                             "You can now trace circuit {subject} to the device it plugs into."),
    "provider_unused": ("{subject}'s line", "Let's find their line.", "You can now look up which circuit {subject} delivers, and where."),
    "device_unconnected": ("What {subject} plugs into", "If it drops off the network, nobody can tell which port to check. Let's record its uplink.",
                           "You can now trace {subject} to what it plugs into."),
    "router_no_address": ("An address for {subject}", "Let's record one.", "You can now look up how to reach {subject}."),
    "device_unreachable": ("How to reach the kit at {site}", "When something breaks, the first question is how to log in. Let's record that.",
                           "You can now look up the management address of {names}."),
    "device_no_power": ("What powers {where}", "If a power strip trips, nobody can tell what goes dark with it. Let's record the power feeds.",
                        "You can now see what loses power when a strip in {where} trips."),
    "address_no_prefix": ("A home for {subject}", "Addresses with no prefix cannot be planned around. Let's record their subnet.",
                          "You can now see how full that subnet is and pick a free address in it."),
    "interface_no_cable": ("The far ends at {subject}", "A port in use with no far end on record cannot be traced. Let's find where the leads go.",
                           "You can now trace the ports of {subject} you recorded to their far ends."),
    "rack_audit": ("Audit rack {subject}", "A device with no serial or tag cannot be matched to a support contract or found after a move. Let's walk the rack.",
                   "You can now match every device you read in rack {subject} to its paperwork."),
    "shelf_audit": ("Audit the kit outside the racks", "A device with no serial or tag cannot be matched to a support contract or found after a move. Let's read the labels.",
                    "You can now match {names} to their paperwork."),
    "platform_survey": ("What {runs}", "{one_answer}Let's record it.",
                        "You can now list every device that runs it when an advisory or an upgrade comes out."),
    "site_record": ("The record of {site}", "Let's fill that in.", "You can now look up where {site} is and what its building is called."),
    "rack_devices_unpositioned": ("Rack units in {subject}", "Nobody can be sent to the right unit. Let's note where each one sits.",
                                  "You can now send someone to the exact unit in rack {subject}."),
    "room_layout": ("The floor plan", "Someone new cannot find a rack that is not on the plan. Let's put that right.", "You can now point to {names} on the floor plan."),
}


# A line about a single port names the port.
ONE_PORT_WORDS = ("The other end of {subject}", "A port in use with no far end on record cannot be traced. Let's find its far end.",
                  "You can now trace {subject} to its far end.")


def words_of(e: Estate, f: dict[str, Any]) -> tuple[str, str, str]:
    """A line's title, purpose and outcome. The purpose opens with the finding's own sentence, so it always says what
    is missing and where, with the names NetBox has; then why that matters."""
    d = f["data"]
    refs = [r["name"] for r in f["evidence"] if r["kind"] in ("device", "rack", "ip_address")]
    rack = e.racks.get(d.get("rack_id") or 0)
    site = e.sites[f["site_id"]]["name"] if f["site_id"] in e.sites else "the estate"
    ctx = {"site": site, "subject": subject_of(e, f), "names": some(refs) or "them",
           "room": e.locations.get(d.get("location_id") or 0, {}).get("name", "one room"),
           "main_room": e.locations.get(d.get("main_location_id") or 0, {}).get("name", "the other"),
           "where": f"rack {rack['name']}" if rack else f"the kit outside any rack at {site}"}
    if f["kind"] == "platform_survey":
        many = len(d["device_ids"]) > 1
        ctx.update(runs=f"the {d['model']} devices run" if many else f"{refs[0]} runs", one_answer="One answer covers them all. " if many else "")
    words = WORDS[f["kind"]]
    if f["kind"] == "interface_no_cable" and len(d["interface_ids"]) == 1 and ("interface", d["interface_ids"][0]) in e.ports:
        words, ctx["subject"] = ONE_PORT_WORDS, f"{ctx['subject']} {e.ports[('interface', d['interface_ids'][0])]['name']}"
    title, why, outcome = (t.format(**ctx) for t in words)
    return title, f"{f['title']} {why}", outcome


def subject_of(e: Estate, f: dict[str, Any]) -> str:
    d = f["data"]
    if "model" in d:
        return d["model"]
    if "rack_id" in d and d["rack_id"] in e.racks:
        return e.racks[d["rack_id"]]["name"]
    if "device_id" in d:
        return e.device_name(d["device_id"])
    if "circuit_id" in d and d["circuit_id"] in e.circuits:
        return e.circuits[d["circuit_id"]]["cid"]
    if "provider_id" in d and d["provider_id"] in e.providers:
        return e.providers[d["provider_id"]]["name"]
    return f["evidence"][0]["name"] if f["evidence"] else ""


def lines(e: Estate, a: Analysis) -> list[dict[str, Any]]:
    """One quest line per finding, plus one per device with an expected-cable gap; most valuable first. Made once per
    estate, like its analysis."""
    key = "lines" if e._memo.get("analysis") is a else None
    if key and key in e._memo:
        return e._memo[key]
    out = _lines(e, a)
    if key:
        e._memo[key] = out
    return out


def _lines(e: Estate, a: Analysis) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    findings = list(a.findings)
    from .findings import _sort_key

    for f in sorted(findings, key=_sort_key):
        title, purpose, outcome = words_of(e, f)
        out.append({"id": f"q:{f['id']}", "kind": f["kind"], "rank": f["rank"], "site_id": f["site_id"], "title": clip(title, 80),
                    "purpose": purpose, "outcome": outcome, "finding": f, "xp": KIND_XP.get(f["kind"], XP[f["rank"]]), "minutes": MINUTES[f["rank"]]})
    return out


CABLE_WORDS = {**dict(CABLE_TYPES), "power": "Power lead"}


def describe(e: Estate, ops: list[dict[str, Any]]) -> list[str]:
    """A proposal in plain words, one line per op, with the names an admin knows (the preview shows the fields too)."""
    refs: dict[str, str] = {}

    def end(kind: str, oid: Any) -> str:
        if isinstance(oid, str):
            return refs.get(oid, "the new circuit end")
        if kind == "circuit_termination":
            term = e.cterms.get(oid)
            return f"circuit {e.circuits[term['circuit_id']]['cid']} (side {term['side']})" if term else f"circuit end {oid}"
        p = e.ports.get((kind, oid))
        return f"{e.device_name(p['device_id'])} {p['name']}" if p else f"{kind.replace('_', ' ')} {oid}"

    out: list[str] = []
    for op in ops:
        f, name, target = op["fields"], op["op"], op["target"]
        if name == "connect_cable":
            kind = CABLE_WORDS.get(f.get("type") or "", "cable type left empty")
            out.append(f"New cable: {end(f['a_kind'], f['a_id'])} to {end(f['b_kind'], f['b_id'])} ({kind[0].lower() + kind[1:]}).")
        elif name == "create_circuit":
            prov = e.providers.get(f["provider_id"], {}).get("name", "the provider")
            ctype = e.circuit_types.get(f["type_id"], {}).get("name", "circuit")
            refs["@" + op.get("client_ref", "")] = f"circuit {f['cid']}"
            out.append(f"New circuit {f['cid']} from {prov} ({ctype}).")
        elif name == "create_circuit_termination":
            circ = refs.get(f["circuit_id"]) if isinstance(f["circuit_id"], str) else f"circuit {e.circuits[f['circuit_id']]['cid']}"
            refs["@" + op.get("client_ref", "")] = f"{circ} (side {f['term_side']})"
            out.append(f"{circ[0].upper() + circ[1:]} gets its {f['term_side']} end at {e.sites[f['site_id']]['name']}.")
        elif name == "assign_ip":
            port = e.ports.get(("interface", f["interface_id"]))
            out.append(f"{e.device_name(target['id'])}: {f['address']} on {port['name'] if port else 'its interface'}"
                       + (", as its management address (primary IP)." if f.get("primary", True) else "."))
        elif name == "create_prefix":
            out.append(f"New prefix {f['prefix']}.")
        elif name == "place_device":
            out.append(f"{e.device_name(target['id'])}: unit {f['position']:g} of rack {e.racks[f['rack_id']]['name']} ({f['face']}).")
        elif name == "move_rack":
            rack = e.racks[target["id"]]["name"]
            bits = ([f"in {e.locations[f['location_id']]['name']}"] if f.get("location_id") in e.locations else []) + \
                   ([f"floor tile column {f['iso_x']}, row {f['iso_y']}"] if "iso_x" in f else [])
            out.append(f"Rack {rack}: {' and '.join(bits)}.")
        elif name == "update_fields":
            who = e.device_name(target["id"]) if target["kind"] == "device" else e.sites.get(target["id"], {}).get("name", target["kind"])
            words = {"serial": "serial", "asset_tag": "asset tag", "facility": "facility", "country": "country", "latitude": "latitude",
                     "longitude": "longitude"}
            bits = [f"{words.get(k, k)} {v}" for k, v in f.items() if k != "platform_id"]
            if f.get("platform_id") in e.platforms:
                bits.append(f"platform {e.platforms[f['platform_id']]['name']}")
            out.append(f"{who}: {', '.join(bits)}.")
    return out


def spot_check(e: Estate, site_id: int, done: int) -> dict[str, Any] | None:
    """A site with nothing open is not given invented work: one device at a time, it is asked whether the record
    still matches the rack. `done` (how many spot checks the player has finished here) rotates through the devices."""
    devs = [d for d in e.site_devices(site_id) if d["rack_id"] is not None and d["position"] is not None and d["status"] == "active"]
    if not devs:
        return None
    d = devs[done % len(devs)]
    site = e.sites[site_id]["name"]
    finding = {"id": f"spot_check:device:{d['id']}:{done}", "kind": "spot_check", "rank": "identity", "site_id": site_id,
               "title": f"Is {nm(d)} still as recorded?", "evidence": [{"kind": "device", "id": d["id"], "name": nm(d)}],
               "data": {"device_id": d["id"]}}
    return {"id": f"q:{finding['id']}", "kind": "spot_check", "rank": "identity", "site_id": site_id, "title": f"Spot check: {nm(d)}",
            "purpose": f"Everything I can see at {site} is in order. Shall we spot-check one device, to keep it that way?",
            "outcome": f"You can now trust the record of {nm(d)}: it was checked against the rack.", "finding": finding, "xp": 0, "minutes": 2}


def flow_spot(ctx: Ctx) -> list[dict[str, Any]]:
    e = ctx.e
    d = e.devices[ctx.line["finding"]["data"]["device_id"]]
    ctx.at(device_id=d["id"])
    serial = f", serial {d['serial']}" if d["serial"] else ""
    picked = ctx.choice("same", f"Go and look: is {nm(d)} still in {e.where(d)}{serial}?",
                        [choice("yes", "Yes, exactly as recorded", "confirm"), choice("no", "No, something has changed", "other")],
                        [f"Find {nm(d)}: {e.where(d)}.", "Compare its label with the record: name, unit, serial."])
    if picked == "no":
        raise Stop("parked", None, [f"Open {nm(d)} in the rack view and correct what differs (every change is previewed first).",
                                    "Or correct it in NetBox itself. Then tell me it matches."],
                   note=f"You found that {nm(d)} no longer matches its record.")
    raise Stop("confirmed")


def open_checks(a: Analysis, line: dict[str, Any]) -> list[str]:
    """The checks of a line that a (fresh) analysis still finds open."""
    return [c for c in check_ids(line) if a.check(c, line["site_id"]) is False]


def check_ids(line: dict[str, Any]) -> list[str]:
    """Every check a line stands for when it is first offered (the base its progress is measured against)."""
    d = line["finding"]["data"]
    if d.get("gap_ids") is not None:
        return list(d["gap_ids"])
    if line["kind"] == "device_unreachable":
        return [f"device:{i}:primary_ip" for i in d["device_ids"]]
    return [line["finding"]["id"]]


def evaluate(e: Estate, line: dict[str, Any], answers: dict[str, Any], tools: frozenset[str] = frozenset()) -> dict[str, Any]:
    ctx = Ctx(e, line, answers, tools)
    base = {"step": None, "proposal": None, "checklist": [], "note": None, "changes": []}
    try:
        ops = (flow_spot if line["kind"] == "spot_check" else FLOWS[line["kind"]])(ctx)
        state = {**base, "status": "ready", "checklist": ctx.skipped, "changes": describe(e, ops),
                 "proposal": {"ops": stamp(e, ops), "reason": clip(f"quest: {line['title']}", 200)}}
    except Stop as s:
        state = {**base, "status": s.status, "step": s.step, "checklist": s.checklist, "note": s.note}
    except KeyError:
        # the estate changed under the line (an object it named is gone): start the line again from its first question
        return evaluate(e, line, {}, tools) if answers else {**base, "status": "parked", "said": [], "target": None, "answered": [], "waived": [],
                                                           "checklist": ["This changed in NetBox while you were away; look again later."]}
    if ctx.target is None and line["site_id"] in e.sites:
        ctx.at(site_id=line["site_id"])
    return {**state, "said": ctx.said, "target": ctx.target, "answered": ctx.answered, "waived": ctx.waived}


def stamp(e: Estate, ops: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Every op that changes an existing site, rack or device carries the time NetBox last changed that object when
    the question was asked (`if_unmodified_since`): a line about an empty field never writes over a value someone
    recorded in NetBox meanwhile. NetBox refuses the change instead, and the line asks again with what is there now."""
    tables = {"site": e.sites, "rack": e.racks, "device": e.devices}
    out = []
    for op in ops:
        target = op["target"]
        row = tables.get(target["kind"], {}).get(target["id"]) if isinstance(target.get("id"), int) else None
        since = row.get("last_updated") if row and op["op"] in ("update_fields", "place_device", "move_rack", "assign_ip") else None
        out.append({**op, "if_unmodified_since": since} if since else op)
    return out


# Bundle lines whose items can each be answered "there is none, on purpose": the step that asks about one object
# (`<prefix><object id>`) and the check that answer settles.
WAIVE = {"device_unreachable": ("ip_", "device:{}:primary_ip"), "device_no_power": ("pdu_", "device:{}:power"),
         "interface_no_cable": ("peer_", "interface:{}:cable")}


def waived(line: dict[str, Any], answers: dict[str, Any]) -> list[str]:
    """Checks of a line the player answered with "there is none, on purpose" (a device nobody logs in to, a port not
    in use): they are not asked again, count as answered in the site's completeness, and pay nothing."""
    if line["kind"] not in WAIVE:
        return []
    prefix, check = WAIVE[line["kind"]]
    return [check.format(k[len(prefix):]) for k, v in sorted(answers.items(), key=lambda kv: natural(kv[0]))
            if k.startswith(prefix) and k[len(prefix):].isdigit() and isinstance(v, dict) and v.get("choice") == "none"]


def prune(answers: dict[str, Any], confirmed: list[str]) -> dict[str, Any]:
    """The answers of a line without those whose check NetBox now confirms: an errand that goes on (three of six labels
    read) keeps what the player said about the rest and does not ask again about what is done. Check ids are
    `<object type>:<object id>:<field>`; answers about one object are keyed `<step>_<object id>`, and form values by the
    check id itself or by `<key>:<object id>` (rack units, floor tiles, room)."""
    form_keys: set[str] = set()
    step_ends: set[str] = set()
    site_fields: set[str] = set()
    for cid in confirmed:
        kind, _, rest = cid.partition(":")
        oid, _, fld = rest.partition(":")
        form_keys.add(cid)
        if kind in ("device", "interface"):
            step_ends.add(f"_{oid}")
            form_keys.add(f"u:{oid}")
        elif kind == "rack":
            form_keys.update({"location": [f"room:{oid}"], "iso": [f"x:{oid}", f"y:{oid}"]}.get(fld, []))
        elif kind == "site":
            site_fields.update({"gps": ["latitude", "longitude"]}.get(fld, [fld]))
    out: dict[str, Any] = {}
    for sid, ans in answers.items():
        if any(sid.endswith(end) for end in step_ends) and not (isinstance(ans, dict) and ans.get("choice") == "none"):
            continue
        if isinstance(ans, dict) and isinstance(ans.get("values"), dict):
            values = {k: v for k, v in ans["values"].items() if k not in form_keys and not (sid == "record" and k in site_fields)}
            if not values:
                continue
            ans = {**ans, "values": values}
        out[sid] = ans
    return out
