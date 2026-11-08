"""Typed NetBox client, trace normalisation and apply, against small hand-written NetBox answers."""
from __future__ import annotations

import asyncio
import json

import httpx
import pytest
from fastapi.testclient import TestClient

from rackwalker_proxy import netbox as nb
from rackwalker_proxy import scene
from rackwalker_proxy.app import Settings, create_app
from rackwalker_proxy.errors import ApiError
from rackwalker_proxy.game import CSRF_HEADER

from .conftest import LOCAL, load, schema_validator
from .netbox_replay import RECORDED_HOST, Replay, load as recordings

TOKEN = "nbt_abcdef123456.0123456789abcdef0123456789abcdef01234567"


def client(handler) -> nb.NetBox:
    return nb.NetBox("http://nb.test", TOKEN, transport=httpx.MockTransport(handler))


def run(coro):
    return asyncio.run(coro)


def test_list_pages_with_offsets_not_next_urls():
    seen = []

    def h(req):
        seen.append(dict(req.url.params))
        off, n = int(req.url.params["offset"]), 2500
        rows = [{"id": i, "slug": "s", "name": "n", "color": "ffffff"} for i in range(off, min(off + 1000, n))]
        return httpx.Response(200, json={"count": n, "next": "http://elsewhere.invalid/", "results": rows})

    roles = run(client(h).list(nb.Role, "dcim/device-roles", {"id": [3, 1]}))
    assert len(roles) == 2500 and [s["offset"] for s in seen] == ["0", "1000", "2000"]
    assert seen[0]["limit"] == "1000"


@pytest.mark.parametrize("resp,code", [
    (httpx.Response(500, text="boom"), "netbox_error"), (httpx.Response(403, json={"detail": "nope"}), "netbox_error"),
    (httpx.Response(200, text="<html>"), "netbox_error")])
def test_http_errors_map_to_contract_codes(resp, code):
    with pytest.raises(ApiError) as e:
        run(client(lambda req: resp).request("GET", "dcim/sites/"))
    assert e.value.code == code and e.value.status == 502
    assert TOKEN not in json.dumps(e.value.body()) and TOKEN.split(".")[1] not in json.dumps(e.value.body())  # nor its secret half


@pytest.mark.parametrize("exc,code", [(httpx.ConnectTimeout("t"), "netbox_unreachable"), (httpx.ConnectError("c"), "netbox_unreachable"),
                                      (httpx.ReadTimeout("r"), "netbox_unreachable")])
def test_transport_errors_are_unreachable(exc, code):
    def h(req):
        raise exc

    with pytest.raises(ApiError) as e:
        run(client(h).request("GET", "status/"))
    assert e.value.code == code


def test_404_becomes_not_found_with_kind_and_id():
    with pytest.raises(ApiError) as e:
        run(client(lambda req: httpx.Response(404, json={"detail": "No Device matches"})).get(nb.Device, "dcim/devices", 9, "device"))
    assert (e.value.code, e.value.message, e.value.details) == ("not_found", "Device 9 does not exist.", {"kind": "device", "id": 9})


def test_unexpected_netbox_shape_is_netbox_error():
    with pytest.raises(ApiError) as e:
        run(client(lambda req: httpx.Response(200, json={"id": 1, "name": "x"})).get(nb.Site, "dcim/sites", 1, "site"))
    assert e.value.code == "netbox_error" and e.value.details["what"] == "dcim/sites"


@pytest.mark.parametrize("version,ok", [("4.7.4", True), ("4.7.0-beta1", True), ("4.6.9", False), ("5.0.0", False), (None, False)])
def test_version_check(version, ok):
    calls = []

    def h(req):
        calls.append(1)
        return httpx.Response(200, json={"netbox-version": version})

    c = client(h)
    if ok:
        assert run(c.version()) == version
        run(c.version())
        assert len(calls) == 1  # cached
    else:
        with pytest.raises(ApiError) as e:
            run(c.version())
        assert e.value.code == "unsupported_netbox_version"


