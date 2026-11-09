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

A line is complete when its finding is gone from a fresh analysis of NetBox (`open_checks`), never because the
client says so.
"""
from __future__ import annotations

import ipaddress
import re
from typing import Any

from .estate import Estate, is_network, is_passive, is_router, natural
from .findings import Analysis

XP = {"connectivity": 120, "identity": 50, "layout": 30}
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
                self.skipped.extend(checklist)
                return "unknown"
            raise Stop("parked", step, checklist)
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
                self.said.append({"step": sid, "question": prompt, "answer": raw.strip()})
                return value
            self.error = err
        raise Stop("asking", self._step(sid, prompt, "text", [UNKNOWN], hint=hint))

    def form(self, sid: str, prompt: str, fields: list[dict[str, Any]], checklist: list[str], hint: str | None = None) -> dict[str, Any]:
        """The filled-in fields of a form (blank ones left out). A form with nothing filled in parks the line."""
        step = self._step(sid, prompt, "form", [UNKNOWN], fields, hint)
        ans = self.answers.get(sid)
        if not isinstance(ans, dict):
            raise Stop("asking", step)
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
            raise Stop("asking", self._step(sid, prompt, "form", [UNKNOWN], fields, hint))
        if not out:
            raise Stop("parked", step, checklist)
        self.said.append({"step": sid, "question": prompt, "answer": f"{len(out)} of {len(fields)} filled in"})
        return out


def check_field(f: dict[str, Any], raw: Any) -> tuple[Any, str | None]:
    if f["type"] == "number":
        try:
            v = float(raw)
        except (TypeError, ValueError):
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
def _dev_label(e: Estate, d: dict[str, Any]) -> str:
    role = d.get("role_name") or d.get("role") or "device"
    return f"{d['name'] or 'unnamed'} ({role.lower()})"


def peer_candidates(e: Estate, dev: dict[str, Any], among: list[int] | None = None, circuits: bool = True,
                    linked_too: bool = False) -> list[dict[str, str]]:
    """Plausible far ends for a link from `dev`, likeliest first: routers and firewalls, then switches (busiest
    first), same room before other rooms; then circuits that end nowhere yet; then patch panels in the same rack.
    Devices `dev` already reaches are left out, unless `linked_too` (a second link, such as a management port)."""
    comp = e.components()
    pool = [e.devices[i] for i in among] if among is not None else [d for d in e.site_devices(dev["site_id"]) if is_network(d)]
    pool = [d for d in pool if d["id"] != dev["id"] and (linked_too or not comp.same(d["id"], dev["id"])) and e.free_ports(d["id"])]
    if linked_too:
        pool.sort(key=lambda d: (d["rack_id"] != dev["rack_id"], d["location_id"] != dev["location_id"], not is_router(d),
                                 natural(d["name"] or ""), d["id"]))
    else:
        pool.sort(key=lambda d: (not is_router(d), d["location_id"] != dev["location_id"], -comp.degree(d["id"]), natural(d["name"] or ""), d["id"]))
    out = [choice(f"device:{d['id']}", _dev_label(e, d)) for d in pool]
    if circuits and among is None:
        landed = {t["circuit_id"] for t in e.cterms.values() if t["cable_id"] is not None}
        for cid, c in sorted(e.circuits.items()):
            if cid not in landed:
                prov = e.providers.get(c["provider_id"] or 0, {}).get("name", "provider")
                out.append(choice(f"circuit:{cid}", f"circuit {c['cid']} ({prov})"))
        panels = [d for d in e.site_devices(dev["site_id"]) if is_passive(d) and d["rack_id"] == dev["rack_id"] and d["rack_id"] is not None
                  and any(p["cable_id"] is None for p in e.device_ports(d["id"], ("front_port",)))]
        out += [choice(f"device:{d['id']}", f"{d['name']} (patch panel)") for d in panels]
    return out[:MAX_CANDIDATES]


def look_checklist(e: Estate, dev: dict[str, Any]) -> list[str]:
    return [f"Find {dev['name'] or 'the device'}: {e.where(dev)}.",
            "Follow the cable from its uplink port (often the highest-numbered port, or a fibre port).",
            "At the far end, read the device's name and the port's label.",
            "Note what kind of cable it is (the jacket says Cat 6, OM4 or OS2)."]


def ask_port(ctx: Ctx, sid: str, dev: dict[str, Any], prompt: str, kinds: tuple[str, ...] = ("interface",)) -> tuple[str, int]:
    """Which free port of a device: a short list of the likeliest, or the port's name typed in."""
    e = ctx.e
    if kinds == ("interface",):
        free = e.free_ports(dev["id"])
        # the likeliest uplinks: the last ports of the fastest kind, then the rest in order
        speed = lambda p: 0 if "10g" in (p["type"] or "") or "sfp" in (p["type"] or "") else 1  # noqa: E731
        short = sorted(free, key=lambda p: (p["mgmt_only"], speed(p)))[:MAX_PORTS]
        short.sort(key=lambda p: natural(p["name"]))
    else:
        free = [p for p in e.device_ports(dev["id"], kinds) if p["cable_id"] is None]
        short = free[:MAX_PORTS]
    choices = [choice(f"{p['kind']}:{p['id']}", p["name"]) for p in short]
    if len(free) > len(short):
        choices.append(choice("typed", "Another port", "other"))
    checklist = [f"Find {dev['name']}: {e.where(dev)}.", "Read the label printed next to the port the cable is in."]
    picked = ctx.choice(sid, prompt, choices, checklist)
    if picked == "typed":
        by_name = {p["name"].lower(): p for p in free}

        def check(t: str) -> tuple[Any, str | None]:
            p = by_name.get(t.lower())
            return (p, None) if p else (None, f"{dev['name']} has no free port called {t!r}.")
        p = ctx.text(sid + "_name", f"Type the port's name as it is printed on {dev['name']}.", check,
                     f"{len(free)} free ports, for example {free[0]['name']}", checklist)
        return p["kind"], p["id"]
    kind, _, oid = picked.rpartition(":")
    return kind, int(oid)


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
        raise Stop("parked", None, [f"Both ends of that circuit are already recorded elsewhere; check it in NetBox (Circuits)."])
    ops.append({"op": "create_circuit_termination", "target": {"kind": "circuit_termination", "id": None}, "client_ref": "handoff",
                "fields": {"circuit_id": circuit_id, "term_side": side, "site_id": site_id}})
    return ops, ("circuit_termination", "@handoff")


