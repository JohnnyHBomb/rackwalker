"""Game state rules the round trip does not reach: players kept apart on every write path, updates that really
overwrite, per-slot revisions, sessions, the store's schema steps, stored rows that no longer match the contract,
and NetBox writes that need a signed-in player."""
import json
import sqlite3

import httpx
import pytest
from fastapi.testclient import TestClient

from rackwalker_proxy.app import Settings, create_app
from rackwalker_proxy.game import COOKIE, CSRF_HEADER
from rackwalker_proxy.gamestore import DEFAULT_SETTINGS, MAX_FLAGS, SCHEMA_VERSION, GameStore, migrate, statements, upgrade_settings

from .conftest import LOCAL, load, schema_validator, token_record
from .netbox_replay import RECORDED_HOST, Replay, load as recordings
from .test_game import SAVE, fixture_client, netbox_client, nb_transport, sign_in

PLAYERS = load("players.json")["players"]
SAVE_B = {"scene": "overworld", "overworld": {"x": 3, "y": 4, "facing": "W"}, "iso": None}


def two_players(tmp_path):
    a, b = fixture_client(tmp_path), fixture_client(tmp_path)
    return a, {CSRF_HEADER: sign_in(a, PLAYERS[0]["token"])}, b, {CSRF_HEADER: sign_in(b, PLAYERS[1]["token"])}


# ------------------------------------------------------------------------------------------- players kept apart


def test_deleting_a_flag_leaves_other_players_flag_of_that_name(tmp_path):
    a, ha, b, hb = two_players(tmp_path)
    a.patch("/api/game/flags", json={"flags": {"shared.name": True}}, headers=ha)
    b.patch("/api/game/flags", json={"flags": {"shared.name": 2}}, headers=hb)
    assert a.patch("/api/game/flags", json={"flags": {"shared.name": None}}, headers=ha).json() == {}
    assert b.get("/api/game/state").json()["flags"] == {"shared.name": 2}


def test_each_player_reads_their_own_settings(tmp_path):
    a, ha, b, hb = two_players(tmp_path)
    sa = {**DEFAULT_SETTINGS, "hint_level": "off"}
    sb = {**DEFAULT_SETTINGS, "hint_level": "high", "reduce_motion": True}
    a.put("/api/game/settings", json=sa, headers=ha)
    b.put("/api/game/settings", json=sb, headers=hb)
    assert a.get("/api/game/state").json()["settings"] == sa
    assert b.get("/api/game/state").json()["settings"] == sb


def test_the_flag_cap_counts_one_players_flags_only(tmp_path):
    a, ha, b, hb = two_players(tmp_path)
    for start in range(0, MAX_FLAGS, 100):  # a patch carries at most 100 flags
        assert a.patch("/api/game/flags", json={"flags": {f"f{i}": True for i in range(start, start + 100)}}, headers=ha).status_code == 200
    assert b.patch("/api/game/flags", json={"flags": {"b.first": True}}, headers=hb).status_code == 200
    r = a.patch("/api/game/flags", json={"flags": {"one.more": True}}, headers=ha)
    assert r.status_code == 409 and r.json()["error"]["code"] == "conflict"


def test_saves_and_revisions_are_per_player(tmp_path):
    a, ha, b, hb = two_players(tmp_path)
    for _ in range(3):
        a.put("/api/game/saves/auto", json=SAVE, headers=ha)
    assert b.put("/api/game/saves/auto", json=SAVE_B, headers=hb).json()["rev"] == 1
    assert a.get("/api/game/state").json()["saves"][0]["data"] == SAVE


# ------------------------------------------------------------------------------------------- writes overwrite


def test_a_flag_update_replaces_its_value(tmp_path):
    cl = fixture_client(tmp_path)
    h = {CSRF_HEADER: sign_in(cl)}
    cl.patch("/api/game/flags", json={"flags": {"visits": 1, "seen": True}}, headers=h)
    assert cl.patch("/api/game/flags", json={"flags": {"visits": 2, "seen": False}}, headers=h).json() == {"visits": 2, "seen": False}
    assert cl.get("/api/game/state").json()["flags"] == {"visits": 2, "seen": False}


