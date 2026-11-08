"""POST /api/changes/preview and /api/changes/apply (contracts/api.md "Changes: preview and apply").

Three layers, so the rules can be tested without NetBox:

1. `gather(client, ops)` reads, from NetBox, every object the ops can touch or collide with, into a `State` of
   `Rec`ords whose values use contract field names.
2. `plan(ops, state)` is pure: it runs the ops in order against a copy of the state and returns one `Planned` per
   op (status, before, after, diff, error). It never mutates its input. Preview is gather + plan.
3. `apply(...)` runs preview; if any op errors nothing is written; otherwise it writes the ops in order, resolving
   `@client_ref` references to the ids NetBox assigns, and reads every written object back for `after`. NetBox has
   no cross-request transaction, so a failed write leaves the earlier ops written: the failing op says so in its
   error (`details.partial`, `details.written_ops`) and every later op is `skipped`.
"""
from __future__ import annotations

import asyncio
import copy
import logging
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Any

from jsonschema import Draft202012Validator

from . import contracts_gen as c
from . import netbox as nb
from .errors import ApiError
from .schema import CONTRACT_VERSION, SCHEMA

log = logging.getLogger("rackwalker_proxy.changes")

Key = tuple[str, int | str]  # (kind, NetBox id) or (kind, "@client_ref" / "#<op index>") for an object created in the set

# contracts/api.md update_fields whitelist (contract field names, in the order api.md lists them).
WHITELIST: dict[str, list[str]] = {
    "site": ["name", "description", "status", "latitude", "longitude", "iso_theme"],
    "location": ["name", "description", "status", "iso_x", "iso_y", "iso_w", "iso_h", "iso_facing", "latitude", "longitude"],
    "rack": ["name", "description", "status", "comments", "iso_x", "iso_y", "iso_facing", "latitude", "longitude"],
    "device": ["name", "status", "serial", "asset_tag", "description", "comments"],
    "cable": ["label", "status", "type", "color", "length", "length_unit"],
    "interface": ["description", "enabled"],
}
# NetBox 4.7 status choices (dcim/choices.py, circuits not involved).
STATUS: dict[str, list[str]] = {
    "site": ["planned", "staging", "active", "decommissioning", "retired"],
    "location": ["planned", "staging", "active", "decommissioning", "retired"],
    "rack": ["reserved", "available", "planned", "active", "deprecated"],
    "device": ["offline", "active", "planned", "staged", "failed", "inventory", "decommissioning"],
    "cable": ["connected", "planned", "decommissioning"],
}
ENDPOINT = {"site": "dcim/sites", "location": "dcim/locations", "rack": "dcim/racks", "device": "dcim/devices",
            "cable": "dcim/cables", "interface": "dcim/interfaces"}
CUSTOM = {"iso_x", "iso_y", "iso_w", "iso_h", "iso_facing", "iso_theme"}
CREATE_RACK_ORDER = list(SCHEMA["$defs"]["CreateRackOp"]["properties"]["fields"]["properties"])

# Value rules for update_fields: the contract's own property schema for that field (by entity) AND NetBox's
# limits (max lengths, status choices). Fields the contract entities do not carry use NetBox's limits alone.
_CONTRACT_PROP = {"site": "Site", "location": "Location", "rack": "Rack", "device": "Device", "cable": "Cable",
                  "interface": "Interface"}
_EXTRA_PROP = {("device", f): "DeviceInfo" for f in ("serial", "asset_tag", "description", "comments")}
_NETBOX_LIMITS: dict[str, dict[str, Any]] = {
    "name": {"type": "string", "minLength": 1, "maxLength": 100},
    "description": {"type": "string", "maxLength": 200},
    "comments": {"type": "string"},
    "serial": {"maxLength": 50},
    "asset_tag": {"type": ["string", "null"], "minLength": 1, "maxLength": 50},
    "label": {"maxLength": 100},
}


