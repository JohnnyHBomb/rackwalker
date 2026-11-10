"""Synthetic network for RackWalker: one pure-data description shared by devnetbox/seed.py (pushes it into
a dev NetBox) and fixtures/make_fixtures.py (renders the contract fixtures from it). No real data: towns, sites, names,
vendors and models are invented; GPS points are made-up but plausible; addresses are documentation ranges
(RFC 5737: 192.0.2.0/24, 198.51.100.0/24, 203.0.113.0/24).

Counts (checked by tests/test_seed_model.py): 3 sites, 8 locations, 12 racks, 60 devices (4 unracked),
82 cables, 2 circuits.
"""
from __future__ import annotations

from dataclasses import dataclass, field

MANUFACTURERS = ["Example Networks", "Example Compute", "Example Power"]

ROLES = {  # slug: (name, colour)
    "switch": ("Switch", "2196f3"),
    "router": ("Router", "ff9800"),
    "firewall": ("Firewall", "f44336"),
    "server": ("Server", "4caf50"),
    "patch-panel": ("Patch panel", "9e9e9e"),
    "pdu": ("PDU", "795548"),
}


@dataclass(frozen=True)
class DeviceTypeSpec:
    slug: str
    manufacturer: str
    model: str
    u_height: float
    is_full_depth: bool
    # (name, type, mgmt_only), listed in NetBox's own template order: NetBox creates a device's components in the
    # natural-name order of its templates (measured on 4.7.4: mgmt0 before Te1/0/1, Gi1/0/24 < mgmt0 < Te1/1/1,
    # bmc before eth0), not in the order the templates were created. fixtures/make_fixtures.py numbers ports in
    # this order, so fixture port ids equal the live ids on a freshly seeded stack (`make netbox-check-fixtures`).
    interfaces: tuple[tuple[str, str, bool], ...] = ()
    front_rear: int = 0  # patch panel: N front ports 1:1 onto N rear ports
    power_ports: tuple[str, ...] = ()
    power_outlets: tuple[str, ...] = ()


def _ifs(prefix: str, n: int, typ: str, start: int = 1) -> tuple[tuple[str, str, bool], ...]:
    return tuple((f"{prefix}{i}", typ, False) for i in range(start, start + n))


DEVICE_TYPES = {
    t.slug: t
    for t in [
        DeviceTypeSpec("ex-c9000", "Example Networks", "EX-C9000", 2, True,
                       (("mgmt0", "1000base-t", True),) + _ifs("Te1/0/", 24, "10gbase-x-sfpp"), power_ports=("PSU1", "PSU2")),
        DeviceTypeSpec("ex-a2400", "Example Networks", "EX-A2400", 1, False,
                       _ifs("Gi1/0/", 24, "1000base-t") + (("mgmt0", "1000base-t", True),) + _ifs("Te1/1/", 2, "10gbase-x-sfpp"),
                       power_ports=("PSU1",)),
        DeviceTypeSpec("ex-r400", "Example Networks", "EX-R400", 1, True,
                       _ifs("ge-0/0/", 4, "1000base-t", start=0) + _ifs("xe-0/1/", 2, "10gbase-x-sfpp", start=0), power_ports=("PSU1",)),
        DeviceTypeSpec("ex-f200", "Example Networks", "EX-F200", 1, False, _ifs("port", 8, "1000base-t"), power_ports=("PSU1",)),
        DeviceTypeSpec("ex-s2", "Example Compute", "EX-S2", 2, True,
                       (("bmc", "1000base-t", True), ("eth0", "10gbase-t", False), ("eth1", "10gbase-t", False)),
                       power_ports=("PSU1", "PSU2")),
        DeviceTypeSpec("ex-pp24", "Example Networks", "EX-PP24", 1, False, front_rear=24),
        DeviceTypeSpec("ex-pdu16", "Example Power", "EX-PDU16", 0, False, power_ports=("INLET",),
                       power_outlets=tuple(f"OUT{i}" for i in range(1, 17))),
    ]
}


@dataclass
class SiteSpec:
    slug: str
    name: str
    town: str
    lat: float
    lon: float
    theme: str
    net: str  # the site's documentation /24 (RFC 5737), first three octets
    country: str = "GB"  # ISO 3166-1 alpha-2 (custom field iso_country); the made-up GPS points are in Great Britain