def test_a_save_and_the_settings_overwrite_what_was_stored(tmp_path):
    cl = fixture_client(tmp_path)
    h = {CSRF_HEADER: sign_in(cl)}
    cl.put("/api/game/saves/auto", json=SAVE, headers=h)
    assert cl.put("/api/game/saves/auto", json=SAVE_B, headers=h).json()["rev"] == 2
    cl.put("/api/game/settings", json={**DEFAULT_SETTINGS, "hint_level": "low"}, headers=h)
    cl.put("/api/game/settings", json={**DEFAULT_SETTINGS, "reduce_motion": True}, headers=h)
    st = cl.get("/api/game/state").json()
    assert st["saves"][0]["data"] == SAVE_B and st["saves"][0]["rev"] == 2
    assert st["settings"] == {**DEFAULT_SETTINGS, "reduce_motion": True}


def test_every_slot_has_its_own_revision(tmp_path):
    cl = fixture_client(tmp_path)
    h = {CSRF_HEADER: sign_in(cl)}
    for _ in range(3):
        cl.put("/api/game/saves/auto", json=SAVE, headers=h)
    assert cl.put("/api/game/saves/1", json=SAVE_B, headers=h).json()["rev"] == 1
    assert cl.put("/api/game/saves/3", json=SAVE_B, headers=h).json()["rev"] == 1
    assert cl.put("/api/game/saves/1", json=SAVE, headers=h).json()["rev"] == 2
    saves = cl.get("/api/game/state").json()["saves"]
    assert [(s["slot"], s["rev"]) for s in saves] == [("auto", 3), ("1", 2), ("3", 1)]
    assert saves[1]["data"] == SAVE and saves[2]["data"] == SAVE_B


# ------------------------------------------------------------------------------------------- sessions


def test_signing_in_again_ends_the_previous_session(tmp_path):
    cl = fixture_client(tmp_path)
    sign_in(cl)
    old = cl.cookies.get(COOKIE)
    sign_in(cl, PLAYERS[1]["token"])
    assert cl.cookies.get(COOKIE) != old
    stale = fixture_client(tmp_path)
    stale.cookies.set(COOKIE, old)
    assert stale.get("/api/game/session").json()["signed_in"] is False
    assert stale.get("/api/game/state").status_code == 401


@pytest.mark.parametrize("secure", ["1", "0"])
def test_the_session_cookie_is_secure_when_configured(tmp_path, secure):
    cl = fixture_client(tmp_path, RACKWALKER_COOKIE_SECURE=secure)
    r = cl.post("/api/game/session", json={"token": PLAYERS[0]["token"]}, headers={CSRF_HEADER: "x"})
    raw = r.headers["set-cookie"]
    assert ("; Secure" in raw or "; secure" in raw) is (secure == "1")
    out = cl.delete("/api/game/session", headers={CSRF_HEADER: r.json()["csrf_token"]}).headers["set-cookie"]
    assert ("; Secure" in out or "; secure" in out) is (secure == "1")


def test_a_same_host_origin_with_a_port_may_write(tmp_path):
    cl = fixture_client(tmp_path)
    h = {CSRF_HEADER: sign_in(cl)}
    for origin in ("http://127.0.0.1:5173", "http://localhost:8710", "https://localhost"):
        r = cl.put("/api/game/saves/auto", json=SAVE, headers={**h, "Origin": origin})
        assert r.status_code == 200, (origin, r.text)


def test_local_mode_still_serves_a_signed_in_player_their_own_state(tmp_path):
    cl = fixture_client(tmp_path, RACKWALKER_LOCAL_PLAYER="1")
    local = cl.get("/api/game/session").json()
    assert cl.put("/api/game/saves/auto", json=SAVE_B, headers={CSRF_HEADER: local["csrf_token"]}).status_code == 200
    h = {CSRF_HEADER: sign_in(cl)}
    assert cl.get("/api/game/session").json()["player"]["id"] == PLAYERS[0]["user"]["id"]
    assert cl.get("/api/game/state").json()["saves"] == []  # not the local player's save
    assert cl.put("/api/game/saves/auto", json=SAVE, headers={CSRF_HEADER: "local"}).status_code == 403
    cl.put("/api/game/saves/auto", json=SAVE, headers=h)
    assert cl.get("/api/game/state").json()["saves"][0]["data"] == SAVE
    cl.cookies.clear()
    assert cl.get("/api/game/state").json()["saves"][0]["data"] == SAVE_B  # the local player's save is untouched


# ------------------------------------------------------------------------------------------- NetBox identity


@pytest.mark.parametrize("body", [
    {"username": "noid"}, {"id": None, "username": "x"}, {"id": "7", "username": "x"}, {"id": 0, "username": "x"},
    {"id": True, "username": "x"}, ["not", "an", "object"],
])
def test_an_identity_answer_without_a_user_id_is_refused(tmp_path, body):
    t, _ = nb_transport(200, None)
    t = httpx.MockTransport(lambda req: httpx.Response(200, json=body))
    r = netbox_client(tmp_path, t).post("/api/game/session", json={"token": "nbt_player.playersecret"}, headers={CSRF_HEADER: "x"})
    assert r.status_code == 502 and r.json()["error"]["code"] == "netbox_error"


