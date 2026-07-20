"""Smoke + ops tests for ceph-aiops.

Proves: every module imports, the CLI builds and --help works, the MCP server
exposes the expected tools and EVERY tool carries the harness marker
``_is_governed_tool``, the JWT-login connection authenticates + translates
errors, the flagship HEALTH_WARN RCA maps check codes to cause/action, and the
OSD writes capture BEFORE-state, record undo, gate dry-run, and carry correct
risk tiers. No real Ceph cluster is needed — the connection is a fake/MagicMock.
"""

import asyncio
import importlib
from unittest.mock import MagicMock

import pytest
from typer.testing import CliRunner

# Kept in sync with mcp_server/server.py (the full registered tool surface).
EXPECTED_TOOLS = {
    # health
    "cluster_health", "cluster_status",
    # osd
    "osd_tree", "osd_df", "osd_perf", "cluster_flag_set", "osd_reweight",
    "osd_mark_in", "osd_mark_out", "osd_purge",
    # pg
    "pg_summary", "pg_dump_stuck", "scrub_status", "trigger_scrub", "trigger_deep_scrub",
    # pool
    "pool_ls", "pool_df", "set_pool_quota", "set_pool_pg_num", "set_pool_autoscale",
    "pool_create", "set_pool_size", "pool_delete",
    # rbd
    "rbd_ls", "rbd_image_create", "rbd_snapshot_create", "rbd_image_delete",
    "rbd_snapshot_delete",
    # filesystem / rgw
    "cephfs_status", "rgw_status",
    # cluster-ops
    "mon_status", "mgr_status", "slow_ops", "capacity_forecast", "throttle_recovery",
}


@pytest.mark.unit
def test_all_modules_import():
    for name in (
        "ceph_aiops", "ceph_aiops.config", "ceph_aiops.connection",
        "ceph_aiops.doctor", "ceph_aiops.secretstore",
        "ceph_aiops.ops.health", "ceph_aiops.ops.osd", "ceph_aiops.ops.overview",
        "ceph_aiops.cli", "ceph_aiops.cli._root", "ceph_aiops.cli._common",
        "ceph_aiops.cli.init", "ceph_aiops.cli.secret", "ceph_aiops.cli.health",
        "ceph_aiops.cli.osd", "ceph_aiops.cli.overview", "ceph_aiops.cli.doctor",
        "mcp_server.server", "mcp_server._shared",
        "mcp_server.tools.health", "mcp_server.tools.osd",
    ):
        importlib.import_module(name)


@pytest.mark.unit
def test_version_matches_pyproject():
    """__version__ is single-sourced from package metadata; it must track
    pyproject.toml so a release bump can never ship a stale self-report."""
    import tomllib
    from pathlib import Path

    import ceph_aiops

    pyproject = Path(__file__).resolve().parents[1] / "pyproject.toml"
    expected = tomllib.loads(pyproject.read_text("utf-8"))["project"]["version"]
    assert ceph_aiops.__version__ == expected


@pytest.mark.unit
def test_cli_app_builds_and_help_works():
    from ceph_aiops.cli import app

    runner = CliRunner()
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    for sub in ("health", "osd", "secret", "init", "overview", "doctor", "mcp"):
        assert sub in result.output


@pytest.mark.unit
def test_cli_leaf_help_triggers_lazy_imports():
    from ceph_aiops.cli import app

    runner = CliRunner()
    for cmd in (
        ["health", "--help"], ["osd", "--help"], ["secret", "--help"],
        ["doctor", "--help"], ["overview", "--help"], ["init", "--help"],
        ["health", "detail", "--help"], ["health", "status", "--help"],
        ["osd", "tree", "--help"], ["osd", "df", "--help"],
        ["osd", "reweight", "--help"], ["osd", "out", "--help"], ["osd", "purge", "--help"],
        ["secret", "list", "--help"], ["secret", "set", "--help"],
    ):
        result = runner.invoke(app, cmd)
        assert result.exit_code == 0, f"{cmd} failed: {result.output}"


@pytest.mark.unit
def test_mcp_list_tools_exposes_expected_tools():
    from mcp_server.server import mcp

    tools = asyncio.run(mcp.list_tools())
    names = {t.name for t in tools}
    assert EXPECTED_TOOLS <= names, f"missing: {EXPECTED_TOOLS - names}"


@pytest.mark.unit
def test_every_mcp_tool_is_governed_by_harness():
    from mcp_server import _shared

    tool_objs = _shared.mcp._tool_manager._tools
    assert EXPECTED_TOOLS <= set(tool_objs), "tool registry incomplete"
    for name, tool in tool_objs.items():
        fn = getattr(tool, "fn", None)
        assert fn is not None, f"{name} has no fn"
        assert getattr(fn, "_is_governed_tool", False), f"{name} missing @governed_tool"


