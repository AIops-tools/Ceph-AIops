"""Connection management for the Ceph mgr Dashboard REST API.

Thin httpx wrapper with per-target session reuse and JWT auth:

  * The Ceph Dashboard authenticates with username + password exchanged for a
    short-lived **JWT** at ``POST /api/auth``. This connection logs in lazily on
    the first request, caches the token, and sends ``Authorization: Bearer
    <jwt>`` thereafter. A 401 mid-session triggers exactly one re-login + retry
    (the token expired), so long-running agents don't fail on token rotation.
  * The Dashboard API is versioned via an ``Accept`` header; we request the
    default (``application/vnd.ceph.api.v1.0+json``) and fall back to plain JSON.
  * ``base_url`` is the mgr origin (``https://host:8443``); callers pass API
    paths like ``/api/health/full`` or ``/api/osd``.

Ceph has no ETag/If-Match and its list endpoints return full arrays (no
server-side paging), so — unlike a v4-style API — there is no ETag capture or
page-walking here; just JSON in, JSON out, with central error translation to
``CephApiError``.

The httpx client is injectable for tests: pass ``client=`` to ``CephConnection``
to substitute a mock implementing ``request`` / ``close``. Mock responses must
expose ``status_code``, ``content``, ``text``, and ``json()``.
"""

from __future__ import annotations

from typing import Any

import httpx

from ceph_aiops.config import AppConfig, TargetConfig, load_config

_TIMEOUT = 60.0
_ACCEPT = "application/vnd.ceph.api.v1.0+json"


class CephApiError(Exception):
    """A Ceph mgr REST call failed; carries a teaching message + status code."""

    def __init__(self, message: str, *, status_code: int | None = None, path: str = "") -> None:
        self.status_code = status_code
        self.path = path
        super().__init__(message)


def _teaching_message(status: int, path: str, body: str) -> str:
    """Map a non-2xx status to an actionable, teaching error message."""
    snippet = body[:200].strip()
    if status in (401, 403):
        return (
            f"Authentication/authorization failed ({status}) on {path}. Check the "
            f"Dashboard username/password and that the mgr 'dashboard' module is "
            f"enabled and the account has the needed role. {snippet}"
        )
    if status == 404:
        return (
            f"Resource not found (404) on {path}. The id/name may be stale — list "
            f"the parent collection first (e.g. osd tree, pool ls). {snippet}"
        )
    if status == 400:
        return (
            f"Bad request (400) on {path}. Ceph rejected the parameters — check the "
            f"osd id / pool name / flag value against the current cluster. {snippet}"
        )
    if status in (500, 502, 503, 504):
        return (
            f"Ceph mgr error ({status}) on {path}. The active mgr may be failing "
            f"over or a module is restarting; retry shortly. {snippet}"
        )
    return f"Ceph mgr API error ({status}) on {path}. {snippet}"


class CephConnection:
    """A single authenticated session against one Ceph cluster's mgr Dashboard API."""

    def __init__(self, target: TargetConfig, client: Any | None = None) -> None:
        self._target = target
        self._token: str | None = None
        self._client = client or httpx.Client(
            base_url=target.base_url,
            verify=target.verify_ssl,
            timeout=_TIMEOUT,
            headers={"Accept": _ACCEPT, "Content-Type": "application/json"},
        )

    @property
    def target(self) -> TargetConfig:
        return self._target

    # ── auth ─────────────────────────────────────────────────────────────
    def _login(self) -> None:
        """Exchange username/password for a JWT and cache it."""
        resp = self._client.request(
            "POST", "/api/auth",
            json={"username": self._target.username, "password": self._target.password},
        )
        if not (200 <= resp.status_code < 300):
            raise CephApiError(
                _teaching_message(resp.status_code, "/api/auth", resp.text),
                status_code=resp.status_code, path="/api/auth",
            )
        try:
            self._token = resp.json().get("token")
        except ValueError:
            self._token = None
        if not self._token:
            raise CephApiError(
                "Login to the Ceph Dashboard succeeded but returned no token; "
                "check the mgr 'dashboard' module version.",
                path="/api/auth",
            )

    def _auth_header(self) -> dict[str, str]:
        if self._token is None:
            self._login()
        return {"Authorization": f"Bearer {self._token}"}

    # ── requests ─────────────────────────────────────────────────────────
    def request(self, method: str, path: str, *, _retry: bool = True, **kwargs: Any) -> Any:
        """Issue an authenticated request and return parsed JSON.

        On a 401 (token expired) this re-logs in once and retries, so a rotated
        JWT surfaces as a transparent refresh rather than an error.
        """
        headers = {**kwargs.pop("headers", {}), **self._auth_header()}
        try:
            resp = self._client.request(method, path, headers=headers, **kwargs)
        except httpx.HTTPError as exc:
            raise CephApiError(
                f"Could not reach the Ceph mgr at {self._target.base_url} "
                f"({method} {path}): {exc}. Check host/port {self._target.port} and "
                f"that the mgr 'dashboard' module is enabled.",
                path=path,
            ) from exc
        if resp.status_code == 401 and _retry:
            self._token = None  # force re-login and retry once
            return self.request(method, path, _retry=False, headers=headers, **kwargs)
        if not (200 <= resp.status_code < 300):
            raise CephApiError(
                _teaching_message(resp.status_code, path, resp.text),
                status_code=resp.status_code, path=path,
            )
        if not resp.content:
            return {}
        try:
            return resp.json()
        except ValueError:
            return {}

    def get(self, path: str, **kwargs: Any) -> Any:
        return self.request("GET", path, **kwargs)

    def post(self, path: str, *, json: Any = None, **kwargs: Any) -> Any:
        return self.request("POST", path, json=json, **kwargs)

    def put(self, path: str, *, json: Any = None, **kwargs: Any) -> Any:
        return self.request("PUT", path, json=json, **kwargs)

    def delete(self, path: str, **kwargs: Any) -> Any:
        return self.request("DELETE", path, **kwargs)

    def close(self) -> None:
        self._client.close()


class ConnectionManager:
    """Manages connections to multiple Ceph targets with session reuse."""

    def __init__(self, config: AppConfig) -> None:
        self._config = config
        self._connections: dict[str, CephConnection] = {}

    @classmethod
    def from_config(cls, config: AppConfig | None = None) -> ConnectionManager:
        cfg = config or load_config()
        return cls(cfg)

    def connect(self, target_name: str | None = None) -> CephConnection:
        """Connect to a target by name, or the default target."""
        target = (
            self._config.get_target(target_name)
            if target_name
            else self._config.default_target
        )
        cached = self._connections.get(target.name)
        if cached is not None:
            return cached
        conn = CephConnection(target)
        self._connections[target.name] = conn
        return conn

    def disconnect(self, target_name: str) -> None:
        conn = self._connections.pop(target_name, None)
        if conn is not None:
            conn.close()

    def disconnect_all(self) -> None:
        for name in list(self._connections):
            self.disconnect(name)

    def list_targets(self) -> list[str]:
        return [t.name for t in self._config.targets]

    def list_connected(self) -> list[str]:
        return list(self._connections.keys())