@pytest.mark.parametrize("token", ["nbt_abcdefgh.pässword", "nbt_abcdefgh.tab\there", "nbt_abcdefgh.line\nbreak"])
def test_a_token_with_characters_no_netbox_token_has_is_refused_without_asking_netbox(tmp_path, caplog, token):
    t, seen = nb_transport(200, {"id": 7, "username": "jdoe"})
    r = netbox_client(tmp_path, t).post("/api/game/session", json={"token": token}, headers={CSRF_HEADER: "x"})
    assert r.status_code == 401 and r.json()["error"]["code"] == "unauthorized"
    assert seen == [] and "Traceback" not in caplog.text and "pässword" not in caplog.text


# ------------------------------------------------------------------------------------------- the store's schema steps


def test_each_migration_step_records_its_own_version():
    db = sqlite3.connect(":memory:", isolation_level=None)
    with pytest.raises(sqlite3.OperationalError):
        migrate(db, ["CREATE TABLE a (x INTEGER);", "INSERT INTO nope VALUES (1);"])
    assert db.execute("PRAGMA user_version").fetchone()[0] == 1  # the first step landed, the failing one did not
    assert migrate(db, ["CREATE TABLE a (x INTEGER);", "CREATE TABLE b (y INTEGER);"]) == 2  # it runs on the next try
    assert db.execute("SELECT count(*) FROM sqlite_master WHERE name = 'b'").fetchone()[0] == 1


def test_a_last_statement_without_a_semicolon_still_runs():
    assert statements("CREATE TABLE a (x INTEGER); INSERT INTO a VALUES (1)") == ["CREATE TABLE a (x INTEGER);", "INSERT INTO a VALUES (1)"]
    db = sqlite3.connect(":memory:", isolation_level=None)
    migrate(db, ["CREATE TABLE a (x INTEGER); INSERT INTO a VALUES (1)"])
    assert db.execute("SELECT x FROM a").fetchall() == [(1,)]


# ------------------------------------------------------------------------------------------- stored rows read back


def _put_raw(tmp_path, sql: str, *args) -> None:
    db = sqlite3.connect(tmp_path / "game.sqlite3")
    db.execute(sql, args)
    db.commit()
    db.close()


@pytest.mark.parametrize("raw", ['{"scene":"overworld",', '{"scene":"overworld","x":5,"y":5}', "null", '"text"'])
def test_a_save_row_that_no_longer_matches_is_left_out_not_a_500(tmp_path, raw, caplog):
    cl = fixture_client(tmp_path)
    h = {CSRF_HEADER: sign_in(cl)}
    cl.put("/api/game/saves/auto", json=SAVE, headers=h)
    cl.put("/api/game/saves/2", json=SAVE_B, headers=h)
    _put_raw(tmp_path, "UPDATE save SET data=? WHERE slot='auto'", raw)
    r = cl.get("/api/game/state")
    assert r.status_code == 200
    schema_validator("GameState").validate(r.json())
    assert [s["slot"] for s in r.json()["saves"]] == ["2"]
    assert "save slot auto" in caplog.text
    # The next autosave replaces the damaged row.
    cl.put("/api/game/saves/auto", json=SAVE, headers=h)
    assert cl.get("/api/game/state").json()["saves"][0]["data"] == SAVE


def test_a_damaged_flag_row_is_left_out(tmp_path):
    cl = fixture_client(tmp_path)
    h = {CSRF_HEADER: sign_in(cl)}
    cl.patch("/api/game/flags", json={"flags": {"good": 1, "bad": 2}}, headers=h)
    _put_raw(tmp_path, "UPDATE flag SET value=? WHERE name='bad'", "{broken")
    r = cl.get("/api/game/state")
    assert r.status_code == 200 and r.json()["flags"] == {"good": 1}


