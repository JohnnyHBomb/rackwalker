"""The change ops of the setup companion (contract 2.5.0): what a NetBox needs before there is anything to cable.

    create_site, create_provider, create_circuit_type      the first objects of an empty NetBox
    add_device_type, add_module_type                        a model, imported from the pinned device-type library
                                                            (or, for a model it does not hold, recorded by hand and
                                                            tagged rw-unverified)
    create_device, set_device_type, install_module          kit of that model
    set_part_number                                         a device type's part number, put right from the library
    mark_unverified                                         the tag for what could not be checked against the packs

They run through the same path as every other op (changes.py: gather what they can touch, plan them without writing,
preview, apply in order, NetBox's changelog as the audit trail). This module holds their three parts: `gather` (the
reads), the `plan_*` functions (pure), and `write`. Nothing is created twice: a provider, circuit type, manufacturer,
device type, module type, role, region, registry or tag that NetBox already holds is matched and used.

`set_device_type` is the one op here that changes recorded kit, so it is the careful one. NetBox lets a device's type
be changed, but it neither removes nor adds components when that happens. The op is allowed only when nothing recorded
would be left hanging:

    safe      every component the device has (interface, console port, power port and outlet, front and rear port,
              module bay, device bay) exists under the same name and kind in the new type, and the new height fits
              where the device is racked. The components the device lacks are then added from the new type's
              templates. Nothing the device has is removed, renamed or changed: cables, addresses and modules stay.
    refused   a component of the device is not in the new type (the op names up to four, and says which carry a
              cable), or the new height does not fit in the rack, or the device already is of that type's kind.
"""
from __future__ import annotations

from typing import Any

from . import netbox as nb
from . import packs
from .country import NAMES as COUNTRY_NAMES
from .errors import ApiError

SETUP_OPS = ("create_site", "create_provider", "create_circuit_type", "add_device_type", "add_module_type", "create_device",
             "set_device_type", "install_module", "set_part_number", "mark_unverified")
SETUP_UPDATES = ("set_device_type", "set_part_number", "mark_unverified")
# A device's components, in the order they must be written (an outlet names its power port, a front port its rear
# port): the key of the library's YAML, the template list, the component list, and the word for a player.
COMPONENTS = (("console-ports", "dcim/console-port-templates", "dcim/console-ports", "console port"),
              ("console-server-ports", "dcim/console-server-port-templates", "dcim/console-server-ports", "console server port"),
              ("power-ports", "dcim/power-port-templates", "dcim/power-ports", "power port"),
              ("power-outlets", "dcim/power-outlet-templates", "dcim/power-outlets", "power outlet"),
              ("interfaces", "dcim/interface-templates", "dcim/interfaces", "interface"),
              ("rear-ports", "dcim/rear-port-templates", "dcim/rear-ports", "rear port"),
              ("front-ports", "dcim/front-port-templates", "dcim/front-ports", "front port"),
              ("module-bays", "dcim/module-bay-templates", "dcim/module-bays", "module bay"),
              ("device-bays", "dcim/device-bay-templates", "dcim/device-bays", "device bay"))
MODULE_COMPONENTS = tuple(c for c in COMPONENTS if c[0] != "device-bays")
# What a template entry of the library carries over to NetBox, per kind (name, label and description always).
TEMPLATE_KEYS = {"console-ports": ("type",), "console-server-ports": ("type",), "power-ports": ("type", "maximum_draw", "allocated_draw"),
                 "power-outlets": ("type", "feed_leg"), "interfaces": ("type", "mgmt_only", "enabled", "poe_mode", "poe_type", "rf_role"),
                 "rear-ports": ("type", "positions", "color"), "front-ports": ("type", "positions", "color"), "module-bays": ("position",), "device-bays": ()}
COUNT_WORDS = {"interfaces": "interfaces", "console-ports": "console_ports", "console-server-ports": "console_server_ports",
               "power-ports": "power_ports", "power-outlets": "power_outlets", "front-ports": "front_ports", "rear-ports": "rear_ports",
               "module-bays": "module_bays", "device-bays": "device_bays"}
ROLE_COLOURS = {"router": "9c27b0", "switch": "2196f3", "firewall": "f44336", "server": "4caf50", "patch": "9e9e9e", "pdu": "ff9800",
                "access": "00bcd4", "storage": "795548"}
COUNTRY_OF = {"GB": "United Kingdom", "NL": "Netherlands", "US": "United States", "DE": "Germany", "FR": "France", "IE": "Ireland"}
for _name, _code in COUNTRY_NAMES.items():
    COUNTRY_OF.setdefault(_code, _name.title())


def _v(x: Any) -> Any:
    return x.get("value") if isinstance(x, dict) and "value" in x else x


def _tags(row: dict[str, Any]) -> list[str]:
    return sorted(t.get("slug") for t in row.get("tags") or [] if isinstance(t, dict) and t.get("slug"))


def _type_rec(row: dict[str, Any], names: dict[str, list[str]] | None = None) -> Any:
    from .changes import Rec

    return Rec("device_type", row["id"], None, {"manufacturer": (row.get("manufacturer") or {}).get("name"), "model": row.get("model"),
                                                "part_number": row.get("part_number") or "", "u_height": float(row.get("u_height") or 0),
                                                "full_depth": bool(row.get("is_full_depth", True)), "tags": _tags(row), "_names": names})


