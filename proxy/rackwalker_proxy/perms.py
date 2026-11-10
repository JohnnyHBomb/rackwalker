"""May this player make this change in NetBox themselves?

The proxy writes with its own NetBox token, so NetBox would let any signed-in player change whatever the proxy may.
This module closes that gap: before a write, the proxy reads the player's own NetBox object permissions and refuses
the write unless they cover every object type and action the change set needs. The proxy's token never widens what a
player may change.

Mechanism (why this one): the player's token is used once at sign-in and forgotten, so the proxy cannot simply make
the write as the player. NetBox's REST API does list object permissions (`/api/users/permissions/`, filtered by user or
group; a user's groups are on `/api/users/users/<id>/`), which is what NetBox itself evaluates. The proxy reads them
with its own token (it needs view on users, groups and object permissions) and applies NetBox's own rule:

  * a superuser may do everything (the API lists superusers under `?is_superuser=true`);
  * otherwise an action on an object type is allowed when an ENABLED permission that names the type and the action is
    assigned to the user or to one of the user's groups;
  * a permission with CONSTRAINTS covers only some objects. The proxy cannot evaluate a constraint (it is a database
    query on NetBox's side), so a constrained permission never counts here: the player is told, and can make that
    change in NetBox itself. Refusing is the safe side.

The answer is kept per user for a short time for the quest log only; every write reads it from NetBox again.
"""
from __future__ import annotations

import time
from typing import Any

from . import netbox as nb
from .errors import ApiError

# What each change op needs: (NetBox object type, action).
NEEDS: dict[str, list[tuple[str, str]]] = {
    "move_rack": [("dcim.rack", "change")],
    "place_device": [("dcim.device", "change")],
    "unplace_device": [("dcim.device", "change")],
    "create_rack": [("dcim.rack", "add")],
    "connect_cable": [("dcim.cable", "add")],
    "create_circuit": [("circuits.circuit", "add")],
    "create_circuit_termination": [("circuits.circuittermination", "add")],
    "assign_ip": [("ipam.ipaddress", "add"), ("dcim.device", "change")],
    "create_prefix": [("ipam.prefix", "add")],
    "set_management": [("dcim.device", "change")],
    "record_circuit": [("circuits.circuit", "change")],
    "create_provider_account": [("circuits.provideraccount", "add")],
    "create_provider_network": [("circuits.providernetwork", "add")],
    "create_contact": [("tenancy.contact", "add")],
    "assign_contact": [("tenancy.contactassignment", "add")],
}
UPDATE_TYPE = {"site": "dcim.site", "location": "dcim.location", "rack": "dcim.rack", "device": "dcim.device", "cable": "dcim.cable",
               "interface": "dcim.interface"}
TTL = 30.0


def needs_of(ops: list[dict[str, Any]]) -> list[tuple[str, str]]:
    """Every (object type, action) a list of change ops (as dicts) needs, in a fixed order, each once."""
    out: list[tuple[str, str]] = []
    for op in ops:
        need = [(UPDATE_TYPE[op["target"]["kind"]], "change")] if op["op"] == "update_fields" else NEEDS.get(op["op"], [("unknown", "change")])
        if op["op"] == "set_management" and "model_default" in (op.get("fields") or {}):
            need = need + [("dcim.devicetype", "change")]   # the model's default lives on the device type
        for n in need:
            if n not in out:
                out.append(n)
    return out


class Permissions:
    """What a NetBox user may do, as far as unconstrained object permissions say."""

    def __init__(self, superuser: bool, grants: set[tuple[str, str]], constrained: set[tuple[str, str]]):
        self.superuser, self.grants, self.constrained = superuser, grants, constrained

    def missing(self, needs: list[tuple[str, str]]) -> list[dict[str, Any]]:
        if self.superuser:
            return []
        return [{"object_type": t, "action": a, "constrained": (t, a) in self.constrained} for t, a in needs if (t, a) not in self.grants]


