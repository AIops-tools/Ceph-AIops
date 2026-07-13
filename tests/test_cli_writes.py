"""CLI confirmed-write path — past dry-run, through governance, onto disk.

The CLI write commands delegate real execution to the ``@governed_tool``
functions in ``mcp_server.tools``. These tests drive a write command PAST the
dry-run branch and the double-confirm prompts and assert the call really went
through the governed path (audit row on disk) — the regression test for the
"CLI writes were unaudited" line-wide fix.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from typer.testing import CliRunner

import ceph_aiops.governance.audit as audit_mod
import ceph_aiops.governance.policy as policy_mod
import ceph_aiops.governance.undo as undo_mod


@pytest.fixture
def gov_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("CEPH_AIOPS_HOME", str(tmp_path))
    audit_mod.reset_engine()
    policy_mod.reset_policy_engine()
    undo_mod.reset_undo_store()
    yield tmp_path
    audit_mod.reset_engine()
    policy_mod.reset_policy_engine()
    undo_mod.reset_undo_store()


def _audit_tools(db_path: Path) -> list[str]:
    conn = sqlite3.connect(db_path)
    try:
        return [r[0] for r in conn.execute("SELECT tool FROM audit_log ORDER BY id")]
    finally:
        conn.close()


def _mock_conn(monkeypatch: pytest.MonkeyPatch) -> MagicMock:
    """Mock the governed module's connection with one out-able OSD."""
    import mcp_server.tools.osd as gov_osd

    conn = MagicMock(name="conn")
    conn.get.return_value = [{"osd": 3, "up": 1, "in": 1, "weight": 1.0, "host": "ceph-a"}]
    conn.post.return_value = {}
    monkeypatch.setattr(gov_osd, "_get_connection", lambda target=None: conn)
    return conn


@pytest.mark.unit
def test_cli_osd_out_dry_run_makes_no_call_and_no_audit(gov_home, monkeypatch):
    from ceph_aiops.cli import app

    conn = _mock_conn(monkeypatch)
    result = CliRunner().invoke(app, ["osd", "out", "3", "--dry-run"])
    assert result.exit_code == 0
    assert "DRY-RUN" in result.output
    conn.get.assert_not_called()
    conn.post.assert_not_called()
    assert not (gov_home / "audit.db").exists()


@pytest.mark.unit
def test_cli_osd_out_confirmed_goes_through_governance(gov_home, monkeypatch):
    """Confirmed CLI write must execute via the governed twin: the API call runs
    AND an audit row lands in audit.db (this is what the reroute fix bought)."""
    from ceph_aiops.cli import app

    conn = _mock_conn(monkeypatch)
    result = CliRunner().invoke(app, ["osd", "out", "3"], input="y\ny\n")
    assert result.exit_code == 0, result.output
    conn.post.assert_called_once()
    assert _audit_tools(gov_home / "audit.db") == ["osd_mark_out"]


@pytest.mark.unit
def test_cli_osd_out_aborts_without_double_confirm(gov_home, monkeypatch):
    from ceph_aiops.cli import app

    conn = _mock_conn(monkeypatch)
    result = CliRunner().invoke(app, ["osd", "out", "3"], input="y\nn\n")
    assert result.exit_code != 0
    conn.post.assert_not_called()
    assert not (gov_home / "audit.db").exists()
