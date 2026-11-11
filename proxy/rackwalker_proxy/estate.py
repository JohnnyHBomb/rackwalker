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

The connectivity graph (`Estate.components`) joins: every interface of a network device to that device (any other
device is an end: a server, a wireless access point, a box whose role says nothing. Traffic ends there, so two switches
that share only a dual-homed server are not linked); the members of a virtual chassis (a switch stack) to each other; a
patch panel's front port to the position of the rear port it maps to (the strands of a trunk are separate wires); the
two ends of a cable; the terminations of a circuit, and a provider network to every circuit that ends on it (sites that
hang off one carrier network are joined through it). A decommissioned circuit joins nothing. Power ports, outlets and
feeds are a separate graph (`powered`).

What a device is comes from its role's name and slug, word by word (`NETWORK_WORDS`, `END_WORDS`, ...). A role the
words do not know ("ToR", "Kit") still counts as a network device when the device has `MANY_PORTS` or more physical
interfaces: a box with a row of ports is a switch, whatever its role is called.
"""
from __future__ import annotations

import asyncio
import copy
import ipaddress
import re
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
NETWORK_WORDS = ("switch", "router", "firewall", "core", "access", "distribution", "gateway", "wan", "edge", "spine", "leaf", "tor",
                 "network", "aggregation", "agg")
ROUTER_WORDS = ("router", "gateway", "wan", "edge", "firewall")
SWITCH_WORDS = ("switch", "access", "core", "distribution", "spine", "leaf", "tor", "aggregation", "agg")
PASSIVE_WORDS = ("patch", "panel", "pdu", "power", "cable", "shelf", "blank")
WIRELESS_WORDS = ("ap", "aps", "wireless", "wifi", "wlan")
END_WORDS = ("server", "host", "storage", "hypervisor", "compute", "workstation", "desktop", "printer", "camera", "phone", "nas", "san")
# A device with this many physical interfaces is a network device even when its role's name says nothing.
MANY_PORTS = 8


# How a device is managed (custom field rw_mgmt_mode on a device; rw_mgmt_default on its device type, which may also
# say `varies`: the model can be either, so each device is asked).
MGMT_MODES = ("local", "cloud", "unmanaged")
MGMT_DEFAULTS = (*MGMT_MODES, "varies")
# A circuit in one of these states carries nothing: it is no way out, joins nothing and is asked nothing about.
DEAD_CIRCUIT = ("decommissioned",)
# At most this many list reads of one estate read are under way at NetBox at a time. NetBox holds a database
# connection per request it is working on; twenty-odd lists asked for at once can use up a small database's connections.
READ_CONCURRENCY = 4
# The ids of what the practice island's quests create sit above this, like the island's own ids: never an id NetBox uses.
PRACTICE_BASE = 9_000_000
# Custom fields of the game that an estate read looks for, and the list whose rows carry them.
GAME_FIELDS = {"devices": ("rw_mgmt_mode", "rw_mgmt_service"), "circuits": ("rw_mrc", "rw_nrc", "rw_currency")}


def port_key(s: str) -> PortKey:
    kind, _, oid = s.rpartition(":")
    return kind, int(oid)


def is_physical(port: dict[str, Any]) -> bool:
    t = port.get("type") or ""
    return port["kind"] != "interface" or not (t in NON_PHYSICAL or t.startswith("ieee802.11") or t.startswith("virtual"))


def role_words(dev: dict[str, Any]) -> list[str]:
    return [w for w in re.split(r"[^a-z0-9]+", f"{dev.get('role') or ''} {dev.get('role_name') or ''}".lower()) if w]


def role_is(dev: dict[str, Any], words: tuple[str, ...]) -> bool:
    """A role names one of `words`: a short word (up to three letters) as a whole word of the role's name or slug, a
    longer one as the start of a word ("switches", "routers")."""
    have = role_words(dev)
    return any((h == w if len(w) <= 3 else h.startswith(w)) for w in words for h in have)


def is_wireless(dev: dict[str, Any]) -> bool:
    have = role_words(dev)
    return role_is(dev, WIRELESS_WORDS) or ("access" in have and any(h.startswith("point") for h in have))


def is_network(dev: dict[str, Any]) -> bool:
    """By the role's words alone; `Estate.is_network` also looks at the device's ports."""
    return role_is(dev, NETWORK_WORDS) and not is_wireless(dev)


def is_router(dev: dict[str, Any]) -> bool:
    return role_is(dev, ROUTER_WORDS) and not is_wireless(dev)


def is_switch(dev: dict[str, Any]) -> bool:
    return role_is(dev, SWITCH_WORDS) and not is_wireless(dev)


def is_passive(dev: dict[str, Any]) -> bool:
    return role_is(dev, PASSIVE_WORDS)


def is_end_host(dev: dict[str, Any]) -> bool:
    """Traffic ends here: a wireless access point, or a role that names a server and no network device."""
    return is_wireless(dev) or (role_is(dev, END_WORDS) and not role_is(dev, NETWORK_WORDS))


def name_of(dev: dict[str, Any]) -> str:
    """A device's name for a sentence. NetBox allows a device with no name: it is called by what it is and its id."""
    if dev.get("name"):
        return dev["name"]
    what = (dev.get("model") or dev.get("role_name") or "device").strip()
    return f"the unnamed {what} (device {dev['id']})"


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
    # the administrative record of a circuit: provider accounts and networks, contacts with their roles, and who is
    # assigned to which provider or circuit
    accounts: dict[int, dict[str, Any]] = field(default_factory=dict)
    networks: dict[int, dict[str, Any]] = field(default_factory=dict)
    contacts: dict[int, dict[str, Any]] = field(default_factory=dict)
    contact_roles: dict[int, dict[str, Any]] = field(default_factory=dict)
    assignments: dict[int, dict[str, Any]] = field(default_factory=dict)
    # for the setup companion: every device type and device role NetBox holds (also those no device uses yet), and
    # the module bays of the devices with what is fitted in them
    dtypes: dict[int, dict[str, Any]] = field(default_factory=dict)
    roles: dict[int, dict[str, Any]] = field(default_factory=dict)
    module_bays: dict[int, dict[str, Any]] = field(default_factory=dict)
    # custom fields the game uses that this NetBox does not have (devnetbox/seed.py --fields-only adds them): what
    # they would hold is not asked for, and never counted as missing
    no_fields: list[str] = field(default_factory=list)
    truncated: list[str] = field(default_factory=list)
    unreadable: list[str] = field(default_factory=list)
    synthetic: bool = False
    # Derived look-ups, made on first use and dropped by `touch()` whenever the estate is changed in memory: a large
    # NetBox has hundreds of thousands of ports, and no rule may walk all of them once per device.
    _memo: dict[str, Any] = field(default_factory=dict, repr=False, compare=False)

    def touch(self) -> None:
        """The estate was changed in memory (the practice island): every derived look-up is made again."""
        self._memo.clear()

    def _ports_by_device(self) -> dict[int, list[dict[str, Any]]]:
        idx = self._memo.get("ports")
        if idx is None:
            idx = {}
            for p in self.ports.values():
                idx.setdefault(p["device_id"], []).append(p)
            self._memo["ports"] = idx
        return idx

    def _devices_by_site(self) -> dict[int, list[dict[str, Any]]]:
        idx = self._memo.get("devices")
        if idx is None:
            idx = {}
            for _, d in sorted(self.devices.items()):
                idx.setdefault(d["site_id"], []).append(d)
            self._memo["devices"] = idx
        return idx

    def _physical(self) -> dict[int, int]:
        """Per device: how many physical interfaces it has."""
        idx = self._memo.get("physical")
        if idx is None:
            idx = {}
            for p in self.ports.values():
                if p["kind"] == "interface" and is_physical(p):
                    idx[p["device_id"]] = idx.get(p["device_id"], 0) + 1
            self._memo["physical"] = idx
        return idx

    def is_network(self, dev: dict[str, Any]) -> bool:
        """A switch, router or firewall: by its role's words, or (a role the words do not know) by a row of ports."""
        if is_network(dev):
            return True
        return not is_passive(dev) and not is_end_host(dev) and self._physical().get(dev["id"], 0) >= MANY_PORTS

    def is_switch(self, dev: dict[str, Any]) -> bool:
        return is_switch(dev) or (self.is_network(dev) and not is_network(dev))

    def _ips_by_interface(self) -> dict[int, list[dict[str, Any]]]:
        idx = self._memo.get("ips")
        if idx is None:
            idx = {}
            for _, ip in sorted(self.ips.items()):
                if ip["interface_id"] is not None:
                    idx.setdefault(ip["interface_id"], []).append(ip)
            self._memo["ips"] = idx
        return idx

    # ---------------------------------------------------------------- (de)serialisation
    TABLES = ("sites", "locations", "racks", "devices", "cables", "circuits", "providers", "circuit_types", "cterms", "feeds",
              "platforms", "ips", "prefixes", "accounts", "networks", "contacts", "contact_roles", "assignments", "dtypes", "roles", "module_bays")

    def to_json(self) -> dict[str, Any]:
        out: dict[str, Any] = {t: [getattr(self, t)[k] for k in sorted(getattr(self, t))] for t in self.TABLES}
        out["ports"] = [self.ports[k] for k in sorted(self.ports)]
        out.update(truncated=self.truncated, unreadable=self.unreadable, synthetic=self.synthetic, no_fields=self.no_fields)
        return out

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> "Estate":
        e = cls(truncated=list(data.get("truncated", [])), unreadable=list(data.get("unreadable", [])),
                synthetic=bool(data.get("synthetic", False)), no_fields=list(data.get("no_fields", [])))
        for t in cls.TABLES:
            setattr(e, t, {row["id"]: _tuples(row) for row in data.get(t, [])})
        e.ports = {(p["kind"], p["id"]): _tuples(p) for p in data.get("ports", [])}
        return e

    def copy(self) -> "Estate":
        e = Estate(truncated=list(self.truncated), unreadable=list(self.unreadable), synthetic=self.synthetic, no_fields=list(self.no_fields))
        for t in self.TABLES:
            setattr(e, t, copy.deepcopy(getattr(self, t)))
        e.ports = copy.deepcopy(self.ports)
        return e

    # ---------------------------------------------------------------- lookups
    def can_see(self, *lists: str) -> bool:
        """True when every named list was read in full."""
        return not any(name in self.truncated or name in self.unreadable for name in lists)

    def site_devices(self, site_id: int) -> list[dict[str, Any]]:
        return list(self._devices_by_site().get(site_id, []))

    def device_ports(self, device_id: int, kinds: tuple[str, ...] = DATA_PORTS) -> list[dict[str, Any]]:
        """A device's ports of these kinds in natural order. Kept per device: a question may look at every device."""
        memo = self._memo.setdefault("device_ports", {})
        got = memo.get((device_id, kinds))
        if got is None:
            got = memo[(device_id, kinds)] = sorted((p for p in self._ports_by_device().get(device_id, []) if p["kind"] in kinds),
                                                    key=lambda p: (kinds.index(p["kind"]), natural(p["name"]), p["id"]))
        return list(got)

    def free_ports(self, device_id: int) -> list[dict[str, Any]]:
        """Physical interfaces of a device with no cable, likeliest uplink first: non-management, then by name."""
        memo = self._memo.setdefault("free_ports", {})
        got = memo.get(device_id)
        if got is None:
            ports = [p for p in self.device_ports(device_id, ("interface",)) if p["cable_id"] is None and is_physical(p) and p["enabled"]]
            got = memo[device_id] = sorted(ports, key=lambda p: (p["mgmt_only"], natural(p["name"]), p["id"]))
        return list(got)

    def has_free_port(self, device_id: int) -> bool:
        memo = self._memo.setdefault("has_free", {})
        if device_id not in memo:
            memo[device_id] = any(p["kind"] == "interface" and p["cable_id"] is None and p["enabled"] and is_physical(p)
                                  for p in self._ports_by_device().get(device_id, []))
        return memo[device_id]

    def device_name(self, device_id: int) -> str:
        d = self.devices.get(device_id)
        return name_of(d) if d else f"device {device_id}"

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
        comp = self._memo.get("components")
        if comp is None:
            comp = self._memo["components"] = Components(self)
        return comp

    def powered(self, device_id: int) -> bool | None:
        """True when a power port of the device has a cable; None when it has no power ports recorded."""
        pp = [p for p in self._ports_by_device().get(device_id, []) if p["kind"] == "power_port"]
        return any(p["cable_id"] is not None for p in pp) if pp else None

    def device_ips(self, device_id: int) -> list[dict[str, Any]]:
        by_iface = self._ips_by_interface()
        out = [ip for p in self._ports_by_device().get(device_id, []) if p["kind"] == "interface" for ip in by_iface.get(p["id"], [])]
        return sorted(out, key=lambda ip: ip["id"])

    # ---------------------------------------------------------------- how a device is managed; a circuit's record
    def has_field(self, name: str) -> bool:
        return name not in self.no_fields

    def mgmt_mode(self, dev: dict[str, Any]) -> str | None:
        """How a device is managed, as far as NetBox says: `local` (it has an address to log in to), `cloud` (a cloud
        or controller service manages it: no local address is expected) or `unmanaged`; None when NetBox does not say.
        The device's own field wins; else the default of its model, unless that says the model varies; a device with
        a primary IP and neither is locally managed (NetBox already says how to reach it)."""
        return self.mgmt_recorded(dev) or ("local" if dev.get("primary_ip") else None)

    def mgmt_recorded(self, dev: dict[str, Any]) -> str | None:
        """How a device is managed as somebody recorded it: its own field, else the default of its model (unless that
        says the model varies). A primary IP alone is no such record."""
        own = dev.get("mgmt_mode")
        if own in MGMT_MODES:
            return own
        default = dev.get("mgmt_default")
        return default if default in MGMT_MODES else None

    def mgmt_service(self, dev: dict[str, Any]) -> str | None:
        return dev.get("mgmt_service") or (dev.get("mgmt_default_service") if dev.get("mgmt_mode") is None else None) or None

    def primary_interface(self, dev: dict[str, Any]) -> int | None:
        """The interface that carries a device's primary IP (its management address), if NetBox has both."""
        if not dev.get("primary_ip"):
            return None
        return next((ip["interface_id"] for ip in self.device_ips(dev["id"]) if ip["address"] == dev["primary_ip"]), None)

    def live_circuit(self, circuit_id: int) -> bool:
        c = self.circuits.get(circuit_id)
        return c is not None and c.get("status") not in DEAD_CIRCUIT

    def _far(self, port: dict[str, Any]) -> PortKey | None:
        """The other end of the cable in a port (the first end that is not this port)."""
        cable = self.cables.get(port["cable_id"] or 0)
        me = (port["kind"], port["id"])
        return next((tuple(k) for k in (list(cable["a"]) + list(cable["b"])) if tuple(k) != me), None) if cable else None  # type: ignore[misc]

    def _front_of(self, rear_id: int, position: int) -> dict[str, Any] | None:
        idx = self._memo.get("fronts")
        if idx is None:
            idx = self._memo["fronts"] = {m: p for p in self.ports.values() if p["kind"] == "front_port" for m in rear_map(p)}
        return idx.get((rear_id, position))

    def through(self, key: PortKey) -> tuple[str, dict[str, Any] | None]:
        """Where a lead that is plugged into `key` ends up when patch panels are followed (front port, the strand of the
        rear port it maps to, the trunk, the front port at the far panel, and so on):
            ("interface", port)  an interface of a device
            ("open", port)       a panel port with no cable in it: the path stops there, and a cable can go on from it
            ("stuck", port)      it cannot be followed (no mapping, a trunk of several strands plugged into one port)"""
        port = self.ports.get(key)
        for _ in range(32):
            if port is None:
                return "stuck", None
            if port["kind"] == "interface":
                return "interface", port
            if port["kind"] == "front_port":
                maps = rear_map(port)
                rear = self.ports.get(("rear_port", maps[0][0])) if maps else None
                if rear is None:
                    return "stuck", port
                lane = maps[0][1]
                if rear["cable_id"] is None:
                    return ("open", rear) if rear.get("positions", 1) == 1 else ("stuck", rear)
                far = self.ports.get(self._far(rear) or ("", 0))
                if far is None:
                    return "stuck", rear
                if far["kind"] != "rear_port":
                    if rear.get("positions", 1) != 1:
                        return "stuck", rear
                    port = far
                    continue
                front = self._front_of(far["id"], lane)
            elif port["kind"] == "rear_port":
                front = self._front_of(port["id"], 1) if port.get("positions", 1) == 1 else None
            else:
                return "stuck", port
            if front is None:
                return "stuck", port
            if front["cable_id"] is None:
                return "open", front
            port = self.ports.get(self._far(front) or ("", 0))
        return "stuck", None

    def circuit_handoff(self, circuit_id: int) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]] | None:
        """Where a circuit plugs in: (termination, port, device) of its first end, by side, that is cabled to a port.
        The port is the interface of the device the line reaches, through any patch panels on the way (a demarc panel,
        then a lead to the router); when the panels lead to no device, it is the panel port the circuit is cabled to."""
        memo = self._memo.setdefault("handoff", {})
        if circuit_id not in memo:
            memo[circuit_id] = self._circuit_handoff(circuit_id)
        return memo[circuit_id]

    def _circuit_handoff(self, circuit_id: int) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]] | None:
        for t in sorted((t for t in self._cterms_of(circuit_id) if t["cable_id"] is not None), key=lambda t: t["side"] or ""):
            cable = self.cables.get(t["cable_id"])
            for key in (list(cable["a"]) + list(cable["b"])) if cable else []:
                port = self.ports.get(tuple(key))  # type: ignore[arg-type]
                if port is not None and port["device_id"] in self.devices:
                    how, end = self.through(tuple(key))  # type: ignore[arg-type]
                    if how == "interface" and end is not None and end["device_id"] in self.devices:
                        return t, end, self.devices[end["device_id"]]
                    return t, port, self.devices[port["device_id"]]
        return None

    def _cterms_of(self, circuit_id: int) -> list[dict[str, Any]]:
        idx = self._memo.get("cterms")
        if idx is None:
            idx = {}
            for _, t in sorted(self.cterms.items()):
                idx.setdefault(t["circuit_id"], []).append(t)
            self._memo["cterms"] = idx
        return idx.get(circuit_id, [])

    def contacts_of(self, circuit_id: int) -> list[dict[str, Any]]:
        """Contact assignments that speak for a circuit: its own, then its provider's. Each with `contact`, `role`."""
        c = self.circuits.get(circuit_id)
        if c is None:
            return []
        keys = [("circuits.circuit", circuit_id), ("circuits.provider", c["provider_id"])]
        idx = self._memo.get("assigned")
        if idx is None:
            idx = {}
            for _, a in sorted(self.assignments.items()):
                idx.setdefault((a["object_type"], a["object_id"]), []).append(a)
            self._memo["assigned"] = idx
        out = []
        for key in keys:
            for a in idx.get(key, []):
                if a["contact_id"] in self.contacts:
                    out.append({**a, "contact": self.contacts[a["contact_id"]], "role": self.contact_roles.get(a["role_id"] or 0, {}).get("name")})
        return out

    # ---------------------------------------------------------------- simulated writes (practice island)
    def next_id(self, table: str) -> int:
        rows = self.ports if table == "ports" else getattr(self, table)
        ids = [k[1] if isinstance(k, tuple) else k for k in rows]
        return max(max(ids, default=0), PRACTICE_BASE if self.synthetic else 0) + 1