class PermissionReader:
    def __init__(self, client: nb.NetBox, ttl: float = TTL):
        self.client, self.ttl = client, ttl
        self._cache: dict[int, tuple[float, Permissions]] = {}

    def clear(self) -> None:
        self._cache.clear()

    async def of(self, user_id: int, fresh: bool = False) -> Permissions:
        """`fresh`: ask NetBox now (every write does: a permission taken away is gone at the next write, not 30 s on)."""
        hit = self._cache.get(user_id)
        if not fresh and hit and time.monotonic() - hit[0] < self.ttl:
            return hit[1]
        try:
            user = await self.client.request("GET", f"users/users/{user_id}/", not_found={"kind": "user", "id": user_id})
            sup = await self.client.request("GET", "users/users/", params={"id": user_id, "is_superuser": "true", "brief": "1", "limit": 1})
        except ApiError as e:
            raise _blocked(e, "proxy_cannot_view_users", "users") from None
        if not user.get("is_active", True):
            perms = Permissions(False, set(), set())
        elif sup.get("count"):
            perms = Permissions(True, set(), set())
        else:
            try:
                rows = await self.client.list_raw("users/permissions", {"user_id": user_id, "enabled": "true"})
                for g in user.get("groups") or []:
                    rows += await self.client.list_raw("users/permissions", {"group_id": g["id"], "enabled": "true"})
            except ApiError as e:
                raise _blocked(e, "proxy_cannot_view_permissions", "object permissions") from None
            grants: set[tuple[str, str]] = set()
            constrained: set[tuple[str, str]] = set()
            for r in rows:
                if not r.get("enabled", True):
                    continue  # asked of NetBox with enabled=true as well; never trust the filter alone
                pairs = {(t, a) for t in r.get("object_types") or [] for a in r.get("actions") or []}
                (constrained if r.get("constraints") else grants).update(pairs)
            perms = Permissions(False, grants, constrained - grants)
        self._cache[user_id] = (time.monotonic(), perms)
        return perms


NOT_WRITTEN = "Nothing was written: "


def _blocked(e: ApiError, reason: str, what: str) -> ApiError:
    if e.code == "netbox_error" and (e.details or {}).get("status") in (401, 403):
        return ApiError("netbox_error", f"{NOT_WRITTEN}RackWalker cannot check what you may change, because NetBox refuses the "
                        f"proxy's own token the view of {what}. Whoever runs RackWalker adds that view permission to the proxy's NetBox user.",
                        {"reason": reason})
    return e


def why_not(missing: list[dict[str, Any]]) -> str:
    """In a sentence: what the player's NetBox user may not do, and what to do about it."""
    words = ", ".join(f"{m['action']} {m['object_type']}" for m in missing)
    extra = (" A permission with constraints does not count here: the game cannot evaluate constraints, so make that change in NetBox itself."
             if any(m["constrained"] for m in missing) else "")
    return (f"Your NetBox user may not {words}. Ask a NetBox admin for that permission; until then you can still play in practice "
            f"mode.{extra}")


def refusal(missing: list[dict[str, Any]]) -> ApiError:
    """The 403 a player gets for a change their own NetBox permissions do not cover."""
    text = why_not(missing)
    return ApiError("forbidden", NOT_WRITTEN + text[0].lower() + text[1:], {"reason": "player_lacks_permission", "missing": missing})


# Every (object type, action) a quest line may need for its change, with the words the quest log uses for it.
QUEST_WORDS = {("dcim.cable", "add"): "add cables", ("circuits.circuit", "add"): "add circuits",
               ("circuits.circuittermination", "add"): "add circuit ends", ("ipam.ipaddress", "add"): "add addresses",
               ("ipam.prefix", "add"): "add prefixes", ("dcim.device", "change"): "change devices", ("dcim.rack", "change"): "change racks",
               ("dcim.site", "change"): "change sites", ("circuits.circuit", "change"): "change circuits",
               ("circuits.provideraccount", "add"): "add provider accounts", ("circuits.providernetwork", "add"): "add provider networks",
               ("tenancy.contact", "add"): "add contacts", ("tenancy.contactassignment", "add"): "assign contacts",
               ("dcim.devicetype", "change"): "change device types"}
QUEST_NEEDS = list(QUEST_WORDS)


def some_missing(missing: list[dict[str, Any]]) -> str:
    """For the quest log of a player who may write some of what quests write, and not the rest."""
    words = [QUEST_WORDS[(m["object_type"], m["action"])] for m in missing]
    listed = ", ".join(words[:-1]) + (" or " if len(words) > 1 else "") + words[-1]
    extra = " A permission with constraints does not count here." if any(m["constrained"] for m in missing) else ""
    return (f"Your NetBox user may not {listed}. Lines that need that run as practice, with no XP; ask a NetBox admin for the "
            f"permission.{extra}")


async def check_health(client: nb.NetBox) -> str:
    """HealthNetbox.permission_check: can the proxy's token read users and object permissions?"""
    for path, bad in (("users/users", "cannot_view_users"), ("users/permissions", "cannot_view_permissions")):
        try:
            await client.request("GET", path + "/", params={"limit": 1, "brief": "1"})
        except ApiError as e:
            if e.code == "netbox_error" and (e.details or {}).get("status") in (401, 403):
                return bad
            return "unknown"
    return "ok"
