"""The proxy serves fixtures, uses the one error shape, and refuses apply unless WRITE_ENABLED=1."""
import pytest
from fastapi.testclient import TestClient

from rackwalker_proxy.app import Settings, create_app
from rackwalker_proxy import contracts_gen as c

from .conftest import INDEX, LOCAL, REPO, load


def client(**env) -> TestClient:
    return TestClient(base_url=LOCAL, app=create_app(Settings.from_env({"RACKWALKER_FIXTURES": str(REPO / "fixtures"), **env})))


# Game routes ("store": true) answer from the game store in every mode: test_game.py covers them.
@pytest.mark.parametrize("f", [f for f in INDEX if f["method"] == "GET" and not f.get("store")], ids=lambda f: f["path"])
def test_get_routes_serve_fixtures(f):
    r = client().get(f["path"])
    assert r.status_code == f["status"]
    assert r.json() == load(f["file"])


def test_preview_validates_body_and_answers_change_result():
    cl = client()
    r = cl.post("/api/changes/preview", json=load("changes-request.json"))
    assert r.status_code == 200
    c.ChangeResult.model_validate(r.json())
    r = cl.post("/api/changes/preview", json={"ops": []})
    assert r.status_code == 422
    assert c.ErrorResponse.model_validate(r.json()).error.code == "validation_failed"


def test_apply_refused_by_default():
    r = client().post("/api/changes/apply", json=load("changes-request.json"))
    assert r.status_code == 403
    assert c.ErrorResponse.model_validate(r.json()).error.code == "write_disabled"


def test_unknown_route_and_kind_use_error_shape():
    cl = client()
    for path, code in [("/api/nope", "not_found"), ("/api/trace/banana/1", "bad_request"), ("/api/scene/site/12345", "not_found")]:
        r = cl.get(path)
        assert c.ErrorResponse.model_validate(r.json()).error.code == code, path


@pytest.mark.parametrize("path", ["/api/scene/site/abc", "/api/scene/rack/1.5", "/api/device/x", "/api/trace/interface/abc"])
def test_malformed_path_id_is_400_bad_request(path):
    r = client().get(path)
    assert r.status_code == 400
    assert c.ErrorResponse.model_validate(r.json()).error.code == "bad_request"


def test_health_netbox_mode_down_when_unreachable():
    app = create_app(Settings.from_env({"NETBOX_URL": "http://127.0.0.1:9", "NETBOX_TOKEN": "x"}))
    r = TestClient(app, base_url=LOCAL).get("/api/health")
    h = c.HealthResponse.model_validate(r.json())
    assert (h.status, h.netbox.reachable, h.write_enabled) == ("down", False, False)
    assert "x" not in r.text.replace("netbox", "")  # token never echoed


def test_netbox_auth_header_v2_and_v1():
    from rackwalker_proxy.app import netbox_auth_header
    assert netbox_auth_header("nbt_abcdef123456.0123456789abcdef0123456789abcdef01234567") == \
        "Bearer nbt_abcdef123456.0123456789abcdef0123456789abcdef01234567"
    assert netbox_auth_header("0123456789abcdef0123456789abcdef01234567") == "Token 0123456789abcdef0123456789abcdef01234567"


def _fake_netbox(monkeypatch, version: str, seen: list):
    import httpx

    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.path.endswith("/api/status/"):  # the health probe itself; the permission probes follow it
            seen.append(req.headers.get("authorization"))
        return httpx.Response(200, json={"netbox-version": version, "results": [], "count": 0})

    real = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: real(transport=httpx.MockTransport(handler), **kw))


@pytest.mark.parametrize("version,status,supported", [("4.7.4", "ok", True), ("4.1.11", "degraded", False)])
def test_health_netbox_mode_sends_bearer_and_checks_version(monkeypatch, version, status, supported):
    seen: list = []
    _fake_netbox(monkeypatch, version, seen)
    token = "nbt_abcdef123456.0123456789abcdef0123456789abcdef01234567"
    r = TestClient(base_url=LOCAL, app=create_app(Settings.from_env({"NETBOX_URL": "http://nb.invalid", "NETBOX_TOKEN": token}))).get("/api/health")
    h = c.HealthResponse.model_validate(r.json())
    assert seen == [f"Bearer {token}"]
    assert (h.status, h.netbox.version, h.netbox.supported) == (status, version, supported)
    assert token not in r.text


def test_health_degraded_when_netbox_slow(monkeypatch):
    from rackwalker_proxy import app as app_module
    _fake_netbox(monkeypatch, "4.7.4", [])
    monkeypatch.setattr(app_module, "SLOW_SECONDS", -1.0)  # every answer counts as slower than the threshold
    r = TestClient(base_url=LOCAL, app=create_app(Settings.from_env({"NETBOX_URL": "http://nb.invalid", "NETBOX_TOKEN": "nbt_k.t"}))).get("/api/health")
    h = c.HealthResponse.model_validate(r.json())
    assert (h.status, h.netbox.reachable, h.netbox.supported) == ("degraded", True, True)


