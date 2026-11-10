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

from .estate import Estate, is_network, is_passive, is_physical, is_router, is_switch

RANKS = ("connectivity", "identity", "layout")
# Devices in these states are expected to be connected, powered and reachable.
LIVE = ("active", "staged")
# Order of finding kinds inside one rank: the most valuable unknown first.
KIND_ORDER = ["site_no_way_out", "sites_not_linked", "locations_not_linked", "site_island", "switch_no_uplink",
              "circuit_unterminated", "provider_unused", "device_unconnected", "router_no_address", "interface_no_cable",
              "device_no_power", "device_unreachable", "address_no_prefix", "rack_audit", "shelf_audit", "platform_survey",
              "site_record", "rack_devices_unpositioned", "room_layout"]
KIND_RANK = {**{k: "connectivity" for k in KIND_ORDER[:13]}, "rack_audit": "identity", "shelf_audit": "identity",
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

    def site_findings(self, site_id: int) -> list[dict[str, Any]]:
        return [f for f in self.findings if f["site_id"] == site_id]

    def site_gaps(self, site_id: int) -> list[dict[str, Any]]:
        return [g for g in self.gaps if g["site_id"] == site_id]

    def completeness(self, site_id: int, resolved: frozenset[str] | set[str] = frozenset()) -> dict[str, int]:
        """Checks passed out of checks made at a site. `resolved` are finding ids the player has answered as "that is
        how it is" (no link on purpose): they count as passed for that player."""
        checks = self.checks.get(site_id, {})
        total = len(checks)
        passed = sum(1 for cid, ok in checks.items() if ok or cid in resolved)
        percent = 100 if total == 0 else (100 * passed) // total
        return {"percent": percent, "total": total, "open": total - passed}


def expects_cable(port: dict[str, Any], has_address: bool) -> bool:
    """A port NetBox already says is in use: enabled, physical, and carrying a description or an address. An address
    on a management-only port does not count: it says how the device is reached, not that a lead in that port goes
    anywhere NetBox should know (management networks are often left out of NetBox on purpose), and the line "how do you
    reach it" has just put that address there. A management port someone described is still expected to have a cable."""
    if not (port["enabled"] and is_physical(port)):
        return False
    return bool(port["description"].strip()) or (has_address and not port["mgmt_only"])


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
    live_net: dict[int, list[dict[str, Any]]] = {s: [d for d in ds if d["status"] in LIVE and is_network(d) and d["id"] in has_ifaces]
                                                 for s, ds in by_site.items()}
    # A circuit termination "lands" when it is cabled to something.
    landed: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for t in e.cterms.values():
        if t["cable_id"] is not None:
            landed[t["circuit_id"]].append(t)

    def way_out(site_id: int) -> bool:
        """A device of the site shares a component with a circuit that lands somewhere."""
        roots = {comp.of_device(d["id"]) for d in live_net.get(site_id, [])}
        return any(comp.of_circuit(c) in roots for c in landed)

    for site_id in sorted(e.sites):
        site = e.sites[site_id]
        devs = by_site.get(site_id, [])
        net = live_net.get(site_id, [])
        sref = ref("site", site_id, site["name"])
        if can_graph:
            # ---- devices nothing is connected to; switches with no uplink
            for d in devs:
                if d["status"] not in LIVE or is_passive(d) or d["id"] not in has_ifaces:
                    continue
                n_ports = sum(1 for p in e.device_ports(d["id"], ("interface",)) if is_physical(p))
                if comp.degree(d["id"]) == 0:
                    add("device_unconnected", ("device", d["id"]), site_id,
                        f"{d['name'] or 'A device'} has {n_ports} port{'s' if n_ports != 1 else ''} and nothing recorded on any of them.",
                        [dref(d)], device_id=d["id"], ports=n_ports)
                    continue
                passed("device_unconnected", ("device", d["id"]), site_id)
                if is_switch(d):
                    others = [o for o in comp.devices_in(comp.of_device(d["id"])) if o != d["id"] and is_network(e.devices[o])]
                    if not others and not comp.circuits_in(comp.of_device(d["id"])):
                        add("switch_no_uplink", ("device", d["id"]), site_id,
                            f"{d['name']} has devices plugged in, but no uplink to another switch, a router or a circuit.",
                            [dref(d)], device_id=d["id"])
                    else:
                        passed("switch_no_uplink", ("device", d["id"]), site_id)
            # ---- rooms with no link between them; islands inside the site
            by_loc: dict[int, list[dict[str, Any]]] = defaultdict(list)
            for d in net:
                if d["location_id"] is not None:
                    by_loc[d["location_id"]].append(d)
            if len(by_loc) >= 2:
                group: dict[int, int] = {loc: loc for loc in by_loc}

                def find(x: int) -> int:
                    while group[x] != x:
                        x = group[x]
                    return x
                locs = sorted(by_loc)
                for i, la in enumerate(locs):
                    for lb in locs[i + 1:]:
                        if any(comp.same(x["id"], y["id"]) for x in by_loc[la] for y in by_loc[lb]):
                            group[max(find(la), find(lb))] = min(find(la), find(lb))
                groups: dict[int, list[int]] = defaultdict(list)
                for loc in locs:
                    groups[find(loc)].append(loc)
                main = max(groups.values(), key=lambda g: (sum(len(by_loc[x]) for x in g), -g[0]))
                for g in sorted(groups.values()):
                    for loc in g:
                        if g is main:
                            passed("locations_not_linked", ("location", loc), site_id)
                            continue
                        add("locations_not_linked", ("location", loc), site_id,
                            f"{e.locations[loc]['name']} and {e.locations[main[0]]['name']} at {site['name']} have no link between them.",
                            [ref("location", loc, e.locations[loc]["name"]), ref("location", main[0], e.locations[main[0]]["name"])]
                            + [dref(d) for d in by_loc[loc]],
                            location_id=loc, main_location_id=main[0], device_ids=[d["id"] for d in by_loc[loc]],
                            main_device_ids=[d["id"] for x in main for d in by_loc[x]])
            connected = [d for d in net if comp.degree(d["id"]) > 0]
            roots: dict[Any, list[dict[str, Any]]] = defaultdict(list)
            for d in connected:
                roots[comp.of_device(d["id"])].append(d)
            if len(roots) >= 2:
                main_root = max(roots, key=lambda r: (len(roots[r]), -roots[r][0]["id"]))
                main_locs = {d["location_id"] for d in roots[main_root]}
                for r, members in sorted(roots.items(), key=lambda kv: kv[1][0]["id"]):
                    if r == main_root:
                        continue
                    first = members[0]
                    if not ({d["location_id"] for d in members} & main_locs):
                        continue  # a room of its own: said by locations_not_linked
                    names = ", ".join(d["name"] or "?" for d in members[:3]) + (" and others" if len(members) > 3 else "")
                    add("site_island", ("device", first["id"]), site_id,
                        f"{names} {'are' if len(members) > 1 else 'is'} cabled, but cut off from the rest of {site['name']}.",
                        [dref(d) for d in members], device_ids=[d["id"] for d in members],
                        main_device_ids=[d["id"] for d in roots[main_root]])
            # ---- the way out of the site, and links to the other sites
            if net:
                has_out = way_out(site_id)
                other_sites = [s for s in sorted(e.sites) if s != site_id and live_net.get(s)]
                my_roots = {comp.of_device(d["id"]) for d in net}
                linked = [s for s in other_sites if any(comp.of_device(d["id"]) in my_roots for d in live_net[s])]
                if not has_out and not any(way_out(s) for s in linked) and (e.providers or e.circuits):
                    provs = [ref("provider", p["id"], p["name"]) for _, p in sorted(e.providers.items())]
                    add("site_no_way_out", ("site", site_id), site_id,
                        f"Nobody can tell from NetBox how {site['name']} reaches the outside world.", [sref] + provs[:3],
                        provider_ids=sorted(e.providers), device_ids=[d["id"] for d in net])
                elif e.providers or e.circuits:
                    passed("site_no_way_out", ("site", site_id), site_id)
                if other_sites:
                    if not linked:
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
                            "title": f"{_some([d['name'] or '?' for d in members])} in {where} {'have' if len(members) != 1 else 'has'} "
                                     f"no power feed on record, although other devices at {site['name']} do.",
                            "evidence": [dref(d) for d in members],
                            "data": {"rack_id": rid or None, "device_ids": [d["id"] for d in members],
                                     "gap_ids": [f"device:{d['id']}:power" for d in members]}})
            # ---- expected cables: a port NetBox already says is in use (address or description), with no cable
            listed = 0
            for d in devs:
                if d["status"] not in LIVE or d["rack_id"] is None or comp.degree(d["id"]) == 0:
                    continue
                on_device = 0
                for p in e.device_ports(d["id"], ("interface",)):
                    if not expects_cable(p, bool(iface_ips.get(p["id"]))):
                        continue
                    if p["cable_id"] is not None or p["mark_connected"]:
                        a.checks[site_id][f"interface:{p['id']}:cable"] = True
                        continue
                    a.checks[site_id][f"interface:{p['id']}:cable"] = False
                    if listed >= MAX_INTERFACE_GAPS or on_device >= MAX_INTERFACE_GAPS_PER_DEVICE:
                        a.unlisted[site_id] += 1
                        continue
                    listed += 1
                    on_device += 1
                    why = "an address" if iface_ips.get(p["id"]) else f"a description ({p['description'].strip()[:40]})"
                    a.gaps.append({"id": f"interface:{p['id']}:cable", "kind": "interface", "object_id": p["id"], "field": "cable",
                                   "site_id": site_id, "rank": "connectivity",
                                   "label": f"{d['name']} {p['name']} has {why} but no cable.", "device_id": d["id"]})
        # ---- addresses: routers with none; devices with no management address; addresses outside every prefix
        if e.can_see("devices", "ports", "ips"):
            for d in devs:
                if d["status"] not in LIVE or is_passive(d) or d["id"] not in has_ifaces:
                    continue
                if is_router(d):
                    if dev_ips.get(d["id"], 0) == 0:
                        add("router_no_address", ("device", d["id"]), site_id,
                            f"{d['name']} routes traffic, but no interface of it has an address in NetBox.", [dref(d)], device_id=d["id"])
                        continue
                    passed("router_no_address", ("device", d["id"]), site_id)
                if d["primary_ip"] is None:
                    a.gaps.append({"id": f"device:{d['id']}:primary_ip", "kind": "device", "object_id": d["id"], "field": "primary_ip",
                                   "site_id": site_id, "rank": "connectivity", "device_id": d["id"],
                                   "label": f"{d['name']} has no management address (primary IP)."})
                    a.checks[site_id][f"device:{d['id']}:primary_ip"] = False
                else:
                    a.checks[site_id][f"device:{d['id']}:primary_ip"] = True
            unreachable = [g for g in a.gaps if g["site_id"] == site_id and g["field"] == "primary_ip"]
            if unreachable:
                ids = [g["object_id"] for g in unreachable]
                names = [e.device_name(i) for i in ids]
                a.findings.append({"id": f"device_unreachable:site:{site_id}", "kind": "device_unreachable", "rank": "connectivity",
                                   "site_id": site_id, "evidence": [ref("device", i, e.device_name(i)) for i in ids],
                                   "title": f"NetBox does not say how to reach {_some(names)} to manage {'it' if len(ids) == 1 else 'them'}.",
                                   "data": {"device_ids": ids}})
            if e.can_see("prefixes"):
                nets = [ipaddress.ip_network(p["prefix"], strict=False) for p in e.prefixes.values()]
                orphans: dict[str, list[dict[str, Any]]] = defaultdict(list)
                site_devs = {d["id"] for d in devs}
                for _, ip in sorted(e.ips.items()):
                    if ip["interface_id"] is None or dev_of_iface.get(ip["interface_id"]) not in site_devs:
                        continue
                    iface = ipaddress.ip_interface(ip["address"])
                    if any(iface.ip in n for n in nets if n.version == iface.version):
                        passed("address_no_prefix", ("ip", ip["id"]), site_id)
                    else:
                        orphans[str(iface.network)].append(ip)
                for network, ips in sorted(orphans.items()):
                    first = ips[0]
                    add("address_no_prefix", ("ip", first["id"]), site_id,
                        f"{_some([i['address'] for i in ips])} {'is' if len(ips) == 1 else 'are'} in use at {site['name']}, "
                        "but NetBox has no prefix that holds " + ("it." if len(ips) == 1 else "them."),
                        [ref("ip_address", i["id"], i["address"]) for i in ips[:5]], network=network, ip_ids=[i["id"] for i in ips])
        # ---- identity gaps, bundled per rack (or per room for what is not in a rack)
        for d in devs:
            for fld, empty, label in (("serial", not d["serial"], "serial number"), ("asset_tag", d["asset_tag"] is None, "asset tag"),
                                      ("platform", d["platform"] is None, "platform")):
                # A field nobody uses is not a gap: asset tags count once one device has one, platforms once NetBox
                # has any (and never for passive kit such as patch panels and power strips).
                if fld == "platform" and (is_passive(d) or not e.platforms):
                    continue
                if fld == "asset_tag" and not uses_tags:
                    continue
                gid = f"device:{d['id']}:{fld}"
                a.checks[site_id][gid] = not empty
                if empty:
                    a.gaps.append({"id": gid, "kind": "device", "object_id": d["id"], "field": fld, "site_id": site_id,
                                   "rank": "identity", "label": f"{d['name'] or 'A device'} has no {label}.", "device_id": d["id"]})
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
        for model, gaps in sorted(by_model.items()):
            ids = [g["object_id"] for g in gaps][:MAX_SURVEY]
            more = f" ({len(gaps) - len(ids)} more after these)" if len(gaps) > len(ids) else ""
            a.findings.append({"id": f"platform_survey:site:{site_id}:{_slug(model)}", "kind": "platform_survey", "rank": "identity",
                               "site_id": site_id,
                               "title": f"NetBox does not say what {_some([e.device_name(i) for i in ids])} ({model}) at {site['name']} "
                                        f"run{'s' if len(ids) == 1 else ''}{more}.",
                               "evidence": [ref("device", i, e.device_name(i)) for i in ids],
                               "data": {"model": model, "device_ids": ids, "gap_ids": [f"device:{i}:platform" for i in ids]}})
        for (kind, oid), gaps in sorted(id_gaps.items()):
            dev_ids = sorted({g["object_id"] for g in gaps}, key=lambda i: (-(e.devices[i]["position"] or 0), i))
            lacking = _some([w for fld, w in (("serial", "serial number"), ("asset_tag", "asset tag")) if any(g["field"] == fld for g in gaps)], "or")
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
                    f"{_some([d['name'] or '?' for d in loose])} in rack {rack['name']} {'have' if len(loose) != 1 else 'has'} no rack unit on record.",
                    [ref("rack", rid, rack["name"])] + [dref(d) for d in loose], rack_id=rid, device_ids=[d["id"] for d in loose])
            elif any(d["rack_id"] == rid for d in devs):
                passed("rack_devices_unpositioned", ("rack", rid), site_id)
            for fld, empty, label in (("location", rack["location_id"] is None, "room"),
                                      ("iso", rack["iso_x"] is None or rack["iso_y"] is None, "place on the floor plan (iso_x, iso_y)")):
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
            terms = [t for t in e.cterms.values() if t["circuit_id"] == cid]
            site_id = next((t["site_id"] for t in sorted(terms, key=lambda t: t["side"] or "") if t["site_id"] in e.sites), home)
            if landed.get(cid):
                passed("circuit_unterminated", ("circuit", cid), site_id)
                continue
            prov = e.providers.get(c["provider_id"] or 0, {}).get("name", "its provider")
            add("circuit_unterminated", ("circuit", cid), site_id,
                f"Circuit {c['cid']} from {prov} is on record, but NetBox does not say which device it plugs into.",
                [ref("circuit", cid, c["cid"])], circuit_id=cid, provider_id=c["provider_id"])
        for pid, p in sorted(e.providers.items()):
            if any(c["provider_id"] == pid for c in e.circuits.values()):
                passed("provider_unused", ("provider", pid), home)
            elif pid not in claimed:
                add("provider_unused", ("provider", pid), home,
                    f"{p['name']} is listed as a provider, but no circuit of theirs is on record.", [ref("provider", pid, p["name"])],
                    provider_id=pid)
    a.findings.sort(key=_sort_key)
    a.gaps.sort(key=lambda g: (g["site_id"], RANKS.index(g["rank"]), g["kind"], g["object_id"], g["field"]))
    return a


def _some(names: list[str], joint: str = "and") -> str:
    names = [n for n in names if n]
    if len(names) <= 3:
        return ", ".join(names[:-1]) + (f" {joint} " if len(names) > 1 else "") + (names[-1] if names else "")
    return f"{names[0]}, {names[1]} and {len(names) - 2} more"


def _slug(text: str) -> str:
    import re

    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-") or "model"
