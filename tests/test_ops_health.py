"""Unit tests for the health RCA ops + MCP tools (the flagship read surface).

Proves: cluster_health decodes a known check code into a canned cause/action,
falls back to the check summary for an unknown code, handles the list-of-checks
shape, and degrades to an error field when the read fails; cluster_status folds
the /api/health/minimal payload (mons/osds/usage) and counts up/in OSDs. No real
Ceph cluster is needed — the connection is a MagicMock.
"""

from unittest.mock import MagicMock

import pytest


@pytest.mark.unit
def test_cluster_health_decodes_known_check():
    from ceph_aiops.ops import health as ops

    conn = MagicMock(name="conn")
    conn.get.return_value = {
        "health": {
            "status": "HEALTH_WARN",
            "checks": {
                "OSD_NEARFULL": {
                    "severity": "HEALTH_WARN",
                    "summary": {"message": "1 nearfull osd(s)"},
                },
            },
        }
    }
    out = ops.cluster_health(conn)
    conn.get.assert_called_once_with("/api/health/full")
    assert out["status"] == "HEALTH_WARN"
    assert out["healthy"] is False
    assert out["activeChecks"] == 1
    finding = out["findings"][0]
    assert finding["check"] == "OSD_NEARFULL"
    assert finding["severity"] == "HEALTH_WARN"
    assert finding["summary"] == "1 nearfull osd(s)"
    # The canned RCA — not the raw summary — is what an operator gets.
    assert "nearfull_ratio" in finding["cause"]
    assert "osd_reweight" in finding["suggestedAction"]


@pytest.mark.unit
def test_cluster_health_unknown_check_falls_back_to_summary():
    from ceph_aiops.ops import health as ops

    conn = MagicMock(name="conn")
    conn.get.return_value = {
        "status": "HEALTH_ERR",
        "checks": {
            "SOME_NOVEL_CHECK": {
                "severity": "HEALTH_ERR",
                "summary": {"message": "a brand new warning"},
            },
        },
    }
    out = ops.cluster_health(conn)
    assert out["status"] == "HEALTH_ERR"
    finding = out["findings"][0]
    assert finding["check"] == "SOME_NOVEL_CHECK"
    # Unknown code: cause is the check's own summary message.
    assert finding["cause"] == "a brand new warning"
    assert finding["suggestedAction"] == "Investigate via the related read tool."


@pytest.mark.unit
def test_cluster_health_handles_list_of_checks_shape():
    from ceph_aiops.ops import health as ops

    conn = MagicMock(name="conn")
    conn.get.return_value = {
        "health": {
            "status": "HEALTH_WARN",
            "checks": [
                {"type": "MON_DOWN", "severity": "HEALTH_WARN",
                 "summary": {"message": "1/3 mons down"}},
            ],
        }
    }
    out = ops.cluster_health(conn)
    assert out["activeChecks"] == 1
    assert out["findings"][0]["check"] == "MON_DOWN"
    assert "quorum" in out["findings"][0]["cause"]


@pytest.mark.unit
def test_cluster_health_healthy_and_ok_status():
    from ceph_aiops.ops import health as ops

    conn = MagicMock(name="conn")
    conn.get.return_value = {"status": "HEALTH_OK", "checks": {}}
    out = ops.cluster_health(conn)
    assert out["healthy"] is True
    assert out["activeChecks"] == 0
    assert out["findings"] == []


@pytest.mark.unit
def test_cluster_health_resilient_to_failure():
    from ceph_aiops.ops import health as ops

    conn = MagicMock(name="conn")
    conn.get.side_effect = RuntimeError("mgr unreachable")
    out = ops.cluster_health(conn)
    assert "error" in out
    assert "mgr unreachable" in out["error"]


@pytest.mark.unit
def test_cluster_status_folds_minimal_payload():
    from ceph_aiops.ops import health as ops

    conn = MagicMock(name="conn")
    conn.get.return_value = {
        "health": {"status": "HEALTH_OK"},
        "mon_status": {"monmap": {"num_mons": 3}},
        "osd_map": {"osds": [
            {"up": 1, "in": 1},
            {"up": 1, "in": 1},
            {"up": 0, "in": 1},
        ]},
        "df": {"stats": {"total_used_raw_bytes": 500, "total_bytes": 2000}},
        "pg_info": {"pgs_per_osd": 100, "object_stats": {"num_objects": 42}},
    }
    out = ops.cluster_status(conn)
    conn.get.assert_called_once_with("/api/health/minimal")
    assert out["status"] == "HEALTH_OK"
    assert out["monsInQuorum"] == 3
    assert out["osdsUp"] == 2
    assert out["osdsIn"] == 3
    assert out["usedBytes"] == 500
    assert out["totalBytes"] == 2000
    assert out["objects"] == 42


@pytest.mark.unit
def test_cluster_status_resilient_to_failure():
    from ceph_aiops.ops import health as ops

    conn = MagicMock(name="conn")
    conn.get.side_effect = RuntimeError("boom")
    out = ops.cluster_status(conn)
    assert "error" in out


@pytest.mark.unit
def test_count_helper_handles_non_list():
    from ceph_aiops.ops import health as ops

    assert ops._count("not-a-list", "up") is None
    assert ops._count([{"up": 1}, {"up": 0}, "junk"], "up") == 1


@pytest.mark.unit
def test_health_mcp_tools_are_governed_and_low_risk():
    from mcp_server.tools import health

    assert health.cluster_health._risk_level == "low"
    assert health.cluster_status._risk_level == "low"
    assert getattr(health.cluster_health, "_is_governed_tool", False)
