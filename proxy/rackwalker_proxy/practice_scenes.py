"""The practice island as scene bodies, so its hut can be walked into like any building.

Built from the island's `Estate` (practice.py) with the signed-in player's practice changes on it. Every body says
`meta.source: "fixture"` and carries no NetBox URL that leads anywhere real: the island is made up, and nothing here
is read from or written to NetBox.
"""
from __future__ import annotations

from typing import Any

from .errors import ApiError
from .estate import Estate
from .practice import PRACTICE_SITE_ID
from .schema import CONTRACT_VERSION

BASE = PRACTICE_SITE_ID - 1
TS = "2026-11-09T00:00:00Z"
URL = "about:blank#practice-island"
ROOMS = {BASE + 1: (0, 0, 6, 5), BASE + 2: (8, 0, 5, 5)}  # x, y, w, h on the site grid


def is_practice(oid: int) -> bool:
    return oid > BASE


def meta() -> dict[str, Any]:
    return {"contract_version": CONTRACT_VERSION, "generated_at": TS, "netbox_version": None, "source": "fixture"}


def _site(e: Estate) -> dict[str, Any]:
    s = e.sites[PRACTICE_SITE_ID]
    return {"id": s["id"], "slug": s["slug"], "name": s["name"], "status": "active", "latitude": s["latitude"], "longitude": s["longitude"],
            "iso_theme": None, "country": None, "description": "Practice island: made-up data, never NetBox.", "last_updated": TS,
            "netbox_url": URL}


def _location(loc: dict[str, Any]) -> dict[str, Any]:
    x, y, w, h = ROOMS[loc["id"]]
    return {"id": loc["id"], "site_id": loc["site_id"], "parent_id": None, "name": loc["name"], "slug": loc["name"].lower().replace(" ", "-"),
            "iso_x": x, "iso_y": y, "iso_w": w, "iso_h": h, "iso_facing": "S", "latitude": None, "longitude": None, "last_updated": TS,
            "netbox_url": URL}


def _rack(r: dict[str, Any]) -> dict[str, Any]:
    return {"id": r["id"], "site_id": r["site_id"], "location_id": r["location_id"], "name": r["name"], "status": "active",
            "u_height": r["u_height"], "starting_unit": 1, "desc_units": False, "iso_x": r["iso_x"], "iso_y": r["iso_y"], "iso_facing": "S",
            "latitude": None, "longitude": None, "last_updated": TS, "netbox_url": URL}


def _device(d: dict[str, Any]) -> dict[str, Any]:
    return {"id": d["id"], "name": d["name"], "role": {"id": 1, "slug": d["role"], "name": d["role_name"], "color": "2196f3"},
            "device_type": {"id": 1, "manufacturer": d["manufacturer"], "model": d["model"], "slug": "ex-a2400", "u_height": d["u_height"],
                            "is_full_depth": False},
            "site_id": d["site_id"], "location_id": d["location_id"], "rack_id": d["rack_id"], "position": d["position"], "face": d["face"],
            "status": d["status"], "primary_ip": d["primary_ip"], "last_updated": TS, "netbox_url": URL}


def _term(e: Estate, key: tuple[str, int]) -> dict[str, Any]:
    kind, oid = key
    if kind == "circuit_termination":
        t = e.cterms[oid]
        return {"kind": kind, "id": oid, "name": t["side"], "device_id": None, "device_name": None, "circuit_id": t["circuit_id"],
                "circuit_cid": e.circuits[t["circuit_id"]]["cid"], "site_id": t["site_id"]}
    p = e.ports[key]
    return {"kind": kind, "id": oid, "name": p["name"], "device_id": p["device_id"], "device_name": e.device_name(p["device_id"]),
            "circuit_id": None, "circuit_cid": None, "site_id": PRACTICE_SITE_ID}


def _cable(e: Estate, c: dict[str, Any]) -> dict[str, Any]:
    return {"id": c["id"], "label": c["label"], "type": c["type"], "color": None, "status": c["status"], "length": None, "length_unit": None,
            "a_terminations": [_term(e, k) for k in c["a"]], "b_terminations": [_term(e, k) for k in c["b"]], "netbox_url": URL}


def _cables_of(e: Estate, device_ids: set[int]) -> list[dict[str, Any]]:
    out = []
    for _, c in sorted(e.cables.items()):
        if any(k[0] != "circuit_termination" and e.ports[k]["device_id"] in device_ids for k in list(c["a"]) + list(c["b"])):
            out.append(_cable(e, c))
    return out


def site_scene(e: Estate) -> dict[str, Any]:
    devs = e.site_devices(PRACTICE_SITE_ID)
    return {"meta": meta(), "site": _site(e), "locations": [_location(x) for _, x in sorted(e.locations.items())],
            "racks": [_rack(r) for _, r in sorted(e.racks.items())], "devices": [_device(d) for d in devs],
            "cables": _cables_of(e, {d["id"] for d in devs})}


def rack_scene(e: Estate, rack_id: int) -> dict[str, Any]:
    if rack_id not in e.racks:
        raise ApiError("not_found", f"Rack {rack_id} does not exist.", {"kind": "rack", "id": rack_id})
    devs = [d for d in e.site_devices(PRACTICE_SITE_ID) if d["rack_id"] == rack_id]
    return {"meta": meta(), "rack": _rack(e.racks[rack_id]), "devices": [_device(d) for d in devs], "cables": _cables_of(e, {d["id"] for d in devs})}


def _far(e: Estate, key: tuple[str, int]) -> tuple[dict[str, Any], tuple[str, int]] | None:
    holder = e.cterms[key[1]] if key[0] == "circuit_termination" else e.ports[key]
    c = e.cables.get(holder["cable_id"]) if holder["cable_id"] is not None else None
    if c is None:
        return None
    other = c["b"][0] if key in c["a"] else c["a"][0]
    return c, other


def device_detail(e: Estate, device_id: int) -> dict[str, Any]:
    d = e.devices.get(device_id)
    if d is None:
        raise ApiError("not_found", f"Device {device_id} does not exist.", {"kind": "device", "id": device_id})
    ports = []
    for p in e.device_ports(device_id, ("interface",)):
        far = _far(e, ("interface", p["id"]))
        peer = _term(e, far[1]) if far else None
        ports.append({"kind": "interface", "id": p["id"], "device_id": device_id, "name": p["name"], "type": p["type"], "enabled": True,
                      "mgmt_only": False, "description": "", "cable_id": p["cable_id"], "link_peers": [peer] if peer else [],
                      "connected_endpoint": peer})
    return {"meta": meta(), "device": _device(d),
            "info": {"serial": d["serial"], "asset_tag": d["asset_tag"], "platform": d["platform"], "description": "", "comments": "",
                     "tags": [], "custom_fields": {}},
            "interfaces": ports, "cables": _cables_of(e, {device_id})}


def trace(e: Estate, kind: str, port_id: int) -> dict[str, Any]:
    key = (kind, port_id)
    if kind != "interface" or key not in e.ports:
        raise ApiError("not_found", f"{kind.replace('_', ' ').capitalize()} {port_id} does not exist.", {"kind": kind, "id": port_id})
    far = _far(e, key)
    hops = []
    if far:
        c, other = far
        hops.append({"near_end": [_term(e, key)], "cable": {"id": c["id"], "label": c["label"], "type": c["type"], "color": None, "status": c["status"]},
                     "far_end": [_term(e, other)]})
    return {"meta": meta(), "trace": {"origin": _term(e, key), "hops": hops, "is_complete": bool(far), "is_split": False}}