def _tuples(row: dict[str, Any]) -> dict[str, Any]:
    """JSON has no tuples: cable ends and port maps come back as lists."""
    out = dict(row)
    for k in ("a", "b"):
        if k in out and isinstance(out[k], list):
            out[k] = [tuple(x) for x in out[k]]
    return out


def rear_map(front: dict[str, Any]) -> list[tuple[int, int]]:
    """The (rear port id, position) pairs a front port maps to. Data from before positions were read has the rear ports only."""
    if front.get("rear") is not None:
        return [(int(r), int(n)) for r, n in front["rear"]]
    return [(r, 1) for r in front.get("rear_ids") or []]


def natural(name: str) -> tuple[Any, ...]:
    import re

    return tuple((0, int(p)) if p.isdigit() else (1, p) for p in re.split(r"(\d+)", name) if p != "")


class Components:
    """Connected components of the data graph. Nodes are devices ("d", id), pass-through ports and circuits."""

    def __init__(self, e: Estate):
        self.e = e
        self.parent: dict[Any, Any] = {}
        self._lists: tuple[dict[Any, list[int]], dict[Any, list[int]]] | None = None
        # Only a network device passes traffic on. A server, an access point, or a box whose role says nothing and that
        # has no row of ports (a database, an appliance, a console server) is an end, however many leads it has.
        self.end_hosts = {did for did, d in e.devices.items() if not e.is_network(d) and not is_passive(d)}
        for did, d in e.devices.items():
            self._find(("d", did))
            if d.get("vc_id") is not None:
                self._union(("d", did), ("v", d["vc_id"]))   # a stack is one switch
        for key, p in e.ports.items():
            if p["kind"] == "interface":
                continue
            if p["kind"] == "front_port":
                for rear, lane in rear_map(p):
                    self._union(("p", "front_port", p["id"]), ("r", rear, lane))
        for c in e.cables.values():
            # Each end as its wires: one for an interface, a front port or a circuit's end; one per position for a
            # rear port (a trunk). A trunk joins position to position; where a trunk of several strands meets a
            # single wire, nothing says which strand it is, so nothing is joined.
            ends = [w for w in (self.wires(tuple(k)) for k in list(c["a"]) + list(c["b"])) if w]  # type: ignore[arg-type]
            width = max((len(w) for w in ends), default=0)
            for lane in range(width):
                same = [w[lane] for w in ends if len(w) == width]
                for n in same[1:]:
                    self._union(same[0], n)
        for t in e.cterms.values():
            if not e.live_circuit(t["circuit_id"]):
                continue
            self._union(("c", t["circuit_id"]), ("t", t["id"]))
            if t.get("network_id") is not None:
                self._union(("c", t["circuit_id"]), ("n", t["network_id"]))   # sites on one carrier network are joined by it

    def wires(self, key: PortKey) -> list[Any]:
        if key[0] == "rear_port":
            p = self.e.ports.get(key)
            return [("r", key[1], lane) for lane in range(1, (p.get("positions", 1) if p else 1) + 1)]
        n = self.node(key)
        return [n] if n is not None else []

    def node(self, key: PortKey) -> Any:
        kind, oid = key
        if kind == "interface":
            p = self.e.ports.get(key)
            if p is None:
                return None
            # a port of an end host is an end, not a way through: its other ports are not reached by it
            return ("i", oid) if p["device_id"] in self.end_hosts else ("d", p["device_id"])
        if kind == "front_port":
            return ("p", kind, oid)
        if kind == "rear_port":
            return ("r", oid, 1)
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

    def _members(self) -> tuple[dict[Any, list[int]], dict[Any, list[int]]]:
        if self._lists is None:
            devs: dict[Any, list[int]] = {}
            circs: dict[Any, list[int]] = {}
            for d in sorted(self.e.devices):
                devs.setdefault(self._find(("d", d)), []).append(d)
            for c in sorted(self.e.circuits):
                circs.setdefault(self._find(("c", c)), []).append(c)
            self._lists = (devs, circs)
        return self._lists

    def devices_in(self, root: Any) -> list[int]:
        return list(self._members()[0].get(root, []))

    def circuits_in(self, root: Any) -> list[int]:
        return list(self._members()[1].get(root, []))

    def degree(self, device_id: int) -> int:
        return sum(1 for p in self.e._ports_by_device().get(device_id, []) if p["kind"] in DATA_PORTS and p["cable_id"] is not None)