@dataclass
class LocationSpec:
    site: str
    slug: str
    name: str
    iso: tuple[int, int, int, int]  # x, y, w, h on the site grid
    facing: str
    gps: tuple[float, float] | None = None


@dataclass
class RackSpec:
    site: str
    location: str
    name: str
    iso: tuple[int, int]
    facing: str
    u_height: int = 42
    gps: tuple[float, float] | None = None


@dataclass
class DeviceSpec:
    name: str
    site: str
    location: str
    role: str
    dtype: str
    rack: str | None = None
    position: float | None = None
    face: str | None = None
    status: str = "active"
    ip: str | None = None
    serial: str = ""


@dataclass
class CableSpec:
    label: str
    a: tuple[str, str, str]  # (kind, device or circuit cid, port name or side)
    b: tuple[str, str, str]
    type: str
    color: str = ""
    status: str = "connected"
    length: float | None = None


# The circuits' paper side (made up; the phone number is from a range reserved for fiction).
CARRIER_ACCOUNT = "SYN-ACC-1001"
# (name, phone, email, the slug of its contact role): who to call when a line is down, the account manager, who sends the bill
CARRIER_CONTACTS = (("Example Carrier NOC", "+44 1632 960001", "noc@example.net", "support"),
                    ("Example Carrier Accounts", "+44 1632 960002", "accounts@example.net", "account-manager"),
                    ("Example Carrier Billing", "+44 1632 960003", "billing@example.net", "billing"))
CARRIER_PREFIX = "198.18.0.0/29"
CURRENCY = "GBP"
CONTACT_ROLES = (("Support / NOC", "support"), ("Account manager", "account-manager"), ("Billing", "billing"))


@dataclass
class CircuitSpec:
    cid: str
    provider: str
    type: str
    a_site: str
    z_site: str
    status: str = "active"
    # the logical and administrative side, all made up: the address on the A end's port, and what the bill says
    a_address: str = ""
    install_date: str = ""
    mrc: float = 0.0
    nrc: float = 0.0


@dataclass
class Model:
    sites: list[SiteSpec] = field(default_factory=list)
    locations: list[LocationSpec] = field(default_factory=list)
    racks: list[RackSpec] = field(default_factory=list)
    devices: list[DeviceSpec] = field(default_factory=list)
    cables: list[CableSpec] = field(default_factory=list)
    circuits: list[CircuitSpec] = field(default_factory=list)


FIBRE, COPPER, POWER, DAC = "mmf-om4", "cat6", "power", "smf-os2"


