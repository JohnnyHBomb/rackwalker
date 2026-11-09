"""A signed-in player's NetBox user is rechecked: on a bounded interval for reads, and before every write, so a user
NetBox deactivates or deletes loses the game session at once. NetBox being down does not sign anyone out, but nothing is
written while NetBox cannot confirm the user."""
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
    assert r.status_code == 401 and nb.lookups == 1
    assert r.json()["error"]["code"] == "unauthorized" and r.json()["error"]["details"] == {"reason": "inactive"}
    assert "deactivated" in r.json()["error"]["message"]
    s = cl.get("/api/game/session")
    assert s.json()["signed_in"] is False
    # reactivating the user does not bring the ended session back
    nb.active = True
    assert cl.get("/api/game/state").status_code == 401


def test_a_write_right_after_deactivation_is_refused(tmp_path, clock):
    nb = FakeNetBox()
    cl, csrf = client(tmp_path, nb)
    assert cl.put("/api/game/saves/auto", json=SAVE, headers={CSRF_HEADER: csrf}).status_code == 200
    assert nb.lookups == 1  # the write checked, though sign-in had just vouched for the user
    nb.active = False  # no time passes: there is no window in which a stale check lets a write through
    r = cl.put("/api/game/settings", json={}, headers={CSRF_HEADER: csrf})
    assert r.status_code == 401 and nb.lookups == 2
    assert r.json()["error"]["details"] == {"reason": "inactive"}


def test_a_deleted_user_is_signed_out_with_the_reason(tmp_path, clock):
    nb = FakeNetBox()
    cl, csrf = client(tmp_path, nb)
    nb.active = None
    r = cl.patch("/api/game/flags", json={"flags": {"a": True}}, headers={CSRF_HEADER: csrf})
    assert r.status_code == 401 and r.json()["error"]["details"] == {"reason": "gone"}
    assert "deleted" in r.json()["error"]["message"]
    assert cl.get("/api/game/session").json()["signed_in"] is False


def test_deactivation_ends_every_session_of_the_player(tmp_path, clock):
    nb = FakeNetBox()
    cl1, csrf1 = client(tmp_path, nb)
    s = Settings.from_env({"NETBOX_URL": "http://netbox.example.invalid", "NETBOX_TOKEN": "nbt_proxy.proxysecret",
                           "RACKWALKER_STATE_DIR": str(tmp_path)})
    cl2 = TestClient(base_url=LOCAL, app=create_app(s, transport=httpx.MockTransport(nb)))
    assert cl2.post("/api/game/session", json={"token": "nbt_player.playersecret"}, headers={CSRF_HEADER: "x"}).status_code == 200
    nb.active = False
    assert cl1.put("/api/game/saves/auto", json=SAVE, headers={CSRF_HEADER: csrf1}).status_code == 401
    # the second browser's session is gone from the store, not just unchecked
    assert cl2.get("/api/game/session").json()["signed_in"] is False


def test_netbox_down_keeps_the_session_and_refuses_writes(tmp_path, clock):
    nb = FakeNetBox()
    cl, csrf = client(tmp_path, nb)
    nb.down = True
    clock[0] += game.RECHECK_SECONDS + 1
    assert cl.get("/api/game/state").status_code == 200
    r = cl.put("/api/game/saves/auto", json=SAVE, headers={CSRF_HEADER: csrf})
    assert r.status_code == 502 and r.json()["error"]["code"] == "netbox_unreachable"
    assert cl.get("/api/game/session").json()["signed_in"] is True
    # signing out does not need NetBox
    assert cl.delete("/api/game/session", headers={CSRF_HEADER: csrf}).json()["signed_in"] is False
    nb.down = False
    cl, csrf = client(tmp_path, nb)
    assert cl.put("/api/game/saves/auto", json=SAVE, headers={CSRF_HEADER: csrf}).status_code == 200


def test_the_read_interval_is_a_setting(tmp_path, clock):
    nb = FakeNetBox()
    s = Settings.from_env({"NETBOX_URL": "http://netbox.example.invalid", "NETBOX_TOKEN": "nbt_proxy.proxysecret",
                           "RACKWALKER_STATE_DIR": str(tmp_path), "RACKWALKER_SESSION_RECHECK": "5"})
    assert s.session_recheck == 5.0
    cl = TestClient(base_url=LOCAL, app=create_app(s, transport=httpx.MockTransport(nb)))
    assert cl.post("/api/game/session", json={"token": "nbt_player.playersecret"}, headers={CSRF_HEADER: "x"}).status_code == 200
    clock[0] += 6
    assert cl.get("/api/game/state").status_code == 200 and nb.lookups == 1


def test_netbox_writes_recheck_the_player_too(tmp_path, clock):
    nb = FakeNetBox()
    s = Settings.from_env({"NETBOX_URL": "http://netbox.example.invalid", "NETBOX_TOKEN": "nbt_proxy.proxysecret",
                           "RACKWALKER_STATE_DIR": str(tmp_path), "WRITE_ENABLED": "1"})
    app = create_app(s, transport=httpx.MockTransport(nb))
    cl = TestClient(base_url=LOCAL, app=app)
    csrf = cl.post("/api/game/session", json={"token": "nbt_player.playersecret"}, headers={CSRF_HEADER: "x"}).json()["csrf_token"]
    nb.active = False
    r = cl.post("/api/changes/preview", json={"ops": []}, headers={CSRF_HEADER: csrf})
    assert r.status_code == 401 and r.json()["error"]["details"] == {"reason": "inactive"}
