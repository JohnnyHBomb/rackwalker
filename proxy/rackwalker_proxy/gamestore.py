"""The game's own state: one SQLite file owned by the proxy (stdlib sqlite3, no extra dependency).

Game state is never written into NetBox. NetBox is the inventory; this file holds what the game remembers about a
player, keyed by the player's NetBox user id: profile, save slots (an autosave slot plus manual slots), settings and
dialogue flags. Sign-in sessions live here too, stored only as a SHA-256 of the cookie value.

The schema is versioned with `PRAGMA user_version`; `MIGRATIONS[i]` (SQL, or a function of the connection where rows
have to be read and rewritten) takes the file from version i to i + 1 inside one transaction. A file written by a newer RackWalker (user_version above what this code knows) is refused, never
rewritten. The default location is `<repo>/data/game.sqlite3` (gitignored); RACKWALKER_STATE_DIR moves it.

Rows are JSON checked against the contract when they are read, not only when they are written, so a damaged row or
one in an older shape never reaches the game: settings keep every part that is still valid and take the default for
the rest (a newly added setting therefore reaches existing players with its default); a save slot or flag that does
not match the contract is left out of the answer (the row stays in the file) and logged.
"""
from __future__ import annotations

import copy
import hashlib
import json
import logging
import re
import secrets
import sqlite3
import threading
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from . import schema

log = logging.getLogger("rackwalker_proxy.gamestore")

def split_management_rows(db: sqlite3.Connection) -> None:
    rows = db.execute("SELECT user_id, scope, quest_id, site_id, status, answers, checks, verified, snapshot, updated_at FROM quest "
                      "WHERE quest_id LIKE 'q:device\\_unreachable:site:%' ESCAPE '\\'").fetchall()
    for user_id, scope, quest_id, site_id, status, answers, checks, verified, snapshot, updated_at in rows:
        try:
            ans, chk, ver, snap = json.loads(answers), json.loads(checks), json.loads(verified), json.loads(snapshot)
            if not (isinstance(ans, dict) and isinstance(chk, list) and isinstance(ver, list) and isinstance(snap, dict)):
                raise ValueError("not the expected shape")
        except ValueError:
            continue   # a damaged row is left as it is (it is left out when read, as before)
        names = {r.get("id"): r.get("name") for r in (snap.get("finding") or {}).get("evidence") or [] if isinstance(r, dict)}
        for check in chk:
            m = re.fullmatch(r"device:(\d+):primary_ip", check) if isinstance(check, str) else None
            if m is None:
                continue
            did = m.group(1)
            mine = {k: v for k, v in ans.items() if k.rsplit("_", 1)[-1] == did}
            said_none = isinstance(mine.get(f"ip_{did}"), dict) and mine[f"ip_{did}"].get("choice") == "none"
            if said_none:
                mine = {f"mode_{did}": {"choice": "unmanaged"}}
            elif any(k.startswith(("ip_", "addr_", "port_")) for k in mine) and not (isinstance(mine.get(f"ip_{did}"), dict)
                                                                                    and mine[f"ip_{did}"].get("choice") == "unknown"):
                mine[f"mode_{did}"] = {"choice": "local"}   # an address was being given: it is managed locally
            done = check in ver
            if not mine and not done:
                continue   # never touched for this device: the new line is simply offered
            unknown = isinstance(mine.get(f"ip_{did}"), dict) and mine[f"ip_{did}"].get("choice") == "unknown"
            new_status = "done" if done else "parked" if unknown and status == "parked" else "active"
            name = names.get(int(did)) or f"device {did}"
            new_id = f"q:device_unreachable:device:{did}"
            new_snap = {**snap, "id": new_id, "title": f"How {name} is managed", "xp": 40,
                        "finding": {**(snap.get("finding") or {}), "id": new_id[2:], "evidence": [{"kind": "device", "id": int(did), "name": name}],
                                    "title": f"NetBox does not say how {name} is managed, or how to reach it.", "data": {}}}
            db.execute("INSERT OR IGNORE INTO quest(user_id, scope, quest_id, site_id, status, answers, checks, verified, snapshot, updated_at) "
                       "VALUES (?,?,?,?,?,?,?,?,?,?)",
                       (user_id, scope, new_id, site_id, new_status, json.dumps(mine, sort_keys=True), json.dumps([check]),
                        json.dumps([check] if done else []), json.dumps(new_snap, sort_keys=True), updated_at))
        db.execute("DELETE FROM quest WHERE user_id=? AND scope=? AND quest_id=?", (user_id, scope, quest_id))


