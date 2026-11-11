"""The change planner, one rule at a time, on hand-built state (no NetBox)."""
from __future__ import annotations

import copy

import pytest

from rackwalker_proxy.changes import WHITELIST, plan, result

from .conftest import schema_validator
from .plan_helpers import (TS, cable, create, device, interface, location, move, ops, place, rack, site, state, unplace,
                           update)

BASE = [site(1), site(2), location(1), location(2), location(9, site_id=2), rack(1), rack(2, location_id=2),
        rack(9, site_id=2, location_id=9)]


def run(*raw, extra=()):
    st = state(*BASE, *extra)
    snapshot = copy.deepcopy(st)
    out = plan(ops(*raw), st)
    assert st == snapshot, "plan must not modify its input state"
    res = result("preview", out, False).model_dump(mode="json", exclude_unset=True)
    schema_validator("ChangeResult").validate(res)
    for o in res["ops"]:  # shape invariants of every op result
        if o["status"] == "error":
            assert (o["before"], o["after"], o["diff"]) == (None, None, []) and o["error"]["code"]
        else:
            assert o["error"] is None
        assert (o["status"] == "no_change") == (o["status"] != "error" and o["diff"] == [])
    return res["ops"]


def err(o):
    return (o["status"], o["error"]["code"]) if o["error"] else (o["status"], None)


# ---- move_rack
def test_move_rack_diff_and_no_change():
    a, b = run(move(1, iso_x=4, iso_y=0), move(1, iso_x=4))
    assert a["before"] == {"iso_x": 0, "iso_y": 0} and a["after"] == {"iso_x": 4, "iso_y": 0}
    assert a["diff"] == [{"field": "iso_x", "before": 0, "after": 4}]
    assert (b["status"], b["before"]) == ("no_change", {"iso_x": 4})  # sees the effect of the earlier op


def test_move_rack_errors():
    assert err(run(move(77, iso_x=1))[0]) == ("error", "not_found")
    assert err(run(move(1, location_id=9))[0]) == ("error", "validation_failed")  # location in another site
    assert err(run(move(1, location_id=55))[0]) == ("error", "not_found")
    o = run(move(1, location_id=2))[0]
    assert o["status"] == "ok" and o["diff"] == [{"field": "location_id", "before": 1, "after": 2}]
    assert err(run(move(1, location_id=None))[0]) == ("ok", None)  # a rack may leave its location


def test_move_rack_into_location_with_same_name_rejected():
    o = run(move(1, location_id=2), extra=[rack(3, location_id=2, name="R1")])[0]
    assert (err(o), o["error"]["details"]) == (("error", "validation_failed"), {"field": "name", "rack_id": 3})
    assert "location 2" in o["error"]["message"]
    # NetBox 4.7.4 Rack.Meta UniqueConstraint(location, name): case-sensitive (measured live, 8 Nov), and a rack that
    # leaves its location never collides (NULL location_id is distinct)
    assert err(run(move(1, location_id=2), extra=[rack(3, location_id=2, name="r1")])[0]) == ("ok", None)
    assert err(run(move(1, location_id=None), extra=[rack(3, location_id=None, name="R1")])[0]) == ("ok", None)


@pytest.mark.parametrize("other,new_name,ok", [
    (dict(location_id=1, name="Ab"), "Ab", False),     # same location, same name
    (dict(location_id=1, name="Ab"), "ab", True),      # case differs: NetBox accepts
    (dict(location_id=2, name="Ab"), "Ab", True),      # another location
    (dict(location_id=None, name="Ab"), "Ab", True),   # the other rack has no location
])
def test_rename_rack_rule_matches_netbox(other, new_name, ok):
    o = run(update("rack", 1, name=new_name), extra=[rack(4, **other)])[0]
    assert err(o) == (("ok", None) if ok else ("error", "validation_failed"))


def test_racks_without_location_never_collide():
    o = run(update("rack", 1, name="Free"), extra=[rack(1, location_id=None), rack(4, location_id=None, name="Free")])[0]
    assert err(o) == ("ok", None)
    assert err(run(create("Free", location_id=None), extra=[rack(4, location_id=None, name="Free")])[0]) == ("ok", None)


