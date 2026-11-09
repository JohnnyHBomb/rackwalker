#!/usr/bin/env python3
"""Seed a dev NetBox (v4.7.x) with the iso custom fields and the synthetic network from seed_model.py.

Idempotent: every object is looked up by its natural key (slug/name/label) and created only when missing;
existing objects are patched back to the model's values. Running it twice leaves the counts unchanged.

    NETBOX_URL=http://localhost:8100 NETBOX_TOKEN=... python devnetbox/seed.py            # seed
    NETBOX_URL=... NETBOX_TOKEN=... python devnetbox/seed.py --counts                     # status + counts only
    NETBOX_URL=... NETBOX_TOKEN=... python devnetbox/seed.py --fields-only                # custom fields only

Only plain `requests`. Synthetic data only: refuse to run against anything but a dev stack (see _guard).
NETBOX_TOKEN is a v2 token (nbt_<key>.<token>, sent as Bearer); a bare v1 token is sent as `Token`.
"""
from __future__ import annotations

import json
import os
import re
import sys
import time
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent))
import seed_model as sm  # noqa: E402

KIND_ENDPOINT = {
    "interface": ("dcim/interfaces", "dcim.interface"),
    "front_port": ("dcim/front-ports", "dcim.frontport"),
    "rear_port": ("dcim/rear-ports", "dcim.rearport"),
    "power_port": ("dcim/power-ports", "dcim.powerport"),
    "power_outlet": ("dcim/power-outlets", "dcim.poweroutlet"),
}

# Custom fields the app relies on (contracts/README.md "NetBox storage").
CHOICE_SETS = {
    "iso-facing": [["N", "North (-y)"], ["E", "East (+x)"], ["S", "South (+y)"], ["W", "West (-x)"]],
    "iso-theme": [["debug", "Debug"], ["industrial-robotic", "Industrial / robotic"], ["farm", "Farm"],
                  ["business", "Business"], ["pagodas-nature", "Pagodas and nature"]],
}
CUSTOM_FIELDS = [
    # name, type, object types, extra
    ("iso_x", "integer", ["dcim.location", "dcim.rack"], {"label": "Iso X (tiles)"}),
    ("iso_y", "integer", ["dcim.location", "dcim.rack"], {"label": "Iso Y (tiles)"}),
    ("iso_w", "integer", ["dcim.location"], {"label": "Iso width (tiles)", "validation_minimum": 1}),
    ("iso_h", "integer", ["dcim.location"], {"label": "Iso depth (tiles)", "validation_minimum": 1}),
    ("iso_facing", "select", ["dcim.location", "dcim.rack"], {"label": "Iso facing", "choice_set": "iso-facing"}),
    ("iso_theme", "select", ["dcim.site"], {"label": "Iso theme", "choice_set": "iso-theme"}),
    ("iso_country", "text", ["dcim.site"], {"label": "Country (ISO 3166-1 alpha-2)", "validation_regex": "^[A-Z]{2}$"}),
    ("iso_latitude", "decimal", ["dcim.location", "dcim.rack"], {"label": "Latitude"}),
    ("iso_longitude", "decimal", ["dcim.location", "dcim.rack"], {"label": "Longitude"}),
]
CF_GROUP = "rackwalker"


SUPPORTED_PREFIX = "4.7."


def auth_header(token: str) -> str:
    """NetBox >= 4.5 v2 tokens (nbt_<key>.<token>) use Bearer; legacy v1 tokens use Token."""
    return f"Bearer {token}" if token.startswith("nbt_") else f"Token {token}"


