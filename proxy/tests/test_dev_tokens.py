"""Regression: the proxy ran on the NetBox admin superuser token. `nb.sh env` now hands out the proxy
user's read-only token by default (write_enabled=false, minted by devnetbox/proxy_tokens.py), the write token only
on request, and the admin token only when asked for by name. No NetBox needed: PY is a stub that stands in for
proxy_tokens.py."""
from __future__ import annotations

import os
import subprocess
import uuid

import pytest

from .conftest import REPO

NB = REPO / "devnetbox" / "nb.sh"


@pytest.fixture
def stack(tmp_path):
    project = f"rw-unittest-{uuid.uuid4().hex[:8]}"
    secrets = REPO / "devnetbox" / ".stacks" / f"{project}.env"
    secrets.parent.mkdir(mode=0o700, exist_ok=True)
    secrets.write_text("API_TOKEN_PEPPER_1=x\nSUPERUSER_API_KEY=adminkey\nSUPERUSER_API_TOKEN=adminsecret\n")
    stub = tmp_path / "py"
    stub.write_text('#!/bin/sh\n# stub for: $PY proxy_tokens.py ro|rw\necho "nbt_proxy.$2"\n')
    stub.chmod(0o755)
    env = {**os.environ, "NB_PROJECT": project, "NB_PORT": "8999", "PY": str(stub), "DOCKER_HOST": "ssh://nowhere"}
    yield lambda *a: subprocess.run([str(NB), "env", *a], env=env, capture_output=True, text=True, timeout=30)
    secrets.unlink()


def token(out: subprocess.CompletedProcess) -> str:
    assert out.returncode == 0, out.stderr
    return dict(line.split("=", 1) for line in out.stdout.splitlines())["NETBOX_TOKEN"]


def test_env_defaults_to_the_proxy_read_only_token(stack):
    assert token(stack()) == "nbt_proxy.ro"
    assert token(stack("ro")) == "nbt_proxy.ro"
    assert token(stack("rw")) == "nbt_proxy.rw"
    assert token(stack("admin")) == "nbt_adminkey.adminsecret"
    assert stack("root").returncode != 0


def test_makefile_never_gives_the_proxy_the_admin_token():
    blocks = (REPO / "Makefile").read_text().split("\n\n")
    env_rule = next(b for b in blocks if "\nnetbox-env:" in "\n" + b)
    assert "nb.sh env $(if $(filter 1,$(WRITE)),rw,ro)" in env_rule and "env admin" not in env_rule
    live = next(b for b in blocks if "\ntest-live:" in "\n" + b)
    assert "nb.sh env rw" in live and "NETBOX_ADMIN_TOKEN" in live
