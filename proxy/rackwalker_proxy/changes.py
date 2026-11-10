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
    "site": ["name", "description", "status", "latitude", "longitude", "iso_theme", "facility", "country"],
    "location": ["name", "description", "status", "iso_x", "iso_y", "iso_w", "iso_h", "iso_facing", "latitude", "longitude"],
    "rack": ["name", "description", "status", "comments", "iso_x", "iso_y", "iso_facing", "latitude", "longitude"],
    "device": ["name", "status", "serial", "asset_tag", "description", "comments", "platform_id"],
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
# Ops added in contract 2.3.0 (what a quest line ends in): each creates one NetBox object, or assigns an address.
NET_OPS = ("connect_cable", "create_circuit", "create_circuit_termination", "assign_ip", "create_prefix",
           # 2.4.0: how a device is managed, and a circuit's administrative record
           "set_management", "record_circuit", "create_provider_account", "create_provider_network", "create_contact", "assign_contact")
# Ops of NET_OPS that change an existing object: they create nothing, so no later op can refer to their result.
NET_UPDATES = ("assign_ip", "set_management", "record_circuit", "assign_contact")
# The custom fields the 2.4.0 ops write (created by devnetbox/seed.py --fields-only).
MGMT_FIELDS = ("rw_mgmt_mode", "rw_mgmt_service")
COST_FIELDS = {"mrc": "rw_mrc", "nrc": "rw_nrc", "currency": "rw_currency"}
# A cable end's contract kind -> NetBox content type.
END_TYPE = {"interface": "dcim.interface", "front_port": "dcim.frontport", "rear_port": "dcim.rearport", "power_port": "dcim.powerport",
            "power_outlet": "dcim.poweroutlet", "circuit_termination": "circuits.circuittermination"}
CREATE_RACK_ORDER = list(SCHEMA["$defs"]["CreateRackOp"]["properties"]["fields"]["properties"])

# Value rules for update_fields: the contract's own property schema for that field (by entity) AND NetBox's
# limits (max lengths, status choices). Fields the contract entities do not carry use NetBox's limits alone.
_CONTRACT_PROP = {"site": "Site", "location": "Location", "rack": "Rack", "device": "Device", "cable": "Cable",
                  "interface": "Interface"}
_EXTRA_PROP = {("device", f): "DeviceInfo" for f in ("serial", "asset_tag", "description", "comments", "platform_id")}
_EXTRA_PROP[("site", "facility")] = "DeviceInfo"  # not a contract entity field: NetBox's limits alone apply
_NETBOX_LIMITS: dict[str, dict[str, Any]] = {
    "name": {"type": "string", "minLength": 1, "maxLength": 100},
    "description": {"type": "string", "maxLength": 200},
    "comments": {"type": "string"},
    "serial": {"maxLength": 50},
    "asset_tag": {"type": ["string", "null"], "minLength": 1, "maxLength": 50},
    "label": {"maxLength": 100},
    "facility": {"type": "string", "maxLength": 50},
    "country": {"type": ["string", "null"], "pattern": "^[A-Z]{2}$"},
    "platform_id": {"type": ["integer", "null"], "minimum": 1},
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
    country = s.cf("iso_country")
    return Rec("site", s.id, s.last_updated, {"name": s.name, "description": s.description, "status": s.status,
                                              "latitude": s.latitude, "longitude": s.longitude, "iso_theme": theme,
                                              "facility": s.facility or "", "country": country or None})


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
        "platform_id": d.platform.id if d.platform else None,
        # planner-only facts (never shown in before/after, which list only touched fields)
        "_u_height": dt.u_height if dt else 1.0, "_full_depth": dt.is_full_depth if dt else True,
        "_child": bool(d.parent_device) or (dt is not None and dt.subdevice_role == "child"),
        "_tenant_id": d.tenant.id if d.tenant else None, "_primary_ip": d.primary_ip.address if d.primary_ip else None,
        "_manufacturer_id": dt.manufacturer.id if dt else None,
        "_mgmt_mode": d.cf("rw_mgmt_mode"), "_mgmt_service": d.cf("rw_mgmt_service") or None,
        "_has_mgmt": all(f in d.custom_fields for f in MGMT_FIELDS), "_type_id": d.device_type.id, "_model": dt.model if dt else None,
        "_mgmt_default": nb._unwrap(dt.custom_fields.get("rw_mgmt_default")) if dt else None,
        "_has_default": dt is not None and "rw_mgmt_default" in dt.custom_fields})


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
        if isinstance(v, int) and not isinstance(v, bool) and kind in want:
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
    await gather_net(client, ops, st)
    return st


