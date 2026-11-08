"""Builders for change-planner tests: a hand-made State (no NetBox) and ChangeSet ops from plain dicts."""
from __future__ import annotations

from typing import Any

from rackwalker_proxy import contracts_gen as c
from rackwalker_proxy.changes import Rec, State

TS = "2026-11-07T10:00:00.000000Z"


def site(i: int, **v: Any) -> Rec:
    return Rec("site", i, TS, {"name": f"S{i}", "description": "", "status": "active", "latitude": None,
                               "longitude": None, "iso_theme": None, **v})


def location(i: int, site_id: int = 1, **v: Any) -> Rec:
    return Rec("location", i, TS, {"site_id": site_id, "name": f"L{i}", "description": "", "status": "active",
                                   "iso_x": None, "iso_y": None, "iso_w": None, "iso_h": None, "iso_facing": None,
                                   "latitude": None, "longitude": None, **v})


def rack(i: int, site_id: int = 1, location_id: int | None = 1, u: int = 42, start: int = 1, **v: Any) -> Rec:
    return Rec("rack", i, TS, {"site_id": site_id, "location_id": location_id, "name": f"R{i}", "description": "",
                               "status": "active", "comments": "", "iso_x": 0, "iso_y": 0, "iso_facing": "S",
                               "latitude": None, "longitude": None, "u_height": u, "starting_unit": start, **v})


def device(i: int, site_id: int = 1, location_id: int | None = 1, rack_id: int | None = None, position: float | None = None,
           face: str | None = None, h: float = 1.0, full: bool = False, child: bool = False, **v: Any) -> Rec:
    return Rec("device", i, TS, {"site_id": site_id, "location_id": location_id, "rack_id": rack_id, "position": position,
                                 "face": face, "name": f"d{i}", "status": "active", "serial": "", "asset_tag": None,
                                 "description": "", "comments": "", "_u_height": h, "_full_depth": full, "_child": child, "_tenant_id": None, **v})


def cable(i: int, **v: Any) -> Rec:
    return Rec("cable", i, TS, {"label": f"C{i}", "status": "connected", "type": "cat6", "color": None, "length": None,
                                "length_unit": None, **v})


def interface(i: int, **v: Any) -> Rec:
    return Rec("interface", i, TS, {"description": "", "enabled": True, **v})


def state(*recs: Rec) -> State:
    s = State()
    for r in recs:
        s.add(r)
    return s


def ops(*raw: dict[str, Any]) -> list[Any]:
    return c.ChangeSet.model_validate({"ops": list(raw)}).ops


def move(rid: Any, **fields: Any) -> dict[str, Any]:
    return {"op": "move_rack", "target": {"kind": "rack", "id": rid}, "fields": fields}


def place(did: int, rack_id: Any, position: float, face: str = "front") -> dict[str, Any]:
    return {"op": "place_device", "target": {"kind": "device", "id": did},
            "fields": {"rack_id": rack_id, "position": position, "face": face}}


def unplace(did: int) -> dict[str, Any]:
    return {"op": "unplace_device", "target": {"kind": "device", "id": did}, "fields": {}}


def create(name: str, ref: str | None = None, **fields: Any) -> dict[str, Any]:
    op = {"op": "create_rack", "target": {"kind": "rack", "id": None}, "fields": {"site_id": 1, "name": name, **fields}}
    return {**op, "client_ref": ref} if ref else op


def update(kind: str, oid: Any, **fields: Any) -> dict[str, Any]:
    return {"op": "update_fields", "target": {"kind": kind, "id": oid}, "fields": fields}
