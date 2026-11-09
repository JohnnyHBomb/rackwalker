"""The estate: everything the quest scanner reasons about, read from NetBox once into plain data.

An `Estate` holds sites, locations, racks, devices (with role, platform, power and position), every cable-able port,
cables, circuits, providers, circuit terminations, power feeds, IP addresses and prefixes, each as a small dict keyed by
its NetBox id. It is plain data on purpose:

  * findings.py and quests.py are pure functions of an Estate, so every rule is tested without NetBox;
  * the practice island is an Estate the proxy makes up (practice.py) and changes in memory, never in NetBox;
  * `to_json` / `from_json` give the fixture-mode proxy an estate without a NetBox.

Reading is bounded: each list is read with a slim `fields=` selection, page by page, up to a cap (`CAPS`); a list that
hits its cap is named in `Estate.truncated`, and a list NetBox refuses to the proxy's token is named in
`Estate.unreadable`. Rules that would need a truncated or unreadable list say nothing (the game never reports
something as missing only because it could not look).

The connectivity graph (`Estate.components`) joins: every interface of a device to that device; a patch panel's front
port to the rear port it maps to; the two ends of a cable; the terminations of a circuit. Power ports, outlets and
feeds are a separate graph (`powered`).
"""
from __future__ import annotations

import asyncio
import copy
import ipaddress
from dataclasses import dataclass, field
from typing import Any

from . import country as country_mod
from . import netbox as nb
from .errors import ApiError

PortKey = tuple[str, int]

# Upper bounds on what one estate read takes from NetBox (rows per list).
CAPS = {"sites": 2_000, "locations": 10_000, "racks": 20_000, "devices": 20_000, "ports": 200_000, "cables": 100_000,
        "circuits": 20_000, "ips": 100_000, "prefixes": 20_000}
DATA_PORTS = ("interface", "front_port", "rear_port")
POWER_PORTS = ("power_port", "power_outlet")
# NetBox interface types that never take a cable.
NON_PHYSICAL = ("virtual", "bridge", "lag")
NETWORK_ROLES = ("switch", "router", "firewall", "core", "access", "distribution", "gateway", "wan", "edge", "spine", "leaf")
ROUTER_ROLES = ("router", "gateway", "wan", "edge")
PASSIVE_ROLES = ("patch", "panel", "pdu", "power", "cable-management", "shelf", "blank")


def port_key(s: str) -> PortKey:
    kind, _, oid = s.rpartition(":")
    return kind, int(oid)


def is_physical(port: dict[str, Any]) -> bool:
    t = port.get("type") or ""
    return port["kind"] != "interface" or not (t in NON_PHYSICAL or t.startswith("ieee802.11") or t.startswith("virtual"))


def role_is(dev: dict[str, Any], words: tuple[str, ...]) -> bool:
    text = f"{dev.get('role') or ''} {dev.get('role_name') or ''}".lower()
    return any(w in text for w in words)


def is_network(dev: dict[str, Any]) -> bool:
    return role_is(dev, NETWORK_ROLES)


def is_router(dev: dict[str, Any]) -> bool:
    return role_is(dev, ROUTER_ROLES + ("firewall",))


def is_switch(dev: dict[str, Any]) -> bool:
    return role_is(dev, ("switch", "access", "core", "distribution", "spine", "leaf"))


def is_passive(dev: dict[str, Any]) -> bool:
    return role_is(dev, PASSIVE_ROLES)