def test_choice_and_select_custom_fields_unwrap():
    r = nb.Rack.model_validate({"id": 1, "site": {"id": 1}, "name": "R", "status": {"value": "active", "label": "Active"},
                                "u_height": 42, "custom_fields": {"iso_facing": {"value": "N", "label": "North (-y)"}, "iso_x": 2}})
    assert (r.status, r.cf("iso_facing"), r.cf("iso_x")) == ("active", "N", 2)
    assert scene.rack(r).iso_facing == "N"


@pytest.mark.parametrize("url,kind", [("http://h/api/dcim/interfaces/3/", "interface"), ("/api/dcim/front-ports/9/", "front_port"),
                                      ("http://h/api/circuits/circuit-terminations/1/", "circuit_termination"),
                                      ("http://h/api/dcim/power-feeds/2/", "power_feed"), ("http://h/api/circuits/provider-networks/1/", None)])
def test_kind_from_url(url, kind):
    assert nb.kind_from_url(url) == kind


# ---- trace normalisation
def _p(kind_ep: str, i: int) -> dict:
    return {"id": i, "url": f"http://h/api/{kind_ep}/{i}/", "name": str(i), "device": {"id": 1, "name": "d"}}


def _ct(i: int, side: str) -> dict:
    return {"id": i, "url": f"http://h/api/circuits/circuit-terminations/{i}/", "circuit": {"id": 1, "cid": "C"}, "term_side": side}


def _cab(i: int) -> dict:
    return {"id": i, "label": f"L{i}", "type": "", "color": "", "status": {"value": "connected"}}


def test_circuit_crossing_gets_its_own_hop():
    segs = scene.normalise(scene.segments_from_trace([
        [[_p("dcim/interfaces", 1)], _cab(10), [_ct(2, "Z")]],
        [[_ct(1, "A")], _cab(11), [_p("dcim/interfaces", 5)]]]))
    assert [(s[0][0].kind, s[1] and s[1].id, s[2][0].kind) for s in segs] == [
        ("interface", 10, "circuit_termination"), ("circuit_termination", None, "circuit_termination"),
        ("circuit_termination", 11, "interface")]
    assert scene.endpoint_state(segs) == (True, False)
    assert segs[0][1].type is None and segs[0][1].color is None  # NetBox "" -> contract null


def test_endpoint_state_rules():
    fp = [[_p("dcim/interfaces", 1)], _cab(1), [_p("dcim/front-ports", 2)]]
    rp = [[_p("dcim/interfaces", 1)], _cab(1), [_p("dcim/rear-ports", 2)]]
    dangling = [[_p("dcim/interfaces", 1)], None, []]
    assert scene.endpoint_state(scene.segments_from_trace([fp])) == (False, False)   # stops at an uncabled patch port
    assert scene.endpoint_state(scene.segments_from_trace([rp])) == (False, True)    # NetBox stops at a rear port only on a split
    assert scene.endpoint_state(scene.segments_from_trace([dangling])) == (False, False)
    assert scene.endpoint_state([]) == (False, False)


def test_path_flattening():
    path = [[_p("dcim/interfaces", 1)], [_cab(10)], [_p("dcim/front-ports", 2)], [_p("dcim/rear-ports", 2)], [_cab(11)],
            [_p("dcim/interfaces", 7)]]
    segs = scene.segments_from_path(path)
    assert [(s[0][0].kind, s[1].id, s[2][0].kind) for s in segs] == [("interface", 10, "front_port"), ("rear_port", 11, "interface")]


# ---- apply through the app, NetBox writes mocked on top of the recorded reads. A write-enabled proxy needs a player:
# the single-user local mode (player "local") with its CSRF header stands in for a signed-in session here.
def _app(on_write):
    return TestClient(base_url=LOCAL, headers={CSRF_HEADER: "local"},
                      app=create_app(Settings(RECORDED_HOST, TOKEN, True, None, cache_ttl=0, local_player=True), transport=Replay(on_write=on_write)))


