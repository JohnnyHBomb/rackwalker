"""The scanner: what an estate does not say yet, as findings (the whole picture) and gaps (single empty fields).

Pure functions of an `Estate` (estate.py); docs/quests.md describes every rule for a NetBox admin.

A FINDING is a statement about the estate that needs more than one field to see: a device nothing is connected to, a
room no cable leaves, a site with no way out although a provider is on record. Each has a stable id
(`<kind>:<object type>:<object id>`), the objects that show it (`evidence`) and a value rank:

    connectivity  can it be reached, and how (cables, uplinks, circuits, addresses, power)
    identity      which box is it (serial, asset tag, platform, where the site is)
    layout        where it stands (rack units, floor plan)

A GAP is one empty field on one object, id `<object type>:<object id>:<field>`. Gaps are never offered one at a time:
quests.py bundles them into errands (audit one rack, record one site).

Every rule also counts a CHECK per object it looked at, passed or not, so a site's completeness is "checks passed /
checks made" and reaches 100 % only when nothing is open.
"""
from __future__ import annotations

import ipaddress
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any

import zlib

from . import setup
from .estate import Estate, is_passive, is_physical, is_router
from .estate import name_of as nm

RANKS = ("connectivity", "identity", "layout")
# Devices in these states are expected to be connected, powered and reachable.
LIVE = ("active", "staged")
# Order of finding kinds inside one rank: the most valuable unknown first.
# The setup lines (setup.py) come first: a site with nothing in it has nothing else to ask about.
KIND_ORDER = ["estate_no_site", "site_no_rack", "site_no_devices", "site_no_provider",
              "site_no_way_out", "sites_not_linked", "locations_not_linked", "site_island", "switch_no_uplink",
              "circuit_unterminated", "provider_unused", "circuit_handoff", "circuit_record", "device_unconnected", "router_no_address",
              "interface_no_cable", "device_no_power", "device_unreachable", "address_no_prefix", "device_type_unknown", "rack_audit", "shelf_audit",
              "module_bays_unrecorded", "platform_survey", "site_record", "rack_devices_unpositioned", "room_layout"]
KIND_RANK = {**{k: "connectivity" for k in KIND_ORDER[:19]}, "device_type_unknown": "identity", "rack_audit": "identity", "shelf_audit": "identity",
             "module_bays_unrecorded": "identity",
             "platform_survey": "identity", "site_record": "identity", "rack_devices_unpositioned": "layout", "room_layout": "layout"}
# At most this many devices of one model are covered by one platform line.
MAX_SURVEY = 20
# At most this many expected-cable interface gaps are listed per site, and per device (the total is still counted).
MAX_INTERFACE_GAPS = 50
MAX_INTERFACE_GAPS_PER_DEVICE = 3


def ref(kind: str, oid: int, name: str | None) -> dict[str, Any]:
    return {"kind": kind, "id": oid, "name": name or f"{kind} {oid}"}


@dataclass
class Analysis:
    findings: list[dict[str, Any]] = field(default_factory=list)
    gaps: list[dict[str, Any]] = field(default_factory=list)
    # site id -> {check id: passed}; estate-wide checks sit under the site they are reported at
    checks: dict[int, dict[str, bool]] = field(default_factory=lambda: defaultdict(dict))
    # per site: interface gaps found beyond MAX_INTERFACE_GAPS (counted, not listed)
    unlisted: dict[int, int] = field(default_factory=lambda: defaultdict(int))
    # checks of the setup lines that passed: made (so a line about them can be confirmed), but not counted into a
    # site's completeness, which would otherwise rise for merely having a rack
    quiet: set[str] = field(default_factory=set)
    _flat: dict[str, bool] | None = field(default=None, repr=False, compare=False)

    def check(self, cid: str, site_id: int | None = None) -> bool | None:
        """What the analysis says about one check: True (passed), False (open) or None (it made no such check: the
        object is gone, is no longer of that kind, or could not be read). Looked up at `site_id` first, then anywhere
        (a device that moved to another site takes its checks with it)."""
        here = self.checks.get(site_id, {}) if site_id is not None else {}
        if cid in here:
            return here[cid]
        if self._flat is None:
            self._flat = {c: ok for checks in self.checks.values() for c, ok in checks.items()}
        return self._flat.get(cid)

    def site_findings(self, site_id: int) -> list[dict[str, Any]]:
        return [f for f in self.findings if f["site_id"] == site_id]

    def site_gaps(self, site_id: int) -> list[dict[str, Any]]:
        return [g for g in self.gaps if g["site_id"] == site_id]

    def completeness(self, site_id: int, resolved: frozenset[str] | set[str] = frozenset()) -> dict[str, int]:
        """Checks passed out of checks made at a site. `resolved` are finding ids the player has answered as "that is
        how it is" (no link on purpose): they count as passed for that player."""
        checks = {cid: ok for cid, ok in self.checks.get(site_id, {}).items() if cid not in self.quiet}
        total = len(checks)
        passed = sum(1 for cid, ok in checks.items() if ok or cid in resolved)
        percent = 100 if total == 0 else (100 * passed) // total
        return {"percent": percent, "total": total, "open": total - passed}


def expects_cable(port: dict[str, Any], has_address: bool, carries_management: bool = False) -> bool:
    """A port NetBox already says is in use: enabled, physical, and carrying a description or an address. An address
    on a management-only port does not count, and neither does the management address (the primary IP) of a device
    recorded as locally managed, on whatever port carries it (`carries_management`): it says how the device is reached,
    not that a lead in that port goes anywhere NetBox should know (management networks are often left out of NetBox on
    purpose), and the line "how is it managed" has just put that address there. A port someone described is still
    expected to have a cable."""
    if not (port["enabled"] and is_physical(port)):
        return False
    return bool(port["description"].strip()) or (has_address and not port["mgmt_only"] and not carries_management)