@dataclass
class Estate:
    sites: dict[int, dict[str, Any]] = field(default_factory=dict)
    locations: dict[int, dict[str, Any]] = field(default_factory=dict)
    racks: dict[int, dict[str, Any]] = field(default_factory=dict)
    devices: dict[int, dict[str, Any]] = field(default_factory=dict)
    ports: dict[PortKey, dict[str, Any]] = field(default_factory=dict)
    cables: dict[int, dict[str, Any]] = field(default_factory=dict)
    circuits: dict[int, dict[str, Any]] = field(default_factory=dict)
    providers: dict[int, dict[str, Any]] = field(default_factory=dict)
    circuit_types: dict[int, dict[str, Any]] = field(default_factory=dict)
    cterms: dict[int, dict[str, Any]] = field(default_factory=dict)
    feeds: dict[int, dict[str, Any]] = field(default_factory=dict)
    platforms: dict[int, dict[str, Any]] = field(default_factory=dict)
    ips: dict[int, dict[str, Any]] = field(default_factory=dict)
    prefixes: dict[int, dict[str, Any]] = field(default_factory=dict)
    truncated: list[str] = field(default_factory=list)
    unreadable: list[str] = field(default_factory=list)
    synthetic: bool = False

    # ---------------------------------------------------------------- (de)serialisation
    TABLES = ("sites", "locations", "racks", "devices", "cables", "circuits", "providers", "circuit_types", "cterms", "feeds",
              "platforms", "ips", "prefixes")

    def to_json(self) -> dict[str, Any]:
        out: dict[str, Any] = {t: [getattr(self, t)[k] for k in sorted(getattr(self, t))] for t in self.TABLES}
        out["ports"] = [self.ports[k] for k in sorted(self.ports)]
        out.update(truncated=self.truncated, unreadable=self.unreadable, synthetic=self.synthetic)
        return out

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> "Estate":
        e = cls(truncated=list(data.get("truncated", [])), unreadable=list(data.get("unreadable", [])),
                synthetic=bool(data.get("synthetic", False)))
        for t in cls.TABLES:
            setattr(e, t, {row["id"]: _tuples(row) for row in data.get(t, [])})
        e.ports = {(p["kind"], p["id"]): _tuples(p) for p in data.get("ports", [])}
        return e

    def copy(self) -> "Estate":
        return copy.deepcopy(self)

    # ---------------------------------------------------------------- lookups
    def can_see(self, *lists: str) -> bool:
        """True when every named list was read in full."""
        return not any(name in self.truncated or name in self.unreadable for name in lists)

    def site_devices(self, site_id: int) -> list[dict[str, Any]]:
        return [d for _, d in sorted(self.devices.items()) if d["site_id"] == site_id]

    def device_ports(self, device_id: int, kinds: tuple[str, ...] = DATA_PORTS) -> list[dict[str, Any]]:
        return sorted((p for p in self.ports.values() if p["device_id"] == device_id and p["kind"] in kinds),
                      key=lambda p: (kinds.index(p["kind"]), natural(p["name"]), p["id"]))

    def free_ports(self, device_id: int) -> list[dict[str, Any]]:
        """Physical interfaces of a device with no cable, likeliest uplink first: non-management, then by name."""
        ports = [p for p in self.device_ports(device_id, ("interface",)) if p["cable_id"] is None and is_physical(p) and p["enabled"]]
        return sorted(ports, key=lambda p: (p["mgmt_only"], natural(p["name"]), p["id"]))

    def device_name(self, device_id: int) -> str:
        d = self.devices.get(device_id)
        return (d["name"] or f"device {device_id}") if d else f"device {device_id}"

    def port_site(self, key: PortKey) -> int | None:
        p = self.ports.get(key)
        d = self.devices.get(p["device_id"]) if p else None
        return d["site_id"] if d else None

    def where(self, dev: dict[str, Any]) -> str:
        """Where to find a device, in words: rack and U, else room, else site."""
        rack = self.racks.get(dev["rack_id"]) if dev["rack_id"] else None
        loc = self.locations.get(dev["location_id"]) if dev["location_id"] else None
        if rack:
            u = f" U{dev['position']:g}" if dev["position"] is not None else ""
            return f"rack {rack['name']}{u}" + (f" in {loc['name']}" if loc else "")
        if loc:
            return f"{loc['name']} (not in a rack)"
        return self.sites[dev["site_id"]]["name"]

    # ---------------------------------------------------------------- graphs
    def components(self) -> "Components":
        return Components(self)

    def powered(self, device_id: int) -> bool | None:
        """True when a power port of the device has a cable; None when it has no power ports recorded."""
        pp = [p for p in self.ports.values() if p["device_id"] == device_id and p["kind"] == "power_port"]
        return any(p["cable_id"] is not None for p in pp) if pp else None

    def device_ips(self, device_id: int) -> list[dict[str, Any]]:
        ifaces = {p["id"] for p in self.ports.values() if p["device_id"] == device_id and p["kind"] == "interface"}
        return [ip for _, ip in sorted(self.ips.items()) if ip["interface_id"] in ifaces]

    # ---------------------------------------------------------------- simulated writes (practice island)
    def next_id(self, table: str) -> int:
        rows = self.ports if table == "ports" else getattr(self, table)
        ids = [k[1] if isinstance(k, tuple) else k for k in rows]
        return max(ids, default=0) + 1


