"""A signed-in player's NetBox user is rechecked: on a bounded interval for reads, and just before every write, so a
user NetBox deactivates or deletes loses the game session promptly. NetBox being down does not sign anyone out."""
import httpx
import pytest
from fastapi.testclient import TestClient

from rackwalker_proxy import game
from rackwalker_proxy.app import Settings, create_app
from rackwalker_proxy.game import CSRF_HEADER

from .conftest import LOCAL

SAVE = {"scene": "overworld", "overworld": {"x": 3, "y": 4, "facing": "S"}, "iso": None}


class FakeNetBox:
    """Answers the sign-in check (player token) and the user lookup (proxy token)."""

    def __init__(self):
        self.active: bool | None = True   # None: the user is gone (404)
        self.down = False
        self.lookups = 0

    def __call__(self, req: httpx.Request) -> httpx.Response:
        if self.down:
            raise httpx.ConnectError("refused")
        if req.url.path == "/api/authentication-check/":
            return httpx.Response(200, json={"id": 7, "username": "jdoe", "first_name": "", "last_name": ""})
        if req.url.path == "/api/users/users/7/":
            assert req.headers["authorization"] == "Bearer nbt_proxy.proxysecret"  # the proxy's token, never the player's
            self.lookups += 1
            if self.active is None:
                return httpx.Response(404, json={"detail": "No User matches the given query."})
            return httpx.Response(200, json={"id": 7, "username": "jdoe", "is_active": self.active})
        return httpx.Response(404)


@pytest.fixture
def clock(monkeypatch):
    t = [1000.0]
    monkeypatch.setattr(game, "_now", lambda: t[0])
    return t


def client(tmp_path, nb: FakeNetBox) -> tuple[TestClient, str]:
    s = Settings.from_env({"NETBOX_URL": "http://netbox.example.invalid", "NETBOX_TOKEN": "nbt_proxy.proxysecret",
                           "RACKWALKER_STATE_DIR": str(tmp_path)})
    cl = TestClient(base_url=LOCAL, app=create_app(s, transport=httpx.MockTransport(nb)))
    r = cl.post("/api/game/session", json={"token": "nbt_player.playersecret"}, headers={CSRF_HEADER: "x"})
    assert r.status_code == 200, r.text
    return cl, r.json()["csrf_token"]


def test_reads_recheck_on_the_interval_and_a_deactivated_user_is_signed_out(tmp_path, clock):
    nb = FakeNetBox()
    cl, _ = client(tmp_path, nb)
    assert cl.get("/api/game/state").status_code == 200
    assert nb.lookups == 0  # sign-in itself vouched for the user
    clock[0] += game.RECHECK_SECONDS - 1
    assert cl.get("/api/game/state").status_code == 200 and nb.lookups == 0
    nb.active = False
    clock[0] += 2
    r = cl.get("/api/game/state")
    assert r.status_code == 401 and r.json()["error"]["code"] == "unauthorized" and nb.lookups == 1
    s = cl.get("/api/game/session")
    assert s.json()["signed_in"] is False
    # reactivating the user does not bring the ended session back
    nb.active = True
    assert cl.get("/api/game/state").status_code == 401


def test_every_write_rechecks_first(tmp_path, clock):
    nb = FakeNetBox()
    cl, csrf = client(tmp_path, nb)
    clock[0] += game.WRITE_FRESH_SECONDS + 1
    assert cl.put("/api/game/saves/auto", json=SAVE, headers={CSRF_HEADER: csrf}).status_code == 200
    assert nb.lookups == 1
    nb.active = False
    clock[0] += game.WRITE_FRESH_SECONDS + 1  # well inside the read interval: only the write triggers the check
    r = cl.put("/api/game/saves/auto", json=SAVE, headers={CSRF_HEADER: csrf})
    assert r.status_code == 401 and nb.lookups == 2


def test_a_deleted_user_is_signed_out(tmp_path, clock):
    nb = FakeNetBox()
    cl, _ = client(tmp_path, nb)
    nb.active = None
    clock[0] += game.RECHECK_SECONDS + 1
    assert cl.get("/api/game/session").json()["signed_in"] is False


def test_netbox_down_keeps_the_session_and_refuses_writes_after_the_grace(tmp_path, clock):
    nb = FakeNetBox()
    cl, csrf = client(tmp_path, nb)
    nb.down = True
    clock[0] += game.RECHECK_SECONDS + 1
    assert cl.get("/api/game/state").status_code == 200
    assert cl.put("/api/game/saves/auto", json=SAVE, headers={CSRF_HEADER: csrf}).status_code == 200
    clock[0] += game.GRACE_SECONDS
    r = cl.put("/api/game/saves/auto", json=SAVE, headers={CSRF_HEADER: csrf})
    assert r.status_code == 502 and r.json()["error"]["code"] == "netbox_unreachable"
    assert cl.get("/api/game/session").json()["signed_in"] is True
    nb.down = False
    assert cl.put("/api/game/saves/auto", json=SAVE, headers={CSRF_HEADER: csrf}).status_code == 200


def test_netbox_writes_recheck_the_player_too(tmp_path, clock):
    nb = FakeNetBox()
    s = Settings.from_env({"NETBOX_URL": "http://netbox.example.invalid", "NETBOX_TOKEN": "nbt_proxy.proxysecret",
                           "RACKWALKER_STATE_DIR": str(tmp_path), "WRITE_ENABLED": "1"})
    app = create_app(s, transport=httpx.MockTransport(nb))
    cl = TestClient(base_url=LOCAL, app=app)
    csrf = cl.post("/api/game/session", json={"token": "nbt_player.playersecret"}, headers={CSRF_HEADER: "x"}).json()["csrf_token"]
    nb.active = False
    clock[0] += game.WRITE_FRESH_SECONDS + 1
    r = cl.post("/api/changes/preview", json={"ops": []}, headers={CSRF_HEADER: csrf})
    assert r.status_code == 401
    assert "signed-in player" in r.json()["error"]["message"]