def ask_new_circuit(ctx: Ctx, provider_id: int) -> dict[str, Any]:
    e = ctx.e
    prov = e.providers[provider_id]["name"]
    taken = {c["cid"].lower() for c in e.circuits.values() if c["provider_id"] == provider_id}

    def check(t: str) -> tuple[Any, str | None]:
        if len(t) > 100:
            return None, "A circuit ID has at most 100 characters."
        return (t, None) if t.lower() not in taken else (None, f"{prov} already has a circuit {t} in NetBox.")
    cid = ctx.text("circuit_id", f"What is the circuit ID? {prov} prints it on the bill and on the hand-off box.", check,
                   "exactly as printed, for example EXF-20417",
                   [f"Find the hand-off box or modem of {prov} (often by the incoming duct).",
                    "Read the circuit ID on its label; the bill and the order mail carry it too."])
    if not e.circuit_types:
        raise Stop("parked", None, ["NetBox has no circuit type yet: add one under Circuits > Circuit Types (for example Internet), then come back."])
    types = [choice(f"type:{t['id']}", t["name"]) for _, t in sorted(e.circuit_types.items())][:MAX_CANDIDATES]
    tid = ctx.choice("circuit_type", f"What kind of service is {cid}?", types, ["The order or the bill names the service."])
    return {"cid": cid, "provider_id": provider_id, "type_id": int(tid.split(":")[1])}


# ---------------------------------------------------------------- flows (one per finding kind)
def flow_link(ctx: Ctx) -> list[dict[str, Any]]:
    """A device (or a group of them) with no link: what does it connect to, on which ports, with what cable."""
    e, f = ctx.e, ctx.line["finding"]
    data, kind = f["data"], f["kind"]
    ids = data.get("device_ids") or [data["device_id"]]
    among: list[int] | None = data.get("main_device_ids")
    if kind == "sites_not_linked":
        among = [d["id"] for s in data["other_site_ids"] for d in e.site_devices(s) if is_network(d)]
    if len(ids) > 1:
        where = e.locations[data["location_id"]]["name"] if kind == "locations_not_linked" else "that group"
        other = {"locations_not_linked": lambda: e.locations[data["main_location_id"]]["name"],
                 "sites_not_linked": lambda: "another site"}.get(kind, lambda: "the rest of the site")()
        picked = ctx.choice("which", f"Which device in {where} has the link to {other}?",
                            [choice(f"device:{i}", _dev_label(e, e.devices[i])) for i in ids[:MAX_CANDIDATES]]
                            + ([choice("none", "None: there is no link, on purpose", "not_applicable")] if kind != "site_island" else []),
                            [f"Look in {where} for the cable that leaves the room or the rack row.", "Note which device and which port it starts from."])
        dev = e.devices[int(picked.split(":")[1])]
    else:
        dev = e.devices[ids[0]]
    ctx.at(device_id=dev["id"])
    n = sum(1 for _ in e.device_ports(dev["id"], ("interface",)))
    question = {
        "device_unconnected": f"{dev['name']} has {n} ports and nothing recorded on any of them. What does it uplink to?",
        "switch_no_uplink": f"{dev['name']} has devices plugged in, but no uplink. What does it uplink to?",
        "site_island": f"What does {dev['name']} connect to in the rest of {e.sites[dev['site_id']]['name']}?",
        "locations_not_linked": f"What does {dev['name']} connect to in {e.locations[data['main_location_id']]['name']}?" if kind == "locations_not_linked" else "",
        "sites_not_linked": f"How does {e.sites[dev['site_id']]['name']} reach your other sites? What does {dev['name']} connect to?",
    }[kind]
    cands = peer_candidates(e, dev, among, circuits=kind in ("device_unconnected", "switch_no_uplink", "sites_not_linked"))
    extra = [OTHER]
    if kind in ("device_unconnected", "sites_not_linked", "locations_not_linked"):
        extra.append(choice("none", "Nothing: it stands alone, on purpose", "not_applicable"))
    peer = ctx.choice("peer", question, cands + extra, look_checklist(e, dev))
    if peer == "other":
        name = ctx.text("other_name", "What is it called? The game cannot add a device for you yet, but it will remember.",
                        lambda t: ((t, None) if len(t) <= 64 else (None, "At most 64 characters.")), "the name on its label", look_checklist(e, dev))
        raise Stop("parked", None, [f"Add {name} to NetBox (Devices > Add, with its type and role).", "Come back: it will then be one of the choices."],
                   note=f"You said {dev['name']} connects to {name}, which is not in NetBox yet.")
    local = ask_port(ctx, "local_port", dev, f"Which port on {dev['name']} carries that link?")
    if peer.startswith("circuit:"):
        ops, end = land_circuit(ctx, int(peer.split(":")[1]), dev["site_id"], None)
        return ops + [cable_op(local, end, ask_cable_type(ctx))]
    far = e.devices[int(peer.split(":")[1])]
    kinds = ("front_port",) if is_passive(far) else ("interface",)
    remote = ask_port(ctx, "peer_port", far, f"And which port on {far['name']} does it land on?", kinds)
    return [cable_op(local, remote, ask_cable_type(ctx))]