@pytest.mark.parametrize("since,status", [("2026-11-07T09:00:00Z", "error"), (TS, "ok"), ("2026-11-07T11:00:00+01:00", "ok"),
                                          ("2026-11-07T10:00:01Z", "ok")])
def test_optimistic_lock(since, status):
    o = run({**move(1, iso_x=1), "if_unmodified_since": since})[0]
    assert o["status"] == status
    if status == "error":
        assert o["error"]["code"] == "conflict" and o["error"]["details"]["last_updated"] == TS
        assert o["error"]["details"]["if_unmodified_since"][:19] == since[:19]
        # the message is read by people: the rack's name, no raw timestamps (regression)
        assert o["error"]["message"] == "Rack R1 was changed in NetBox after you loaded it: reload, then stage this change again."
        assert TS not in o["error"]["message"] and since[:19] not in o["error"]["message"]


# ---- place / unplace
def test_place_ok_and_location_follows_rack():
    o = run(place(5, 2, 10), extra=[device(5, location_id=1)])[0]
    assert o["after"] == {"rack_id": 2, "position": 10, "face": "front", "location_id": 2}
    assert {"field": "location_id", "before": 1, "after": 2} in o["diff"]
    o = run(place(5, 1, 10), extra=[device(5, location_id=1)])[0]
    assert "location_id" not in o["after"]


@pytest.mark.parametrize("other,new,code", [
    (dict(position=10, face="front"), dict(position=10, face="front"), "conflict"),       # same slot, same face
    (dict(position=10, face="front"), dict(position=10, face="rear"), None),              # half depth, opposite faces
    (dict(position=10, face="front", full=True), dict(position=10, face="rear"), "conflict"),  # full depth blocks both
    (dict(position=10, face="front", h=2), dict(position=11, face="front"), "conflict"),  # overlaps the upper U
    (dict(position=10, face="front", h=2), dict(position=12, face="front"), None),        # adjacent is fine
    (dict(position=10, face="front", h=0.5), dict(position=10.5, face="front", h=0.5), None),  # half-U neighbours
    (dict(position=10, face="front", h=0.5), dict(position=10, face="front", h=0.5), "conflict"),
    (dict(position=None, face=None, h=0), dict(position=10, face="front"), None),         # zero-U PDU takes no slot
    # new device directly BELOW the other one (previously: `op_ <= pos + h` survived without these)
    (dict(position=10, face="front"), dict(position=9, face="front"), None),              # adjacent below is fine
    (dict(position=10, face="front"), dict(position=8, face="front", h=2), None),         # 2U ending just below
    (dict(position=10, face="front"), dict(position=9, face="front", h=2), "conflict"),   # 2U reaching into U10
    (dict(position=10, face="front", h=0.5), dict(position=9.5, face="front", h=0.5), None),  # half-U below
])
def test_slot_collisions(other, new, code):
    h_new = new.pop("h", 1.0)
    full_other, h_other = other.pop("full", False), other.pop("h", 1.0)
    o = run(place(5, 1, new["position"], new["face"]),
            extra=[device(4, rack_id=1, full=full_other, h=h_other, **other), device(5, h=h_new)])[0]
    assert err(o) == (("ok", None) if code is None else ("error", code))
    if code == "conflict":
        assert o["error"]["details"]["device_id"] == 4


@pytest.mark.parametrize("u,start,pos,h,ok", [(42, 1, 42, 1, True), (42, 1, 42, 2, False), (42, 1, 41, 2, True),
                                             (42, 1, 0.5, 1, False), (24, 1, 24.5, 0.5, True), (24, 1, 25, 1, False),
                                             (10, 5, 4, 1, False), (10, 5, 5, 1, True), (10, 5, 14, 1, True), (10, 5, 15, 1, False)])
def test_rack_bounds(u, start, pos, h, ok):
    o = run(place(5, 3, pos), extra=[rack(3, u=u, start=start), device(5, h=h)])[0]
    assert err(o) == (("ok", None) if ok else ("error", "validation_failed"))
    if not ok:
        assert o["error"]["details"]["field"] == "position"