def _field_validator(kind: str, name: str) -> Draft202012Validator:
    parts: list[dict[str, Any]] = []
    entity = _EXTRA_PROP.get((kind, name), _CONTRACT_PROP[kind])
    prop = SCHEMA["$defs"][entity]["properties"].get(name)
    if prop is not None:
        parts.append({k: v for k, v in prop.items() if k not in ("description", "examples")})
    limit = dict(_NETBOX_LIMITS.get(name, {}))
    if kind == "device" and name == "name":
        limit = {"type": ["string", "null"], "minLength": 1, "maxLength": 64}
    if name == "status":
        limit = {"enum": STATUS[kind]}
    if limit:
        parts.append(limit)
    return Draft202012Validator({"$defs": SCHEMA["$defs"], "allOf": parts})


FIELD_RULES: dict[tuple[str, str], Draft202012Validator] = {(k, f): _field_validator(k, f) for k, fs in WHITELIST.items() for f in fs}


@dataclass
class Rec:
    kind: str
    id: int | str
    last_updated: str | None
    v: dict[str, Any]

    @property
    def key(self) -> Key:
        return (self.kind, self.id)


@dataclass
class State:
    recs: dict[Key, Rec] = field(default_factory=dict)

    def add(self, r: Rec) -> None:
        self.recs[r.key] = r

    def of(self, kind: str) -> list[Rec]:
        return [r for r in self.recs.values() if r.kind == kind]


class OpError(Exception):
    def __init__(self, code: str, message: str, details: dict[str, Any] | None = None):
        super().__init__(message)
        self.err = ApiError(code, message, details)


@dataclass
class Planned:
    index: int
    op: str
    client_ref: str | None
    kind: str
    target_id: int | str | None       # as in the request (preview shows refs unchanged)
    status: str = "ok"
    before: dict[str, Any] | None = None
    after: dict[str, Any] | None = None
    error: ApiError | None = None
    key: Key | None = None            # resolved state key of the target (created key for create_rack)
    write: dict[str, Any] = field(default_factory=dict)  # contract fields to write, refs resolved to state keys

    @property
    def diff(self) -> list[dict[str, Any]]:
        if self.before is None and self.after is None:
            return []
        b = self.before or {}
        return [{"field": f, "before": b.get(f), "after": a} for f, a in (self.after or {}).items()
                if self.before is None or b.get(f) != a]

    def result(self, target_id: Any = None) -> c.OpResult:
        return c.OpResult(index=self.index, op=self.op, client_ref=self.client_ref,
                          target=c.OpTarget(kind=self.kind, id=self.target_id if target_id is None else target_id),
                          status=self.status, before=self.before, after=self.after,
                          diff=[c.FieldDiff(**d) for d in self.diff], error=c.ErrorBody(**self.error.body()) if self.error else None)


# ---------------------------------------------------------------- records from NetBox objects
def _cf_num(o: nb.NbObject, name: str) -> float | None:
    v = o.cf(name)
    return None if v is None or v == "" else float(v)


def rec_site(s: nb.Site) -> Rec:
    theme = s.cf("iso_theme")
    return Rec("site", s.id, s.last_updated, {"name": s.name, "description": s.description, "status": s.status,
                                              "latitude": s.latitude, "longitude": s.longitude, "iso_theme": theme})


def rec_location(loc: nb.Location) -> Rec:
    return Rec("location", loc.id, loc.last_updated, {
        "site_id": loc.site.id, "name": loc.name, "description": loc.description, "status": loc.status,
        **{f: loc.cf(f) for f in ("iso_x", "iso_y", "iso_w", "iso_h", "iso_facing")},
        "latitude": _cf_num(loc, "iso_latitude"), "longitude": _cf_num(loc, "iso_longitude"),
        "_parent_id": loc.parent.id if loc.parent else None})  # planner-only (name uniqueness)


def rec_rack(r: nb.Rack) -> Rec:
    return Rec("rack", r.id, r.last_updated, {
        "site_id": r.site.id, "location_id": r.location.id if r.location else None, "name": r.name,
        "description": r.description, "status": r.status, "comments": r.comments,
        "iso_x": r.cf("iso_x"), "iso_y": r.cf("iso_y"), "iso_facing": r.cf("iso_facing"),
        "latitude": _cf_num(r, "iso_latitude"), "longitude": _cf_num(r, "iso_longitude"),
        "u_height": r.u_height, "starting_unit": r.starting_unit})