MIGRATIONS: list[Any] = [
    # 0 -> 1: players, sessions, saves, settings, flags.
    """
    CREATE TABLE player (
        user_id      INTEGER PRIMARY KEY,           -- NetBox user id (0 = the single-user local mode)
        username     TEXT NOT NULL,
        display_name TEXT,
        created_at   TEXT NOT NULL,
        seen_at      TEXT NOT NULL
    );
    CREATE TABLE session (
        id_hash    TEXT PRIMARY KEY,                -- sha256 of the cookie value, never the value itself
        user_id    INTEGER NOT NULL REFERENCES player(user_id) ON DELETE CASCADE,
        csrf       TEXT NOT NULL,
        created_at REAL NOT NULL,
        expires_at REAL NOT NULL
    );
    CREATE INDEX session_user ON session(user_id);
    CREATE TABLE save (
        user_id    INTEGER NOT NULL REFERENCES player(user_id) ON DELETE CASCADE,
        slot       TEXT NOT NULL,
        rev        INTEGER NOT NULL,
        data       TEXT NOT NULL,                   -- SaveData JSON
        updated_at TEXT NOT NULL,
        PRIMARY KEY (user_id, slot)
    );
    CREATE TABLE settings (
        user_id INTEGER PRIMARY KEY REFERENCES player(user_id) ON DELETE CASCADE,
        data    TEXT NOT NULL                       -- GameSettings JSON
    );
    CREATE TABLE flag (
        user_id INTEGER NOT NULL REFERENCES player(user_id) ON DELETE CASCADE,
        name    TEXT NOT NULL,
        value   TEXT NOT NULL,                      -- JSON scalar
        PRIMARY KEY (user_id, name)
    );
    """,
    # 1 -> 2: quests. The quest log (one row per quest line a player has touched), the reward ledger (XP, potions and
    # tools, one row per award, so a total is always the sum of what was really earned), the player's practice-island
    # changes, and which sites' "Confusion" they have acknowledged. Nothing of version 1 changes: old saves keep working.
    """
    CREATE TABLE quest (
        user_id    INTEGER NOT NULL REFERENCES player(user_id) ON DELETE CASCADE,
        scope      TEXT NOT NULL,                   -- 'netbox' or 'practice'
        quest_id   TEXT NOT NULL,                   -- 'q:' + the finding's stable id
        site_id    INTEGER NOT NULL,
        status     TEXT NOT NULL,                   -- active, ready, parked, dismissed, practised, done
        answers    TEXT NOT NULL,                   -- JSON: step id -> the player's answer
        checks     TEXT NOT NULL,                   -- JSON list: every check the line stood for when first offered
        verified   TEXT NOT NULL,                   -- JSON list: the checks NetBox has confirmed (and paid XP for)
        snapshot   TEXT NOT NULL,                   -- JSON: the line as last shown (title, purpose, finding)
        updated_at TEXT NOT NULL,
        PRIMARY KEY (user_id, scope, quest_id)
    );
    CREATE TABLE reward (
        id        INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id   INTEGER NOT NULL REFERENCES player(user_id) ON DELETE CASCADE,
        quest_id  TEXT NOT NULL,
        kind      TEXT NOT NULL,                    -- 'xp', 'potion' or 'tool'
        amount    INTEGER NOT NULL,
        item      TEXT,                             -- the tool's id
        signature TEXT NOT NULL,                    -- what kind of fix it was (repeats earn less)
        at        TEXT NOT NULL
    );
    CREATE INDEX reward_user ON reward(user_id);
    CREATE TABLE practice (
        user_id INTEGER PRIMARY KEY REFERENCES player(user_id) ON DELETE CASCADE,
        ops     TEXT NOT NULL                       -- JSON list of change ops applied to the practice island
    );
    CREATE TABLE boss (
        user_id INTEGER NOT NULL REFERENCES player(user_id) ON DELETE CASCADE,
        scope   TEXT NOT NULL,
        site_id INTEGER NOT NULL,
        at      TEXT NOT NULL,
        PRIMARY KEY (user_id, scope, site_id)
    );
    """,
    # 2 -> 3: what a session may do, and a reward ledger that cannot pay twice.
    # A session records whether the NetBox token it was started with may write (a read-only token plays in dry-run) and
    # that token's NetBox id (not a secret), so the proxy can notice when the token is deleted. Sessions from before
    # this step read as "may not write" until the player signs in again.
    # `paid` holds every check NetBox has confirmed for a player, once: XP is paid only for a row this table accepts,
    # so the same fix is never paid twice (two requests at once, a line that changed its id, a fix undone and redone).
    # It starts with what the quest log already counts as confirmed.
    """
    ALTER TABLE session ADD COLUMN can_write INTEGER NOT NULL DEFAULT 0;
    ALTER TABLE session ADD COLUMN token_id INTEGER;
    CREATE TABLE paid (
        user_id  INTEGER NOT NULL REFERENCES player(user_id) ON DELETE CASCADE,
        check_id TEXT NOT NULL,                     -- a check id of the scanner, such as 'device:12:serial'
        at       TEXT NOT NULL,
        PRIMARY KEY (user_id, check_id)
    );
    INSERT OR IGNORE INTO paid(user_id, check_id, at)
        SELECT quest.user_id, json_each.value, quest.updated_at
        FROM quest, json_each(CASE WHEN json_valid(quest.verified) AND json_type(quest.verified) = 'array' THEN quest.verified ELSE '[]' END)
        WHERE quest.scope = 'netbox' AND json_each.type = 'text';
    """,
    # 3 -> 4: how a device is managed is asked device by device (it is a stage of that device's chain), where it was one
    # line for a whole site ("how to reach the kit"). Each such row becomes one row per device it had an answer or a
    # confirmed check for, with that device's answers; "It has none: nobody logs in to it" becomes the answer "it is
    # not managed at all" to the new first question, so it is not asked again and can be written to the device.
    split_management_rows,
]
SCHEMA_VERSION = len(MIGRATIONS)
SESSION_SECONDS = 7 * 24 * 3600
MAX_FLAGS = 500

