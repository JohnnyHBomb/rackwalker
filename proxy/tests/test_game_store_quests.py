"""The game store's quest tables: each player's log, rewards, practice changes and boss markers are their own, each
scope is its own, a reward is never paid twice, and a state file written by an earlier RackWalker keeps everything
when it is brought up to date. The version-1 and version-2 schemas below are the ones those versions shipped with,
written out here on purpose: a later edit to a shipped migration step must not go unseen."""
from __future__ import annotations

import json
import sqlite3
import threading

from rackwalker_proxy.game import CSRF_HEADER
from rackwalker_proxy.gamestore import MIGRATIONS, SCHEMA_VERSION, GameStore, migrate

from .conftest import load
from .test_game import fixture_client, sign_in

V1 = """
CREATE TABLE player (user_id INTEGER PRIMARY KEY, username TEXT NOT NULL, display_name TEXT, created_at TEXT NOT NULL, seen_at TEXT NOT NULL);
CREATE TABLE session (id_hash TEXT PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES player(user_id) ON DELETE CASCADE, csrf TEXT NOT NULL,
                      created_at REAL NOT NULL, expires_at REAL NOT NULL);
CREATE INDEX session_user ON session(user_id);
CREATE TABLE save (user_id INTEGER NOT NULL REFERENCES player(user_id) ON DELETE CASCADE, slot TEXT NOT NULL, rev INTEGER NOT NULL,
                   data TEXT NOT NULL, updated_at TEXT NOT NULL, PRIMARY KEY (user_id, slot));
CREATE TABLE settings (user_id INTEGER PRIMARY KEY REFERENCES player(user_id) ON DELETE CASCADE, data TEXT NOT NULL);
CREATE TABLE flag (user_id INTEGER NOT NULL REFERENCES player(user_id) ON DELETE CASCADE, name TEXT NOT NULL, value TEXT NOT NULL,
                   PRIMARY KEY (user_id, name));
"""
V2 = V1 + """
CREATE TABLE quest (user_id INTEGER NOT NULL REFERENCES player(user_id) ON DELETE CASCADE, scope TEXT NOT NULL, quest_id TEXT NOT NULL,
                    site_id INTEGER NOT NULL, status TEXT NOT NULL, answers TEXT NOT NULL, checks TEXT NOT NULL, verified TEXT NOT NULL,
                    snapshot TEXT NOT NULL, updated_at TEXT NOT NULL, PRIMARY KEY (user_id, scope, quest_id));
CREATE TABLE reward (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL REFERENCES player(user_id) ON DELETE CASCADE,
                     quest_id TEXT NOT NULL, kind TEXT NOT NULL, amount INTEGER NOT NULL, item TEXT, signature TEXT NOT NULL, at TEXT NOT NULL);
CREATE INDEX reward_user ON reward(user_id);
CREATE TABLE practice (user_id INTEGER PRIMARY KEY REFERENCES player(user_id) ON DELETE CASCADE, ops TEXT NOT NULL);
CREATE TABLE boss (user_id INTEGER NOT NULL REFERENCES player(user_id) ON DELETE CASCADE, scope TEXT NOT NULL, site_id INTEGER NOT NULL,
                   at TEXT NOT NULL, PRIMARY KEY (user_id, scope, site_id));
"""
SAVE = {"scene": "overworld", "overworld": {"x": 3, "y": 4, "facing": "S"}, "iso": None}
SETTINGS = {"hint_level": "off", "audio": {"muted": True, "volume": 0.25}, "reduce_motion": True}


def shape(db: sqlite3.Connection) -> dict[str, list[tuple]]:
    """Every table and index of a state file: columns with type, not-null, default and key position; foreign keys."""
    out: dict[str, list[tuple]] = {}
    for kind, name in db.execute("SELECT type, name FROM sqlite_master WHERE name NOT LIKE 'sqlite_%' ORDER BY name").fetchall():
        if kind == "table":
            out[name] = [tuple(r)[1:] for r in db.execute(f"PRAGMA table_info({name})")] + \
                        sorted(("fk", r[2], r[3], r[4], r[6]) for r in db.execute(f"PRAGMA foreign_key_list({name})"))
        else:
            out[f"index {name}"] = [tuple(r)[1:] for r in db.execute(f"PRAGMA index_info({name})")]
    return out


