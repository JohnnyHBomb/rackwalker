#!/usr/bin/env python3
"""Render the contract fixtures in fixtures/*.json from the synthetic model in devnetbox/seed_model.py.

Deterministic (fixed ids in model order, fixed timestamps). The output is committed; tests validate it against
contracts/scene.schema.json on both sides, and `--check` fails if the committed files are stale.

    python fixtures/make_fixtures.py          # write
    python fixtures/make_fixtures.py --check  # compare only
"""
from __future__ import annotations

import json
import re
import sys
from collections import defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "devnetbox"))
sys.path.insert(0, str(HERE.parent / "proxy"))
import seed_model as sm  # noqa: E402

NB = "http://netbox.example.invalid"
TS = "2026-11-07T09:00:00Z"
META = {"contract_version": "2.5.0", "generated_at": "2026-11-07T10:00:00Z", "netbox_version": None, "source": "fixture"}

PASS_THROUGH = {"front_port", "rear_port"}
# PortKind enum order (contracts/scene.schema.json): DeviceDetail.interfaces is ordered by kind in this order.
KIND_ORDER = ["interface", "front_port", "rear_port", "console_port", "console_server_port", "power_port", "power_outlet",
              "power_feed", "circuit_termination"]


def natural(name: str) -> tuple:
    """NetBox's list order for a device's ports, as far as the seed's names need it: digit runs compare as numbers,
    text compares by code point (upper case before lower case). Measured on 4.7.4 for all 60 seeded devices by
    devnetbox/check_fixtures.py (e.g. Gi1/0/24 < Te1/1/1 < mgmt0; bmc < eth0)."""
    return tuple(int(p) if p.isdigit() else p for p in re.split(r"(\d+)", name))