def build() -> Model:
    m = Model()
    m.sites = [
        SiteSpec("north-depot", "North Depot", "Brackenford", 54.1180, -1.3420, "industrial-robotic", "192.0.2"),
        SiteSpec("west-works", "West Works", "Wrenmouth", 51.6210, -2.8930, "farm", "198.51.100"),
        SiteSpec("east-exchange", "East Exchange", "Saltings Cross", 52.7840, 1.4120, "business", "203.0.113"),
    ]
    S = {s.slug: s for s in m.sites}
    m.locations = [
        LocationSpec("north-depot", "nd-server-room", "Server Room", (0, 0, 10, 6), "S"),
        LocationSpec("north-depot", "nd-comms", "Comms Cupboard", (12, 0, 4, 4), "S", gps=(54.1183, -1.3416)),
        LocationSpec("north-depot", "nd-store", "Store", (0, 8, 6, 4), "E"),
        LocationSpec("west-works", "ww-server-room", "Server Room", (0, 0, 8, 6), "S"),
        LocationSpec("west-works", "ww-comms", "Comms Cupboard", (10, 0, 4, 4), "W"),
        LocationSpec("east-exchange", "ee-hall-a", "Hall A", (0, 0, 8, 6), "S"),
        LocationSpec("east-exchange", "ee-hall-b", "Hall B", (10, 0, 6, 6), "S"),
        LocationSpec("east-exchange", "ee-comms", "Comms Cupboard", (0, 8, 4, 4), "N"),
    ]
    R = m.racks
    R += [RackSpec("north-depot", "nd-server-room", f"ND-R{i}", (1 + 2 * (i - 1), 2), "S") for i in range(1, 5)]
    R += [RackSpec("north-depot", "nd-comms", "ND-C1", (1, 1), "S", u_height=24)]
    R += [RackSpec("west-works", "ww-server-room", f"WW-R{i}", (2 + 3 * (i - 1), 2), "S") for i in range(1, 3)]
    R += [RackSpec("west-works", "ww-comms", "WW-C1", (1, 1), "W", u_height=24)]
    R += [RackSpec("east-exchange", "ee-hall-a", "EE-A1", (2, 2), "S"), RackSpec("east-exchange", "ee-hall-a", "EE-A2", (4, 2), "S"),
          RackSpec("east-exchange", "ee-hall-b", "EE-B1", (2, 2), "E"),
          RackSpec("east-exchange", "ee-comms", "EE-C1", (1, 1), "N", u_height=24, gps=(52.7842, 1.4116))]

    D = m.devices
    C = m.cables
    serial = iter(range(100001, 199999))

    def dev(name, site, loc, role, dtype, rack=None, pos=None, face="front", ip=None, status="active"):
        d = DeviceSpec(name, site, loc, role, dtype, rack, pos, face if pos is not None else None, status, ip, f"SYN{next(serial)}")
        D.append(d)
        return d

    def cable(a, b, typ, **kw):
        prefix = {"north-depot": "ND", "west-works": "WW", "east-exchange": "EE"}[_site_of(m, a)]
        C.append(CableSpec(f"{prefix}-{len([c for c in C if c.label.startswith(prefix)]) + 1:03d}", a, b, typ, **kw))

    def core_rack(site, loc, rack, p, net, n_access):
        s = S[site]
        dev(f"{p}-pp1", site, loc, "patch-panel", "ex-pp24", rack, 42)
        core = dev(f"{p}-core1", site, loc, "switch", "ex-c9000", rack, 38, ip=f"{net}.2/24")
        rtr = dev(f"{p}-rtr1", site, loc, "router", "ex-r400", rack, 36, ip=f"{net}.1/24")
        fw = dev(f"{p}-fw1", site, loc, "firewall", "ex-f200", rack, 35, ip=f"{net}.3/24")
        pdu = dev(f"{p}-pdu-{rack.lower()}", site, loc, "pdu", "ex-pdu16", rack, None)
        cable(("interface", rtr.name, "ge-0/0/1"), ("interface", fw.name, "port1"), COPPER, color="2196f3")
        cable(("interface", fw.name, "port2"), ("interface", core.name, "Te1/0/24"), COPPER, color="2196f3")
        for i, d in enumerate([core, rtr, fw], start=1):
            cable(("power_outlet", pdu.name, f"OUT{i}"), ("power_port", d.name, "PSU1"), POWER, color="111111")
        del s, n_access
        return core

    def access_rack(site, loc, rack, p, core, uplink_base, n_servers, net, server_base):
        dev(f"{p}-pp-{rack.lower()}", site, loc, "patch-panel", "ex-pp24", rack, 42)
        acc = dev(f"{p}-acc-{rack.lower()}", site, loc, "switch", "ex-a2400", rack, 40, ip=f"{net}.{10 + uplink_base}/24")
        dev(f"{p}-pdu-{rack.lower()}", site, loc, "pdu", "ex-pdu16", rack, None)
        for j in (1, 2):
            cable(("interface", acc.name, f"Te1/1/{j}"), ("interface", core.name, f"Te1/0/{uplink_base + j}"), FIBRE, color="00bcd4")
        for k in range(n_servers):
            srv = dev(f"{p}-srv{server_base + k:02d}", site, loc, "server", "ex-s2", rack, 10 + 2 * k,
                      ip=f"{net}.{100 + server_base + k}/24", status="planned" if (server_base + k) % 7 == 0 else "active")
            for j, nic in enumerate(("eth0", "eth1")):
                cable(("interface", srv.name, nic), ("interface", acc.name, f"Gi1/0/{2 * k + j + 1}"), COPPER)
        return acc

    def comms(site, loc, rack, p, core_pp, core, net):
        pp = dev(f"{p}-pp-{rack.lower()}", site, loc, "patch-panel", "ex-pp24", rack, 24)
        acc = dev(f"{p}-acc-{rack.lower()}", site, loc, "switch", "ex-a2400", rack, 22, ip=f"{net}.30/24")
        # access uplink -> local patch front 1 -> trunk rear 1 <-> core-room patch rear 1 -> front 1 -> core
        cable(("interface", acc.name, "Te1/1/1"), ("front_port", pp.name, "1"), FIBRE, color="00bcd4")
        cable(("rear_port", pp.name, "1"), ("rear_port", core_pp, "1"), DAC, color="ffeb3b", length=45)
        cable(("front_port", core_pp, "1"), ("interface", core.name, "Te1/0/20"), FIBRE, color="00bcd4")
        return acc

    # North Depot: core rack + three access racks + comms cupboard + store
    nd_core = core_rack("north-depot", "nd-server-room", "ND-R1", "nd", "192.0.2", 3)
    for i, r in enumerate(["ND-R2", "ND-R3", "ND-R4"]):
        access_rack("north-depot", "nd-server-room", r, "nd", nd_core, 2 * i, 3, "192.0.2", 1 + 3 * i)
    dev("nd-pdu-nd-c1", "north-depot", "nd-comms", "pdu", "ex-pdu16", "ND-C1", None)
    comms("north-depot", "nd-comms", "ND-C1", "nd", "nd-pp1", nd_core, "192.0.2")
    dev("nd-srv90", "north-depot", "nd-store", "server", "ex-s2", status="inventory")
    dev("nd-srv91", "north-depot", "nd-store", "server", "ex-s2", status="inventory")
    for k in range(1, 10):  # BMC network for the North servers
        cable(("interface", f"nd-srv{k:02d}", "bmc"), ("interface", f"nd-acc-nd-r{2 + (k - 1) // 3}", f"Gi1/0/{20 + (k - 1) % 3}"), COPPER, color="9c27b0")

    # West Works
    ww_core = core_rack("west-works", "ww-server-room", "WW-R1", "ww", "198.51.100", 1)
    access_rack("west-works", "ww-server-room", "WW-R2", "ww", ww_core, 0, 2, "198.51.100", 1)
    comms("west-works", "ww-comms", "WW-C1", "ww", "ww-pp1", ww_core, "198.51.100")
    dev("ww-fw2", "west-works", "ww-server-room", "firewall", "ex-f200", status="offline")

    # East Exchange
    ee_core = core_rack("east-exchange", "ee-hall-a", "EE-A1", "ee", "203.0.113", 2)
    access_rack("east-exchange", "ee-hall-a", "EE-A2", "ee", ee_core, 0, 3, "203.0.113", 1)
    access_rack("east-exchange", "ee-hall-b", "EE-B1", "ee", ee_core, 2, 2, "203.0.113", 4)
    comms("east-exchange", "ee-comms", "EE-C1", "ee", "ee-pp1", ee_core, "203.0.113")
    dev("ee-rtr2", "east-exchange", "ee-hall-b", "router", "ex-r400", status="planned")

    # Inter-site: two carrier circuits from North Depot, one dark-fibre cable North <-> East.
    m.circuits = [
        CircuitSpec("EXC-1001", "Example Carrier", "Ethernet WAN", "north-depot", "west-works", a_address="198.18.0.1/30",
                    install_date="2025-03-03", mrc=310.0, nrc=950.0),
        CircuitSpec("EXC-1002", "Example Carrier", "Ethernet WAN", "north-depot", "east-exchange", a_address="198.18.0.5/30",
                    install_date="2025-03-17", mrc=310.0, nrc=950.0),
    ]
    cable(("interface", "nd-rtr1", "xe-0/1/0"), ("circuit_termination", "EXC-1001", "A"), DAC, color="ffeb3b")
    cable(("interface", "ww-rtr1", "xe-0/1/0"), ("circuit_termination", "EXC-1001", "Z"), DAC, color="ffeb3b")
    cable(("interface", "nd-rtr1", "xe-0/1/1"), ("circuit_termination", "EXC-1002", "A"), DAC, color="ffeb3b")
    cable(("interface", "ee-rtr1", "xe-0/1/0"), ("circuit_termination", "EXC-1002", "Z"), DAC, color="ffeb3b")
    cable(("interface", "nd-core1", "Te1/0/23"), ("interface", "ee-core1", "Te1/0/23"), DAC, color="ff5722", length=260, status="planned")
    return m


def _site_of(m: Model, end: tuple[str, str, str]) -> str:
    kind, name, _ = end
    if kind == "circuit_termination":
        c = next(c for c in m.circuits if c.cid == name)
        return c.a_site
    return next(d.site for d in m.devices if d.name == name)