# The facts of a circuit's administrative record, in the order they are asked for. Each is one check
# (`circuit:<id>:<fact>`); none of them is known to everybody, so "I don't know yet" on one leaves the rest writable.
CIRCUIT_FACTS = ("network", "account", "contact_support", "contact_account", "contact_billing", "install_date", "commit_rate", "mrc", "nrc")
FACT_WORDS = {"network": "the provider's network it lands on", "account": "the account with the provider",
              "contact_support": "who to call when it is down", "contact_account": "who the account manager is", "contact_billing": "who sends the bill",
              "install_date": "the install date", "commit_rate": "the commit rate", "mrc": "the monthly charge", "nrc": "the one-off charge"}
# The same facts in a few words, for a list of what is still to find out.
FACT_SHORT = {"network": "the provider's network", "account": "the account", "contact_support": "the support contact",
              "contact_account": "the account manager", "contact_billing": "the billing contact", "install_date": "the install date",
              "commit_rate": "the commit rate", "mrc": "the monthly charge", "nrc": "the one-off charge"}
# The three contacts a circuit's record asks for, each its own fact (support on record says nothing about who sends the
# bill), and the words that find each among NetBox's own contact roles.
CONTACT_ROLES = (("support", "Support or NOC (who to call when it is down)", ("support", "noc", "operations", "technical", "fault", "faults", "helpdesk")),
                 ("account", "Account manager", ("account", "sales", "commercial")),
                 ("billing", "Billing", ("billing", "finance", "invoice", "invoices", "invoicing", "payable", "accounting", "accounts")))


def contact_roles(e: Estate) -> dict[str, dict[str, Any]]:
    """NetBox's own contact roles that stand for support, the account manager and billing: by a whole word of their
    name or slug, never by a part of one ("Accounting" is not the account manager; it is billing). "Billing" is looked
    for before "account", so "Accounts payable" is billing."""
    import re

    got = e._memo.get("contact_roles")
    if got is not None:
        return got
    out: dict[str, dict[str, Any]] = {}
    for key, _, words in sorted(CONTACT_ROLES, key=lambda r: r[0] != "billing"):
        taken = {r["id"] for r in out.values()}
        hit = next((r for _, r in sorted(e.contact_roles.items()) if r["id"] not in taken
                    and set(words) & set(re.split(r"[^a-z0-9]+", f"{r['name']} {r.get('slug') or ''}".lower()))), None)
        if hit is not None:
            out[key] = hit
    e._memo["contact_roles"] = out
    return out


def circuit_facts(e: Estate, circuit_id: int) -> dict[str, bool]:
    """Which facts of a circuit's administrative record NetBox holds. A fact NetBox cannot hold (the custom field for
    it was never created; both ends of the circuit are at sites, so no provider network is its far end) is left out."""
    c = e.circuits[circuit_id]
    terms = e._cterms_of(circuit_id)
    out: dict[str, bool] = {}
    on_network = any(t.get("network_id") is not None for t in terms)
    if on_network or len(terms) < 2:
        out["network"] = on_network
    if e.can_see("accounts"):
        out["account"] = c.get("account_id") is not None
    if e.can_see("contacts", "contact_roles", "assignments"):
        roles = contact_roles(e)
        held = {a["role_id"] for a in e.contacts_of(circuit_id)}
        for key, _, _ in CONTACT_ROLES:
            if key in roles:   # a contact is recorded with its role: with no such role in NetBox there is nothing to ask
                out[f"contact_{key}"] = roles[key]["id"] in held
    out["install_date"] = bool(c.get("install_date"))
    out["commit_rate"] = c.get("commit_rate") is not None
    for fact in ("mrc", "nrc"):
        if e.has_field(f"rw_{fact}") and e.has_field("rw_currency"):
            out[fact] = c.get(fact) is not None
    return out


def _sort_key(f: dict[str, Any]) -> tuple[Any, ...]:
    return (RANKS.index(f["rank"]), KIND_ORDER.index(f["kind"]), f["site_id"] or 0, f["id"])


def analyse(e: Estate) -> Analysis:
    """The analysis of an estate, made once per estate (it is kept with the estate until the estate changes)."""
    a = e._memo.get("analysis")
    if a is None:
        a = e._memo["analysis"] = _analyse(e)
    return a