@pytest.mark.unit
def test_write_tools_have_correct_risk_tiers():
    from mcp_server.tools import osd as o

    assert o.cluster_flag_set._risk_level == "medium"  # a write: must vanish in read-only mode
    assert o.osd_reweight._risk_level == "medium"
    assert o.osd_mark_in._risk_level == "medium"
    assert o.osd_mark_out._risk_level == "high"
    assert o.osd_purge._risk_level == "high"


# ── JWT-login connection ────────────────────────────────────────────────


class _Resp:
    def __init__(self, status, payload=None, content=b"{}"):
        self.status_code = status
        self._payload = payload if payload is not None else {}
        self.content = content
        self.text = "body"

    def json(self):
        return self._payload


@pytest.mark.unit
def test_connection_jwt_login_and_error_translation(monkeypatch):
    from ceph_aiops.config import TargetConfig
    from ceph_aiops.connection import CephApiError, CephConnection

    monkeypatch.setenv("CEPH_LAB1_PASSWORD", "pw")
    target = TargetConfig(name="lab1", host="mgr.local", verify_ssl=False)
    calls = []

    class _Client:
        def request(self, method, path, **k):
            calls.append((method, path))
            if path == "/api/auth":
                return _Resp(201, {"token": "jwt-xyz"})
            if path == "/notfound":
                return _Resp(404, content=b"x")
            return _Resp(200, {"health": {"status": "HEALTH_OK"}})

        def close(self):
            pass

    conn = CephConnection(target, client=_Client())
    out = conn.get("/api/health/minimal")
    assert out["health"]["status"] == "HEALTH_OK"
    assert ("POST", "/api/auth") in calls  # logged in lazily
    with pytest.raises(CephApiError) as ei:
        conn.get("/notfound")
    assert ei.value.status_code == 404


# ── flagship HEALTH_WARN RCA ────────────────────────────────────────────


@pytest.mark.unit
def test_cluster_health_maps_check_to_cause_and_action():
    from ceph_aiops.ops import health as ops

    conn = MagicMock(name="conn")
    conn.get.return_value = {
        "health": {
            "status": "HEALTH_WARN",
            "checks": {
                "OSD_NEARFULL": {"severity": "HEALTH_WARN",
                                 "summary": {"message": "1 nearfull osd(s)"}},
            },
        }
    }
    out = ops.cluster_health(conn)
    assert out["status"] == "HEALTH_WARN" and out["healthy"] is False
    f = out["findings"][0]
    assert f["check"] == "OSD_NEARFULL"
    assert "nearfull_ratio" in f["cause"]
    assert f["suggestedAction"]


@pytest.mark.unit
def test_cluster_health_resilient_to_failure():
    from ceph_aiops.ops import health as ops

    conn = MagicMock(name="conn")
    conn.get.side_effect = RuntimeError("mgr down")
    out = ops.cluster_health(conn)
    assert "error" in out


# ── OSD writes: undo, before-state, dry-run ─────────────────────────────


def _osd_map():
    return [{"osd": 3, "up": 1, "in": 1, "weight": 1.0, "host": "ceph-a"}]


@pytest.mark.unit
def test_osd_reweight_captures_prior_and_records_undo(monkeypatch):
    import ceph_aiops.governance.undo as undo_mod
    from mcp_server.tools import osd as o

    conn = MagicMock(name="conn")
    conn.get.return_value = _osd_map()
    conn.post.return_value = {}
    monkeypatch.setattr(o, "_get_connection", lambda target=None: conn)

    recorded = {}

    class _Store:
        def record(self, *, skill, tool, undo_descriptor, orig_params, effect_verified=True):
            recorded["d"] = undo_descriptor
            return "undo-1"

    monkeypatch.setattr(undo_mod, "get_undo_store", lambda: _Store())

    result = o.osd_reweight(osd_id=3, weight=0.0)
    assert result["priorState"]["weight"] == 1.0
    assert recorded["d"]["tool"] == "osd_reweight"
    assert recorded["d"]["params"]["weight"] == 1.0  # restore prior
    assert result.get("_undo_id") == "undo-1"


@pytest.mark.unit
def test_osd_purge_dry_run_does_not_mutate(monkeypatch):
    from mcp_server.tools import osd as o

    conn = MagicMock(name="conn")
    monkeypatch.setattr(o, "_get_connection", lambda target=None: conn)
    result = o.osd_purge(osd_id=3, dry_run=True)
    assert result["dryRun"] is True
    conn.delete.assert_not_called()


@pytest.mark.unit
def test_cli_osd_purge_dry_run_gates():
    from ceph_aiops.cli import app

    runner = CliRunner()
    result = runner.invoke(app, ["osd", "purge", "3", "--dry-run"])
    assert result.exit_code == 0
    assert "DRY-RUN" in result.output


@pytest.mark.unit
def test_set_cluster_flag_captures_prior():
    from ceph_aiops.ops import osd as ops

    conn = MagicMock(name="conn")
    conn.get.return_value = {"flags": []}
    conn.put.return_value = {}
    out = ops.set_cluster_flag(conn, "noout", enable=True)
    assert out["action"] == "set_cluster_flag" and out["enabled"] is True
    assert out["priorState"]["flags"] == []