def flow_way_out(ctx: Ctx) -> list[dict[str, Any]]:
    """The line to the outside: which device it plugs into, whose line it is, which circuit, which port."""
    e, f = ctx.e, ctx.line["finding"]
    data, kind = f["data"], f["kind"]
    site_id = f["site_id"]
    circuit_id: int | None = data.get("circuit_id")
    provider_id: int | None = data.get("provider_id")
    if kind in ("provider_unused", "circuit_unterminated"):
        what = f"{e.providers[provider_id]['name']}'s line" if kind == "provider_unused" else f"circuit {e.circuits[circuit_id]['cid']}"  # type: ignore[index]
        fixed = next((t["site_id"] for t in e.cterms.values() if t["circuit_id"] == circuit_id and t["site_id"] is not None and t["cable_id"] is None), None)
        if fixed is None:
            sites = [choice(f"site:{s}", e.sites[s]["name"]) for s in sorted(e.sites)][:MAX_CANDIDATES]
            gone = [choice("none", "Nowhere: we no longer use it", "not_applicable")]
            picked = ctx.choice("site", f"Which site does {what} come into?", sites + gone,
                                ["Ask whoever pays the bill which address the service is delivered to."])
            site_id = int(picked.split(":")[1])
        else:
            site_id = fixed
    site = e.sites[site_id]
    ctx.at(site_id=site_id)
    devs = sorted((d for d in e.site_devices(site_id) if is_network(d) and e.free_ports(d["id"])),
                  key=lambda d: (not is_router(d), natural(d["name"] or ""), d["id"]))
    line_name = "the line to the outside" if kind == "site_no_way_out" else "it"
    checklist = [f"At {site['name']}, find where the carrier's line comes in (a hand-off box, modem or fibre tray).",
                 "Follow its cable to the first device of yours and note that device's name.", "Note the port it is plugged into."]
    picked = ctx.choice("device", f"Which device at {site['name']} does {line_name} plug into?",
                        [choice(f"device:{d['id']}", _dev_label(e, d)) for d in devs[:MAX_CANDIDATES]] + [OTHER], checklist)
    if picked == "other":
        raise Stop("parked", None, ["Add that device to NetBox (Devices > Add, with its type and role).", "Come back: it will then be one of the choices."],
                   note="You said the line plugs into a device that is not in NetBox yet.")
    dev = e.devices[int(picked.split(":")[1])]
    ctx.at(device_id=dev["id"])
    new_circuit: dict[str, Any] | None = None
    if circuit_id is None:
        if provider_id is None:
            provs = sorted(e.providers.items())
            if len(provs) == 1:
                provider_id = provs[0][0]
            elif provs:
                p = ctx.choice("provider", "Whose line is it?", [choice(f"provider:{i}", pr["name"]) for i, pr in provs][:MAX_CANDIDATES] + [OTHER],
                               ["The hand-off box and the bill carry the carrier's name."])
                if p == "other":
                    raise Stop("parked", None, ["Add the carrier to NetBox (Circuits > Providers).", "Come back: it will then be one of the choices."])
                provider_id = int(p.split(":")[1])
        landed = {t["circuit_id"] for t in e.cterms.values() if t["cable_id"] is not None}
        open_circuits = [c for _, c in sorted(e.circuits.items()) if c["id"] not in landed and (provider_id is None or c["provider_id"] == provider_id)]
        prov_name = e.providers[provider_id]["name"] if provider_id in e.providers else "the carrier"
        if open_circuits:
            c = ctx.choice("circuit", f"Which circuit of {prov_name} is it?",
                           [choice(f"circuit:{c['id']}", c["cid"]) for c in open_circuits[:MAX_CANDIDATES]]
                           + ([choice("new", "A circuit not in NetBox yet", "other")] if provider_id in e.providers else []),
                           ["Read the circuit ID on the hand-off box's label."])
            if c != "new":
                circuit_id = int(c.split(":")[1])
        if circuit_id is None:
            if provider_id not in e.providers:
                raise Stop("parked", None, ["Add the carrier to NetBox (Circuits > Providers).", "Come back: it will then be one of the choices."])
            new_circuit = ask_new_circuit(ctx, provider_id)
    local = ask_port(ctx, "local_port", dev, f"Which port on {dev['name']} is the line plugged into?")
    ops, end = land_circuit(ctx, "@circuit" if new_circuit else circuit_id, site_id, new_circuit)  # type: ignore[arg-type]
    return ops + [cable_op(local, end, ask_cable_type(ctx))]


