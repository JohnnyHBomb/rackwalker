"""The practice island: a small made-up estate the proxy serves so a player can learn every kind of quest without
touching NetBox. Nothing here is read from or written to NetBox, ever; a player's practice changes are a list of ops
kept in the game store and replayed on top of `island()`.

The island is the smallest estate that shows the whole idea: three switches, an internet provider, two rooms with a
rack each, and nothing recorded between any of them. Its way out is one whole short chain: the port the line plugs
into, the address on it, then the provider's network, the account, who to call and what it costs (read off the island's
made-up bill). One switch has no management address on record, so the island also asks how a device is managed. Its ids sit far above anything a real NetBox hands out.
"""
from __future__ import annotations

from typing import Any

from .estate import Estate, simulate

PRACTICE_SITE_ID = 9_000_001
SITE_NAME = "Practice Island"
# The island's made-up bill. On the island nobody types an account number, a contact or a charge: the questions about
# them offer only what this bill says, so nothing of a player's own is ever kept with their practice changes. Its
# currency is XTS, the code ISO 4217 keeps for testing: no real money is meant.
BILL = {"account": "PI-ACC-0042", "mrc": "45.00", "nrc": "120.00", "currency": "XTS",
        "contacts": {"support": {"role": "support", "name": "Example Fibre NOC", "phone": "+00 555 0100"},
                     "account": {"role": "account manager", "name": "Example Fibre Accounts", "email": "accounts@fibre.example"},
                     "billing": {"role": "billing", "name": "Example Fibre Billing", "email": "billing@fibre.example"}}}


def example_estate(base: int = 9_000_000, site_name: str = SITE_NAME, complete: bool = False) -> Estate:
    """Three switches, an ISP and two rooms with a rack each; nothing interconnected. With `complete`, the labels,
    rack units and floor plan are all filled in, so only the connectivity findings remain."""
    e = Estate(synthetic=True)
    site = base + 1
    e.sites[site] = {"id": site, "name": site_name, "slug": "practice-island", "status": "active", "latitude": 52.1 if complete else None,
                     "longitude": 4.3 if complete else None, "facility": "PI-1" if complete else None, "country": "NL"}
    quay, loft = base + 1, base + 2
    e.locations[quay] = {"id": quay, "name": "Quay Room", "site_id": site}
    e.locations[loft] = {"id": loft, "name": "Loft", "site_id": site}
    q1, l1 = base + 1, base + 2
    e.racks[q1] = {"id": q1, "name": "PI-Q1", "site_id": site, "location_id": quay, "u_height": 12, "iso_x": 2, "iso_y": 2}
    e.racks[l1] = {"id": l1, "name": "PI-L1", "site_id": site, "location_id": loft, "u_height": 12,
                   "iso_x": 1 if complete else None, "iso_y": 1 if complete else None}
    e.platforms[base + 1] = {"id": base + 1, "name": "Example OS"}
    e.providers[base + 1] = {"id": base + 1, "name": "Example Fibre"}
    e.circuit_types[base + 1] = {"id": base + 1, "name": "Internet"}
    # what a circuit's record needs from NetBox before it can be written: a network of the provider, and contact roles
    e.networks[base + 1] = {"id": base + 1, "provider_id": base + 1, "name": "Example Fibre Internet"}
    for n, (name, slug) in enumerate((("Support / NOC", "support"), ("Account manager", "account-manager"), ("Billing", "billing")), 1):
        e.contact_roles[base + n] = {"id": base + n, "name": name, "slug": slug}
    specs = [("sw-quay-1", quay, q1, 10.0, "SYN-PI-0001", "PI-0001", "Example OS"),
             ("sw-quay-2", quay, q1, 8.0, "" if not complete else "SYN-PI-0002", None if not complete else "PI-0002", "Example OS" if complete else None),
             ("sw-loft-1", loft, l1, None if not complete else 10.0, "SYN-PI-0003", "PI-0003", "Example OS")]
    port_id = base
    for n, (name, loc, rack, pos, serial, tag, platform) in enumerate(specs, 1):
        did = base + n
        e.devices[did] = {"id": did, "name": name, "site_id": site, "location_id": loc, "rack_id": rack, "position": pos,
                          "face": "front" if pos is not None else None, "role": "switch", "role_name": "Switch", "status": "active",
                          "serial": serial, "asset_tag": tag, "platform": platform,
                          # the loft switch has an address, but NetBox does not say it is how the switch is managed
                          "primary_ip": f"192.0.2.{10 + n}/24" if complete or name != "sw-loft-1" else None,
                          "u_height": 1.0, "model": "EX-A2400", "manufacturer": "Example Networks", "type_id": base + 1,
                          "mgmt_mode": None, "mgmt_service": None, "mgmt_default": None, "mgmt_default_service": None}
        for i in range(1, 27):
            port_id += 1
            pname = f"Gi1/0/{i}" if i <= 24 else f"Te1/1/{i - 24}"
            e.ports[("interface", port_id)] = {"kind": "interface", "id": port_id, "device_id": did, "name": pname,
                                               "type": "1000base-t" if i <= 24 else "10gbase-x-sfpp", "enabled": True, "mgmt_only": False,
                                               "cable_id": None, "description": "", "mark_connected": False, "rear_ids": []}
        # managed in-band, as switches are: the address sits on a VLAN interface, which takes no cable
        vlan = base + 100 + n
        e.ports[("interface", vlan)] = {"kind": "interface", "id": vlan, "device_id": did, "name": "Vlan10", "type": "virtual", "enabled": True,
                                        "mgmt_only": True, "cable_id": None, "description": "", "mark_connected": False, "rear_ids": []}
        e.ips[base + n] = {"id": base + n, "address": f"192.0.2.{10 + n}/24", "interface_id": vlan}
    e.prefixes[base + 1] = {"id": base + 1, "prefix": "192.0.2.0/24"}
    return e


def island(ops: list[dict[str, Any]] | None = None) -> Estate:
    """The practice island with a player's practice changes replayed on it."""
    e = example_estate()
    if ops:
        simulate(e, ops)
    return e