class World:
    def __init__(self, m: sm.Model):
        self.m = m
        self.site_id = {s.slug: i for i, s in enumerate(m.sites, 1)}
        self.loc_id = {loc.slug: i for i, loc in enumerate(m.locations, 1)}
        self.rack_id = {r.name: i for i, r in enumerate(m.racks, 1)}
        self.dev_id = {d.name: i for i, d in enumerate(m.devices, 1)}
        self.dev = {d.name: d for d in m.devices}
        self.role_id = {r: i for i, r in enumerate(sm.ROLES, 1)}
        self.dt_id = {t: i for i, t in enumerate(sm.DEVICE_TYPES, 1)}
        self.circ_id = {c.cid: i for i, c in enumerate(m.circuits, 1)}
        # ports: (kind, device name, port name) -> port dict
        self.ports: dict[tuple[str, str, str], dict] = {}
        counters: dict[str, int] = defaultdict(int)
        for d in m.devices:
            t = sm.DEVICE_TYPES[d.dtype]
            groups = [("interface", n, typ, mg) for n, typ, mg in t.interfaces]
            groups += [("front_port", str(i), "lc", False) for i in range(1, t.front_rear + 1)]
            groups += [("rear_port", str(i), "lc", False) for i in range(1, t.front_rear + 1)]
            groups += [("power_port", n, "iec-60320-c14", False) for n in t.power_ports]
            groups += [("power_outlet", n, "iec-60320-c13", False) for n in t.power_outlets]
            for kind, name, typ, mgmt in groups:
                counters[kind] += 1
                self.ports[(kind, d.name, name)] = {"kind": kind, "id": counters[kind], "device": d.name, "name": name,
                                                    "type": typ, "mgmt_only": mgmt, "cable": None}
        self.cterm: dict[tuple[str, str], dict] = {}
        n = 0
        for c in m.circuits:
            for side, site in (("A", c.a_site), ("Z", c.z_site)):
                n += 1
                self.cterm[(c.cid, side)] = {"kind": "circuit_termination", "id": n, "cid": c.cid, "side": side, "site": site,
                                             "cable": None}
        self.cables = []
        for i, c in enumerate(m.cables, 1):
            a, b = self.end(c.a), self.end(c.b)
            a["cable"] = b["cable"] = i
            self.cables.append({"id": i, "spec": c, "a": a, "b": b})

    def end(self, e):
        kind, owner, port = e
        return self.cterm[(owner, port)] if kind == "circuit_termination" else self.ports[(kind, owner, port)]

    # ---- contract objects ----
    def site(self, s: sm.SiteSpec) -> dict:
        i = self.site_id[s.slug]
        return {"id": i, "slug": s.slug, "name": s.name, "status": "active", "latitude": s.lat, "longitude": s.lon,
                "iso_theme": s.theme, "country": s.country, "description": f"Synthetic site near {s.town}", "last_updated": TS,
                "netbox_url": f"{NB}/dcim/sites/{i}/"}

    def location(self, loc: sm.LocationSpec) -> dict:
        i = self.loc_id[loc.slug]
        x, y, w, h = loc.iso
        return {"id": i, "site_id": self.site_id[loc.site], "parent_id": None, "name": loc.name, "slug": loc.slug,
                "iso_x": x, "iso_y": y, "iso_w": w, "iso_h": h, "iso_facing": loc.facing,
                "latitude": loc.gps[0] if loc.gps else None, "longitude": loc.gps[1] if loc.gps else None,
                "last_updated": TS, "netbox_url": f"{NB}/dcim/locations/{i}/"}

    def rack(self, r: sm.RackSpec) -> dict:
        i = self.rack_id[r.name]
        return {"id": i, "site_id": self.site_id[r.site], "location_id": self.loc_id[r.location], "name": r.name,
                "status": "active", "u_height": r.u_height, "starting_unit": 1, "desc_units": False,
                "iso_x": r.iso[0], "iso_y": r.iso[1], "iso_facing": r.facing,
                "latitude": r.gps[0] if r.gps else None, "longitude": r.gps[1] if r.gps else None,
                "last_updated": TS, "netbox_url": f"{NB}/dcim/racks/{i}/"}

    def device(self, d: sm.DeviceSpec) -> dict:
        i = self.dev_id[d.name]
        t = sm.DEVICE_TYPES[d.dtype]
        rname, colour = sm.ROLES[d.role]
        return {"id": i, "name": d.name,
                "role": {"id": self.role_id[d.role], "slug": d.role, "name": rname, "color": colour},
                "device_type": {"id": self.dt_id[d.dtype], "manufacturer": t.manufacturer, "model": t.model, "slug": t.slug,
                                "u_height": t.u_height, "is_full_depth": t.is_full_depth},
                "site_id": self.site_id[d.site], "location_id": self.loc_id[d.location],
                "rack_id": self.rack_id[d.rack] if d.rack else None, "position": d.position, "face": d.face,
                "status": d.status, "primary_ip": d.ip, "last_updated": TS,
                "netbox_url": f"{NB}/dcim/devices/{i}/"}

    def term(self, p: dict) -> dict:
        if p["kind"] == "circuit_termination":
            return {"kind": "circuit_termination", "id": p["id"], "name": p["side"], "device_id": None, "device_name": None,
                    "circuit_id": self.circ_id[p["cid"]], "circuit_cid": p["cid"], "site_id": self.site_id[p["site"]]}
        d = self.dev[p["device"]]
        return {"kind": p["kind"], "id": p["id"], "name": p["name"], "device_id": self.dev_id[d.name], "device_name": d.name,
                "circuit_id": None, "circuit_cid": None, "site_id": self.site_id[d.site]}

    def peer(self, p: dict) -> dict | None:
        if p["cable"] is None:
            return None
        c = self.cables[p["cable"] - 1]
        return c["b"] if c["a"] is p else c["a"]

    def cable_ref(self, c: dict) -> dict:
        s = c["spec"]
        return {"id": c["id"], "label": s.label, "type": s.type, "color": s.color or None, "status": s.status}

    def cable(self, c: dict) -> dict:
        s = c["spec"]
        return {**self.cable_ref(c), "length": s.length, "length_unit": "m" if s.length is not None else None,
                "a_terminations": [self.term(c["a"])], "b_terminations": [self.term(c["b"])],
                "netbox_url": f"{NB}/dcim/cables/{c['id']}/"}

    def trace(self, origin: dict) -> dict:
        hops, cur, complete = [], origin, False
        for _ in range(32):
            far = self.peer(cur)
            if far is None:
                break
            hops.append({"near_end": [self.term(cur)], "cable": self.cable_ref(self.cables[cur["cable"] - 1]),
                         "far_end": [self.term(far)]})
            if far["kind"] == "front_port":
                cur = self.ports[("rear_port", far["device"], far["name"])]
            elif far["kind"] == "rear_port":
                cur = self.ports[("front_port", far["device"], far["name"])]
            elif far["kind"] == "circuit_termination":
                other = self.cterm[(far["cid"], "Z" if far["side"] == "A" else "A")]
                hops.append({"near_end": [self.term(far)], "cable": None, "far_end": [self.term(other)]})
                cur = other
            else:
                complete = True
                break
        return {"origin": self.term(origin), "hops": hops, "is_complete": complete, "is_split": False}

    def interface(self, p: dict) -> dict:
        peer = self.peer(p)
        ce = None
        if p["kind"] not in PASS_THROUGH:
            t = self.trace(p)
            if t["is_complete"]:
                ce = t["hops"][-1]["far_end"][0]
        return {"kind": p["kind"], "id": p["id"], "device_id": self.dev_id[p["device"]], "name": p["name"], "type": p["type"],
                "enabled": True, "mgmt_only": p["mgmt_only"], "description": "", "cable_id": p["cable"],
                "link_peers": [self.term(peer)] if peer else [], "connected_endpoint": ce}

    def estate(self) -> dict:
        """The seed as the quest scanner's estate (proxy/rackwalker_proxy/estate.py `Estate.to_json`): what the proxy
        analyses in fixture mode, where there is no NetBox to read."""
        m = self.m
        key = lambda p: self.cterm_key(p) if p["kind"] == "circuit_termination" else [p["kind"], p["id"]]  # noqa: E731
        ports = [{"kind": p["kind"], "id": p["id"], "device_id": self.dev_id[p["device"]], "name": p["name"], "type": p["type"],
                  "enabled": True, "mgmt_only": p["mgmt_only"], "cable_id": p["cable"], "description": "", "mark_connected": False,
                  "rear_ids": [self.ports[("rear_port", p["device"], p["name"])]["id"]] if p["kind"] == "front_port" else [],
                  # the seed's panels are one front port to one rear port of one position
                  "rear": [[self.ports[("rear_port", p["device"], p["name"])]["id"], 1]] if p["kind"] == "front_port" else [], "positions": 1}
                 for p in self.ports.values()]
        ips, n = [], 0
        for d in m.devices:
            if d.ip:
                n += 1
                # devnetbox/seed.py puts each primary address on the device type's management-ish interface
                name = {"ex-c9000": "mgmt0", "ex-a2400": "mgmt0", "ex-r400": "ge-0/0/0", "ex-f200": "port8", "ex-s2": "eth0"}[d.dtype]
                ips.append({"id": n, "address": d.ip, "vrf_id": None, "vrf": None, "interface_id": self.ports[("interface", d.name, name)]["id"]})
        for c in m.circuits:
            if c.a_address:
                n += 1
                _, owner, port = next(x.a for x in m.cables if x.b == ("circuit_termination", c.cid, "A"))
                ips.append({"id": n, "address": c.a_address, "vrf_id": None, "vrf": None, "interface_id": self.ports[("interface", owner, port)]["id"]})
        return {
            "sites": [{"id": self.site_id[s.slug], "name": s.name, "slug": s.slug, "status": "active", "latitude": s.lat, "longitude": s.lon,
                       "facility": None, "country": s.country, "last_updated": None} for s in m.sites],
            "locations": [{"id": self.loc_id[x.slug], "name": x.name, "site_id": self.site_id[x.site]} for x in m.locations],
            "racks": [{"id": self.rack_id[r.name], "name": r.name, "site_id": self.site_id[r.site], "location_id": self.loc_id[r.location],
                       "u_height": r.u_height, "iso_x": r.iso[0], "iso_y": r.iso[1], "last_updated": None} for r in m.racks],
            "devices": [{"id": self.dev_id[d.name], "name": d.name, "site_id": self.site_id[d.site], "location_id": self.loc_id[d.location],
                         "rack_id": self.rack_id[d.rack] if d.rack else None, "position": d.position, "face": d.face, "role": d.role,
                         "role_name": sm.ROLES[d.role][0], "status": d.status, "serial": d.serial, "asset_tag": None, "platform": None,
                         "primary_ip": d.ip, "u_height": float(sm.DEVICE_TYPES[d.dtype].u_height), "model": sm.DEVICE_TYPES[d.dtype].model,
                         "manufacturer": sm.DEVICE_TYPES[d.dtype].manufacturer, "vc_id": None, "type_id": self.dt_id[d.dtype],
                         # nothing is on record about how a device is managed: one with a primary IP is locally managed
                         "mgmt_mode": None, "mgmt_service": None, "mgmt_default": None, "mgmt_default_service": None,
                         "tags": [], "last_updated": None} for d in m.devices],
            "ports": sorted(ports, key=lambda p: (p["kind"], p["id"])),
            "cables": [{"id": c["id"], "type": c["spec"].type, "label": c["spec"].label, "status": c["spec"].status,
                        "a": [key(c["a"])], "b": [key(c["b"])]} for c in self.cables],
            "circuits": [{"id": self.circ_id[c.cid], "cid": c.cid, "provider_id": 1, "type": c.type, "status": c.status, "account_id": 1,
                          "install_date": c.install_date or None, "commit_rate": 1000000, "mrc": f"{c.mrc:.2f}", "nrc": f"{c.nrc:.2f}",
                          "currency": sm.CURRENCY, "last_updated": None} for c in m.circuits],
            "providers": [{"id": 1, "name": m.circuits[0].provider, "slug": "example-carrier", "asns": []}] if m.circuits else [],
            # every device type and role of the seed, and no module bays (the seed's models have none)
            "dtypes": [{"id": self.dt_id[t.slug], "manufacturer": t.manufacturer, "model": t.model, "slug": t.slug, "part_number": "",
                        "u_height": float(t.u_height), "tags": [], "interfaces": len(t.interfaces),
                        "templates": len(t.interfaces) + 2 * t.front_rear + len(t.power_ports) + len(t.power_outlets)} for t in sm.DEVICE_TYPES.values()],
            "roles": [{"id": self.role_id[slug], "name": name, "slug": slug} for slug, (name, _) in sm.ROLES.items()],
            "module_bays": [],
            "circuit_types": [{"id": 1, "name": m.circuits[0].type}] if m.circuits else [],
            "cterms": [{"id": t["id"], "circuit_id": self.circ_id[t["cid"]], "side": t["side"], "site_id": self.site_id[t["site"]],
                        "location_id": None, "network_id": None, "cable_id": t["cable"]} for t in self.cterm.values()],
            "feeds": [], "platforms": [], "ips": ips,
            # devnetbox/seed.py creates one prefix (the carrier's hand-off range) and none for the devices' own addresses
            "prefixes": [{"id": 1, "prefix": sm.CARRIER_PREFIX, "vrf_id": None}],
            "accounts": [{"id": 1, "provider_id": 1, "account": sm.CARRIER_ACCOUNT, "name": "Example Carrier account"}], "networks": [],
            "contacts": [{"id": i, "name": name, "phone": phone, "email": email} for i, (name, phone, email, _) in enumerate(sm.CARRIER_CONTACTS, 1)],
            "contact_roles": [{"id": i, "name": n_, "slug": s_} for i, (n_, s_) in enumerate(sm.CONTACT_ROLES, 1)],
            "assignments": [{"id": i, "object_type": "circuits.provider", "object_id": 1, "contact_id": i,
                             "role_id": 1 + [slug for _, slug in sm.CONTACT_ROLES].index(role)} for i, (_, _, _, role) in enumerate(sm.CARRIER_CONTACTS, 1)],
            "no_fields": [],
            "truncated": [], "unreadable": [], "synthetic": False}

    @staticmethod
    def cterm_key(p: dict) -> list:
        return ["circuit_termination", p["id"]]

    def cable_sites(self, c: dict) -> set[int]:
        return {self.term(c["a"])["site_id"], self.term(c["b"])["site_id"]}


