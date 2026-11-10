"""Quest routes: the gap scanner, the quest log, answers, preview, apply-and-verify, rewards, the practice island and
the site boss marker (contracts/api.md "Quests").

The estate behind a board is: the practice island with the player's practice changes (scope `practice`); else, in
fixture mode, fixtures/estate.json; else one bounded read of NetBox, kept for RACKWALKER_ESTATE_TTL seconds. A quest's
own write, "Check NetBox" and "Read NetBox again" read only the sites concerned again and put them into the kept
estate (`estate.splice`); such reads on request are spaced out (FRESH_SITE_S, FRESH_ALL_S), so no player can make the
proxy read a large NetBox over and over. A write through the editor drops the kept estate.

A quest line is complete only when a fresh read of NetBox no longer shows what it was about. XP comes from that
check, never from the client: per line, `rank XP x share of its checks NetBox now confirms x repeat factor` (the
same kind of fix at the same site pays 100, 80, 60, then 40 %). Practice and dry-run completions pay nothing.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from pathlib import Path
from typing import Annotated, Any

from fastapi import Body, FastAPI, Query, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse

from . import changes, findings, perms, practice, quests, schema
from . import contracts_gen as c
from .errors import ApiError
from .estate import Estate, read_estate, simulate, splice
from .netbox import NetBox

log = logging.getLogger("rackwalker_proxy.quests")

MAX_OPEN = 3      # lines offered at one site at a time (parked lines wait beside them)
MAX_PARKED = 4
MAX_DONE = 12     # finished lines kept in the log's answer
LEVEL_XP = 250
REPEAT = (1.0, 0.8, 0.6, 0.4)
TOOL_FOR_RANK = {"connectivity": "cable_tracer", "identity": "label_maker"}
# Reads of NetBox on a player's request are spaced out: the sites of one line at most every FRESH_SITE_S seconds, the
# whole estate at most every FRESH_ALL_S; in between, the kept estate answers. A quest's own write is always read back.
FRESH_SITE_S = 5.0
FRESH_ALL_S = 15.0
GIVER_NAMES = ("Bramble", "Tansy", "Pippin", "Moss", "Wren", "Sorrel", "Hazel", "Fenn", "Juniper", "Teasel")
Json = Annotated[Any, Body()]


def _valid(defname: str, body: Any) -> Any:
    errs = schema.errors(defname, body)
    if errs:
        raise ApiError("validation_failed", f"The request body does not match the {defname} schema.", {"errors": errs})
    return body


def rewards_body(raw: dict[str, Any], rows: dict[str, dict[str, Any]], practice_rows: dict[str, dict[str, Any]], lapsed: int,
                 loan: bool = False) -> dict[str, Any]:
    level = 1 + raw["xp"] // LEVEL_XP
    tools = ["label_maker", "cable_tracer"] if loan else raw["tools"]
    return {"xp": raw["xp"], "level": level, "next_level_xp": level * LEVEL_XP, "potions": raw["potions"], "tools": tools,
            "completed": sum(1 for r in rows.values() if r["status"] == "done"),
            "practised": sum(1 for r in rows.values() if r["status"] == "practised") + sum(1 for r in practice_rows.values() if r["status"] == "done"),
            "lapsed": lapsed}


def xp_for(line_xp: int, newly: int, total: int, repeats: int) -> int:
    """XP for `newly` confirmed checks of a line that stands for `total`: the line's rank XP, by the share NetBox now
    confirms, times the repeat factor (the same kind of fix at the same site has paid `repeats` times before)."""
    return round(line_xp * newly / max(1, total) * REPEAT[min(repeats, len(REPEAT) - 1)])


def offer(fresh: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The few lines offered at one site out of all that are open, most valuable first: the first line of each kind
    before a second line of any kind, so a site with twelve unpowered racks does not offer the same quest three times."""
    seen: set[str] = set()
    first, more = [], []
    for line in fresh:
        (more if line["kind"] in seen else first).append(line)
        seen.add(line["kind"])
    chosen = (first + more)[:MAX_OPEN]
    return [line for line in fresh if any(line is c for c in chosen)]


class Gate:
    """Would a quest's change really be written for this player, and if not, why not."""

    def __init__(self, note: str | None, allowed: perms.Permissions | None):
        self.note, self.allowed = note, allowed

    def blocked(self, ops: list[dict[str, Any]]) -> str | None:
        """Why these ops would run as practice for this player, or None when they would be written."""
        if self.note is not None:
            return self.note
        missing = self.allowed.missing(perms.needs_of(ops)) if self.allowed is not None else []
        return perms.why_not(missing) if missing else None

    def board(self) -> tuple[bool, str | None]:
        """(can_write, write_blocked) of a quest log: false when nothing a quest writes would be written."""
        if self.note is not None:
            return False, self.note
        missing = self.allowed.missing(perms.QUEST_NEEDS) if self.allowed is not None else []
        if len(missing) == len(perms.QUEST_NEEDS):
            return False, ("Your NetBox user may not change anything a quest writes (cables, circuits, addresses, devices, racks, sites), "
                           "so every line runs as practice. Ask a NetBox admin for the permission.")
        return True, perms.some_missing(missing) if missing else None