def rec_device(d: nb.Device, dtypes: dict[int, nb.DeviceType]) -> Rec:
    dt = dtypes.get(d.device_type.id)
    return Rec("device", d.id, d.last_updated, {
        "site_id": d.site.id, "location_id": d.location.id if d.location else None, "rack_id": d.rack.id if d.rack else None,
        "position": d.position, "face": d.face, "name": d.name, "status": d.status, "serial": d.serial,
        "asset_tag": d.asset_tag, "description": d.description, "comments": d.comments,
        # planner-only facts (never shown in before/after, which list only touched fields)
        "_u_height": dt.u_height if dt else 1.0, "_full_depth": dt.is_full_depth if dt else True,
        "_child": bool(d.parent_device) or (dt is not None and dt.subdevice_role == "child"),
        "_tenant_id": d.tenant.id if d.tenant else None})


def rec_cable(cb: nb.Cable) -> Rec:
    return Rec("cable", cb.id, cb.last_updated, {"label": cb.label, "status": cb.status, "type": cb.type, "color": cb.color,
                                                 "length": cb.length, "length_unit": cb.length_unit})


def rec_interface(p: nb.Port) -> Rec:
    return Rec("interface", p.id, p.last_updated, {"description": p.description, "enabled": p.enabled})


MODEL = {"site": nb.Site, "location": nb.Location, "rack": nb.Rack, "device": nb.Device, "cable": nb.Cable, "interface": nb.Port}


def to_rec(kind: str, obj: Any, dtypes: dict[int, nb.DeviceType]) -> Rec:
    return {"site": rec_site, "location": rec_location, "rack": rec_rack, "cable": rec_cable,
            "interface": rec_interface}[kind](obj) if kind != "device" else rec_device(obj, dtypes)


# ---------------------------------------------------------------- gather (NetBox reads)
def _ids(ops: list[Any]) -> dict[str, set[int]]:
    want: dict[str, set[int]] = {k: set() for k in ENDPOINT}

    def add(kind: str, v: Any) -> None:
        if isinstance(v, int) and not isinstance(v, bool):
            want[kind].add(v)

    for op in ops:
        f = op.fields.model_dump(mode="json", exclude_unset=True) if hasattr(op.fields, "model_dump") else {}
        tid = op.target.id.root if hasattr(op.target.id, "root") else op.target.id
        add(op.target.kind, tid)
        add("location", f.get("location_id"))
        add("rack", f.get("rack_id"))
        add("site", f.get("site_id"))
    return want


def _global_values(ops: list[Any]) -> tuple[set[str], set[str]]:
    """Values whose NetBox uniqueness is global, not per site: device asset tags and site names the ops set."""
    tags: set[str] = set()
    names: set[str] = set()
    for op in ops:
        if op.op != "update_fields":
            continue
        f = op.fields.model_dump(mode="json", exclude_unset=True) if hasattr(op.fields, "model_dump") else {}
        if op.target.kind == "device" and isinstance(f.get("asset_tag"), str):
            tags.add(f["asset_tag"])
        if op.target.kind == "site" and isinstance(f.get("name"), str):
            names.add(f["name"])
    return tags, names


async def gather(client: nb.NetBox, ops: list[Any]) -> State:
    """Everything the ops can touch: the named objects, then every rack, device and location of their sites
    (U-slot occupants, name uniqueness, cross-site checks), and the holders of any asset tag or site name the ops set
    (both unique across NetBox). Unknown ids are simply absent (-> not_found)."""
    want = _ids(ops)
    named = await asyncio.gather(*(client.by_ids(MODEL[k], ENDPOINT[k], ids) for k, ids in want.items()))
    st = State()
    devices: list[nb.Device] = []
    tags, site_names = _global_values(ops)
    holders = await asyncio.gather(*(client.list(nb.Device, "dcim/devices", {"asset_tag": t}) for t in sorted(tags)),
                                   *(client.list(nb.Site, "dcim/sites", {"name": n}) for n in sorted(site_names)))
    for objs in holders:
        for o in objs:
            if isinstance(o, nb.Device):
                devices.append(o)
            else:
                st.add(rec_site(o))
    for kind, objs in zip(want, named):
        for o in objs.values():
            if kind == "device":
                devices.append(o)
            else:
                st.add(to_rec(kind, o, {}))
    sites = set(want["site"]) | {r.v["site_id"] for r in st.recs.values() if "site_id" in r.v} | {d.site.id for d in devices}
    for s in sorted(sites):
        q = {"site_id": s}
        locs, racks, devs = await asyncio.gather(client.list(nb.Location, "dcim/locations", q),
                                                 client.list(nb.Rack, "dcim/racks", q), client.list(nb.Device, "dcim/devices", q))
        for o in locs:
            st.add(rec_location(o))
        for o in racks:
            st.add(rec_rack(o))
        devices.extend(devs)
    # only the device types these devices use (U height, depth, child role); a later copy of a device wins, as before
    dtypes = await client.device_types({d.device_type.id for d in devices})
    for o in devices:
        st.add(rec_device(o, dtypes))
    return st


