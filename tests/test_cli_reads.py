"""CLI read-command tests (rendering + error translation), no live cluster.

Proves: the osd/health/overview read commands fetch via a (mocked) connection
and emit the ops JSON, and the shared cli_errors decorator turns a CephApiError
into a one-line red error with exit code 1 rather than a traceback. The
connection is mocked at each command module's get_connection seam.
"""

from unittest.mock import MagicMock

import pytest
from typer.testing import CliRunner

runner = CliRunner()


def _patch_conn(monkeypatch, module, conn):
    monkeypatch.setattr(module, "get_connection", lambda target=None: (conn, None))


@pytest.mark.unit
def test_osd_tree_renders_json(monkeypatch):
    import ceph_aiops.cli.osd as osd_cli
    from ceph_aiops.cli import app

    conn = MagicMock(name="conn")
    conn.get.return_value = [{"osd": 0, "up": 1, "in": 1, "weight": 1.0}]
    _patch_conn(monkeypatch, osd_cli, conn)

    result = runner.invoke(app, ["osd", "tree"])
    assert result.exit_code == 0, result.output
    assert "usedPercent" in result.output or "weight" in result.output


@pytest.mark.unit
def test_osd_df_renders_json(monkeypatch):
    import ceph_aiops.cli.osd as osd_cli
    from ceph_aiops.cli import app

    conn = MagicMock(name="conn")
    conn.get.return_value = [{"osd": 0, "up": 1, "in": 1,
                             "osd_stats": {"kb": 100, "kb_used": 90}}]
    _patch_conn(monkeypatch, osd_cli, conn)

    result = runner.invoke(app, ["osd", "df"])
    assert result.exit_code == 0, result.output
    assert "nearfull" in result.output


@pytest.mark.unit
def test_health_detail_and_status(monkeypatch):
    import ceph_aiops.cli.health as health_cli
    from ceph_aiops.cli import app

    conn = MagicMock(name="conn")
    conn.get.return_value = {"status": "HEALTH_OK", "checks": {}}
    _patch_conn(monkeypatch, health_cli, conn)

    detail = runner.invoke(app, ["health", "detail"])
    assert detail.exit_code == 0, detail.output
    assert "HEALTH_OK" in detail.output

    conn.get.return_value = {
        "health": {"status": "HEALTH_OK"},
        "osd_map": {"osds": [{"up": 1, "in": 1}]},
        "df": {"stats": {}}, "pg_info": {}, "mon_status": {},
    }
    status = runner.invoke(app, ["health", "status"])
    assert status.exit_code == 0, status.output


@pytest.mark.unit
def test_overview_renders_json(monkeypatch):
    import ceph_aiops.cli.overview as overview_cli
    from ceph_aiops.cli import app

    def _dispatch(path, **kwargs):
        if "health" in path:
            return {"status": "HEALTH_OK", "checks": {}}
        return [{"osd": 0, "up": 1, "in": 1}]

    conn = MagicMock(name="conn")
    conn.get.side_effect = _dispatch
    _patch_conn(monkeypatch, overview_cli, conn)

    result = runner.invoke(app, ["overview"])
    assert result.exit_code == 0, result.output
    assert "osdsTotal" in result.output


@pytest.mark.unit
def test_cli_errors_translates_api_error(monkeypatch):
    import ceph_aiops.cli.health as health_cli
    from ceph_aiops.cli import app
    from ceph_aiops.connection import CephApiError

    def _boom(target=None):
        raise CephApiError("mgr unreachable", status_code=503, path="/api/health/full")

    monkeypatch.setattr(health_cli, "get_connection", _boom)
    result = runner.invoke(app, ["health", "detail"])
    assert result.exit_code == 1
    assert "Error:" in result.output
    assert "mgr unreachable" in result.output
