"""Quest chains: the quest lines about one subject as a short series of stages.

A chain takes an admin from knowing nothing about a subject to a complete record of it, in three stages:

    physical        what is plugged into what: the device, the port, the cable, the circuit's end
    logical         the addresses on it, and how the device is managed
    administrative  whose it is and what stands on paper: the provider's network, the account, who to call, the
                    install date and commit rate, the charges; for a rack, the labels that match kit to its paperwork

The subjects are: a site's way out (`way_out`), a site's link to the other sites (`site_link`), the link between two
rooms (`room_link`), a device (`device`) and a rack (`rack`). A subject has only the stages that apply to it: a cable
between two rooms has no carrier and no bill, so that chain is its physical stage alone.

A chain is derived from the data, never stored. `build(e, a, lines)` is a pure function of an estate, its analysis and
its quest lines: each stage is a list of the scanner's checks (findings.py) and the open quest lines that settle them.
`view(...)` then says where one player stands in it:

    done       every check of the stage passes in NetBox: shown as done, never asked
    answered   what is still open was answered "none, on purpose"
    not_known  what is still open was answered "I don't know yet" (only facts of a circuit's record can be): the chain
               stays honestly open at that stage, and the open facts are listed as open questions
    open       the stage in play
    locked     an earlier stage comes first: its lines are not offered yet

A later stage unlocks when every earlier one is done or answered. docs/quests.md "Chains" describes this for a NetBox
admin.
"""
from __future__ import annotations

from typing import Any

from .estate import Estate, is_passive, is_physical
from .estate import name_of as nm
from .findings import CIRCUIT_FACTS, FACT_SHORT, LIVE, Analysis
from .findings import _some as some

STAGES = ("physical", "logical", "administrative")
STAGE_TITLES = {"physical": "what is plugged into what", "logical": "addresses, and how it is managed",
                "administrative": "whose it is, who to call, what it costs"}
KINDS = ("way_out", "site_link", "room_link", "device", "rack")
# What each stage of a chain puts on record. A chain says what it will hold from the stages it has, so a link that is
# a plain cable (one stage) promises no carrier and no bill.
STAGE_HOLDS = {
    ("way_out", "physical"): "the port the line plugs into, and the circuit",
    ("way_out", "logical"): "your address on it",
    ("way_out", "administrative"): "the carrier's network, your account, who to call, and what it costs",
    ("site_link", "physical"): "the port and the circuit that join the sites",
    ("site_link", "logical"): "your address on it",
    ("site_link", "administrative"): "the carrier's network, your account, who to call, and what it costs",
    ("room_link", "physical"): "the cable between the two rooms, and the port at each end",
    ("device", "physical"): "what the device is plugged into",
    ("device", "logical"): "how it is managed, and the address to log in to it if it has one",
    ("rack", "physical"): "where the rack stands, which unit each device is in, and what powers it",
    ("rack", "administrative"): "the serial number and asset tag of each device",
}
# The bonus for a chain NetBox shows complete: more than any single stage of it pays. A chain of one stage has none.
CHAIN_XP = {"way_out": 250, "site_link": 250, "room_link": 0, "device": 150, "rack": 100}


def holds(kind: str, stages: list[str]) -> str:
    """What a chain's finished record will hold, from the stages it has."""
    return "; ".join(STAGE_HOLDS[(kind, stage)] for stage in stages if (kind, stage) in STAGE_HOLDS)