# ---------------------------------------------------------------- reading NetBox
async def _rows(client: nb.NetBox, e: Estate, name: str, path: str, fields: str, cap: int,
                where: tuple[tuple[str, Any], ...] = ()) -> list[dict[str, Any]]:
    """A whole list (or the part of it `where` selects) with a slim field selection, up to `cap` rows. A list the
    proxy's token may not view is recorded in `e.unreadable` and read as empty."""
    out: list[dict[str, Any]] = []
    try:
        while True:
            page = await client.request("GET", path + "/", params=[("fields", fields), *where, ("limit", nb.PAGE), ("offset", len(out))])
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


def _money(v: Any) -> str | None:
    """An amount as NetBox gave it (a decimal custom field reads back as a number or a string), as plain text with two
    decimals; never a float that is then rounded by arithmetic."""
    if v is None or v == "":
        return None
    from decimal import Decimal, InvalidOperation

    try:
        return f"{Decimal(str(v)):.2f}"
    except InvalidOperation:
        return None


# Lists NetBox can select by site (`site_id=`); every other list is small and is always read whole.
BY_SITE = ("locations", "racks", "devices", "cables", "feeds", "ports", "module_bays")
# Addresses have no site filter: for a few sites they are read by device, this many devices to a request.
IP_CHUNK = 100


