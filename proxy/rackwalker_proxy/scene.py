"""Build the contract bodies (contracts/scene.schema.json) from typed NetBox reads.

Every builder returns a generated contract model (contracts_gen), so a body that drifts from the schema fails here,
inside the proxy, rather than in the browser. List order: entities by id (NetBox primary key, stable), except
DeviceDetail.interfaces (PortKind order, then NetBox's list order, as the contract fixes) and DeviceDetail.cables
(first appearance in that port order).
"""
from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any

from . import contracts_gen as c
from . import netbox as nb
from .errors import ApiError
from .schema import CONTRACT_VERSION, enum

THEMES = set(enum("ThemeId"))
PASS_THROUGH = {"front_port", "rear_port", "circuit_termination"}


def now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def meta(version: str) -> c.Meta:
    return c.Meta(contract_version=CONTRACT_VERSION, generated_at=now(), netbox_version=version, source="netbox")


def _num(v: Any) -> float | None:
    return None if v is None or v == "" else float(v)


class DeviceSite(nb.NbModel):
    id: int
    site: nb.Ref


class Directory:
    """Lookups that turn nested NetBox refs into contract objects: roles, device types, device -> site and
    circuit terminations. Filled lazily per request: `load_types` fetches the roles and device types of the given
    devices only, `ensure` what a set of terminations needs (both skip ids already known; lookups are chunked)."""

    def __init__(self) -> None:
        self.roles: dict[int, nb.Role] = {}
        self.dtypes: dict[int, nb.DeviceType] = {}
        self.device_site: dict[int, int] = {}
        self.cterms: dict[int, nb.CircuitTermination] = {}

    def add_devices(self, devices: list[nb.Device]) -> None:
        self.device_site.update({d.id: d.site.id for d in devices})

    async def load_types(self, client: nb.NetBox, devices: list[nb.Device]) -> None:
        roles, dtypes = await asyncio.gather(
            client.roles({d.role.id for d in devices} - self.roles.keys()),
            client.device_types({d.device_type.id for d in devices} - self.dtypes.keys()))
        self.roles.update(roles)
        self.dtypes.update(dtypes)

    async def ensure(self, client: nb.NetBox, refs: list[nb.PortRef]) -> None:
        dev_ids = {r.device.id for r in refs if r.device and r.device.id not in self.device_site}
        ct_ids = {r.id for r in refs if r.kind == "circuit_termination" and r.id not in self.cterms}
        sites, cterms = await asyncio.gather(
            client.by_ids(DeviceSite, "dcim/devices", dev_ids, {"fields": "id,site"}),
            client.by_ids(nb.CircuitTermination, "circuits/circuit-terminations", ct_ids))
        self.device_site.update({i: d.site.id for i, d in sites.items()})
        self.cterms.update(cterms)

    # ---- mappers ----
    def device(self, d: nb.Device) -> c.Device:
        role, dt = self.roles.get(d.role.id), self.dtypes.get(d.device_type.id)
        if role is None or dt is None:
            raise ApiError("netbox_error", f"Device {d.id} refers to a role or device type NetBox did not list.",
                           {"device_id": d.id, "role_id": d.role.id, "device_type_id": d.device_type.id})
        self.device_site[d.id] = d.site.id
        return c.Device(
            id=d.id, name=d.name, role=c.Role(id=role.id, slug=role.slug, name=role.name, color=role.color.lower()),
            device_type=c.DeviceType(id=dt.id, manufacturer=dt.manufacturer.name or "", model=dt.model, slug=dt.slug,
                                     u_height=dt.u_height, is_full_depth=dt.is_full_depth),
            site_id=d.site.id, location_id=d.location.id if d.location else None, rack_id=d.rack.id if d.rack else None,
            position=d.position, face=d.face, status=d.status, primary_ip=d.primary_ip.address if d.primary_ip else None,
            last_updated=d.last_updated, netbox_url=d.display_url)

    def termination(self, r: nb.PortRef) -> c.Termination:
        kind = r.kind
        if kind == "circuit_termination":
            ct = self.cterms.get(r.id)
            return c.Termination(kind=kind, id=r.id, name=r.term_side, device_id=None, device_name=None,
                                 circuit_id=r.circuit.id if r.circuit else None, circuit_cid=r.circuit.cid if r.circuit else None,
                                 site_id=ct.site_id if ct else None)
        return c.Termination(kind=kind, id=r.id, name=r.name, device_id=r.device.id if r.device else None,
                             device_name=r.device.name if r.device else None, circuit_id=None, circuit_cid=None,
                             site_id=self.device_site.get(r.device.id) if r.device else None)

    def cable(self, cb: nb.Cable) -> c.Cable:
        return c.Cable(id=cb.id, label=cb.label, type=cb.type, color=cb.color.lower() if cb.color else None, status=cb.status,
                       length=cb.length, length_unit=cb.length_unit,
                       a_terminations=[self.termination(t.object) for t in cb.a_terminations if t.object.kind],
                       b_terminations=[self.termination(t.object) for t in cb.b_terminations if t.object.kind],
                       netbox_url=cb.display_url)

    def cable_sites(self, cb: nb.Cable) -> set[int]:
        sites = {self.termination(t.object).site_id for t in cb.a_terminations + cb.b_terminations if t.object.kind}
        return {s for s in sites if s is not None}