# ---------------------------------------------------------------- gather (NetBox reads, and the library for an import)
async def gather(client: nb.NetBox, ops: list[Any], st: Any) -> None:
    from .changes import Rec, _fields, _one

    index = packs.load().index()

    async def maker(name: str) -> None:
        for row in await client.list_raw("dcim/manufacturers", {"name__ie": name}) + await client.list_raw("dcim/manufacturers", {"slug": packs.slugify(name)}):
            st.add(Rec("x:manufacturer", row["id"], None, {"name": row["name"], "slug": row["slug"]}))

    async def existing_types(kind: str, model: str) -> None:
        path = "dcim/device-types" if kind == "device" else "dcim/module-types"
        for row in await client.list_raw(path, {"model__ie": model}):
            if kind == "device":
                st.add(_type_rec(row))
            else:
                st.add(Rec("module_type", row["id"], None, {"manufacturer": (row.get("manufacturer") or {}).get("name"), "model": row["model"],
                                                            "part_number": row.get("part_number") or ""}))

    async def template_names(type_id: int) -> dict[str, list[str]]:
        return {key: [r["name"] for r in await client.list_raw(tpl, {"device_type_id": type_id})] for key, tpl, _, _ in COMPONENTS}

    for op in ops:
        if op.op not in SETUP_OPS:
            continue
        f = _fields(op)
        tid = op.target.id.root if hasattr(op.target.id, "root") else op.target.id
        if op.op == "create_site":
            for row in await client.list_raw("dcim/sites", {"name__ie": f["name"]}) + await client.list_raw("dcim/sites", {"slug": packs.slugify(f["name"])}):
                st.add(Rec("x:site", row["id"], None, {"name": row["name"], "slug": row["slug"]}))
            if f.get("country"):
                for row in await client.list_raw("dcim/regions", {"slug": f["country"].lower()}):
                    st.add(Rec("x:region", row["id"], None, {"name": row["name"], "slug": row["slug"]}))
        elif op.op == "create_provider":
            for row in await client.list_raw("circuits/providers", {"fields": "id,name,slug,asns"}):
                st.add(Rec("provider", row["id"], None, {"name": row["name"], "slug": row.get("slug"),
                                                         "asns": [a["asn"] for a in row.get("asns") or [] if isinstance(a, dict)]}))
        elif op.op == "create_circuit_type":
            for row in await client.list_raw("circuits/circuit-types", {"fields": "id,name,slug"}):
                st.add(Rec("circuit_type", row["id"], None, {"name": row["name"], "slug": row.get("slug")}))
        elif op.op in ("add_device_type", "add_module_type"):
            kind = "device" if op.op == "add_device_type" else "module"
            row = index.by_file(kind, f["library_file"]) if f.get("library_file") else None
            name, model = (row["manufacturer"], row["model"]) if row else (f.get("manufacturer"), f.get("model"))
            if not name or not model:
                continue
            await maker(name)
            await existing_types(kind, model)
            have = any(r.kind == f"{kind}_type" and packs.norm(r.v["manufacturer"] or "") == packs.norm(name)
                       and packs._words(r.v["model"] or "") == packs._words(model) for r in st.recs.values())
            if row and not have:
                try:
                    st.add(Rec("x:libdef", f"{kind}:{row['file']}", None, {"doc": await packs.definition(row), "error": None}))
                except packs.LibraryUnavailable as err:
                    st.add(Rec("x:libdef", f"{kind}:{row['file']}", None, {"doc": None, "error": str(err)}))
        elif op.op == "create_device":
            if isinstance(f["device_type_id"], int):
                row = await _one(client, "dcim/device-types", f["device_type_id"])
                if row:
                    st.add(_type_rec(row))
            for row in await client.list_raw("dcim/device-roles", {"name__ie": f["role"]}) + await client.list_raw("dcim/device-roles", {"slug": packs.slugify(f["role"])}):
                st.add(Rec("x:role", row["id"], None, {"name": row["name"], "slug": row["slug"]}))
            if f.get("serial"):
                for row in await client.list_raw("dcim/devices", {"serial": f["serial"], "fields": "id,name,serial"}):
                    st.add(Rec("x:serial", row["id"], None, {"serial": row.get("serial"), "name": row.get("name")}))
        elif op.op == "set_device_type" and isinstance(tid, int):
            have: dict[str, list[dict[str, Any]]] = {}
            for key, _, path, _ in COMPONENTS:
                have[key] = [{"name": r["name"], "cable": bool(r.get("cable")), "module": bool(r.get("installed_module"))}
                             for r in await client.list_raw(path, {"device_id": tid})]
            st.add(Rec("x:components", tid, None, have))
            if isinstance(f["device_type_id"], int):
                row = await _one(client, "dcim/device-types", f["device_type_id"])
                if row:
                    st.add(_type_rec(row, await template_names(row["id"])))
        elif op.op == "install_module":
            bay = await _one(client, "dcim/module-bays", f["module_bay_id"])
            if bay:
                st.add(Rec("x:bay", bay["id"], None, {"name": bay["name"], "device_id": (bay.get("device") or {}).get("id"),
                                                      "device": (bay.get("device") or {}).get("name"), "module": (bay.get("installed_module") or {}).get("id")}))
            if isinstance(f["module_type_id"], int):
                row = await _one(client, "dcim/module-types", f["module_type_id"])
                if row:
                    st.add(Rec("module_type", row["id"], None, {"manufacturer": (row.get("manufacturer") or {}).get("name"), "model": row["model"],
                                                                "part_number": row.get("part_number") or ""}))
        elif op.op == "set_part_number" and isinstance(tid, int):
            row = await _one(client, "dcim/device-types", tid)
            if row:
                st.add(_type_rec(row))
        elif op.op == "mark_unverified" and isinstance(tid, int):
            path = "dcim/devices" if op.target.kind == "device" else "dcim/device-types"
            row = await _one(client, path, tid)
            if row:
                st.add(Rec(f"x:tags:{op.target.kind}", tid, None, {"tags": _tags(row), "ids": [t["id"] for t in row.get("tags") or []],
                                                                   "name": row.get("name") or row.get("model")}))


# ---------------------------------------------------------------- plan (pure)
def _err(code: str, message: str, **details: Any) -> Exception:
    from .changes import OpError

    return OpError(code, message, details or None)


def plan_create_site(pl: Any, p: Any, op: Any, f: dict[str, Any]) -> None:
    name = f["name"].strip()
    slug = packs.slugify(name)
    for r in [*pl.st.of("x:site"), *pl.st.of("site")]:
        if str(r.v.get("name", "")).lower() == name.lower() or r.v.get("slug") == slug:
            raise _err("conflict", f"NetBox already has a site called {r.v.get('name')}: a site's name is unique.", field="name")
    region = next((r for r in pl.st.of("x:region") if r.v["slug"] == (f.get("country") or "").lower()), None)
    shown = {"name": name, **({"country": f["country"]} if f.get("country") else {})}
    from .changes import Rec

    rec = Rec("site", f"@{op.client_ref}" if op.client_ref else f"#{p.index}", None, {"name": name, "slug": slug})
    pl.st.add(rec)
    p.key, p.before, p.after = rec.key, None, shown
    p.write = {**shown, "slug": slug, "region_id": region.id if region else None}


