"""CLI write path — preview AND confirmed write, through governance, onto disk.

The CLI write commands delegate BOTH the ``--dry-run`` preview and the real
execution to the ``@governed_tool`` functions in ``mcp_server.tools``. These
tests drive a write command through the dry-run branch and past the
double-confirm prompts, and assert each really went through the governed path
(audit row on disk) — the regression test for the "CLI writes were unaudited"
and "CLI previews bypassed the guards" line-wide fixes.

The invariant a preview must hold is *a dry_run MAY read; it must never write*.
It is not "makes no call and leaves no trace": the MCP dry_run path has always
audited, because ``@governed_tool`` wraps the function regardless of the
``dry_run`` argument. The CLI was the outlier.
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


def _mutating_calls(conn: MagicMock) -> list[str]:
    """Every transport verb that can change cluster state."""
    return [
        verb
        for verb in ("post", "put", "patch", "delete")
        if getattr(conn, verb).called
    ]


@pytest.mark.unit
def test_cli_osd_out_dry_run_writes_nothing_but_is_audited(gov_home, monkeypatch):
    """A preview MAY read; it must never write.

    The preview now runs through the governed twin, so it is reachable by every
    guard on that write and leaves an audit row — exactly as the MCP dry_run
    path always has. What it must not do is mutate: no POST/PUT/PATCH/DELETE.
    """
    from ceph_aiops.cli import app

    conn = _mock_conn(monkeypatch)
    result = CliRunner().invoke(app, ["osd", "out", "3", "--dry-run"])
    assert result.exit_code == 0, result.output
    assert "DRY-RUN" in result.output
    assert _mutating_calls(conn) == []
    assert _audit_tools(gov_home / "audit.db") == ["osd_mark_out"]


@pytest.mark.unit
def test_cli_osd_purge_dry_run_writes_nothing_but_is_audited(gov_home, monkeypatch):
    from ceph_aiops.cli import app

    conn = _mock_conn(monkeypatch)
    result = CliRunner().invoke(app, ["osd", "purge", "3", "--dry-run"])
    assert result.exit_code == 0, result.output
    assert "DRY-RUN" in result.output
    assert _mutating_calls(conn) == []
    assert _audit_tools(gov_home / "audit.db") == ["osd_purge"]


@pytest.mark.unit
def test_cli_osd_out_dry_run_reports_a_transport_failure_instead_of_a_banner(
    gov_home, monkeypatch
):
    """A preview that could not reach the cluster knows nothing; it must not
    claim to know what would happen."""
    import mcp_server.tools.osd as gov_osd
    from ceph_aiops.cli import app

    def _boom(target=None):
        raise ValueError("no such target 'nope'")

    monkeypatch.setattr(gov_osd, "_get_connection", _boom)
    result = CliRunner().invoke(app, ["osd", "out", "3", "--dry-run", "-t", "nope"])
    assert result.exit_code == 1, result.output
    assert "DRY-RUN" not in result.output
    assert "no such target" in result.output


@pytest.mark.unit
def test_cli_osd_out_dry_run_still_renders_the_ordinary_banner(gov_home, monkeypatch):
    """The allowed path keeps the human banner it always had — not a JSON dump."""
    from ceph_aiops.cli import app

    _mock_conn(monkeypatch)
    result = CliRunner().invoke(app, ["osd", "out", "3", "--dry-run"])
    assert result.exit_code == 0, result.output
    assert "[DRY-RUN] No changes will be made." in result.output
    assert "Operation: mark_osd_out" in result.output
    assert "API Call:  POST /api/osd/3/mark" in result.output
    assert "action = out" in result.output
    assert "Run without --dry-run to execute." in result.output
    assert "wouldMarkOut" not in result.output, "render the banner, do not dump the dict"


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