def cable_refs(cables: list[nb.Cable]) -> list[nb.PortRef]:
    return [t.object for cb in cables for t in cb.a_terminations + cb.b_terminations]


def site_country(s: nb.Site) -> str | None:
    """ISO 3166-1 alpha-2 country of a site: the custom field iso_country, else the site's own region slug when it is
    two letters (a region named after its country code); anything else is unknown (None)."""
    for v in (s.cf("iso_country"), s.region.slug if s.region else None):
        if isinstance(v, str) and len(v.strip()) == 2 and v.strip().isascii() and v.strip().isalpha():
            return v.strip().upper()
    return None


def site(s: nb.Site) -> c.Site:
    theme = s.cf("iso_theme")
    return c.Site(id=s.id, slug=s.slug, name=s.name, status=s.status, latitude=s.latitude, longitude=s.longitude,
                  iso_theme=theme if theme in THEMES else None, country=site_country(s), description=s.description,
                  last_updated=s.last_updated, netbox_url=s.display_url)


def location(loc: nb.Location) -> c.Location:
    return c.Location(id=loc.id, site_id=loc.site.id, parent_id=loc.parent.id if loc.parent else None, name=loc.name,
                      slug=loc.slug, iso_x=loc.cf("iso_x"), iso_y=loc.cf("iso_y"), iso_w=loc.cf("iso_w"), iso_h=loc.cf("iso_h"),
                      iso_facing=loc.cf("iso_facing"), latitude=_num(loc.cf("iso_latitude")),
                      longitude=_num(loc.cf("iso_longitude")), last_updated=loc.last_updated, netbox_url=loc.display_url)


def rack(r: nb.Rack) -> c.Rack:
    return c.Rack(id=r.id, site_id=r.site.id, location_id=r.location.id if r.location else None, name=r.name,
                  status=r.status, u_height=r.u_height, starting_unit=r.starting_unit, desc_units=r.desc_units,
                  iso_x=r.cf("iso_x"), iso_y=r.cf("iso_y"), iso_facing=r.cf("iso_facing"),
                  latitude=_num(r.cf("iso_latitude")), longitude=_num(r.cf("iso_longitude")),
                  last_updated=r.last_updated, netbox_url=r.display_url)


def _by_id(xs: list[Any]) -> list[Any]:
    return sorted(xs, key=lambda x: x.id)


# ---- routes ----
class Brief(nb.NbModel):
    id: int
    site: nb.Ref
    rack: nb.Ref | None = None
    position: float | None = None


async def world(client: nb.NetBox) -> c.WorldResponse:
    version = await client.version()
    sites, locs, racks, devs, cables, cterms, circuits = await asyncio.gather(
        client.list(nb.Site, "dcim/sites"),
        client.list(Brief, "dcim/locations", {"fields": "id,site"}),
        client.list(Brief, "dcim/racks", {"fields": "id,site"}),
        client.list(Brief, "dcim/devices", {"fields": "id,site,rack,position"}),
        client.list(nb.Cable, "dcim/cables"),
        client.list(nb.CircuitTermination, "circuits/circuit-terminations"),
        client.list(nb.Circuit, "circuits/circuits"))
    d = Directory()
    d.device_site = {x.id: x.site.id for x in devs}
    d.cterms = {t.id: t for t in cterms}
    cable_sites = {cb.id: d.cable_sites(cb) for cb in cables}

    out = []
    for s in _by_id(sites):
        sd = [x for x in devs if x.site.id == s.id]
        out.append(c.WorldSite(site=site(s), counts=c.SiteCounts(
            locations=sum(1 for x in locs if x.site.id == s.id), racks=sum(1 for x in racks if x.site.id == s.id),
            devices=len(sd), unracked_devices=sum(1 for x in sd if x.rack is None or x.position is None),
            cables=sum(1 for ss in cable_sites.values() if s.id in ss))))

    links: dict[tuple[int, int], dict[str, list[Any]]] = {}

    def link(a: int, b: int) -> dict[str, list[Any]]:
        return links.setdefault((min(a, b), max(a, b)), {"cable_ids": [], "circuits": []})

    for cb in _by_id(cables):
        ss = cable_sites[cb.id]
        if len(ss) == 2:
            link(*ss)["cable_ids"].append(cb.id)
    for ci in _by_id(circuits):
        ends = {t.term_side: t.site_id for t in cterms if t.circuit.id == ci.id}
        a, z = ends.get("A"), ends.get("Z")
        if a and z and a != z:
            link(a, z)["circuits"].append(c.WorldCircuit(id=ci.id, cid=ci.cid, provider=ci.provider.name or "",
                                                      type=ci.type.name or "", status=ci.status))
    return c.WorldResponse(meta=meta(version), sites=out,
                           links=[c.WorldLink(a_site_id=a, b_site_id=b, **links[(a, b)]) for a, b in sorted(links)])


