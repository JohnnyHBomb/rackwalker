"""Every fixture validates against the JSON Schema AND the generated pydantic models; mutations are rejected."""
import copy

import jsonschema
import pytest
from jsonschema import Draft202012Validator

from rackwalker_proxy import contracts_gen as c

from .conftest import INDEX, SCHEMA, load

FORMAT = Draft202012Validator.FORMAT_CHECKER


def validator(defname: str) -> Draft202012Validator:
    sub = {"$schema": SCHEMA["$schema"], "$defs": SCHEMA["$defs"], "$ref": f"#/$defs/{defname}"}
    return Draft202012Validator(sub, format_checker=FORMAT)


CASES = [(f["file"], f["schema"]) for f in INDEX] + [(f["request_file"], f["request_schema"]) for f in INDEX if "request_file" in f]


def test_schema_is_valid_2020_12():
    Draft202012Validator.check_schema(SCHEMA)


def test_every_get_endpoint_has_a_fixture():
    paths = {f["path"].rsplit("/", 1)[0] if f["path"][-1].isdigit() else f["path"] for f in INDEX if f["method"] == "GET"}
    for p in ["/api/health", "/api/scene/world", "/api/scene/site", "/api/scene/rack", "/api/device", "/api/trace/interface"]:
        assert p in paths, p


@pytest.mark.parametrize("name,defname", CASES)
def test_fixture_matches_json_schema(name, defname):
    validator(defname).validate(load(name))


@pytest.mark.parametrize("name,defname", CASES)
def test_fixture_matches_pydantic(name, defname):
    model = getattr(c, defname)
    obj = model.model_validate(load(name))
    # Contract rule: Python serialises with model_dump(mode="json", exclude_unset=True). The round trip must
    # still validate and keep every key (optional keys absent stay absent; required-nullable keys stay null).
    dumped = obj.model_dump(mode="json", exclude_unset=True)
    validator(defname).validate(dumped)
    assert sorted(_keys(dumped)) == sorted(_keys(load(name)))


def _keys(x, prefix=""):
    if isinstance(x, dict):
        for k, v in x.items():
            yield prefix + "/" + k
            yield from _keys(v, prefix + "/" + k)
    elif isinstance(x, list):
        for i, v in enumerate(x):
            yield from _keys(v, f"{prefix}/{i}")


def test_mutations_rejected_on_both_sides():
    site = load("site-1.json")
    bad_extra = copy.deepcopy(site)
    bad_extra["racks"][0]["colour"] = "red"
    bad_enum = copy.deepcopy(site)
    bad_enum["racks"][0]["iso_facing"] = "NE"
    bad_missing = copy.deepcopy(site)
    del bad_missing["devices"][0]["primary_ip"]
    for bad in (bad_extra, bad_enum, bad_missing):
        with pytest.raises(jsonschema.ValidationError):
            validator("SiteScene").validate(bad)
        with pytest.raises(Exception):
            c.SiteScene.model_validate(bad)


def test_changeset_ops_are_discriminated():
    req = load("changes-request.json")
    cs = c.ChangeSet.model_validate(req)
    assert [type(op.root if hasattr(op, "root") else op).__name__ for op in cs.ops] == [
        "MoveRackOp", "CreateRackOp", "PlaceDeviceOp", "UnplaceDeviceOp", "UpdateFieldsOp", "MoveRackOp"]
    bad = copy.deepcopy(req)
    bad["ops"][0]["op"] = "teleport_rack"
    with pytest.raises(jsonschema.ValidationError):
        validator("ChangeSet").validate(bad)
    with pytest.raises(Exception):
        c.ChangeSet.model_validate(bad)
    bad2 = copy.deepcopy(req)
    bad2["ops"][2]["fields"]["position"] = 0.3  # not a multiple of 0.5
    with pytest.raises(jsonschema.ValidationError):
        validator("ChangeSet").validate(bad2)
    with pytest.raises(Exception):
        c.ChangeSet.model_validate(bad2)


def test_preview_target_may_echo_a_ref():
    """1.1.0: a preview op on an object created earlier in the ChangeSet keeps '@<client_ref>' in target.id."""
    res = load("changes-preview.json")
    assert res["ops"][5]["target"] == {"kind": "rack", "id": "@newrack"}
    validator("ChangeResult").validate(res)
    c.ChangeResult.model_validate(res)
    bad = copy.deepcopy(res)
    bad["ops"][5]["target"]["id"] = "newrack"  # a ref without '@' is neither Id nor Ref
    with pytest.raises(jsonschema.ValidationError):
        validator("ChangeResult").validate(bad)