def flow_address(ctx: Ctx) -> list[dict[str, Any]]:
    """How to reach a device: one of the addresses it already has, or one the player types, on a port they pick."""
    e, f = ctx.e, ctx.line["finding"]
    ids = (f["data"].get("device_ids") or [f["data"]["device_id"]])[:MAX_BUNDLE]
    ops: list[dict[str, Any]] = []
    for did in ids:
        dev = e.devices[did]
        if ctx.target is None:
            ctx.at(device_id=did)
        have = e.device_ips(did)
        checklist = [f"Ask whoever looks after {dev['name']} which address they use to log in to it.",
                     "Or read it from the device: its management interface, or the label on the front."]
        picked = ctx.choice(f"ip_{did}", f"How do you reach {dev['name']} to manage it?",
                            [choice(f"ip:{ip['id']}", ip["address"]) for ip in have[:MAX_PORTS]]
                            + [choice("typed", "An address not in NetBox yet", "other")], checklist, soft=len(ids) > 1)
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
        addr = ctx.text(f"addr_{did}", f"What is the address of {dev['name']}, with its mask?", check, "for example 192.0.2.10/24", checklist)
        ports = sorted((p for p in e.device_ports(did, ("interface",))), key=lambda p: (not p["mgmt_only"], natural(p["name"])))
        port = ctx.choice(f"port_{did}", f"Which interface of {dev['name']} carries {addr}?",
                          [choice(f"interface:{p['id']}", p["name"]) for p in ports[:MAX_PORTS]], checklist, soft=len(ids) > 1)
        if port == "unknown":
            continue
        ops.append({"op": "assign_ip", "target": {"kind": "device", "id": did},
                    "fields": {"address": addr, "interface_id": int(port.split(":")[1]), "primary": True}})
    if not ops:
        raise Stop("parked", None, ctx.skipped)
    return ops


def flow_power(ctx: Ctx) -> list[dict[str, Any]]:
    """What powers each device of a rack: which power strip, which outlet."""
    e, f = ctx.e, ctx.line["finding"]
    ids = f["data"]["device_ids"][:MAX_BUNDLE]
    ops: list[dict[str, Any]] = []
    used: set[tuple[str, int]] = set()
    for did in ids:
        dev = e.devices[did]
        if ctx.target is None:
            ctx.at(device_id=did)
        feeders = [d for d in e.site_devices(dev["site_id"]) if d["id"] != did
                   and any(p["cable_id"] is None and (p["kind"], p["id"]) not in used for p in e.device_ports(d["id"], ("power_outlet",)))]
        feeders.sort(key=lambda d: (d["rack_id"] != dev["rack_id"], d["location_id"] != dev["location_id"], natural(d["name"] or "")))
        checklist = [f"Find {dev['name']}: {e.where(dev)}.", "Follow its power lead to the power strip (PDU) and read the strip's name.",
                     "Read the number printed next to the outlet."]
        picked = ctx.choice(f"pdu_{did}", f"{dev['name']} has no power feed on record. Which power strip is it plugged into?",
                            [choice(f"device:{d['id']}", f"{d['name']} ({e.where(d)})") for d in feeders[:MAX_CANDIDATES]], checklist,
                            soft=len(ids) > 1)
        if picked == "unknown":
            continue
        pdu = e.devices[int(picked.split(":")[1])]
        free = [p for p in e.device_ports(pdu["id"], ("power_outlet",)) if p["cable_id"] is None and (p["kind"], p["id"]) not in used]
        out = ctx.choice(f"outlet_{did}", f"Which outlet on {pdu['name']} feeds {dev['name']}?",
                         [choice(f"power_outlet:{p['id']}", p["name"]) for p in free[:MAX_CANDIDATES]], checklist, soft=len(ids) > 1)
        if out == "unknown":
            continue
        outlet = ("power_outlet", int(out.split(":")[1]))
        used.add(outlet)
        inlets = [p for p in e.device_ports(did, ("power_port",)) if p["cable_id"] is None]
        ops.append(cable_op(("power_port", inlets[0]["id"]), outlet, "power"))
    if not ops:
        raise Stop("parked", None, ctx.skipped)
    return ops