class NB:
    def __init__(self, url: str, token: str):
        self.url = url.rstrip("/") + "/api/"
        self.s = requests.Session()
        self.s.headers.update({"Authorization": auth_header(token), "Accept": "application/json",
                               "Content-Type": "application/json"})
        self.created = 0
        self.patched = 0

    def req(self, method: str, path: str, **kw):
        for attempt in range(5):
            r = self.s.request(method, self.url + path, timeout=30, **kw)
            if r.status_code < 500:
                break
            time.sleep(1 + attempt)
        if r.status_code >= 400:
            raise SystemExit(f"seed: {method} {path} -> HTTP {r.status_code}: {r.text[:500]}")
        return r.json() if r.content else None

    def list(self, path: str, **params):
        out, params = [], {"limit": 1000, **params}
        while True:
            page = self.req("GET", path + "/", params=params)
            out += page["results"]
            if not page.get("next"):
                return out
            params["offset"] = params.get("offset", 0) + params["limit"]

    def count(self, path: str, **params) -> int:
        return self.req("GET", path + "/", params={"limit": 1, **params})["count"]

    def ensure(self, path: str, lookup: dict, payload: dict) -> dict:
        found = self.list(path, **lookup)
        if len(found) > 1:
            raise SystemExit(f"seed: {path} lookup {lookup} matched {len(found)} objects")
        if not found:
            self.created += 1
            return self.req("POST", path + "/", data=json.dumps(payload))
        obj = found[0]
        diff = {k: v for k, v in payload.items() if not _same(obj.get(k), v)}
        if diff:
            self.patched += 1
            return self.req("PATCH", f"{path}/{obj['id']}/", data=json.dumps(diff))
        return obj


def _same(have, want) -> bool:
    if want in ("", None) and have in ("", None):
        return True
    if isinstance(want, dict) and isinstance(have, dict):
        return all(_same(have.get(k), v) for k, v in want.items())
    if isinstance(have, dict):  # FK or choice field returned as an object
        if "value" in have and not isinstance(want, dict):
            return have["value"] == want
        if "id" in have:
            return have["id"] == want
    if isinstance(want, list) and isinstance(have, list) and any(isinstance(w, dict) for w in want):
        return len(have) == len(want) and all(_same(h, w) for h, w in zip(have, want))
    if isinstance(want, list) and isinstance(have, list):
        return sorted(map(str, have)) == sorted(map(str, want))
    if isinstance(want, float) or isinstance(have, float):
        try:
            return have is not None and want is not None and abs(float(have) - float(want)) < 1e-6
        except (TypeError, ValueError):
            return False
    return have == want


def slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")


def _guard(nb: NB) -> dict:
    st = nb.req("GET", "status/")
    ver = st.get("netbox-version", "")
    if not ver.startswith(SUPPORTED_PREFIX):
        raise SystemExit(f"seed: expected NetBox {SUPPORTED_PREFIX}x, got {ver!r}")
    # Dev stacks only: either empty or already carrying our synthetic marker site.
    if nb.count("dcim/sites") and not nb.count("dcim/sites", slug="north-depot"):
        raise SystemExit("seed: this NetBox has sites but none of ours; refusing (synthetic dev stacks only)")
    return st


def seed_fields(nb: NB) -> None:
    sets = {}
    for name, choices in CHOICE_SETS.items():
        sets[name] = nb.ensure("extras/custom-field-choice-sets", {"name": name},
                               {"name": name, "extra_choices": choices, "order_alphabetically": False})["id"]
    for i, (name, typ, objs, extra) in enumerate(CUSTOM_FIELDS):
        payload = {"name": name, "type": typ, "object_types": objs, "group_name": CF_GROUP, "required": False,
                   "weight": 100 + i, "description": "RackWalker layout (see contracts/README.md)", **extra}
        if "choice_set" in payload:
            payload["choice_set"] = sets[payload["choice_set"]]
        nb.ensure("extras/custom-fields", {"name": name}, payload)