# ---------------------------------------------------------------- plan (pure)
def _ts(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def _u(x: float) -> str:
    return f"U{x:g}"


class Planner:
    def __init__(self, state: State):
        self.st = copy.deepcopy(state)
        self.refs: dict[str, Key | None] = {}  # client_ref -> target key of that op (None: the op failed)

    # -- helpers --
    def resolve(self, kind: str, ident: Any, field_name: str | None = None) -> Rec:
        where = {"field": field_name} if field_name else {}
        if isinstance(ident, str):
            name = ident[1:]
            if name not in self.refs:
                raise OpError("not_found", f"Reference {ident} does not name an earlier op of this ChangeSet.",
                              {**where, "kind": kind, "id": ident})
            key = self.refs[name]
            if key is None:
                raise OpError("validation_failed", f"Reference {ident} names an op that failed.", {**where, "id": ident})
            if key[0] != kind:
                raise OpError("validation_failed", f"Reference {ident} names a {key[0]}, not a {kind}.",
                              {**where, "kind": kind, "id": ident})
            return self.st.recs[key]
        rec = self.st.recs.get((kind, ident))
        if rec is None:
            raise OpError("not_found", f"{kind.capitalize()} {ident} does not exist.", {**where, "kind": kind, "id": ident})
        return rec

    @staticmethod
    def lock(rec: Rec, since: Any) -> None:
        if since is None or rec.last_updated is None:
            return
        if _ts(rec.last_updated) > (since if isinstance(since, datetime) else _ts(str(since))):
            shown = since.isoformat() if isinstance(since, datetime) else since
            # The message is for people (the basket shows it): the object's name, no ids or raw timestamps; those stay
            # in details (previously: "Device 16 ... last_updated 2026-...").
            label = rec.v.get("name") or rec.v.get("label") or f"{rec.id}"
            raise OpError("conflict", f"{rec.kind.capitalize()} {label} was changed in NetBox after you loaded it: "
                          "reload, then stage this change again.",
                          {"field": "if_unmodified_since", "last_updated": rec.last_updated, "if_unmodified_since": shown})

    def unique_name(self, rack: Rec | None, location_id: Any, name: str) -> None:
        """NetBox 4.7 (dcim/models/racks.py, Rack.Meta): UniqueConstraint(location, name). Case-sensitive, and a rack
        without a location never collides (NULLs are distinct in that constraint)."""
        if location_id is None:
            return
        for r in self.st.of("rack"):
            if r is not rack and r.v["location_id"] == location_id and r.v["name"] == name:
                raise OpError("validation_failed", f"A rack named {r.v['name']!r} already exists in location {location_id} "
                              "(NetBox: rack name must be unique per location).", {"field": "name", "rack_id": r.id})

    def unique_location_name(self, loc: Rec, name: str) -> None:
        """NetBox 4.7 (dcim/models/sites.py, Location.Meta): UniqueConstraint(site, parent, name, nulls_distinct=False).
        Case-sensitive; two top-level locations of one site share a NULL parent and do collide."""
        for o in self.st.of("location"):
            if o is not loc and o.v["site_id"] == loc.v["site_id"] and o.v.get("_parent_id") == loc.v.get("_parent_id") \
                    and o.v["name"] == name:
                raise OpError("validation_failed", f"A location named {o.v['name']!r} already exists under the same parent in "
                              f"site {loc.v['site_id']} (NetBox: location name must be unique per site and parent).",
                              {"field": "name", "location_id": o.id})

    @staticmethod
    def cable_length(rec: Rec, f: dict[str, Any]) -> None:
        """NetBox 4.7 Cable.clean: a length needs a unit. (A null length with a unit is fine: Cable.save clears the unit.)"""
        merged = {**rec.v, **f}
        if merged.get("length") is not None and not merged.get("length_unit"):
            field_name = "length_unit" if "length_unit" in f else "length"
            raise OpError("validation_failed", "A cable length needs a length_unit (NetBox: Must specify a unit when setting "
                          "a cable length).", {"field": field_name})

    def unique_device_name(self, dev: Rec, name: str | None) -> None:
        """NetBox 4.7 (dcim/models/devices.py, Device.Meta): Lower(name), site and tenant are unique together for named
        devices, and a null tenant equals a null tenant (nulls_distinct=False)."""
        if name is None:
            return
        for o in self.st.of("device"):
            if o is not dev and o.v["name"] is not None and o.v["site_id"] == dev.v["site_id"] \
                    and o.v.get("_tenant_id") == dev.v.get("_tenant_id") and str(o.v["name"]).lower() == name.lower():
                raise OpError("validation_failed", f"A device named {o.v['name']!r} already exists in site {dev.v['site_id']} "
                              "with the same tenant (NetBox: device name must be unique per site and tenant).",
                              {"field": "name", "device_id": o.id})

    def unique_asset_tag(self, dev: Rec, tag: str) -> None:
        """NetBox 4.7 Device.asset_tag: unique=True across all of NetBox (case-sensitive; measured on 4.7.4: 'at-1'
        next to 'AT-1' is accepted). gather() loads every device holding a tag the ops set."""
        for o in self.st.of("device"):
            if o is not dev and o.v.get("asset_tag") == tag:
                raise OpError("validation_failed", f"Device {o.v['name'] or o.id} already has asset tag {tag!r} (NetBox: an "
                              "asset tag is unique across all devices).", {"field": "asset_tag", "device_id": o.id})

    def unique_site_name(self, site: Rec, name: str) -> None:
        """NetBox 4.7 Site.name: unique=True across all sites (case-sensitive; measured on 4.7.4). gather() loads every
        site holding a name the ops set."""
        for o in self.st.of("site"):
            if o is not site and o.v["name"] == name:
                raise OpError("validation_failed", f"A site named {name!r} already exists (NetBox: site names are unique).",
                              {"field": "name", "site_id": o.id})

    @staticmethod
    def site_coordinate(field_name: str, value: float) -> None:
        """NetBox 4.7 Site.latitude/longitude: DecimalField(max_digits 8/9, decimal_places 6). Within the contract's
        ranges (|lat| <= 90, |lon| <= 180) only the decimal places can overflow: more than 6 is refused on write."""
        exp = Decimal(repr(float(value))).normalize().as_tuple().exponent
        if isinstance(exp, int) and exp < -6:
            raise OpError("validation_failed", f"Site {field_name} {value} has more than 6 decimal places (NetBox stores "
                          "site coordinates with at most 6).", {"field": field_name, "decimal_places": 6})

    @staticmethod
    def change(p: Planned, rec: Rec, shown: dict[str, Any], resolved: dict[str, Any]) -> None:
        p.before = {f: rec.v.get(f) for f in shown}
        p.after = dict(shown)
        p.write = resolved
        p.status = "ok" if any(rec.v.get(f) != v for f, v in resolved.items()) else "no_change"
        rec.v.update(resolved)

    # -- ops --
    def move_rack(self, p: Planned, op: Any, f: dict[str, Any]) -> None:
        rack = self.resolve("rack", p.target_id)
        self.lock(rack, op.if_unmodified_since)
        resolved = dict(f)
        if f.get("location_id") is not None:
            loc = self.resolve("location", f["location_id"], "location_id")
            if loc.v["site_id"] != rack.v["site_id"]:
                raise OpError("validation_failed", f"Location {loc.id} is in site {loc.v['site_id']}, rack {rack.id} in site "
                              f"{rack.v['site_id']}.", {"field": "location_id", "site_id": rack.v["site_id"]})
            resolved["location_id"] = loc.id
        if "location_id" in f:
            self.unique_name(rack, resolved["location_id"], rack.v["name"])
        p.key = rack.key
        self.change(p, rack, f, resolved)
        if "location_id" in resolved:  # NetBox moves the racked devices with the rack (Rack.save)
            for d in self.st.of("device"):
                if d.v["rack_id"] == rack.id:
                    d.v["location_id"] = resolved["location_id"]

    def place_device(self, p: Planned, op: Any, f: dict[str, Any]) -> None:
        dev = self.resolve("device", p.target_id)
        self.lock(dev, op.if_unmodified_since)
        rack = self.resolve("rack", f["rack_id"], "rack_id")
        if dev.v["_child"]:
            raise OpError("validation_failed", f"Device {dev.id} is a child device; it goes in a device bay, not a rack.",
                          {"field": "rack_id"})
        if rack.v["site_id"] != dev.v["site_id"]:
            raise OpError("validation_failed", f"Rack {rack.v['name']} is in site {rack.v['site_id']}; device {dev.id} is in site "
                          f"{dev.v['site_id']}.", {"field": "rack_id", "site_id": dev.v["site_id"]})
        h, pos, face = float(dev.v["_u_height"]), float(f["position"]), f["face"]
        if h == 0:
            raise OpError("validation_failed", f"Device {dev.id} is zero-U; it cannot take a U position.",
                          {"field": "position", "u_height": 0})
        lo, hi = rack.v["starting_unit"], rack.v["starting_unit"] + rack.v["u_height"]
        if pos < lo or pos + h > hi:
            raise OpError("validation_failed", f"{_u(pos)}-{_u(pos + h - 1)} is outside rack {rack.v['name']} ({_u(lo)}-{_u(hi - 1)}).",
                          {"field": "position", "reason": "out of range", "min": lo, "max": hi - h})
        for o in sorted(self.st.of("device"), key=lambda r: (isinstance(r.id, str), r.id)):
            if o is dev or o.v["rack_id"] != rack.id or o.v["position"] is None or not o.v["_u_height"]:
                continue
            op_, oh = float(o.v["position"]), float(o.v["_u_height"])
            if pos < op_ + oh and op_ < pos + h and (dev.v["_full_depth"] or o.v["_full_depth"] or o.v["face"] == face):
                raise OpError("conflict", f"{_u(max(pos, op_))} is occupied by device {o.id}.",
                              {"field": "position", "reason": f"{_u(max(pos, op_))} occupied by device {o.id}", "device_id": o.id})
        shown = {"rack_id": f["rack_id"], "position": f["position"], "face": face}
        resolved = {"rack_id": rack.id, "position": f["position"], "face": face}
        if rack.v["location_id"] != dev.v["location_id"]:  # NetBox requires device.location == rack.location
            shown["location_id"] = resolved["location_id"] = rack.v["location_id"]
        p.key = dev.key
        self.change(p, dev, shown, resolved)

    def unplace_device(self, p: Planned, op: Any, f: dict[str, Any]) -> None:
        dev = self.resolve("device", p.target_id)
        self.lock(dev, op.if_unmodified_since)
        p.key = dev.key
        blank = {"rack_id": None, "position": None, "face": None}
        self.change(p, dev, blank, dict(blank))

    def create_rack(self, p: Planned, op: Any, f: dict[str, Any]) -> None:
        site = self.resolve("site", f["site_id"], "site_id")
        if f.get("location_id") is not None:
            loc = self.resolve("location", f["location_id"], "location_id")
            if loc.v["site_id"] != site.id:
                raise OpError("validation_failed", f"Location {loc.id} is not in site {site.id}.", {"field": "location_id"})
        f = {"status": "planned", "u_height": 42, **f}
        if f["status"] not in STATUS["rack"]:
            raise OpError("validation_failed", f"Rack status {f['status']!r} is not a NetBox rack status.",
                          {"field": "status", "allowed": STATUS["rack"]})
        self.unique_name(None, f.get("location_id"), f["name"])
        shown = {k: f[k] for k in CREATE_RACK_ORDER if k in f}
        rec = Rec("rack", f"@{op.client_ref}" if op.client_ref else f"#{p.index}", None,
                  {"location_id": None, "description": "", "comments": "", "iso_x": None, "iso_y": None, "iso_facing": None,
                   "latitude": None, "longitude": None, "starting_unit": 1, **shown})
        self.st.add(rec)
        p.key, p.before, p.after, p.write = rec.key, None, shown, dict(shown)

    def update_fields(self, p: Planned, op: Any, f: dict[str, Any]) -> None:
        rec = self.resolve(p.kind, p.target_id)
        self.lock(rec, op.if_unmodified_since)
        allowed = WHITELIST[p.kind]
        for k in f:
            if k not in allowed:
                raise OpError("validation_failed", f"Field '{k}' is not editable on {p.kind} in contract {CONTRACT_VERSION}.",
                              {"field": k, "allowed": allowed})
        for k, v in f.items():
            err = next(FIELD_RULES[(p.kind, k)].iter_errors(v), None)
            if err is not None:
                raise OpError("validation_failed", f"Value for {p.kind} field '{k}' is not valid: {err.message[:200]}",
                              {"field": k, "reason": err.message[:200]})
        if p.kind == "rack" and "name" in f:
            self.unique_name(rec, rec.v["location_id"], f["name"])
        if p.kind == "location" and "name" in f:
            self.unique_location_name(rec, f["name"])
        if p.kind == "device" and "name" in f:
            self.unique_device_name(rec, f["name"])
        if p.kind == "device" and f.get("asset_tag") is not None:
            self.unique_asset_tag(rec, f["asset_tag"])
        if p.kind == "site" and "name" in f:
            self.unique_site_name(rec, f["name"])
        if p.kind == "site":
            for k in ("latitude", "longitude"):
                if f.get(k) is not None:
                    self.site_coordinate(k, f[k])
        if p.kind == "cable" and ("length" in f or "length_unit" in f):
            self.cable_length(rec, f)
        p.key = rec.key
        self.change(p, rec, f, dict(f))


def plan(ops: list[Any], state: State) -> list[Planned]:
    """Validate and simulate the ops in order. Pure: `state` is not modified."""
    pl = Planner(state)
    out: list[Planned] = []
    for i, op in enumerate(ops):
        tid = op.target.id.root if hasattr(op.target.id, "root") else op.target.id
        p = Planned(index=i, op=op.op, client_ref=op.client_ref, kind=op.target.kind, target_id=tid)
        fields = op.fields.model_dump(mode="json", exclude_unset=True) if hasattr(op.fields, "model_dump") else {}
        fields = {k: (v.root if hasattr(v, "root") else v) for k, v in fields.items()}
        try:
            if op.client_ref and op.client_ref in pl.refs:
                raise OpError("validation_failed", f"client_ref {op.client_ref!r} is used by an earlier op.",
                              {"field": "client_ref"})
            getattr(pl, op.op)(p, op, fields)
        except OpError as e:
            p.status, p.error, p.before, p.after, p.write, p.key = "error", e.err, None, None, {}, None
        if op.client_ref and op.client_ref not in pl.refs:
            pl.refs[op.client_ref] = p.key
        out.append(p)
    return out


# ---------------------------------------------------------------- preview / apply
def _payload(kind: str, fields: dict[str, Any], real: dict[Key, int]) -> dict[str, Any]:
    """Contract field names -> NetBox body. iso_* and a location's/rack's GPS are custom fields (NetBox merges a
    partial custom_fields dict on PATCH); *_id fields are the FK without the suffix; created keys -> real ids."""
    out: dict[str, Any] = {}
    cf: dict[str, Any] = {}
    for k, v in fields.items():
        if isinstance(v, str) and k.endswith("_id"):
            v = real[("location" if k == "location_id" else "rack", v)]
        if k in CUSTOM:
            cf[k] = v
        elif k in ("latitude", "longitude") and kind in ("location", "rack"):
            cf["iso_" + k] = v
        elif k in ("site_id", "location_id", "rack_id"):
            out[k[:-3]] = v
        else:
            out[k] = v
    if cf:
        out["custom_fields"] = cf
    return out


def result(mode: str, planned: list[Planned], write_enabled: bool, applied: bool = False,
           ids: dict[int, Any] | None = None) -> c.ChangeResult:
    return c.ChangeResult(mode=mode, applied=applied, write_enabled=write_enabled,
                          ops=[p.result((ids or {}).get(p.index)) for p in planned])


async def preview(client: nb.NetBox, cs: c.ChangeSet) -> list[Planned]:
    await client.version()  # never plan (or write) against a NetBox outside 4.7.x
    return plan(cs.ops, await gather(client, cs.ops))


async def apply(client: nb.NetBox, cs: c.ChangeSet, write_enabled: bool, player: str | None = None) -> c.ChangeResult:
    planned = await preview(client, cs)
    if any(p.status == "error" for p in planned):
        for p in planned:
            if p.status != "error":
                p.status, p.after = "skipped", None
        return result("apply", planned, write_enabled)
    log.info("apply: %d ops, player=%r, reason=%r", len(planned), player, cs.reason)
    # NetBox >= 4.4 stores `changelog_message` on each ObjectChange (measured on 4.7.4): the reason lands in the audit
    # trail. NetBox records the proxy's own user as the author, so the message names the player who asked for the write.
    who = f"rackwalker ({player})" if player else "rackwalker"
    message = (f"{who}: {cs.reason}" if cs.reason else f"{who} apply")[:200]
    real: dict[Key, int] = {}
    ids: dict[int, Any] = {}
    written: list[int] = []
    for n, p in enumerate(planned):
        kind = p.key[0] if p.key else p.kind
        try:
            if p.status == "no_change":
                ids[p.index] = real.get(p.key, p.key[1])  # type: ignore[index]
                continue
            body = {**_payload(kind, p.write, real), "changelog_message": message}
            if p.op == "create_rack":
                obj = await client.request("POST", ENDPOINT["rack"] + "/", json=body)
                real[p.key] = obj["id"]  # type: ignore[index]
            else:
                oid = real.get(p.key, p.key[1])  # type: ignore[index]
                obj = await client.request("PATCH", f"{ENDPOINT[kind]}/{oid}/", json=body)
            written.append(p.index)
            ids[p.index] = obj["id"]
            # `after` is NetBox's own answer to the write (its serialisation after save), in contract names; a
            # `before` that named an object created earlier in the set (rack_id "@x") now shows its real id.
            back = to_rec(kind, nb.parse(MODEL[kind], obj, ENDPOINT[kind]), {})  # planner-only `_` facts unused here
            p.after = {f: back.v.get(f) for f in (p.after or {})}
            if p.before is not None:
                p.before = {f: real.get(("rack", v), v) if f == "rack_id" else v for f, v in p.before.items()}
            log.info("apply: op %d %s %s %s ok", p.index, p.op, kind, obj["id"])
        except ApiError as e:
            later = [q.index for q in planned[n + 1:]]
            # A transport failure after the request went out (read timeout, lost connection): NetBox may have
            # committed this op, so its outcome is unknown and nothing may be reported as "not written".
            unknown = e.code == "netbox_unreachable" and bool(e.details and e.details.get("maybe_sent"))
            details = {"partial": True if written else (None if unknown else False), "written_ops": written,
                       "skipped_ops": later, "outcome_unknown": unknown,
                       **({"netbox": e.details.get("netbox")} if e.details and "netbox" in e.details else {})}
            code = "validation_failed" if e.details and e.details.get("status") == 400 else e.code
            done = (f"Ops {written} were already written and stay in NetBox (see its changelog); " if written else
                    ("No earlier op was written; " if unknown else "Nothing was written; "))
            head = (f"NetBox did not answer op {p.index} ({p.op}) after the request was sent ({e.details.get('reason')}): "  # type: ignore[union-attr]
                    "it may or may not have been written; check the NetBox changelog before retrying. "
                    if unknown else f"NetBox refused op {p.index} ({p.op}): {e.message} ")
            msg = head + done + f"ops {later} were skipped."
            p.status, p.error, p.after = "error", ApiError(code, msg, details), None
            for q in planned[n + 1:]:
                q.status, q.after = "skipped", None
                if q.key in real:  # aimed at an object created earlier in the set: show its real id
                    ids[q.index] = real[q.key]  # type: ignore[index]
            log.warning("apply: op %d failed (%s); written=%s skipped=%s", p.index, e.code, written, later)
            return result("apply", planned, write_enabled, False, ids)
    return result("apply", planned, write_enabled, True, ids)