def test_apply_resolves_refs_writes_in_order_and_reads_back():
    writes = []
    rack2 = recordings()["GET /api/dcim/racks/?id=2&limit=1000&offset=0"]["json"]["results"][0]

    def on_write(req):
        body = json.loads(req.content)
        writes.append((req.method, req.url.path, body))
        if req.method == "POST":
            return httpx.Response(201, json={**rack2, "id": 501, "name": body["name"], "location": {"id": 1},
                                             "custom_fields": {**rack2["custom_fields"], **body["custom_fields"]},
                                             "status": {"value": "planned"}, "u_height": body["u_height"]})
        if "/racks/" in req.url.path:
            return httpx.Response(200, json={**rack2, "id": 501, "custom_fields": {**rack2["custom_fields"], **body["custom_fields"]}})
        return httpx.Response(400, json={"position": ["U20 is already occupied"]})

    req = load("changes-request.json")
    del req["ops"][0]["if_unmodified_since"]
    req["ops"][4]["fields"] = {"description": "x"}  # the fixture's op 4 is an intentional error; make it valid
    with _app(on_write) as cl:
        res = cl.post("/api/changes/apply", json=req).json()
    schema_validator("ChangeResult").validate(res)
    assert [o["status"] for o in res["ops"]] == ["ok", "ok", "error", "skipped", "skipped", "skipped"]
    assert [(m, p) for m, p, _ in writes] == [("PATCH", "/api/dcim/racks/2/"), ("POST", "/api/dcim/racks/"),
                                              ("PATCH", "/api/dcim/devices/27/")]
    assert writes[2][2] == {"rack": 501, "position": 20.0, "face": "front", "location": 1,
                            "changelog_message": "rackwalker (local): fixture example"}  # @newrack -> 501
    assert writes[0][2]["custom_fields"] == {"iso_x": 4, "iso_y": 3}
    assert res["ops"][1]["target"]["id"] == 501 and res["ops"][5]["target"]["id"] == 501  # refs shown as real ids
    e = res["ops"][2]["error"]
    assert e["code"] == "validation_failed" and e["details"]["partial"] is True
    assert (e["details"]["written_ops"], e["details"]["skipped_ops"]) == ([0, 1], [3, 4, 5])
    assert e["details"]["netbox"] == {"position": ["U20 is already occupied"]}
    assert res["applied"] is False


def test_apply_with_an_invalid_op_writes_nothing():
    def on_write(req):
        raise AssertionError("must not write")

    req = load("changes-request.json")
    del req["ops"][0]["if_unmodified_since"]
    with _app(on_write) as cl:
        res = cl.post("/api/changes/apply", json=req).json()
    assert res["applied"] is False and [o["status"] for o in res["ops"]] == ["skipped"] * 4 + ["error", "skipped"]


@pytest.mark.parametrize("body,path", [
    ({"ops": []}, "/ops"), ({"ops": [{"op": "move_rack", "target": {"kind": "rack", "id": 1}, "fields": {}}]}, "/ops/0/fields"),
    ({"ops": [{"op": "place_device", "target": {"kind": "device", "id": 1}, "fields": {"rack_id": 1, "position": 1.25, "face": "front"}}]},
     "/ops/0/fields/position"),
    ({"ops": [{"op": "teleport", "target": {"kind": "rack", "id": 1}, "fields": {}}]}, "/ops/0"),
    ({"ops": [{"op": "unplace_device", "target": {"kind": "device", "id": 1}, "fields": {"x": 1}}]}, "/ops/0/fields"),
    ({"ops": [{"op": "create_rack", "target": {"kind": "rack", "id": 3}, "fields": {"site_id": 1, "name": "x"}}]}, "/ops/0/target/id"),
    ({"ops": [{"op": "update_fields", "target": {"kind": "vlan", "id": 1}, "fields": {"name": "x"}}]}, "/ops/0/target/kind"),
    ({"ops": [{"op": "move_rack", "target": {"kind": "rack", "id": "newrack"}, "fields": {"iso_x": 1}}]}, "/ops/0/target/id"),
    ({"ops": [{"op": "move_rack", "target": {"kind": "rack", "id": 1}, "fields": {"iso_x": 1}}], "extra": 1}, "/"),
    ([], "/"), ({"ops": [{"op": "move_rack", "target": {"kind": "rack", "id": 1}, "fields": {"iso_x": "1"}}]}, "/ops/0/fields/iso_x"),
])
def test_body_schema_errors_are_422_with_paths(body, path):
    with _app(None) as cl:
        r = cl.post("/api/changes/preview", json=body)
    assert (r.status_code, r.json()["error"]["code"]) == (422, "validation_failed")
    schema_validator("ErrorResponse").validate(r.json())
    assert path in {e["path"] for e in r.json()["error"]["details"]["errors"]}