DEFAULT_SETTINGS: dict[str, Any] = {
    "hint_level": "normal",
    "audio": {"muted": False, "volume": 0.6},
    "reduce_motion": False,
    "keys": {
        "up": ["ArrowUp", "KeyW"], "down": ["ArrowDown", "KeyS"], "left": ["ArrowLeft", "KeyA"],
        "right": ["ArrowRight", "KeyD"], "interact": ["Enter", "Space", "KeyE"], "cancel": ["Escape", "Backspace"],
        "menu": ["KeyM"], "zoom_in": ["Equal", "NumpadAdd"], "zoom_out": ["Minus", "NumpadSubtract"], "search": ["Slash"],
        "rotate": ["KeyR"], "trace": ["KeyT"], "unrack": ["Delete"], "undo": ["KeyZ"],
    },
}


class StoreError(Exception):
    pass


def _json(text: str) -> tuple[bool, Any]:
    try:
        return True, json.loads(text)
    except (ValueError, TypeError):
        return False, None


# Actions added in contract 2.2.0: optional in KeyBindings, so settings written before them stay valid.
OPTIONAL_KEY_ACTIONS = ("zoom_in", "zoom_out", "search", "rotate", "trace", "unrack", "undo")


def duplicate_key(keys: dict[str, list[str]]) -> tuple[str, str, str] | None:
    """(key, first action, second action) when one key is bound to two actions, else None."""
    seen: dict[str, str] = {}
    for action, codes in keys.items():
        for k in codes:
            if k in seen and seen[k] != action:
                return k, seen[k], action
            seen[k] = action
    return None