def build(e: Estate, a: Analysis, lines: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Every chain of an estate, in the order they come into play at a site: the way out, the links, then devices,
    then racks. Made once per estate, like its analysis."""
    key = "chains" if e._memo.get("analysis") is a else None
    if key and key in e._memo:
        return e._memo[key]
    out = _build(e, a, lines)
    if key:
        e._memo[key] = out
    return out


def _build(e: Estate, a: Analysis, lines: list[dict[str, Any]]) -> list[dict[str, Any]]:
    from .quests import check_ids

    by_check: dict[str, list[str]] = {}
    for line in lines:
        for cid in check_ids(line):
            by_check.setdefault(cid, []).append(line["id"])

    def made(site_id: int, *cids: str) -> list[str]:
        return [c for c in cids if c in a.checks.get(site_id, {})]

    def chain(kind: str, obj: tuple[str, int], site_id: int, title: str, stages: dict[str, list[str]], always: tuple[str, ...] = ()) -> None:
        """A chain with the stages that have checks (and those in `always`, which wait for an earlier stage to produce
        what they are about: a circuit has no address to ask for before it is plugged in)."""
        rows = []
        for stage in STAGES:
            checks = list(dict.fromkeys(stages.get(stage, [])))
            if checks or stage in always:
                rows.append({"stage": stage, "title": STAGE_TITLES[stage], "checks": checks,
                             "line_ids": list(dict.fromkeys(q for c in checks for q in by_check.get(c, [])))})
        if any(r["checks"] for r in rows):
            out.append({"id": f"chain:{kind}:{obj[0]}:{obj[1]}", "kind": kind, "site_id": site_id, "title": title,
                        "stages": rows, "object": {"kind": obj[0], "id": obj[1]}})

    out: list[dict[str, Any]] = []
    # circuits by the site they plug in at, and whether another of the estate's sites is at their far end
    plugged: dict[int, list[int]] = {}
    between: set[int] = set()
    for cid in sorted(e.circuits):
        hand = e.circuit_handoff(cid)
        if hand is not None and hand[2]["site_id"] in e.sites:
            plugged.setdefault(hand[2]["site_id"], []).append(cid)
        if len({t["site_id"] for t in e.cterms.values() if t["circuit_id"] == cid and t["site_id"] in e.sites}) > 1:
            between.add(cid)
    loose: dict[int, list[int]] = {}
    for t in e.cterms.values():
        if t["site_id"] in e.sites and t["cable_id"] is None:
            loose.setdefault(t["site_id"], []).append(t["circuit_id"])

    def circuit_stages(site_id: int, circuits: list[int]) -> dict[str, list[str]]:
        return {"logical": [c for i in circuits for c in made(site_id, f"circuit_handoff:circuit:{i}")],
                "administrative": [c for i in circuits for c in made(site_id, *(f"circuit:{i}:{fact}" for fact in CIRCUIT_FACTS))]}

    for site_id in sorted(e.sites):
        site = e.sites[site_id]["name"]
        here = plugged.get(site_id, [])
        out_circuits = [c for c in here if c not in between]
        physical = made(site_id, f"site_no_way_out:site:{site_id}") + \
            [c for i in sorted(set(loose.get(site_id, []))) if i not in between for c in made(site_id, f"circuit_unterminated:circuit:{i}")]
        if physical or out_circuits:
            chain("way_out", ("site", site_id), site_id, f"The way out of {site}", {"physical": physical, **circuit_stages(site_id, out_circuits)},
                  always=STAGES)
        link_circuits = [c for c in here if c in between]
        physical = made(site_id, f"sites_not_linked:site:{site_id}") + \
            [c for i in sorted(set(loose.get(site_id, []))) if i in between for c in made(site_id, f"circuit_unterminated:circuit:{i}")]
        if physical or link_circuits:
            chain("site_link", ("site", site_id), site_id, f"{site} and the other sites",
                  {"physical": physical, **circuit_stages(site_id, link_circuits)})
        for cid in sorted(a.checks.get(site_id, {})):
            if cid.startswith("locations_not_linked:location:"):
                loc = int(cid.rsplit(":", 1)[1])
                chain("room_link", ("location", loc), site_id, f"The link from {e.locations.get(loc, {}).get('name', 'a room')}", {"physical": [cid]})
        for d in e.site_devices(site_id):
            did = d["id"]
            ports = [c for c in a.checks.get(site_id, {}) if c.startswith("interface:") and c.endswith(":cable")
                     and e.ports.get(("interface", int(c.split(":")[1])), {}).get("device_id") == did] if d["status"] in LIVE else []
            chain("device", ("device", did), site_id, f"{nm(d)}: from its ports to its login", {
                "physical": made(site_id, f"device_unconnected:device:{did}", f"switch_no_uplink:device:{did}") + ports,
                "logical": made(site_id, f"router_no_address:device:{did}", f"device:{did}:primary_ip")})
        for rid, rack in sorted(e.racks.items()):
            if rack["site_id"] != site_id:
                continue
            inside = [d["id"] for d in e.site_devices(site_id) if d["rack_id"] == rid]
            chain("rack", ("rack", rid), site_id, f"Rack {rack['name']}", {
                "physical": made(site_id, f"rack:{rid}:location", f"rack:{rid}:iso", f"rack_devices_unpositioned:rack:{rid}",
                                 *(f"device:{i}:power" for i in inside)),
                "administrative": made(site_id, *(f"device:{i}:{fld}" for i in inside for fld in ("serial", "asset_tag")))})
    out.sort(key=lambda c: (c["site_id"], KINDS.index(c["kind"]), c["object"]["id"]))
    return out


def stage_of(chains: list[dict[str, Any]]) -> dict[str, tuple[dict[str, Any], int]]:
    """Quest line id -> (its chain, the index of its stage). A line belongs to the first chain that names it."""
    out: dict[str, tuple[dict[str, Any], int]] = {}
    for c in chains:
        for i, stage in enumerate(c["stages"]):
            for qid in stage["line_ids"]:
                out.setdefault(qid, (c, i))
    return out


def of_checks(chains: list[dict[str, Any]], checks: list[str]) -> dict[str, Any] | None:
    """The first chain one of whose stages holds one of these checks (the chain of a line, also once its finding is gone)."""
    want = set(checks)
    return next((c for c in chains if any(want & set(s["checks"]) for s in c["stages"])), None)


def view(e: Estate, a: Analysis, chain: dict[str, Any], resolved: set[str] | frozenset[str] = frozenset(),
         deferred: set[str] | frozenset[str] = frozenset(), titles: dict[str, str] | None = None, told: bool = True,
         pays: bool = True) -> dict[str, Any]:
    """Where a player stands in a chain: each stage's status, the position ("2 of 3: logical"), what is still missing,
    and the record so far in one sentence. `resolved` are checks the player answered "none, on purpose"; `deferred` are
    facts answered "I don't know yet". `pays` is false where nothing is paid (the practice island, a dry run, a player who may not write): no bonus is shown."""
    site_id = chain["site_id"]
    stages: list[dict[str, Any]] = []
    blocked = False
    for s in chain["stages"]:
        open_ = [c for c in s["checks"] if a.check(c, site_id) is False]
        left = [c for c in open_ if c not in resolved]
        if not s["checks"]:
            status = "locked" if blocked else "done"
        elif not open_:
            status = "done"
        elif not left:
            status = "answered"
        elif blocked:
            status = "locked"
        elif all(c in deferred for c in left):
            status = "not_known"
        else:
            status = "open"
        if status in ("open", "not_known", "locked"):
            blocked = True
        stages.append({"stage": s["stage"], "title": s["title"], "status": status, "quest_ids": list(s["line_ids"]),
                       "missing": _missing(left, s, titles or {}), "_left": left})
    # a stage with no checks that nothing blocks has nothing in it (the way out goes through another site's circuit)
    stages = [s for s, raw in zip(stages, chain["stages"]) if raw["checks"] or s["status"] == "locked"]
    total = len(stages)
    current = next((i for i, s in enumerate(stages) if s["status"] in ("open", "not_known")), None)
    not_known = [m for s in stages if s["status"] == "not_known" for m in s["missing"]]
    if current is None:
        status, position = "complete", total
        label = f"{total} of {total}: complete"
    else:
        position = current + 1
        waiting = stages[current]["status"] == "not_known"
        status = "open_questions" if waiting else "open"
        label = f"{position} of {total}: {stages[current]['stage']}" + (f" (not known yet: {some(not_known)})" if waiting else "")
    for s in stages:
        s.pop("_left")
    return {"id": chain["id"], "kind": chain["kind"], "site_id": site_id, "title": chain["title"],
            "holds": holds(chain["kind"], [s["stage"] for s in stages]), "status": status,
            "position": position, "total": total, "label": label, "stages": stages, "summary": summary(e, chain) if told else None,
            "open_questions": not_known, "bonus_xp": CHAIN_XP.get(chain["kind"], 0) if total > 1 and pays else 0}


def verified(a: Analysis, chain: dict[str, Any]) -> bool:
    """NetBox itself shows the whole record: every check of every stage passes (nothing merely answered or unknown)."""
    checks = [c for s in chain["stages"] for c in s["checks"]]
    return bool(checks) and all(a.check(c, chain["site_id"]) is True for c in checks)


def _missing(left: list[str], stage: dict[str, Any], titles: dict[str, str]) -> list[str]:
    out: list[str] = []
    for c in left:
        parts = c.split(":")
        if parts[0] == "circuit" and parts[-1] in FACT_SHORT:
            out.append(FACT_SHORT[parts[-1]])
    for qid in stage["line_ids"]:
        if qid in titles and not qid.startswith("q:circuit_record:"):
            out.append(titles[qid])
    return list(dict.fromkeys(out))


# ---------------------------------------------------------------- the record in one sentence, read from the estate
def circuit_summary(e: Estate, circuit_id: int) -> str | None:
    """One circuit's record as NetBox holds it: where it plugs in, whose it is, the account, who to call, the cost."""
    hand = e.circuit_handoff(circuit_id)
    c = e.circuits.get(circuit_id)
    if hand is None or c is None:
        return None
    _, port, dev = hand
    prov = e.providers.get(c["provider_id"] or 0, {}).get("name")
    bits = [f"{nm(dev)} {port['name']} -> circuit {c['cid']}" + (f" ({c['type']})" if c.get("type") else "")]
    if prov:
        bits.append(prov)
    net = next((e.networks[t["network_id"]]["name"] for t in e.cterms.values()
                if t["circuit_id"] == circuit_id and t.get("network_id") in e.networks), None)
    if net:
        bits.append(f"network {net}")
    ips = [ip["address"] for ip in e._ips_by_interface().get(port["id"], [])] if port["kind"] == "interface" else []
    if ips:
        bits.append(f"your address {ips[0]}")
    acct = e.accounts.get(c.get("account_id") or 0)
    if acct:
        bits.append(f"account {acct['account']}")
    seen: set[int] = set()
    for a in e.contacts_of(circuit_id)[:3]:
        if a["contact_id"] in seen:
            continue
        seen.add(a["contact_id"])
        how = a["contact"]["phone"] or a["contact"]["email"]
        bits.append(f"{a['role'] or 'contact'}: {a['contact']['name']}" + (f" {how}" if how else ""))
    cur = c.get("currency") or ""
    if c.get("mrc") is not None:
        bits.append(f"{c['mrc']} {cur} a month".replace("  ", " "))
    if c.get("nrc") is not None:
        bits.append(f"{c['nrc']} {cur} once".replace("  ", " "))
    if c.get("install_date"):
        bits.append(f"installed {c['install_date']}")
    if c.get("commit_rate") is not None:
        bits.append(f"{c['commit_rate'] / 1000:g} Mbps committed")
    return ", ".join(bits)


def _cable_words(e: Estate, port: dict[str, Any]) -> str | None:
    cable = e.cables.get(port["cable_id"] or 0)
    if cable is None:
        return None
    for key in list(cable["a"]) + list(cable["b"]):
        key = tuple(key)
        if key == (port["kind"], port["id"]):
            continue
        if key[0] == "circuit_termination":
            term = e.cterms.get(key[1])
            return f"circuit {e.circuits[term['circuit_id']]['cid']}" if term and term["circuit_id"] in e.circuits else "a circuit"
        far = e.ports.get(key)  # type: ignore[arg-type]
        if far is not None:
            return f"{e.device_name(far['device_id'])} {far['name']}"
    return None


def summary(e: Estate, chain: dict[str, Any]) -> str | None:
    """The record of a chain's subject as the estate holds it now, in one plain sentence; None while it holds nothing
    of it. Read from the estate (NetBox, read again after a write), never from what the player typed."""
    kind, oid, site_id = chain["kind"], chain["object"]["id"], chain["site_id"]
    site = e.sites.get(site_id, {}).get("name", "the site")
    if kind in ("way_out", "site_link"):
        rows = []
        for cid in sorted(e.circuits):
            hand = e.circuit_handoff(cid)
            if hand is None or hand[2]["site_id"] != site_id:
                continue
            sites = {t["site_id"] for t in e.cterms.values() if t["circuit_id"] == cid and t["site_id"] in e.sites}
            if (len(sites) > 1) == (kind == "site_link"):
                rows.append(circuit_summary(e, cid))
        rows = [r for r in rows if r]
        return f"{site}: " + "; ".join(rows) + "." if rows else None
    if kind == "device":
        d = e.devices.get(oid)
        if d is None:
            return None
        links = [f"{p['name']} -> {w}" for p in e.device_ports(oid, ("interface",)) if is_physical(p) for w in [_cable_words(e, p)] if w]
        mode = e.mgmt_mode(d)
        service = e.mgmt_service(d)
        how = {"local": f"managed locally at {d['primary_ip']}" if d.get("primary_ip") else "managed locally (no address on record)",
               "cloud": f"managed by {service or 'a cloud or controller service'} (no local address)",
               "unmanaged": "not managed (nobody logs in to it)"}.get(mode or "")
        bits = ([some(links[:3]) + (f" and {len(links) - 3} more" if len(links) > 3 else "")] if links else []) + ([how] if how else [])
        return f"{nm(d)}: " + "; ".join(bits) + "." if bits else None
    if kind == "rack":
        rack = e.racks.get(oid)
        if rack is None:
            return None
        inside = [d for d in e.site_devices(site_id) if d["rack_id"] == oid]
        room = e.locations.get(rack.get("location_id") or 0, {}).get("name")
        bits = []
        if room:
            bits.append(f"in {room}" + (f", floor tile {rack['iso_x']},{rack['iso_y']}" if rack.get("iso_x") is not None and rack.get("iso_y") is not None else ""))
        if inside:
            bits.append(f"{sum(1 for d in inside if d['position'] is not None)} of {len(inside)} devices in a unit")
            kit = [d for d in inside if not is_passive(d)]
            if kit:
                bits.append(f"{sum(1 for d in kit if d['serial'])} of {len(kit)} serial numbers")
        return f"Rack {rack['name']}: " + ", ".join(bits) + "." if bits else None
    if kind == "room_link":
        loc = e.locations.get(oid)
        if loc is None:
            return None
        for d in e.site_devices(site_id):
            if d["location_id"] != oid:
                continue
            for p in e.device_ports(d["id"], ("interface",)):
                cable = e.cables.get(p["cable_id"] or 0)
                for key in (list(cable["a"]) + list(cable["b"])) if cable else []:
                    far = e.ports.get(tuple(key))  # type: ignore[arg-type]
                    other = e.devices.get(far["device_id"]) if far else None
                    if other is not None and other["location_id"] not in (None, oid):
                        room = e.locations.get(other["location_id"], {}).get("name", "another room")
                        return f"{loc['name']} to {room}: {nm(d)} {p['name']} -> {nm(other)} {far['name']}."  # type: ignore[index]
        return None
    return None
