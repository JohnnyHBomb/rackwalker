import json
import sys
from pathlib import Path

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


def schema_validator(defname: str):
    from rackwalker_proxy.schema import validator
    return validator(defname)