def upgrade_settings(raw: Any) -> dict[str, Any]:
    """Stored settings as a valid GameSettings body: every part of `raw` that is still valid on its own is kept, every
    other part (missing, from an older shape, damaged) takes its default. Keys that end up bound to two actions fall
    back to the default bindings as a whole."""
    out = copy.deepcopy(DEFAULT_SETTINGS)
    if not isinstance(raw, dict):
        return out

    def keep(path: tuple[str, ...], value: Any) -> None:
        trial = copy.deepcopy(out)
        node = trial
        for k in path[:-1]:
            node = node[k]
        node[path[-1]] = value
        if not schema.errors("GameSettings", trial, limit=1):
            node = out
            for k in path[:-1]:
                node = node[k]
            node[path[-1]] = value

    for key, default in DEFAULT_SETTINGS.items():
        if key not in raw:
            continue
        if isinstance(default, dict) and isinstance(raw[key], dict):
            for sub in default:
                if sub in raw[key]:
                    keep((key, sub), raw[key][sub])
        else:
            keep((key,), raw[key])
    if duplicate_key(out["keys"]) and isinstance(raw.get("keys"), dict):
        # Settings from before an action existed: the player's own keys win, the new action's default gives way.
        mine = {k for a, codes in out["keys"].items() if a in raw["keys"] for k in codes}
        for a in list(out["keys"]):
            if a not in raw["keys"]:
                left = [k for k in out["keys"][a] if k not in mine]
                if left:
                    out["keys"][a] = left
                elif a in OPTIONAL_KEY_ACTIONS:
                    del out["keys"][a]  # every default key of a newer action is the player's: it waits for a remap
    if duplicate_key(out["keys"]):
        out["keys"] = copy.deepcopy(DEFAULT_SETTINGS["keys"])
    return out


def _now_iso() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def statements(script: str) -> list[str]:
    """Splits a migration into single statements. `executescript` cannot be used: it COMMITs the open transaction, so
    a failing step would leave half a migration behind. A `;` ends a statement only where SQLite agrees the statement
    is complete (`sqlite3.complete_statement`), so a `;` inside a string, a comment or a trigger body never splits.
    Pieces that hold only comments or whitespace are dropped."""
    out: list[str] = []
    buf = ""
    for ch in script:
        buf += ch
        if ch == ";" and sqlite3.complete_statement(buf):
            out.append(buf.strip())
            buf = ""
    if buf.strip() and not _only_comments(buf):
        out.append(buf.strip())
    return [st for st in out if not _only_comments(st)]


def _only_comments(text: str) -> bool:
    rest = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
    rest = "\n".join(line.split("--", 1)[0] for line in rest.splitlines())
    return not rest.strip(" \t\r\n;")


def migrate(db: sqlite3.Connection, steps: list[Any], label: object = "game state file") -> int:
    """Brings `db` (autocommit mode) to version len(steps): `steps[i]` takes it from version i to i + 1 inside one
    transaction, so a failing step leaves the file at the version before it. A newer file is refused untouched."""
    have = db.execute("PRAGMA user_version").fetchone()[0]
    if have > len(steps):
        raise StoreError(f"game state file {label} has schema version {have}; this RackWalker knows up to "
                         f"{len(steps)}. Refusing to touch it (update RackWalker).")
    for v in range(have, len(steps)):
        db.execute("BEGIN IMMEDIATE")
        try:
            if callable(steps[v]):
                steps[v](db)   # a step that has to read and rewrite rows (JSON inside them)
            else:
                for stmt in statements(steps[v]):
                    db.execute(stmt)
            db.execute(f"PRAGMA user_version = {v + 1}")
            db.execute("COMMIT")
        except BaseException:
            db.execute("ROLLBACK")
            raise
    return len(steps)