def old_file(path, schema: str, version: int) -> sqlite3.Connection:
    db = sqlite3.connect(path, isolation_level=None)
    db.executescript(schema)
    db.execute(f"PRAGMA user_version = {version}")
    db.execute("INSERT INTO player VALUES (7, 'walker', NULL, '2026-11-08T10:00:00Z', '2026-11-08T10:00:00Z')")
    db.execute("INSERT INTO session VALUES ('hash-of-a-cookie', 7, 'csrf', 1.0, 99999999999.0)")
    db.execute("INSERT INTO save VALUES (7, 'auto', 5, ?, '2026-11-08T10:00:00Z')", (json.dumps(SAVE),))
    db.execute("INSERT INTO settings VALUES (7, ?)", (json.dumps(SETTINGS),))
    db.execute("INSERT INTO flag VALUES (7, 'ilatea.intro.seen', 'true')")
    return db


def test_the_shipped_migration_steps_still_build_the_schemas_they_shipped_with(tmp_path):
    for version, schema in ((1, V1), (2, V2)):
        ours, theirs = sqlite3.connect(":memory:", isolation_level=None), sqlite3.connect(":memory:", isolation_level=None)
        migrate(ours, MIGRATIONS[:version])
        theirs.executescript(schema)
        assert shape(ours) == shape(theirs), f"migration step {version - 1} -> {version} was edited after it shipped"


def test_a_version_1_file_is_brought_up_to_date_and_keeps_everything(tmp_path):
    path = tmp_path / "game.sqlite3"
    old_file(path, V1, 1).close()
    st = GameStore(path)
    assert st.version == SCHEMA_VERSION == 4
    assert st.saves(7) == [{"slot": "auto", "rev": 5, "updated_at": "2026-11-08T10:00:00Z", "data": SAVE}]
    assert st.flags(7) == {"ilatea.intro.seen": True}
    kept = st.settings(7)
    assert (kept["hint_level"], kept["audio"], kept["reduce_motion"]) == ("off", {"muted": True, "volume": 0.25}, True)
    # the session is still there; it may not write until the player signs in again (its token was never looked at)
    with st.atomic() as db:
        assert [tuple(r) for r in db.execute("SELECT id_hash, user_id, can_write, token_id FROM session")] == [("hash-of-a-cookie", 7, 0, None)]
    assert st.quests(7, "netbox") == {} and st.rewards(7) == {"xp": 0, "potions": 0, "tools": [], "repeats": {}}


def test_a_version_2_file_keeps_its_quest_log_and_what_was_confirmed_is_not_paid_again(tmp_path):
    path = tmp_path / "game.sqlite3"
    db = old_file(path, V2, 2)
    snap = json.dumps({"id": "q:rack_audit:rack:1", "kind": "rack_audit"})
    rows = [("netbox", "q:rack_audit:rack:1", "active", '["device:1:serial", "device:2:serial"]', '["device:1:serial"]'),
            ("netbox", "q:damaged", "active", "[]", "not json"), ("netbox", "q:odd", "active", "[]", '{"a": 1}'),
            ("practice", "q:site_record:site:9000001", "done", '["site:9000001:gps"]', '["site:9000001:gps"]')]
    for scope, qid, status, checks, verified in rows:
        db.execute("INSERT INTO quest VALUES (7, ?, ?, 1, ?, '{}', ?, ?, ?, '2026-11-09T09:00:00Z')", (scope, qid, status, checks, verified, snap))
    db.execute("INSERT INTO reward(user_id, quest_id, kind, amount, item, signature, at) VALUES (7, 'q:rack_audit:rack:1', 'xp', 25, NULL, 'rack_audit:1', 'x')")
    db.close()
    st = GameStore(path)
    assert st.version == 4 and st.rewards(7)["xp"] == 25
    assert st.quests(7, "netbox")["q:rack_audit:rack:1"]["verified"] == ["device:1:serial"]
    # what NetBox had confirmed before is on the ledger: confirming it again pays nothing; the practice island never paid
    assert st.mark_paid(7, ["device:1:serial", "device:2:serial", "site:9000001:gps"]) == ["device:2:serial", "site:9000001:gps"]


