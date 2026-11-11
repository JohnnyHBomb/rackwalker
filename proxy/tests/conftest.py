import json
import sys
from pathlib import Path

import httpx
import pytest

REPO = Path(__file__).resolve().parents[2]
LOCAL = "http://127.0.0.1"  # the proxy only answers Host 127.0.0.1/localhost (TestClient defaults to "testserver")
SCHEMA = json.loads((REPO / "contracts" / "scene.schema.json").read_text())
INDEX = json.loads((REPO / "fixtures" / "index.json").read_text())["fixtures"]
sys.path.insert(0, str(REPO / "devnetbox"))


def load(name: str):
    return json.loads((REPO / "fixtures" / name).read_text())


@pytest.fixture(scope="session")
def repo() -> Path:
    return REPO


@pytest.fixture(scope="session", autouse=True)
def no_leftovers_on_the_live_stack():
    """A live run that was cut short leaves its made-up site in the dev NetBox, and every later live test that compares
    the stack with the seed would fail. Before the first live test, what such a run left behind is removed, so the live
    suites can simply be started again."""
    import os

    if os.environ.get("RACKWALKER_LIVE_NETBOX") == "1" and os.environ.get("NETBOX_ADMIN_TOKEN"):
        from .test_live_quests import clear_harbour

        with httpx.Client(base_url=os.environ.get("NETBOX_URL", "") + "/api/", timeout=30,
                          headers={"Authorization": f"Bearer {os.environ['NETBOX_ADMIN_TOKEN']}"}) as nbx:
            clear_harbour(nbx)
    yield


def schema_validator(defname: str):
    from rackwalker_proxy.schema import validator
    return validator(defname)



TOKEN_ID = 77


def token_record(req: httpx.Request, write_enabled: bool = True, expires: str | None = None, enabled: bool = True,
                 gone: bool = False) -> httpx.Response | None:
    """What a made-up NetBox says about the token a player signs in with: the token list filtered by the token's public
    key (asked with the player's own token at sign-in) and the record by id (asked with the proxy's token before a
    write). None for any other path, so a fake can try this first."""
    path = req.url.path
    row = {"id": TOKEN_ID, "version": 2, "key": req.url.params.get("key"), "write_enabled": write_enabled, "enabled": enabled, "expires": expires}
    if path == "/api/users/tokens/":
        return httpx.Response(200, json={"count": 1, "results": [row]}, request=req)
    if path == f"/api/users/tokens/{TOKEN_ID}/":
        return httpx.Response(404, json={"detail": "No Token matches the given query."}, request=req) if gone else httpx.Response(200, json=row, request=req)
    return None