def _tuples(row: dict[str, Any]) -> dict[str, Any]:
    """JSON has no tuples: cable ends and port maps come back as lists."""
    out = dict(row)
    for k in ("a", "b"):
        if k in out and isinstance(out[k], list):
            out[k] = [tuple(x) for x in out[k]]
    return out


def natural(name: str) -> tuple[Any, ...]:
    import re

    return tuple((0, int(p)) if p.isdigit() else (1, p) for p in re.split(r"(\d+)", name) if p != "")


class Components:
    """Connected components of the data graph. Nodes are devices ("d", id), pass-through ports and circuits."""

    def __init__(self, e: Estate):
        self.e = e
        self.parent: dict[Any, Any] = {}
        for did in e.devices:
            self._find(("d", did))
        for key, p in e.ports.items():
            if p["kind"] == "interface":
                continue
            if p["kind"] == "front_port":
                for rear in p.get("rear_ids") or []:
                    self._union(("p", "front_port", p["id"]), ("p", "rear_port", rear))
        for c in e.cables.values():
            ends = [self.node(k) for k in list(c["a"]) + list(c["b"])]
            ends = [n for n in ends if n is not None]
            for n in ends[1:]:
                self._union(ends[0], n)
        for t in e.cterms.values():
            self._union(("c", t["circuit_id"]), ("t", t["id"]))

    def node(self, key: PortKey) -> Any:
        kind, oid = key
        if kind == "interface":
            p = self.e.ports.get(key)
            return ("d", p["device_id"]) if p else None
        if kind in ("front_port", "rear_port"):
            return ("p", kind, oid)
        if kind == "circuit_termination":
            return ("t", oid)
        return None  # power: not part of the data graph

    def _find(self, x: Any) -> Any:
        self.parent.setdefault(x, x)
        while self.parent[x] != x:
            self.parent[x] = self.parent[self.parent[x]]
            x = self.parent[x]
        return x

    def _union(self, a: Any, b: Any) -> None:
        ra, rb = self._find(a), self._find(b)
        if ra != rb:
            self.parent[max(ra, rb, key=repr)] = min(ra, rb, key=repr)

    def of_device(self, device_id: int) -> Any:
        return self._find(("d", device_id))

    def of_circuit(self, circuit_id: int) -> Any:
        return self._find(("c", circuit_id))

    def same(self, a: int, b: int) -> bool:
        return self.of_device(a) == self.of_device(b)

    def devices_in(self, root: Any) -> list[int]:
        return sorted(d for d in self.e.devices if self._find(("d", d)) == root)

    def circuits_in(self, root: Any) -> list[int]:
        return sorted(c for c in self.e.circuits if self._find(("c", c)) == root)

    def degree(self, device_id: int) -> int:
        return sum(1 for p in self.e.ports.values() if p["device_id"] == device_id and p["kind"] in DATA_PORTS and p["cable_id"] is not None)