def base_checks(line: dict[str, Any], row: dict[str, Any] | None) -> list[str]:
    """Every check a line has stood for: those it had when the player first saw it, and any it has gained since (a
    device added to an audited rack). A line shown from its snapshot (its finding is gone) has only the first."""
    now = quests.check_ids(line) if line["finding"].get("data") else []
    return list(dict.fromkeys((row["checks"] if row else []) + now))


class Standing:
    """How a player's quest rows stand against an analysis: what they answered as "that is how it is", and which lines
    are closed for them."""

    def __init__(self, a: findings.Analysis, rows: dict[str, dict[str, Any]]):
        self.a, self.rows = a, rows
        self.resolved = {qid[2:] for qid, r in rows.items() if r["status"] == "dismissed"} | \
                        {cid for r in rows.values() for cid in quests.waived(r["snapshot"], r["answers"])}

    def closed(self, line: dict[str, Any]) -> bool:
        """Done or dismissed, with nothing new to ask. A closed line whose finding comes back is not reopened; one that
        has gained an object it never covered (a new device with no label in an audited rack) is offered again."""
        row = self.rows.get(line["id"])
        if row is None or row["status"] not in ("done", "dismissed"):
            return False
        return not [cid for cid in quests.open_checks(self.a, line) if cid not in row["checks"] and cid not in self.resolved]

    def open_lines(self, ls: list[dict[str, Any]], site_id: int) -> list[dict[str, Any]]:
        return [line for line in ls if line["site_id"] == site_id and not self.closed(line)]

    def in_order(self, ls: list[dict[str, Any]], site_id: int) -> bool:
        """Nothing open at the site and nothing missing: the only state in which a spot check is offered."""
        return not self.open_lines(ls, site_id) and self.a.completeness(site_id, self.resolved)["open"] == 0


def finding_body(f: dict[str, Any]) -> dict[str, Any]:
    return {k: f[k] for k in ("id", "kind", "rank", "site_id", "title", "evidence")}


def quest_body(scope: str, line: dict[str, Any], row: dict[str, Any] | None, state: dict[str, Any] | None, status: str,
               lapsed: bool = False, gate: Gate | None = None) -> dict[str, Any]:
    step = state["step"] if state else None
    if step is not None:
        step = {**step, "fields": [{k: f.get(k) for k in ("id", "label", "type", "hint", "suggestion")} | {"options": f.get("options") or []}
                                   for f in step["fields"]]}
    total = len(row["checks"]) if row else len(quests.check_ids(line))
    return {"id": line["id"], "scope": scope, "kind": line["kind"], "rank": line["rank"], "site_id": line["site_id"], "title": line["title"],
            "purpose": line["purpose"], "outcome": line["outcome"], "status": status, "step": step,
            "proposal": state["proposal"] if state else None, "changes": state.get("changes", []) if state else [], "checklist": state["checklist"] if state else [],
            "write_blocked": gate.blocked(state["proposal"]["ops"]) if gate and state and state["proposal"] else None,
            "note": state["note"] if state else None, "said": state["said"] if state else [], "target": state["target"] if state else None,
            "finding": finding_body(line["finding"]), "xp": line["xp"], "minutes": line["minutes"],
            "progress": {"total": total, "verified": len(row["verified"]) if row else 0}, "lapsed": lapsed,
            "updated_at": row["updated_at"] if row else None}


def snapshot(line: dict[str, Any]) -> dict[str, Any]:
    return {k: line[k] for k in ("id", "kind", "rank", "site_id", "title", "purpose", "outcome", "xp", "minutes")} | {"finding": finding_body(line["finding"]) | {"data": {}}}