def test_place_rules():
    assert err(run(place(5, 1, 1), extra=[device(5, h=0)])[0]) == ("error", "validation_failed")          # zero-U
    assert err(run(place(5, 1, 1), extra=[device(5, child=True)])[0]) == ("error", "validation_failed")   # child
    assert err(run(place(5, 9, 1), extra=[device(5)])[0]) == ("error", "validation_failed")               # other site
    assert err(run(place(5, 77, 1), extra=[device(5)])[0]) == ("error", "not_found")
    assert err(run(place(77, 1, 1))[0]) == ("error", "not_found")


def test_move_within_own_slot_and_sequences():
    me = device(5, rack_id=1, position=10, face="front", h=2)
    assert err(run(place(5, 1, 11), extra=[me])[0]) == ("ok", None)  # overlapping only itself
    a, b = run(place(6, 1, 10), place(6, 1, 10), extra=[me, device(6)])
    assert err(a) == ("error", "conflict") and err(b) == ("error", "conflict")
    a, b = run(unplace(5), place(6, 1, 10), extra=[me, device(6)])  # freed by the earlier op
    assert (err(a), err(b)) == (("ok", None), ("ok", None))
    a, b = run(place(6, 1, 20), place(7, 1, 20), extra=[device(6), device(7)])  # taken by the earlier op
    assert (err(a), err(b)) == (("ok", None), ("error", "conflict"))


def test_unplace():
    o = run(unplace(5), extra=[device(5, rack_id=1, position=3, face="rear")])[0]
    assert o["before"] == {"rack_id": 1, "position": 3, "face": "rear"} and o["after"] == {"rack_id": None, "position": None, "face": None}
    assert run(unplace(5), extra=[device(5)])[0]["status"] == "no_change"


# ---- create_rack and references
def test_create_rack_defaults_and_order():
    o = run(create("New", ref="n", location_id=1, iso_facing="S", iso_x=9))[0]
    assert list(o["after"]) == ["site_id", "location_id", "name", "status", "u_height", "iso_x", "iso_facing"]
    assert (o["after"]["status"], o["after"]["u_height"], o["before"], o["target"]["id"]) == ("planned", 42, None, None)
    assert [d["before"] for d in o["diff"]] == [None] * 7


def test_references():
    a, b, c_, d = run(create("New", ref="n", location_id=1, u_height=10), place(5, "@n", 10), move("@n", iso_facing="E"),
                      place(6, "@n", 10), extra=[device(5), device(6)])
    assert [err(x) for x in (a, b, c_)] == [("ok", None)] * 3
    assert b["after"]["rack_id"] == "@n" and c_["target"]["id"] == "@n" and c_["before"] == {"iso_facing": None}
    assert err(d) == ("error", "conflict")  # the new rack's slot is now taken by device 5
    assert err(run(place(5, "@n", 11), create("New", ref="n"), extra=[device(5)])[0]) == ("error", "not_found")  # forward ref
    assert err(run(place(5, "@n", 1), extra=[device(5)])[0]) == ("error", "not_found")
    x, y = run(create("New", ref="n", status="bogus"), place(5, "@n", 1), extra=[device(5)])
    assert err(x) == ("error", "validation_failed") and err(y) == ("error", "validation_failed")  # names a failed op
    x, y = run(update("device", 5, name="a", ) | {"client_ref": "d"}, place(6, "@d", 1), extra=[device(5), device(6)])
    assert err(y) == ("error", "validation_failed")  # @d names a device, not a rack


def test_create_rack_errors():
    assert err(run(create("R1", location_id=1))[0]) == ("error", "validation_failed")          # duplicate of rack 1
    assert err(run(create("R1", location_id=None))[0]) == ("ok", None)                          # other location: fine
    assert err(run(create("X", location_id=9))[0]) == ("error", "validation_failed")            # location of site 2
    assert err(run(create("X") | {"fields": {"site_id": 77, "name": "X"}})[0]) == ("error", "not_found")
    a, b = run(create("Y", ref="y"), create("Z", ref="y"))
    assert err(b) == ("error", "validation_failed") and b["error"]["details"]["field"] == "client_ref"
    a, b = run(create("Y", location_id=1), create("Y", location_id=1))
    assert err(b) == ("error", "validation_failed")  # a name used earlier in the same set
    a, b = run(create("Y", location_id=1), create("y", location_id=1))
    assert err(b) == ("ok", None)  # NetBox compares rack names case-sensitively