async def read_estate(client: nb.NetBox, site_ids: list[int] | None = None) -> Estate:
    """One bounded read of everything the scanner looks at. With `site_ids`, the lists NetBox can select by site are
    read for those sites only (the answer is a part to `splice` into an estate read earlier): what a quest at one
    site changed is read again without reading every other site again."""
    await client.version()
    e = Estate()
    here = tuple(("site_id", s) for s in site_ids or [])

    def where(name: str) -> tuple[tuple[str, Any], ...]:
        if name == "assignments":
            # only who speaks for a provider or a circuit: a large NetBox has contacts on every tenant and site
            return (("object_type", "circuits.provider"), ("object_type", "circuits.circuit"))
        return here if name in BY_SITE else ()
    port_fields = {
        "interface": ("dcim/interfaces", "id,name,device,type,enabled,mgmt_only,cable,description,mark_connected"),
        "front_port": ("dcim/front-ports", "id,name,device,cable,rear_ports,description"),
        "rear_port": ("dcim/rear-ports", "id,name,device,cable,description,positions"),
        "power_port": ("dcim/power-ports", "id,name,device,cable,description"),
        "power_outlet": ("dcim/power-outlets", "id,name,device,cable,description"),
    }
    jobs = {
        "sites": ("dcim/sites", "id,name,slug,status,latitude,longitude,facility,region,physical_address,custom_fields,last_updated", CAPS["sites"]),
        "locations": ("dcim/locations", "id,name,site", CAPS["locations"]),
        "racks": ("dcim/racks", "id,name,site,location,u_height,custom_fields,last_updated", CAPS["racks"]),
        "devices": ("dcim/devices", "id,name,site,location,rack,position,face,role,device_type,status,serial,asset_tag,platform,primary_ip,virtual_chassis,custom_fields,last_updated,tags", CAPS["devices"]),
        "cables": ("dcim/cables", "id,type,label,status,a_terminations,b_terminations", CAPS["cables"]),
        "circuits": ("circuits/circuits", "id,cid,provider,provider_account,type,status,install_date,commit_rate,custom_fields,last_updated", CAPS["circuits"]),
        "accounts": ("circuits/provider-accounts", "id,provider,account,name", CAPS["circuits"]),
        "networks": ("circuits/provider-networks", "id,provider,name", CAPS["circuits"]),
        "contacts": ("tenancy/contacts", "id,name,phone,email", CAPS["circuits"]),
        "contact_roles": ("tenancy/contact-roles", "id,name,slug", CAPS["circuits"]),
        "assignments": ("tenancy/contact-assignments", "id,object_type,object_id,contact,role", CAPS["circuits"]),
        "providers": ("circuits/providers", "id,name,slug,asns", CAPS["circuits"]),
        "circuit_types": ("circuits/circuit-types", "id,name", CAPS["circuits"]),
        "cterms": ("circuits/circuit-terminations", "id,circuit,term_side,termination_type,termination_id,cable", CAPS["circuits"]),
        "feeds": ("dcim/power-feeds", "id,name,rack,cable", CAPS["circuits"]),
        "platforms": ("dcim/platforms", "id,name,manufacturer", CAPS["circuits"]),
        "ips": ("ipam/ip-addresses", "id,address,vrf,assigned_object_type,assigned_object_id", CAPS["ips"]),
        "prefixes": ("ipam/prefixes", "id,prefix,vrf", CAPS["prefixes"]),
        "dtypes": ("dcim/device-types", "id,manufacturer,model,slug,part_number,u_height,tags", CAPS["devices"]),
        "roles": ("dcim/device-roles", "id,name,slug", CAPS["circuits"]),
        "module_bays": ("dcim/module-bays", "id,name,device,installed_module", CAPS["ports"]),
    }
    names = [n for n in jobs if not (site_ids is not None and n == "ips")]
    slots = asyncio.Semaphore(READ_CONCURRENCY)

    async def rows(*args: Any) -> list[dict[str, Any]]:
        async with slots:
            return await _rows(client, e, *args)
    got = await asyncio.gather(*(rows(n, *jobs[n], where(n)) for n in names),
                               *(rows("ports", p, f, CAPS["ports"], here) for p, f in port_fields.values()))
    raw = dict(zip(names, got[:len(names)]))
    raw_ports = dict(zip(port_fields, got[len(names):]))
    if site_ids is not None:
        ids = [d["id"] for d in raw["devices"]]
        path, fields, cap = jobs["ips"]
        parts = await asyncio.gather(*(rows("ips", path, fields, cap, tuple(("device_id", i) for i in ids[n:n + IP_CHUNK]))
                                       for n in range(0, len(ids), IP_CHUNK)))
        raw["ips"] = [row for part in parts for row in part]

    for s in raw["sites"]:
        e.sites[s["id"]] = {
            "id": s["id"], "name": s["name"], "slug": s.get("slug"), "status": _val(s.get("status")),
            "latitude": s.get("latitude"), "longitude": s.get("longitude"), "facility": s.get("facility") or None,
            "country": country_mod.site_country(_cf(s, "iso_country"), (s.get("region") or {}).get("slug"),
                                                (s.get("region") or {}).get("name"), s.get("physical_address")),
            "last_updated": s.get("last_updated")}
    for loc in raw["locations"]:
        e.locations[loc["id"]] = {"id": loc["id"], "name": loc["name"], "site_id": _id(loc.get("site"))}
    for r in raw["racks"]:
        e.racks[r["id"]] = {"id": r["id"], "name": r["name"], "site_id": _id(r.get("site")), "location_id": _id(r.get("location")),
                            "u_height": r.get("u_height") or 42, "iso_x": _cf(r, "iso_x"), "iso_y": _cf(r, "iso_y"),
                            "last_updated": r.get("last_updated")}
    dtypes = await client.device_types({_id(d.get("device_type")) for d in raw["devices"]} - {None})  # type: ignore[arg-type]
    # A custom field NetBox does not have is absent from every row's `custom_fields`: with at least one row to look at,
    # a missing name means the field was never created.
    for name, fields in GAME_FIELDS.items():
        if raw[name]:
            have = raw[name][0].get("custom_fields") or {}
            e.no_fields += [f for f in fields if f not in have]
    if dtypes and "rw_mgmt_default" not in next(iter(dtypes.values())).custom_fields:
        e.no_fields.append("rw_mgmt_default")
    e.no_fields.sort()
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
            "manufacturer": dt.manufacturer.name if dt else None, "vc_id": _id(d.get("virtual_chassis")),
            "type_id": dt.id if dt else None, "mgmt_mode": _cf(d, "rw_mgmt_mode"), "mgmt_service": _cf(d, "rw_mgmt_service") or None,
            "mgmt_default": _val(dt.custom_fields.get("rw_mgmt_default")) if dt else None,
            "mgmt_default_service": (_val(dt.custom_fields.get("rw_mgmt_service")) or None) if dt else None,
            "tags": sorted(t.get("slug") for t in d.get("tags") or [] if isinstance(t, dict) and t.get("slug")),
            "last_updated": d.get("last_updated")}
    for kind, rows in raw_ports.items():
        for p in rows:
            did = _id(p.get("device"))
            if did not in e.devices:
                continue
            e.ports[(kind, p["id"])] = {
                "kind": kind, "id": p["id"], "device_id": did, "name": p["name"], "type": _val(p.get("type")),
                "enabled": p.get("enabled", True), "mgmt_only": bool(p.get("mgmt_only", False)), "cable_id": _id(p.get("cable")),
                "description": p.get("description") or "", "mark_connected": bool(p.get("mark_connected", False)),
                "rear_ids": [m["rear_port"] for m in p.get("rear_ports") or [] if isinstance(m, dict) and isinstance(m.get("rear_port"), int)],
                # a front port is one strand of the rear port it maps to: (rear port, position on it)
                "rear": [(m["rear_port"], m.get("rear_port_position") or 1) for m in p.get("rear_ports") or []
                         if isinstance(m, dict) and isinstance(m.get("rear_port"), int)],
                "positions": p.get("positions") or 1}
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
        e.providers[p["id"]] = {"id": p["id"], "name": p["name"], "slug": p.get("slug") or None,
                                "asns": sorted(a["asn"] for a in p.get("asns") or [] if isinstance(a, dict) and isinstance(a.get("asn"), int))}
    for t in raw["circuit_types"]:
        e.circuit_types[t["id"]] = {"id": t["id"], "name": t["name"]}
    for c in raw["circuits"]:
        e.circuits[c["id"]] = {"id": c["id"], "cid": c["cid"], "provider_id": _id(c.get("provider")),
                               "type": (c.get("type") or {}).get("name"), "status": _val(c.get("status")),
                               "account_id": _id(c.get("provider_account")), "install_date": c.get("install_date"),
                               "commit_rate": c.get("commit_rate"), "mrc": _money(_cf(c, "rw_mrc")), "nrc": _money(_cf(c, "rw_nrc")),
                               "currency": _cf(c, "rw_currency") or None, "last_updated": c.get("last_updated")}
    for t in raw["cterms"]:
        where_type, where_id = t.get("termination_type"), t.get("termination_id")
        # an end recorded on a room is at that room's site (when only some sites were read, the room may be unseen)
        at_site = where_id if where_type == "dcim.site" else e.locations.get(where_id or 0, {}).get("site_id") if where_type == "dcim.location" else None
        e.cterms[t["id"]] = {"id": t["id"], "circuit_id": _id(t.get("circuit")), "side": t.get("term_side"),
                             "site_id": at_site, "location_id": where_id if where_type == "dcim.location" else None,
                             "network_id": t.get("termination_id") if t.get("termination_type") == "circuits.providernetwork" else None,
                             "cable_id": _id(t.get("cable"))}
    for r in raw["accounts"]:
        e.accounts[r["id"]] = {"id": r["id"], "provider_id": _id(r.get("provider")), "account": r.get("account") or "", "name": r.get("name") or ""}
    for r in raw["networks"]:
        e.networks[r["id"]] = {"id": r["id"], "provider_id": _id(r.get("provider")), "name": r["name"]}
    for r in raw["contacts"]:
        e.contacts[r["id"]] = {"id": r["id"], "name": r["name"], "phone": r.get("phone") or "", "email": r.get("email") or ""}
    for r in raw["contact_roles"]:
        e.contact_roles[r["id"]] = {"id": r["id"], "name": r["name"], "slug": r.get("slug") or ""}
    for r in raw["assignments"]:
        e.assignments[r["id"]] = {"id": r["id"], "object_type": r.get("object_type"), "object_id": r.get("object_id"),
                                  "contact_id": _id(r.get("contact")), "role_id": _id(r.get("role"))}
    for f in raw["feeds"]:
        e.feeds[f["id"]] = {"id": f["id"], "name": f["name"], "rack_id": _id(f.get("rack")), "cable_id": _id(f.get("cable"))}
    for p in raw["platforms"]:
        e.platforms[p["id"]] = {"id": p["id"], "name": p["name"], "manufacturer": (p.get("manufacturer") or {}).get("name")}
    for ip in raw["ips"]:
        e.ips[ip["id"]] = {"id": ip["id"], "address": ip["address"], "vrf_id": _id(ip.get("vrf")),
                           "vrf": (ip.get("vrf") or {}).get("name"),
                           "interface_id": ip.get("assigned_object_id") if ip.get("assigned_object_type") == "dcim.interface" else None}
    for p in raw["prefixes"]:
        e.prefixes[p["id"]] = {"id": p["id"], "prefix": p["prefix"], "vrf_id": _id(p.get("vrf"))}
    for t in raw["dtypes"]:
        e.dtypes[t["id"]] = {"id": t["id"], "manufacturer": (t.get("manufacturer") or {}).get("name"), "model": t.get("model") or "",
                             "slug": t.get("slug") or "", "part_number": t.get("part_number") or "", "u_height": float(t.get("u_height") or 0),
                             "tags": sorted(x.get("slug") for x in t.get("tags") or [] if isinstance(x, dict) and x.get("slug"))}
    for r in raw["roles"]:
        e.roles[r["id"]] = {"id": r["id"], "name": r["name"], "slug": r.get("slug") or ""}
    for b in raw["module_bays"]:
        did = _id(b.get("device"))
        if did in e.devices:
            e.module_bays[b["id"]] = {"id": b["id"], "device_id": did, "name": b["name"], "module_id": _id(b.get("installed_module"))}
    return e


