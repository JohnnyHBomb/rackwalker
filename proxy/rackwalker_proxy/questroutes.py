"""Quest routes: the gap scanner, the quest log, answers, preview, apply-and-verify, rewards, the practice island and
the site boss marker (contracts/api.md "Quests").

The estate behind a board is: the practice island with the player's practice changes (scope `practice`); else, in
fixture mode, fixtures/estate.json; else one bounded read of NetBox, kept for RACKWALKER_ESTATE_TTL seconds. A quest's
own write, "Check NetBox" and "Read NetBox again" read only the sites concerned again and put them into the kept
estate (`estate.splice`); such reads on request are spaced out (FRESH_SITE_S, FRESH_ALL_S), so no player can make the
proxy read a large NetBox over and over. A write through the editor drops the kept estate.

A quest line is complete only when a fresh read of NetBox shows its checks as passed. A check the fresh analysis does
not make at all (the device was deleted, switched off or moved; the proxy could not read a list) confirms nothing: a
line left with only such checks is closed as "no longer applies", with no XP and no tool. XP comes from the check,
never from the client: per line, `line XP x share of one pass that NetBox now confirms x repeat factor` (the same kind
of fix at the same site pays 100, 80, 60, then 40 %). A check pays once per player, whatever line it is confirmed
under and however many requests ask at once (the store's `paid` table). Practice and dry-run completions pay nothing.
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

from . import chains, changes, findings, perms, practice, quests, schema
from . import contracts_gen as c
from .errors import ApiError
from .estate import Estate, left, read_estate, simulate, splice
from .netbox import NetBox

log = logging.getLogger("rackwalker_proxy.quests")

MAX_OPEN = 3      # lines offered at one site at a time (parked lines wait beside them)
MAX_PARKED = 4
MAX_DONE = 12     # finished lines kept in the log's answer
MAX_CHAINS_SHOWN = 24   # chains listed in one quest log answer
LEVEL_XP = 250
REPEAT = (1.0, 0.8, 0.6, 0.4)
TOOL_FOR_RANK = {"connectivity": "cable_tracer", "identity": "label_maker"}
# Reads of NetBox on a player's request are spaced out: the sites of one line at most every FRESH_SITE_S seconds, the
# whole estate at most every FRESH_ALL_S; in between, the kept estate answers. A quest's own write is always read back.
FRESH_SITE_S = 5.0
FRESH_ALL_S = 15.0
READ_ONLY_TOKEN = ("You signed in with a read-only NetBox token (or before the game checked tokens), so changes are previewed, not "
                   "written. Sign in again with a write-enabled token of your own to write.")
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
    """XP for `newly` confirmed checks of a line that stands for `total`: the line's XP, by the share NetBox now
    confirms, times the repeat factor (the same kind of fix at the same site has paid `repeats` times before). A long
    line is played a few objects at a time (quests.MAX_BUNDLE), and one such pass is worth the line's XP: recording
    how to reach four of a hundred devices pays the same as four of four."""
    share = min(1.0, newly / max(1, min(total, quests.MAX_BUNDLE)))
    return round(line_xp * share * REPEAT[min(repeats, len(REPEAT) - 1)])


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
        # a dismissed line settles every check it stood for (a line about twelve ports has twelve), not only its own id
        self.resolved = {cid for qid, r in rows.items() if r["status"] == "dismissed" for cid in [qid[2:], *r["checks"]]} | \
                        {cid for r in rows.values() for cid in quests.waived(r["snapshot"], r["answers"])}
        # facts of a circuit's record answered "I don't know yet": open questions, which do not count against completeness
        self.deferred = {cid for r in rows.values() if r["status"] not in ("done", "dismissed") for cid in quests.deferred(r["snapshot"], r["answers"])}
        self.settled = self.resolved | self.deferred

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
        return not self.open_lines(ls, site_id) and self.a.completeness(site_id, self.settled)["open"] == 0


def setup_notes(e: Estate) -> list[str]:
    """What this NetBox lacks for the game to record everything, in words: what cannot be recorded is not asked for."""
    out = []
    how = "The setup script (devnetbox/seed.py --fields-only) adds it."
    if not e.has_field("rw_mgmt_mode"):
        out.append(f"NetBox has no custom field rw_mgmt_mode on devices, so how a device is managed cannot be recorded there. {how}")
    elif not e.has_field("rw_mgmt_default"):
        out.append(f"NetBox has no custom field rw_mgmt_default on device types, so a model's default cannot be recorded. {how}")
    if any(not e.has_field(f) for f in ("rw_mrc", "rw_nrc", "rw_currency")):
        out.append(f"NetBox has no custom fields rw_mrc, rw_nrc and rw_currency on circuits, so what a circuit costs is not asked for. {how}")
    if e.circuits and e.can_see("contact_roles") and not quests.contact_roles(e):
        out.append("NetBox has no contact role for support, the account manager or billing, so who to call cannot be recorded yet. "
                   "Add them under Organization > Contact Roles; the setup script does too.")
    return out


def finding_body(f: dict[str, Any]) -> dict[str, Any]:
    return {k: f[k] for k in ("id", "kind", "rank", "site_id", "title", "evidence")}


def chain_ref(line_id: str, in_chain: dict[str, tuple[dict[str, Any], int]] | None, views: dict[str, dict[str, Any]] | None) -> dict[str, Any] | None:
    """The chain and stage a line belongs to, as the quest log says it ("2 of 3: logical")."""
    if not in_chain or line_id not in in_chain or views is None:
        return None
    chain, idx = in_chain[line_id]
    v = views.get(chain["id"])
    name = chain["stages"][idx]["stage"]
    n = next((i for i, s in enumerate(v["stages"]) if s["stage"] == name), None) if v else None
    if v is None or n is None:
        return None
    return {"id": v["id"], "title": v["title"], "stage": name, "position": n + 1, "total": v["total"], "label": f"{n + 1} of {v['total']}: {name}"}


def quest_body(scope: str, line: dict[str, Any], row: dict[str, Any] | None, state: dict[str, Any] | None, status: str,
               lapsed: bool = False, gate: Gate | None = None, chain: dict[str, Any] | None = None) -> dict[str, Any]:
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
            "updated_at": row["updated_at"] if row else None, "chain": chain}


def snapshot(line: dict[str, Any]) -> dict[str, Any]:
    return {k: line[k] for k in ("id", "kind", "rank", "site_id", "title", "purpose", "outcome", "xp", "minutes")} | {"finding": finding_body(line["finding"]) | {"data": {}}}


def install(app: FastAPI, *, client: NetBox | None, fixtures_dir: Path | None, write_enabled: bool, estate_ttl: float,
            clear_cache: Any) -> None:
    store = lambda: app.state.game_store()  # noqa: E731
    require = lambda request, write: app.state.game_require(request, write)  # noqa: E731
    reader = perms.PermissionReader(client) if client is not None else None
    app.state.permission_reader = reader
    # the kept estate: when it was read whole, and when single sites of it were read again since
    # (`drops` counts the times the estate was dropped: a read that was under way when a write dropped it is not kept)
    cached: dict[str, Any] = {"at": 0.0, "estate": None, "sites": {}, "drops": 0}
    reading = asyncio.Lock()   # one read of NetBox at a time: requests that arrive meanwhile use its result
    applying: dict[int, asyncio.Lock] = {}   # per player: one quest apply at a time (two tabs never write one line twice)
    # What a player typed for a figure of their own (an account number, a charge, a contact's details) waits here, in
    # memory, until it is written to NetBox: it is never put in the game's store. After a restart of the proxy the
    # question is simply asked again.
    vault: dict[tuple[int, str, str], dict[str, Any]] = {}

    def answers_of(uid: int, scope: str, row: dict[str, Any] | None, quest_id: str) -> dict[str, Any]:
        return {**(row["answers"] if row else {}), **vault.get((uid, scope, quest_id), {})}

    def keep(uid: int, scope: str, quest_id: str, answers: dict[str, Any]) -> dict[str, Any]:
        """The part of a line's answers that goes to the store; a typed figure goes to `vault` instead. ("I don't know
        yet" on such a question is no figure: it is stored, so the line still knows after a restart.) On the practice
        island these questions take no typed value at all."""
        private = {k: v for k, v in answers.items() if scope == "netbox" and quests.private_step(k) and isinstance(v, dict)
                   and (v.get("text") or v.get("values"))}
        if private:
            vault[(uid, scope, quest_id)] = private
        else:
            vault.pop((uid, scope, quest_id), None)
        return {k: v for k, v in answers.items() if k not in private}

    def drop_estate() -> None:
        cached.update(estate=None, sites={}, drops=cached["drops"] + 1)
        if reader:
            reader.clear()

    async def read_whole() -> Estate:
        """Reads the whole estate (the caller holds `reading`) and keeps it, unless a write dropped the estate meanwhile."""
        assert client is not None
        drops = cached["drops"]
        e = await read_estate(client)
        if cached["drops"] == drops:
            cached.update(at=time.monotonic(), estate=e, sites={})
        return e

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
            return await read_whole(), "netbox"

    async def site_estate(site_ids: set[int], force: bool = False) -> Estate:
        """The kept estate with these sites read again from NetBox now. Without `force` (a player asked: "Check
        NetBox", "Read NetBox again") a site read less than FRESH_SITE_S seconds ago is not read again; with it (the
        proxy has just written there) it always is. With no kept estate, the whole estate is read."""
        if fixtures_dir or client is None:
            return (await real_estate())[0]
        async with reading:   # whatever is read from here on is read after the write that asked for it
            base, now, drops = cached["estate"], time.monotonic(), cached["drops"]
            if base is None or now - cached["at"] >= estate_ttl:
                return await read_whole()
            want = sorted(s for s in site_ids if force or now - max(cached["at"], cached["sites"].get(s, 0.0)) >= FRESH_SITE_S)
            if not want:
                return base
            try:
                part = await read_estate(client, want)
            except ApiError as err:
                if err.code == "netbox_error" and (err.details or {}).get("status") == 400:
                    return await read_whole()   # NetBox no longer knows one of these sites: the whole picture is read
                drop_estate()
                raise
            except BaseException:
                drop_estate()   # never serve what was read before a write as current
                raise
            if left(base, part, want):
                return await read_whole()   # something left these sites (moved or deleted): only a whole read says where it is
            e = splice(base, part, want)
            if cached["drops"] == drops:
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

    async def may_write(uid: int | None, fresh: bool = False) -> perms.Permissions | None:
        """The player's NetBox permissions, or None for the single-user local mode (no NetBox user to ask about).
        `fresh` (every write) asks NetBox now; the quest log may use an answer a few seconds old."""
        if uid is None or uid == 0 or reader is None:
            return None
        return await reader.of(uid, fresh=fresh)

    app.state.may_write = may_write

    # ---------------------------------------------------------------- the gap scanner (no sign-in: a read, like a scene)
    @app.get("/api/quests/gaps/{site_id}")
    async def gaps(site_id: Annotated[int, schema_id()], offset: Annotated[int, Query(ge=0)] = 0,
                   limit: Annotated[int, Query(ge=1, le=500)] = 100):
        e, source = await real_estate()
        if site_id not in e.sites:
            raise ApiError("not_found", f"Site {site_id} does not exist.", {"kind": "site", "id": site_id})
        a = await run_in_threadpool(findings.analyse, e)   # the first scan of a large estate takes seconds
        items = a.site_gaps(site_id)
        body = {"contract_version": schema.CONTRACT_VERSION, "site_id": site_id, "total": len(items), "offset": offset, "limit": limit,
                "unlisted": a.unlisted.get(site_id, 0),
                "items": [{k: g[k] for k in ("id", "kind", "object_id", "field", "site_id", "rank", "label")} for g in items[offset:offset + limit]],
                "completeness": a.completeness(site_id), "source": source, "incomplete": sorted(set(e.truncated + e.unreadable))}
        return JSONResponse(body)

    # ---------------------------------------------------------------- the board
    LIVE_ROW = ("active", "parked", "ready", "practised")

    def adopt(uid: int, scope: str, a: findings.Analysis, ls: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
        """The player's rows, after any line they had begun has followed its finding to a new id. A finding about a
        group is named after the group's first member; when that member goes (the first address is deleted, the first
        switch of an island is switched off, the device moves to another rack) the same unfixed problem comes back
        under another id, and the begun line goes with it instead of being left behind."""
        st = store()
        rows = st.quests(uid, scope)
        by_id = {line["id"]: line for line in ls}
        for qid, r in sorted(rows.items()):
            if r["status"] not in LIVE_ROW or qid in by_id or qid.startswith("q:spot_check:"):
                continue
            still = {cid for cid in r["checks"] if a.check(cid, r["site_id"]) is False}
            heir = next((line for line in ls if line["kind"] == r["snapshot"].get("kind") and line["id"] not in rows
                         and still & set(quests.check_ids(line))), None) if still else None
            if heir is not None:
                st.move_quest(uid, scope, qid, {**r, "quest_id": heir["id"], "site_id": heir["site_id"],
                                                "checks": list(dict.fromkeys(r["checks"] + quests.check_ids(heir))), "snapshot": snapshot(heir)})
                rows = st.quests(uid, scope)
        return rows

    def board(uid: int, scope: str, site_id: int | None, e: Estate, gate: Gate) -> dict[str, Any]:
        st = store()
        a = findings.analyse(e)
        ls = quests.lines(e, a)
        rows = adopt(uid, scope, a, ls)
        raw = st.rewards(uid)
        tools = frozenset(["label_maker", "cable_tracer"] if scope == "practice" else raw["tools"])
        by_id = {line["id"]: line for line in ls}
        acks = st.boss_acks(uid, scope)
        stand = Standing(a, rows)
        dismissed = stand.settled
        all_chains = chains.build(e, a, ls)
        in_chain = chains.stage_of(all_chains)
        titles = {line["id"]: line["title"] for line in ls}
        views = {ch["id"]: chains.view(e, a, ch, stand.resolved, stand.deferred, titles, told=False) for ch in all_chains}
        chains_at: dict[int, list[dict[str, Any]]] = {}
        for ch in all_chains:
            chains_at.setdefault(ch["site_id"], []).append(ch)
        mine = {cid for r in rows.values() for cid in r["verified"]} | stand.resolved
        shown_chains: list[str] = []
        chains_queued = 0
        # something the player completed that NetBox no longer shows: counted, never reopened
        lapsed_ids = {qid for qid, r in rows.items() if r["status"] == "done" and
                      ((qid in by_id and stand.closed(by_id[qid]) and quests.open_checks(a, by_id[qid]))
                       or (qid not in by_id and any(a.check(cid, r["site_id"]) is False for cid in r["verified"])))}
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
            # Chains: only the stage each chain has reached is asked about; a later stage waits for the earlier one.
            open_ids = {line["id"] for line in open_lines}
            playable: set[str] = set()
            site_chains = chains_at.get(sid, [])
            for ch in site_chains:
                v = views[ch["id"]]
                if v["status"] != "complete":
                    playable |= set(v["stages"][v["position"] - 1]["quest_ids"]) & open_ids
            fresh = [line for line in open_lines if line not in waiting
                     and (line["id"] not in in_chain or line["id"] in playable or line["id"] in rows)]
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
            # The chains in play are those of the few lines on offer or waiting (so at most a few per site), those that
            # wait on a fact the player does not know yet, and those this player completed; the rest are counted.
            in_play = {in_chain[line["id"]][0]["id"] for line in shown if line["id"] in in_chain}
            for ch in site_chains if site_id in (None, sid) else []:
                v = views[ch["id"]]
                if v["status"] == "complete":
                    if any(cid in mine for st_ in ch["stages"] for cid in st_["checks"]):
                        shown_chains.append(ch["id"])
                elif ch["id"] in in_play or v["status"] == "open_questions":
                    shown_chains.append(ch["id"])
                else:
                    chains_queued += 1
            if site_id in (None, sid):
                queued += max(0, len(open_lines) - len(shown))
                for line in shown:
                    row = rows.get(line["id"])
                    state = quests.evaluate(e, line, answers_of(uid, scope, row, line["id"]), tools)
                    status = "offered" if row is None else {"asking": "active"}.get(state["status"], state["status"])
                    if row is not None and row["status"] == "practised" and state["status"] == "ready":
                        status = "practised"
                    out_quests.append(quest_body(scope, line, row, state, status, gate=gate, chain=chain_ref(line["id"], in_chain, views)))
            if fresh and fresh[0] in ls and (best is None or ls.index(fresh[0]) < best[0]):
                best = (ls.index(fresh[0]), fresh[0]["id"])
            # a site's Confusion appears over kit that is in order: a site with nothing in it has nothing to guard
            sites_out.append({"site_id": sid, "name": e.sites[sid]["name"], "completeness": comp, "open_quests": real_open,
                              "boss_ready": comp["total"] > 0 and comp["open"] == 0 and not partial and bool(e.site_devices(sid)),
                              "boss_acknowledged": sid in acks, "giver": {"name": GIVER_NAMES[sid % len(GIVER_NAMES)]}})
        # A line the player had begun whose finding is gone. Either NetBox now shows what it asked for (someone recorded
        # it by other means), or what it was about is no longer there. It waits in the log as "ready" with nothing to
        # write; "Check NetBox" completes it in the first case and closes it, unpaid, in the second.
        if scope == "netbox":
            for r in rows.values():
                if r["status"] in LIVE_ROW and r["quest_id"] not in by_id and site_id in (None, r["site_id"]) \
                        and not r["quest_id"].startswith("q:spot_check:"):   # a spot check is answered by the player, never by NetBox
                    snap = r["snapshot"]
                    shows = any(a.check(cid, r["site_id"]) is True for cid in r["checks"])
                    gone = {"step": None, "proposal": None, "checklist": [], "said": [], "target": None,
                            "note": (f"The game could not read everything from NetBox ({', '.join(partial)}), so this line cannot be "
                                     "checked now. It stays as it is." if partial and not shows else
                                     "NetBox already shows this. Check it against NetBox to complete the line." if shows else
                                     "This no longer applies: what the line was about is not in NetBox any more, or is asked about in "
                                     "another line. Check it against NetBox to close it (nothing is paid for it).")}
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
        left_open = sum(s["completeness"]["open"] for s in in_view)
        all_ok = not here and queued == 0 and left_open == 0 and not partial
        where = e.sites[site_id]["name"] if site_id in e.sites else ("the practice island" if scope == "practice" else "your estate")
        if partial and not here and queued == 0 and left_open == 0:
            message = (f"Nothing to ask at {where} from what the game could read, but it could not read everything "
                       f"({', '.join(partial)}): the picture is not whole, so nothing here counts as complete.")
        elif all_ok:
            message = f"All in order at {where}: nothing the game can see is missing. Well kept."
        elif not here and queued == 0 and undone:
            # nothing left to ask, yet not complete: something done before is no longer in NetBox. Said, not reopened.
            message = (f"Nothing new to ask at {where}. {undone} thing{'s' if undone != 1 else ''} you recorded before "
                       f"{'are' if undone != 1 else 'is'} no longer in NetBox; nothing is reopened.")
        elif not here and queued == 0:
            message = (f"Nothing new to ask at {where} right now, though {left_open} check{'s' if left_open != 1 else ''} "
                       f"{'are' if left_open != 1 else 'is'} still open; read NetBox again later.")
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
                "incomplete": partial,
                "chains": [chains.view(e, a, ch, stand.resolved, stand.deferred, titles) for ch in all_chains if ch["id"] in set(shown_chains[:MAX_CHAINS_SHOWN])],
                "chains_queued": chains_queued + max(0, len(shown_chains) - MAX_CHAINS_SHOWN), "setup": setup_notes(e) if scope == "netbox" else []}

    async def gate_of(scope: str, uid: int | None, request: Request | None = None, fresh: bool = False) -> Gate:
        """Whether a change would really be written for this player: the proxy's mode first, then the token they signed
        in with, then the player's own NetBox permissions (so the quest log says it before the player has answered a
        whole line). `fresh` (an apply) reads the permissions from NetBox now."""
        if scope == "practice":
            return Gate("The practice island is made up: nothing you do here reaches NetBox.", None)
        if fixtures_dir:
            return Gate("This proxy serves canned fixture data: nothing is written.", None)
        if not write_enabled:
            return Gate("The proxy runs in dry-run mode (WRITE_ENABLED is not 1): changes are previewed, not written.", None)
        if request is not None and not await run_in_threadpool(app.state.session_may_write, request):
            return Gate(READ_ONLY_TOKEN, None)
        try:
            return Gate(None, await may_write(uid, fresh))
        except ApiError as err:
            text = err.message.removeprefix(perms.NOT_WRITTEN)
            return Gate(text[:1].upper() + text[1:], None)

    async def board_for(uid: int, scope: str, site_id: int | None, fresh: bool = False, request: Request | None = None) -> dict[str, Any]:
        e, _ = await estate_of(scope, uid, fresh, site_id)
        if site_id is not None and site_id not in e.sites and scope == "netbox" and not fresh:
            e, _ = await real_estate(fresh=True)   # a site added to NetBox since the estate was read
        if site_id is not None and site_id not in e.sites:
            raise ApiError("not_found", f"Site {site_id} does not exist.", {"kind": "site", "id": site_id})
        gate = await gate_of(scope, uid, request)
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
        return _no_store(await board_for(uid, scope, site_id, fresh, request))

    @app.post("/api/game/quests/answer")
    async def answer(request: Request, body: Json):
        uid = await run_in_threadpool(require, request, True)
        b = _valid("QuestAnswer", body)
        scope = b["scope"]
        e, _ = await estate_of(scope, uid)
        gate = await gate_of(scope, uid, request)

        def work() -> dict[str, Any]:
            line, row, _, tools = find_line(e, scope, uid, b["quest_id"])
            answers = answers_of(uid, scope, row, b["quest_id"])
            if b["step"] == "restart":
                answers = {}
            elif b["step"] == "back":
                # one answer back: the question a parked line waits at, else the last one answered
                now = quests.evaluate(e, line, answers, tools)
                if row is None:
                    return quest_body(scope, line, None, now, "offered", gate=gate, chain=line_chain(uid, scope, e, line))   # nothing answered: nothing to take back
                last = now["step"]["id"] if now["status"] == "parked" and now["step"] else (now["answered"] or [None])[-1]
                answers.pop(last, None)
            else:
                current = quests.evaluate(e, line, {k: v for k, v in answers.items() if k != b["step"]}, tools)
                if current["step"] is None or current["step"]["id"] != b["step"]:
                    # not the question the line is waiting at (a stale page): show where the line really stands
                    current = quests.evaluate(e, line, answers, tools)
                    status = {"asking": "active"}.get(current["status"], current["status"])
                    return quest_body(scope, line, row, current, status if row else "offered", gate=gate, chain=line_chain(uid, scope, e, line))
                answers[b["step"]] = {k: b[k] for k in ("choice", "text", "values") if k in b}
            state = quests.evaluate(e, line, answers, tools)
            status = {"asking": "active", "confirmed": "done"}.get(state["status"], state["status"])
            saved = save_row(uid, scope, line, row, status, keep(uid, scope, line["id"], answers))
            return quest_body(scope, line, saved, state, status, gate=gate, chain=line_chain(uid, scope, e, line))
        return _no_store(await run_in_threadpool(work))

    def chain_view(uid: int, scope: str, e: Estate, line: dict[str, Any], checks: list[str] | None = None) -> dict[str, Any] | None:
        """The chain of a line as it stands for this player on this estate (also when the line's finding is gone)."""
        a = findings.analyse(e)
        ls = quests.lines(e, a)
        all_chains = chains.build(e, a, ls)
        found = chains.stage_of(all_chains).get(line["id"])
        ch = found[0] if found else chains.of_checks(all_chains, checks or (quests.check_ids(line) if line["finding"].get("data") else []))
        if ch is None:
            return None
        stand = Standing(a, store().quests(uid, scope))
        return chains.view(e, a, ch, stand.resolved, stand.deferred, {x["id"]: x["title"] for x in ls})

    def line_chain(uid: int, scope: str, e: Estate, line: dict[str, Any]) -> dict[str, Any] | None:
        v = chain_view(uid, scope, e, line)
        stage = next((s for s in v["stages"] if line["id"] in s["quest_ids"]), None) if v else None
        if v is None or stage is None:
            return None
        n = v["stages"].index(stage) + 1
        return {"id": v["id"], "title": v["title"], "stage": stage["stage"], "position": n, "total": v["total"], "label": f"{n} of {v['total']}: {stage['stage']}"}

    def ready_line(e: Estate, scope: str, uid: int, quest_id: str) -> tuple[dict[str, Any], dict[str, Any] | None, dict[str, Any]]:
        line, row, _, tools = find_line(e, scope, uid, quest_id)
        state = quests.evaluate(e, line, answers_of(uid, scope, row, quest_id), tools)
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

    def settle(uid: int, scope: str, line: dict[str, Any], after: Estate, real: bool, orphan: bool = False) -> dict[str, Any]:
        """Reads the outcome from a fresh estate: which of the line's checks NetBox now confirms, and what that pays.

        A check counts as confirmed only when the fresh analysis makes it and it passes. A check the analysis no longer
        makes (the object is gone, or is no longer of that kind) confirms nothing; a line left with only such checks is
        closed and forgotten, unpaid. `orphan`: the line's finding is gone, so a check of it that is still open belongs
        to another line now. What a line has had confirmed stays counted when the fix is undone later (it is never
        reopened or paid again). The row is read, paid and saved in one store transaction, and a check pays once per
        player, so two requests at the same moment cannot both be paid."""
        st = store()
        a = findings.analyse(after)
        whole = not (after.truncated or after.unreadable)
        with st.atomic():
            row = st.quests(uid, scope).get(line["id"])
            base = base_checks(line, row)
            said = row["answers"] if row else {}
            before = list(row["verified"]) if row else []
            waived = set(quests.waived(line, said))   # "there is none, on purpose": answered, never paid
            state = {cid: a.check(cid, line["site_id"]) for cid in base}
            if line["kind"] == "device_unreachable":
                # how a device is managed is written to NetBox where NetBox can hold it: what NetBox then shows is confirmed
                waived = {cid for cid in waived if state.get(cid) is not True}
            now = [cid for cid in base if state[cid] is True and cid not in waived]
            newly = [cid for cid in now if cid not in before]
            # what is counted stays counted; an open check is one NetBox shows as open (or one it could not look at)
            still = [cid for cid in base if cid not in waived and cid not in before
                     and (state[cid] is False and not orphan or state[cid] is None and not whole)]
            verified = list(dict.fromkeys(before + now))
            complete = not still and bool(verified)
            moot = real and not still and not verified and any(cid not in waived for cid in base)
            xp = potions = 0
            unlocked: list[str] = []
            if real and newly:
                paid = st.mark_paid(uid, newly)
                if paid:
                    signature = f"{line['kind']}:{line['site_id']}"
                    raw = st.rewards(uid)
                    # the share is of what the line still stands for: a check that went away is not part of the errand
                    stands = [cid for cid in base if state[cid] is not None or cid in before]
                    xp = xp_for(line["xp"], len(paid), len(stands), raw["repeats"].get(signature, 0))
                    before_level = 1 + raw["xp"] // LEVEL_XP
                    st.add_reward(uid, line["id"], "xp", xp, signature)
                    if 1 + (raw["xp"] + xp) // LEVEL_XP > before_level:
                        potions = 1
                        st.add_reward(uid, line["id"], "potion", 1, "level")
                    tool = TOOL_FOR_RANK.get(line["rank"])
                    if complete and tool and tool not in raw["tools"]:
                        st.add_reward(uid, line["id"], "tool", 1, "tool", item=tool)
                        unlocked.append(tool)
            # The whole record: when this check leaves NetBox showing every stage of the line's chain, the chain pays
            # a bonus above its stages, once per player, by the share of its stages this player had confirmed (at
            # least two: one stage is a loose stage). A chain NetBox showed complete all along pays nobody.
            chain_xp = 0
            if real and newly:
                ch = chains.of_checks(chains.build(after, a, quests.lines(after, a)), base)
                bonus = chains.CHAIN_XP.get(ch["kind"], 0) if ch else 0
                if ch and bonus and chains.verified(a, ch):
                    mine = st.paid(uid)
                    played = sum(1 for x in ch["stages"] if any(cid in mine for cid in x["checks"]))
                    stages = sum(1 for x in ch["stages"] if x["checks"])
                    if played >= 2 and st.mark_paid(uid, [ch["id"]]):
                        raw = st.rewards(uid)
                        signature = f"chain:{ch['kind']}:{ch['site_id']}"
                        chain_xp = round(bonus * played / stages * REPEAT[min(raw["repeats"].get(signature, 0), len(REPEAT) - 1)])
                        st.add_reward(uid, line["id"], "xp", chain_xp, signature)
                        if 1 + raw["xp"] // LEVEL_XP < 1 + (raw["xp"] + chain_xp) // LEVEL_XP:
                            potions += 1
                            st.add_reward(uid, line["id"], "potion", 1, "level")
                        xp += chain_xp
            wrote = real or scope == "practice"
            if moot:
                st.delete_quest(uid, scope, line["id"])
                saved = {**(row or {"quest_id": line["id"], "site_id": line["site_id"], "answers": {}, "checks": base, "verified": [],
                                    "snapshot": snapshot(line), "updated_at": None}), "status": "dismissed"}
            else:
                if complete:
                    status = "done" if wrote else "practised"
                elif row is not None and row["status"] == "dismissed" and not wrote:
                    status = "dismissed"
                else:
                    status = "active"
                # What the player said is kept: a check that found nothing new, a dry run and a refused write change
                # nothing, so the line stands where it stood. Only the answers about what is now confirmed go (the rest
                # of an errand is asked about, not the part that is done).
                answers = quests.prune(said, now) if status == "active" and wrote else said
                saved = save_row(uid, scope, line, row, status, answers, verified=verified if wrote else before)
        undone = [cid for cid in before if state.get(cid) is False]
        return {"saved": saved, "complete": complete, "newly": newly, "xp": xp, "potions": potions, "unlocked": unlocked, "open": sorted(still),
                "moot": moot, "undone": undone, "was_done": row is not None and row["status"] == "done", "chain_xp": chain_xp}

    def standing_still(uid: int, scope: str, line: dict[str, Any]) -> dict[str, Any]:
        """A settlement that changes nothing: the line stays exactly where it stood (a write NetBox did not take)."""
        row = store().quests(uid, scope).get(line["id"])
        saved = row if row is not None else save_row(uid, scope, line, None, "active", {})
        return {"saved": saved, "complete": False, "newly": [], "xp": 0, "potions": 0, "unlocked": [], "open": [], "moot": False,
                "undone": [], "was_done": False, "chain_xp": 0}

    def verdict(uid: int, scope: str, line: dict[str, Any], s: dict[str, Any], mode: str, verified: bool, message: str,
                result: dict[str, Any] | None, trace: str | None, e: Estate, gate: Gate | None = None) -> dict[str, Any]:
        st = store()
        rows = st.quests(uid, "netbox")
        prows = st.quests(uid, "practice")
        state = None
        shown = line
        if s["saved"]["status"] == "active":
            # the rest of the line, as the estate stands now (an errand goes on with the labels still missing)
            fresh = next((x for x in quests.lines(e, findings.analyse(e)) if x["id"] == line["id"]), None)
            if fresh is not None:
                tools = frozenset(["label_maker", "cable_tracer"] if scope == "practice" else st.rewards(uid)["tools"])
                shown, state = fresh, quests.evaluate(e, fresh, answers_of(uid, scope, s["saved"], line["id"]), tools)
        status = s["saved"]["status"]
        if status == "active" and state is not None:
            status = {"asking": "active"}.get(state["status"], state["status"])   # as the quest log would show it
        # the line's chain, as the estate stands now: where the player is, and the record so far read back from NetBox
        ch = chain_view(uid, scope, e, shown, s["saved"]["checks"])
        told = (verified or mode == "practice") and (s["newly"] or s["complete"])
        going_on = ch is not None and ch["total"] > 1 and ch["status"] != "complete"
        if s["complete"] and verified and line["site_id"] in e.sites and not going_on:
            # the small step, and where it leaves the site. (In the middle of a chain the next stage is said instead:
            # a circuit that has just been plugged in brings its own questions, and the percentage would only dip.)
            comp = findings.analyse(e).completeness(line["site_id"], Standing(findings.analyse(e), st.quests(uid, scope)).settled)
            message += f" {e.sites[line['site_id']]['name']} is now {comp['percent']} % complete."
        if ch is not None and told:
            if ch["status"] == "complete" and ch["total"] > 1:
                message += f" That completes the chain \"{ch['title']}\"." + (f" {ch['summary']}" if ch["summary"] else "")
            elif ch["status"] == "open_questions":
                message += f" The chain \"{ch['title']}\" stays open at {ch['label']}."
            elif going_on and s["complete"]:
                message += f" Next in the chain \"{ch['title']}\": {ch['label']}."
        return {"quest": quest_body(scope, shown, s["saved"], state, status, gate=gate, chain=line_chain(uid, scope, e, shown)), "chain": ch,
                "chain_xp": s["chain_xp"], "mode": mode, "verified": verified,
                "complete": s["complete"], "xp_awarded": s["xp"], "potions_awarded": s["potions"], "message": message,
                "can_now": line["outcome"] if s["complete"] and verified and not s["was_done"] else None, "unlocked": s["unlocked"],
                "trace": trace, "result": result,
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
            elif t.get("device"):
                names.append(f"{t['device'].get('name') or 'an unnamed device'} {t.get('name') or ''}".strip())
            elif t.get("name") or t.get("display"):
                names.append(str(t.get("name") or t.get("display")))   # a circuit's far side: the site or network it ends at
        return f"NetBox's cable trace from {start} reaches {' and '.join(names) or 'an open end'} in {len(segs)} hop{'s' if len(segs) != 1 else ''}."

    def refusal_reason(result: dict[str, Any]) -> str:
        err = next((o.get("error") for o in result["ops"] if o["status"] == "error" and o.get("error")), None)
        return (err or {}).get("message") or "NetBox gave no reason."

    @app.post("/api/game/quests/apply")
    async def apply(request: Request, body: Json):
        uid = await run_in_threadpool(require, request, True)
        b = _valid("QuestRef", body)
        async with applying.setdefault(uid, asyncio.Lock()):
            return await apply_one(request, uid, b)

    async def apply_one(request: Request, uid: int, b: dict[str, Any]) -> JSONResponse:
        scope = b["scope"]
        e, _ = await estate_of(scope, uid)
        line, row, state = await run_in_threadpool(ready_line, e, scope, uid, b["quest_id"])
        proposal = state["proposal"]
        gate = await gate_of(scope, uid, request, fresh=True)
        if scope == "practice":
            result, after = sim_result(e, proposal, "apply")
            ok = all(o["status"] != "error" for o in result["ops"])
            if ok:
                result["applied"] = True
                await run_in_threadpool(lambda: store().set_practice_ops(uid, store().practice_ops(uid) + proposal["ops"]))
            s = await run_in_threadpool(settle, uid, scope, line, after if ok else e, False)
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
            s = await run_in_threadpool(settle, uid, scope, line, after if ok else e, False)
            msg = (f"Practice run, nothing was written: {blocked} No XP for a practice run." if ok
                   else f"NetBox would refuse that change: {refusal_reason(result)}")
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
        wrote_some = any(o["status"] == "ok" for o in result["ops"])
        if res.applied:
            vault.pop((uid, scope, line["id"]), None)   # what was typed is in NetBox now: the proxy forgets it
        if not res.applied and not wrote_some:
            # NetBox took nothing (the object changed or went since the question was asked, or NetBox refused the
            # value): nothing is confirmed and nothing is paid. The estate is read again before the next question, so
            # the line asks with what NetBox holds now.
            drop_estate()
            s = await run_in_threadpool(standing_still, uid, scope, line)
            msg = f"NetBox did not take that change, and nothing was written: {refusal_reason(result)}"
            return _no_store(await run_in_threadpool(verdict, uid, scope, line, s, "real", False, msg, result, None, e, gate))
        # never the client's word: NetBox is read again, at the sites this change reached
        after = await site_estate(touched_sites(e, line, proposal["ops"]), force=True)
        s = await run_in_threadpool(settle, uid, scope, line, after, True)
        trace = await trace_text(after, proposal) if res.applied else None
        if not res.applied:
            msg = f"NetBox took only part of that change: {refusal_reason(result)}"
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
        known = next((x for x in await run_in_threadpool(lambda: quests.lines(kept, findings.analyse(kept))) if x["id"] == b["quest_id"]), None)
        if row is None and known is None:
            raise ApiError("not_found", "That quest is not open.", {"quest_id": b["quest_id"]})
        # the line's own site is read again (not the whole estate), and not more often than every few seconds
        after = await site_estate({row["site_id"] if row else known["site_id"]})
        line = next((x for x in await run_in_threadpool(lambda: quests.lines(after, findings.analyse(after))) if x["id"] == b["quest_id"]), None)
        orphan = line is None
        if line is None:
            if row is None:
                raise ApiError("not_found", "That quest is not open.", {"quest_id": b["quest_id"]})
            snap = row["snapshot"]
            line = {**snap, "finding": {**snap["finding"], "data": {}}, "site_id": row["site_id"]}
        s = await run_in_threadpool(settle, uid, "netbox", line, after, True, orphan)
        if s["moot"]:
            msg = ("This no longer applies: what the line was about is not in NetBox any more (deleted, switched off, moved, or "
                   "asked about in another line). The line is closed; nothing is paid for it.")
        elif s["was_done"] and not s["newly"]:
            msg = ("NetBox no longer shows what you recorded here. It stays counted as done; nothing is reopened or paid again."
                   if s["undone"] else "NetBox still confirms it.")
        elif s["complete"]:
            msg = "NetBox confirms it."
        elif s["newly"]:
            msg = f"NetBox confirms {len(s['newly'])} more; the rest stays open."
        elif after.truncated or after.unreadable:
            msg = (f"The game could not read everything from NetBox ({', '.join(sorted(set(after.truncated + after.unreadable)))}), "
                   "so nothing could be checked. The line stays as it is.")
        else:
            msg = "NetBox does not show it yet."
        gate = await gate_of("netbox", uid, request)
        return _no_store(await run_in_threadpool(verdict, uid, "netbox", line, s, "real", bool(s["newly"]) or (s["complete"] and not s["undone"]), msg, None, None, after, gate))

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
