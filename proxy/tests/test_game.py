"""Game routes: sign-in as a NetBox user, session cookie, CSRF, and the game-state round trip in SQLite."""
import json
import sqlite3

import httpx
import pytest
from fastapi.testclient import TestClient

from rackwalker_proxy import contracts_gen as c
from rackwalker_proxy.app import Settings, create_app
from rackwalker_proxy.game import COOKIE, CSRF_HEADER
from rackwalker_proxy.gamestore import DEFAULT_SETTINGS, SCHEMA_VERSION, GameStore, StoreError, migrate, statements

from .conftest import LOCAL, REPO, load, schema_validator, token_record

PLAYER_TOKEN = load("players.json")["players"][0]["token"]
SAVE = {"scene": "iso", "overworld": {"x": 12, "y": 9, "facing": "S"},
        "iso": {"site_id": 1, "view": "room:1:1", "camera": {"cx": 40.5, "cy": -12.0, "scale": 3}}}


def fixture_client(tmp_path, **env) -> TestClient:
    s = Settings.from_env({"RACKWALKER_FIXTURES": str(REPO / "fixtures"), "RACKWALKER_STATE_DIR": str(tmp_path), **env})
    return TestClient(base_url=LOCAL, app=create_app(s))


def sign_in(cl: TestClient, token: str = PLAYER_TOKEN) -> str:
    r = cl.post("/api/game/session", json={"token": token}, headers={CSRF_HEADER: "sign-in"})
    assert r.status_code == 200, r.text
    body = c.GameSession.model_validate(r.json())
    assert body.signed_in and body.csrf_token
    return body.csrf_token


def test_signed_out_session_and_state_refused(tmp_path):
    cl = fixture_client(tmp_path)
    r = cl.get("/api/game/session")
    assert r.status_code == 200 and r.json() == {"signed_in": False, "player": None, "csrf_token": None, "local_mode": False}
    assert r.headers["cache-control"] == "no-store"
    r = cl.get("/api/game/state")
    assert r.status_code == 401
    assert c.ErrorResponse.model_validate(r.json()).error.code == "unauthorized"


def test_sign_in_sets_httponly_strict_cookie_and_never_stores_the_token(tmp_path):
    cl = fixture_client(tmp_path)
    sign_in(cl)
    cookie = cl.cookies.get(COOKIE)
    raw = [h for k, h in cl.post("/api/game/session", json={"token": PLAYER_TOKEN}, headers={CSRF_HEADER: "x"}).headers.multi_items()
           if k == "set-cookie"][0]
    assert "HttpOnly" in raw and "SameSite=strict" in raw and "Path=/api;" in raw
    assert cookie
    db = (tmp_path / "game.sqlite3").read_bytes() + b"".join(p.read_bytes() for p in tmp_path.glob("game.sqlite3-*"))
    assert PLAYER_TOKEN.encode() not in db
    assert cl.cookies.get(COOKIE).encode() not in db  # only the hash of the session cookie is stored
    me = cl.get("/api/game/session").json()
    assert me["player"] == load("players.json")["players"][0]["user"]


def test_bad_token_is_unauthorized_and_sign_in_needs_the_header(tmp_path):
    cl = fixture_client(tmp_path)
    r = cl.post("/api/game/session", json={"token": "nbt_wrong.wrongwrong"}, headers={CSRF_HEADER: "x"})
    assert r.status_code == 401
    assert "nbt_wrong" not in r.text
    r = cl.post("/api/game/session", json={"token": PLAYER_TOKEN})
    assert r.status_code == 403 and r.json()["error"]["code"] == "forbidden"
    r = cl.post("/api/game/session", json={"token": PLAYER_TOKEN}, headers={CSRF_HEADER: "x", "Origin": "http://evil.example"})
    assert r.status_code == 403
    r = cl.post("/api/game/session", json={"token": "short"}, headers={CSRF_HEADER: "x"})
    assert r.status_code == 422