def render() -> dict[str, object]:
    m = sm.build()
    w = World(m)
    out: dict[str, object] = {}
    out["health.json"] = {"status": "ok", "app_version": "0.1.0", "contract_version": "2.5.0", "write_enabled": False,
                          "netbox": {"url": "http://netbox.example.invalid", "reachable": True, "version": "4.7.4", "supported": True,
                                     "permission_check": "unknown"}}
    sites = []
    for s in m.sites:
        sid = w.site_id[s.slug]
        devs = [d for d in m.devices if d.site == s.slug]
        sites.append({"site": w.site(s), "counts": {
            "locations": sum(1 for loc in m.locations if loc.site == s.slug),
            "racks": sum(1 for r in m.racks if r.site == s.slug),
            "devices": len(devs),
            "unracked_devices": sum(1 for d in devs if d.rack is None or d.position is None),
            "cables": sum(1 for c in w.cables if sid in w.cable_sites(c))}})
    links: dict[tuple[int, int], dict] = {}
    for c in w.cables:
        ss = w.cable_sites(c)
        if len(ss) == 2:
            a, b = sorted(ss)
            links.setdefault((a, b), {"a_site_id": a, "b_site_id": b, "cable_ids": [], "circuits": []})["cable_ids"].append(c["id"])
    for c in m.circuits:
        a, b = sorted((w.site_id[c.a_site], w.site_id[c.z_site]))
        links.setdefault((a, b), {"a_site_id": a, "b_site_id": b, "cable_ids": [], "circuits": []})["circuits"].append(
            {"id": w.circ_id[c.cid], "cid": c.cid, "provider": c.provider, "type": c.type, "status": c.status})
    out["world.json"] = {"meta": META, "sites": sites, "links": [links[k] for k in sorted(links)]}

    for site in m.sites:  # every site and every rack, so fixture mode can be walked from the world down
        sid = w.site_id[site.slug]
        out[f"site-{sid}.json"] = {
            "meta": META, "site": w.site(site),
            "locations": [w.location(loc) for loc in m.locations if loc.site == site.slug],
            "racks": [w.rack(r) for r in m.racks if r.site == site.slug],
            "devices": [w.device(d) for d in m.devices if d.site == site.slug],
            "cables": [w.cable(c) for c in w.cables if sid in w.cable_sites(c)]}

    for rack in m.racks:
        rid = w.rack_id[rack.name]
        rack_devs = [d for d in m.devices if d.rack == rack.name]
        names = {d.name for d in rack_devs}
        out[f"rack-{rid}.json"] = {
            "meta": META, "rack": w.rack(rack), "devices": [w.device(d) for d in rack_devs],
            "cables": [w.cable(c) for c in w.cables if {c["a"].get("device"), c["b"].get("device")} & names]}

    # nd-core1 (patch-panel trace), nd-rtr1 (its WAN port traces across circuit EXC-1001), nd-fw1 (a firewall)
    detail_ids = []
    for name in ("nd-core1", "nd-rtr1", "nd-fw1"):
        dev = w.dev[name]
        did = w.dev_id[dev.name]
        detail_ids.append(did)
        ports = sorted((p for p in w.ports.values() if p["device"] == dev.name),
                       key=lambda p: (KIND_ORDER.index(p["kind"]), natural(p["name"])))
        out[f"device-{did}.json"] = {
            "meta": META, "device": w.device(dev),
            "info": {"serial": dev.serial, "asset_tag": None, "platform": None, "description": "", "comments": "",
                     "tags": [], "custom_fields": {"rw_mgmt_mode": None, "rw_mgmt_service": None}},  # the seed tags nothing (live 4.7.4)
            "interfaces": [w.interface(p) for p in ports],
            "cables": [w.cable(w.cables[p["cable"] - 1]) for p in ports if p["cable"]]}

    origin = w.ports[("interface", "nd-acc-nd-c1", "Te1/1/1")]  # through two patch panels to the core
    out[f"trace-interface-{origin['id']}.json"] = {"meta": META, "trace": w.trace(origin)}
    wan = w.ports[("interface", "ww-rtr1", "xe-0/1/0")]  # across carrier circuit EXC-1001
    out[f"trace-interface-{wan['id']}.json"] = {"meta": META, "trace": w.trace(wan)}
    bare = w.ports[("interface", "nd-acc-nd-c1", "mgmt0")]  # no cable: NetBox /trace/ is [], so hops []
    out[f"trace-interface-{bare['id']}.json"] = {"meta": META, "trace": w.trace(bare)}
    # Traces that start at a port of a device fixture (fixture mode can trace from the device level): nd-core1's
    # uplink through both patch panels (173 reversed), nd-rtr1's WAN port across the circuit (210 reversed).
    extra_traces = [w.ports[("interface", "nd-core1", "Te1/0/20")], w.ports[("interface", "nd-rtr1", "xe-0/1/0")]]
    for o in extra_traces:
        out[f"trace-interface-{o['id']}.json"] = {"meta": META, "trace": w.trace(o)}

    srv = w.dev["nd-srv01"]
    srv90_loc = w.loc_id[w.dev["nd-srv90"].location]
    changeset = {"reason": "fixture example", "ops": [
        {"op": "move_rack", "target": {"kind": "rack", "id": 2}, "fields": {"iso_x": 4, "iso_y": 3}, "if_unmodified_since": TS},
        {"op": "create_rack", "client_ref": "newrack", "target": {"kind": "rack", "id": None},
         "fields": {"site_id": 1, "location_id": 1, "name": "ND-R5", "u_height": 42, "iso_x": 9, "iso_y": 2, "iso_facing": "S"}},
        {"op": "place_device", "target": {"kind": "device", "id": w.dev_id["nd-srv90"]},
         "fields": {"rack_id": "@newrack", "position": 20, "face": "front"}},
        {"op": "unplace_device", "target": {"kind": "device", "id": w.dev_id[srv.name]}, "fields": {}},
        {"op": "update_fields", "target": {"kind": "device", "id": detail_ids[0]}, "fields": {"primary_ip": "192.0.2.99/24"}},
        {"op": "move_rack", "target": {"kind": "rack", "id": "@newrack"}, "fields": {"iso_facing": "E"}}]}
    out["changes-request.json"] = changeset
    out["changes-preview.json"] = {"mode": "preview", "applied": False, "write_enabled": False, "ops": [
        {"index": 0, "op": "move_rack", "client_ref": None, "target": {"kind": "rack", "id": 2}, "status": "ok",
         "before": {"iso_x": 3, "iso_y": 2}, "after": {"iso_x": 4, "iso_y": 3},
         "diff": [{"field": "iso_x", "before": 3, "after": 4}, {"field": "iso_y", "before": 2, "after": 3}], "error": None},
        {"index": 1, "op": "create_rack", "client_ref": "newrack", "target": {"kind": "rack", "id": None}, "status": "ok",
         "before": None, "after": {"site_id": 1, "location_id": 1, "name": "ND-R5", "status": "planned", "u_height": 42,
                                   "iso_x": 9, "iso_y": 2, "iso_facing": "S"},
         "diff": [{"field": f, "before": None, "after": v} for f, v in
                  [("site_id", 1), ("location_id", 1), ("name", "ND-R5"), ("status", "planned"), ("u_height", 42),
                   ("iso_x", 9), ("iso_y", 2), ("iso_facing", "S")]], "error": None},
        {"index": 2, "op": "place_device", "client_ref": None, "target": {"kind": "device", "id": w.dev_id["nd-srv90"]},
         # nd-srv90 sits in the Store; NetBox requires device.location == rack.location, so place_device moves it too
         "status": "ok", "before": {"rack_id": None, "position": None, "face": None, "location_id": srv90_loc},
         "after": {"rack_id": "@newrack", "position": 20, "face": "front", "location_id": 1},
         "diff": [{"field": "rack_id", "before": None, "after": "@newrack"}, {"field": "position", "before": None, "after": 20},
                  {"field": "face", "before": None, "after": "front"},
                  {"field": "location_id", "before": srv90_loc, "after": 1}], "error": None},
        {"index": 3, "op": "unplace_device", "client_ref": None, "target": {"kind": "device", "id": w.dev_id[srv.name]},
         "status": "ok", "before": {"rack_id": w.rack_id[srv.rack], "position": srv.position, "face": srv.face},
         "after": {"rack_id": None, "position": None, "face": None},
         "diff": [{"field": "rack_id", "before": w.rack_id[srv.rack], "after": None},
                  {"field": "position", "before": srv.position, "after": None},
                  {"field": "face", "before": srv.face, "after": None}], "error": None},
        {"index": 4, "op": "update_fields", "client_ref": None, "target": {"kind": "device", "id": detail_ids[0]}, "status": "error",
         "before": None, "after": None, "diff": [],
         "error": {"code": "validation_failed", "message": "Field 'primary_ip' is not editable on device in contract 2.5.0.",
                   "details": {"field": "primary_ip",
                               "allowed": ["name", "status", "serial", "asset_tag", "description", "comments", "platform_id"]}}},
        {"index": 5, "op": "move_rack", "client_ref": None, "target": {"kind": "rack", "id": "@newrack"}, "status": "ok",
         "before": {"iso_facing": "S"}, "after": {"iso_facing": "E"},
         "diff": [{"field": "iso_facing", "before": "S", "after": "E"}], "error": None}]}
    out["error-write-disabled.json"] = {"error": {"code": "write_disabled",
                                                  "message": "Apply is refused: the proxy runs in dry-run mode (WRITE_ENABLED is not 1).",
                                                  "details": None}, "request_id": "fixture-0001"}
    out["error-not-found.json"] = {"error": {"code": "not_found", "message": "Site 999 does not exist.",
                                             "details": {"kind": "site", "id": 999}}, "request_id": "fixture-0002"}

    # Game routes (contract 2.1.0). They answer from the game store in every mode, fixture mode included, so these
    # bodies are examples for both sides' schema checks, not canned answers ("store": true in the index).
    # players.json: synthetic tokens that fixture mode accepts at sign-in, and the NetBox users they stand for.
    names = [("walker", "Rae Walker"), ("sorter", None), ("mapper", "Ida Mapper"), ("cabler", None), ("labeller", "Lee Labeller"),
             ("linker", None), ("racker", "Mo Racker"), ("patcher", None), ("tagger", "Kit Tagger"), ("planner", None),
             ("auditor", "Ana Auditor"), ("cooler", None), ("mounter", "Sam Mounter"), ("splicer", None), ("tester", "Jo Tester"),
             ("cataloguer", None), ("surveyor", "Pat Surveyor"), ("fixer", None), ("scout", "Ren Scout"), ("keeper", None),
             ("drafter", None), ("lister", "Val Lister"), ("router", None), ("sweeper", "Max Sweeper"), ("finder", None),
             ("weigher", "Ade Weigher"), ("quester", "Quin Quester"), ("prober", None), ("asker", "Ada Asker"), ("islander", None),
             ("checker", "Che Checker"), ("pointer", None), ("auditor2", None), ("listener", "Lou Listener")]
    players = [{"token": f"nbt_fixtureplayer{i}.synthetic-not-a-secret", "user": {"id": 10 + i, "username": u, "display_name": d}}
               for i, (u, d) in enumerate(names, 1)]
    out["players.json"] = {"$comment": "Fixture mode only: synthetic sign-in tokens -> the NetBox user each stands for. Not secrets.",
                           "players": players}
    keys = {"up": ["ArrowUp", "KeyW"], "down": ["ArrowDown", "KeyS"], "left": ["ArrowLeft", "KeyA"], "right": ["ArrowRight", "KeyD"],
            "interact": ["Enter", "Space", "KeyE"], "cancel": ["Escape", "Backspace"], "menu": ["KeyM"],
            "zoom_in": ["Equal", "NumpadAdd"], "zoom_out": ["Minus", "NumpadSubtract"], "search": ["Slash"], "rotate": ["KeyR"],
            "trace": ["KeyT"], "unrack": ["Delete"], "undo": ["KeyZ"]}
    settings = {"audio": {"muted": False, "volume": 0.6}, "hint_level": "normal", "keys": keys, "reduce_motion": False}
    save = {"iso": {"camera": {"cx": 48.0, "cy": 120.0, "scale": 2}, "site_id": 1, "view": "room:1:1"},
            "overworld": {"facing": "N", "x": 9, "y": 11}, "scene": "iso"}
    out["game-session.json"] = {"signed_in": True, "player": players[0]["user"], "csrf_token": "fixture-csrf-token", "local_mode": False}
    out["game-session-signed-out.json"] = {"signed_in": False, "player": None, "csrf_token": None, "local_mode": False}
    out["game-sign-in-request.json"] = {"token": players[0]["token"]}
    out["game-state.json"] = {"contract_version": META["contract_version"], "player": players[0]["user"], "settings": settings,
                              "flags": {"ilatea.intro.seen": True}, "saves": [{"slot": "auto", "rev": 4, "updated_at": TS, "data": save}]}
    out["game-save-request.json"] = save
    out["game-save.json"] = {"slot": "auto", "rev": 5, "updated_at": TS, "data": save}
    out["game-settings.json"] = settings
    out["game-flags-request.json"] = {"flags": {"ilatea.intro.seen": True, "ilatea.stuck.seen": None}}
    out["game-flags.json"] = {"ilatea.intro.seen": True}
    out["error-unauthorized.json"] = {"error": {"code": "unauthorized", "message": "Sign in first (POST /api/game/session).",
                                                "details": None}, "request_id": "fixture-0003"}

    index = [
        {"method": "GET", "path": "/api/health", "status": 200, "schema": "HealthResponse", "file": "health.json"},
        {"method": "GET", "path": "/api/scene/world", "status": 200, "schema": "WorldResponse", "file": "world.json"}]
    index += [{"method": "GET", "path": f"/api/scene/site/{i}", "status": 200, "schema": "SiteScene", "file": f"site-{i}.json"}
              for i in w.site_id.values()]
    index += [{"method": "GET", "path": f"/api/scene/rack/{i}", "status": 200, "schema": "RackScene", "file": f"rack-{i}.json"}
              for i in w.rack_id.values()]
    index += [
        *({"method": "GET", "path": f"/api/device/{i}", "status": 200, "schema": "DeviceDetail", "file": f"device-{i}.json"}
          for i in detail_ids),
        *({"method": "GET", "path": f"/api/trace/interface/{o['id']}", "status": 200, "schema": "TraceResponse",
           "file": f"trace-interface-{o['id']}.json"} for o in extra_traces),
        {"method": "GET", "path": f"/api/trace/interface/{origin['id']}", "status": 200, "schema": "TraceResponse",
         "file": f"trace-interface-{origin['id']}.json"},
        {"method": "GET", "path": f"/api/trace/interface/{wan['id']}", "status": 200, "schema": "TraceResponse",
         "file": f"trace-interface-{wan['id']}.json"},
        {"method": "GET", "path": f"/api/trace/interface/{bare['id']}", "status": 200, "schema": "TraceResponse",
         "file": f"trace-interface-{bare['id']}.json"},
        {"method": "POST", "path": "/api/changes/preview", "status": 200, "schema": "ChangeResult", "file": "changes-preview.json",
         "request_schema": "ChangeSet", "request_file": "changes-request.json"},
        {"method": "POST", "path": "/api/changes/apply", "status": 403, "schema": "ErrorResponse", "file": "error-write-disabled.json",
         "request_schema": "ChangeSet", "request_file": "changes-request.json"},
        {"method": "GET", "path": "/api/scene/site/999", "status": 404, "schema": "ErrorResponse", "file": "error-not-found.json"},
        {"method": "GET", "path": "/api/game/session", "status": 200, "schema": "GameSession", "file": "game-session.json", "store": True},
        {"method": "POST", "path": "/api/game/session", "status": 200, "schema": "GameSession", "file": "game-session.json",
         "request_schema": "SignInRequest", "request_file": "game-sign-in-request.json", "store": True},
        {"method": "DELETE", "path": "/api/game/session", "status": 200, "schema": "GameSession", "file": "game-session-signed-out.json",
         "store": True},
        {"method": "GET", "path": "/api/game/state", "status": 200, "schema": "GameState", "file": "game-state.json", "store": True},
        {"method": "PUT", "path": "/api/game/saves/auto", "status": 200, "schema": "SaveSlot", "file": "game-save.json",
         "request_schema": "SaveData", "request_file": "game-save-request.json", "store": True},
        {"method": "PUT", "path": "/api/game/settings", "status": 200, "schema": "GameSettings", "file": "game-settings.json",
         "request_schema": "GameSettings", "request_file": "game-settings.json", "store": True},
        {"method": "PATCH", "path": "/api/game/flags", "status": 200, "schema": "Flags", "file": "game-flags.json",
         "request_schema": "FlagsPatch", "request_file": "game-flags-request.json", "store": True},
        {"method": "GET", "path": "/api/game/state", "status": 401, "schema": "ErrorResponse", "file": "error-unauthorized.json",
         "store": True},
    ]
    # What the quest scanner analyses in fixture mode (not a route body, so it is not in the index).
    out["estate.json"] = w.estate()
    quest_files, quest_index = quest_examples(out, index, players[0]["token"])
    out.update(quest_files)
    index += quest_index
    out["index.json"] = {"$comment": "Fixture catalogue: request -> canned response, with the $defs name each body validates against.",
                         "fixtures": index}
    return out