async def site_scene(client: nb.NetBox, site_id: int) -> c.SiteScene:
    version = await client.version()
    s = await client.get(nb.Site, "dcim/sites", site_id, "site")  # first: NetBox answers 400 to ?site_id=<unknown>
    q = {"site_id": site_id}
    locs, racks, devs, cables = await asyncio.gather(
        client.list(nb.Location, "dcim/locations", q), client.list(nb.Rack, "dcim/racks", q),
        client.list(nb.Device, "dcim/devices", q), client.list(nb.Cable, "dcim/cables", q))
    d = Directory()
    d.add_devices(devs)
    await asyncio.gather(d.load_types(client, devs), d.ensure(client, cable_refs(cables)))
    return c.SiteScene(meta=meta(version), site=site(s), locations=[location(x) for x in _by_id(locs)],
                       racks=[rack(x) for x in _by_id(racks)], devices=[d.device(x) for x in _by_id(devs)],
                       cables=[d.cable(x) for x in _by_id(cables)])


async def rack_scene(client: nb.NetBox, rack_id: int) -> c.RackScene:
    version = await client.version()
    r = await client.get(nb.Rack, "dcim/racks", rack_id, "rack")  # first, as in site_scene
    devs, cables = await asyncio.gather(client.list(nb.Device, "dcim/devices", {"rack_id": rack_id}),
                                        client.list(nb.Cable, "dcim/cables", {"rack_id": rack_id}))
    d = Directory()
    d.add_devices(devs)
    await asyncio.gather(d.load_types(client, devs), d.ensure(client, cable_refs(cables)))
    return c.RackScene(meta=meta(version), rack=rack(r), devices=[d.device(x) for x in _by_id(devs)],
                       cables=[d.cable(x) for x in _by_id(cables)])


def interface(d: Directory, kind: str, p: nb.Port) -> c.Interface:
    ce = p.connected_endpoints[0] if p.connected_endpoints and kind not in PASS_THROUGH else None
    return c.Interface(kind=kind, id=p.id, device_id=p.device.id, name=p.name, type=p.type,
                       enabled=p.enabled if kind == "interface" else True, mgmt_only=p.mgmt_only if kind == "interface" else False,
                       description=p.description, cable_id=p.cable.id if p.cable else None,
                       link_peers=[d.termination(x) for x in p.link_peers if x.kind],
                       connected_endpoint=d.termination(ce) if ce and ce.kind else None)


async def device_detail(client: nb.NetBox, device_id: int) -> c.DeviceDetail:
    version = await client.version()
    dev = await client.get(nb.Device, "dcim/devices", device_id, "device")  # first: a 404 must not list ports
    d = Directory()
    d.add_devices([dev])
    ports, _ = await asyncio.gather(client.device_ports(device_id), d.load_types(client, [dev]))
    cable_ids = list(dict.fromkeys(p.cable.id for _, p in ports if p.cable))
    cables = await client.by_ids(nb.Cable, "dcim/cables", cable_ids)
    refs = [x for _, p in ports for x in p.link_peers + (p.connected_endpoints or [])] + cable_refs(list(cables.values()))
    await d.ensure(client, refs)
    info = c.DeviceInfo(serial=dev.serial, asset_tag=dev.asset_tag, platform=dev.platform.name if dev.platform else None,
                        description=dev.description, comments=dev.comments, tags=[t.slug for t in dev.tags],
                        custom_fields=dev.custom_fields)
    return c.DeviceDetail(meta=meta(version), device=d.device(dev), info=info,
                          interfaces=[interface(d, k, p) for k, p in ports],
                          cables=[d.cable(cables[i]) for i in cable_ids if i in cables])


