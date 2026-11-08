"""The game's own state: one SQLite file owned by the proxy (stdlib sqlite3, no extra dependency).

Game state is never written into NetBox. NetBox is the inventory; this file holds what the game remembers about a
player, keyed by the player's NetBox user id: profile, save slots (an autosave slot plus manual slots), settings and
dialogue flags. Sign-in sessions live here too, stored only as a SHA-256 of the cookie value.

The schema is versioned with `PRAGMA user_version`; `MIGRATIONS[i]` takes the file from version i to i + 1 inside one
transaction. A file written by a newer RackWalker (user_version above what this code knows) is refused, never
rewritten. The default location is `<repo>/data/game.sqlite3` (gitignored); RACKWALKER_STATE_DIR moves it.
"""
from __future__ import annotations

import hashlib
import json
import secrets
import sqlite3
import threading
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

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
        "right": ["ArrowRight", "KeyD"], "interact": ["Enter", "Space", "KeyE"], "cancel": ["Escape"],
        "menu": ["KeyM"],
    },
}


class StoreError(Exception):
    pass


def _now_iso() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


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
            have = self.version
            if have > SCHEMA_VERSION:
                raise StoreError(f"game state file {self.path} has schema version {have}; this RackWalker knows up to "
                                 f"{SCHEMA_VERSION}. Refusing to touch it (update RackWalker).")
            for v in range(have, SCHEMA_VERSION):
                self._db.execute("BEGIN IMMEDIATE")
                try:
                    for stmt in MIGRATIONS[v].split(";"):
                        if stmt.strip():
                            self._db.execute(stmt)
                    self._db.execute(f"PRAGMA user_version = {v + 1}")
                    self._db.execute("COMMIT")
                except BaseException:
                    self._db.execute("ROLLBACK")
                    raise

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

    def end_session(self, token: str | None) -> None:
        if token:
            with self._tx() as db:
                db.execute("DELETE FROM session WHERE id_hash=?", (_hash(token),))

    # ------------------------------------------------------------------------------------------- game state

    def settings(self, user_id: int) -> dict[str, Any]:
        with self._tx() as db:
            r = db.execute("SELECT data FROM settings WHERE user_id=?", (user_id,)).fetchone()
        return json.loads(r["data"]) if r else json.loads(json.dumps(DEFAULT_SETTINGS))

    def put_settings(self, user_id: int, data: dict[str, Any]) -> dict[str, Any]:
        with self._tx() as db:
            db.execute("INSERT INTO settings(user_id, data) VALUES (?,?) ON CONFLICT(user_id) DO UPDATE SET data=excluded.data",
                       (user_id, json.dumps(data, sort_keys=True)))
        return data

    def flags(self, user_id: int) -> dict[str, Any]:
        with self._tx() as db:
            rows = db.execute("SELECT name, value FROM flag WHERE user_id=? ORDER BY name", (user_id,)).fetchall()
        return {r["name"]: json.loads(r["value"]) for r in rows}

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
        return [{"slot": r["slot"], "rev": r["rev"], "updated_at": r["updated_at"], "data": json.loads(r["data"])} for r in rows]

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
