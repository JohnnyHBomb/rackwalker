"""A made-up NetBox that starts empty and takes writes: for the setup tests, and (served over HTTP) for the browser
tests of the setup lines, which need a NetBox with nothing in it and must run without Docker and without a network.

    python -m tests.empty_netbox 8743        (from proxy/)

It keeps what it is sent, hands a new device the components of its type's templates, and answers the list filters the
proxy uses. One player exists: user 9, a superuser, whatever token is presented. It is not NetBox: only what the
proxy asks of NetBox is here.
"""
from __future__ import annotations

import json
import sys
from collections import defaultdict
from typing import Any

import httpx

from rackwalker_proxy import setupops

from .conftest import token_record

FK = {"site": "dcim/sites", "region": "dcim/regions", "rack": "dcim/racks", "location": "dcim/locations", "role": "dcim/device-roles",
      "device_type": "dcim/device-types", "module_type": "dcim/module-types", "manufacturer": "dcim/manufacturers", "provider": "circuits/providers",
      "type": "circuits/circuit-types", "circuit": "circuits/circuits", "rir": "ipam/rirs", "device": "dcim/devices", "module_bay": "dcim/module-bays",
      "power_port": None}
CHOICES = ("status", "face", "type", "feed_leg", "poe_mode", "poe_type")
TEMPLATES = {tpl: path for _, tpl, path, _ in setupops.COMPONENTS}


