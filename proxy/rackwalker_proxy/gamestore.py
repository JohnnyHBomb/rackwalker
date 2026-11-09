"""The game's own state: one SQLite file owned by the proxy (stdlib sqlite3, no extra dependency).

Game state is never written into NetBox. NetBox is the inventory; this file holds what the game remembers about a
player, keyed by the player's NetBox user id: profile, save slots (an autosave slot plus manual slots), settings and
dialogue flags. Sign-in sessions live here too, stored only as a SHA-256 of the cookie value.

The schema is versioned with `PRAGMA user_version`; `MIGRATIONS[i]` takes the file from version i to i + 1 inside one
transaction. A file written by a newer RackWalker (user_version above what this code knows) is refused, never
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

MIGRATIONS: list[str] = [
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


def migrate(db: sqlite3.Connection, steps: list[str], label: object = "game state file") -> int:
    """Brings `db` (autocommit mode) to version len(steps): `steps[i]` takes it from version i to i + 1 inside one
    transaction, so a failing step leaves the file at the version before it. A newer file is refused untouched."""
    have = db.execute("PRAGMA user_version").fetchone()[0]
    if have > len(steps):
        raise StoreError(f"game state file {label} has schema version {have}; this RackWalker knows up to "
                         f"{len(steps)}. Refusing to touch it (update RackWalker).")
    for v in range(have, len(steps)):
        db.execute("BEGIN IMMEDIATE")
        try:
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


class GameStore:
    """Thread-safe (one connection, one lock); every call is a short transaction."""

    def __init__(self, path: Path | str, clock=time.time):
        self.path = Path(path) if str(path) != ":memory:" else path
        if isinstance(self.path, Path):
            self.path.parent.mkdir(parents=True, exist_ok=True)
        self.clock = clock
        self._lock = threading.Lock()
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

    def new_session(self, user_id: int) -> tuple[str, Session]:
        """Returns (cookie value, session). Only the hash of the cookie value is stored."""
        token, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(24)
        now = self.clock()
        with self._tx() as db:
            db.execute("DELETE FROM session WHERE expires_at <= ?", (now,))
            db.execute("INSERT INTO session(id_hash, user_id, csrf, created_at, expires_at) VALUES (?,?,?,?,?)",
                       (_hash(token), user_id, csrf, now, now + SESSION_SECONDS))
        return token, Session(user_id, csrf, now + SESSION_SECONDS)

    def session(self, token: str | None) -> Session | None:
        if not token or len(token) > 200:
            return None
        with self._tx() as db:
            r = db.execute("SELECT user_id, csrf, expires_at FROM session WHERE id_hash=?", (_hash(token),)).fetchone()
        if r is None or r["expires_at"] <= self.clock():
            return None
        return Session(r["user_id"], r["csrf"], r["expires_at"])

    def end_user_sessions(self, user_id: int) -> None:
        """Ends every session of one player (their NetBox user was deleted or deactivated)."""
        with self._tx() as db:
            db.execute("DELETE FROM session WHERE user_id=?", (user_id,))

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


class _Rollback(Exception):
    def __init__(self, cause: Exception):
        self.cause = cause


class _Tx:
    def __init__(self, store: GameStore):
        self.store = store

    def __enter__(self) -> sqlite3.Connection:
        self.store._lock.acquire()
        self.store._db.execute("BEGIN IMMEDIATE")
        return self.store._db

    def __exit__(self, et, ev, tb) -> bool:
        try:
            self.store._db.execute("COMMIT" if et is None else "ROLLBACK")
        finally:
            self.store._lock.release()
        if isinstance(ev, _Rollback):
            raise ev.cause
        return False