# ---- regressions: dry-run parsing, Host header (DNS rebinding)
@pytest.mark.parametrize("v", ["", "0", "false", "False", "no", "off", "true", "TRUE", "yes", "on", " 1", "1 ", "01", "2"])
def test_write_enabled_only_for_exactly_1(v):
    assert Settings.from_env({"WRITE_ENABLED": v}).write_enabled is False
    assert Settings.from_env({}).write_enabled is False
    assert Settings.from_env({"WRITE_ENABLED": "1"}).write_enabled is True


@pytest.mark.parametrize("host,ok", [("127.0.0.1:8710", True), ("localhost:5173", True), ("LOCALHOST", True),
                                     ("127.0.0.1", True), ("rebind.attacker.example", False),
                                     ("rebind.attacker.example:8710", False), ("127.0.0.1.nip.io:8710", False),
                                     ("localhost.attacker.example", False), ("[::1]:8710", False), ("", False)])
def test_foreign_host_header_refused(host, ok):
    cl = client(WRITE_ENABLED="1", RACKWALKER_LOCAL_PLAYER="1")
    for method, path in [("GET", "/api/scene/world"), ("GET", "/api/device/2"), ("GET", "/api/health"),
                         ("POST", "/api/changes/preview")]:
        r = cl.request(method, path, headers={"host": host, "X-RackWalker-CSRF": "local"}, json=load("changes-request.json") if method == "POST" else None)
        if ok:
            assert r.status_code == 200, (host, path, r.text)
        else:
            assert r.status_code == 400 and c.ErrorResponse.model_validate(r.json()).error.code == "bad_request", (host, path)


def test_allowed_hosts_from_env():
    s = Settings.from_env({"RACKWALKER_ALLOWED_HOSTS": " walker.example , 127.0.0.1 "})
    assert s.allowed_hosts == ("walker.example", "127.0.0.1")
    assert Settings.from_env({}).allowed_hosts == ("127.0.0.1", "localhost")
    cl = TestClient(create_app(Settings.from_env({"RACKWALKER_FIXTURES": str(REPO / "fixtures"), "RACKWALKER_ALLOWED_HOSTS": "walker.example"})),
                    base_url="http://walker.example:8710")
    assert cl.get("/api/scene/world").status_code == 200
    assert cl.get("/api/scene/world", headers={"host": "localhost"}).status_code == 400


@pytest.mark.parametrize("status", [401, 403, 500, 503])
def test_health_non_200_never_echoes_the_token(monkeypatch, status):
    """Regression: only the exception branch was leak-tested. A NetBox that refuses the token
    (and echoes it back) must not get it into /api/health."""
    import httpx
    token = "nbt_abcdef123456.0123456789abcdef0123456789abcdef01234567"

    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(status, json={"detail": f"Invalid token {req.headers['authorization']}", "netbox-version": "4.7.4"},
                              headers={"x-echo": req.headers["authorization"]})

    real = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: real(transport=httpx.MockTransport(handler), **kw))
    r = TestClient(base_url=LOCAL, app=create_app(Settings.from_env({"NETBOX_URL": "http://nb.invalid", "NETBOX_TOKEN": token}))).get("/api/health")
    h = c.HealthResponse.model_validate(r.json())
    assert (h.status, h.netbox.reachable, h.netbox.version) == ("down", False, None)
    assert token not in r.text and token.split(".")[1] not in r.text and token not in str(r.headers)


def test_cdn_docs_pages_off_by_default():
    """Regression: Swagger UI / ReDoc pull CDN scripts (swagger-ui-dist@5, no SRI) onto the origin
    that serves POST /api/changes/apply. Off unless RACKWALKER_DOCS=1; the OpenAPI JSON stays."""
    cl = client(WRITE_ENABLED="1")
    for path in ("/api/docs", "/api/redoc", "/docs", "/redoc"):
        assert cl.get(path).status_code == 404, path
    assert cl.get("/api/openapi.json").status_code == 200
    on = client(RACKWALKER_DOCS="1")
    assert on.get("/api/docs").status_code == 200 and on.get("/api/redoc").status_code == 200
    assert Settings.from_env({"RACKWALKER_DOCS": "true"}).docs is False


@pytest.mark.parametrize("url,shown", [
    ("https://svc:basicpw@netbox.internal", "https://netbox.internal"),
    ("https://svc@netbox.internal:8443/nb", "https://netbox.internal:8443/nb"),
    ("http://u:p@[::1]:8000", "http://[::1]:8000"),
    ("http://nb.invalid", "http://nb.invalid"),
])
def test_health_never_reports_credentials_in_the_netbox_url(monkeypatch, url, shown):
    # Regression: a NETBOX_URL with user:password@ (basic auth in front of NetBox) reached the browser verbatim.
    seen: list = []
    _fake_netbox(monkeypatch, "4.7.4", seen)
    r = TestClient(base_url=LOCAL, app=create_app(Settings.from_env({"NETBOX_URL": url, "NETBOX_TOKEN": "nbt_abc.def"}))).get("/api/health")
    assert r.json()["netbox"]["url"] == shown
    assert "basicpw" not in r.text and "u:p@" not in r.text
    assert len(seen) == 1  # the proxy itself still talks to the URL as configured