def test_not_json_body_is_422():
    with _app(None) as cl:
        r = cl.post("/api/changes/preview", content=b"{nope", headers={"content-type": "application/json"})
    assert (r.status_code, r.json()["error"]["code"]) == (422, "validation_failed")


# ---- chunked id lookups and referenced-only roles/types
def _id_handler(seen, model_row):
    def h(req):
        seen.append(req)
        ids = [int(x) for x in req.url.params.get_list("id")]
        rows = [model_row(i) for i in ids if i % 7]  # every 7th id "does not exist"
        return httpx.Response(200, json={"count": len(rows), "results": rows})
    return h


def test_by_ids_chunks_long_id_lists():
    seen: list[httpx.Request] = []
    got = run(client(_id_handler(seen, lambda i: {"id": i, "slug": "s", "name": "n", "color": "ffffff"}))
              .by_ids(nb.Role, "dcim/device-roles", list(range(1, 1001)) + [5, 5]))
    assert len(seen) == 10 and all(len(r.url.params.get_list("id")) <= nb.ID_CHUNK for r in seen)
    assert max(len(str(r.url)) for r in seen) < 1500  # gunicorn refuses request lines over 4094 bytes
    assert sorted(got) == [i for i in range(1, 1001) if i % 7]
    assert run(client(_id_handler(seen, None)).by_ids(nb.Role, "dcim/device-roles", [])) == {} and len(seen) == 10


def test_directory_fetches_only_referenced_and_unknown_ids():
    seen: list[httpx.Request] = []

    def h(req):
        seen.append(req)
        ids = [int(x) for x in req.url.params.get_list("id")]
        if req.url.path.endswith("/device-roles/"):
            rows = [{"id": i, "slug": f"r{i}", "name": "n", "color": "ffffff"} for i in ids]
        elif req.url.path.endswith("/device-types/"):
            rows = [{"id": i, "manufacturer": {"id": 1, "name": "m"}, "model": "x", "slug": f"t{i}", "u_height": 1,
                     "is_full_depth": True} for i in ids]
        else:
            rows = [{"id": i, "site": {"id": 2}} for i in ids]
        return httpx.Response(200, json={"count": len(rows), "results": rows})

    def dev(i, role, dtype):
        return nb.Device.model_validate({"id": i, "role": {"id": role}, "device_type": {"id": dtype}, "site": {"id": 1},
                                         "status": "active"})

    cl, d = client(h), scene.Directory()
    devs = [dev(i, 1 + i % 2, 10 + i % 3) for i in range(1, 7)]
    d.add_devices(devs)
    run(d.load_types(cl, devs))
    assert {r.url.path: sorted(map(int, r.url.params.get_list("id"))) for r in seen} == {
        "/api/dcim/device-roles/": [1, 2], "/api/dcim/device-types/": [10, 11, 12]}
    seen.clear()
    run(d.load_types(cl, devs[:2]))  # all known: no request at all
    refs = [nb.PortRef(id=1, url="http://nb.test/api/dcim/interfaces/1/", device={"id": i}) for i in (1, 2, 300, 301)]
    run(d.ensure(cl, refs))  # devices 1, 2 are known; only 300 and 301 are looked up
    assert [(r.url.path, r.url.params.get_list("id"), r.url.params["fields"]) for r in seen] == [
        ("/api/dcim/devices/", ["300", "301"], "id,site")]
    assert (d.device_site[300], d.device_site[1]) == (2, 1)


# ---- regressions: a write whose answer never came has an unknown outcome
def _one_write_set():
    req = load("changes-request.json")
    del req["ops"][0]["if_unmodified_since"]
    req["ops"][4]["fields"] = {"description": "x"}
    return req


@pytest.mark.parametrize("exc,after_send", [(httpx.ReadTimeout, True), (httpx.RemoteProtocolError, True), (httpx.ReadError, True),
                                            (httpx.ConnectError, False), (httpx.ConnectTimeout, False)])