def left(base: Estate, part: Estate, site_ids: list[int]) -> bool:
    """True when something `base` had at `site_ids` is not in `part` (a read of those sites): it was deleted, or it
    moved to a site that was not read. A splice would drop it from the estate, so the whole estate is read instead."""
    here = set(site_ids)
    return any(r["site_id"] in here and i not in getattr(part, t) for t in ("locations", "racks", "devices") for i, r in getattr(base, t).items())


def splice(base: Estate, part: Estate, site_ids: list[int]) -> Estate:
    """A new estate: `base` with everything of `site_ids` replaced by `part` (a `read_estate` of those sites), and
    the small estate-wide lists (sites, circuits and their ends, providers, platforms, prefixes) taken from `part`.
    `base` itself is not changed. A cable between one of the sites and another site is kept right at both ends."""
    here = set(site_ids)
    e = Estate(truncated=sorted(set(base.truncated) | set(part.truncated)), unreadable=sorted(set(base.unreadable) | set(part.unreadable)))
    # which custom fields NetBox lacks is seen on a row: a part with no device or circuit in it keeps what was known
    e.no_fields = sorted(set(part.no_fields) | {f for t, fs in GAME_FIELDS.items() if not getattr(part, t) for f in fs if f in base.no_fields}
                         | ({"rw_mgmt_default"} & set(base.no_fields) if not part.devices else set()))
    for t in ("sites", "circuits", "providers", "circuit_types", "cterms", "platforms", "prefixes", "accounts", "networks", "contacts",
              "contact_roles", "assignments", "dtypes", "roles"):
        setattr(e, t, dict(getattr(part, t)))
    for t in ("locations", "racks", "devices"):
        setattr(e, t, {i: r for i, r in getattr(base, t).items() if r["site_id"] not in here} | getattr(part, t))
    for t in e.cterms.values():
        if t.get("location_id") is not None and t["site_id"] is None:   # an end on a room of a site that was not read again
            t["site_id"] = e.locations.get(t["location_id"], {}).get("site_id")
    old_devices = {i for i, d in base.devices.items() if d["site_id"] in here}
    old_ports = {k for k, p in base.ports.items() if p["device_id"] in old_devices}
    e.module_bays = {i: b for i, b in base.module_bays.items() if b["device_id"] not in old_devices} | part.module_bays
    e.ports = {k: p for k, p in base.ports.items() if k not in old_ports} | part.ports
    e.feeds = {i: f for i, f in base.feeds.items() if base.racks.get(f["rack_id"] or 0, {}).get("site_id") not in here} | part.feeds
    old_ifaces = {k[1] for k in old_ports if k[0] == "interface"}
    e.ips = {i: ip for i, ip in base.ips.items() if ip["interface_id"] not in old_ifaces} | part.ips
    touched = old_ports | set(part.ports)
    gone = {i: c for i, c in base.cables.items() if any(k in touched for k in c["a"] + c["b"])}
    e.cables = {i: c for i, c in base.cables.items() if i not in gone} | part.cables

    def far_end(key: PortKey, cable_id: int | None, only_if: int | None = None) -> None:
        """The end of a cable on a port of another site: that port's row says which cable it has."""
        port = e.ports.get(key)
        if port is not None and key not in part.ports and (only_if is None or port["cable_id"] == only_if):
            e.ports[key] = {**port, "cable_id": cable_id}
    for i, c in gone.items():
        if i not in part.cables:
            for k in c["a"] + c["b"]:
                far_end(k, None, only_if=i)
    for i, c in part.cables.items():
        for k in c["a"] + c["b"]:
            far_end(k, i)
    return e