# ---------------------------------------------------------------- reading NetBox
async def _rows(client: nb.NetBox, e: Estate, name: str, path: str, fields: str, cap: int) -> list[dict[str, Any]]:
    """A whole list with a slim field selection, up to `cap` rows. A list the proxy's token may not view is recorded
    in `e.unreadable` and read as empty."""
    out: list[dict[str, Any]] = []
    try:
        while True:
            page = await client.request("GET", path + "/", params=[("fields", fields), ("limit", nb.PAGE), ("offset", len(out))])
            out.extend(page["results"])
            if not page["results"] or len(out) >= page["count"]:
                return out
            if len(out) >= cap:
                if name not in e.truncated:
                    e.truncated.append(name)
                return out[:cap]
    except ApiError as err:
        if err.code == "netbox_error" and (err.details or {}).get("status") in (401, 403):
            if name not in e.unreadable:
                e.unreadable.append(name)
            return []
        raise


def _id(ref: Any) -> int | None:
    return ref["id"] if isinstance(ref, dict) and "id" in ref else None


def _val(v: Any) -> Any:
    return v.get("value") if isinstance(v, dict) and "value" in v else v


def _cf(row: dict[str, Any], name: str) -> Any:
    return _val((row.get("custom_fields") or {}).get(name))


async def read_estate(client: nb.NetBox) -> Estate:
    """One bounded read of everything the scanner looks at."""
    await client.version()
    e = Estate()
    port_fields = {
        "interface": ("dcim/interfaces", "id,name,device,type,enabled,mgmt_only,cable,description,mark_connected"),
        "front_port": ("dcim/front-ports", "id,name,device,cable,rear_ports,description"),
        "rear_port": ("dcim/rear-ports", "id,name,device,cable,description"),
        "power_port": ("dcim/power-ports", "id,name,device,cable,description"),
        "power_outlet": ("dcim/power-outlets", "id,name,device,cable,description"),
    }
    jobs = {
        "sites": ("dcim/sites", "id,name,slug,status,latitude,longitude,facility,region,physical_address,custom_fields", CAPS["sites"]),
        "locations": ("dcim/locations", "id,name,site", CAPS["locations"]),
        "racks": ("dcim/racks", "id,name,site,location,u_height,custom_fields", CAPS["racks"]),
        "devices": ("dcim/devices", "id,name,site,location,rack,position,face,role,device_type,status,serial,asset_tag,platform,primary_ip", CAPS["devices"]),
        "cables": ("dcim/cables", "id,type,label,status,a_terminations,b_terminations", CAPS["cables"]),
        "circuits": ("circuits/circuits", "id,cid,provider,type,status", CAPS["circuits"]),
        "providers": ("circuits/providers", "id,name", CAPS["circuits"]),
        "circuit_types": ("circuits/circuit-types", "id,name", CAPS["circuits"]),
        "cterms": ("circuits/circuit-terminations", "id,circuit,term_side,termination_type,termination_id,cable", CAPS["circuits"]),
        "feeds": ("dcim/power-feeds", "id,name,rack,cable", CAPS["circuits"]),
        "platforms": ("dcim/platforms", "id,name", CAPS["circuits"]),
        "ips": ("ipam/ip-addresses", "id,address,assigned_object_type,assigned_object_id", CAPS["ips"]),
        "prefixes": ("ipam/prefixes", "id,prefix", CAPS["prefixes"]),
    }
    names = list(jobs)
    got = await asyncio.gather(*(_rows(client, e, n, *jobs[n]) for n in names),
                               *(_rows(client, e, "ports", p, f, CAPS["ports"]) for p, f in port_fields.values()))
    raw = dict(zip(names, got[:len(names)]))
    raw_ports = dict(zip(port_fields, got[len(names):]))

    for s in raw["sites"]:
        e.sites[s["id"]] = {
            "id": s["id"], "name": s["name"], "slug": s.get("slug"), "status": _val(s.get("status")),
            "latitude": s.get("latitude"), "longitude": s.get("longitude"), "facility": s.get("facility") or None,
            "country": country_mod.site_country(_cf(s, "iso_country"), (s.get("region") or {}).get("slug"),
                                                (s.get("region") or {}).get("name"), s.get("physical_address"))}
    for loc in raw["locations"]:
        e.locations[loc["id"]] = {"id": loc["id"], "name": loc["name"], "site_id": _id(loc.get("site"))}
    for r in raw["racks"]:
        e.racks[r["id"]] = {"id": r["id"], "name": r["name"], "site_id": _id(r.get("site")), "location_id": _id(r.get("location")),
                            "u_height": r.get("u_height") or 42, "iso_x": _cf(r, "iso_x"), "iso_y": _cf(r, "iso_y")}
    dtypes = await client.device_types({_id(d.get("device_type")) for d in raw["devices"]} - {None})  # type: ignore[arg-type]
    roles = await client.roles({_id(d.get("role")) for d in raw["devices"]} - {None})  # type: ignore[arg-type]
    for d in raw["devices"]:
        dt, role = dtypes.get(_id(d.get("device_type")) or 0), roles.get(_id(d.get("role")) or 0)
        ip = d.get("primary_ip")
        e.devices[d["id"]] = {
            "id": d["id"], "name": d.get("name"), "site_id": _id(d.get("site")), "location_id": _id(d.get("location")),
            "rack_id": _id(d.get("rack")), "position": d.get("position"), "face": _val(d.get("face")),
            "role": role.slug if role else None, "role_name": role.name if role else None, "status": _val(d.get("status")),
            "serial": d.get("serial") or "", "asset_tag": d.get("asset_tag") or None,
            "platform": (d.get("platform") or {}).get("name") if d.get("platform") else None,
            "primary_ip": ip.get("address") if isinstance(ip, dict) else None,
            "u_height": float(dt.u_height) if dt else 1.0, "model": dt.model if dt else None,
            "manufacturer": dt.manufacturer.name if dt else None}
    for kind, rows in raw_ports.items():
        for p in rows:
            did = _id(p.get("device"))
            if did not in e.devices:
                continue
            e.ports[(kind, p["id"])] = {
                "kind": kind, "id": p["id"], "device_id": did, "name": p["name"], "type": _val(p.get("type")),
                "enabled": p.get("enabled", True), "mgmt_only": bool(p.get("mgmt_only", False)), "cable_id": _id(p.get("cable")),
                "description": p.get("description") or "", "mark_connected": bool(p.get("mark_connected", False)),
                "rear_ids": [m["rear_port"] for m in p.get("rear_ports") or [] if isinstance(m, dict) and isinstance(m.get("rear_port"), int)]}
    for c in raw["cables"]:
        def ends(side: str) -> list[PortKey]:
            out = []
            for t in c.get(side) or []:
                kind = _TERM_KIND.get(t.get("object_type") or "")
                if kind and isinstance(t.get("object_id"), int):
                    out.append((kind, t["object_id"]))
            return out
        e.cables[c["id"]] = {"id": c["id"], "type": c.get("type") or None, "label": c.get("label") or None,
                             "status": _val(c.get("status")), "a": ends("a_terminations"), "b": ends("b_terminations")}
    for p in raw["providers"]:
        e.providers[p["id"]] = {"id": p["id"], "name": p["name"]}
    for t in raw["circuit_types"]:
        e.circuit_types[t["id"]] = {"id": t["id"], "name": t["name"]}
    for c in raw["circuits"]:
        e.circuits[c["id"]] = {"id": c["id"], "cid": c["cid"], "provider_id": _id(c.get("provider")),
                               "type": (c.get("type") or {}).get("name"), "status": _val(c.get("status"))}
    for t in raw["cterms"]:
        e.cterms[t["id"]] = {"id": t["id"], "circuit_id": _id(t.get("circuit")), "side": t.get("term_side"),
                             "site_id": t.get("termination_id") if t.get("termination_type") == "dcim.site" else None,
                             "cable_id": _id(t.get("cable"))}
    for f in raw["feeds"]:
        e.feeds[f["id"]] = {"id": f["id"], "name": f["name"], "rack_id": _id(f.get("rack")), "cable_id": _id(f.get("cable"))}
    for p in raw["platforms"]:
        e.platforms[p["id"]] = {"id": p["id"], "name": p["name"]}
    for ip in raw["ips"]:
        e.ips[ip["id"]] = {"id": ip["id"], "address": ip["address"],
                           "interface_id": ip.get("assigned_object_id") if ip.get("assigned_object_type") == "dcim.interface" else None}
    for p in raw["prefixes"]:
        e.prefixes[p["id"]] = {"id": p["id"], "prefix": p["prefix"]}
    return e