def test_apply_transport_error_after_send_is_unknown_not_unwritten(exc, after_send):
    def on_write(req):
        raise exc("lost", request=req)

    with _app(on_write) as cl:
        res = cl.post("/api/changes/apply", json=_one_write_set()).json()
    schema_validator("ChangeResult").validate(res)
    e = res["ops"][0]["error"]
    assert [o["status"] for o in res["ops"]] == ["error"] + ["skipped"] * 5 and res["applied"] is False
    assert e["code"] == "netbox_unreachable" and e["details"]["written_ops"] == []
    assert e["details"]["outcome_unknown"] is after_send
    if after_send:
        assert e["details"]["partial"] is None
        assert "may or may not have been written" in e["message"] and "Nothing was written" not in e["message"]
    else:
        assert e["details"]["partial"] is False and "Nothing was written" in e["message"]
    assert TOKEN not in json.dumps(res) and TOKEN.split(".")[1] not in json.dumps(res)


def test_apply_unknown_outcome_after_earlier_writes_is_partial():
    rack2 = recordings()["GET /api/dcim/racks/?id=2&limit=1000&offset=0"]["json"]["results"][0]

    def on_write(req):
        if req.method == "PATCH" and "/racks/2/" in req.url.path:
            return httpx.Response(200, json={**rack2, "custom_fields": {**rack2["custom_fields"], **json.loads(req.content)["custom_fields"]}})
        raise httpx.ReadTimeout("slow", request=req)

    with _app(on_write) as cl:
        res = cl.post("/api/changes/apply", json=_one_write_set()).json()
    e = res["ops"][1]["error"]
    assert (e["details"]["partial"], e["details"]["written_ops"], e["details"]["outcome_unknown"]) == (True, [0], True)


# ---- regressions: token-leak tests that a leak of the secret half or via timeouts would pass
SECRET = TOKEN.split(".")[1]


@pytest.mark.parametrize("exc", [httpx.ConnectTimeout, httpx.ReadTimeout, httpx.ConnectError, httpx.RemoteProtocolError])
def test_transport_error_details_never_carry_the_token(exc):
    def h(req):
        raise exc(f"failed with {req.headers['authorization']}", request=req)  # exception text carrying the header

    with pytest.raises(ApiError) as e:
        run(client(h).request("GET", "status/"))
    text = json.dumps(e.value.body()) + str(e.value)
    assert SECRET not in text and TOKEN not in text


def test_version_cache_expires(monkeypatch):
    calls, now = [], [1000.0]
    monkeypatch.setattr(nb.time, "monotonic", lambda: now[0])

    def h(req):
        calls.append(1)
        return httpx.Response(200, json={"netbox-version": "4.7.4" if len(calls) == 1 else "5.0.0"})

    c = client(h)
    assert run(c.version()) == "4.7.4"
    now[0] += nb.NetBox.VERSION_TTL - 1
    assert run(c.version()) == "4.7.4" and len(calls) == 1
    now[0] += 2  # past the TTL: NetBox is asked again (an upgrade to 5.0 is noticed)
    with pytest.raises(ApiError) as e:
        run(c.version())
    assert e.value.code == "unsupported_netbox_version" and len(calls) == 2


# ---- regressions: if_unmodified_since must be RFC 3339, refused as 422 (never 500, never coerced)
def test_date_time_format_is_actually_checked():
    from rackwalker_proxy.schema import validator
    assert "date-time" in validator("ChangeSet").format_checker.checkers  # silently absent without rfc3339-validator


@pytest.mark.parametrize("since,status", [("1762500000", 422), ("2026-13-45T00:00:00Z", 422), ("not a date", 422),
                                          ("2026-11-07 10:00:00", 422), ("2026-11-07T10:00:00", 422),
                                          ("2026-11-07T10:00:00Z", 200), ("2026-11-07t10:00:00.5+01:00", 200)])
def test_if_unmodified_since_must_be_rfc3339(since, status):
    body = {"ops": [{"op": "move_rack", "target": {"kind": "rack", "id": 2}, "fields": {"iso_x": 1}, "if_unmodified_since": since}]}
    with _app(None) as cl:
        r = cl.post("/api/changes/preview", json=body)
    assert r.status_code == status, r.text
    if status == 422:
        assert r.json()["error"]["code"] == "validation_failed"
        assert "/ops/0/if_unmodified_since" in {e["path"] for e in r.json()["error"]["details"]["errors"]}
