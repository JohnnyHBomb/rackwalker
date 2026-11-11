#!/usr/bin/env python3
"""Build the compact, searchable index of a device pack from a checkout of the device-type library.

    python packs/tools/build_device_index.py packs/devices/community-library.json /path/to/devicetype-library

The checkout must be at the commit the pack names (`library.commit`); the script refuses anything else. It writes the
index next to the pack (`index.file`, gzip, byte-for-byte reproducible) and updates the pack's `index` block (counts,
size, SHA-256). Nothing here runs at play time or in the tests: the index ships with the game.

One index row per device type or module type: manufacturer, model, part number, height, how many interfaces, console,
power and module-bay templates it has, the file it came from and that file's SHA-256. The game fetches the file itself
only when a player imports the type, and checks it against that SHA-256.
"""
from __future__ import annotations

import gzip
import hashlib
import json
import subprocess
import sys
from pathlib import Path

import yaml

DEVICE_COLUMNS = ["manufacturer", "model", "part_number", "slug", "u_height", "interfaces", "console_ports", "power_ports", "power_outlets",
                  "front_ports", "rear_ports", "module_bays", "device_bays", "file", "sha256"]
MODULE_COLUMNS = ["manufacturer", "model", "part_number", "interfaces", "console_ports", "power_ports", "front_ports", "rear_ports",
                  "module_bays", "file", "sha256"]


def count(doc: dict, *keys: str) -> int:
    return sum(len(doc.get(k) or []) for k in keys)


def main(pack_path: str, checkout: str) -> None:
    pack_file, lib = Path(pack_path), Path(checkout)
    pack = json.loads(pack_file.read_text())
    head = subprocess.run(["git", "-C", str(lib), "rev-parse", "HEAD"], capture_output=True, text=True, check=True).stdout.strip()
    if head != pack["library"]["commit"]:
        sys.exit(f"the checkout is at {head}, the pack pins {pack['library']['commit']}: check out the pinned commit, or change the pin on purpose")
    wanted = pack["manufacturers"]
    makers: list[str] = []
    rows: dict[str, list[list]] = {"device-types": [], "module-types": []}
    for folder in rows:
        for path in sorted((lib / folder).glob("*/*.y*ml"), key=lambda p: (p.parent.name.lower(), p.name.lower())):
            raw = path.read_bytes()
            doc = yaml.safe_load(raw)
            maker = str(doc["manufacturer"])
            if wanted != "*" and maker not in wanted:
                continue
            if maker not in makers:
                makers.append(maker)
            rel, digest = f"{path.parent.name}/{path.name}", hashlib.sha256(raw).hexdigest()
            part = str(doc.get("part_number") or "")
            if folder == "device-types":
                height = doc.get("u_height", 1)
                rows[folder].append([makers.index(maker), str(doc["model"]), part, str(doc.get("slug") or ""), float(height) if height != int(height) else int(height),
                                     count(doc, "interfaces"), count(doc, "console-ports", "console-server-ports"), count(doc, "power-ports"),
                                     count(doc, "power-outlets"), count(doc, "front-ports"), count(doc, "rear-ports"), count(doc, "module-bays"),
                                     count(doc, "device-bays"), rel, digest])
            else:
                rows[folder].append([makers.index(maker), str(doc["model"]), part, count(doc, "interfaces"),
                                     count(doc, "console-ports", "console-server-ports"), count(doc, "power-ports"), count(doc, "front-ports"),
                                     count(doc, "rear-ports"), count(doc, "module-bays"), rel, digest])
    index = {"pack": "device-index", "of": pack["id"], "commit": head, "manufacturers": makers,
             "device_columns": DEVICE_COLUMNS, "device_types": rows["device-types"], "module_columns": MODULE_COLUMNS, "module_types": rows["module-types"]}
    body = json.dumps(index, ensure_ascii=False, separators=(",", ":")).encode()
    out = pack_file.parent / pack["index"]["file"]
    with open(out, "wb") as fh, gzip.GzipFile(fileobj=fh, mode="wb", mtime=0, compresslevel=9, filename="") as gz:
        gz.write(body)
    pack["index"].update(sha256=hashlib.sha256(out.read_bytes()).hexdigest(), bytes=out.stat().st_size, manufacturers=len(makers),
                         device_types=len(rows["device-types"]), module_types=len(rows["module-types"]))
    pack_file.write_text(json.dumps(pack, indent=1, ensure_ascii=False) + "\n")
    print(f"{out}: {len(makers)} manufacturers, {len(rows['device-types'])} device types, {len(rows['module-types'])} module types, {out.stat().st_size} bytes")


if __name__ == "__main__":
    if len(sys.argv) != 3:
        sys.exit(__doc__)
    main(sys.argv[1], sys.argv[2])