def test_save_settings_flags_round_trip(tmp_path):
    cl = fixture_client(tmp_path)
    csrf = sign_in(cl)
    h = {CSRF_HEADER: csrf}
    st = c.GameState.model_validate(cl.get("/api/game/state").json())
    assert st.saves == [] and st.flags.root == {} and st.settings.model_dump(mode="json") == DEFAULT_SETTINGS
    r = cl.put("/api/game/saves/auto", json=SAVE, headers=h)
    assert r.status_code == 200 and r.json()["rev"] == 1
    assert cl.put("/api/game/saves/auto", json=SAVE, headers=h).json()["rev"] == 2
    settings = {**DEFAULT_SETTINGS, "hint_level": "low", "keys": {**DEFAULT_SETTINGS["keys"], "menu": ["KeyP"]}}
    assert cl.put("/api/game/settings", json=settings, headers=h).json() == settings
    assert cl.patch("/api/game/flags", json={"flags": {"ilatea.intro.seen": True, "visits": 3}}, headers=h).json() == {
        "ilatea.intro.seen": True, "visits": 3}
    assert cl.patch("/api/game/flags", json={"flags": {"visits": None}}, headers=h).json() == {"ilatea.intro.seen": True}

    # A new proxy process on the same file (restart): same session cookie, same state.
    cl2 = fixture_client(tmp_path)
    cl2.cookies.set(COOKIE, cl.cookies.get(COOKIE))
    body = cl2.get("/api/game/state").json()
    schema_validator("GameState").validate(body)
    assert body["saves"][0]["slot"] == "auto" and body["saves"][0]["data"] == SAVE and body["saves"][0]["rev"] == 2
    assert body["settings"]["hint_level"] == "low" and body["flags"] == {"ilatea.intro.seen": True}


GAME_WRITES = [("put", "/api/game/saves/auto", SAVE), ("put", "/api/game/settings", DEFAULT_SETTINGS),
               ("patch", "/api/game/flags", {"flags": {"a.b": True}}), ("delete", "/api/game/session", None)]


@pytest.mark.parametrize("method,path,body", GAME_WRITES, ids=[f"{m} {p}" for m, p, _ in GAME_WRITES])
def test_every_game_write_needs_the_session_csrf_token_and_a_same_host_origin(tmp_path, method, path, body):
    cl = fixture_client(tmp_path)
    csrf = sign_in(cl)
    kw = {"json": body} if body is not None else {}
    for h in ({}, {CSRF_HEADER: "wrong"}, {CSRF_HEADER: "sign-in"}, {CSRF_HEADER: csrf, "Origin": "http://evil.example"},
              {CSRF_HEADER: csrf, "Origin": "null"}):
        r = cl.request(method.upper(), path, headers=h, **kw)
        assert r.status_code == 403 and r.json()["error"]["code"] == "forbidden", (h, r.text)
    state = cl.get("/api/game/state").json()
    assert state["saves"] == [] and state["flags"] == {} and state["settings"] == DEFAULT_SETTINGS  # nothing was written
    assert cl.request(method.upper(), path, headers={CSRF_HEADER: csrf}, **kw).status_code == 200


def test_a_key_bound_to_two_actions_is_refused(tmp_path):
    cl = fixture_client(tmp_path)
    h = {CSRF_HEADER: sign_in(cl)}
    keys = {**DEFAULT_SETTINGS["keys"], "menu": ["KeyM", "KeyW"]}  # KeyW also walks up
    r = cl.put("/api/game/settings", json={**DEFAULT_SETTINGS, "keys": keys}, headers=h)
    assert r.status_code == 422 and r.json()["error"]["code"] == "validation_failed"
    assert "KeyW" in r.json()["error"]["message"]
    assert cl.get("/api/game/state").json()["settings"] == DEFAULT_SETTINGS


def test_players_are_kept_apart(tmp_path):
    a = fixture_client(tmp_path)
    b = fixture_client(tmp_path)
    ha = {CSRF_HEADER: sign_in(a)}
    hb = {CSRF_HEADER: sign_in(b, load("players.json")["players"][1]["token"])}
    a.put("/api/game/saves/auto", json=SAVE, headers=ha)
    b.patch("/api/game/flags", json={"flags": {"b.only": True}}, headers=hb)
    assert b.get("/api/game/state").json()["saves"] == []
    assert a.get("/api/game/state").json()["flags"] == {}
    assert a.get("/api/game/session").json()["player"]["id"] != b.get("/api/game/session").json()["player"]["id"]


