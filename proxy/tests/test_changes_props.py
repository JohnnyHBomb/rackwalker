"""Property tests of the change planner (hypothesis).

The planner decides U-slot validity with interval arithmetic. The oracle below is written differently on purpose:
it paints half-U cells per rack face on a grid and replays the same op sequence, so a disagreement between the two
on any generated sequence is a bug in one of them.
"""
from __future__ import annotations

import copy

from hypothesis import given, settings
from hypothesis import strategies as st

from rackwalker_proxy.changes import plan, result

from .conftest import schema_validator
from .plan_helpers import device, ops, place, rack, site, state, unplace, location

HEIGHTS = [0, 0.5, 1, 1, 2, 4]


@st.composite
def worlds(draw):
    racks = [rack(r, site_id=draw(st.sampled_from([1, 1, 2])), location_id=None, u=draw(st.integers(1, 12)),
                  start=draw(st.integers(1, 3))) for r in (1, 2, 3)]
    devices = [device(d, site_id=draw(st.sampled_from([1, 1, 2])), location_id=None, h=draw(st.sampled_from(HEIGHTS)),
                      full=draw(st.booleans())) for d in range(10, 16)]
    seq = []
    for _ in range(draw(st.integers(1, 25))):
        d = draw(st.integers(10, 15))
        if draw(st.integers(0, 4)) == 0:
            seq.append(unplace(d))
        else:
            seq.append(place(d, draw(st.integers(1, 3)), draw(st.integers(1, 30)) / 2, draw(st.sampled_from(["front", "rear"]))))
    return racks, devices, seq


def oracle(racks, devices, seq):
    """Expected status per op: True = accepted (ok/no_change), False = refused."""
    rk = {r.id: r.v for r in racks}
    dv = {d.id: dict(d.v) for d in devices}
    cells: dict[int, set[tuple[int, str, int]]] = {d: set() for d in dv}  # device -> {(rack, face, half-U index)}
    verdicts = []
    for op in seq:
        d = op["target"]["id"]
        if op["op"] == "unplace_device":
            cells[d] = set()
            verdicts.append(True)
            continue
        f = op["fields"]
        r, pos, face = f["rack_id"], f["position"], f["face"]
        h = dv[d]["_u_height"]
        lo, hi = 2 * rk[r]["starting_unit"], 2 * (rk[r]["starting_unit"] + rk[r]["u_height"])
        mine = {(r, fc, k) for fc in (("front", "rear") if dv[d]["_full_depth"] else (face,))
                for k in range(int(2 * pos), int(2 * (pos + h)))}
        ok = (h > 0 and rk[r]["site_id"] == dv[d]["site_id"] and 2 * pos >= lo and 2 * (pos + h) <= hi
              and not any(mine & cs for o, cs in cells.items() if o != d))
        if ok:
            cells[d] = mine
        verdicts.append(ok)
    return verdicts


@settings(max_examples=400, deadline=None)
@given(worlds())
def test_planner_agrees_with_grid_oracle(w):
    racks, devices, seq = w
    st0 = state(site(1), site(2), location(1), *racks, *devices)
    before = copy.deepcopy(st0)
    out = plan(ops(*seq), st0)
    assert st0 == before
    assert [p.status in ("ok", "no_change") for p in out] == oracle(racks, devices, seq)


@settings(max_examples=200, deadline=None)
@given(worlds())
def test_result_shape_invariants(w):
    racks, devices, seq = w
    out = plan(ops(*seq), state(site(1), site(2), *racks, *devices))
    res = result("preview", out, False).model_dump(mode="json", exclude_unset=True)
    schema_validator("ChangeResult").validate(res)
    assert [o["index"] for o in res["ops"]] == list(range(len(seq)))
    for o, req in zip(res["ops"], seq):
        assert o["target"] == req["target"] and o["op"] == req["op"]
        if o["status"] == "error":
            assert o["error"]["code"] in ("conflict", "validation_failed") and o["diff"] == []
            assert o["before"] is None and o["after"] is None
        else:
            assert set(o["before"]) == set(o["after"])
            assert o["diff"] == [{"field": k, "before": o["before"][k], "after": v} for k, v in o["after"].items()
                                 if o["before"][k] != v]
            assert (o["status"] == "no_change") == (o["diff"] == [])


@settings(max_examples=200, deadline=None)
@given(worlds(), st.data())
def test_accepted_ops_never_produce_an_overlap(w, data):
    """Whatever the order, the end state of the accepted ops has no two devices in the same half-U cell of a face."""
    racks, devices, seq = w
    seq = data.draw(st.permutations(seq))
    pl_state = state(site(1), site(2), *racks, *devices)
    from rackwalker_proxy.changes import Planner
    pl = Planner(pl_state)
    for p, op in zip(plan(ops(*seq), pl_state), seq):
        if p.status == "ok":
            pl.st.recs[("device", op["target"]["id"])].v.update(p.write)
    seen: dict[tuple, int] = {}
    for d in pl.st.of("device"):
        v = d.v
        if v["rack_id"] is None or v["position"] is None:
            continue
        r = pl.st.recs[("rack", v["rack_id"])].v
        assert r["starting_unit"] <= v["position"] and v["position"] + v["_u_height"] <= r["starting_unit"] + r["u_height"]
        for fc in (("front", "rear") if v["_full_depth"] else (v["face"],)):
            for k in range(int(2 * v["position"]), int(2 * (v["position"] + v["_u_height"]))):
                assert (v["rack_id"], fc, k) not in seen, f"device {d.id} overlaps device {seen[(v['rack_id'], fc, k)]}"
                seen[(v["rack_id"], fc, k)] = d.id