def plan_create_provider(pl: Any, p: Any, op: Any, f: dict[str, Any]) -> None:
    entry = {"name": f["name"].strip(), "slug": f.get("slug") or packs.slugify(f["name"]), "asns": f.get("asns") or [], "aka": f.get("aka") or []}
    known = [{"id": r.id, **r.v} for r in pl.st.of("provider")]
    hit = packs.match_provider(entry, known)
    if hit is not None:
        # never a second provider for the same carrier: the one NetBox holds is used, as it is
        p.status, p.key, p.before, p.after, p.target_id = "no_change", ("provider", hit["id"]), None, None, hit["id"]
        return
    slug, n = entry["slug"], 2
    while any(r.v.get("slug") == slug for r in pl.st.of("provider")):
        slug, n = f"{entry['slug']}-{n}", n + 1
    shown = {"name": entry["name"], **({"asns": [a["asn"] for a in entry["asns"]]} if entry["asns"] else {}), **({"pack": f["pack"]} if f.get("pack") else {})}
    pl._new(p, op, "provider", {"name": entry["name"], "slug": slug, "asns": [a["asn"] for a in entry["asns"]]}, shown)
    p.write = {"name": entry["name"], "slug": slug, "asns": entry["asns"]}


def plan_create_circuit_type(pl: Any, p: Any, op: Any, f: dict[str, Any]) -> None:
    name = f["name"].strip()
    slug = packs.slugify(name)
    hit = next((r for r in pl.st.of("circuit_type") if str(r.v.get("name", "")).lower() == name.lower() or r.v.get("slug") == slug), None)
    if hit is not None:
        p.status, p.key, p.before, p.after, p.target_id = "no_change", hit.key, None, None, hit.id
        return
    pl._new(p, op, "circuit_type", {"name": name, "slug": slug}, {"name": name})
    p.write = {"name": name, "slug": slug}


def _same_type(r: Any, maker: str, model: str) -> bool:
    return packs.norm(r.v.get("manufacturer") or "") == packs.norm(maker) and packs._words(r.v.get("model") or "") == packs._words(model)


def _plan_type(pl: Any, p: Any, op: Any, f: dict[str, Any], kind: str) -> None:
    index = packs.load().index()
    rec_kind = f"{kind}_type"
    file = f.get("library_file")
    if file:
        if f.get("manufacturer") or f.get("model"):
            raise _err("validation_failed", "A model is either imported from the library (library_file) or recorded by hand (manufacturer and model), not both.")
        row = index.by_file(kind, file)
        if row is None:
            raise _err("not_found", f"The game's index of the device-type library has no {kind} type {file}.", field="library_file")
        maker, model = row["manufacturer"], row["model"]
    else:
        maker, model = (f.get("manufacturer") or "").strip(), (f.get("model") or "").strip()
        if not maker or not model:
            raise _err("validation_failed", "A model recorded by hand needs its manufacturer and its model.", field="model")
        check = index.part_check(maker, model, f.get("part_number"), kind)
        if check["status"] == "mismatch":
            raise _err("validation_failed", f"The device-type library knows {maker} {model} with the part number {check['expected']}; "
                       f"{f.get('part_number')} does not match it. Read the label again, or pick the model from the search.",
                       field="part_number", expected=check["expected"])
        if check["status"] != "unknown":
            raise _err("validation_failed", f"The device-type library holds {maker} {model}: pick it from the search, so its {'ports' if kind == 'device' else 'details'} come with it.",
                       field="model")
    hit = next((r for r in pl.st.of(rec_kind) if _same_type(r, maker, model)), None)
    if hit is not None:
        p.status, p.key, p.before, p.after, p.target_id = "no_change", hit.key, None, None, hit.id
        return
    made = next((r for r in pl.st.of("x:manufacturer") if packs.norm(r.v["name"]) == packs.norm(maker) or r.v["slug"] == packs.slugify(maker)), None)
    if file:
        got = pl.st.recs.get(("x:libdef", f"{kind}:{file}"))
        if got is None or got.v["doc"] is None:
            raise _err("library_unavailable", got.v["error"] if got is not None else
                       "The definition of that model was not read from the device-type library.", library_file=file)
        doc = got.v["doc"]
        comps = COMPONENTS if kind == "device" else MODULE_COMPONENTS
        names = {key: [str(x["name"]) for x in doc.get(key) or []] for key, *_ in comps}
        pack = packs.load().device_pack()
        shown = {"manufacturer": maker, "model": model, "part_number": str(doc.get("part_number") or ""),
                 **({"u_height": float(doc.get("u_height", 1))} if kind == "device" else {}),
                 **{COUNT_WORDS[key]: len(v) for key, v in names.items() if v},
                 **({"port_mappings": sum(len(v) for v in port_mappings(doc).values())} if port_mappings(doc) else {}),
                 "source": f"device-type library at {pack['library']['commit'][:10]}" if pack else "device-type library"}
        values = {"manufacturer": maker, "model": model, "part_number": shown["part_number"], "u_height": float(doc.get("u_height", 1)),
                  "full_depth": bool(doc.get("is_full_depth", True)), "tags": [], "_names": names}
        pl._new(p, op, rec_kind, values, shown)
        p.write = {"doc": doc, "manufacturer": maker, "manufacturer_id": made.id if made else None, "verified": True}
        return
    height = float(f.get("u_height", 1))
    shown = {"manufacturer": maker, "model": model, "part_number": (f.get("part_number") or "").strip(),
             **({"u_height": height} if kind == "device" else {}), "unverified": True}
    pl._new(p, op, rec_kind, {"manufacturer": maker, "model": model, "part_number": shown["part_number"], "u_height": height, "full_depth": True,
                              "tags": [packs.UNVERIFIED_TAG["slug"]], "_names": {key: [] for key, *_ in COMPONENTS}}, shown)
    p.write = {"doc": {"manufacturer": maker, "model": model, "slug": packs.slugify(f"{maker} {model}"), "part_number": shown["part_number"],
                       "u_height": height},
               "manufacturer": maker, "manufacturer_id": made.id if made else None, "verified": False}


