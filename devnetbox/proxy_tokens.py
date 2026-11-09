#!/usr/bin/env python3
"""Mint the proxy's own NetBox tokens on a dev stack, so the proxy never runs on the admin superuser token.

    NETBOX_URL=... NETBOX_TOKEN=<admin> SECRETS=devnetbox/.stacks/<project>.env python devnetbox/proxy_tokens.py ro|rw

Ensures (idempotently, with the admin token):
  * user `rackwalker-proxy` (not staff, not superuser) and two ObjectPermissions on it:
      - `rackwalker-proxy view`:  view on exactly the object types the proxy reads (netbox.py / scene.py / changes.py);
      - `rackwalker-proxy write`: change on the types update_fields/move/place may write, add on dcim.rack (create_rack);
  * two v2 tokens for that user, each expiring after TOKEN_DAYS:
      - ro: write_enabled=false  -> the dry-run default (.env with WRITE_ENABLED=0): NetBox itself refuses writes;
      - rw: write_enabled=true   -> only for WRITE_ENABLED=1.
The plaintext of a v2 token is shown by NetBox once, at creation; it is kept in the stack's SECRETS file (mode 600,
gitignored) as PROXY_RO_TOKEN / PROXY_RW_TOKEN and re-minted when it no longer authenticates (expired, deleted,
stack reset). Prints the requested token on stdout and nothing else.
"""
from __future__ import annotations

import json
import os
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

USER = "rackwalker-proxy"
TOKEN_DAYS = 30
VIEW = ["dcim.site", "dcim.location", "dcim.rack", "dcim.device", "dcim.devicetype", "dcim.devicerole", "dcim.cable",
        "dcim.interface", "dcim.frontport", "dcim.rearport", "dcim.consoleport", "dcim.consoleserverport",
        "dcim.powerport", "dcim.poweroutlet", "dcim.powerfeed", "circuits.circuit", "circuits.circuittermination",
        "users.user"]  # users: the game rechecks that a signed-in player's NetBox user still exists and is active
CHANGE = ["dcim.site", "dcim.location", "dcim.rack", "dcim.device", "dcim.cable", "dcim.interface"]  # changes.ENDPOINT
PERMS = [("rackwalker-proxy view", VIEW, ["view"]), ("rackwalker-proxy write", CHANGE, ["change"]),
         ("rackwalker-proxy add rack", ["dcim.rack"], ["add"])]


def main() -> None:
    which = sys.argv[1] if len(sys.argv) > 1 else ""
    if which not in ("ro", "rw"):
        raise SystemExit("usage: proxy_tokens.py ro|rw")
    url, admin, secrets = os.environ["NETBOX_URL"].rstrip("/"), os.environ["NETBOX_TOKEN"], Path(os.environ["SECRETS"])
    var = f"PROXY_{which.upper()}_TOKEN"
    stored = dict(re.findall(r"^(PROXY_R[OW]_TOKEN)=(\S+)$", secrets.read_text(), re.M)).get(var)
    if stored and works(url, stored):
        print(stored)
        return
    s = requests.Session()
    s.headers.update({"Authorization": f"Bearer {admin}", "Content-Type": "application/json", "Accept": "application/json"})

    def req(method: str, path: str, body: dict | None = None, **params) -> dict:
        r = s.request(method, f"{url}/api/{path}", params=params or None, data=json.dumps(body) if body else None, timeout=30)
        if r.status_code >= 400:
            raise SystemExit(f"proxy_tokens: {method} {path} -> HTTP {r.status_code}: {r.text[:300]}")
        return r.json() if r.content else {}

    users = req("GET", "users/users/", username=USER)["results"]
    uid = users[0]["id"] if users else req("POST", "users/users/", {"username": USER, "password": "Nf-" + os.urandom(24).hex() + "X",
                                                                   "is_active": True})["id"]
    for name, types, actions in PERMS:
        want = {"name": name, "enabled": True, "object_types": types, "actions": actions, "users": [uid]}
        found = req("GET", "users/permissions/", name=name)["results"]
        if found:
            req("PATCH", f"users/permissions/{found[0]['id']}/", want)
        else:
            req("POST", "users/permissions/", want)
    desc = f"rackwalker proxy ({which})"
    for old in req("GET", "users/tokens/", user_id=uid)["results"]:  # one live token per kind
        if old.get("description") == desc:
            req("DELETE", f"users/tokens/{old['id']}/")
    expires = (datetime.now(timezone.utc) + timedelta(days=TOKEN_DAYS)).isoformat()
    t = req("POST", "users/tokens/", {"user": uid, "version": 2, "write_enabled": which == "rw", "expires": expires,
                                      "description": desc})
    plain = t["token"] if t["token"].startswith("nbt_") else f"nbt_{t['key']}.{t['token']}"
    if not works(url, plain):
        raise SystemExit("proxy_tokens: the new token does not authenticate")
    text = re.sub(rf"^{var}=.*\n?", "", secrets.read_text(), flags=re.M)
    secrets.write_text(text.rstrip("\n") + f"\n{var}={plain}\n")
    os.chmod(secrets, 0o600)
    print(plain)


def works(url: str, token: str) -> bool:
    r = requests.get(f"{url}/api/dcim/sites/", params={"limit": 1}, headers={"Authorization": f"Bearer {token}"}, timeout=30)
    return r.status_code == 200


if __name__ == "__main__":
    main()