def flow_prefix(ctx: Ctx) -> list[dict[str, Any]]:
    f = ctx.line["finding"]
    network = f["data"]["network"]
    names = ", ".join(r["name"] for r in f["evidence"][:3])

    def check(t: str) -> tuple[Any, str | None]:
        try:
            net = ipaddress.ip_network(t, strict=True)
        except ValueError:
            return None, "Type a network with its mask, for example 192.0.2.0/24."
        if not all(ipaddress.ip_interface(r["name"]).ip in net for r in f["evidence"]):
            return None, f"{net} does not hold {names}."
        return str(net), None
    picked = ctx.choice("subnet", f"{names} sit in no prefix. Their mask says the subnet is {network}. Is that right?",
                        [choice("yes", f"Yes, it is {network}", "confirm"), choice("typed", "No, the subnet is another size", "other")],
                        ["Ask whoever runs the addressing plan, or read the mask on one of the devices."])
    net = network if picked == "yes" else ctx.text("subnet_typed", "What is the subnet, with its mask?", check, f"for example {network}", [])
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
    """Walk one rack top to bottom (or one shelf): the labels that tell each device apart."""
    e, f = ctx.e, ctx.line["finding"]
    data = f["data"]
    ids = data["device_ids"][:MAX_BUNDLE]
    open_ = set(data["gap_ids"])
    if data.get("rack_id"):
        ctx.at(rack_id=data["rack_id"])
        prompt = f"Walk rack {e.racks[data['rack_id']]['name']} top to bottom and read each label. Leave blank what you cannot see."
    else:
        ctx.at(device_id=ids[0])
        prompt = "Read the label of each device on the shelf. Leave blank what you cannot see."
    fields: list[dict[str, Any]] = []
    suggestion = name_pattern(e, f["site_id"]) if "label_maker" in ctx.tools else None
    platforms = [{"id": str(p["id"]), "label": p["name"]} for _, p in sorted(e.platforms.items())][:40]
    for did in ids:
        d = e.devices[did]
        where = f"U{d['position']:g}" if d["position"] is not None else "not in a unit"
        head = f"{d['name'] or 'unnamed'} ({where})"
        if f"device:{did}:serial" in open_:
            fields.append({"id": f"device:{did}:serial", "label": f"{head}: serial number", "type": "text", "max_length": 50,
                           "hint": "on the pull-out tag or the rear label", "suggestion": None, "options": []})
        if f"device:{did}:asset_tag" in open_:
            fields.append({"id": f"device:{did}:asset_tag", "label": f"{head}: asset tag", "type": "text", "max_length": 50,
                           "hint": "your own sticker" + (f"; label maker: the next free tag here is {suggestion}" if suggestion else ""),
                           "suggestion": suggestion, "options": []})
            suggestion = None  # one suggestion per form: the player confirms each tag they stick on
        if f"device:{did}:platform" in open_ and platforms:
            fields.append({"id": f"device:{did}:platform", "label": f"{head}: platform (operating system)", "type": "select",
                           "hint": "what it runs", "suggestion": None, "options": platforms})
    checklist = [f"Go to {ctx.target['label'] if ctx.target else 'the rack'}.", "Start at the top unit and work down, one device at a time.",
                 "Serial numbers are on a pull-out tag at the front or a label at the rear; photograph them if the light is poor.",
                 "An asset tag is your own sticker: if a device has none, that is worth knowing too."]
    values = ctx.form("labels", prompt, fields, checklist)
    per_dev: dict[int, dict[str, Any]] = {}
    for fid, v in values.items():
        _, did, fld = fid.split(":")
        per_dev.setdefault(int(did), {})["platform_id" if fld == "platform" else fld] = int(v) if fld == "platform" else v
    return [{"op": "update_fields", "target": {"kind": "device", "id": did}, "fields": fs} for did, fs in sorted(per_dev.items())]


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
    values = ctx.form("record", f"Where is {site['name']}? Fill in what you know; leave the rest blank.", fields,
                      ["Look the address up on a map and note the latitude and longitude of the building.",
                       "The carrier's order or the landlord's contract names the facility."])
    out = {k: (round(float(v), 6) if k in ("latitude", "longitude") else (v.upper() if k == "country" else v)) for k, v in values.items()}
    if ("latitude" in out) != ("longitude" in out) and "gps" in f["data"]["fields"]:
        ctx.error = "Latitude and longitude go together: fill in both or neither."
        raise Stop("asking", ctx._step("record", f"Where is {site['name']}? Fill in what you know; leave the rest blank.", "form", [UNKNOWN], fields))
    return [{"op": "update_fields", "target": {"kind": "site", "id": site["id"]}, "fields": out}]