def _fields(op: Any) -> dict[str, Any]:
    f = op.fields.model_dump(mode="json", exclude_unset=True) if hasattr(op.fields, "model_dump") else {}
    return {k: (v.root if hasattr(v, "root") else v) for k, v in f.items()}


async def _one(client: nb.NetBox, path: str, oid: int) -> dict[str, Any] | None:
    rows = await client.list_raw(path, {"id": oid})
    return rows[0] if rows else None


async def gather_net(client: nb.NetBox, ops: list[Any], st: State) -> None:
    """What the 2.3.0 ops can touch: the cable ends they name, circuits with their terminations, providers, circuit
    types, platforms, and any address or prefix that already holds a value they set."""
    for op in ops:
        f = _fields(op)
        if op.op == "update_fields" and isinstance(f.get("platform_id"), int) and ("platform", f["platform_id"]) not in st.recs:
            row = await _one(client, "dcim/platforms", f["platform_id"])
            if row:
                maker = row.get("manufacturer") or {}
                st.add(Rec("platform", row["id"], None, {"name": row["name"], "manufacturer_id": maker.get("id"), "manufacturer": maker.get("name")}))
        if op.op == "connect_cable":
            for side in ("a", "b"):
                kind, oid = f[f"{side}_kind"], f[f"{side}_id"]
                if isinstance(oid, int) and ("end:" + kind, oid) not in st.recs:
                    row = await _one(client, nb.PORT_ENDPOINT[kind], oid)
                    if row:
                        dev = row.get("device") or {}
                        st.add(Rec("end:" + kind, oid, None, {"cable": (row.get("cable") or {}).get("id"), "name": row.get("name") or row.get("term_side"),
                                                              "device_id": dev.get("id"), "device": dev.get("name")}))
        if op.op == "create_circuit":
            for kind, path, oid in (("provider", "circuits/providers", f["provider_id"]), ("circuit_type", "circuits/circuit-types", f["type_id"])):
                row = await _one(client, path, oid)
                if row:
                    st.add(Rec(kind, oid, None, {"name": row["name"]}))
            for row in await client.list_raw("circuits/circuits", {"cid": f["cid"], "provider_id": f["provider_id"]}):
                st.add(Rec("circuit", row["id"], None, {"cid": row["cid"], "provider_id": f["provider_id"]}))
        if op.op in ("create_provider_account", "create_provider_network") and ("provider", f["provider_id"]) not in st.recs:
            row = await _one(client, "circuits/providers", f["provider_id"])
            if row:
                st.add(Rec("provider", row["id"], None, {"name": row["name"]}))
        if op.op == "create_provider_account":
            for row in await client.list_raw("circuits/provider-accounts", {"provider_id": f["provider_id"], "account": f["account"]}):
                st.add(Rec("provider_account", row["id"], None, {"provider_id": f["provider_id"], "account": row["account"]}))
        if op.op == "create_provider_network":
            for row in await client.list_raw("circuits/provider-networks", {"provider_id": f["provider_id"], "name": f["name"]}):
                st.add(Rec("provider_network", row["id"], None, {"provider_id": f["provider_id"], "name": row["name"]}))
        if op.op == "create_circuit_termination" and isinstance(f.get("provider_network_id"), int):
            row = await _one(client, "circuits/provider-networks", f["provider_network_id"])
            if row:
                st.add(Rec("provider_network", row["id"], None, {"provider_id": (row.get("provider") or {}).get("id"), "name": row["name"]}))
        if op.op == "record_circuit":
            tid = op.target.id.root if hasattr(op.target.id, "root") else op.target.id
            row = await _one(client, "circuits/circuits", tid)
            if row:
                cf = row.get("custom_fields") or {}
                st.add(Rec("circuit", row["id"], row.get("last_updated"), {
                    "cid": row["cid"], "provider_id": (row.get("provider") or {}).get("id"),
                    "provider_account_id": (row.get("provider_account") or {}).get("id"), "install_date": row.get("install_date"),
                    "commit_rate": row.get("commit_rate"), "mrc": _money(cf.get("rw_mrc")), "nrc": _money(cf.get("rw_nrc")),
                    "currency": cf.get("rw_currency") or None, "_has_cost": all(v in cf for v in COST_FIELDS.values())}))
            if isinstance(f.get("provider_account_id"), int):
                acct = await _one(client, "circuits/provider-accounts", f["provider_account_id"])
                if acct:
                    st.add(Rec("provider_account", acct["id"], None, {"provider_id": (acct.get("provider") or {}).get("id"), "account": acct["account"]}))
        if op.op == "assign_contact":
            tid = op.target.id.root if hasattr(op.target.id, "root") else op.target.id
            kind = op.target.kind
            if (kind, tid) not in st.recs:
                row = await _one(client, "circuits/providers" if kind == "provider" else "circuits/circuits", tid)
                if row:
                    st.add(Rec(kind, row["id"], None, {"name": row.get("name") or row.get("cid"), "cid": row.get("cid"),
                                                      "provider_id": (row.get("provider") or {}).get("id")}))
            role = await _one(client, "tenancy/contact-roles", f["role_id"])
            if role:
                st.add(Rec("contact_role", role["id"], None, {"name": role["name"]}))
            if isinstance(f["contact_id"], int):
                contact = await _one(client, "tenancy/contacts", f["contact_id"])
                if contact:
                    st.add(Rec("contact", contact["id"], None, {"name": contact["name"]}))
                for row in await client.list_raw("tenancy/contact-assignments", {"object_type": f"circuits.{kind}", "object_id": tid,
                                                                                  "contact_id": f["contact_id"], "role_id": f["role_id"]}):
                    st.add(Rec("contact_assignment", row["id"], None, {"object": (kind, tid), "contact_id": f["contact_id"], "role_id": f["role_id"]}))
        if op.op == "create_circuit_termination":
            if isinstance(f["circuit_id"], int):
                row = await _one(client, "circuits/circuits", f["circuit_id"])
                if row:
                    if ("circuit", row["id"]) not in st.recs:   # a fuller record of it (record_circuit) is kept
                        st.add(Rec("circuit", row["id"], None, {"cid": row["cid"], "provider_id": (row.get("provider") or {}).get("id")}))
                    for term in await client.list_raw("circuits/circuit-terminations", {"circuit_id": row["id"]}):
                        st.add(Rec("circuit_termination", term["id"], None, {"circuit_id": row["id"], "term_side": term["term_side"]}))
        if op.op == "assign_ip":
            row = await _one(client, "dcim/interfaces", f["interface_id"])
            if row:
                st.add(Rec("end:interface", row["id"], None, {"cable": (row.get("cable") or {}).get("id"), "name": row["name"],
                                                              "device_id": (row.get("device") or {}).get("id"), "device": (row.get("device") or {}).get("name")}))
            for ip in await client.list_raw("ipam/ip-addresses", {"address": f["address"]}):
                st.add(Rec("ip_address", ip["id"], None, {"address": ip["address"], "assigned_type": ip.get("assigned_object_type"),
                                                          "assigned_id": ip.get("assigned_object_id")}))
        if op.op == "create_prefix":
            for pf in await client.list_raw("ipam/prefixes", {"prefix": f["prefix"]}):
                st.add(Rec("prefix", pf["id"], None, {"prefix": pf["prefix"]}))