# ---- update_fields
def test_update_whitelist_message_matches_fixture():
    o = run(update("device", 5, primary_ip="192.0.2.99/24"), extra=[device(5)])[0]
    assert o["error"] == {"code": "validation_failed", "message": "Field 'primary_ip' is not editable on device in contract 2.5.0.",
                          "details": {"field": "primary_ip", "allowed": WHITELIST["device"]}}


@pytest.mark.parametrize("kind,oid,fields,ok", [
    ("site", 1, {"latitude": 54.1, "longitude": -1.34, "iso_theme": "farm"}, True),
    ("site", 1, {"latitude": 91}, False), ("site", 1, {"iso_theme": "western"}, False), ("site", 1, {"name": ""}, False),
    ("site", 1, {"status": "retired"}, True), ("site", 1, {"status": "gone"}, False), ("site", 1, {"iso_theme": None}, "no_change"),
    ("location", 1, {"iso_w": 0}, False), ("location", 1, {"iso_w": 256, "iso_h": 1}, True), ("location", 1, {"iso_x": True}, False),
    ("location", 1, {"iso_facing": "X"}, False), ("location", 1, {"latitude": 51.5}, True),
    ("rack", 1, {"iso_x": 4097}, False), ("rack", 1, {"iso_x": -4096, "comments": "x"}, True), ("rack", 1, {"status": "active"}, "no_change"),
    ("rack", 1, {"iso_x": 1.5}, False), ("rack", 1, {"name": "R2"}, True), ("rack", 1, {"name": "r2"}, True),
    ("device", 5, {"name": None}, True), ("device", 5, {"name": "x" * 65}, False), ("device", 5, {"asset_tag": ""}, False),
    ("device", 5, {"serial": "S" * 50, "asset_tag": None}, True), ("device", 5, {"status": "staged"}, True),
    ("cable", 3, {"color": "ff00aa", "length": 2.5, "length_unit": "m"}, True), ("cable", 3, {"color": "FF00AA"}, False),
    ("cable", 3, {"length": -1}, False),
    ("cable", 3, {"length": 2.5}, False), ("cable", 3, {"length": None, "length_unit": "m"}, True), ("cable", 3, {"length_unit": "yd"}, False), ("cable", 3, {"status": "planned"}, True),
    ("interface", 4, {"enabled": False, "description": "uplink"}, True), ("interface", 4, {"enabled": "no"}, False),
    ("interface", 4, {"mtu": 9000}, False), ("cable", 77, {"label": "x"}, "not_found"),
])
def test_update_fields_values(kind, oid, fields, ok):
    o = run(update(kind, oid, **fields), extra=[device(5), cable(3), interface(4)])[0]
    want = {True: ("ok", None), False: ("error", "validation_failed"), "no_change": ("no_change", None),
            "not_found": ("error", "not_found")}[ok]
    assert err(o) == want
    if ok is True:
        assert o["after"] == fields


def test_rename_rack_onto_sibling_rejected():
    o = run(update("rack", 1, name="R3"), extra=[rack(3)])[0]
    assert err(o) == ("error", "validation_failed")


# Regression: NetBox 4.7 rejects a device rename onto Lower(name) + site + tenant of another device; preview
# said ok and apply wrote the earlier ops, then NetBox refused the rename (partial apply).
@pytest.mark.parametrize("other,new_name,code", [
    (dict(name="core-a"), "CORE-A", "validation_failed"),                       # case-insensitive, both tenant-less
    (dict(name="core-a", _tenant_id=7), "core-a", None),                        # different tenant
    (dict(name="core-a", site_id=2, location_id=9), "core-a", None),            # different site
    (dict(name=None), "d4", None),                                              # unnamed devices never collide
    (dict(name="core-a"), "core-b", None),
])
def test_rename_device_onto_sibling(other, new_name, code):
    o = run(update("device", 5, name=new_name), extra=[device(4, **other), device(5)])[0]
    assert err(o) == (("ok", None) if code is None else ("error", code))
    if code:
        assert o["error"]["details"] == {"field": "name", "device_id": 4}


