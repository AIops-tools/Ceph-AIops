"""Unit tests for the cluster-level ops + MCP tools.

Proves: capacity_forecast projects days-to-nearfull from a /api/health/full df
payload (and degrades to insufficient-data without a growth rate), slow_ops
decodes a SLOW_OPS health check (and reports count=0 when absent), and
throttle_recovery is a risk=medium write that captures prior config into
priorState with a working undo descriptor. No real Ceph cluster is needed — the
connection is a MagicMock.
"""

from unittest.mock import MagicMock

import pytest


def _df_payload():
    # nearfull = 0.85 * 1000 = 850; headroom over used(800) = 50.
    return {
        "df": {
            "stats": {
                "total_bytes": 1000,
                "total_used_raw_bytes": 800,
                "total_avail_bytes": 200,
            }
        }
    }


def _slow_ops_payload():
    return {
        "health": {
            "checks": {
                "SLOW_OPS": {
                    "severity": "HEALTH_WARN",
                    "summary": {"message": "2 slow ops, oldest blocked 45 sec", "count": 2},
                    "detail": [
                        {"message": "osd.3 has slow ops", "osd": 3, "blockedMs": 45000},
                        {"message": "osd.5 has slow ops", "osd": 5, "blockedMs": 12000},
                    ],
                }
            }
        }
    }


@pytest.mark.unit
def test_capacity_forecast_projects_days_to_nearfull():
    from ceph_aiops.ops import clusterops as ops

    conn = MagicMock(name="conn")
    conn.get.return_value = _df_payload()
    out = ops.capacity_forecast(conn, daily_growth_bytes=10)
    # (0.85 * 1000 - 800) / 10 == 5
    assert out["daysToNearfull"] == 5
    assert out["forecast"] == "ok"
    assert out["usedBytes"] == 800
    assert out["usedPercent"] == 80.0


@pytest.mark.unit
def test_capacity_forecast_insufficient_data_without_growth():
    from ceph_aiops.ops import clusterops as ops

    conn = MagicMock(name="conn")
    conn.get.return_value = _df_payload()
    out = ops.capacity_forecast(conn)
    assert out["daysToNearfull"] is None
    assert out["forecast"] == "insufficient-data"


@pytest.mark.unit
def test_slow_ops_parses_check_and_zero_when_absent():
    from ceph_aiops.ops import clusterops as ops

    conn = MagicMock(name="conn")
    conn.get.return_value = _slow_ops_payload()
    out = ops.slow_ops(conn)
    assert out["count"] == 2
    assert {"osd": 3, "blockedMs": 45000} in out["byOsd"]

    conn.get.return_value = {"health": {"checks": {}}}
    empty = ops.slow_ops(conn)
    assert empty == {"count": 0, "byOsd": []}


@pytest.mark.unit
def test_throttle_recovery_risk_and_prior_state_undo():
    from mcp_server.tools import clusterops

    assert clusterops.throttle_recovery._risk_level == "medium"

    conn = MagicMock(name="conn")
    conn.get.return_value = {"value": [{"section": "osd", "value": "3"}]}
    conn.post.return_value = {}
    out = clusterops.ops.throttle_recovery(conn, max_backfills=1)
    assert out["priorState"]["osd_max_backfills"] == "3"
    assert out["applied"] == {"osd_max_backfills": "1"}

    undo = clusterops._throttle_undo({"max_backfills": 1}, out)
    assert undo["tool"] == "throttle_recovery"
    assert undo["params"]["max_backfills"] == "3"
