"""Generated code and fixtures must match their sources (contracts/scene.schema.json, devnetbox/seed_model.py)."""
import subprocess
import sys

import yaml

from .conftest import REPO, SCHEMA


def test_pydantic_models_regenerate_identically(tmp_path):
    out = tmp_path / "gen.py"
    subprocess.run([str(REPO / "proxy" / "gen-models.sh"), str(out)], check=True, capture_output=True,
                   env={"VENV": str(REPO / ".venv") if (REPO / ".venv").exists() else sys.prefix, "PATH": "/usr/bin:/bin"})
    assert out.read_text() == (REPO / "proxy" / "rackwalker_proxy" / "contracts_gen.py").read_text(), \
        "contracts_gen.py is stale: run proxy/gen-models.sh"


def test_fixtures_regenerate_identically():
    r = subprocess.run([sys.executable, str(REPO / "fixtures" / "make_fixtures.py"), "--check"], capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr


def test_openapi_refs_resolve():
    spec = yaml.safe_load((REPO / "contracts" / "openapi.yaml").read_text())
    refs = []

    def walk(x):
        if isinstance(x, dict):
            for k, v in x.items():
                if k == "$ref":
                    refs.append(v)
                walk(v)
        elif isinstance(x, list):
            for v in x:
                walk(v)
    walk(spec)
    assert refs
    for r in refs:
        f, _, frag = r.partition("#")
        if f:
            assert f == "scene.schema.json", r
            assert frag.startswith("/$defs/") and frag.split("/")[-1] in SCHEMA["$defs"], r
    paths = set(spec["paths"])
    assert paths == {"/api/health", "/api/scene/world", "/api/scene/site/{site_id}", "/api/scene/rack/{rack_id}",
                     "/api/device/{device_id}", "/api/trace/{kind}/{port_id}", "/api/changes/preview", "/api/changes/apply",
                     "/api/game/session", "/api/game/state", "/api/game/saves/{slot}", "/api/game/settings", "/api/game/flags",
                     "/api/quests/gaps/{site_id}", "/api/game/quests", "/api/game/quests/answer", "/api/game/quests/preview",
                     "/api/game/quests/apply", "/api/game/quests/verify", "/api/game/practice/reset", "/api/game/boss/ack"}