# ---------------------------------------------------------------- plan (pure)
def _money(v: Any) -> str | None:
    """An amount NetBox gave back (a decimal custom field) as decimal text with two places."""
    if v is None or v == "":
        return None
    try:
        return f"{Decimal(str(v)):.2f}"
    except ArithmeticError:
        return None


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
        if p.kind == "device" and f.get("platform_id") is not None and ("platform", f["platform_id"]) not in self.st.recs:
            raise OpError("not_found", f"Platform {f['platform_id']} does not exist.", {"field": "platform_id", "id": f["platform_id"]})
        if p.kind == "device" and f.get("platform_id") is not None:
            # NetBox refuses a platform that is tied to another manufacturer: said in the preview, not after Apply
            plat = self.st.recs[("platform", f["platform_id"])]
            if plat.v.get("manufacturer_id") is not None and rec.v.get("_manufacturer_id") not in (None, plat.v["manufacturer_id"]):
                raise OpError("validation_failed", f"Platform {plat.v['name']} is only for {plat.v.get('manufacturer') or 'another manufacturer'} "
                              f"devices in NetBox; {rec.v['name'] or 'this device'} is of another make.", {"field": "platform_id"})
        p.key = rec.key
        self.change(p, rec, f, dict(f))


    # -- ops added in 2.3.0 --
    def _new(self, p: Planned, op: Any, kind: str, values: dict[str, Any], shown: dict[str, Any]) -> Rec:
        rec = Rec(kind, f"@{op.client_ref}" if op.client_ref else f"#{p.index}", None, values)
        self.st.add(rec)
        p.key, p.before, p.after, p.write = rec.key, None, shown, dict(shown)
        return rec

    def _end(self, kind: str, ident: Any, field_name: str) -> Rec:
        if isinstance(ident, str):
            rec = self.resolve("circuit_termination", ident, field_name)
            if kind != "circuit_termination":
                raise OpError("validation_failed", f"Reference {ident} names a circuit termination, not a {kind}.", {"field": field_name})
            return rec
        rec = self.st.recs.get(("end:" + kind, ident))
        if rec is None:
            raise OpError("not_found", f"{kind.replace('_', ' ').capitalize()} {ident} does not exist.", {"field": field_name, "kind": kind, "id": ident})
        return rec

    def connect_cable(self, p: Planned, op: Any, f: dict[str, Any]) -> None:
        a, b = self._end(f["a_kind"], f["a_id"], "a_id"), self._end(f["b_kind"], f["b_id"], "b_id")
        if a is b:
            raise OpError("validation_failed", "A cable needs two different ends.", {"field": "b_id"})
        power = {"power_port", "power_outlet"}
        if (f["a_kind"] in power) != (f["b_kind"] in power):
            raise OpError("validation_failed", "A power port connects only to a power outlet, not to a data port.", {"field": "b_kind"})
        for side, rec in (("a_id", a), ("b_id", b)):
            if rec.v.get("cable") is not None:
                where = f"{rec.v.get('device') or ''} {rec.v.get('name') or ''}".strip() or "That end"
                raise OpError("conflict", f"{where} already has a cable (NetBox: one cable per port).", {"field": side, "cable_id": rec.v["cable"]})
        shown = {k: f[k] for k in ("a_kind", "a_id", "b_kind", "b_id", "type", "label") if k in f}
        rec = self._new(p, op, "cable", {"label": f.get("label"), "status": "connected", "type": f.get("type"), "color": None,
                                         "length": None, "length_unit": None}, shown)
        p.write = {**shown, "a_id": a.id, "b_id": b.id}
        a.v["cable"] = b.v["cable"] = rec.id

    def create_circuit(self, p: Planned, op: Any, f: dict[str, Any]) -> None:
        for kind, key in (("provider", "provider_id"), ("circuit_type", "type_id")):
            if (kind, f[key]) not in self.st.recs:
                raise OpError("not_found", f"{kind.replace('_', ' ').capitalize()} {f[key]} does not exist.", {"field": key, "id": f[key]})
        for c in self.st.of("circuit"):
            if c.v["cid"] == f["cid"] and c.v.get("provider_id") == f["provider_id"]:
                raise OpError("validation_failed", f"Circuit {f['cid']} already exists for that provider (NetBox: a circuit ID is "
                              "unique per provider).", {"field": "cid"})
        self._new(p, op, "circuit", {"cid": f["cid"], "provider_id": f["provider_id"]}, dict(f))

    def create_circuit_termination(self, p: Planned, op: Any, f: dict[str, Any]) -> None:
        circuit = self.resolve("circuit", f["circuit_id"], "circuit_id")
        network = None
        if f.get("provider_network_id") is not None:
            network = self.resolve("provider_network", f["provider_network_id"], "provider_network_id")
            if network.v.get("provider_id") != circuit.v.get("provider_id"):
                raise OpError("validation_failed", f"Network {network.v['name']} is not a network of the provider of circuit {circuit.v['cid']}.",
                              {"field": "provider_network_id"})
        elif ("site", f["site_id"]) not in self.st.recs:
            raise OpError("not_found", f"Site {f['site_id']} does not exist.", {"field": "site_id", "kind": "site", "id": f["site_id"]})
        for term in self.st.of("circuit_termination"):
            if term.v.get("circuit_id") == circuit.id and term.v.get("term_side") == f["term_side"]:
                raise OpError("conflict", f"Side {f['term_side']} of circuit {circuit.v['cid']} already has a termination.", {"field": "term_side"})
        self._new(p, op, "circuit_termination", {"circuit_id": circuit.id, "term_side": f["term_side"], "cable": None,
                                                 "name": f["term_side"], "device": f"circuit {circuit.v['cid']}"}, dict(f))
        p.write = {**f, "circuit_id": circuit.id, **({"provider_network_id": network.id} if network is not None else {})}

    # -- ops added in 2.4.0 --
    def set_management(self, p: Planned, op: Any, f: dict[str, Any]) -> None:
        dev = self.resolve("device", p.target_id)
        self.lock(dev, op.if_unmodified_since)
        if not dev.v.get("_has_mgmt"):
            raise OpError("validation_failed", "This NetBox has no custom field rw_mgmt_mode on devices yet, so it cannot hold how a device "
                          "is managed. The setup script (devnetbox/seed.py --fields-only) adds it.", {"field": "mode"})
        if "model_default" in f and not dev.v.get("_has_default"):
            raise OpError("validation_failed", "This NetBox has no custom field rw_mgmt_default on device types yet. The setup script "
                          "(devnetbox/seed.py --fields-only) adds it.", {"field": "model_default"})
        if f.get("service") and f["mode"] != "cloud":
            raise OpError("validation_failed", "A managing service is recorded only for a device that a cloud or controller service manages.",
                          {"field": "service"})
        p.key = dev.key
        before = {"mode": dev.v.get("_mgmt_mode"), "service": dev.v.get("_mgmt_service"), "model_default": dev.v.get("_mgmt_default")}
        p.before = {k: before[k] for k in f}
        p.after = dict(f)
        p.write = {**f, "type_id": dev.v.get("_type_id")}
        dev.v.update(_mgmt_mode=f["mode"], **({"_mgmt_service": f["service"]} if "service" in f else {}),
                     **({"_mgmt_default": f["model_default"]} if "model_default" in f else {}))
        if p.before == p.after:
            p.status = "no_change"

    def record_circuit(self, p: Planned, op: Any, f: dict[str, Any]) -> None:
        circuit = self.resolve("circuit", p.target_id)
        if "install_date" not in circuit.v:
            raise OpError("not_found", f"Circuit {p.target_id} does not exist.", {"kind": "circuit", "id": p.target_id})
        write = dict(f)
        if "provider_account_id" in f:
            account = self.resolve("provider_account", f["provider_account_id"], "provider_account_id")
            if account.v.get("provider_id") != circuit.v.get("provider_id"):
                raise OpError("validation_failed", f"That account is not an account with the provider of circuit {circuit.v['cid']}.",
                              {"field": "provider_account_id"})
            write["provider_account_id"] = account.id
        if "install_date" in f:
            try:
                datetime.fromisoformat(f["install_date"])
            except ValueError:
                raise OpError("validation_failed", f"{f['install_date']!r} is not a date (year-month-day).", {"field": "install_date"}) from None
        if any(k in f for k in COST_FIELDS):
            if not circuit.v.get("_has_cost"):
                raise OpError("validation_failed", "This NetBox has no custom fields rw_mrc, rw_nrc and rw_currency on circuits yet, so it "
                              "cannot hold what a circuit costs. The setup script (devnetbox/seed.py --fields-only) adds them.", {"field": "mrc"})
            if ("mrc" in f or "nrc" in f) and not (f.get("currency") or circuit.v.get("currency")):
                raise OpError("validation_failed", "A charge needs its currency (three letters, for example GBP).", {"field": "currency"})
        for k in ("mrc", "nrc"):
            if k in write:
                write[k] = _money(write[k])
        # a fact NetBox already holds is not this op's to change: someone recorded it, and it is theirs
        for k, v in write.items():
            have = circuit.v.get(k)
            if have not in (None, "") and have != v:
                raise OpError("conflict", f"Circuit {circuit.v['cid']} already has a value for {k.replace('_id', '').replace('_', ' ')} in NetBox; "
                              "change it in NetBox itself if it is wrong.", {"field": k})
        p.key = circuit.key
        p.before = {k: circuit.v.get(k) for k in f}
        p.after = {k: write[k] for k in f}
        p.write = write
        circuit.v.update(write)
        if p.before == p.after:
            p.status = "no_change"

    def create_provider_account(self, p: Planned, op: Any, f: dict[str, Any]) -> None:
        if ("provider", f["provider_id"]) not in self.st.recs:
            raise OpError("not_found", f"Provider {f['provider_id']} does not exist.", {"field": "provider_id", "id": f["provider_id"]})
        for acct in self.st.of("provider_account"):
            if acct.v.get("provider_id") == f["provider_id"] and acct.v.get("account") == f["account"]:
                raise OpError("conflict", "That provider already has an account with this number in NetBox.", {"field": "account"})
        self._new(p, op, "provider_account", {"provider_id": f["provider_id"], "account": f["account"]}, dict(f))

    def create_provider_network(self, p: Planned, op: Any, f: dict[str, Any]) -> None:
        if ("provider", f["provider_id"]) not in self.st.recs:
            raise OpError("not_found", f"Provider {f['provider_id']} does not exist.", {"field": "provider_id", "id": f["provider_id"]})
        for net in self.st.of("provider_network"):
            if net.v.get("provider_id") == f["provider_id"] and str(net.v.get("name", "")).lower() == f["name"].lower():
                raise OpError("conflict", f"That provider already has a network called {f['name']} in NetBox.", {"field": "name"})
        self._new(p, op, "provider_network", {"provider_id": f["provider_id"], "name": f["name"]}, dict(f))

    def create_contact(self, p: Planned, op: Any, f: dict[str, Any]) -> None:
        import re

        if f.get("email") and not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", f["email"]):
            raise OpError("validation_failed", "That is not an email address.", {"field": "email"})
        self._new(p, op, "contact", {"name": f["name"]}, dict(f))

    def assign_contact(self, p: Planned, op: Any, f: dict[str, Any]) -> None:
        target = self.st.recs.get((p.kind, p.target_id))
        if target is None:
            raise OpError("not_found", f"{p.kind.capitalize()} {p.target_id} does not exist.", {"kind": p.kind, "id": p.target_id})
        contact = self.resolve("contact", f["contact_id"], "contact_id")
        role = self.st.recs.get(("contact_role", f["role_id"]))
        if role is None:
            raise OpError("not_found", f"Contact role {f['role_id']} does not exist.", {"field": "role_id", "id": f["role_id"]})
        for a in self.st.of("contact_assignment"):
            if a.v["object"] == (p.kind, p.target_id) and a.v["contact_id"] == contact.id and a.v["role_id"] == role.id:
                raise OpError("conflict", f"{contact.v['name']} already is the {role.v['name']} contact there.", {"field": "contact_id"})
        self.st.add(Rec("contact_assignment", f"#{p.index}", None, {"object": (p.kind, p.target_id), "contact_id": contact.id, "role_id": role.id}))
        p.key = target.key
        p.before = None
        p.after = dict(f)
        p.write = {"contact_id": contact.id, "role_id": role.id}

    def assign_ip(self, p: Planned, op: Any, f: dict[str, Any]) -> None:
        import ipaddress

        dev = self.resolve("device", p.target_id)
        self.lock(dev, op.if_unmodified_since)
        try:
            addr = ipaddress.ip_interface(f["address"])
        except ValueError:
            raise OpError("validation_failed", f"{f['address']!r} is not an IP address with a mask (for example 192.0.2.10/24).", {"field": "address"}) from None
        if "/" not in f["address"]:
            raise OpError("validation_failed", "The address needs its mask (for example /24).", {"field": "address"})
        iface = self.st.recs.get(("end:interface", f["interface_id"]))
        if iface is None or iface.v.get("device_id") != dev.id:
            raise OpError("validation_failed", f"Interface {f['interface_id']} is not an interface of {dev.v['name'] or 'that device'}.", {"field": "interface_id"})
        existing = None
        for ip in self.st.of("ip_address"):
            if ipaddress.ip_interface(ip.v["address"]).ip != addr.ip:
                continue
            if ip.v.get("assigned_type") == "dcim.interface" and ip.v.get("assigned_id") == iface.id and ip.v["address"] == str(addr):
                existing = ip
            else:
                raise OpError("conflict", f"{addr.ip} is already in NetBox as {ip.v['address']}"
                              + (", on another interface." if ip.v.get("assigned_id") else ", not assigned to an interface."),
                              {"field": "address", "ip_address_id": ip.id})
        primary = f.get("primary", True)
        p.key = dev.key
        p.before = {"primary_ip": dev.v.get("_primary_ip")}
        p.after = {"address": str(addr), "interface_id": iface.id, "primary_ip": str(addr) if primary else dev.v.get("_primary_ip")}
        p.write = {"address": str(addr), "interface_id": iface.id, "primary": primary, "ip_id": existing.id if existing else None,
                   "family": addr.version}
        dev.v["_primary_ip"] = p.after["primary_ip"]
        if existing is None:
            self.st.add(Rec("ip_address", f"#{p.index}", None, {"address": str(addr), "assigned_type": "dcim.interface", "assigned_id": iface.id}))

    def create_prefix(self, p: Planned, op: Any, f: dict[str, Any]) -> None:
        import ipaddress

        try:
            net = ipaddress.ip_network(f["prefix"], strict=True)
        except ValueError:
            raise OpError("validation_failed", f"{f['prefix']!r} is not a network with its mask and no host bits (for example 192.0.2.0/24).",
                          {"field": "prefix"}) from None
        for pf in self.st.of("prefix"):
            if pf.v["prefix"] == str(net):
                raise OpError("conflict", f"Prefix {net} already exists in NetBox.", {"field": "prefix", "prefix_id": pf.id})
        self._new(p, op, "prefix", {"prefix": str(net)}, {"prefix": str(net)})