def plan_add_device_type(pl: Any, p: Any, op: Any, f: dict[str, Any]) -> None:
    _plan_type(pl, p, op, f, "device")


def plan_add_module_type(pl: Any, p: Any, op: Any, f: dict[str, Any]) -> None:
    _plan_type(pl, p, op, f, "module")


def _present_part(dtype: Any) -> None:
    """A device type NetBox holds whose part number contradicts the library's for that maker and model is not built on."""
    check = packs.load().index().part_check(dtype.v.get("manufacturer"), dtype.v.get("model"), dtype.v.get("part_number"))
    if check["status"] == "mismatch":
        raise _err("validation_failed", f"NetBox holds {dtype.v['manufacturer']} {dtype.v['model']} with the part number {dtype.v['part_number']}; "
                   f"the device-type library says {check['expected']}. Correct the device type in NetBox (Devices > Device Types) first.",
                   field="device_type_id", expected=check["expected"])


def _fits(pl: Any, rack: Any, me: Any, pos: float, height: float, face: str | None, full_depth: bool) -> None:
    from .changes import _u

    if height == 0:
        return
    lo, hi = rack.v["starting_unit"], rack.v["starting_unit"] + rack.v["u_height"]
    if pos < lo or pos + height > hi:
        raise _err("validation_failed", f"{_u(pos)}-{_u(pos + height - 1)} is outside rack {rack.v['name']} ({_u(lo)}-{_u(hi - 1)}).", field="position")
    for o in sorted(pl.st.of("device"), key=lambda r: (isinstance(r.id, str), r.id)):
        if o is me or o.v.get("rack_id") != rack.id or o.v.get("position") is None or not o.v.get("_u_height"):
            continue
        op_, oh = float(o.v["position"]), float(o.v["_u_height"])
        if pos < op_ + oh and op_ < pos + height and (full_depth or o.v.get("_full_depth", True) or o.v.get("face") == face):
            raise _err("conflict", f"{_u(max(pos, op_))} of rack {rack.v['name']} is taken by {o.v.get('name') or 'another device'}.",
                       field="position", device_id=o.id)


def plan_create_device(pl: Any, p: Any, op: Any, f: dict[str, Any]) -> None:
    from .changes import Rec

    site = pl.resolve("site", f["site_id"], "site_id")
    dtype = pl.resolve("device_type", f["device_type_id"], "device_type_id")
    _present_part(dtype)
    name = f["name"].strip()
    for o in pl.st.of("device"):
        if o.v.get("site_id") == site.id and str(o.v.get("name") or "").lower() == name.lower():
            raise _err("conflict", f"{site.v.get('name', 'That site')} already has a device called {name}.", field="name")
    serial = (f.get("serial") or "").strip()
    if serial:
        twin = next((o for o in [*pl.st.of("x:serial"), *pl.st.of("device")] if str(o.v.get("serial") or "").lower() == serial.lower()), None)
        if twin is not None:
            raise _err("conflict", f"{twin.v.get('name') or 'Another device'} already has the serial {serial}: a serial number belongs to one device.",
                       field="serial")
    rack = None
    if f.get("rack_id") is not None:
        rack = pl.resolve("rack", f["rack_id"], "rack_id")
        if rack.v["site_id"] != site.id:
            raise _err("validation_failed", f"Rack {rack.v['name']} is not at that site.", field="rack_id")
        if f.get("position") is not None:
            _fits(pl, rack, None, float(f["position"]), float(dtype.v["u_height"]), f.get("face") or "front", bool(dtype.v.get("full_depth", True)))
    elif f.get("position") is not None:
        raise _err("validation_failed", "A unit needs a rack.", field="position")
    role = next((r for r in pl.st.of("x:role") if r.v["name"].lower() == f["role"].strip().lower() or r.v["slug"] == packs.slugify(f["role"])), None)
    shown = {k: f[k] for k in ("name", "site_id", "role", "device_type_id", "rack_id", "position", "face", "serial", "unverified") if f.get(k) is not None}
    if rack is not None and f.get("position") is not None:
        shown.setdefault("face", "front")
    rec = Rec("device", f"@{op.client_ref}" if op.client_ref else f"#{p.index}", None, {
        "site_id": site.id, "location_id": rack.v.get("location_id") if rack else None, "rack_id": rack.id if rack else None,
        "position": f.get("position"), "face": shown.get("face"), "name": name, "status": "active", "serial": serial, "asset_tag": None,
        "_u_height": dtype.v["u_height"], "_full_depth": bool(dtype.v.get("full_depth", True))})
    pl.st.add(rec)
    p.key, p.before, p.after = rec.key, None, shown
    p.write = {**shown, "name": name, "device_type_id": dtype.id, "role_id": role.id if role else None,
               "location_id": rack.v.get("location_id") if rack else None}