def test_partial_apply_shape_is_caught_in_preview():
    """The failing set: [describe A, rename B onto A's name upper-cased] must fail op 1 in preview, so apply
    writes nothing; a rename to None (unnamed) and a swap via an intermediate name stay ok."""
    a, b = run(update("device", 4, description="rv partial"), update("device", 5, name="D4"),
               extra=[device(4), device(5)])
    assert err(a) == ("ok", None) and err(b) == ("error", "validation_failed")
    x, y, z = run(update("device", 4, name="tmp"), update("device", 5, name="d4"), update("device", 4, name="d5"),
                  extra=[device(4), device(5)])
    assert [err(o) for o in (x, y, z)] == [("ok", None)] * 3


def test_fixture_style_set_keeps_going_after_an_error():
    out = run(update("device", 5, primary_ip="x"), move(1, iso_x=2), extra=[device(5)])
    assert [o["status"] for o in out] == ["error", "ok"]


# ---- regressions: NetBox rules preview used to let through (both measured as 400 on 4.7.4)
@pytest.mark.parametrize("other,ok", [
    (dict(name="Hall"), False),                  # top-level sibling (NULL parents collide: nulls_distinct=False)
    (dict(name="hall"), True),                   # case-sensitive
    (dict(name="Hall", site_id=2), True),        # another site
    (dict(name="Hall", _parent_id=3), True),     # another parent
])
def test_rename_location_onto_sibling(other, ok):
    o = run(update("location", 7, name="Hall"), extra=[location(7), location(8, **other)])[0]
    assert err(o) == (("ok", None) if ok else ("error", "validation_failed"))
    if not ok:
        assert o["error"]["details"] == {"field": "name", "location_id": 8}


def test_rename_location_under_a_parent():
    extra = [location(7, name="A", _parent_id=1), location(8, name="B", _parent_id=1)]
    assert err(run(update("location", 7, name="B"), extra=extra)[0]) == ("error", "validation_failed")
    assert err(run(update("location", 7, name="L2"), extra=extra)[0]) == ("ok", None)  # L2 is top-level, not a sibling


@pytest.mark.parametrize("start,fields,ok", [
    (dict(), {"length": 3}, False),                                      # the failing case: unit is null
    (dict(length_unit="m"), {"length": 3}, True),                        # unit already set
    (dict(length=3, length_unit="m"), {"length_unit": None}, False),     # dropping the unit of a cable with a length
    (dict(length=3, length_unit="m"), {"length": None}, True),           # NetBox clears the unit itself
    (dict(), {"length": 3, "length_unit": "ft"}, True),
    (dict(length=3, length_unit="m"), {"label": "x"}, True),
])
def test_cable_length_needs_unit(start, fields, ok):
    o = run(update("cable", 3, **fields), extra=[cable(3, **start)])[0]
    assert err(o) == (("ok", None) if ok else ("error", "validation_failed"))
    if not ok:
        assert o["error"]["details"]["field"] in fields


# ---- regressions: rules NetBox 4.7.4 enforces on write that preview let through
# (each measured live as ok in preview, then 400 on apply; case sensitivity measured on 4.7.4 too)
def test_asset_tag_is_unique_across_netbox():
    other_site = device(40, site_id=2, location_id=9, asset_tag="AT-1")
    o = run(update("device", 5, asset_tag="AT-1"), extra=[device(5), other_site])[0]
    assert err(o) == ("error", "validation_failed") and o["error"]["details"] == {"field": "asset_tag", "device_id": 40}
    assert err(run(update("device", 5, asset_tag="at-1"), extra=[device(5), other_site])[0]) == ("ok", None)  # case-sensitive
    # the failing set: two devices given the same new tag in one ChangeSet -> op 1 fails in preview, nothing is written
    a, b = run(update("device", 2, asset_tag="AT-9"), update("device", 3, asset_tag="AT-9"), extra=[device(2), device(3)])
    assert [err(a), err(b)] == [("ok", None), ("error", "validation_failed")]
    # moving a tag from one device to another inside the set is fine
    x, y = run(update("device", 2, asset_tag=None), update("device", 3, asset_tag="AT-2"),
               extra=[device(2, asset_tag="AT-2"), device(3)])
    assert [err(x), err(y)] == [("ok", None), ("ok", None)]
    assert err(run(update("device", 2, asset_tag="AT-2"), extra=[device(2, asset_tag="AT-2")])[0]) == ("no_change", None)