_TERM_KIND = {"dcim.interface": "interface", "dcim.frontport": "front_port", "dcim.rearport": "rear_port",
              "dcim.powerport": "power_port", "dcim.poweroutlet": "power_outlet", "dcim.powerfeed": "power_feed",
              "circuits.circuittermination": "circuit_termination", "dcim.consoleport": "console_port",
              "dcim.consoleserverport": "console_server_port"}


# ---------------------------------------------------------------- simulated ops (practice island; never NetBox)
class SimError(Exception):
    pass


def setup_simulate(e: Estate, op: dict[str, Any], f: dict[str, Any], res: dict[str, Any], refs: dict[str, tuple[str, int]], resolve: Any) -> bool:
    from . import setupops   # the setup ops live in a module of their own; it needs this one

    return setupops.simulate(e, op, f, res, refs, resolve)


def simulate(e: Estate, ops: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Runs change ops against the estate in memory and returns OpResult-shaped dicts. Used only for the practice
    island: the same op shapes as the real change path, written to nothing but this object."""
    refs: dict[str, tuple[str, int]] = {}
    out: list[dict[str, Any]] = []
    e.touch()

    def resolve(v: Any, kind: str) -> int:
        if isinstance(v, str) and v.startswith("@"):
            if v[1:] not in refs or refs[v[1:]][0] != kind:
                raise SimError(f"Reference {v} does not name an earlier {kind} of this change set.")
            return refs[v[1:]][1]
        return int(v)

    named: set[str] = set()
    for i, op in enumerate(ops):
        name, f, target = op["op"], dict(op.get("fields") or {}), op["target"]
        res = {"index": i, "op": name, "client_ref": op.get("client_ref"), "target": dict(target), "status": "ok",
               "before": None, "after": None, "diff": [], "error": None}
        try:
            if op.get("client_ref"):
                if op["client_ref"] in named:
                    raise SimError(f"client_ref {op['client_ref']!r} is used by an earlier op.")
                named.add(op["client_ref"])
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
                prov, ctype = resolve(f["provider_id"], "provider"), resolve(f["type_id"], "circuit_type")
                if any(c["cid"] == f["cid"] and c["provider_id"] == prov for c in e.circuits.values()):
                    raise SimError(f"Circuit {f['cid']} already exists for that provider.")
                cid = e.next_id("circuits")
                e.circuits[cid] = {"id": cid, "cid": f["cid"], "provider_id": prov,
                                   "type": e.circuit_types[ctype]["name"], "status": "active", "account_id": None,
                                   "install_date": None, "commit_rate": None, "mrc": None, "nrc": None, "currency": None}
                res["target"]["id"] = cid
                res["after"] = dict(f)
                if op.get("client_ref"):
                    refs[op["client_ref"]] = ("circuit", cid)
            elif name == "create_circuit_termination":
                circ = resolve(f["circuit_id"], "circuit")
                if any(t["circuit_id"] == circ and t["side"] == f["term_side"] for t in e.cterms.values()):
                    raise SimError(f"Side {f['term_side']} of that circuit already has a termination.")
                if (f.get("site_id") is None) == (f.get("provider_network_id") is None):
                    raise SimError("A circuit end is at a site or on a provider network: name one of the two.")
                tid = e.next_id("cterms")
                net = resolve(f["provider_network_id"], "provider_network") if f.get("provider_network_id") is not None else None
                if net is not None and net not in e.networks:
                    raise SimError(f"Provider network {net} does not exist.")
                e.cterms[tid] = {"id": tid, "circuit_id": circ, "side": f["term_side"], "site_id": f.get("site_id"), "network_id": net,
                                 "cable_id": None}
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
                    e.ips[iid] = {"id": iid, "address": addr, "interface_id": iface["id"], "vrf_id": None, "vrf": None}
                res["before"] = {"primary_ip": d["primary_ip"]}
                if f.get("primary", True):
                    d["primary_ip"] = addr
                res["after"] = {"address": addr, "interface_id": iface["id"], "primary_ip": d["primary_ip"]}
            elif name == "create_prefix":
                net = str(ipaddress.ip_network(f["prefix"], strict=True))
                if any(p["prefix"] == net and p.get("vrf_id") is None for p in e.prefixes.values()):
                    raise SimError(f"Prefix {net} already exists.")
                pid = e.next_id("prefixes")
                e.prefixes[pid] = {"id": pid, "prefix": net, "vrf_id": None}
                res["target"]["id"] = pid
                res["after"] = {"prefix": net}
            elif name == "set_management":
                d = e.devices[target["id"]]
                res["before"] = {"mode": d.get("mgmt_mode"), **({"service": d.get("mgmt_service")} if "service" in f else {}),
                                 **({"model_default": d.get("mgmt_default")} if "model_default" in f else {})}
                d["mgmt_mode"] = f["mode"]
                if "service" in f:
                    d["mgmt_service"] = f["service"] or None
                if "model_default" in f:
                    for other in e.devices.values():
                        if other.get("type_id") == d.get("type_id") and (d.get("type_id") is not None or other is d):
                            other["mgmt_default"] = f["model_default"]
                            if f["model_default"] == "cloud" and f.get("service"):
                                other["mgmt_default_service"] = f["service"]
                res["after"] = dict(f)
            elif name == "record_circuit":
                circ = e.circuits.get(target["id"])
                if circ is None:
                    raise SimError(f"Circuit {target['id']} does not exist.")
                write = dict(f)
                if "provider_account_id" in write:
                    acct = resolve(write.pop("provider_account_id"), "provider_account")
                    if acct not in e.accounts or e.accounts[acct]["provider_id"] != circ["provider_id"]:
                        raise SimError("That account is not an account with this circuit's provider.")
                    write["account_id"] = acct
                if ("mrc" in write or "nrc" in write) and not (write.get("currency") or circ.get("currency")):
                    raise SimError("A charge needs its currency (three letters, for example GBP).")
                for k in ("mrc", "nrc"):
                    if write.get(k) is not None:
                        write[k] = _money(write[k])
                res["before"] = {k: circ.get("account_id" if k == "provider_account_id" else k) for k in f}
                circ.update(write)
                res["after"] = {k: circ.get("account_id" if k == "provider_account_id" else k) for k in f}
            elif name == "create_provider_account":
                if f["provider_id"] not in e.providers:
                    raise SimError(f"Provider {f['provider_id']} does not exist.")
                if any(a["provider_id"] == f["provider_id"] and a["account"] == f["account"] for a in e.accounts.values()):
                    raise SimError("That provider already has an account with this number.")
                aid = e.next_id("accounts")
                e.accounts[aid] = {"id": aid, "provider_id": f["provider_id"], "account": f["account"], "name": f.get("name") or ""}
                res["target"]["id"] = aid
                res["after"] = dict(f)
                if op.get("client_ref"):
                    refs[op["client_ref"]] = ("provider_account", aid)
            elif name == "create_provider_network":
                if f["provider_id"] not in e.providers:
                    raise SimError(f"Provider {f['provider_id']} does not exist.")
                if any(n["provider_id"] == f["provider_id"] and n["name"].lower() == f["name"].lower() for n in e.networks.values()):
                    raise SimError(f"That provider already has a network called {f['name']}.")
                nid = e.next_id("networks")
                e.networks[nid] = {"id": nid, "provider_id": f["provider_id"], "name": f["name"]}
                res["target"]["id"] = nid
                res["after"] = dict(f)
                if op.get("client_ref"):
                    refs[op["client_ref"]] = ("provider_network", nid)
            elif name == "create_contact":
                cid = e.next_id("contacts")
                e.contacts[cid] = {"id": cid, "name": f["name"], "phone": f.get("phone") or "", "email": f.get("email") or ""}
                res["target"]["id"] = cid
                res["after"] = dict(f)
                if op.get("client_ref"):
                    refs[op["client_ref"]] = ("contact", cid)
            elif name == "assign_contact":
                contact = resolve(f["contact_id"], "contact")
                table = {"provider": e.providers, "circuit": e.circuits}[target["kind"]]
                if target["id"] not in table or contact not in e.contacts or f["role_id"] not in e.contact_roles:
                    raise SimError("That contact, role, provider or circuit does not exist.")
                otype = f"circuits.{target['kind']}"
                if any((a["object_type"], a["object_id"], a["contact_id"], a["role_id"]) == (otype, target["id"], contact, f["role_id"])
                       for a in e.assignments.values()):
                    raise SimError("That contact already has this role there.")
                aid = e.next_id("assignments")
                e.assignments[aid] = {"id": aid, "object_type": otype, "object_id": target["id"], "contact_id": contact, "role_id": f["role_id"]}
                res["after"] = {**f, "contact_id": contact}
            elif not setup_simulate(e, op, f, res, refs, resolve):
                raise SimError(f"Op {name} is not available on the practice island.")
            b = res["before"] or {}
            res["diff"] = [{"field": k, "before": b.get(k), "after": v} for k, v in (res["after"] or {}).items() if b.get(k) != v]
        except (SimError, KeyError, ValueError) as err:
            res.update(status="error", before=None, after=None, diff=[],
                       error={"code": "validation_failed", "message": str(err) if isinstance(err, SimError) else f"That change does not fit the practice island ({err}).", "details": None})
        out.append(res)
    e.touch()
    return out
