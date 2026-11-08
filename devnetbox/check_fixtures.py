#!/usr/bin/env python3
"""Check that the contract fixtures use the live dev NetBox's ids (`make netbox-check-fixtures`).

fixtures/make_fixtures.py numbers every object offline, in seed-model order. That matches NetBox only on a stack
seeded once from empty (`make netbox-reset`); this script proves it: every site, location, rack, device, port
(each kind), circuit termination and cable id, each device's port order within a kind (DeviceDetail.interfaces),
and every trace fixture's cable sequence and far end, are compared with the live NetBox. Read-only. Exit 1 and a
list of mismatches if anything differs.

    NETBOX_URL=... NETBOX_TOKEN=... python devnetbox/check_fixtures.py
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent / "fixtures"))
import make_fixtures as mf  # noqa: E402
import seed as sd  # noqa: E402
import seed_model as sm  # noqa: E402

PORT_EP = {**{k: ep for k, (ep, _) in sd.KIND_ENDPOINT.items()}, "circuit_termination": "circuits/circuit-terminations"}


def main() -> int:
    url, token = os.environ.get("NETBOX_URL"), os.environ.get("NETBOX_TOKEN")
    if not url or not token:
        raise SystemExit("check_fixtures: set NETBOX_URL and NETBOX_TOKEN")
    nb = sd.NB(url, token)
    w = mf.World(sm.build())
    bad: list[str] = []
    checked = 0

    def cmp(what: str, want: dict, live: dict) -> None:
        nonlocal checked
        for key, wid in want.items():
            checked += 1
            if live.get(key) != wid:
                bad.append(f"{what} {key}: fixture id {wid}, live id {live.get(key)}")

    cmp("site", w.site_id, {o["slug"]: o["id"] for o in nb.list("dcim/sites")})
    cmp("location", w.loc_id, {o["slug"]: o["id"] for o in nb.list("dcim/locations")})
    cmp("rack", w.rack_id, {o["name"]: o["id"] for o in nb.list("dcim/racks")})
    cmp("device", w.dev_id, {o["name"]: o["id"] for o in nb.list("dcim/devices")})
    for kind, ep in sd.KIND_ENDPOINT.items():
        want = {(d, n): p["id"] for (k, d, n), p in w.ports.items() if k == kind}
        live = nb.list(ep[0])
        cmp(kind, want, {(o["device"]["name"], o["name"]): o["id"] for o in live})
        # DeviceDetail order within a kind = NetBox's list order; the fixtures reproduce it with mf.natural.
        per_dev: dict[str, list[str]] = {}
        for o in live:
            per_dev.setdefault(o["device"]["name"], []).append(o["name"])
        for dev, names in per_dev.items():
            checked += 1
            if names != sorted(names, key=mf.natural):
                bad.append(f"{kind} order on {dev}: live {names[:6]}..., fixture {sorted(names, key=mf.natural)[:6]}...")
    cmp("circuit_termination", {k: p["id"] for k, p in w.cterm.items()},
        {(o["circuit"]["cid"], o["term_side"]): o["id"] for o in nb.list("circuits/circuit-terminations")})
    cmp("cable", {c["spec"].label: c["id"] for c in w.cables}, {o["label"]: o["id"] for o in nb.list("dcim/cables")})

    # Trace fixtures: same cables in the same order, and the same far end, as NetBox's own /trace/.
    index = json.loads((HERE.parent / "fixtures" / "index.json").read_text())["fixtures"]
    for f in index:
        if not f["path"].startswith("/api/trace/") or f["status"] != 200:
            continue
        _, _, _, kind, pid = f["path"].split("/")
        tr = json.loads((HERE.parent / "fixtures" / f["file"]).read_text())["trace"]
        live = nb.req("GET", f"{PORT_EP[kind]}/{pid}/trace/")
        want_cables = [h["cable"]["id"] for h in tr["hops"] if h["cable"]]
        live_cables = [seg[1]["id"] for seg in live if seg[1]]
        want_end = tr["hops"][-1]["far_end"][0]["id"] if tr["hops"] else None
        live_end = live[-1][2][0]["id"] if live and live[-1][2] else None
        checked += 1
        if (want_cables, want_end) != (live_cables, live_end):
            bad.append(f"{f['path']}: fixture cables {want_cables} end {want_end}, live cables {live_cables} end {live_end}")

    print(json.dumps({"checked": checked, "mismatches": len(bad)}))
    for b in bad[:40]:
        print("  " + b)
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