def test_sign_out_ends_the_session(tmp_path):
    cl = fixture_client(tmp_path)
    csrf = sign_in(cl)
    cookie = cl.cookies.get(COOKIE)
    assert cl.delete("/api/game/session").status_code == 403  # no CSRF header
    r = cl.delete("/api/game/session", headers={CSRF_HEADER: csrf})
    assert r.status_code == 200 and r.json()["signed_in"] is False
    cl.cookies.set(COOKIE, cookie)  # the old cookie no longer works
    assert cl.get("/api/game/state").status_code == 401


@pytest.mark.parametrize("bad", [
    {**SAVE, "scene": "space"},
    {**SAVE, "extra": 1},
    {**SAVE, "iso": {**SAVE["iso"], "view": "world"}},
    {**SAVE, "overworld": {"x": -1, "y": 0, "facing": "S"}},
])
def test_save_body_is_validated(tmp_path, bad):
    cl = fixture_client(tmp_path)
    r = cl.put("/api/game/saves/auto", json=bad, headers={CSRF_HEADER: sign_in(cl)})
    assert r.status_code == 422 and r.json()["error"]["code"] == "validation_failed"


def test_bad_slot_and_bad_flag_names(tmp_path):
    cl = fixture_client(tmp_path)
    h = {CSRF_HEADER: sign_in(cl)}
    assert cl.put("/api/game/saves/9", json=SAVE, headers=h).status_code == 400
    assert cl.patch("/api/game/flags", json={"flags": {"Bad Name": True}}, headers=h).status_code == 422


def test_local_mode_is_off_by_default_and_needs_the_header(tmp_path):
    assert Settings.from_env({}).local_player is False
    cl = fixture_client(tmp_path, RACKWALKER_LOCAL_PLAYER="1")
    s = cl.get("/api/game/session").json()
    assert s["signed_in"] and s["local_mode"] and s["player"]["id"] == 0
    assert cl.put("/api/game/saves/auto", json=SAVE).status_code == 403
    assert cl.put("/api/game/saves/auto", json=SAVE, headers={CSRF_HEADER: s["csrf_token"]}).status_code == 200
    assert cl.get("/api/game/state").json()["saves"][0]["data"] == SAVE


def test_game_routes_keep_the_host_allow_list(tmp_path):
    cl = fixture_client(tmp_path)
    assert cl.get("/api/game/session", headers={"Host": "evil.example"}).status_code == 400


def test_no_state_file_until_a_player_signs_in(tmp_path):
    cl = fixture_client(tmp_path)
    cl.get("/api/health")
    assert cl.get("/api/game/session").json()["signed_in"] is False  # a signed-out check never needs the file
    assert not (tmp_path / "game.sqlite3").exists()
    sign_in(cl)
    assert (tmp_path / "game.sqlite3").stat().st_mode & 0o077 == 0


def test_a_slow_game_store_never_stalls_the_other_routes(tmp_path, monkeypatch):
    # Opening the store (create, migrate, fsync) can take seconds on a busy disk; it runs off the event loop.
    import threading
    import time

    from rackwalker_proxy import gamestore

    opened, release = threading.Event(), threading.Event()
    real_init = gamestore.GameStore.__init__

    def slow_init(self, *a, **kw):
        opened.set()
        assert release.wait(10)
        real_init(self, *a, **kw)

    monkeypatch.setattr(gamestore.GameStore, "__init__", slow_init)
    with fixture_client(tmp_path) as cl:
        done: list[int] = []
        t = threading.Thread(target=lambda: done.append(cl.post("/api/game/session", json={"token": PLAYER_TOKEN},
                                                                headers={CSRF_HEADER: "sign-in"}).status_code))
        t.start()
        assert opened.wait(10)  # the sign-in is now inside the store's open
        t0 = time.monotonic()
        assert cl.get("/api/health").status_code == 200
        assert cl.get("/api/game/session").json()["signed_in"] is False
        assert time.monotonic() - t0 < 2
        release.set()
        t.join(10)
    assert done == [200]