def flow_units(ctx: Ctx) -> list[dict[str, Any]]:
    e, f = ctx.e, ctx.line["finding"]
    rack = e.racks[f["data"]["rack_id"]]
    ctx.at(rack_id=rack["id"])
    fields = []
    for did in f["data"]["device_ids"][:MAX_BUNDLE]:
        d = e.devices[did]
        fields.append({"id": f"u:{did}", "label": f"{d['name'] or 'unnamed'}: lowest rack unit it occupies", "type": "number", "integer": True,
                       "min": 1, "max": rack["u_height"], "hint": f"1 to {rack['u_height']}, counted as printed on the rack rail", "suggestion": None, "options": []})
    values = ctx.form("units", f"Walk rack {rack['name']} top to bottom: which unit is each of these devices in?", fields,
                      [f"Go to rack {rack['name']}.", "Read the unit number on the rail next to the bottom edge of each device."])
    return [{"op": "place_device", "target": {"kind": "device", "id": int(fid.split(":")[1])},
             "fields": {"rack_id": rack["id"], "position": v, "face": "front"}} for fid, v in sorted(values.items())]


def flow_layout(ctx: Ctx) -> list[dict[str, Any]]:
    e, f = ctx.e, ctx.line["finding"]
    ops: list[dict[str, Any]] = []
    fields = []
    rooms = [{"id": str(loc["id"]), "label": loc["name"]} for _, loc in sorted(e.locations.items()) if loc["site_id"] == f["site_id"]]
    for rid in f["data"]["rack_ids"][:MAX_BUNDLE]:
        r = e.racks[rid]
        if ctx.target is None:
            ctx.at(rack_id=rid)
        if r["location_id"] is None and rooms:
            fields.append({"id": f"room:{rid}", "label": f"Rack {r['name']}: which room is it in?", "type": "select", "options": rooms, "hint": None, "suggestion": None})
        if r["iso_x"] is None or r["iso_y"] is None:
            for axis, word in (("x", "column, counted from the room's left wall"), ("y", "row, counted from the room's far wall")):
                fields.append({"id": f"{axis}:{rid}", "label": f"Rack {r['name']}: floor tile {word}", "type": "number", "integer": True,
                               "min": 0, "max": 500, "hint": "one tile is one rack width (about 60 cm)", "suggestion": None, "options": []})
    values = ctx.form("floor", "Where do these racks stand? Count floor tiles from the corner of the room, or drag each rack in the room view.",
                      fields, ["Stand in the doorway and sketch the room: one square per floor tile.", "Mark each rack's square and which way its front faces."])
    per: dict[int, dict[str, Any]] = {}
    for fid, v in values.items():
        key, rid = fid.split(":")
        per.setdefault(int(rid), {})[{"room": "location_id", "x": "iso_x", "y": "iso_y"}[key]] = int(v)
    for rid, fs in sorted(per.items()):
        if ("iso_x" in fs) != ("iso_y" in fs):
            ctx.error = "A rack needs both its column and its row."
            raise Stop("asking", ctx._step("floor", "Where do these racks stand?", "form", [UNKNOWN], fields))
        ops.append({"op": "move_rack", "target": {"kind": "rack", "id": rid}, "fields": fs})
    return ops


def flow_port(ctx: Ctx) -> list[dict[str, Any]]:
    """A port NetBox says is in use, with no cable: what is on the other end."""
    e, f = ctx.e, ctx.line["finding"]
    port = e.ports[("interface", f["data"]["interface_id"])]
    dev = e.devices[port["device_id"]]
    ctx.at(device_id=dev["id"])
    cands = peer_candidates(e, dev, linked_too=True)
    servers = [d for d in e.site_devices(dev["site_id"]) if d["id"] != dev["id"] and not is_network(d) and not is_passive(d) and e.free_ports(d["id"])]
    cands = (cands + [choice(f"device:{d['id']}", _dev_label(e, d)) for d in servers])[:MAX_CANDIDATES]
    why = f"described as {port['description'].strip()[:40]!r}" if port["description"].strip() else "carrying an address"
    peer = ctx.choice("peer", f"{dev['name']} port {port['name']} is {why}, but has no cable on record. What is on the other end?",
                      cands + [OTHER, choice("none", "Nothing: the port is not in use", "not_applicable")], look_checklist(e, dev))
    if peer == "other":
        raise Stop("parked", None, ["Add the far-end device to NetBox (Devices > Add).", "Come back: it will then be one of the choices."])
    if peer.startswith("circuit:"):
        ops, end = land_circuit(ctx, int(peer.split(":")[1]), dev["site_id"], None)
        return ops + [cable_op(("interface", port["id"]), end, ask_cable_type(ctx))]
    far = e.devices[int(peer.split(":")[1])]
    remote = ask_port(ctx, "peer_port", far, f"Which port on {far['name']} does it land on?", ("front_port",) if is_passive(far) else ("interface",))
    return [cable_op(("interface", port["id"]), remote, ask_cable_type(ctx))]