async def write_net(client: nb.NetBox, p: Planned, real: dict[Key, int], message: str) -> dict[str, Any]:
    """Writes one 2.3.0 op. Returns the created object (assign_ip: the device)."""
    w = p.write

    def rid(kind: str, v: Any) -> int:
        return real[(kind, v)] if isinstance(v, str) else v

    if p.op == "connect_cable":
        ends = {side: [{"object_type": END_TYPE[w[f"{side}_kind"]],
                        "object_id": rid("circuit_termination", w[f"{side}_id"])}] for side in ("a", "b")}
        body = {"a_terminations": ends["a"], "b_terminations": ends["b"], "status": "connected",
                **({"type": w["type"]} if w.get("type") else {}), **({"label": w["label"]} if w.get("label") else {})}
        return await client.request("POST", "dcim/cables/", json={**body, "changelog_message": message})
    if p.op == "create_circuit":
        return await client.request("POST", "circuits/circuits/", json={"cid": w["cid"], "provider": w["provider_id"], "type": w["type_id"],
                                                                        "status": "active", "changelog_message": message})
    if p.op == "create_circuit_termination":
        at = ({"termination_type": "circuits.providernetwork", "termination_id": rid("provider_network", w["provider_network_id"])}
              if w.get("provider_network_id") is not None else {"termination_type": "dcim.site", "termination_id": w["site_id"]})
        return await client.request("POST", "circuits/circuit-terminations/", json={
            "circuit": rid("circuit", w["circuit_id"]), "term_side": w["term_side"], **at, "changelog_message": message})
    if p.op == "set_management":
        cf = {"rw_mgmt_mode": w["mode"], **({"rw_mgmt_service": w["service"] or None} if "service" in w else {})}
        if "model_default" in w and w.get("type_id") is not None:
            # the model's default first: if NetBox refuses it, the device has not been touched
            type_cf = {"rw_mgmt_default": w["model_default"],
                       **({"rw_mgmt_service": w["service"]} if w["model_default"] == "cloud" and w.get("service") else {})}
            await client.request("PATCH", f"dcim/device-types/{w['type_id']}/", json={"custom_fields": type_cf, "changelog_message": message})
        return await client.request("PATCH", f"dcim/devices/{p.key[1]}/", json={"custom_fields": cf, "changelog_message": message})  # type: ignore[index]
    if p.op == "record_circuit":
        body: dict[str, Any] = {k: w[k] for k in ("install_date", "commit_rate") if k in w}
        if "provider_account_id" in w:
            body["provider_account"] = rid("provider_account", w["provider_account_id"])
        cf = {name: w[k] for k, name in COST_FIELDS.items() if k in w}
        if cf:
            body["custom_fields"] = cf
        return await client.request("PATCH", f"circuits/circuits/{p.key[1]}/", json={**body, "changelog_message": message})  # type: ignore[index]
    if p.op == "create_provider_account":
        return await client.request("POST", "circuits/provider-accounts/", json={
            "provider": w["provider_id"], "account": w["account"], **({"name": w["name"]} if w.get("name") else {}), "changelog_message": message})
    if p.op == "create_provider_network":
        return await client.request("POST", "circuits/provider-networks/", json={"provider": w["provider_id"], "name": w["name"],
                                                                                 "changelog_message": message})
    if p.op == "create_contact":
        return await client.request("POST", "tenancy/contacts/", json={"name": w["name"], **{k: w[k] for k in ("phone", "email") if w.get(k)},
                                                                       "changelog_message": message})
    if p.op == "assign_contact":
        await client.request("POST", "tenancy/contact-assignments/", json={
            "object_type": f"circuits.{p.key[0]}", "object_id": p.key[1], "contact": rid("contact", w["contact_id"]),  # type: ignore[index]
            "role": w["role_id"], "changelog_message": message})
        return {"id": p.key[1]}  # type: ignore[index]
    if p.op == "assign_ip":
        ip_id = w["ip_id"]
        if ip_id is None:
            ip = await client.request("POST", "ipam/ip-addresses/", json={
                "address": w["address"], "assigned_object_type": "dcim.interface", "assigned_object_id": w["interface_id"],
                "status": "active", "changelog_message": message})
            ip_id = ip["id"]
        if w["primary"]:
            return await client.request("PATCH", f"dcim/devices/{p.key[1]}/", json={  # type: ignore[index]
                f"primary_ip{w['family']}": ip_id, "changelog_message": message})
        return {"id": p.key[1]}  # type: ignore[index]
    return await client.request("POST", "ipam/prefixes/", json={"prefix": w["prefix"], "status": "active", "changelog_message": message})


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
        elif k == "country":
            cf["iso_country"] = v
        elif k in ("site_id", "location_id", "rack_id", "platform_id"):
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
            if p.op in NET_OPS:
                obj = await write_net(client, p, real, message)
                if p.op not in NET_UPDATES:
                    real[p.key] = obj["id"]  # type: ignore[index]
                written.append(p.index)
                ids[p.index] = obj["id"]
                p.after = {f: (next((i for (_, ref), i in real.items() if ref == v), v) if isinstance(v, str) and v.startswith("@") else v)
                           for f, v in (p.after or {}).items()}
                log.info("apply: op %d %s %s ok", p.index, p.op, obj["id"])
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