_TERM_KIND = {"dcim.interface": "interface", "dcim.frontport": "front_port", "dcim.rearport": "rear_port",
              "dcim.powerport": "power_port", "dcim.poweroutlet": "power_outlet", "dcim.powerfeed": "power_feed",
              "circuits.circuittermination": "circuit_termination", "dcim.consoleport": "console_port",
              "dcim.consoleserverport": "console_server_port"}


# ---------------------------------------------------------------- simulated ops (practice island; never NetBox)
class SimError(Exception):
    pass


def simulate(e: Estate, ops: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Runs change ops against the estate in memory and returns OpResult-shaped dicts. Used only for the practice
    island: the same op shapes as the real change path, written to nothing but this object."""
    refs: dict[str, tuple[str, int]] = {}
    out: list[dict[str, Any]] = []

    def resolve(v: Any, kind: str) -> int:
        if isinstance(v, str) and v.startswith("@"):
            if v[1:] not in refs or refs[v[1:]][0] != kind:
                raise SimError(f"Reference {v} does not name an earlier {kind} of this change set.")
            return refs[v[1:]][1]
        return int(v)

    for i, op in enumerate(ops):
        name, f, target = op["op"], dict(op.get("fields") or {}), op["target"]
        res = {"index": i, "op": name, "client_ref": op.get("client_ref"), "target": dict(target), "status": "ok",
               "before": None, "after": None, "diff": [], "error": None}
        try:
            if name == "update_fields":
                table = {"device": e.devices, "site": e.sites, "rack": e.racks}.get(target["kind"])
                row = table.get(target["id"]) if table is not None else None
                if row is None:
                    raise SimError(f"{target['kind'].capitalize()} {target['id']} does not exist on the practice island.")
                write = dict(f)
                if "platform_id" in write:
                    pid = write.pop("platform_id")
                    if pid is not None and pid not in e.platforms:
                        raise SimError(f"Platform {pid} does not exist.")
                    write["platform"] = e.platforms[pid]["name"] if pid is not None else None
                    res["before"], res["after"] = {"platform_id": None}, {"platform_id": pid}
                shown = {k: v for k, v in f.items() if k != "platform_id"}
                res["before"] = {**(res["before"] or {}), **{k: row.get(k) for k in shown}}
                res["after"] = {**(res["after"] or {}), **shown}
                row.update(write)
            elif name == "place_device":
                d = e.devices[target["id"]]
                res["before"] = {k: d[k] for k in ("rack_id", "position", "face")}
                d.update(rack_id=f["rack_id"], position=f["position"], face=f["face"])
                res["after"] = {k: d[k] for k in ("rack_id", "position", "face")}
            elif name == "move_rack":
                r = e.racks[target["id"]]
                res["before"] = {k: r.get(k) for k in f}
                r.update(f)
                res["after"] = dict(f)
            elif name == "connect_cable":
                a = (f["a_kind"], resolve(f["a_id"], f["a_kind"]))
                b = (f["b_kind"], resolve(f["b_id"], f["b_kind"]))
                for k in (a, b):
                    holder = e.cterms.get(k[1]) if k[0] == "circuit_termination" else e.ports.get(k)
                    if holder is None:
                        raise SimError(f"{k[0].replace('_', ' ').capitalize()} {k[1]} does not exist.")
                    if holder["cable_id"] is not None:
                        raise SimError(f"{k[0].replace('_', ' ').capitalize()} {holder.get('name') or k[1]} already has a cable.")
                cid = e.next_id("cables")
                e.cables[cid] = {"id": cid, "type": f.get("type"), "label": f.get("label"), "status": "connected", "a": [a], "b": [b]}
                for k in (a, b):
                    (e.cterms[k[1]] if k[0] == "circuit_termination" else e.ports[k])["cable_id"] = cid
                res["target"]["id"] = cid
                res["after"] = {k: v for k, v in f.items()}
            elif name == "create_circuit":
                if any(c["cid"] == f["cid"] and c["provider_id"] == f["provider_id"] for c in e.circuits.values()):
                    raise SimError(f"Circuit {f['cid']} already exists for that provider.")
                cid = e.next_id("circuits")
                e.circuits[cid] = {"id": cid, "cid": f["cid"], "provider_id": f["provider_id"],
                                   "type": e.circuit_types[f["type_id"]]["name"], "status": "active"}
                res["target"]["id"] = cid
                res["after"] = dict(f)
                if op.get("client_ref"):
                    refs[op["client_ref"]] = ("circuit", cid)
            elif name == "create_circuit_termination":
                circ = resolve(f["circuit_id"], "circuit")
                if any(t["circuit_id"] == circ and t["side"] == f["term_side"] for t in e.cterms.values()):
                    raise SimError(f"Side {f['term_side']} of that circuit already has a termination.")
                tid = e.next_id("cterms")
                e.cterms[tid] = {"id": tid, "circuit_id": circ, "side": f["term_side"], "site_id": f["site_id"], "cable_id": None}
                res["target"]["id"] = tid
                res["after"] = dict(f)
                if op.get("client_ref"):
                    refs[op["client_ref"]] = ("circuit_termination", tid)
            elif name == "assign_ip":
                d = e.devices[target["id"]]
                iface = e.ports.get(("interface", f["interface_id"]))
                if iface is None or iface["device_id"] != d["id"]:
                    raise SimError("That interface is not on this device.")
                addr = str(ipaddress.ip_interface(f["address"]))
                found = next((ip for ip in e.ips.values() if ip["address"] == addr and ip["interface_id"] == iface["id"]), None)
                if found is None:
                    iid = e.next_id("ips")
                    e.ips[iid] = {"id": iid, "address": addr, "interface_id": iface["id"]}
                res["before"] = {"primary_ip": d["primary_ip"]}
                if f.get("primary", True):
                    d["primary_ip"] = addr
                res["after"] = {"address": addr, "interface_id": iface["id"], "primary_ip": d["primary_ip"]}
            elif name == "create_prefix":
                net = str(ipaddress.ip_network(f["prefix"], strict=True))
                if any(p["prefix"] == net for p in e.prefixes.values()):
                    raise SimError(f"Prefix {net} already exists.")
                pid = e.next_id("prefixes")
                e.prefixes[pid] = {"id": pid, "prefix": net}
                res["target"]["id"] = pid
                res["after"] = {"prefix": net}
            else:
                raise SimError(f"Op {name} is not available on the practice island.")
            b = res["before"] or {}
            res["diff"] = [{"field": k, "before": b.get(k), "after": v} for k, v in (res["after"] or {}).items() if b.get(k) != v]
        except (SimError, KeyError, ValueError) as err:
            res.update(status="error", before=None, after=None, diff=[],
                       error={"code": "validation_failed", "message": str(err) if isinstance(err, SimError) else f"That change does not fit the practice island ({err}).", "details": None})
        out.append(res)
    return out
