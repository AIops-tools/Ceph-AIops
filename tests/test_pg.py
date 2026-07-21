"""Unit tests for the Placement Group ops + MCP tools.

Proves: pg_summary builds a state histogram from a /api/health/full payload and
is resilient to a failing read, trigger_scrub posts to the scrub path, and the
scrub write tools carry the correct (low) risk tier. No real Ceph cluster is
needed — the connection is a MagicMock.
"""

from unittest.mock import MagicMock

import pytest


def _health_payload():
    return {
        "pg_info": {
            "pgs": [
                {"pgid": "2.0", "state": "active+clean", "up": [0, 1]},
                {"pgid": "2.1", "state": "active+clean", "up": [1, 2]},
                {"pgid": "2.2", "state": "active+undersized+degraded", "up": [3]},
            ]
        }
    }


@pytest.mark.unit
def test_pg_summary_builds_state_histogram():
    from ceph_aiops.ops import pg as ops

    conn = MagicMock(name="conn")
    conn.get.return_value = _health_payload()
    out = ops.pg_summary(conn)
    assert out["states"]["active+clean"] == 2
    assert out["states"]["active+undersized+degraded"] == 1
    assert out["unhealthyCount"] == 1
    assert out["unhealthy"][0]["pgid"] == "2.2"
    assert out["truncated"] is False and out["returned"] == 1


@pytest.mark.unit
def test_pg_summary_resilient_to_failure():
    from ceph_aiops.ops import pg as ops

    conn = MagicMock(name="conn")
    conn.get.side_effect = RuntimeError("mgr down")
    out = ops.pg_summary(conn)
    assert "error" in out


@pytest.mark.unit
def test_trigger_scrub_posts_to_scrub_path():
    from ceph_aiops.ops import pg as ops

    conn = MagicMock(name="conn")
    conn.post.return_value = {}
    out = ops.trigger_scrub(conn, "2.1a")
    conn.post.assert_called_once_with("/api/pg/2.1a/scrub", json={})
    assert out == {"action": "trigger_scrub", "pgid": "2.1a"}


@pytest.mark.unit
def test_scrub_write_tools_have_correct_risk_tiers():
    from mcp_server.tools import pg

    assert pg.trigger_scrub._risk_level == "medium"  # a write (non-low risk_level)
    assert pg.trigger_deep_scrub._risk_level == "medium"