def install(app: FastAPI, *, client: NetBox | None, fixtures_dir: Path | None, write_enabled: bool, estate_ttl: float,
            clear_cache: Any) -> None:
    store = lambda: app.state.game_store()  # noqa: E731
    require = lambda request, write: app.state.game_require(request, write)  # noqa: E731
    reader = perms.PermissionReader(client) if client is not None else None
    app.state.permission_reader = reader
    # the kept estate: when it was read whole, and when single sites of it were read again since
    cached: dict[str, Any] = {"at": 0.0, "estate": None, "sites": {}}
    reading = asyncio.Lock()   # one read of NetBox at a time: requests that arrive meanwhile use its result

    def drop_estate() -> None:
        cached.update(estate=None, sites={})
        if reader:
            reader.clear()

    app.state.drop_estate = drop_estate

    async def real_estate(fresh: bool = False) -> tuple[Estate, str]:
        """The kept estate, read whole when there is none or it is older than the TTL. `fresh` asks for a read now:
        granted at most every FRESH_ALL_S seconds."""
        if fixtures_dir:
            return Estate.from_json(json.loads((fixtures_dir / "estate.json").read_text())), "fixture"
        if client is None:
            raise ApiError("netbox_unreachable", "The proxy has no NetBox configured (NETBOX_URL is empty).", None)

        def kept() -> Estate | None:
            age = time.monotonic() - cached["at"]
            return cached["estate"] if cached["estate"] is not None and age < (FRESH_ALL_S if fresh else estate_ttl) else None
        if kept() is not None:
            return cached["estate"], "cache"
        async with reading:
            if kept() is not None:
                return cached["estate"], "cache"
            e = await read_estate(client)
            cached.update(at=time.monotonic(), estate=e, sites={})
            return e, "netbox"

    async def site_estate(site_ids: set[int], force: bool = False) -> Estate:
        """The kept estate with these sites read again from NetBox now. Without `force` (a player asked: "Check
        NetBox", "Read NetBox again") a site read less than FRESH_SITE_S seconds ago is not read again; with it (the
        proxy has just written there) it always is. With no kept estate, the whole estate is read."""
        if fixtures_dir or client is None or cached["estate"] is None or time.monotonic() - cached["at"] >= estate_ttl:
            return (await real_estate())[0]
        async with reading:
            base, now = cached["estate"], time.monotonic()
            if base is None:
                e = await read_estate(client)
                cached.update(at=now, estate=e, sites={})
                return e
            want = sorted(s for s in site_ids if force or now - max(cached["at"], cached["sites"].get(s, 0.0)) >= FRESH_SITE_S)
            if not want:
                return base
            try:
                e = splice(base, await read_estate(client, want), want)
            except BaseException:
                drop_estate()   # never serve what was read before a write as current
                raise
            cached["estate"] = e
            cached["sites"].update({s: time.monotonic() for s in want})
            return e

    async def estate_of(scope: str, uid: int, fresh: bool = False, site_id: int | None = None) -> tuple[Estate, str]:
        if scope == "practice":
            ops = await run_in_threadpool(lambda: store().practice_ops(uid))
            return practice.island(ops), "practice"
        if fresh and site_id is not None:
            return await site_estate({site_id}), "netbox"
        return await real_estate(fresh)

    def touched_sites(e: Estate, line: dict[str, Any], ops: list[dict[str, Any]]) -> set[int]:
        """The sites a quest's change reaches: the line's own, and that of every object the ops name."""
        out: set[int | None] = {line["site_id"]}
        for op in ops:
            f, target = op.get("fields") or {}, op["target"]
            if op["op"] == "connect_cable":
                out.update(e.port_site((f[k + "_kind"], f[k + "_id"])) for k in ("a", "b") if isinstance(f[k + "_id"], int))
            elif op["op"] == "create_circuit_termination":
                out.add(f.get("site_id"))
            elif target["kind"] == "device":
                out.add(e.devices.get(target["id"], {}).get("site_id"))
            elif target["kind"] == "rack":
                out.add(e.racks.get(target["id"], {}).get("site_id"))
            elif target["kind"] == "site":
                out.add(target["id"])
        return {s for s in out if s is not None}

    async def may_write(uid: int | None) -> perms.Permissions | None:
        """The player's NetBox permissions, or None for the single-user local mode (no NetBox user to ask about)."""
        if uid is None or uid == 0 or reader is None:
            return None
        return await reader.of(uid)

    app.state.may_write = may_write

    # ---------------------------------------------------------------- the gap scanner (no sign-in: a read, like a scene)
    @app.get("/api/quests/gaps/{site_id}")
    async def gaps(site_id: Annotated[int, schema_id()], offset: Annotated[int, Query(ge=0)] = 0,
                   limit: Annotated[int, Query(ge=1, le=500)] = 100):
        e, source = await real_estate()
        if site_id not in e.sites:
            raise ApiError("not_found", f"Site {site_id} does not exist.", {"kind": "site", "id": site_id})
        a = findings.analyse(e)
        items = a.site_gaps(site_id)
        body = {"contract_version": schema.CONTRACT_VERSION, "site_id": site_id, "total": len(items), "offset": offset, "limit": limit,
                "unlisted": a.unlisted.get(site_id, 0),
                "items": [{k: g[k] for k in ("id", "kind", "object_id", "field", "site_id", "rank", "label")} for g in items[offset:offset + limit]],
                "completeness": a.completeness(site_id), "source": source, "incomplete": sorted(set(e.truncated + e.unreadable))}
        return JSONResponse(body)

    # ---------------------------------------------------------------- the board
    def board(uid: int, scope: str, site_id: int | None, e: Estate, gate: Gate) -> dict[str, Any]:
        st = store()
        a = findings.analyse(e)
        ls = quests.lines(e, a)
        rows = st.quests(uid, scope)
        raw = st.rewards(uid)
        tools = frozenset(["label_maker", "cable_tracer"] if scope == "practice" else raw["tools"])
        by_id = {line["id"]: line for line in ls}
        acks = st.boss_acks(uid, scope)
        stand = Standing(a, rows)
        dismissed = stand.resolved
        # something the player completed that NetBox no longer shows: counted, never reopened
        lapsed_ids = {qid for qid, r in rows.items() if r["status"] == "done" and qid in by_id and stand.closed(by_id[qid])
                      and quests.open_checks(a, by_id[qid])}
        reopened = {qid for qid, r in rows.items() if r["status"] in ("done", "dismissed") and qid in by_id and not stand.closed(by_id[qid])}
        # a list that was cut at its cap, or that NetBox refuses to the proxy: the picture is not whole
        partial = sorted(set(e.truncated + e.unreadable))
        out_quests: list[dict[str, Any]] = []
        sites_out: list[dict[str, Any]] = []
        queued = 0
        best: tuple[Any, str] | None = None
        for sid in sorted(e.sites):
            open_lines = stand.open_lines(ls, sid)
            waiting = [line for line in open_lines if rows.get(line["id"], {}).get("status") in ("parked", "practised")]
            fresh = [line for line in open_lines if line not in waiting]
            comp = a.completeness(sid, dismissed)
            if partial:
                comp = {**comp, "percent": min(comp["percent"], 99)}   # never 100 % on a picture that is not whole
            if not open_lines and comp["open"] == 0 and not partial:
                # nothing open here and nothing missing: one spot check, never invented work
                n_done = sum(1 for qid, r in rows.items() if r["site_id"] == sid and qid.startswith("q:spot_check:") and r["status"] == "done")
                spot = quests.spot_check(e, sid, n_done)
                parked_spot = spot is not None and rows.get(spot["id"], {}).get("status") == "parked"
                if spot is not None:
                    (waiting if parked_spot else fresh).append(spot)
            real_open = len(open_lines)
            shown = offer(fresh) + waiting[:MAX_PARKED]
            if site_id in (None, sid):
                queued += max(0, len(open_lines) - len(shown))
                for line in shown:
                    row = rows.get(line["id"])
                    state = quests.evaluate(e, line, row["answers"] if row else {}, tools)
                    status = "offered" if row is None else {"asking": "active"}.get(state["status"], state["status"])
                    if row is not None and row["status"] == "practised" and state["status"] == "ready":
                        status = "practised"
                    out_quests.append(quest_body(scope, line, row, state, status, gate=gate))
            if fresh and fresh[0] in ls and (best is None or ls.index(fresh[0]) < best[0]):
                best = (ls.index(fresh[0]), fresh[0]["id"])
            sites_out.append({"site_id": sid, "name": e.sites[sid]["name"], "completeness": comp, "open_quests": real_open,
                              "boss_ready": comp["total"] > 0 and comp["open"] == 0 and not partial, "boss_acknowledged": sid in acks,
                              "giver": {"name": GIVER_NAMES[sid % len(GIVER_NAMES)]}})
        # A line the player had begun whose finding is gone: someone recorded it by other means. It waits in the log as
        # "ready" with nothing to write; checking it against NetBox completes it.
        if scope == "netbox":
            for r in rows.values():
                if r["status"] in ("active", "parked", "ready", "practised") and r["quest_id"] not in by_id and site_id in (None, r["site_id"]) \
                        and not r["quest_id"].startswith("q:spot_check:"):   # a spot check is answered by the player, never by NetBox
                    snap = r["snapshot"]
                    gone = {"step": None, "proposal": None, "checklist": [], "said": [], "target": None,
                            "note": "NetBox already shows this. Check it against NetBox to complete the line."}
                    out_quests.append(quest_body(scope, {**snap, "finding": {**snap["finding"], "data": {}}}, r, gone, "ready"))
        done = sorted((r for r in rows.values() if r["status"] in ("done", "dismissed") and site_id in (None, r["site_id"])
                       and r["quest_id"] not in reopened),
                      key=lambda r: r["updated_at"], reverse=True)[:MAX_DONE]
        for r in done:
            snap = r["snapshot"]
            line = {**snap, "finding": {**snap["finding"], "data": {}}}
            out_quests.append(quest_body(scope, line, r, None, r["status"], lapsed=r["quest_id"] in lapsed_ids))
        here = [q for q in out_quests if q["status"] not in ("done", "dismissed") and q["kind"] != "spot_check"]
        in_view = [s for s in sites_out if site_id in (None, s["site_id"])]
        undone = sum(1 for qid in lapsed_ids if site_id in (None, rows[qid]["site_id"]))
        all_ok = not here and queued == 0 and all(s["completeness"]["open"] == 0 for s in in_view) and not partial
        where = e.sites[site_id]["name"] if site_id in e.sites else ("the practice island" if scope == "practice" else "your estate")
        if partial and not here and queued == 0 and all(s["completeness"]["open"] == 0 for s in in_view):
            message = (f"Nothing to ask at {where} from what the game could read, but it could not read everything "
                       f"({', '.join(partial)}): the picture is not whole, so nothing here counts as complete.")
        elif all_ok:
            message = f"All in order at {where}: nothing the game can see is missing. Well kept."
        elif not here and queued == 0:
            # nothing left to ask, yet not complete: something done before is no longer in NetBox. Said, not reopened.
            message = (f"Nothing new to ask at {where}. {undone} thing{'s' if undone != 1 else ''} you recorded before "
                       f"{'are' if undone != 1 else 'is'} no longer in NetBox; nothing is reopened.")
        else:
            n = len(here) + queued
            message = f"{n} thing{'s' if n != 1 else ''} to find out at {where}; the most useful one comes first."
        practice_rows = rows if scope == "practice" else st.quests(uid, "practice")
        real_rows = rows if scope == "netbox" else st.quests(uid, "netbox")
        return {"contract_version": schema.CONTRACT_VERSION, "scope": scope, "site_id": site_id, "write_enabled": write_enabled,
                "can_write": gate.board()[0], "write_blocked": gate.board()[1], "all_in_order": all_ok, "message": message,
                "findings": [finding_body(f) for f in a.findings if site_id in (None, f["site_id"])][:50], "quests": out_quests,
                "queued": queued, "next_quest_id": best[1] if best else None, "sites": sites_out,
                "rewards": rewards_body(raw, real_rows, practice_rows, len(lapsed_ids) if scope == "netbox" else 0, loan=scope == "practice"),
                "incomplete": partial}

    async def gate_of(scope: str, uid: int | None) -> Gate:
        """Whether a change would really be written for this player: the proxy's mode first, then the player's own
        NetBox permissions (so the quest log says it before the player has answered a whole line)."""
        if scope == "practice":
            return Gate("The practice island is made up: nothing you do here reaches NetBox.", None)
        if fixtures_dir:
            return Gate("This proxy serves canned fixture data: nothing is written.", None)
        if not write_enabled:
            return Gate("The proxy runs in dry-run mode (WRITE_ENABLED is not 1): changes are previewed, not written.", None)
        try:
            return Gate(None, await may_write(uid))
        except ApiError as err:
            text = err.message.removeprefix(perms.NOT_WRITTEN)
            return Gate(text[:1].upper() + text[1:], None)

    async def board_for(uid: int, scope: str, site_id: int | None, fresh: bool = False) -> dict[str, Any]:
        e, _ = await estate_of(scope, uid, fresh, site_id)
        if site_id is not None and site_id not in e.sites and scope == "netbox" and not fresh:
            e, _ = await real_estate(fresh=True)   # a site added to NetBox since the estate was read
        if site_id is not None and site_id not in e.sites:
            raise ApiError("not_found", f"Site {site_id} does not exist.", {"kind": "site", "id": site_id})
        gate = await gate_of(scope, uid)
        return await run_in_threadpool(board, uid, scope, site_id, e, gate)

    def find_line(e: Estate, scope: str, uid: int, quest_id: str) -> tuple[dict[str, Any], dict[str, Any] | None, findings.Analysis, frozenset[str]]:
        a = findings.analyse(e)
        line = next((x for x in quests.lines(e, a) if x["id"] == quest_id), None)
        st = store()
        row = st.quests(uid, scope).get(quest_id)
        if line is None and quest_id.startswith("q:spot_check:device:"):
            # a spot check is made up of the site's state, not of a finding: build the same one the board offered
            parts = quest_id.split(":")
            dev = e.devices.get(int(parts[3])) if len(parts) == 5 and parts[3].isdigit() and parts[4].isdigit() else None
            cand = quests.spot_check(e, dev["site_id"], int(parts[4])) if dev else None
            line = cand if cand is not None and cand["id"] == quest_id else None
            if line is not None and (e.truncated or e.unreadable
                                     or not Standing(a, st.quests(uid, scope)).in_order(quests.lines(e, a), line["site_id"])):
                raise ApiError("conflict", "A spot check is offered only at a site with nothing open; this site still has things to find out.",
                               {"quest_id": quest_id})
        if line is None:
            raise ApiError("not_found", "That quest is no longer open: NetBox has changed since it was offered.", {"quest_id": quest_id})
        tools = frozenset(["label_maker", "cable_tracer"] if scope == "practice" else st.rewards(uid)["tools"])
        return line, row, a, tools

    def save_row(uid: int, scope: str, line: dict[str, Any], row: dict[str, Any] | None, status: str, answers: dict[str, Any],
                 verified: list[str] | None = None) -> dict[str, Any]:
        return store().put_quest(uid, scope, {
            "quest_id": line["id"], "site_id": line["site_id"], "status": status, "answers": answers,
            "checks": base_checks(line, row), "verified": verified if verified is not None else (row["verified"] if row else []),
            "snapshot": snapshot(line)})

    @app.get("/api/game/quests")
    async def get_board(request: Request, scope: Annotated[str, Query(pattern="^(netbox|practice)$")] = "netbox",
                        site_id: Annotated[int | None, Query(ge=1)] = None, fresh: bool = False):
        uid = await run_in_threadpool(require, request, False)
        # fresh=true reads NetBox now instead of using the kept estate (the log's "Read NetBox again")
        # (asked too soon after the last such read, the kept estate answers: see FRESH_SITE_S and FRESH_ALL_S)
        return _no_store(await board_for(uid, scope, site_id, fresh))

    @app.post("/api/game/quests/answer")
    async def answer(request: Request, body: Json):
        uid = await run_in_threadpool(require, request, True)
        b = _valid("QuestAnswer", body)
        scope = b["scope"]
        e, _ = await estate_of(scope, uid)
        gate = await gate_of(scope, uid)

        def work() -> dict[str, Any]:
            line, row, _, tools = find_line(e, scope, uid, b["quest_id"])
            answers = dict(row["answers"]) if row else {}
            if b["step"] == "restart":
                answers = {}
            elif b["step"] == "back":
                # one answer back: the question a parked line waits at, else the last one answered
                now = quests.evaluate(e, line, answers, tools)
                last = now["step"]["id"] if now["status"] == "parked" and now["step"] else (now["answered"] or [None])[-1]
                answers.pop(last, None)
            else:
                current = quests.evaluate(e, line, {k: v for k, v in answers.items() if k != b["step"]}, tools)
                if current["step"] is None or current["step"]["id"] != b["step"]:
                    # not the question the line is waiting at (a stale page): show where the line really stands
                    current = quests.evaluate(e, line, answers, tools)
                    status = {"asking": "active"}.get(current["status"], current["status"])
                    return quest_body(scope, line, row, current, status if row else "offered", gate=gate)
                answers[b["step"]] = {k: b[k] for k in ("choice", "text", "values") if k in b}
            state = quests.evaluate(e, line, answers, tools)
            status = {"asking": "active", "confirmed": "done"}.get(state["status"], state["status"])
            saved = save_row(uid, scope, line, row, status, answers)
            return quest_body(scope, line, saved, state, status, gate=gate)
        return _no_store(await run_in_threadpool(work))

    def ready_line(e: Estate, scope: str, uid: int, quest_id: str) -> tuple[dict[str, Any], dict[str, Any] | None, dict[str, Any]]:
        line, row, _, tools = find_line(e, scope, uid, quest_id)
        state = quests.evaluate(e, line, row["answers"] if row else {}, tools)
        if state["status"] != "ready":
            raise ApiError("conflict", "That quest has no change ready yet: a question is still open.", {"status": state["status"]})
        return line, row, state

    def sim_result(e: Estate, proposal: dict[str, Any], mode: str) -> tuple[dict[str, Any], Estate]:
        trial = e.copy()
        ops = simulate(trial, proposal["ops"])
        if any(o["status"] == "error" for o in ops):
            for o in ops:
                if o["status"] != "error":
                    o.update(status="skipped", after=None, diff=[])
        return {"mode": mode, "applied": False, "write_enabled": write_enabled, "ops": ops}, trial

    @app.post("/api/game/quests/preview")
    async def preview(request: Request, body: Json):
        uid = await run_in_threadpool(require, request, True)
        b = _valid("QuestRef", body)
        e, _ = await estate_of(b["scope"], uid)
        line, row, state = await run_in_threadpool(ready_line, e, b["scope"], uid, b["quest_id"])
        if b["scope"] == "practice" or fixtures_dir or client is None:
            return _no_store(sim_result(e, state["proposal"], "preview")[0])
        planned = await changes.preview(client, c.ChangeSet.model_validate(state["proposal"]))
        return _no_store(changes.result("preview", planned, write_enabled).model_dump(mode="json", exclude_unset=True))

    def settle(uid: int, scope: str, line: dict[str, Any], row: dict[str, Any] | None, after: Estate, real: bool) -> dict[str, Any]:
        """Reads the outcome from a fresh estate: which of the line's checks are now confirmed, what that pays."""
        st = store()
        a = findings.analyse(after)
        base = base_checks(line, row)
        said = row["answers"] if row else {}
        waived = set(quests.waived(line, said))   # "there is none, on purpose": answered, never paid
        site_checks = a.checks.get(line["site_id"], {})
        still = {cid for cid in base if site_checks.get(cid) is False and cid not in waived}
        confirmed = [cid for cid in base if cid not in still and cid not in waived]
        newly = [cid for cid in confirmed if cid not in (row["verified"] if row else [])]
        complete = not still
        xp = potions = 0
        unlocked: list[str] = []
        if real and newly:
            signature = f"{line['kind']}:{line['site_id']}"
            raw = st.rewards(uid)
            xp = xp_for(line["xp"], len(newly), len(base), raw["repeats"].get(signature, 0))
            before_level = 1 + raw["xp"] // LEVEL_XP
            st.add_reward(uid, line["id"], "xp", xp, signature)
            if 1 + (raw["xp"] + xp) // LEVEL_XP > before_level:
                potions = 1
                st.add_reward(uid, line["id"], "potion", 1, "level")
            tool = TOOL_FOR_RANK.get(line["rank"])
            if complete and tool and tool not in raw["tools"]:
                st.add_reward(uid, line["id"], "tool", 1, "tool", item=tool)
                unlocked.append(tool)
        if complete:
            status = "done" if (real or scope == "practice") else "practised"
        else:
            status = "active"
        # What the player said is kept: a check that found nothing new, a dry run and a refused write change nothing,
        # so the line stands where it stood. Only the answers about what is now confirmed go (the rest of an errand
        # is asked about, not the part that is done).
        wrote = real or scope == "practice"
        answers = quests.prune(said, confirmed) if status == "active" and wrote else said
        saved = save_row(uid, scope, line, row, status, answers, verified=confirmed if wrote else (row["verified"] if row else []))
        return {"saved": saved, "complete": complete, "newly": newly, "xp": xp, "potions": potions, "unlocked": unlocked, "open": sorted(still)}

    def verdict(uid: int, scope: str, line: dict[str, Any], s: dict[str, Any], mode: str, verified: bool, message: str,
                result: dict[str, Any] | None, trace: str | None, e: Estate, gate: Gate | None = None) -> dict[str, Any]:
        st = store()
        if s["complete"] and verified and line["site_id"] in e.sites:
            # the small step, and where it leaves the site
            comp = findings.analyse(e).completeness(line["site_id"], Standing(findings.analyse(e), st.quests(uid, scope)).resolved)
            message += f" {e.sites[line['site_id']]['name']} is now {comp['percent']} % complete."
        rows = st.quests(uid, "netbox")
        prows = st.quests(uid, "practice")
        state = None
        shown = line
        if s["saved"]["status"] == "active":
            # the rest of the line, as the estate stands now (an errand goes on with the labels still missing)
            fresh = next((x for x in quests.lines(e, findings.analyse(e)) if x["id"] == line["id"]), None)
            if fresh is not None:
                tools = frozenset(["label_maker", "cable_tracer"] if scope == "practice" else st.rewards(uid)["tools"])
                shown, state = fresh, quests.evaluate(e, fresh, s["saved"]["answers"], tools)
        status = s["saved"]["status"]
        if status == "active" and state is not None:
            status = {"asking": "active"}.get(state["status"], state["status"])   # as the quest log would show it
        return {"quest": quest_body(scope, shown, s["saved"], state, status, gate=gate), "mode": mode, "verified": verified,
                "complete": s["complete"], "xp_awarded": s["xp"], "potions_awarded": s["potions"], "message": message,
                "can_now": line["outcome"] if s["complete"] and verified else None, "unlocked": s["unlocked"], "trace": trace, "result": result,
                "rewards": rewards_body(st.rewards(uid), rows, prows, 0, loan=scope == "practice")}

    async def trace_text(e: Estate, proposal: dict[str, Any] | None) -> str | None:
        """What NetBox's own cable trace says about the first interface a proposal cabled."""
        if client is None or not proposal:
            return None
        op = next((o for o in proposal["ops"] if o["op"] == "connect_cable" and o["fields"]["a_kind"] == "interface"), None)
        if op is None:
            return None
        iid = op["fields"]["a_id"]
        try:
            segs = await client.trace("interface", iid)
        except ApiError:
            return None
        port = e.ports.get(("interface", iid))
        start = f"{e.device_name(port['device_id'])} {port['name']}" if port else f"interface {iid}"
        if not segs:
            return f"NetBox's cable trace from {start} finds no cable."
        far = segs[-1][2] if len(segs[-1]) > 2 else []
        names = []
        for t in far or []:
            if t.get("circuit"):
                names.append(f"circuit {t['circuit'].get('cid')} (side {t.get('term_side')})")
            else:
                names.append(f"{(t.get('device') or {}).get('name') or '?'} {t.get('name') or ''}".strip())
        return f"NetBox's cable trace from {start} reaches {' and '.join(names) or 'an open end'} in {len(segs)} hop{'s' if len(segs) != 1 else ''}."

    @app.post("/api/game/quests/apply")
    async def apply(request: Request, body: Json):
        uid = await run_in_threadpool(require, request, True)
        b = _valid("QuestRef", body)
        scope = b["scope"]
        e, _ = await estate_of(scope, uid)
        line, row, state = await run_in_threadpool(ready_line, e, scope, uid, b["quest_id"])
        proposal = state["proposal"]
        gate = await gate_of(scope, uid)
        if scope == "practice":
            result, after = sim_result(e, proposal, "apply")
            ok = all(o["status"] != "error" for o in result["ops"])
            if ok:
                result["applied"] = True
                await run_in_threadpool(lambda: store().set_practice_ops(uid, store().practice_ops(uid) + proposal["ops"]))
            s = await run_in_threadpool(settle, uid, scope, line, row, after if ok else e, False)
            msg = ("The island now shows it. On your own NetBox this would earn XP, once NetBox confirms it."
                   if s["complete"] else "The island took that change." if ok else "That change does not fit the island; look at the preview.")
            return _no_store(await run_in_threadpool(verdict, uid, scope, line, s, "practice", ok and s["complete"], msg, result, None, after if ok else e, gate))
        _, who = await run_in_threadpool(app.state.change_identity, request)
        blocked = gate.blocked(proposal["ops"])
        if blocked is not None:
            # a dry run: the change is planned against NetBox (or the fixture estate) and shown, never written
            if fixtures_dir or client is None:
                result, after = sim_result(e, proposal, "preview")
            else:
                planned = await changes.preview(client, c.ChangeSet.model_validate(proposal))
                result = changes.result("preview", planned, write_enabled).model_dump(mode="json", exclude_unset=True)
                after = e.copy()
                simulate(after, proposal["ops"])
            ok = all(o["status"] != "error" for o in result["ops"])
            s = await run_in_threadpool(settle, uid, scope, line, row, after if ok else e, False)
            msg = (f"Practice run, nothing was written: {blocked} No XP for a practice run." if ok
                   else "NetBox would refuse that change; look at the preview.")
            return _no_store(await run_in_threadpool(verdict, uid, scope, line, s, "dry_run", False, msg, result, None, e, gate))
        assert client is not None
        try:
            res = await changes.apply(client, c.ChangeSet.model_validate(proposal), write_enabled, player=who)
        except BaseException:
            drop_estate()   # a write that failed half-way: never serve what was read before it as current
            raise
        finally:
            clear_cache()
            if reader:
                reader.clear()
        result = res.model_dump(mode="json", exclude_unset=True)
        # never the client's word: NetBox is read again, at the sites this change reached
        after = await site_estate(touched_sites(e, line, proposal["ops"]), force=True)
        s = await run_in_threadpool(settle, uid, scope, line, row, after, True)
        trace = await trace_text(after, proposal) if res.applied else None
        if not res.applied:
            msg = "NetBox did not take that change; look at the preview for the reason."
        elif s["complete"]:
            msg = "NetBox confirms it."
        elif s["newly"]:
            msg = f"NetBox confirms {len(s['newly'])} of {len(s['saved']['checks'])}; the rest of this line stays open."
        else:
            msg = "Written, but NetBox does not show the outcome yet; the line stays open."
        return _no_store(await run_in_threadpool(verdict, uid, scope, line, s, "real", bool(s["newly"]) or s["complete"], msg, result, trace, after, gate))

    @app.post("/api/game/quests/verify")
    async def verify(request: Request, body: Json):
        """Reads NetBox again for a line the player may have fixed by other means (the editor, or NetBox itself)."""
        uid = await run_in_threadpool(require, request, True)
        b = _valid("QuestRef", body)
        if b["scope"] != "netbox" or fixtures_dir:
            raise ApiError("bad_request", "Only quests on your own NetBox are checked against NetBox.", None)
        if b["quest_id"].startswith("q:spot_check:"):
            raise ApiError("bad_request", "A spot check is answered by looking at the device, not by reading NetBox.", None)
        st_rows = await run_in_threadpool(lambda: store().quests(uid, "netbox"))
        row = st_rows.get(b["quest_id"])
        kept, _ = await real_estate()
        known = next((x for x in quests.lines(kept, findings.analyse(kept)) if x["id"] == b["quest_id"]), None)
        if row is None and known is None:
            raise ApiError("not_found", "That quest is not open.", {"quest_id": b["quest_id"]})
        # the line's own site is read again (not the whole estate), and not more often than every few seconds
        after = await site_estate({row["site_id"] if row else known["site_id"]})
        a = findings.analyse(after)
        line = next((x for x in quests.lines(after, a) if x["id"] == b["quest_id"]), None)
        if line is None:
            if row is None:
                raise ApiError("not_found", "That quest is not open.", {"quest_id": b["quest_id"]})
            snap = row["snapshot"]
            line = {**snap, "finding": {**snap["finding"], "data": {}}, "site_id": row["site_id"]}
        s = await run_in_threadpool(settle, uid, "netbox", line, row, after, True)
        msg = ("NetBox confirms it." if s["complete"] else
               f"NetBox confirms {len(s['newly'])} more; the rest stays open." if s["newly"] else "NetBox does not show it yet.")
        gate = await gate_of("netbox", uid)
        return _no_store(await run_in_threadpool(verdict, uid, "netbox", line, s, "real", bool(s["newly"]) or s["complete"], msg, None, None, after, gate))

    @app.post("/api/game/practice/reset")
    async def reset_practice(request: Request):
        uid = await run_in_threadpool(require, request, True)

        def work() -> None:
            store().set_practice_ops(uid, [])
            store().clear_quests(uid, "practice")
        await run_in_threadpool(work)
        return _no_store(await board_for(uid, "practice", None))

    @app.post("/api/game/boss/ack")
    async def boss_ack(request: Request, body: Json):
        uid = await run_in_threadpool(require, request, True)
        b = _valid("BossAck", body)
        full = await board_for(uid, b["scope"], None)
        site = next((s for s in full["sites"] if s["site_id"] == b["site_id"]), None)
        if site is None:
            raise ApiError("not_found", f"Site {b['site_id']} does not exist.", {"kind": "site", "id": b["site_id"]})
        if not site["boss_ready"]:
            raise ApiError("conflict", "That site's Confusion is not ready: the site is not at 100 % yet.", {"completeness": site["completeness"]})
        await run_in_threadpool(lambda: store().ack_boss(uid, b["scope"], b["site_id"]))
        return _no_store(await board_for(uid, b["scope"], None))


def schema_id() -> Any:
    from fastapi import Path as PathParam

    return PathParam(ge=1)


def _no_store(body: dict[str, Any]) -> JSONResponse:
    resp = JSONResponse(body)
    resp.headers["Cache-Control"] = "no-store"
    return resp
