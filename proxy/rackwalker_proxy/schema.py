"""contracts/scene.schema.json at run time: the request validator and the enums the proxy derives from it.

The generated pydantic models (contracts_gen.py) are lax (they coerce types), so request bodies are validated
against the JSON Schema itself (contracts/api.md) before they reach the change planner.
"""
from __future__ import annotations

import json
from functools import cache
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator

SCHEMA_PATH = Path(__file__).resolve().parents[2] / "contracts" / "scene.schema.json"
SCHEMA: dict[str, Any] = json.loads(SCHEMA_PATH.read_text())
CONTRACT_VERSION: str = SCHEMA["$defs"]["Meta"]["properties"]["contract_version"]["const"]


@cache
def validator(defname: str) -> Draft202012Validator:
    sub = {"$schema": SCHEMA["$schema"], "$defs": SCHEMA["$defs"], "$ref": f"#/$defs/{defname}"}
    return Draft202012Validator(sub, format_checker=Draft202012Validator.FORMAT_CHECKER)


def enum(defname: str) -> list[Any]:
    """Non-null enum values of a $def (e.g. ThemeId, PortKind, IsoFacing)."""
    return [v for v in SCHEMA["$defs"][defname]["enum"] if v is not None]


def errors(defname: str, body: Any, limit: int = 10) -> list[dict[str, Any]]:
    """Schema errors as [{path, message}], deepest-first, capped; [] when the body is valid."""
    errs = sorted(validator(defname).iter_errors(body), key=lambda e: (-len(e.absolute_path), list(map(str, e.absolute_path))))
    return [{"path": "/" + "/".join(map(str, e.absolute_path)), "message": e.message[:300]} for e in errs[:limit]]