FLOWS = {
    "device_unconnected": flow_link, "switch_no_uplink": flow_link, "site_island": flow_link, "locations_not_linked": flow_link,
    "sites_not_linked": flow_link, "site_no_way_out": flow_way_out, "circuit_unterminated": flow_way_out, "provider_unused": flow_way_out,
    "router_no_address": flow_address, "device_unreachable": flow_address, "device_no_power": flow_power, "address_no_prefix": flow_prefix,
    "rack_audit": flow_audit, "shelf_audit": flow_audit, "site_record": flow_site, "rack_devices_unpositioned": flow_units,
    "room_layout": flow_layout, "interface_no_cable": flow_port,
}

# Plain-words purpose (why it matters) and what the admin can do once it is true, per finding kind.
WORDS: dict[str, tuple[str, str, str]] = {
    # kind: (quest title, purpose, what you can now do)
    "site_no_way_out": ("The way out of {site}", "Nobody can tell how {site} reaches the internet. Let's find out.",
                        "You can now trace {site} to the carrier's hand-off."),
    "sites_not_linked": ("{site} and the other sites", "NetBox cannot say how {site} reaches your other sites. Let's find the link.",
                         "You can now trace from {site} to another site."),
    "locations_not_linked": ("The link between two rooms", "If the link between these rooms fails, nobody knows which cable to check. Let's record it.",
                             "You can now trace from one room to the other."),
    "site_island": ("Cut off from the rest", "These devices are cabled to each other, but NetBox cannot say how they reach the rest of {site}.",
                    "You can now trace from these devices to the rest of {site}."),
    "switch_no_uplink": ("An uplink for {subject}", "When {subject} goes quiet, nobody can tell what sits above it. Let's record its uplink.",
                         "You can now trace {subject} towards the core."),
    "circuit_unterminated": ("Where {subject} lands", "A circuit you pay for ends nowhere in NetBox. Let's record where it lands.",
                             "You can now trace circuit {subject} to the device it plugs into."),
    "provider_unused": ("{subject}'s line", "{subject} is on record as a provider, with nothing of theirs recorded. Let's find their line.",
                        "You can now look up which circuit {subject} delivers, and where."),
    "device_unconnected": ("What {subject} plugs into", "If {subject} drops off the network, nobody can tell which port to check. Let's record its uplink.",
                           "You can now trace {subject} to what it plugs into."),
    "router_no_address": ("An address for {subject}", "{subject} routes traffic, but NetBox cannot say on which address. Let's record one.",
                          "You can now look up how to reach {subject}."),
    "device_unreachable": ("How to reach the kit at {site}", "When something breaks, the first question is how to log in. Let's record that.",
                           "You can now look up the management address of these devices."),
    "device_no_power": ("What powers rack {subject}", "If a power strip trips, nobody can tell what goes dark with it. Let's record what feeds this rack.",
                        "You can now see what loses power when a strip in rack {subject} trips."),
    "address_no_prefix": ("A home for stray addresses", "Addresses with no prefix cannot be planned around. Let's record their subnet.",
                          "You can now see how full that subnet is and pick a free address in it."),
    "interface_no_cable": ("The other end of {subject}", "A port is marked as in use, but nobody recorded what it connects to.",
                           "You can now trace that port to its far end."),
    "rack_audit": ("Audit rack {subject}", "A device with no serial or tag cannot be matched to a support contract or found after a move. Let's walk the rack.",
                   "You can now match every device you read in rack {subject} to its paperwork."),
    "shelf_audit": ("Audit the shelf", "Spare kit with no serial is kit nobody can prove you own. Let's read the labels.",
                    "You can now match those devices to their paperwork."),
    "site_record": ("Put {site} on the map", "{site} has no position or facility name, so it cannot be put on a map or named to a carrier.",
                    "You can now find {site} on the map and name it to a carrier."),
    "rack_devices_unpositioned": ("Rack units in {subject}", "Devices in rack {subject} have no unit, so nobody can be sent to the right one.",
                                  "You can now send someone to the exact unit in rack {subject}."),
    "room_layout": ("The floor plan", "Racks with no place on the floor plan cannot be found by someone new. Let's place them.",
                    "You can now point to those racks on the floor plan."),
}