def plan_set_device_type(pl: Any, p: Any, op: Any, f: dict[str, Any]) -> None:
    dev = pl.resolve("device", p.target_id)
    pl.lock(dev, op.if_unmodified_since)
    dtype = pl.resolve("device_type", f["device_type_id"], "device_type_id")
    _present_part(dtype)
    if dev.v.get("_type_id") == dtype.id:
        p.status, p.key = "no_change", dev.key
        return
    have = pl.st.recs.get(("x:components", dev.id))
    names = dtype.v.get("_names")
    if have is None or names is None:
        raise _err("not_found", "The device's ports or the model's templates could not be read, so the change was not judged safe.")
    who = dev.v.get("name") or "The device"
    model = f"{dtype.v['manufacturer']} {dtype.v['model']}"
    orphans = [(word, c) for key, _, _, word in COMPONENTS for c in have.v.get(key, []) if c["name"] not in set(names.get(key, []))]
    if orphans:
        some = ", ".join(f"{word} {c['name']}" + (" (cabled)" if c["cable"] else " (a module is fitted)" if c["module"] else "") for word, c in orphans[:4])
        raise _err("conflict", f"{who} has {len(orphans)} component{'s' if len(orphans) != 1 else ''} that {model} does not have: {some}"
                   f"{' and more' if len(orphans) > 4 else ''}. Changing the model would leave them recorded on a box that cannot have them, so nothing "
                   "is changed. If the device really is that model, remove or rename them in NetBox first.",
                   field="device_type_id", orphans=len(orphans))
    if dev.v.get("rack_id") is not None and dev.v.get("position") is not None:
        rack = pl.st.recs.get(("rack", dev.v["rack_id"]))
        if rack is not None:
            _fits(pl, rack, dev, float(dev.v["position"]), float(dtype.v["u_height"]), dev.v.get("face"), bool(dtype.v.get("full_depth", True)))
    missing = {key: [n for n in names.get(key, []) if n not in {c["name"] for c in have.v.get(key, [])}] for key, *_ in COMPONENTS}
    added = {COUNT_WORDS[key] + "_added": len(v) for key, v in missing.items() if v}
    p.key = dev.key
    p.before = {"device_type": dev.v.get("_model"), **{k: 0 for k in added}}
    p.after = {"device_type": model, **added}
    dev.v.update(_type_id=dtype.id, _model=dtype.v["model"], _u_height=dtype.v["u_height"])
    p.write = {"device_type_id": dtype.id, "missing": missing}


def plan_install_module(pl: Any, p: Any, op: Any, f: dict[str, Any]) -> None:
    bay = pl.st.recs.get(("x:bay", f["module_bay_id"]))
    if bay is None:
        raise _err("not_found", f"Module bay {f['module_bay_id']} does not exist.", field="module_bay_id")
    if bay.v["device_id"] != f["device_id"]:
        raise _err("validation_failed", f"Module bay {bay.v['name']} is not a bay of that device.", field="module_bay_id")
    if bay.v["module"] is not None:
        raise _err("conflict", f"Bay {bay.v['name']} of {bay.v['device']} already holds a module.", field="module_bay_id")
    mtype = pl.resolve("module_type", f["module_type_id"], "module_type_id")
    shown = {k: f[k] for k in ("device_id", "module_bay_id", "module_type_id", "serial") if f.get(k) is not None}
    pl._new(p, op, "module", {"device_id": f["device_id"], "bay_id": bay.id}, shown)
    bay.v["module"] = p.key[1]
    p.write = {**shown, "module_type_id": mtype.id}


def plan_set_part_number(pl: Any, p: Any, op: Any, f: dict[str, Any]) -> None:
    """A device type's part number is only ever set to what the library lists for that maker and model, in the
    library's own spelling: a number nothing can confirm is not written through the game."""
    dtype = pl.st.recs.get(("device_type", p.target_id))
    if dtype is None:
        raise _err("not_found", f"Device type {p.target_id} does not exist.", kind="device_type", id=p.target_id)
    name = f"{dtype.v['manufacturer']} {dtype.v['model']}"
    check = packs.load().index().part_check(dtype.v["manufacturer"], dtype.v["model"], f["part_number"])
    if check["status"] == "unknown" or not check["expected"]:
        raise _err("validation_failed", f"The device-type library lists no part number for {name}, so there is nothing to check one against.",
                   field="part_number")
    if check["status"] != "verified":
        raise _err("validation_failed", f"The device-type library lists {name} with the part number {check['expected']}; {f['part_number']} does not match it.",
                   field="part_number", expected=check["expected"])
    p.key = dtype.key
    if dtype.v["part_number"] == check["expected"]:
        p.status = "no_change"
        return
    p.before, p.after = {"part_number": dtype.v["part_number"]}, {"part_number": check["expected"]}
    dtype.v["part_number"] = check["expected"]
    p.write = {"part_number": check["expected"]}


def plan_mark_unverified(pl: Any, p: Any, op: Any, f: dict[str, Any]) -> None:
    rec = pl.st.recs.get((f"x:tags:{p.kind}", p.target_id))
    if rec is None:
        raise _err("not_found", f"{p.kind.replace('_', ' ').capitalize()} {p.target_id} does not exist.", kind=p.kind, id=p.target_id)
    p.key = (p.kind, p.target_id)
    if packs.UNVERIFIED_TAG["slug"] in rec.v["tags"]:
        p.status = "no_change"
        return
    p.before, p.after = {"unverified": False}, {"unverified": True}
    p.write = {"tag_ids": rec.v["ids"]}


def install(planner: type) -> None:
    for name in SETUP_OPS:
        fn = globals()[f"plan_{name}"]
        setattr(planner, name, lambda self, p, op, f, _fn=fn: _fn(self, p, op, f))


# ---------------------------------------------------------------- write
async def _find_or_make(client: nb.NetBox, path: str, query: dict[str, Any], body: dict[str, Any], message: str) -> int:
    rows = await client.list_raw(path, query)
    if rows:
        return rows[0]["id"]
    return (await client.request("POST", path + "/", json={**body, "changelog_message": message}))["id"]


async def _tag(client: nb.NetBox, message: str) -> int:
    t = packs.UNVERIFIED_TAG
    return await _find_or_make(client, "extras/tags", {"slug": t["slug"]}, dict(t), message)


