"""Unit tests for the Ceph Dashboard connection layer (httpx-injected, no cluster).

Proves: CephConnection logs in lazily and caches the JWT, sends Bearer auth,
transparently re-logs-in + retries exactly once on a 401, translates non-2xx
statuses into teaching CephApiError messages, wraps transport errors, and returns
{} for empty / non-JSON bodies; ConnectionManager reuses sessions per target and
disconnects cleanly. The httpx client is a hand-rolled fake implementing
``request`` / ``close``.
"""

from types import SimpleNamespace

import httpx
import pytest

from ceph_aiops.connection import (
    CephApiError,
    CephConnection,
    ConnectionManager,
    _teaching_message,
)


class _Resp:
    def __init__(self, status_code=200, payload=None, text="", content=b"x"):
        self.status_code = status_code
        self._payload = payload
        self.text = text
        self.content = content

    def json(self):
        if self._payload is None:
            raise ValueError("no json")
        return self._payload


class _FakeClient:
    """Records requests and returns queued responses (or a default 200)."""

    def __init__(self, responses=None):
        self._responses = list(responses or [])
        self.calls = []
        self.closed = False

    def request(self, method, path, **kwargs):
        self.calls.append((method, path, kwargs))
        if self._responses:
            resp = self._responses.pop(0)
            if isinstance(resp, Exception):
                raise resp
            return resp
        return _Resp(200, payload={}, content=b"{}")

    def close(self):
        self.closed = True


def _target():
    return SimpleNamespace(
        name="lab", username="admin", password="pw",
        base_url="https://mgr:8443", port=8443, verify_ssl=False,
    )


@pytest.mark.unit
def test_login_then_get_sends_bearer_and_caches_token():
    client = _FakeClient([
        _Resp(200, payload={"token": "jwt-abc"}, content=b'{"token":"jwt-abc"}'),
        _Resp(200, payload={"status": "HEALTH_OK"}, content=b"{...}"),
        _Resp(200, payload={"x": 1}, content=b"{...}"),
    ])
    conn = CephConnection(_target(), client=client)

    out = conn.get("/api/health/full")
    assert out == {"status": "HEALTH_OK"}
    # First call is the /api/auth login, second is the GET with Bearer header.
    assert client.calls[0][1] == "/api/auth"
    get_call = client.calls[1]
    assert get_call[2]["headers"]["Authorization"] == "Bearer jwt-abc"

    # Second GET reuses the cached token — no second login.
    conn.get("/api/osd")
    assert sum(1 for c in client.calls if c[1] == "/api/auth") == 1


@pytest.mark.unit
def test_401_triggers_one_relogin_and_retry():
    client = _FakeClient([
        _Resp(200, payload={"token": "jwt-1"}, content=b"{...}"),  # initial login
        _Resp(401, text="expired", content=b"expired"),            # GET → token expired
        _Resp(200, payload={"token": "jwt-2"}, content=b"{...}"),  # re-login
        _Resp(200, payload={"ok": True}, content=b"{...}"),        # retried GET
    ])
    conn = CephConnection(_target(), client=client)
    out = conn.get("/api/osd")
    assert out == {"ok": True}
    assert sum(1 for c in client.calls if c[1] == "/api/auth") == 2


@pytest.mark.unit
def test_non_2xx_raises_teaching_error():
    client = _FakeClient([
        _Resp(200, payload={"token": "t"}, content=b"{...}"),
        _Resp(404, text="missing", content=b"missing"),
    ])
    conn = CephConnection(_target(), client=client)
    with pytest.raises(CephApiError) as ei:
        conn.get("/api/pool/nope")
    assert ei.value.status_code == 404
    assert "not found" in str(ei.value).lower()


@pytest.mark.unit
def test_login_failure_raises():
    client = _FakeClient([_Resp(401, text="bad creds", content=b"bad")])
    conn = CephConnection(_target(), client=client)
    with pytest.raises(CephApiError) as ei:
        conn.get("/api/health/full")
    assert ei.value.status_code == 401


@pytest.mark.unit
def test_login_without_token_raises():
    client = _FakeClient([_Resp(200, payload={"nope": 1}, content=b"{...}")])
    conn = CephConnection(_target(), client=client)
    with pytest.raises(CephApiError) as ei:
        conn.get("/api/health/full")
    assert "no token" in str(ei.value).lower()


@pytest.mark.unit
def test_transport_error_wrapped():
    client = _FakeClient([
        _Resp(200, payload={"token": "t"}, content=b"{...}"),
        httpx.ConnectError("refused"),
    ])
    conn = CephConnection(_target(), client=client)
    with pytest.raises(CephApiError) as ei:
        conn.get("/api/osd")
    assert "Could not reach" in str(ei.value)


@pytest.mark.unit
def test_empty_and_non_json_bodies_return_empty_dict():
    client = _FakeClient([
        _Resp(200, payload={"token": "t"}, content=b"{...}"),
        _Resp(204, content=b""),               # empty body
        _Resp(200, payload=None, content=b"not-json"),  # json() raises
    ])
    conn = CephConnection(_target(), client=client)
    assert conn.get("/api/x") == {}
    assert conn.get("/api/y") == {}


@pytest.mark.unit
def test_write_verbs_pass_json_body():
    client = _FakeClient([
        _Resp(200, payload={"token": "t"}, content=b"{...}"),
        _Resp(200, payload={"ok": 1}, content=b"{...}"),
    ])
    conn = CephConnection(_target(), client=client)
    conn.put("/api/pool/rbd", json={"size": 2})
    put_call = client.calls[1]
    assert put_call[0] == "PUT"
    assert put_call[2]["json"] == {"size": 2}


@pytest.mark.unit
def test_close_closes_client():
    client = _FakeClient()
    conn = CephConnection(_target(), client=client)
    conn.close()
    assert client.closed is True


@pytest.mark.unit
@pytest.mark.parametrize("status,needle", [
    (401, "Authentication"),
    (403, "Authentication"),
    (404, "not found"),
    (400, "Bad request"),
    (503, "failing"),
    (418, "API error"),
])
def test_teaching_message_maps_statuses(status, needle):
    msg = _teaching_message(status, "/api/x", "body")
    assert needle.lower() in msg.lower()


# ── ConnectionManager ───────────────────────────────────────────────────────


def _app_config():
    from ceph_aiops.config import AppConfig, TargetConfig

    return AppConfig(targets=(
        TargetConfig(name="a", host="h1"),
        TargetConfig(name="b", host="h2"),
    ))


@pytest.mark.unit
def test_manager_reuses_and_disconnects(monkeypatch):
    import ceph_aiops.connection as conn_mod

    # Avoid constructing a real httpx.Client for each CephConnection.
    monkeypatch.setattr(conn_mod, "CephConnection",
                        lambda target: SimpleNamespace(close=lambda: None, target=target))
    mgr = ConnectionManager(_app_config())

    c1 = mgr.connect("a")
    c1_again = mgr.connect("a")
    assert c1 is c1_again  # session reuse
    assert mgr.list_connected() == ["a"]
    assert set(mgr.list_targets()) == {"a", "b"}

    mgr.disconnect("a")
    assert mgr.list_connected() == []
    # default target is the first configured one.
    assert mgr.connect().target.name == "a"


@pytest.mark.unit
def test_manager_unknown_target_raises(monkeypatch):
    import ceph_aiops.connection as conn_mod

    monkeypatch.setattr(conn_mod, "CephConnection",
                        lambda target: SimpleNamespace(close=lambda: None, target=target))
    mgr = ConnectionManager(_app_config())
    with pytest.raises(KeyError):
        mgr.connect("nonexistent")