# ------------------------------------------------------------------------------------------- NetBox identity


def nb_transport(status: int, body: dict | None = None):
    seen = []
    tokens = nb_transport.token_lookups = []   # the lookups of the player's own token record

    def handler(req: httpx.Request) -> httpx.Response:
        if token_record(req) is not None:
            tokens.append(req)
            return token_record(req) if status == 200 else httpx.Response(status, json=body or {})
        seen.append(req)
        # the sign-in check, and the read-only user lookup the proxy makes (with its own token) before a write
        assert req.url.path in ("/api/authentication-check/", "/api/users/users/7/")
        return httpx.Response(status, json=body or {})
    return httpx.MockTransport(handler), seen


def netbox_client(tmp_path, transport) -> TestClient:
    s = Settings.from_env({"NETBOX_URL": "http://netbox.example.invalid", "NETBOX_TOKEN": "nbt_proxy.proxysecret",
                           "RACKWALKER_STATE_DIR": str(tmp_path)})
    return TestClient(base_url=LOCAL, app=create_app(s, transport=transport))


def test_netbox_sign_in_uses_the_players_token_at_sign_in_and_never_again(tmp_path, caplog):
    t, seen = nb_transport(200, {"id": 7, "username": "jdoe", "first_name": "Jo", "last_name": "Doe", "is_staff": False})
    cl = netbox_client(tmp_path, t)
    r = cl.post("/api/game/session", json={"token": "nbt_player.playersecret"}, headers={CSRF_HEADER: "x"})
    assert r.status_code == 200
    assert r.json()["player"] == {"id": 7, "username": "jdoe", "display_name": "Jo Doe"}
    assert [q.headers["authorization"] for q in seen] == ["Bearer nbt_player.playersecret"]
    # ... and once more, in the same sign-in, to ask NetBox what that token may do (its own record, by its public key)
    asked = nb_transport.token_lookups
    assert [(q.url.path, q.url.params["key"], q.headers["authorization"]) for q in asked] == [("/api/users/tokens/", "player", "Bearer nbt_player.playersecret")]
    assert "playersecret" not in str(asked[0].url)
    cl.get("/api/game/state")
    assert len(seen) == 1 and len(asked) == 1  # the token is not used again
    assert "playersecret" not in caplog.text and "playersecret" not in r.text


@pytest.mark.parametrize("status,code,http", [(403, "unauthorized", 401), (401, "unauthorized", 401), (500, "netbox_error", 502)])
def test_netbox_refusals(tmp_path, status, code, http):
    t, _ = nb_transport(status, {"detail": "Invalid v2 token"})
    r = netbox_client(tmp_path, t).post("/api/game/session", json={"token": "nbt_player.playersecret"}, headers={CSRF_HEADER: "x"})
    assert r.status_code == http and r.json()["error"]["code"] == code


def test_netbox_down_is_unreachable(tmp_path):
    def boom(req):
        raise httpx.ConnectError("refused")
    r = netbox_client(tmp_path, httpx.MockTransport(boom)).post(
        "/api/game/session", json={"token": "nbt_player.playersecret"}, headers={CSRF_HEADER: "x"})
    assert r.status_code == 502 and r.json()["error"]["code"] == "netbox_unreachable"


# ------------------------------------------------------------------------------------------- the store itself


def test_store_migrates_and_refuses_newer_files(tmp_path):
    p = tmp_path / "g.sqlite3"
    s = GameStore(p)
    assert s.version == SCHEMA_VERSION
    s.close()
    GameStore(p).close()  # re-open: idempotent
    db = sqlite3.connect(p)
    db.execute(f"PRAGMA user_version = {SCHEMA_VERSION + 1}")
    db.commit()
    db.close()
    with pytest.raises(StoreError, match="schema version"):
        GameStore(p)