@dataclass(frozen=True)
class Session:
    user_id: int
    csrf: str
    expires_at: float
    can_write: bool = True      # the NetBox token this session was started with may write
    token_id: int | None = None  # that token's id in NetBox (not a secret), when NetBox told it


class GameStore:
    """Thread-safe (one connection, one lock); every call is a short transaction."""

    def __init__(self, path: Path | str, clock=time.time):
        self.path = Path(path) if str(path) != ":memory:" else path
        if isinstance(self.path, Path):
            self.path.parent.mkdir(parents=True, exist_ok=True)
        self.clock = clock
        self._lock = threading.RLock()
        self._depth = 0   # open `_tx` blocks of the thread that holds the lock: only the outermost begins and commits
        self._db = sqlite3.connect(str(path), check_same_thread=False, isolation_level=None)
        self._db.row_factory = sqlite3.Row
        self._db.execute("PRAGMA foreign_keys = ON")
        if isinstance(self.path, Path):
            self._db.execute("PRAGMA journal_mode = WAL")
            self.path.chmod(0o600)
        self.migrate()

    # ------------------------------------------------------------------------------------------- schema

    @property
    def version(self) -> int:
        return self._db.execute("PRAGMA user_version").fetchone()[0]

    def migrate(self) -> None:
        with self._lock:
            migrate(self._db, MIGRATIONS, self.path)

    def close(self) -> None:
        with self._lock:
            self._db.close()

    def _tx(self):
        return _Tx(self)

    def atomic(self):
        """Several store calls as one transaction: `with store.atomic(): ...`. Calls made inside by the same thread
        join it; every other thread waits. An exception rolls all of it back."""
        return _Tx(self)

    # ------------------------------------------------------------------------------------------- players, sessions

    def upsert_player(self, user_id: int, username: str, display_name: str | None) -> None:
        now = _now_iso()
        with self._tx() as db:
            db.execute("INSERT INTO player(user_id, username, display_name, created_at, seen_at) VALUES (?,?,?,?,?) "
                       "ON CONFLICT(user_id) DO UPDATE SET username=excluded.username, display_name=excluded.display_name, "
                       "seen_at=excluded.seen_at", (user_id, username, display_name, now, now))

    def player(self, user_id: int) -> dict[str, Any] | None:
        with self._tx() as db:
            r = db.execute("SELECT user_id, username, display_name FROM player WHERE user_id=?", (user_id,)).fetchone()
        return None if r is None else {"id": r["user_id"], "username": r["username"], "display_name": r["display_name"]}

    def new_session(self, user_id: int, can_write: bool = True, token_id: int | None = None,
                    token_expires: float | None = None) -> tuple[str, Session]:
        """Returns (cookie value, session). Only the hash of the cookie value is stored. A session never outlives the
        NetBox token it was started with (`token_expires`, a time on the store's clock)."""
        token, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(24)
        now = self.clock()
        until = min(now + SESSION_SECONDS, token_expires) if token_expires is not None else now + SESSION_SECONDS
        with self._tx() as db:
            db.execute("DELETE FROM session WHERE expires_at <= ?", (now,))
            db.execute("INSERT INTO session(id_hash, user_id, csrf, created_at, expires_at, can_write, token_id) VALUES (?,?,?,?,?,?,?)",
                       (_hash(token), user_id, csrf, now, until, int(can_write), token_id))
        return token, Session(user_id, csrf, until, can_write, token_id)

    def session(self, token: str | None) -> Session | None:
        if not token or len(token) > 200:
            return None
        with self._tx() as db:
            r = db.execute("SELECT user_id, csrf, expires_at, can_write, token_id FROM session WHERE id_hash=?", (_hash(token),)).fetchone()
        if r is None or r["expires_at"] <= self.clock():
            return None
        return Session(r["user_id"], r["csrf"], r["expires_at"], bool(r["can_write"]), r["token_id"])

    def update_session(self, token: str, can_write: bool, token_expires: float | None) -> None:
        """What NetBox now says about the token a session was started with: its write flag and its expiry."""
        with self._tx() as db:
            db.execute("UPDATE session SET can_write=? WHERE id_hash=?", (int(can_write), _hash(token)))
            if token_expires is not None:
                db.execute("UPDATE session SET expires_at=MIN(expires_at, ?) WHERE id_hash=?", (token_expires, _hash(token)))

    def end_user_sessions(self, user_id: int) -> list[str]:
        """Ends every session of one player (their NetBox user was deleted or deactivated). Returns the ended sessions'
        cookie hashes, so the proxy can still tell each of those browsers why."""
        with self._tx() as db:
            ended = [r["id_hash"] for r in db.execute("SELECT id_hash FROM session WHERE user_id=?", (user_id,))]
            db.execute("DELETE FROM session WHERE user_id=?", (user_id,))
        return ended

    @staticmethod
    def cookie_hash(token: str) -> str:
        return _hash(token)

    def end_session(self, token: str | None) -> None:
        if token:
            with self._tx() as db:
                db.execute("DELETE FROM session WHERE id_hash=?", (_hash(token),))

    # ------------------------------------------------------------------------------------------- game state

    def settings(self, user_id: int) -> dict[str, Any]:
        with self._tx() as db:
            r = db.execute("SELECT data FROM settings WHERE user_id=?", (user_id,)).fetchone()
        if r is None:
            return copy.deepcopy(DEFAULT_SETTINGS)
        ok, raw = _json(r["data"])
        out = upgrade_settings(raw)
        if not ok or out != raw:
            log.warning("settings of player %d did not match the contract; the invalid parts read as their defaults", user_id)
        return out

    def put_settings(self, user_id: int, data: dict[str, Any]) -> dict[str, Any]:
        with self._tx() as db:
            db.execute("INSERT INTO settings(user_id, data) VALUES (?,?) ON CONFLICT(user_id) DO UPDATE SET data=excluded.data",
                       (user_id, json.dumps(data, sort_keys=True)))
        return data

    def flags(self, user_id: int) -> dict[str, Any]:
        with self._tx() as db:
            rows = db.execute("SELECT name, value FROM flag WHERE user_id=? ORDER BY name", (user_id,)).fetchall()
        out: dict[str, Any] = {}
        for r in rows:
            ok, value = _json(r["value"])
            if ok and not schema.errors("Flags", {r["name"]: value}, limit=1):
                out[r["name"]] = value
            else:
                log.warning("flag %r of player %d does not match the contract; left out", r["name"], user_id)
        return out

    def patch_flags(self, user_id: int, patch: dict[str, Any]) -> dict[str, Any]:
        """Sets each flag; a null value deletes it. Refuses to grow past MAX_FLAGS."""
        with self._tx() as db:
            for name, value in patch.items():
                if value is None:
                    db.execute("DELETE FROM flag WHERE user_id=? AND name=?", (user_id, name))
                else:
                    db.execute("INSERT INTO flag(user_id, name, value) VALUES (?,?,?) "
                               "ON CONFLICT(user_id, name) DO UPDATE SET value=excluded.value", (user_id, name, json.dumps(value)))
            n = db.execute("SELECT COUNT(*) FROM flag WHERE user_id=?", (user_id,)).fetchone()[0]
            if n > MAX_FLAGS:
                raise _Rollback(StoreError(f"at most {MAX_FLAGS} flags per player"))
        return self.flags(user_id)

    def saves(self, user_id: int) -> list[dict[str, Any]]:
        with self._tx() as db:
            rows = db.execute("SELECT slot, rev, data, updated_at FROM save WHERE user_id=? ORDER BY slot='auto' DESC, slot",
                              (user_id,)).fetchall()
        out = []
        for r in rows:
            ok, data = _json(r["data"])
            body = {"slot": r["slot"], "rev": r["rev"], "updated_at": r["updated_at"], "data": data}
            if ok and not schema.errors("SaveSlot", body, limit=1):
                out.append(body)
            else:
                log.warning("save slot %s of player %d does not match the contract; left out (the row stays in the file)", r["slot"], user_id)
        return out

    def put_save(self, user_id: int, slot: str, data: dict[str, Any]) -> dict[str, Any]:
        now = _now_iso()
        with self._tx() as db:
            db.execute("INSERT INTO save(user_id, slot, rev, data, updated_at) VALUES (?,?,1,?,?) "
                       "ON CONFLICT(user_id, slot) DO UPDATE SET rev=rev+1, data=excluded.data, updated_at=excluded.updated_at",
                       (user_id, slot, json.dumps(data, sort_keys=True), now))
            rev = db.execute("SELECT rev FROM save WHERE user_id=? AND slot=?", (user_id, slot)).fetchone()[0]
        return {"slot": slot, "rev": rev, "updated_at": now, "data": data}


    # ------------------------------------------------------------------------------------------- quests and rewards

    def quests(self, user_id: int, scope: str) -> dict[str, dict[str, Any]]:
        """The player's quest rows of one scope, by quest id. A row that is not valid JSON is left out and logged."""
        with self._tx() as db:
            rows = db.execute("SELECT quest_id, site_id, status, answers, checks, verified, snapshot, updated_at FROM quest "
                              "WHERE user_id=? AND scope=? ORDER BY quest_id", (user_id, scope)).fetchall()
        out: dict[str, dict[str, Any]] = {}
        for r in rows:
            parts = [_json(r[k]) for k in ("answers", "checks", "verified", "snapshot")]
            if not all(ok for ok, _ in parts) or not isinstance(parts[0][1], dict) or not isinstance(parts[3][1], dict) \
                    or not all(isinstance(p[1], list) for p in parts[1:3]):
                log.warning("quest row %s of player %d is damaged; left out", r["quest_id"], user_id)
                continue
            out[r["quest_id"]] = {"quest_id": r["quest_id"], "site_id": r["site_id"], "status": r["status"], "answers": parts[0][1],
                                  "checks": parts[1][1], "verified": parts[2][1], "snapshot": parts[3][1], "updated_at": r["updated_at"]}
        return out

    def put_quest(self, user_id: int, scope: str, row: dict[str, Any]) -> dict[str, Any]:
        now = _now_iso()
        with self._tx() as db:
            db.execute("INSERT INTO quest(user_id, scope, quest_id, site_id, status, answers, checks, verified, snapshot, updated_at) "
                       "VALUES (?,?,?,?,?,?,?,?,?,?) ON CONFLICT(user_id, scope, quest_id) DO UPDATE SET status=excluded.status, "
                       "answers=excluded.answers, checks=excluded.checks, verified=excluded.verified, snapshot=excluded.snapshot, "
                       "updated_at=excluded.updated_at",
                       (user_id, scope, row["quest_id"], row["site_id"], row["status"], json.dumps(row["answers"], sort_keys=True),
                        json.dumps(row["checks"]), json.dumps(row["verified"]), json.dumps(row["snapshot"], sort_keys=True), now))
        return {**row, "updated_at": now}

    def delete_quest(self, user_id: int, scope: str, quest_id: str) -> None:
        with self._tx() as db:
            db.execute("DELETE FROM quest WHERE user_id=? AND scope=? AND quest_id=?", (user_id, scope, quest_id))

    def move_quest(self, user_id: int, scope: str, old_id: str, row: dict[str, Any]) -> None:
        """A begun line takes the id its finding now has: the old row goes, `row` (with the new quest_id) is stored."""
        with self._tx():
            self.delete_quest(user_id, scope, old_id)
            self.put_quest(user_id, scope, row)

    def mark_paid(self, user_id: int, check_ids: list[str]) -> list[str]:
        """Records checks as confirmed-and-paid for a player and returns those that were not recorded before: each
        check pays once, whoever asks and however often."""
        fresh: list[str] = []
        with self._tx() as db:
            for cid in check_ids:
                cur = db.execute("INSERT OR IGNORE INTO paid(user_id, check_id, at) VALUES (?,?,?)", (user_id, cid, _now_iso()))
                if cur.rowcount == 1:
                    fresh.append(cid)
        return fresh

    def paid(self, user_id: int) -> set[str]:
        """Every check (and chain) this player has been paid for."""
        with self._tx() as db:
            return {r["check_id"] for r in db.execute("SELECT check_id FROM paid WHERE user_id=?", (user_id,))}

    def clear_quests(self, user_id: int, scope: str) -> None:
        with self._tx() as db:
            db.execute("DELETE FROM quest WHERE user_id=? AND scope=?", (user_id, scope))
            db.execute("DELETE FROM boss WHERE user_id=? AND scope=?", (user_id, scope))

    def add_reward(self, user_id: int, quest_id: str, kind: str, amount: int, signature: str, item: str | None = None) -> None:
        with self._tx() as db:
            db.execute("INSERT INTO reward(user_id, quest_id, kind, amount, item, signature, at) VALUES (?,?,?,?,?,?,?)",
                       (user_id, quest_id, kind, amount, item, signature, _now_iso()))

    def rewards(self, user_id: int) -> dict[str, Any]:
        """Totals from the ledger: XP, potions, tools (in the order earned), and how often each kind of fix has paid XP."""
        with self._tx() as db:
            rows = db.execute("SELECT kind, amount, item, signature FROM reward WHERE user_id=? ORDER BY id", (user_id,)).fetchall()
        xp = sum(r["amount"] for r in rows if r["kind"] == "xp")
        tools: list[str] = []
        for r in rows:
            if r["kind"] == "tool" and r["item"] and r["item"] not in tools:
                tools.append(r["item"])
        repeats: dict[str, int] = {}
        for r in rows:
            if r["kind"] == "xp":
                repeats[r["signature"]] = repeats.get(r["signature"], 0) + 1
        return {"xp": xp, "potions": sum(r["amount"] for r in rows if r["kind"] == "potion"), "tools": tools, "repeats": repeats}

    def practice_ops(self, user_id: int) -> list[dict[str, Any]]:
        with self._tx() as db:
            r = db.execute("SELECT ops FROM practice WHERE user_id=?", (user_id,)).fetchone()
        ok, ops = _json(r["ops"]) if r else (True, [])
        return ops if ok and isinstance(ops, list) else []

    def set_practice_ops(self, user_id: int, ops: list[dict[str, Any]]) -> None:
        with self._tx() as db:
            db.execute("INSERT INTO practice(user_id, ops) VALUES (?,?) ON CONFLICT(user_id) DO UPDATE SET ops=excluded.ops",
                       (user_id, json.dumps(ops)))

    def boss_acks(self, user_id: int, scope: str) -> set[int]:
        with self._tx() as db:
            return {r["site_id"] for r in db.execute("SELECT site_id FROM boss WHERE user_id=? AND scope=?", (user_id, scope))}

    def ack_boss(self, user_id: int, scope: str, site_id: int) -> None:
        with self._tx() as db:
            db.execute("INSERT OR IGNORE INTO boss(user_id, scope, site_id, at) VALUES (?,?,?,?)", (user_id, scope, site_id, _now_iso()))


class _Rollback(Exception):
    def __init__(self, cause: Exception):
        self.cause = cause


class _Tx:
    def __init__(self, store: GameStore):
        self.store = store

    def __enter__(self) -> sqlite3.Connection:
        self.store._lock.acquire()
        if self.store._depth == 0:
            try:
                self.store._db.execute("BEGIN IMMEDIATE")
            except BaseException:
                self.store._lock.release()
                raise
        self.store._depth += 1
        return self.store._db

    def __exit__(self, et, ev, tb) -> bool:
        self.store._depth -= 1
        try:
            if self.store._depth == 0:
                self.store._db.execute("COMMIT" if et is None else "ROLLBACK")
        finally:
            self.store._lock.release()
        if isinstance(ev, _Rollback) and self.store._depth == 0:
            raise ev.cause
        return False