@pytest.mark.parametrize("stored,expected", [
    ("{broken", DEFAULT_SETTINGS),
    (json.dumps({k: v for k, v in DEFAULT_SETTINGS.items() if k != "keys"} | {"keys": {k: v for k, v in DEFAULT_SETTINGS["keys"].items() if k != "menu"}}),
     DEFAULT_SETTINGS),
    (json.dumps({"hint_level": "off"}), {**DEFAULT_SETTINGS, "hint_level": "off"}),
    (json.dumps({**DEFAULT_SETTINGS, "hint_level": "loud", "audio": {"muted": True, "volume": 7}}),
     {**DEFAULT_SETTINGS, "audio": {"muted": True, "volume": DEFAULT_SETTINGS["audio"]["volume"]}}),
    (json.dumps({**DEFAULT_SETTINGS, "keys": {**DEFAULT_SETTINGS["keys"], "menu": ["KeyW"]}}), DEFAULT_SETTINGS),
    (json.dumps({**DEFAULT_SETTINGS, "keys": {**DEFAULT_SETTINGS["keys"], "menu": ["KeyP"]}, "retired": 1}),
     {**DEFAULT_SETTINGS, "keys": {**DEFAULT_SETTINGS["keys"], "menu": ["KeyP"]}}),
])
def test_stored_settings_keep_their_valid_parts_and_default_the_rest(tmp_path, stored, expected):
    cl = fixture_client(tmp_path)
    h = {CSRF_HEADER: sign_in(cl)}
    cl.put("/api/game/settings", json=DEFAULT_SETTINGS, headers=h)
    _put_raw(tmp_path, "UPDATE settings SET data=?", stored)
    r = cl.get("/api/game/state")
    assert r.status_code == 200 and r.json()["settings"] == expected
    schema_validator("GameSettings").validate(r.json()["settings"])


def test_upgrade_settings_never_shares_the_defaults():
    out = upgrade_settings(None)
    out["keys"]["up"].append("KeyZ")
    assert DEFAULT_SETTINGS["keys"]["up"] == ["ArrowUp", "KeyW"]


@pytest.mark.parametrize("damage,store_code", [("newer", "newer_schema"), ("garbage", "unreadable")])
def test_a_state_file_the_proxy_cannot_use_is_named_in_the_answer(tmp_path, damage, store_code):
    p = tmp_path / "game.sqlite3"
    if damage == "newer":
        GameStore(p).close()
        db = sqlite3.connect(p)
        db.execute(f"PRAGMA user_version = {SCHEMA_VERSION + 1}")
        db.commit()
        db.close()
    else:
        p.write_bytes(b"this is not a database file" * 100)
    cl = fixture_client(tmp_path)
    r = cl.post("/api/game/session", json={"token": PLAYERS[0]["token"]}, headers={CSRF_HEADER: "x"})
    assert r.status_code == 500
    err = r.json()["error"]
    assert err["details"] == {"store": store_code}
    assert ("newer RackWalker" if damage == "newer" else "cannot be read") in err["message"]


# ------------------------------------------------------------------------------------------- NetBox writes need a player


CHANGE = {"ops": [{"op": "move_rack", "target": {"kind": "rack", "id": 1}, "fields": {"iso_y": 3}}], "reason": "no session"}


def test_a_write_enabled_proxy_needs_a_signed_in_player_for_the_change_routes(tmp_path):
    cl = fixture_client(tmp_path, WRITE_ENABLED="1")
    for path in ("/api/changes/preview", "/api/changes/apply"):
        r = cl.post(path, json=CHANGE, headers={"Origin": "http://localhost:9999"})
        assert r.status_code == 401 and r.json()["error"]["code"] == "unauthorized", (path, r.text)
    csrf = sign_in(cl)
    for h in ({}, {CSRF_HEADER: "wrong"}, {CSRF_HEADER: "local"}, {CSRF_HEADER: csrf, "Origin": "http://evil.example"}):
        for path in ("/api/changes/preview", "/api/changes/apply"):
            r = cl.post(path, json=CHANGE, headers=h)
            assert r.status_code == 403 and r.json()["error"]["code"] == "forbidden", (path, h)
    assert cl.post("/api/changes/preview", json=CHANGE, headers={CSRF_HEADER: csrf}).status_code == 200
    # Past the player check, fixture mode still never writes.
    r = cl.post("/api/changes/apply", json=CHANGE, headers={CSRF_HEADER: csrf})
    assert r.status_code == 400 and r.json()["error"]["message"] == "Fixture mode never writes."


def test_a_dry_run_proxy_previews_without_a_player_and_refuses_apply_as_before(tmp_path):
    cl = fixture_client(tmp_path)
    assert cl.post("/api/changes/preview", json=CHANGE).status_code == 200
    r = cl.post("/api/changes/apply", json=CHANGE)
    assert r.status_code == 403 and r.json()["error"]["code"] == "write_disabled"