def seed_data(nb: NB, m: sm.Model) -> None:
    site_id, loc_id, rack_id, dev_id, dt_id, role_id = {}, {}, {}, {}, {}, {}
    for s in m.sites:
        site_id[s.slug] = nb.ensure("dcim/sites", {"slug": s.slug}, {
            "name": s.name, "slug": s.slug, "status": "active", "latitude": round(s.lat, 6), "longitude": round(s.lon, 6),
            "description": f"Synthetic site near {s.town}", "custom_fields": {"iso_theme": s.theme, "iso_country": s.country}})["id"]
    for loc in m.locations:
        x, y, w, h = loc.iso
        cf = {"iso_x": x, "iso_y": y, "iso_w": w, "iso_h": h, "iso_facing": loc.facing,
              "iso_latitude": loc.gps[0] if loc.gps else None, "iso_longitude": loc.gps[1] if loc.gps else None}
        loc_id[loc.slug] = nb.ensure("dcim/locations", {"slug": loc.slug, "site_id": site_id[loc.site]}, {
            "name": loc.name, "slug": loc.slug, "site": site_id[loc.site], "status": "active", "custom_fields": cf})["id"]
    for r in m.racks:
        cf = {"iso_x": r.iso[0], "iso_y": r.iso[1], "iso_facing": r.facing,
              "iso_latitude": r.gps[0] if r.gps else None, "iso_longitude": r.gps[1] if r.gps else None}
        rack_id[r.name] = nb.ensure("dcim/racks", {"name": r.name, "site_id": site_id[r.site]}, {
            "name": r.name, "site": site_id[r.site], "location": loc_id[r.location], "status": "active",
            "u_height": r.u_height, "width": 19, "custom_fields": cf})["id"]
    for name in sm.MANUFACTURERS:
        nb.ensure("dcim/manufacturers", {"slug": slug(name)}, {"name": name, "slug": slug(name)})
    for rslug, (rname, colour) in sm.ROLES.items():
        role_id[rslug] = nb.ensure("dcim/device-roles", {"slug": rslug}, {"name": rname, "slug": rslug, "color": colour})["id"]
    for t in sm.DEVICE_TYPES.values():
        man = nb.list("dcim/manufacturers", slug=slug(t.manufacturer))[0]["id"]
        dt = nb.ensure("dcim/device-types", {"slug": t.slug}, {
            "manufacturer": man, "model": t.model, "slug": t.slug, "u_height": t.u_height, "is_full_depth": t.is_full_depth})
        dt_id[t.slug] = dt["id"]
        for name, typ, mgmt in t.interfaces:
            nb.ensure("dcim/interface-templates", {"device_type_id": dt["id"], "name": name},
                      {"device_type": dt["id"], "name": name, "type": typ, "mgmt_only": mgmt})
        for i in range(1, t.front_rear + 1):
            rp = nb.ensure("dcim/rear-port-templates", {"device_type_id": dt["id"], "name": str(i)},
                           {"device_type": dt["id"], "name": str(i), "type": "lc", "positions": 1})
            nb.ensure("dcim/front-port-templates", {"device_type_id": dt["id"], "name": str(i)},
                      {"device_type": dt["id"], "name": str(i), "type": "lc", "positions": 1,
                       # NetBox >= 4.5: front/rear port mappings are a list (rear_port/rear_port_position were removed).
                       "rear_ports": [{"position": 1, "rear_port": rp["id"], "rear_port_position": 1}]})
        for name in t.power_ports:
            nb.ensure("dcim/power-port-templates", {"device_type_id": dt["id"], "name": name},
                      {"device_type": dt["id"], "name": name, "type": "iec-60320-c14"})
        for name in t.power_outlets:
            nb.ensure("dcim/power-outlet-templates", {"device_type_id": dt["id"], "name": name},
                      {"device_type": dt["id"], "name": name, "type": "iec-60320-c13"})
    for d in m.devices:
        payload = {"name": d.name, "device_type": dt_id[d.dtype], "role": role_id[d.role], "site": site_id[d.site],
                   "location": loc_id[d.location], "rack": rack_id[d.rack] if d.rack else None,
                   "position": d.position, "face": d.face or "", "status": d.status, "serial": d.serial}
        dev_id[d.name] = nb.ensure("dcim/devices", {"name": d.name, "site_id": site_id[d.site]}, payload)["id"]
    # Primary IPs on the management-ish interface of each device that has one.
    mgmt_if = {"ex-c9000": "mgmt0", "ex-a2400": "mgmt0", "ex-r400": "ge-0/0/0", "ex-f200": "port8", "ex-s2": "eth0"}
    for d in m.devices:
        if not d.ip:
            continue
        iface = nb.list("dcim/interfaces", device_id=dev_id[d.name], name=mgmt_if[d.dtype])[0]
        ip = nb.ensure("ipam/ip-addresses", {"address": d.ip}, {
            "address": d.ip, "status": "active", "assigned_object_type": "dcim.interface", "assigned_object_id": iface["id"],
            "dns_name": f"{d.name}.example.net"})
        nb.ensure("dcim/devices", {"name": d.name, "site_id": site_id[d.site]}, {"primary_ip4": ip["id"]})
    # Circuits
    prov = nb.ensure("circuits/providers", {"slug": "example-carrier"}, {"name": "Example Carrier", "slug": "example-carrier"})["id"]
    ctype = nb.ensure("circuits/circuit-types", {"slug": "ethernet-wan"}, {"name": "Ethernet WAN", "slug": "ethernet-wan"})["id"]
    term_id = {}
    for c in m.circuits:
        cid = nb.ensure("circuits/circuits", {"cid": c.cid}, {"cid": c.cid, "provider": prov, "type": ctype, "status": c.status,
                                                             "commit_rate": 1000000})["id"]
        for side, site in (("A", c.a_site), ("Z", c.z_site)):
            term_id[(c.cid, side)] = nb.ensure("circuits/circuit-terminations", {"circuit_id": cid, "term_side": side}, {
                "circuit": cid, "term_side": side, "termination_type": "dcim.site", "termination_id": site_id[site],
                "port_speed": 1000000})["id"]

    def term(end):
        kind, owner, port = end
        if kind == "circuit_termination":
            return {"object_type": "circuits.circuittermination", "object_id": term_id[(owner, port)]}
        path, otype = KIND_ENDPOINT[kind]
        obj = nb.list(path, device_id=dev_id[owner], name=port)
        if len(obj) != 1:
            raise SystemExit(f"seed: {kind} {owner}:{port} not found")
        return {"object_type": otype, "object_id": obj[0]["id"]}

    existing = {c["label"] for c in nb.list("dcim/cables") if c.get("label")}
    for c in m.cables:
        if c.label in existing:
            continue
        payload = {"label": c.label, "type": c.type, "status": c.status, "color": c.color,
                   "a_terminations": [term(c.a)], "b_terminations": [term(c.b)]}
        if c.length is not None:
            payload.update(length=c.length, length_unit="m")
        nb.req("POST", "dcim/cables/", data=json.dumps(payload))
        nb.created += 1


def counts(nb: NB) -> dict:
    eps = {"sites": "dcim/sites", "locations": "dcim/locations", "racks": "dcim/racks", "devices": "dcim/devices",
           "interfaces": "dcim/interfaces", "cables": "dcim/cables", "circuits": "circuits/circuits",
           "custom_fields": "extras/custom-fields"}
    return {k: nb.count(v) for k, v in eps.items()}


def main(argv: list[str]) -> int:
    url, token = os.environ.get("NETBOX_URL"), os.environ.get("NETBOX_TOKEN")
    if not url or not token:
        raise SystemExit("seed: set NETBOX_URL and NETBOX_TOKEN")
    nb = NB(url, token)
    if "--counts" in argv:
        st = nb.req("GET", "status/")
        print(json.dumps({"netbox_version": st.get("netbox-version"), **counts(nb)}))
        return 0
    st = _guard(nb)
    t0 = time.time()
    seed_fields(nb)
    if "--fields-only" not in argv:
        seed_data(nb, sm.build())
    print(json.dumps({"netbox_version": st.get("netbox-version"), "created": nb.created, "patched": nb.patched,
                      "seconds": round(time.time() - t0, 1), **counts(nb)}))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