def port_mappings(doc: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    """Which rear port (and which position of it) each front port of a definition leads to, by the front port's name.
    The library keeps this in a `port-mappings` section; older files name one `rear_port` on the front port itself.
    Without it a patch panel is two rows of ports that lead nowhere, and a cable trace stops at the panel."""
    out: dict[str, list[dict[str, Any]]] = {}
    for m in doc.get("port-mappings") or []:
        if isinstance(m, dict) and m.get("front_port") is not None and m.get("rear_port") is not None:
            out.setdefault(str(m["front_port"]), []).append({"position": int(m.get("front_port_position") or 1), "rear": str(m["rear_port"]),
                                                             "rear_position": int(m.get("rear_port_position") or 1)})
    for e in doc.get("front-ports") or []:
        if e.get("rear_port") is not None and str(e["name"]) not in out:
            out[str(e["name"])] = [{"position": 1, "rear": str(e["rear_port"]), "rear_position": int(e.get("rear_port_position") or 1)}]
    return out


async def _registry(client: nb.NetBox, key: str, message: str) -> int:
    """The registry of an AS number as NetBox holds it, under whichever of its names it was given there ("RIPE" or
    "RIPE NCC", slug `ripe` or `ripe-ncc`); made only when NetBox has none of them."""
    name, slug = packs.RIR_NAMES[key]
    names, slugs = {name.lower(), key}, {slug, key}
    for row in await client.list_raw("ipam/rirs", {"fields": "id,name,slug"}):
        if str(row.get("name", "")).strip().lower() in names or row.get("slug") in slugs:
            return row["id"]
    return (await client.request("POST", "ipam/rirs/", json={"name": name, "slug": slug, "changelog_message": message}))["id"]


def _template(key: str, entry: dict[str, Any], owner: dict[str, int], by_name: dict[str, dict[str, int]],
              mappings: dict[str, list[dict[str, Any]]]) -> dict[str, Any]:
    body = {**owner, "name": str(entry["name"]), **{k: entry[k] for k in ("label", "description", *TEMPLATE_KEYS[key]) if entry.get(k) is not None}}
    if key == "power-outlets" and entry.get("power_port") is not None:
        body["power_port"] = by_name["power-ports"].get(str(entry["power_port"]))
    if key == "front-ports" and mappings.get(str(entry["name"])):
        body["rear_ports"] = [{"position": m["position"], "rear_port": by_name["rear-ports"].get(m["rear"]), "rear_port_position": m["rear_position"]}
                              for m in mappings[str(entry["name"])]]
    return body


async def _write_type(client: nb.NetBox, w: dict[str, Any], kind: str, message: str) -> dict[str, Any]:
    doc = w["doc"]
    maker_id = w["manufacturer_id"] or await _find_or_make(client, "dcim/manufacturers", {"slug": packs.slugify(w["manufacturer"])},
                                                           {"name": w["manufacturer"], "slug": packs.slugify(w["manufacturer"])}, message)
    body: dict[str, Any] = {"manufacturer": maker_id, "model": str(doc["model"]), "part_number": str(doc.get("part_number") or "")}
    for k in ("weight", "weight_unit", "airflow", "description"):
        if doc.get(k) is not None:
            body[k] = doc[k]
    if kind == "device":
        body.update(slug=str(doc.get("slug") or packs.slugify(f"{w['manufacturer']} {doc['model']}")), u_height=doc.get("u_height", 1))
        for k in ("is_full_depth", "subdevice_role"):
            if doc.get(k) is not None:
                body[k] = doc[k]
    if not w["verified"]:
        body["tags"] = [await _tag(client, message)]
    path = "dcim/device-types" if kind == "device" else "dcim/module-types"
    obj = await client.request("POST", path + "/", json={**body, "changelog_message": message})
    owner = {"device_type" if kind == "device" else "module_type": obj["id"]}
    by_name: dict[str, dict[str, int]] = {}
    mappings = port_mappings(doc)
    try:
        for key, tpl, _, _ in (COMPONENTS if kind == "device" else MODULE_COMPONENTS):
            entries = doc.get(key) or []
            if not entries:
                continue
            made = await client.request("POST", tpl + "/", json=[{**_template(key, e, owner, by_name, mappings), "changelog_message": message} for e in entries])
            by_name[key] = {m["name"]: m["id"] for m in made}
    except ApiError as err:
        # a model with half its ports is worse than no model: take it out again, so a later import starts clean
        try:
            await client.request("DELETE", f"{path}/{obj['id']}/", json={"changelog_message": message + " (undone: its templates were refused)"})
            left = ""
        except ApiError:
            left = f" The {kind} type {doc['model']} was created without all of its templates and could not be removed again: delete it in NetBox."
        raise ApiError(err.code, f"NetBox refused a template of {doc['model']} ({err.message.rstrip('.')}).{left}", err.details) from None
    return obj


async def _add_components(client: nb.NetBox, device_id: int, type_id: int, missing: dict[str, list[str]], message: str) -> None:
    """The components a device lacks against its (new) type, made from the type's own templates as NetBox holds them."""
    ids: dict[str, dict[str, int]] = {}

    async def device_ids(key: str, path: str) -> dict[str, int]:
        if key not in ids:
            ids[key] = {r["name"]: r["id"] for r in await client.list_raw(path, {"device_id": device_id})}
        return ids[key]
    paths = {key: path for key, _, path, _ in COMPONENTS}
    for key, tpl, path, _ in COMPONENTS:
        want = set(missing.get(key) or [])
        if not want:
            continue
        rows = [r for r in await client.list_raw(tpl, {"device_type_id": type_id}) if r["name"] in want]
        tpl_rear = {r["id"]: r["name"] for r in await client.list_raw("dcim/rear-port-templates", {"device_type_id": type_id})} if key == "front-ports" else {}
        bodies = []
        for r in rows:
            body: dict[str, Any] = {"device": device_id, "name": r["name"], **{k: r[k] for k in ("label", "description") if r.get(k)}}
            for k in TEMPLATE_KEYS[key]:
                v = _v(r.get(k))
                if v is not None and v != "":
                    body[k] = v
            if key == "power-outlets" and r.get("power_port"):
                body["power_port"] = (await device_ids("power-ports", paths["power-ports"])).get(r["power_port"]["name"])
            if key == "front-ports":
                rear = await device_ids("rear-ports", paths["rear-ports"])
                body["rear_ports"] = [{"position": m.get("position") or 1, "rear_port": rear.get(tpl_rear.get(m.get("rear_port"), "")),
                                       "rear_port_position": m.get("rear_port_position") or 1} for m in r.get("rear_ports") or []]
            bodies.append({**body, "changelog_message": message})
        if bodies:
            await client.request("POST", path + "/", json=bodies)
            ids.pop(key, None)


async def write(client: nb.NetBox, p: Any, real: dict[Any, int], message: str) -> dict[str, Any]:
    w = p.write

    def rid(kind: str, v: Any) -> int:
        return real[(kind, v)] if isinstance(v, str) else v
    note = {"changelog_message": message}
    if p.op == "create_site":
        region = w.get("region_id")
        if region is None and w.get("country"):
            code = w["country"]
            region = await _find_or_make(client, "dcim/regions", {"slug": code.lower()}, {"name": COUNTRY_OF.get(code, code), "slug": code.lower()}, message)
        return await client.request("POST", "dcim/sites/", json={"name": w["name"], "slug": w["slug"], "status": "active",
                                                                 **({"region": region} if region else {}), **note})
    if p.op == "create_provider":
        asn_ids = []
        for a in w["asns"]:
            rows = await client.list_raw("ipam/asns", {"asn": a["asn"]})
            if rows:
                asn_ids.append(rows[0]["id"])
                continue
            rir = await _registry(client, a["rir"], message)
            asn_ids.append((await client.request("POST", "ipam/asns/", json={"asn": a["asn"], "rir": rir, **note}))["id"])
        return await client.request("POST", "circuits/providers/", json={"name": w["name"], "slug": w["slug"], "asns": asn_ids, **note})
    if p.op == "create_circuit_type":
        return await client.request("POST", "circuits/circuit-types/", json={"name": w["name"], "slug": w["slug"], **note})
    if p.op in ("add_device_type", "add_module_type"):
        return await _write_type(client, w, "device" if p.op == "add_device_type" else "module", message)
    if p.op == "create_device":
        role = w["role_id"]
        if role is None:
            slug = packs.slugify(w["role"])
            colour = next((c for word, c in ROLE_COLOURS.items() if word in slug), "607d8b")
            role = await _find_or_make(client, "dcim/device-roles", {"slug": slug}, {"name": w["role"].strip(), "slug": slug, "color": colour}, message)
        body = {"name": w["name"], "site": w["site_id"], "role": role, "device_type": rid("device_type", w["device_type_id"]), "status": "active"}
        if w.get("rack_id") is not None:
            body["rack"] = w["rack_id"]
            if w.get("location_id") is not None:
                body["location"] = w["location_id"]
            if w.get("position") is not None:
                body.update(position=w["position"], face=w.get("face") or "front")
        if w.get("serial"):
            body["serial"] = w["serial"]
        if w.get("unverified"):
            body["tags"] = [await _tag(client, message)]
        return await client.request("POST", "dcim/devices/", json={**body, **note})
    if p.op == "set_device_type":
        type_id = rid("device_type", w["device_type_id"])
        await client.request("PATCH", f"dcim/devices/{p.key[1]}/", json={"device_type": type_id, **note})
        await _add_components(client, p.key[1], type_id, w["missing"], message)
        return {"id": p.key[1]}
    if p.op == "install_module":
        return await client.request("POST", "dcim/modules/", json={
            "device": w["device_id"], "module_bay": w["module_bay_id"], "module_type": rid("module_type", w["module_type_id"]), "status": "active",
            **({"serial": w["serial"]} if w.get("serial") else {}), **note})
    if p.op == "set_part_number":
        await client.request("PATCH", f"dcim/device-types/{p.key[1]}/", json={"part_number": w["part_number"], **note})
        return {"id": p.key[1]}
    if p.op == "mark_unverified":
        tag = await _tag(client, message)
        path = "dcim/devices" if p.key[0] == "device" else "dcim/device-types"
        await client.request("PATCH", f"{path}/{p.key[1]}/", json={"tags": sorted({*w["tag_ids"], tag}), **note})
        return {"id": p.key[1]}
    raise ApiError("internal", f"No writer for op {p.op}.", None)


# ---------------------------------------------------------------- the same ops against an estate in memory (dry runs, fixtures)
def simulate(e: Any, op: dict[str, Any], f: dict[str, Any], res: dict[str, Any], refs: dict[str, tuple[str, int]], resolve: Any) -> bool:
    """A setup op against an estate in memory, for a dry run's "what NetBox would then show". True when the op is one
    of this module's. An import is taken from the index (its counts), since nothing is fetched for a dry run."""
    from .estate import SimError

    name, target = op["op"], op["target"]
    if name not in SETUP_OPS:
        return False
    index = packs.load().index()

    def ref(kind: str, oid: int) -> None:
        res["target"]["id"] = oid
        if op.get("client_ref"):
            refs[op["client_ref"]] = (kind, oid)
    if name == "create_site":
        if any(s["name"].lower() == f["name"].strip().lower() for s in e.sites.values()):
            raise SimError(f"NetBox already has a site called {f['name']}.")
        sid = e.next_id("sites")
        e.sites[sid] = {"id": sid, "name": f["name"].strip(), "slug": packs.slugify(f["name"]), "status": "active", "latitude": None, "longitude": None,
                        "facility": None, "country": f.get("country"), "last_updated": None}
        res["after"] = dict(f)
        ref("site", sid)
    elif name == "create_provider":
        entry = {"name": f["name"], "slug": f.get("slug") or packs.slugify(f["name"]), "asns": f.get("asns") or [], "aka": f.get("aka") or []}
        hit = packs.match_provider(entry, list(e.providers.values()))
        if hit is not None:
            res["status"] = "no_change"
            ref("provider", hit["id"])
        else:
            pid = e.next_id("providers")
            e.providers[pid] = {"id": pid, "name": f["name"], "slug": entry["slug"], "asns": [a["asn"] for a in entry["asns"]]}
            res["after"] = {"name": f["name"], **({"asns": [a["asn"] for a in entry["asns"]]} if entry["asns"] else {})}
            ref("provider", pid)
    elif name == "create_circuit_type":
        hit = next((t for t in e.circuit_types.values() if t["name"].lower() == f["name"].strip().lower()), None)
        if hit is not None:
            res["status"] = "no_change"
            ref("circuit_type", hit["id"])
        else:
            tid = e.next_id("circuit_types")
            e.circuit_types[tid] = {"id": tid, "name": f["name"].strip()}
            res["after"] = dict(f)
            ref("circuit_type", tid)
    elif name in ("add_device_type", "add_module_type"):
        kind = "device" if name == "add_device_type" else "module"
        row = index.by_file(kind, f["library_file"]) if f.get("library_file") else None
        if f.get("library_file") and row is None:
            raise SimError(f"The index of the device-type library has no {kind} type {f['library_file']}.")
        maker, model = (row["manufacturer"], row["model"]) if row else (f.get("manufacturer", ""), f.get("model", ""))
        if row is None:
            check = index.part_check(maker, model, f.get("part_number"), kind)
            if check["status"] == "mismatch":
                raise SimError(f"The device-type library knows {maker} {model} with the part number {check['expected']}; {f.get('part_number')} does not match it.")
        if kind == "module":
            res["after"] = {"manufacturer": maker, "model": model}
            ref("module_type", 1 + (row["n"] if row else 0))    # module types are not kept in an estate
            return True
        hit = next((t for t in e.dtypes.values() if packs.norm(t["manufacturer"] or "") == packs.norm(maker) and packs._words(t["model"]) == packs._words(model)), None)
        if hit is not None:
            res["status"] = "no_change"
            ref("device_type", hit["id"])
            return True
        tid = e.next_id("dtypes")
        e.dtypes[tid] = {"id": tid, "manufacturer": maker, "model": model, "slug": (row or {}).get("slug") or packs.slugify(f"{maker} {model}"),
                         "part_number": (row["part_number"] if row else f.get("part_number") or ""), "u_height": float(row["u_height"] if row else f.get("u_height", 1)),
                         "tags": [] if row else [packs.UNVERIFIED_TAG["slug"]], "_interfaces": row["interfaces"] if row else 0}
        res["after"] = ({"manufacturer": maker, "model": model, "part_number": row["part_number"], "u_height": row["u_height"], "interfaces": row["interfaces"]}
                        if row else {"manufacturer": maker, "model": model, "unverified": True})
        ref("device_type", tid)
    elif name == "create_device":
        tid = resolve(f["device_type_id"], "device_type")
        dt = e.dtypes.get(tid)
        if dt is None or f["site_id"] not in e.sites:
            raise SimError("That site or device type does not exist.")
        if any(d["site_id"] == f["site_id"] and (d["name"] or "").lower() == f["name"].strip().lower() for d in e.devices.values()):
            raise SimError(f"That site already has a device called {f['name']}.")
        if f.get("serial") and any((d["serial"] or "").lower() == f["serial"].lower() for d in e.devices.values()):
            raise SimError(f"Another device already has the serial {f['serial']}.")
        did = e.next_id("devices")
        rack = e.racks.get(f.get("rack_id") or 0)
        slug = packs.slugify(f["role"])
        e.devices[did] = {"id": did, "name": f["name"].strip(), "site_id": f["site_id"], "location_id": rack["location_id"] if rack else None,
                          "rack_id": rack["id"] if rack else None, "position": f.get("position"), "face": f.get("face") or ("front" if f.get("position") else None),
                          "role": slug, "role_name": f["role"], "status": "active", "serial": f.get("serial") or "", "asset_tag": None, "platform": None,
                          "primary_ip": None, "u_height": dt["u_height"], "model": dt["model"], "manufacturer": dt["manufacturer"], "vc_id": None,
                          "type_id": tid, "mgmt_mode": None, "mgmt_service": None, "mgmt_default": None, "mgmt_default_service": None,
                          "tags": [packs.UNVERIFIED_TAG["slug"]] if f.get("unverified") else [], "last_updated": None}
        for n in range(int(dt.get("_interfaces") or 0)):
            pid = e.next_id("ports")
            e.ports[("interface", pid)] = {"kind": "interface", "id": pid, "device_id": did, "name": f"port {n + 1}", "type": "1000base-t", "enabled": True,
                                           "mgmt_only": False, "cable_id": None, "description": "", "mark_connected": False, "rear_ids": [], "rear": [],
                                           "positions": 1}
        res["after"] = {k: v for k, v in f.items() if v is not None}
        ref("device", did)
    elif name == "set_device_type":
        tid = resolve(f["device_type_id"], "device_type")
        d, dt = e.devices.get(target["id"]), e.dtypes.get(tid)
        if d is None or dt is None:
            raise SimError("That device or device type does not exist.")
        if e.device_ports(d["id"], ("interface", "front_port", "rear_port", "power_port", "power_outlet")):
            raise SimError(f"{d['name'] or 'The device'} has ports on record; whether they fit the new model is checked against NetBox when the change is really made.")
        res["before"], res["after"] = {"device_type": d["model"]}, {"device_type": f"{dt['manufacturer']} {dt['model']}"}
        d.update(type_id=tid, model=dt["model"], manufacturer=dt["manufacturer"], u_height=dt["u_height"])
    elif name == "install_module":
        bay = e.module_bays.get(f["module_bay_id"])
        if bay is None or bay["device_id"] != f["device_id"]:
            raise SimError("That module bay does not exist on that device.")
        if bay["module_id"] is not None:
            raise SimError(f"Bay {bay['name']} already holds a module.")
        resolve(f["module_type_id"], "module_type")
        bay["module_id"] = 1 + max((b["module_id"] or 0 for b in e.module_bays.values()), default=0)
        res["after"] = {k: v for k, v in f.items()}
        res["target"]["id"] = bay["module_id"]
    elif name == "set_part_number":
        t = e.dtypes.get(target["id"])
        if t is None:
            raise SimError("That device type does not exist.")
        check = index.part_check(t["manufacturer"], t["model"], f["part_number"])
        if check["status"] != "verified":
            raise SimError(f"The device-type library does not list {f['part_number']} for {t['manufacturer']} {t['model']}.")
        if t["part_number"] == check["expected"]:
            res["status"] = "no_change"
        else:
            res["before"], res["after"] = {"part_number": t["part_number"]}, {"part_number": check["expected"]}
            t["part_number"] = check["expected"]
    elif name == "mark_unverified":
        row = (e.devices if target["kind"] == "device" else e.dtypes).get(target["id"])
        if row is None:
            raise SimError("That object does not exist.")
        if packs.UNVERIFIED_TAG["slug"] in row.get("tags", []):
            res["status"] = "no_change"
        else:
            row["tags"] = sorted({*row.get("tags", []), packs.UNVERIFIED_TAG["slug"]})
            res["before"], res["after"] = {"unverified": False}, {"unverified": True}
    return True