def test_migrations_run_in_order_and_a_failing_step_leaves_the_old_version():
    db = sqlite3.connect(":memory:", isolation_level=None)
    steps = ["CREATE TABLE a (x INTEGER);", "ALTER TABLE a ADD COLUMN y TEXT; INSERT INTO a (x, y) VALUES (1, 'one');"]
    assert migrate(db, steps) == 2
    assert db.execute("SELECT x, y FROM a").fetchall() == [(1, "one")]
    with pytest.raises(sqlite3.OperationalError):
        migrate(db, steps + ["CREATE TABLE b (z INTEGER); INSERT INTO nope VALUES (1);"])
    assert db.execute("PRAGMA user_version").fetchone()[0] == 2
    assert db.execute("SELECT count(*) FROM sqlite_master WHERE name = 'b'").fetchone()[0] == 0
    assert migrate(db, steps) == 2  # already there: nothing runs again
    assert db.execute("SELECT count(*) FROM a").fetchone()[0] == 1


def test_a_semicolon_in_a_string_comment_or_trigger_does_not_split_a_migration():
    script = """
    -- a comment; with a semicolon
    CREATE TABLE t (v TEXT, n INTEGER DEFAULT 0); /* block; comment */
    INSERT INTO t (v) VALUES ('a;b'), ('it''s; fine');
    CREATE TRIGGER t_bump AFTER INSERT ON t BEGIN
        UPDATE t SET n = n + 1 WHERE rowid = NEW.rowid;
        UPDATE t SET v = v || ';' WHERE rowid = NEW.rowid;
    END;
    -- trailing comment
    """
    parts = statements(script)
    assert len(parts) == 3 and parts[2].startswith("CREATE TRIGGER") and parts[2].endswith("END;")
    db = sqlite3.connect(":memory:", isolation_level=None)
    assert migrate(db, [script, "INSERT INTO t (v) VALUES ('c');"]) == 2
    assert db.execute("SELECT v, n FROM t ORDER BY rowid").fetchall() == [("a;b", 0), ("it's; fine", 0), ("c;", 1)]


def test_store_session_expiry(tmp_path):
    now = [1000.0]
    s = GameStore(tmp_path / "g.sqlite3", clock=lambda: now[0])
    s.upsert_player(3, "p", None)
    value, sess = s.new_session(3)
    assert s.session(value).user_id == 3
    now[0] = sess.expires_at + 1
    assert s.session(value) is None
    assert s.session(None) is None and s.session("x" * 500) is None


def test_store_flag_cap(tmp_path):
    s = GameStore(tmp_path / "g.sqlite3")
    s.upsert_player(1, "p", None)
    s.patch_flags(1, {f"f{i}": True for i in range(500)})
    with pytest.raises(StoreError):
        s.patch_flags(1, {"one.more": True})
    assert len(s.flags(1)) == 500  # the refused patch left nothing behind


def test_game_state_never_reaches_netbox(tmp_path):
    """No game state is ever sent to NetBox: the only requests are the identity check at sign-in and the read-only user
    lookup before each write."""
    t, seen = nb_transport(200, {"id": 7, "username": "jdoe", "first_name": "", "last_name": ""})
    cl = netbox_client(tmp_path, t)
    csrf = cl.post("/api/game/session", json={"token": "nbt_player.playersecret"}, headers={CSRF_HEADER: "x"}).json()["csrf_token"]
    h = {CSRF_HEADER: csrf}
    cl.put("/api/game/saves/auto", json=SAVE, headers=h)
    cl.put("/api/game/settings", json=DEFAULT_SETTINGS, headers=h)
    cl.patch("/api/game/flags", json={"flags": {"a": 1}}, headers=h)
    cl.get("/api/game/state")
    assert [q.method for q in seen] == ["GET"] * 4
    assert [q.url.path for q in seen[1:]] == ["/api/users/users/7/"] * 3
    assert all(not q.content for q in seen)
    assert json.loads(cl.get("/api/game/session").content)["player"]["display_name"] is None


def test_openapi_lists_exactly_the_routes_the_proxy_serves(tmp_path):
    import yaml
    spec = yaml.safe_load((REPO / "contracts" / "openapi.yaml").read_text())
    documented = {(m.upper(), p) for p, ops in spec["paths"].items() for m in ops}
    app = fixture_client(tmp_path).app
    served = {(m, r.path) for r in app.routes if r.path.startswith("/api/") and r.path != "/api/openapi.json" for m in getattr(r, "methods", ())
              if m != "HEAD"}
    assert served == documented