def test_site_name_is_unique_across_netbox():
    o = run(update("site", 1, name="S2"))[0]
    assert err(o) == ("error", "validation_failed") and o["error"]["details"] == {"field": "name", "site_id": 2}
    assert err(run(update("site", 1, name="s2"))[0]) == ("ok", None)  # case-sensitive (measured)
    a, b = run(update("site", 2, name="Old"), update("site", 1, name="S2"))
    assert [err(a), err(b)] == [("ok", None), ("ok", None)]


@pytest.mark.parametrize("field,value,ok", [
    ("latitude", 51.12345678, False),     # the failing value: NetBox "no more than 8 digits in total"
    ("latitude", 51.123456, True),
    ("latitude", -89.999999, True),
    ("longitude", 179.1234567, False),    # measured: "no more than 9 digits in total"
    ("longitude", -179.123456, True),
    ("longitude", 2, True),
])
def test_site_coordinates_fit_netbox_decimal_places(field, value, ok):
    o = run(update("site", 1, **{field: value}))[0]
    assert err(o) == (("ok", None) if ok else ("error", "validation_failed"))
    if not ok:
        assert o["error"]["details"] == {"field": field, "decimal_places": 6}


def test_rack_coordinates_are_custom_fields_without_the_site_limit():
    # iso_latitude/iso_longitude are decimal custom fields: 51.123456789 was accepted by NetBox 4.7.4 (measured)
    assert err(run(update("rack", 1, latitude=51.123456789))[0]) == ("ok", None)


class _GatherClient:
    """Answers gather()'s reads from canned rows and records the list queries."""

    def __init__(self, devices, sites):
        self.devices, self.sites, self.queries = devices, sites, []

    async def by_ids(self, model, path, ids, params=None):
        rows = {"dcim/devices": self.devices, "dcim/sites": self.sites}.get(path, [])
        return {r["id"]: model.model_validate(r) for r in rows if r["id"] in set(ids)}

    async def list(self, model, path, params=None):
        self.queries.append((path, dict(params or {})))
        rows = {"dcim/devices": self.devices, "dcim/sites": self.sites}.get(path, [])
        keep = [r for r in rows if all(k == "site_id" and r["site"]["id"] == v or r.get(k) == v for k, v in (params or {}).items())]
        return [model.model_validate(r) for r in keep]

    async def device_types(self, ids):
        return {}


def test_gather_loads_the_global_holders_of_a_tag_or_site_name():
    import asyncio

    from rackwalker_proxy.changes import gather

    def dev(i, site_id, tag):
        return {"id": i, "name": f"d{i}", "site": {"id": site_id}, "device_type": {"id": 1}, "role": {"id": 1},
                "status": {"value": "active"}, "asset_tag": tag, "last_updated": TS}

    def st(i, name):
        return {"id": i, "name": name, "slug": f"s{i}", "status": {"value": "active"}, "last_updated": TS}

    client = _GatherClient([dev(2, 1, None), dev(40, 3, "AT-1")], [st(1, "North"), st(3, "West")])
    s = asyncio.run(gather(client, ops(update("device", 2, asset_tag="AT-1"), update("site", 1, name="West"))))
    assert ("dcim/devices", {"asset_tag": "AT-1"}) in client.queries and ("dcim/sites", {"name": "West"}) in client.queries
    assert {r.id for r in s.of("device")} >= {2, 40} and {r.id for r in s.of("site")} >= {1, 3}
    out = plan(ops(update("device", 2, asset_tag="AT-1"), update("site", 1, name="West")), s)
    assert [p.status for p in out] == ["error", "error"]