def quest_examples(files: dict[str, object], index: list[dict], token: str) -> tuple[dict[str, object], list[dict]]:
    """Example bodies of the quest routes (contract 2.3.0), made by a fixture-mode proxy with an empty game store: the
    first practice-island quest line played from its offer to its practice completion. Like the other game routes they
    answer from the proxy's own state, so they are examples for the schema checks ("store": true), not canned answers.
    Time stamps are fixed so the files are reproducible."""
    import tempfile

    from fastapi.testclient import TestClient

    from rackwalker_proxy.app import Settings, create_app

    def fixed(x):
        if isinstance(x, dict):
            return {k: (TS if k == "updated_at" and v is not None else fixed(v)) for k, v in x.items()}
        return [fixed(v) for v in x] if isinstance(x, list) else x

    def fixed_seen(body):
        return {**body, "items": [{**it, "first_seen": TS} for it in body["items"]]}

    out: dict[str, object] = {}
    with tempfile.TemporaryDirectory() as tmp:
        fx = Path(tmp) / "fixtures"
        fx.mkdir()
        for name, body in {**files, "index.json": {"fixtures": index}}.items():
            (fx / name).write_text(json.dumps(body))
        app = create_app(Settings.from_env({"RACKWALKER_FIXTURES": str(fx), "RACKWALKER_STATE_DIR": str(Path(tmp) / "state")}))
        with TestClient(app, base_url="http://127.0.0.1") as cl:
            csrf = cl.post("/api/game/session", json={"token": token}, headers={"X-RackWalker-CSRF": "sign-in"}).json()["csrf_token"]
            h = {"X-RackWalker-CSRF": csrf}

            def call(method: str, path: str, body=None):
                r = cl.request(method, path, json=body, headers=h)
                assert r.status_code == 200, (path, r.text)
                return fixed(r.json())

            out["quest-gaps-1.json"] = call("GET", "/api/quests/gaps/1?limit=5")
            board = call("GET", "/api/game/quests?scope=practice")
            out["quest-board.json"] = board
            qid = board["next_quest_id"]
            ref = {"scope": "practice", "quest_id": qid}
            answers = [("device", {"choice": "device:9000001"}), ("provider", {"choice": "provider:9000001"}),
           ("circuit_id", {"text": "EXF-20417"}), ("circuit_type", {"choice": "type:9000001"}),
                       ("local_port", {"choice": "interface:9000026"}), ("cable_type", {"choice": "smf"})]
            for step, a in answers:
                body = {**ref, "step": step, **a}
                quest = call("POST", "/api/game/quests/answer", body)
                if step == "device":
                    out["quest-answer-request.json"], out["quest.json"] = body, quest
            out["quest-ref-request.json"] = ref
            out["quest-preview.json"] = call("POST", "/api/game/quests/preview", ref)
            out["quest-verdict.json"] = call("POST", "/api/game/quests/apply", ref)
            out["boss-ack-request.json"] = {"scope": "practice", "site_id": 9000001}
            out["packs.json"] = call("GET", "/api/packs")
            out["game-catalogue.json"] = fixed_seen(call("GET", "/api/game/catalogue"))
            cl.post("/api/game/practice/reset", headers=h)
    idx = [
        {"method": "GET", "path": "/api/quests/gaps/1", "status": 200, "schema": "GapList", "file": "quest-gaps-1.json", "store": True},
        {"method": "GET", "path": "/api/game/quests", "status": 200, "schema": "QuestBoard", "file": "quest-board.json", "store": True},
        {"method": "POST", "path": "/api/game/quests/answer", "status": 200, "schema": "Quest", "file": "quest.json",
         "request_schema": "QuestAnswer", "request_file": "quest-answer-request.json", "store": True},
        {"method": "POST", "path": "/api/game/quests/preview", "status": 200, "schema": "ChangeResult", "file": "quest-preview.json",
         "request_schema": "QuestRef", "request_file": "quest-ref-request.json", "store": True},
        {"method": "POST", "path": "/api/game/quests/apply", "status": 200, "schema": "QuestVerdict", "file": "quest-verdict.json",
         "request_schema": "QuestRef", "request_file": "quest-ref-request.json", "store": True},
        {"method": "POST", "path": "/api/game/quests/verify", "status": 200, "schema": "QuestVerdict", "file": "quest-verdict.json",
         "request_schema": "QuestRef", "request_file": "quest-ref-request.json", "store": True},
        {"method": "POST", "path": "/api/game/practice/reset", "status": 200, "schema": "QuestBoard", "file": "quest-board.json", "store": True},
        {"method": "POST", "path": "/api/game/boss/ack", "status": 200, "schema": "QuestBoard", "file": "quest-board.json",
         "request_schema": "BossAck", "request_file": "boss-ack-request.json", "store": True},
        {"method": "GET", "path": "/api/packs", "status": 200, "schema": "PackList", "file": "packs.json", "store": True},
        {"method": "GET", "path": "/api/game/catalogue", "status": 200, "schema": "Catalogue", "file": "game-catalogue.json", "store": True},
    ]
    return out, idx


def main(argv: list[str]) -> int:
    files = render()
    stale = []
    for name, body in files.items():
        text = json.dumps(body, indent=2, ensure_ascii=False) + "\n"
        p = HERE / name
        if "--check" in argv:
            if not p.exists() or p.read_text() != text:
                stale.append(name)
        else:
            p.write_text(text)
    if stale:
        print("stale fixtures (run python fixtures/make_fixtures.py):", ", ".join(stale))
        return 1
    print(("checked " if "--check" in argv else "wrote ") + f"{len(files)} fixture files")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