# ---- trace ----
Segment = tuple[list[nb.PortRef], "nb.TraceCable | None", list[nb.PortRef]]


def _refs(items: list[Any]) -> list[nb.PortRef]:
    out = [nb.PortRef.model_validate(x) for x in items if isinstance(x, dict) and "url" in x]
    return [r for r in out if r.kind]


def segments_from_trace(raw: list[Any]) -> list[Segment]:
    """NetBox /trace/ rows are [near ends, cable or null, far ends]."""
    return [(_refs(near), nb.TraceCable.model_validate(cable) if cable else None, _refs(far)) for near, cable, far in raw]


def segments_from_path(path: list[list[dict[str, Any]]]) -> list[Segment]:
    """A /paths/ entry's `path` is a flat list of node lists: near, [cable], far, near, [cable], far, ..."""
    segs: list[Segment] = []
    for i in range(0, len(path), 3):
        near = _refs(path[i])
        cab = path[i + 1] if i + 1 < len(path) else []
        far = _refs(path[i + 2]) if i + 2 < len(path) else []
        segs.append((near, nb.TraceCable.model_validate(cab[0]) if cab else None, far))
    return segs


def normalise(segs: list[Segment]) -> list[Segment]:
    """Contract rule 7: crossing a circuit is its own hop with cable null, from one circuit termination to the
    other. NetBox's trace jumps straight from the far end of one segment (termination Z) to the near end of the
    next (termination A); insert the hop between them. Empty near/far lists are kept as NetBox gives them."""
    out: list[Segment] = []
    for seg in segs:
        if out:
            prev_far, near = out[-1][2], seg[0]
            if (prev_far and near and all(r.kind == "circuit_termination" for r in prev_far + near)
                    and {r.id for r in prev_far} != {r.id for r in near}):
                out.append((prev_far, None, near))
        out.append(seg)
    return out


def endpoint_state(segs: list[Segment]) -> tuple[bool, bool]:
    """(is_complete, is_split) for a /trace/ answer, which does not carry them. Inferred from NetBox's path logic:
    a path is complete when its last far end is a path endpoint (interface, console/power port or outlet, power
    feed); NetBox stops at a rear port only when it cannot choose a position, i.e. the path splits."""
    if not segs or not segs[-1][2]:
        return False, False
    kinds = {r.kind for r in segs[-1][2]}
    return kinds <= nb.TRACE_KINDS, kinds == {"rear_port"}


async def trace(client: nb.NetBox, kind: str, port_id: int) -> c.TraceResponse:
    version = await client.version()
    origin_raw = await client.request("GET", f"{nb.PORT_ENDPOINT[kind]}/{port_id}/", not_found={"kind": kind, "id": port_id})
    origin = nb.PortRef.model_validate(origin_raw)
    if kind in nb.TRACE_KINDS:
        segs = segments_from_trace(await client.trace(kind, port_id))
        complete, split = endpoint_state(segs)
    else:
        # Pass-through ports (front/rear port, circuit termination) have no /trace/ in NetBox, only /paths/: every
        # cable path through the port. The trace from the port is the tail of the first path (lowest id) in which
        # the port is a near end, i.e. the path leaves through the port's own cable.
        segs, complete, split = [], False, False
        for p in sorted(await client.paths(kind, port_id), key=lambda p: p.id):
            ps = segments_from_path(p.path)
            at = next((i for i, s in enumerate(ps) if any(r.kind == kind and r.id == port_id for r in s[0])), None)
            if at is not None:
                segs, complete, split = ps[at:], p.is_complete, p.is_split
                break
    segs = normalise(segs)
    d = Directory()
    await d.ensure(client, [origin] + [r for s in segs for r in s[0] + s[2]])
    hops = [c.TraceHop(near_end=[d.termination(r) for r in near],
                       cable=c.CableRef(id=cb.id, label=cb.label, type=cb.type, color=cb.color.lower() if cb.color else None,
                                        status=cb.status or "connected") if cb else None,
                       far_end=[d.termination(r) for r in far]) for near, cb, far in segs]
    return c.TraceResponse(meta=meta(version), trace=c.CableTrace(origin=d.termination(origin), hops=hops,
                                                                  is_complete=complete, is_split=split))