def test_a_signed_in_players_write_names_them_in_the_netbox_changelog(tmp_path):
    rack2 = recordings()["GET /api/dcim/racks/?id=2&limit=1000&offset=0"]["json"]["results"][0]
    writes: list[dict] = []

    class NetBox(Replay):
        async def handle_async_request(self, request):
            if token_record(request) is not None:
                return token_record(request)
            if request.url.path == "/api/authentication-check/":
                return httpx.Response(200, json={"id": 9, "username": "player-nine", "first_name": "", "last_name": ""}, request=request)
            if request.url.path == "/api/users/users/9/":  # the session recheck before the write
                return httpx.Response(200, json={"id": 9, "username": "player-nine", "is_active": True}, request=request)
            if request.url.path == "/api/users/users/":  # is the player a superuser? (yes: may change anything)
                return httpx.Response(200, json={"count": 1, "results": [{"id": 9}]}, request=request)
            return await super().handle_async_request(request)

    def on_write(req):
        writes.append(json.loads(req.content))
        return httpx.Response(200, json=rack2, request=req)

    s = Settings(RECORDED_HOST, "nbt_proxy.proxysecret", True, None, cache_ttl=0, state_dir=tmp_path)
    cl = TestClient(base_url=LOCAL, app=create_app(s, transport=NetBox(on_write=on_write)))
    csrf = cl.post("/api/game/session", json={"token": "nbt_player.playersecret"}, headers={CSRF_HEADER: "x"}).json()["csrf_token"]
    cs = {"ops": [{"op": "update_fields", "target": {"kind": "rack", "id": 2}, "fields": {"description": "named"}}], "reason": "label it"}
    res = cl.post("/api/changes/apply", json=cs, headers={CSRF_HEADER: csrf})
    assert res.status_code == 200 and res.json()["applied"] is True, res.text
    assert [w["changelog_message"] for w in writes] == ["rackwalker (player-nine): label it"]


def test_no_proxy_answer_may_be_framed(tmp_path):
    cl = fixture_client(tmp_path)
    for r in (cl.get("/api/health"), cl.get("/api/scene/world"), cl.get("/api/game/session"), cl.get("/api/nope"),
              cl.get("/api/health", headers={"Host": "evil.example"})):
        assert r.headers["x-frame-options"] == "DENY" and r.headers["content-security-policy"] == "frame-ancestors 'none'", r.url


def test_settings_from_before_the_building_keys_keep_the_players_own_keys():
    from rackwalker_proxy.gamestore import DEFAULT_SETTINGS, upgrade_settings
    old = {"hint_level": "low", "audio": {"muted": True, "volume": 0.2}, "reduce_motion": False,
           "keys": {"up": ["ArrowUp"], "down": ["ArrowDown"], "left": ["ArrowLeft"], "right": ["ArrowRight"],
                    "interact": ["Enter"], "cancel": ["Escape"], "menu": ["Equal"]}}
    up = upgrade_settings(old)
    assert up["keys"]["menu"] == ["Equal"]                 # the player's key stays theirs
    assert up["keys"]["zoom_in"] == ["NumpadAdd"]          # the new action's default gives way
    assert up["keys"]["rotate"] == DEFAULT_SETTINGS["keys"]["rotate"]
    assert up["keys"]["cancel"] == ["Escape"] and up["hint_level"] == "low"


def test_settings_put_in_the_shape_from_before_the_building_keys_is_taken_and_filled(tmp_path):
    cl = fixture_client(tmp_path)
    csrf = sign_in(cl)
    old = {"hint_level": "low", "audio": {"muted": False, "volume": 0.5}, "reduce_motion": True,
           "keys": {"up": ["ArrowUp"], "down": ["ArrowDown"], "left": ["ArrowLeft"], "right": ["ArrowRight"],
                    "interact": ["Enter"], "cancel": ["Escape"], "menu": ["KeyR"]}}
    r = cl.put("/api/game/settings", json=old, headers={CSRF_HEADER: csrf})
    assert r.status_code == 200, r.text
    keys = r.json()["keys"]
    # the player's keys stay theirs; rotate's only default (R) is the player's menu key, so rotate waits for a remap
    assert {a: keys[a] for a in old["keys"]} == old["keys"]
    assert "rotate" not in keys
    assert {a: keys[a] for a in ("zoom_in", "search", "trace", "unrack", "undo")} == \
        {a: DEFAULT_SETTINGS["keys"][a] for a in ("zoom_in", "search", "trace", "unrack", "undo")}
    assert cl.get("/api/game/state").json()["settings"]["keys"] == keys
    assert not list(schema_validator("GameSettings").iter_errors(r.json()))