def subject_of(e: Estate, f: dict[str, Any]) -> str:
    d = f["data"]
    if "rack_id" in d and d["rack_id"] in e.racks:
        return e.racks[d["rack_id"]]["name"]
    if "device_id" in d:
        return e.device_name(d["device_id"])
    if "circuit_id" in d and d["circuit_id"] in e.circuits:
        return e.circuits[d["circuit_id"]]["cid"]
    if "provider_id" in d and d["provider_id"] in e.providers:
        return e.providers[d["provider_id"]]["name"]
    if "interface_id" in d:
        p = e.ports[("interface", d["interface_id"])]
        return f"{e.device_name(p['device_id'])} {p['name']}"
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
    seen: set[int] = set()
    for g in a.gaps:
        if g["field"] == "cable" and g["device_id"] not in seen:
            seen.add(g["device_id"])
            findings.append({"id": f"interface_no_cable:interface:{g['object_id']}", "kind": "interface_no_cable", "rank": "connectivity",
                             "site_id": g["site_id"], "title": g["label"],
                             "evidence": [{"kind": "interface", "id": g["object_id"], "name": g["label"].split(" has ")[0]}],
                             "data": {"interface_id": g["object_id"]}})
    from .findings import _sort_key

    for f in sorted(findings, key=_sort_key):
        site = e.sites[f["site_id"]]["name"] if f["site_id"] in e.sites else "the estate"
        title, purpose, outcome = (t.format(site=site, subject=subject_of(e, f)) for t in WORDS[f["kind"]])
        out.append({"id": f"q:{f['id']}", "kind": f["kind"], "rank": f["rank"], "site_id": f["site_id"], "title": clip(title, 80),
                    "purpose": purpose, "outcome": outcome, "finding": f, "xp": XP[f["rank"]], "minutes": MINUTES[f["rank"]]})
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
               "title": f"Is {d['name']} still as recorded?", "evidence": [{"kind": "device", "id": d["id"], "name": d["name"] or "device"}],
               "data": {"device_id": d["id"]}}
    return {"id": f"q:{finding['id']}", "kind": "spot_check", "rank": "identity", "site_id": site_id, "title": f"Spot check: {d['name']}",
            "purpose": f"Everything I can see at {site} is in order. Shall we spot-check one device, to keep it that way?",
            "outcome": f"You can now trust the record of {d['name']}: it was checked against the rack.", "finding": finding, "xp": 0, "minutes": 2}


def flow_spot(ctx: Ctx) -> list[dict[str, Any]]:
    e = ctx.e
    d = e.devices[ctx.line["finding"]["data"]["device_id"]]
    ctx.at(device_id=d["id"])
    serial = f", serial {d['serial']}" if d["serial"] else ""
    picked = ctx.choice("same", f"Go and look: is {d['name']} still in {e.where(d)}{serial}?",
                        [choice("yes", "Yes, exactly as recorded", "confirm"), choice("no", "No, something has changed", "other")],
                        [f"Find {d['name']}: {e.where(d)}.", "Compare its label with the record: name, unit, serial."])
    if picked == "no":
        raise Stop("parked", None, [f"Open {d['name']} in the rack view and correct what differs (every change is previewed first).",
                                    "Or correct it in NetBox itself. Then tell me it matches."],
                   note=f"You found that {d['name']} no longer matches its record.")
    raise Stop("confirmed")


def open_checks(a: Analysis, line: dict[str, Any]) -> list[str]:
    """The checks of a line that a (fresh) analysis still finds open."""
    checks = a.checks.get(line["site_id"], {})
    return [c for c in check_ids(line) if checks.get(c) is False]


def check_ids(line: dict[str, Any]) -> list[str]:
    """Every check a line stands for when it is first offered (the base its progress is measured against)."""
    d = line["finding"]["data"]
    if d.get("gap_ids") is not None:
        return list(d["gap_ids"])
    if line["kind"] == "device_unreachable":
        return [f"device:{i}:primary_ip" for i in d["device_ids"]]
    if line["kind"] == "interface_no_cable":
        return [f"interface:{d['interface_id']}:cable"]
    return [line["finding"]["id"]]


def evaluate(e: Estate, line: dict[str, Any], answers: dict[str, Any], tools: frozenset[str] = frozenset()) -> dict[str, Any]:
    ctx = Ctx(e, line, answers, tools)
    base = {"step": None, "proposal": None, "checklist": [], "note": None, "changes": []}
    try:
        ops = (flow_spot if line["kind"] == "spot_check" else FLOWS[line["kind"]])(ctx)
        state = {**base, "status": "ready", "checklist": ctx.skipped, "changes": describe(e, ops),
                 "proposal": {"ops": ops, "reason": clip(f"quest: {line['title']}", 200)}}
    except Stop as s:
        state = {**base, "status": s.status, "step": s.step, "checklist": s.checklist, "note": s.note}
    except KeyError:
        # the estate changed under the line (an object it named is gone): start the line again from its first question
        return evaluate(e, line, {}, tools) if answers else {**base, "status": "parked", "said": [], "target": None,
                                                           "checklist": ["This changed in NetBox while you were away; look again later."]}
    if ctx.target is None and line["site_id"] in e.sites:
        ctx.at(site_id=line["site_id"])
    return {**state, "said": ctx.said, "target": ctx.target}