class EmptyBox:
    """NetBox with nothing in it and one player (user 9, a superuser). It keeps what it is sent, hands a new device the
    components of its type's templates, and answers the filters the proxy uses."""

    def __init__(self, data: dict[str, list[dict[str, Any]]] | None = None):
        self.data: dict[str, list[dict[str, Any]]] = defaultdict(list, data or {})
        self.writes: list[tuple[str, str, Any]] = []
        self.calls: list[tuple[str, str]] = []
        self.ticks = 0
        self.superuser = True
        self.refuse: tuple[str, ...] = ()

    def stamp(self) -> str:
        self.ticks += 1
        return f"2026-11-11T12:{self.ticks // 60:02d}:{self.ticks % 60:02d}.000000Z"

    def row(self, path: str, oid: Any) -> dict[str, Any] | None:
        return next((r for r in self.data[path] if r["id"] == oid), None)

    def nested(self, field: str, value: Any) -> Any:
        if field in ("asns", "tags") and isinstance(value, list):
            table = "ipam/asns" if field == "asns" else "extras/tags"
            return [{k: v for k, v in (self.row(table, i) or {"id": i}).items() if k in ("id", "asn", "slug", "name")} for i in value]
        if field in FK and isinstance(value, int) and FK[field]:
            ref = self.row(FK[field], value) or {}
            return {"id": value, **{k: ref[k] for k in ("name", "slug", "model", "cid") if k in ref}}
        if field in CHOICES and isinstance(value, str):
            return {"value": value, "label": value}
        return value

    def site_of(self, path: str, row: dict[str, Any]) -> str | None:
        if path == "dcim/sites":
            return str(row["id"])
        if isinstance(row.get("site"), dict):
            return str(row["site"]["id"])
        if isinstance(row.get("device"), dict):
            dev = self.row("dcim/devices", row["device"]["id"])
            return str(dev["site"]["id"]) if dev else None
        return None

    def matches(self, path: str, row: dict[str, Any], key: str, values: list[str]) -> bool:
        if key == "site_id":
            if path == "dcim/cables":
                ends = [t for side in ("a_terminations", "b_terminations") for t in row[side] if t["object_type"] == "dcim.interface"]
                return any(self.site_of("dcim/interfaces", self.row("dcim/interfaces", t["object_id"]) or {}) in values for t in ends)
            return self.site_of(path, row) in values
        if key == "id":
            return str(row["id"]) in values
        fold = key.endswith("__ie")
        name = key.removesuffix("__ie")
        got = row.get(name[:-3]) if name.endswith("_id") and name[:-3] in row else row.get(name)
        if isinstance(got, dict):
            got = got.get("id", got.get("value"))
        if isinstance(got, list):
            return any(str(x.get(name, x) if isinstance(x, dict) else x) in values for x in got)
        return (str(got).lower() in [v.lower() for v in values]) if fold else (str(got) in values)

    def __call__(self, req: httpx.Request) -> httpx.Response:
        full = req.url.path
        path = full.removeprefix("/api/").strip("/")
        self.calls.append((req.method, path))
        if token_record(req) is not None:
            return token_record(req)
        if path == "status":
            return httpx.Response(200, json={"netbox-version": "4.7.4"})
        if path == "authentication-check":
            return httpx.Response(200, json={"id": 9, "username": "walker", "first_name": "", "last_name": ""})
        if path == "users/users/9":
            return httpx.Response(200, json={"id": 9, "username": "walker", "is_active": True, "is_superuser": self.superuser, "groups": []})
        if path == "users/users":
            return httpx.Response(200, json={"count": 1 if self.superuser else 0, "results": [{"id": 9}] if self.superuser else []})
        if path == "users/permissions":
            return httpx.Response(200, json={"count": 0, "results": []})
        if path.endswith("/trace"):
            return httpx.Response(200, json=[])
        if req.method == "GET":
            if path in self.refuse:
                return httpx.Response(403, json={"detail": "You do not have permission to perform this action."})
            q = req.url.params
            rows = self.data[path]
            for key in {k for k in q.keys() if k not in ("limit", "offset", "fields", "brief")}:
                rows = [r for r in rows if self.matches(path, r, key, q.get_list(key))]
            offset, limit = int(q.get("offset", 0)), int(q.get("limit", 50))
            return httpx.Response(200, json={"count": len(rows), "results": rows[offset:offset + limit]})
        body = json.loads(req.content) if req.content else {}
        self.writes.append((req.method, path, body))
        if req.method == "DELETE":
            table, oid = path.rsplit("/", 1)
            self.data[table] = [r for r in self.data[table] if r["id"] != int(oid)]
            return httpx.Response(204)
        if req.method == "PATCH":
            table, oid = path.rsplit("/", 1)
            row = self.row(table, int(oid))
            if row is None:
                return httpx.Response(404, json={"detail": "Not found."})
            fields = {k: self.nested(k, v) for k, v in body.items() if k != "changelog_message"}
            row["custom_fields"] = {**row.get("custom_fields", {}), **fields.pop("custom_fields", {})}
            row.update(fields, last_updated=self.stamp())
            return httpx.Response(200, json=row)
        made = [self.create(path, b) for b in (body if isinstance(body, list) else [body])]
        bad = next((m for m in made if "id" not in m), None)
        if bad is not None:
            return httpx.Response(400, json=bad)
        return httpx.Response(201, json=made if isinstance(body, list) else made[0])

    def create(self, path: str, body: dict[str, Any]) -> dict[str, Any]:
        unique = {"dcim/sites": ("name",), "dcim/regions": ("slug",), "circuits/providers": ("name",), "ipam/asns": ("asn",), "extras/tags": ("slug",),
                  "dcim/manufacturers": ("slug",), "dcim/device-roles": ("slug",), "circuits/circuit-types": ("slug",)}.get(path, ())
        for field in unique:
            if any(str(r.get(field)).lower() == str(body.get(field)).lower() for r in self.data[path]):
                return {field: [f"{path} with this {field} already exists."]}
        if path == "ipam/asns" and body.get("rir") is None:
            return {"rir": ["This field cannot be null."]}
        new = {"id": 1 + max((r["id"] for rows in self.data.values() for r in rows), default=0),
               **{k: self.nested(k, v) for k, v in body.items() if k != "changelog_message"}, "last_updated": self.stamp()}
        new.setdefault("custom_fields", {})
        new.setdefault("tags", [])
        if path == "dcim/sites":
            new = {"latitude": None, "longitude": None, "facility": "", "physical_address": "", "region": None, "description": "", **new}
        elif path == "dcim/racks":
            new = {"location": None, "starting_unit": 1, "u_height": 42, **new}
        elif path == "circuits/providers":
            new.setdefault("asns", [])
        elif path == "dcim/device-types":
            new = {"part_number": "", "is_full_depth": True, "u_height": 1, **new}
        elif path in TEMPLATES or path in TEMPLATES.values():
            new.setdefault("cable", None)
            if path == "dcim/module-bays":
                new.setdefault("installed_module", None)
        elif path == "dcim/modules":
            bay = self.row("dcim/module-bays", body["module_bay"])
            if bay is None or bay["installed_module"] is not None:
                return {"module_bay": ["That bay is taken."]}
            bay["installed_module"] = {"id": new["id"]}
        elif path == "circuits/circuits":
            new.update(status={"value": "active"}, provider_account=None, install_date=None, commit_rate=None)
        elif path == "circuits/circuit-terminations":
            new["cable"] = None
        elif path == "dcim/cables":
            new.update(label="", status={"value": "connected"})
            for t in body["a_terminations"] + body["b_terminations"]:
                table = "dcim/interfaces" if t["object_type"] == "dcim.interface" else "circuits/circuit-terminations"
                self.row(table, t["object_id"])["cable"] = {"id": new["id"]}
        elif path == "dcim/devices":
            if any(d["site"]["id"] == body["site"] and (d["name"] or "").lower() == body["name"].lower() for d in self.data[path]):
                return {"name": ["A device with this name already exists at this site."]}
            new = {"location": None, "rack": None, "position": None, "face": None, "serial": "", "asset_tag": None, "platform": None, "primary_ip": None,
                   "virtual_chassis": None, **new}
            self.data[path].append(new)
            for tpl, comp in TEMPLATES.items():
                for t in [t for t in self.data[tpl] if (t.get("device_type") or {}).get("id") == body["device_type"]]:
                    self.create(comp, {k: (v["value"] if isinstance(v, dict) and "value" in v else v) for k, v in t.items()
                                       if k in ("name", "type", "mgmt_only", "enabled", "positions", "position", "label")} | {"device": new["id"]})
            return new
        if path == "dcim/interfaces":
            new = {"enabled": True, "mgmt_only": False, "description": "", "mark_connected": False, **new}
        self.data[path].append(new)
        return new


def asgi(box: EmptyBox) -> Any:
    """The box as an ASGI app, so a real proxy can be pointed at it."""
    async def app(scope: dict[str, Any], receive: Any, send: Any) -> None:
        if scope["type"] != "http":
            return
        body = b""
        while True:
            msg = await receive()
            body += msg.get("body", b"")
            if not msg.get("more_body"):
                break
        query = scope.get("query_string", b"").decode()
        req = httpx.Request(scope["method"], f"http://netbox.invalid{scope['path']}" + (f"?{query}" if query else ""), content=body)
        res = box(req)
        await send({"type": "http.response.start", "status": res.status_code, "headers": [(b"content-type", b"application/json")]})
        await send({"type": "http.response.body", "body": res.content})
    return app


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(asgi(EmptyBox()), host="127.0.0.1", port=int(sys.argv[1]) if len(sys.argv) > 1 else 8743, log_level="warning")