def test_a_check_is_paid_once_per_player_whoever_asks_and_however_often(tmp_path):
    st = GameStore(tmp_path / "g.sqlite3")
    for uid in (7, 8):
        st.upsert_player(uid, f"p{uid}", None)
    assert st.mark_paid(7, ["device:1:serial", "device:2:serial"]) == ["device:1:serial", "device:2:serial"]
    assert st.mark_paid(7, ["device:2:serial", "device:3:serial"]) == ["device:3:serial"]
    assert st.mark_paid(8, ["device:1:serial"]) == ["device:1:serial"]          # another player's ledger is their own
    got: list[list[str]] = []
    threads = [threading.Thread(target=lambda: got.append(st.mark_paid(7, ["rack:1:iso"]))) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(10)
    assert sorted(got) == [[]] * 7 + [["rack:1:iso"]]


def test_several_store_calls_in_one_transaction_are_all_kept_or_all_undone(tmp_path):
    st = GameStore(tmp_path / "g.sqlite3")
    st.upsert_player(7, "a", None)
    try:
        with st.atomic():
            st.add_reward(7, "q:x", "xp", 120, "k:1")
            st.mark_paid(7, ["c1"])
            raise RuntimeError("the request failed half-way")
    except RuntimeError:
        pass
    assert st.rewards(7)["xp"] == 0 and st.mark_paid(7, ["c1"]) == ["c1"]
    with st.atomic():
        st.add_reward(7, "q:x", "xp", 120, "k:1")
    assert st.rewards(7)["xp"] == 120


def row(quest_id: str, **more) -> dict:
    return {"quest_id": quest_id, "site_id": 1, "status": "parked", "answers": {"peer": {"choice": "unknown"}}, "checks": ["a"], "verified": [],
            "snapshot": {"id": quest_id, "kind": "device_unconnected"}, **more}


def test_quest_rows_rewards_practice_changes_and_boss_markers_are_per_player_and_per_scope(tmp_path):
    st = GameStore(tmp_path / "g.sqlite3")
    for uid in (7, 8):
        st.upsert_player(uid, f"p{uid}", None)
    st.put_quest(7, "netbox", row("q:real"))
    st.put_quest(7, "practice", row("q:island"))
    st.put_quest(8, "practice", row("q:island", status="done"))
    st.add_reward(7, "q:real", "xp", 120, "k:1")
    st.add_reward(7, "q:real", "tool", 1, "tool", item="cable_tracer")
    st.add_reward(7, "q:real", "potion", 1, "level")
    st.set_practice_ops(7, [{"op": "create_prefix"}])
    st.ack_boss(7, "netbox", 1)
    st.ack_boss(7, "practice", 9000001)
    st.ack_boss(8, "practice", 9000001)
    assert st.rewards(7) == {"xp": 120, "potions": 1, "tools": ["cable_tracer"], "repeats": {"k:1": 1}}
    assert st.rewards(8) == {"xp": 0, "potions": 0, "tools": [], "repeats": {}}
    assert st.practice_ops(8) == [] and st.practice_ops(7) == [{"op": "create_prefix"}]
    assert st.boss_acks(7, "netbox") == {1} and st.boss_acks(7, "practice") == {9000001} and st.boss_acks(8, "netbox") == set()
    # starting the practice island again clears that scope of that player, and nothing else
    st.clear_quests(7, "practice")
    assert list(st.quests(7, "netbox")) == ["q:real"] and st.quests(7, "practice") == {} and list(st.quests(8, "practice")) == ["q:island"]
    assert st.boss_acks(7, "netbox") == {1} and st.boss_acks(7, "practice") == set() and st.boss_acks(8, "practice") == {9000001}
    assert st.rewards(7)["xp"] == 120


def test_a_quest_row_keeps_its_new_checks_and_a_row_of_the_wrong_shape_is_left_out(tmp_path):
    st = GameStore(tmp_path / "g.sqlite3")
    st.upsert_player(7, "a", None)
    st.put_quest(7, "netbox", row("q:audit"))
    st.put_quest(7, "netbox", row("q:audit", checks=["a", "b"], verified=["a"], status="active"))
    got = st.quests(7, "netbox")["q:audit"]
    assert (got["checks"], got["verified"], got["status"]) == (["a", "b"], ["a"], "active")
    st.move_quest(7, "netbox", "q:audit", row("q:audit-in-its-new-rack", checks=["a", "b", "c"]))
    assert list(st.quests(7, "netbox")) == ["q:audit-in-its-new-rack"]
    with st.atomic() as db:      # valid JSON, wrong shape: answers must be an object, checks and verified lists
        db.execute("UPDATE quest SET answers='[]'")
    assert st.quests(7, "netbox") == {}
    with st.atomic() as db:
        db.execute("""UPDATE quest SET answers='{}', checks='{"a": 1}'""")
    assert st.quests(7, "netbox") == {}
    st.delete_quest(7, "netbox", "q:audit-in-its-new-rack")
    with st.atomic() as db:
        assert db.execute("SELECT COUNT(*) FROM quest").fetchone()[0] == 0


def test_a_players_quest_log_goes_with_the_player(tmp_path):
    st = GameStore(tmp_path / "g.sqlite3")
    st.upsert_player(7, "a", None)
    st.put_quest(7, "netbox", row("q:x"))
    st.mark_paid(7, ["a"])
    with st.atomic() as db:
        db.execute("DELETE FROM player WHERE user_id=7")
        assert [db.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in ("quest", "paid")] == [0, 0]


def test_starting_the_island_again_through_the_route_keeps_the_real_log_and_forgets_the_islands_boss(tmp_path):
    cl = fixture_client(tmp_path)
    cl.headers[CSRF_HEADER] = sign_in(cl)
    real = cl.get("/api/game/quests", params={"scope": "netbox", "site_id": 1}).json()["quests"][0]
    step = real["step"]
    unknown = next(c for c in step["choices"] if c["kind"] == "unknown")
    q = cl.post("/api/game/quests/answer", json={"scope": "netbox", "quest_id": real["id"], "step": step["id"], "choice": unknown["id"]}).json()
    assert q["status"] == "parked"
    st = cl.app.state.game_store()
    uid = load("players.json")["players"][0]["user"]["id"]
    st.ack_boss(uid, "practice", 9000001)
    island = cl.post("/api/game/practice/reset").json()
    assert island["sites"][0]["boss_acknowledged"] is False
    after = [x["status"] for x in cl.get("/api/game/quests", params={"scope": "netbox", "site_id": 1}).json()["quests"] if x["id"] == real["id"]]
    assert after == ["parked"]


def test_saying_i_dont_know_to_everything_fills_nothing_up(tmp_path):
    cl = fixture_client(tmp_path)
    cl.headers[CSRF_HEADER] = sign_in(cl)
    before = cl.get("/api/game/quests", params={"scope": "practice"}).json()["sites"][0]
    for _ in range(40):
        b = cl.get("/api/game/quests", params={"scope": "practice"}).json()
        fresh = [q for q in b["quests"] if q["status"] in ("offered", "active")]
        if not fresh:
            break
        q = fresh[0]
        step = q["step"]
        r = cl.post("/api/game/quests/answer", json={"scope": "practice", "quest_id": q["id"], "step": step["id"],
                                                     "choice": next(c for c in step["choices"] if c["kind"] == "unknown")["id"]})
        assert r.status_code == 200, r.text
    after = cl.get("/api/game/quests", params={"scope": "practice"}).json()
    s = after["sites"][0]
    assert s["completeness"] == before["completeness"] and not s["boss_ready"] and not after["all_in_order"]
    assert {q["status"] for q in after["quests"]} == {"parked"}