def _analyse(e: Estate) -> Analysis:
    a = Analysis()
    comp = e.components()
    by_site = e._devices_by_site()
    has_ifaces = {p["device_id"] for p in e.ports.values() if p["kind"] == "interface" and is_physical(p)}
    iface_ips: dict[int, int] = defaultdict(int)
    for ip in e.ips.values():
        if ip["interface_id"] is not None:
            iface_ips[ip["interface_id"]] += 1
    dev_of_iface = {p["id"]: p["device_id"] for p in e.ports.values() if p["kind"] == "interface"}
    dev_ips: dict[int, int] = defaultdict(int)
    for iid, n in iface_ips.items():
        if iid in dev_of_iface:
            dev_ips[dev_of_iface[iid]] += n

    uses_tags = any(d["asset_tag"] for d in e.devices.values())
    # A virtual chassis (a switch stack) is one switch: its members' cables, uplink and management address are the
    # stack's. The member with the lowest id speaks for it in the device rules.
    stacks: dict[int, list[int]] = defaultdict(list)
    for did, d in sorted(e.devices.items()):
        if d.get("vc_id") is not None:
            stacks[d["vc_id"]].append(did)

    def stack(d: dict[str, Any]) -> list[int]:
        return stacks[d["vc_id"]] if d.get("vc_id") is not None else [d["id"]]

    def speaker(d: dict[str, Any]) -> int:
        """The member that speaks for a stack: its first live one (a stack whose first member is switched off is still a switch)."""
        members = stack(d)
        return next((i for i in members if e.devices[i]["status"] in LIVE), members[0])

    def degree(d: dict[str, Any]) -> int:
        return sum(comp.degree(i) for i in stack(d))

    def add(kind: str, obj: tuple[str, int], site_id: int, title: str, evidence: list[dict[str, Any]], **data: Any) -> None:
        fid = f"{kind}:{obj[0]}:{obj[1]}"
        a.findings.append({"id": fid, "kind": kind, "rank": KIND_RANK[kind], "site_id": site_id, "title": title,
                           "evidence": evidence, "data": data})
        a.checks[site_id][fid] = False

    def passed(kind: str, obj: tuple[str, int], site_id: int) -> None:
        a.checks[site_id].setdefault(f"{kind}:{obj[0]}:{obj[1]}", True)

    def dref(d: dict[str, Any]) -> dict[str, Any]:
        return ref("device", d["id"], d["name"])

    can_graph = e.can_see("devices", "ports", "cables", "cterms", "circuits")
    live_net: dict[int, list[dict[str, Any]]] = {s: [d for d in ds if d["status"] in LIVE and e.is_network(d) and d["id"] in has_ifaces]
                                                 for s, ds in by_site.items()}
    # A circuit termination "lands" when it is cabled to something. A circuit with an end recorded at a site and no
    # cable on that end is unfinished: it carries nobody anywhere yet, whatever its other end is plugged into.
    landed: dict[int, list[dict[str, Any]]] = defaultdict(list)
    loose: set[int] = set()
    waiting: dict[int, list[dict[str, Any]]] = defaultdict(list)   # per site: circuit ends recorded there with no cable
    at_sites: dict[int, set[int]] = defaultdict(set)               # per circuit: the estate's own sites it has an end at
    for _, t in sorted(e.cterms.items()):
        if not e.live_circuit(t["circuit_id"]):
            continue   # a decommissioned circuit carries nothing and is asked nothing about
        if t["site_id"] in e.sites:
            at_sites[t["circuit_id"]].add(t["site_id"])
        if t["cable_id"] is not None:
            landed[t["circuit_id"]].append(t)
        elif t["site_id"] is not None:
            loose.add(t["circuit_id"])
            waiting[t["site_id"]].append(t)
    # A circuit with ends at two of the estate's own sites is a private line between them: a link, not a way out.
    between = {c for c, sites in at_sites.items() if len(sites) > 1}
    # Sites such a line is meant to join while one of its ends has no cable: "where it lands" asks that, once.
    half_joined = {s for c in between & loose for s in at_sites[c]}
    out_roots = {comp.of_circuit(c) for c in landed if c not in loose and c not in between}
    site_roots: dict[int, set[Any]] = {s: {comp.of_device(d["id"]) for d in ds} for s, ds in live_net.items()}
    sites_at_root: dict[Any, set[int]] = defaultdict(set)
    for s, rs in site_roots.items():
        for r in rs:
            sites_at_root[r].add(s)

    def way_out(site_id: int) -> bool:
        """A device of the site shares a component with a circuit to the outside that lands and has no end left loose.
        A private line between two of the estate's own sites says nothing about how either reaches the outside world."""
        return not out_roots.isdisjoint(site_roots.get(site_id, ()))

    all_net_sites = [s for s in sorted(e.sites) if live_net.get(s)]
    for site_id in sorted(e.sites):
        site = e.sites[site_id]
        devs = by_site.get(site_id, [])
        net = live_net.get(site_id, [])
        sref = ref("site", site_id, site["name"])
        if can_graph:
            # ---- devices nothing is connected to; switches with no uplink
            for d in devs:
                if d["status"] not in LIVE or is_passive(d) or d["id"] not in has_ifaces or speaker(d) != d["id"]:
                    continue
                n_ports = sum(1 for p in e.device_ports(d["id"], ("interface",)) if is_physical(p))
                if degree(d) == 0:
                    add("device_unconnected", ("device", d["id"]), site_id,
                        f"{_cap(nm(d))} has {n_ports} ports and nothing recorded on any of them." if n_ports != 1 else
                        f"{_cap(nm(d))} has 1 port and nothing recorded on it.",
                        [dref(d)], device_id=d["id"], ports=n_ports)
                    continue
                passed("device_unconnected", ("device", d["id"]), site_id)
                if e.is_switch(d):
                    others = [o for o in comp.devices_in(comp.of_device(d["id"])) if o not in stack(d) and e.is_network(e.devices[o])]
                    if not others and not comp.circuits_in(comp.of_device(d["id"])):
                        add("switch_no_uplink", ("device", d["id"]), site_id,
                            f"{_cap(nm(d))} has devices plugged in, but no uplink to another switch, a router or a circuit.",
                            [dref(d)], device_id=d["id"])
                    else:
                        passed("switch_no_uplink", ("device", d["id"]), site_id)
            # ---- rooms with no link between them; islands inside the site
            by_loc: dict[int, list[dict[str, Any]]] = defaultdict(list)
            for d in net:
                if d["location_id"] is not None:
                    by_loc[d["location_id"]].append(d)
            apart: set[int] = set()   # rooms that locations_not_linked reports as cut off from the main room
            if len(by_loc) >= 2 and e.can_see("locations") and all(loc in e.locations for loc in by_loc):
                group: dict[int, int] = {loc: loc for loc in by_loc}

                def find(x: int) -> int:
                    while group[x] != x:
                        group[x] = group[group[x]]
                        x = group[x]
                    return x
                locs = sorted(by_loc)
                # two rooms are linked when a device of each is in one component: one pass over the devices, not
                # every pair of rooms times every pair of devices
                room_of_root: dict[Any, int] = {}
                for loc in locs:
                    for x in by_loc[loc]:
                        other = room_of_root.setdefault(comp.of_device(x["id"]), loc)
                        if find(other) != find(loc):
                            group[max(find(other), find(loc))] = min(find(other), find(loc))
                groups: dict[int, list[int]] = defaultdict(list)
                for loc in locs:
                    groups[find(loc)].append(loc)
                main = max(groups.values(), key=lambda g: (sum(len(by_loc[x]) for x in g), -g[0]))
                for g in sorted(groups.values()):
                    for loc in g:
                        if g is main:
                            passed("locations_not_linked", ("location", loc), site_id)
                            continue
                        apart.add(loc)
                        add("locations_not_linked", ("location", loc), site_id,
                            f"{e.locations[loc]['name']} and {e.locations[main[0]]['name']} at {site['name']} have no link between them.",
                            [ref("location", loc, e.locations[loc]["name"]), ref("location", main[0], e.locations[main[0]]["name"])]
                            + [dref(d) for d in by_loc[loc]],
                            location_id=loc, main_location_id=main[0], device_ids=[d["id"] for d in by_loc[loc]],
                            main_device_ids=[d["id"] for x in main for d in by_loc[x]])
            connected = [d for d in net if degree(d) > 0]
            roots: dict[Any, list[dict[str, Any]]] = defaultdict(list)
            for d in connected:
                roots[comp.of_device(d["id"])].append(d)
            if roots:
                main_root = max(roots, key=lambda r: (len(roots[r]), -roots[r][0]["id"]))
                for d in roots[main_root]:
                    passed("site_island", ("device", d["id"]), site_id)
                for r, members in sorted(roots.items(), key=lambda kv: kv[1][0]["id"]):
                    if r == main_root:
                        continue
                    first = members[0]
                    if all(d["location_id"] in apart for d in members):
                        continue  # every room of this group is already reported as cut off (locations_not_linked)
                    names = ", ".join(nm(d) for d in members[:3]) + (" and others" if len(members) > 3 else "")
                    # one check per member: the line stays the same line when its first member goes away
                    add("site_island", ("device", first["id"]), site_id,
                        f"{_cap(names)} {'are' if len(members) > 1 else 'is'} cabled, but cut off from the rest of {site['name']}.",
                        [dref(d) for d in members], device_ids=[d["id"] for d in members],
                        main_device_ids=[d["id"] for d in roots[main_root]],
                        gap_ids=[f"site_island:device:{d['id']}" for d in members])
                    for d in members:
                        a.checks[site_id][f"site_island:device:{d['id']}"] = False
            # ---- the way out of the site, and links to the other sites
            if net:
                has_out = way_out(site_id)
                other_sites = [s for s in all_net_sites if s != site_id]
                linked = sorted({s for r in site_roots[site_id] for s in sites_at_root[r] if s != site_id and s in e.sites})
                if waiting.get(site_id):
                    pass   # a circuit end is on record here with no cable: "where it lands" asks this, once
                elif not has_out and not any(way_out(s) for s in linked) and (e.providers or e.circuits):
                    provs = [ref("provider", p["id"], p["name"]) for _, p in sorted(e.providers.items())]
                    add("site_no_way_out", ("site", site_id), site_id,
                        f"Nobody can tell from NetBox how {site['name']} reaches the outside world.", [sref] + provs[:3],
                        provider_ids=sorted(e.providers), device_ids=[d["id"] for d in net])
                elif e.providers or e.circuits:
                    passed("site_no_way_out", ("site", site_id), site_id)
                if other_sites:
                    if site_id in half_joined and not linked:
                        pass   # a line to another site is on record with an end not plugged in: "where it lands" asks this, once
                    elif not linked:
                        add("sites_not_linked", ("site", site_id), site_id,
                            f"{site['name']} has no recorded link to any other site.",
                            [sref] + [ref("site", s, e.sites[s]["name"]) for s in other_sites[:3]],
                            other_site_ids=other_sites, device_ids=[d["id"] for d in net])
                    else:
                        passed("sites_not_linked", ("site", site_id), site_id)
            # ---- power: devices with power ports and no feed, where other devices here have one (bundled per rack)
            if e.can_see("ports", "cables"):
                states = {d["id"]: e.powered(d["id"]) for d in devs if d["status"] in LIVE and not is_passive(d)}
                if any(v is True for v in states.values()):
                    dark: dict[int, list[dict[str, Any]]] = defaultdict(list)
                    for d in devs:
                        a.checks[site_id][f"device:{d['id']}:power"] = states.get(d["id"]) is not False
                        if states.get(d["id"]) is False:
                            dark[d["rack_id"] or 0].append(d)
                    for rid, members in sorted(dark.items()):
                        where = f"rack {e.racks[rid]['name']}" if rid in e.racks else f"no rack at {site['name']}"
                        fid = f"device_no_power:{'rack' if rid else 'site'}:{rid or site_id}"
                        a.findings.append({
                            "id": fid, "kind": "device_no_power", "rank": "connectivity", "site_id": site_id,
                            "title": f"{_cap(_some([nm(d) for d in members]))} in {where} {'have' if len(members) != 1 else 'has'} "
                                     f"no power feed on record, although other devices at {site['name']} do.",
                            "evidence": [dref(d) for d in members],
                            "data": {"rack_id": rid or None, "device_ids": [d["id"] for d in members],
                                     "gap_ids": [f"device:{d['id']}:power" for d in members]}})
            # ---- expected cables: a port NetBox already says is in use (address or description), with no cable
            listed = 0
            for d in devs:
                if d["status"] not in LIVE or d["rack_id"] is None or degree(d) == 0:
                    continue
                on_device = 0
                open_ports: list[dict[str, Any]] = []
                # the port that carries management, on a device recorded as locally managed (on the device itself, or
                # as the default of its model)
                carrier = e.primary_interface(d) if e.mgmt_recorded(d) == "local" else None
                for p in e.device_ports(d["id"], ("interface",)):
                    if not expects_cable(p, bool(iface_ips.get(p["id"])), p["id"] == carrier):
                        continue
                    if p["cable_id"] is not None or p["mark_connected"]:
                        a.checks[site_id][f"interface:{p['id']}:cable"] = True
                        continue
                    a.checks[site_id][f"interface:{p['id']}:cable"] = False
                    open_ports.append(p)
                    if listed >= MAX_INTERFACE_GAPS or on_device >= MAX_INTERFACE_GAPS_PER_DEVICE:
                        a.unlisted[site_id] += 1
                        continue
                    listed += 1
                    on_device += 1
                    why = "an address" if iface_ips.get(p["id"]) else f"a description ({p['description'].strip()[:40]})"
                    a.gaps.append({"id": f"interface:{p['id']}:cable", "kind": "interface", "object_id": p["id"], "field": "cable",
                                   "site_id": site_id, "rank": "connectivity",
                                   "label": f"{nm(d)} {p['name']} has {why} but no cable.", "device_id": d["id"]})
                if open_ports:
                    # one line per device for all of its ports (the gap list above is capped; the line is not)
                    n = len(open_ports)
                    shown = _some([p["name"] for p in open_ports])
                    first = open_ports[0]
                    why = "an address" if iface_ips.get(first["id"]) else f"a description ({first['description'].strip()[:40]})"
                    a.findings.append({
                        "id": f"interface_no_cable:device:{d['id']}", "kind": "interface_no_cable", "rank": "connectivity", "site_id": site_id,
                        "title": (f"{_cap(nm(d))} {first['name']} has {why} but no cable." if n == 1 else
                                  f"{_cap(nm(d))} {shown} are in use by NetBox's own record (a description or an address), but have no cable."),
                        "evidence": [dref(d)] + [ref("interface", p["id"], f"{nm(d)} {p['name']}") for p in open_ports[:5]],
                        "data": {"device_id": d["id"], "interface_ids": [p["id"] for p in open_ports],
                                 "gap_ids": [f"interface:{p['id']}:cable" for p in open_ports]}})
        # ---- addresses: routers with none; devices with no management address; addresses outside every prefix
        if e.can_see("devices", "ports", "ips"):
            for d in devs:
                if d["status"] not in LIVE or is_passive(d) or d["id"] not in has_ifaces:
                    continue
                if is_router(d):
                    if dev_ips.get(d["id"], 0) == 0:
                        add("router_no_address", ("device", d["id"]), site_id,
                            f"{_cap(nm(d))} routes traffic, but no interface of it has an address in NetBox.", [dref(d)], device_id=d["id"])
                        continue
                    passed("router_no_address", ("device", d["id"]), site_id)
                # One check per device: NetBox says how it is managed. A locally managed device needs its management
                # address (primary IP); one that a cloud or controller service manages, or that is not managed at all,
                # has no address to record and is complete as it is. With nothing on record, that is the question.
                mode = e.mgmt_mode(d)
                settled = mode in ("cloud", "unmanaged") or d["primary_ip"] is not None
                if len(stack(d)) > 1:
                    # A stack is managed as one: one check, on the member that speaks for it. What any member says
                    # settles it; "managed locally" with no address anywhere in the stack still needs the address.
                    if speaker(d) != d["id"]:
                        continue
                    members = [e.devices[i] for i in stack(d) if e.devices[i]["status"] in LIVE]
                    modes = [e.mgmt_mode(m) for m in members]
                    settled = any(m in ("cloud", "unmanaged") for m in modes) or any(m["primary_ip"] is not None for m in members)
                    mode = "local" if "local" in modes else None
                gid = f"device:{d['id']}:primary_ip"
                if settled:
                    a.checks[site_id][gid] = True
                    continue
                a.checks[site_id][gid] = False
                label = (f"{_cap(nm(d))} is managed locally, but has no management address (primary IP)." if mode == "local" else
                         f"{_cap(nm(d))} has no management address (primary IP), and NetBox does not say how it is managed.")
                a.gaps.append({"id": gid, "kind": "device", "object_id": d["id"], "field": "primary_ip", "site_id": site_id,
                               "rank": "connectivity", "device_id": d["id"], "label": label})
                a.findings.append({"id": f"device_unreachable:device:{d['id']}", "kind": "device_unreachable", "rank": "connectivity",
                                   "site_id": site_id, "evidence": [dref(d)],
                                   "title": (f"{_cap(nm(d))} is managed locally, but NetBox has no address to log in to it." if mode == "local"
                                             else f"NetBox does not say how {nm(d)} is managed, or how to reach it."),
                                   "data": {"device_id": d["id"], "gap_ids": [gid], "mode": mode}})
            if e.can_see("prefixes"):
                # NetBox keeps one address table per VRF (and the global one): a prefix holds an address only in its own
                nets: dict[Any, list[Any]] = defaultdict(list)
                for p in e.prefixes.values():
                    nets[p.get("vrf_id")].append(ipaddress.ip_network(p["prefix"], strict=False))
                orphans: dict[tuple[int, str], list[dict[str, Any]]] = defaultdict(list)
                site_devs = {d["id"] for d in devs}
                for _, ip in sorted(e.ips.items()):
                    if ip["interface_id"] is None or dev_of_iface.get(ip["interface_id"]) not in site_devs:
                        continue
                    iface = ipaddress.ip_interface(ip["address"])
                    if any(iface.ip in n for n in nets.get(ip.get("vrf_id"), []) if n.version == iface.version):
                        passed("address_no_prefix", ("ip", ip["id"]), site_id)
                    else:
                        # a host address (/32, /128) names no subnet: all of them in one table are one question
                        host = iface.network.prefixlen == iface.max_prefixlen
                        orphans[(ip.get("vrf_id") or 0, f"hosts{iface.version}" if host else str(iface.network))].append(ip)
                for (vrf_id, network), ips in sorted(orphans.items()):
                    first = ips[0]
                    vrf = first.get("vrf") or (f"VRF {vrf_id}" if vrf_id else None)
                    # one check per address: the line stays the same line when its first address goes away
                    add("address_no_prefix", ("ip", first["id"]), site_id,
                        f"{_some([i['address'] for i in ips])} {'is' if len(ips) == 1 else 'are'} in use at {site['name']}, "
                        f"but NetBox has no prefix{f' in VRF {vrf}' if vrf else ''} that holds " + ("it." if len(ips) == 1 else "them."),
                        [ref("ip_address", i["id"], i["address"]) for i in ips[:5]], network=None if network.startswith("hosts") else network,
                        ip_ids=[i["id"] for i in ips], vrf_id=vrf_id or None, vrf=vrf,
                        gap_ids=[f"address_no_prefix:ip:{i['id']}" for i in ips])
                    for i in ips:
                        a.checks[site_id][f"address_no_prefix:ip:{i['id']}"] = False
        # ---- identity gaps, bundled per rack (or per room for what is not in a rack)
        for d in devs:
            for fld, empty, label in (("serial", not d["serial"], "serial number"), ("asset_tag", d["asset_tag"] is None, "asset tag"),
                                      ("platform", d["platform"] is None, "platform")):
                # A field nobody uses is not a gap: asset tags count once one device has one, platforms once NetBox
                # has any (and never for passive kit such as patch panels and power strips).
                if fld == "platform" and (is_passive(d) or not e.platforms):
                    continue
                if fld == "serial" and is_passive(d):
                    continue   # patch panels, shelves and blanking plates often carry no serial at all
                if fld == "asset_tag" and not uses_tags:
                    continue
                gid = f"device:{d['id']}:{fld}"
                a.checks[site_id][gid] = not empty
                if empty:
                    a.gaps.append({"id": gid, "kind": "device", "object_id": d["id"], "field": fld, "site_id": site_id,
                                   "rank": "identity", "label": f"{_cap(nm(d))} has no {label}.", "device_id": d["id"]})
        id_gaps: dict[tuple[str, int], list[dict[str, Any]]] = defaultdict(list)
        by_model: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for g in a.gaps:
            if g["site_id"] == site_id and g["rank"] == "identity" and g["kind"] == "device":
                d = e.devices[g["object_id"]]
                if g["field"] == "platform":
                    # what a model runs is one question for all of them, not a field to read off each label
                    by_model[d["model"] or "unknown model"].append(g)
                else:
                    id_gaps[("rack", d["rack_id"]) if d["rack_id"] else ("location", d["location_id"] or 0)].append(g)
        slugs: dict[str, int] = defaultdict(int)
        for model in by_model:
            slugs[_slug(model)] += 1
        for model, gaps in sorted(by_model.items()):
            # two model names that differ only in punctuation ("EX 4300", "EX-4300") are two lines with two ids
            key = _slug(model) + (f"-{zlib.crc32(model.encode()):08x}" if slugs[_slug(model)] > 1 else "")
            ids = [g["object_id"] for g in gaps][:MAX_SURVEY]
            more = f" ({len(gaps) - len(ids)} more after these)" if len(gaps) > len(ids) else ""
            a.findings.append({"id": f"platform_survey:site:{site_id}:{key}", "kind": "platform_survey", "rank": "identity",
                               "site_id": site_id,
                               "title": f"NetBox does not say what {_some([e.device_name(i) for i in ids])} ({model}) at {site['name']} "
                                        f"run{'s' if len(ids) == 1 else ''}{more}.",
                               "evidence": [ref("device", i, e.device_name(i)) for i in ids],
                               "data": {"model": model, "device_ids": ids, "gap_ids": [f"device:{i}:platform" for i in ids]}})
        for (kind, oid), gaps in sorted(id_gaps.items()):
            dev_ids = sorted({g["object_id"] for g in gaps}, key=lambda i: (-(e.devices[i]["position"] or 0), i))
            lacking = _some([w for fld, w in (("serial", "serial number"), ("asset_tag", "asset tag")) if any(g["field"] == fld for g in gaps)], "or")
            if kind == "rack" and oid not in e.racks:
                continue   # the rack list could not be read (or was cut short): nothing is said about a rack unseen
            if kind == "rack":
                rack = e.racks[oid]
                a.findings.append({"id": f"rack_audit:rack:{oid}", "kind": "rack_audit", "rank": "identity", "site_id": site_id,
                                   "title": f"{_some([e.device_name(i) for i in dev_ids])} in rack {rack['name']} "
                                            f"{'have' if len(dev_ids) != 1 else 'has'} no {lacking} on record.",
                                   "evidence": [ref("rack", oid, rack["name"])] + [ref("device", i, e.device_name(i)) for i in dev_ids],
                                   "data": {"rack_id": oid, "device_ids": dev_ids, "gap_ids": [g["id"] for g in gaps]}})
            else:
                where = e.locations[oid]["name"] if oid in e.locations else site["name"]
                a.findings.append({"id": f"shelf_audit:{'location' if oid else 'site'}:{oid or site_id}", "kind": "shelf_audit",
                                   "rank": "identity", "site_id": site_id,
                                   "title": f"{_some([e.device_name(i) for i in dev_ids])}, outside any rack in {where}, "
                                            f"{'have' if len(dev_ids) != 1 else 'has'} no {lacking} on record.",
                                   "evidence": [ref("device", i, e.device_name(i)) for i in dev_ids],
                                   "data": {"location_id": oid or None, "device_ids": dev_ids, "gap_ids": [g["id"] for g in gaps]}})
        site_gaps = []
        for fld, empty, label in (("gps", site["latitude"] is None or site["longitude"] is None, "GPS position"),
                                  ("facility", not site["facility"], "facility name"), ("country", not site["country"], "country")):
            gid = f"site:{site_id}:{fld}"
            a.checks[site_id][gid] = not empty
            if empty:
                g = {"id": gid, "kind": "site", "object_id": site_id, "field": fld, "site_id": site_id, "rank": "identity",
                     "label": f"{site['name']} has no {label}."}
                a.gaps.append(g)
                site_gaps.append(g)
        if site_gaps:
            missing = [g["field"] for g in site_gaps]
            cannot = _some((["put on a map"] if "gps" in missing or "country" in missing else [])
                           + (["named to a carrier"] if "facility" in missing else []), "or")
            words = {"gps": "GPS position", "facility": "facility name", "country": "country"}
            a.findings.append({"id": f"site_record:site:{site_id}", "kind": "site_record", "rank": "identity", "site_id": site_id,
                               "title": f"{site['name']} has no {_some([words[m] for m in missing], 'or')} on record, so it cannot be {cannot}.",
                               "evidence": [sref], "data": {"gap_ids": [g["id"] for g in site_gaps], "fields": [g["field"] for g in site_gaps]}})
        # ---- layout: devices in a rack with no unit; racks with no room or no place on the floor plan
        for rid, rack in sorted(e.racks.items()):
            if rack["site_id"] != site_id:
                continue
            loose = [d for d in devs if d["rack_id"] == rid and d["position"] is None and d["u_height"] > 0]
            if loose:
                add("rack_devices_unpositioned", ("rack", rid), site_id,
                    f"{_cap(_some([nm(d) for d in loose]))} in rack {rack['name']} {'have' if len(loose) != 1 else 'has'} no rack unit on record.",
                    [ref("rack", rid, rack["name"])] + [dref(d) for d in loose], rack_id=rid, device_ids=[d["id"] for d in loose])
            elif any(d["rack_id"] == rid for d in devs):
                passed("rack_devices_unpositioned", ("rack", rid), site_id)
            # a site that records no rooms at all is not asked which room a rack is in
            has_rooms = e.can_see("locations") and any(loc["site_id"] == site_id for loc in e.locations.values())
            for fld, empty, label in (("location", rack["location_id"] is None, "room"),
                                      ("iso", rack["iso_x"] is None or rack["iso_y"] is None, "place on the floor plan (iso_x, iso_y)")):
                if fld == "location" and not has_rooms:
                    continue
                gid = f"rack:{rid}:{fld}"
                a.checks[site_id][gid] = not empty
                if empty:
                    a.gaps.append({"id": gid, "kind": "rack", "object_id": rid, "field": fld, "site_id": site_id, "rank": "layout",
                                   "label": f"Rack {rack['name']} has no {label}."})
        lay: dict[int, list[dict[str, Any]]] = defaultdict(list)
        for g in a.gaps:
            if g["site_id"] == site_id and g["kind"] == "rack":
                lay[e.racks[g["object_id"]]["location_id"] or 0].append(g)
        for loc, gaps in sorted(lay.items()):
            rack_ids = sorted({g["object_id"] for g in gaps})
            where = e.locations[loc]["name"] if loc in e.locations else site["name"]
            a.findings.append({"id": f"room_layout:{'location' if loc else 'site'}:{loc or site_id}", "kind": "room_layout", "rank": "layout",
                               "site_id": site_id,
                               "title": f"Rack{'s' if len(rack_ids) != 1 else ''} {_some([e.racks[r]['name'] for r in rack_ids])} in {where} "
                                        f"{'have' if len(rack_ids) != 1 else 'has'} "
                                        f"{_some([w for fld, w in (('location', 'no room'), ('iso', 'no place on the floor plan')) if any(g['field'] == fld for g in gaps)], 'or')} on record.",
                               "evidence": [ref("rack", r, e.racks[r]["name"]) for r in rack_ids],
                               "data": {"location_id": loc or None, "rack_ids": rack_ids, "gap_ids": [g["id"] for g in gaps]}})

    # ---- circuits that end nowhere; providers with nothing on record (estate-wide, reported at one site)
    if can_graph and e.sites:
        home = min(e.sites)
        claimed = {p for f in a.findings if f["kind"] == "site_no_way_out" for p in f["data"]["provider_ids"]}
        for cid, c in sorted(e.circuits.items()):
            if not e.live_circuit(cid):
                continue
            terms = sorted(e._cterms_of(cid), key=lambda t: t["side"] or "")
            # an end recorded at a site with no cable on it ends nowhere, even when the other end is cabled
            loose_end = next((t for t in terms if t["site_id"] in e.sites and t["cable_id"] is None), None)
            site_id = loose_end["site_id"] if loose_end else next((t["site_id"] for t in terms if t["site_id"] in e.sites), home)
            prov = e.providers.get(c["provider_id"] or 0, {}).get("name", "its provider")
            hand = e.circuit_handoff(cid)
            if landed.get(cid) and loose_end is None and hand is not None and hand[1]["kind"] != "interface" and hand[2]["site_id"] in e.sites:
                # Cabled, but only as far as a patch panel: the line is not plugged into any device yet. The line asks
                # which device it goes on to, from the panel port where the path stops.
                _, panel_port, panel = hand
                how, stop = e.through((panel_port["kind"], panel_port["id"]))
                add("circuit_unterminated", ("circuit", cid), panel["site_id"],
                    f"Circuit {c['cid']} from {prov} is patched to {nm(panel)} {panel_port['name']}, but NetBox does not say which device it goes on to.",
                    [ref("circuit", cid, c["cid"]), dref(panel)], circuit_id=cid, provider_id=c["provider_id"], panel_id=panel["id"],
                    from_port=[stop["kind"], stop["id"]] if how == "open" and stop is not None else None)
                continue
            if landed.get(cid) and loose_end is None:
                passed("circuit_unterminated", ("circuit", cid), site_id)
                continue
            plugged = next((t["site_id"] for t in landed.get(cid, []) if t["site_id"] in e.sites and t["site_id"] != site_id), None)
            add("circuit_unterminated", ("circuit", cid), site_id,
                (f"Circuit {c['cid']} from {prov} is plugged in at {e.sites[plugged]['name']}, but its end at {e.sites[site_id]['name']} has no "
                 "cable: NetBox does not say which device it plugs into there.") if plugged is not None and loose_end is not None else
                f"Circuit {c['cid']} from {prov} is on record, but NetBox does not say which device it plugs into.",
                [ref("circuit", cid, c["cid"])], circuit_id=cid, provider_id=c["provider_id"])
        # ---- a circuit that is plugged in: the address on your side of it (logical), then its record (administrative)
        for cid, c in sorted(e.circuits.items()):
            hand = e.circuit_handoff(cid) if e.live_circuit(cid) else None
            if hand is None or hand[1]["kind"] != "interface":
                continue   # not plugged into a device (yet): there is no port of yours to ask an address for
            term, port, dev = hand
            site_id = dev["site_id"]
            if site_id not in e.sites:
                continue
            prov = e.providers.get(c["provider_id"] or 0, {}).get("name", "its provider")
            cref = ref("circuit", cid, c["cid"])
            where = f"{nm(dev)} {port['name']}"
            if e.can_see("ips"):
                if iface_ips.get(port["id"]):
                    passed("circuit_handoff", ("circuit", cid), site_id)
                else:
                    add("circuit_handoff", ("circuit", cid), site_id,
                        f"Circuit {c['cid']} from {prov} plugs into {where}, but NetBox has no address on that port.",
                        [cref, dref(dev)], circuit_id=cid, device_id=dev["id"], interface_id=port["id"], provider_id=c["provider_id"])
            facts = circuit_facts(e, cid)
            if facts:
                for fact, ok in facts.items():
                    a.checks[site_id][f"circuit:{cid}:{fact}"] = ok
                missing = [fact for fact, ok in facts.items() if not ok]
                if missing:
                    # The line stands for the facts NetBox lacks, and only those: what NetBox held all along is no
                    # part of the errand, so it is never confirmed (or paid) under it.
                    a.findings.append({
                        "id": f"circuit_record:circuit:{cid}", "kind": "circuit_record", "rank": "connectivity", "site_id": site_id,
                        "title": f"Circuit {c['cid']} from {prov} is plugged in at {e.sites[site_id]['name']}, but NetBox does not hold "
                                 f"{_all([FACT_WORDS[m] for m in missing], 'or')}.",
                        "evidence": [cref, ref("provider", c["provider_id"] or 0, prov)],
                        "data": {"circuit_id": cid, "provider_id": c["provider_id"], "device_id": dev["id"], "facts": list(facts),
                                 "missing": missing, "gap_ids": [f"circuit:{cid}:{fact}" for fact in missing]}})
        for pid, p in sorted(e.providers.items()):
            if any(c["provider_id"] == pid for c in e.circuits.values()):
                passed("provider_unused", ("provider", pid), home)
            elif pid not in claimed:
                add("provider_unused", ("provider", pid), home,
                    f"{p['name']} is listed as a provider, but no circuit of theirs is on record.", [ref("provider", pid, p["name"])],
                    provider_id=pid)

    def quiet(kind: str, obj: tuple[str, int], site_id: int) -> None:
        cid = f"{kind}:{obj[0]}:{obj[1]}"
        if a.checks[site_id].setdefault(cid, True):
            a.quiet.add(cid)
    setup.findings(e, a, add, quiet, LIVE)
    a.findings.sort(key=_sort_key)
    a.gaps.sort(key=lambda g: (g["site_id"], RANKS.index(g["rank"]), g["kind"], g["object_id"], g["field"]))
    return a


def _some(names: list[str], joint: str = "and") -> str:
    names = [n for n in names if n]
    if len(names) <= 3:
        return ", ".join(names[:-1]) + (f" {joint} " if len(names) > 1 else "") + (names[-1] if names else "")
    return f"{names[0]}, {names[1]} and {len(names) - 2} more"


def _all(names: list[str], joint: str = "and") -> str:
    """Every name, however many: a sentence that says what is missing names all of it."""
    names = [n for n in names if n]
    return ", ".join(names[:-1]) + (f" {joint} " if len(names) > 1 else "") + (names[-1] if names else "")


def _cap(text: str) -> str:
    """A sentence may start with an unnamed device ("The unnamed EX-A2400 ..."); a real name keeps its own case."""
    return "The" + text[3:] if text.startswith("the unnamed") else text


def _slug(text: str) -> str:
    import re

    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-") or "model"
