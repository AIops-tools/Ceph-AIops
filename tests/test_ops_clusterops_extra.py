"""Unit tests for the cluster-level reads not covered by test_clusterops.py.

Proves: mon_status splits in/out-of-quorum mon names and reads the monmap epoch,
mgr_status surfaces the active mgr + standbys + modules, both degrade to an error
field on a failing read, capacity_forecast degrades on failure, and _conf_value
unwraps the nested cluster_conf value shape (and tolerates an unreadable option).
Connection is a MagicMock — no real cluster.
"""

from unittest.mock import MagicMock

import pytest


@pytest.mark.unit
def test_mon_status_splits_quorum_and_reads_epoch():
    from ceph_aiops.ops import clusterops as ops

    conn = MagicMock(name="conn")
    conn.get.return_value = {
        "in_quorum": [{"name": "a"}, {"name": "b"}],
        "out_quorum": ["c"],
        "mon_status": {"monmap": {"epoch": 12}},
    }
    out = ops.mon_status(conn)
    conn.get.assert_called_once_with("/api/monitor")
    assert out["inQuorum"] == ["a", "b"]
    assert out["outOfQuorum"] == ["c"]
    assert out["epoch"] == 12


@pytest.mark.unit
def test_mon_status_resilient_to_failure():
    from ceph_aiops.ops import clusterops as ops

    conn = MagicMock(name="conn")
    conn.get.side_effect = RuntimeError("mon api down")
    assert "error" in ops.mon_status(conn)


@pytest.mark.unit
def test_mgr_status_surfaces_active_standbys_modules():
    from ceph_aiops.ops import clusterops as ops

    conn = MagicMock(name="conn")
    conn.get.return_value = {
        "mgr_map": {
            "active_name": "node1",
            "standbys": [{"name": "node2"}, {"name": "node3"}],
            "modules": ["dashboard", "prometheus"],
        }
    }
    out = ops.mgr_status(conn)
    assert out["active"] == "node1"
    assert out["standbys"] == ["node2", "node3"]
    assert out["modules"] == ["dashboard", "prometheus"]


@pytest.mark.unit
def test_mgr_status_resilient_to_failure():
    from ceph_aiops.ops import clusterops as ops

    conn = MagicMock(name="conn")
    conn.get.side_effect = RuntimeError("boom")
    assert "error" in ops.mgr_status(conn)


@pytest.mark.unit
def test_capacity_forecast_resilient_to_failure():
    from ceph_aiops.ops import clusterops as ops

    conn = MagicMock(name="conn")
    conn.get.side_effect = RuntimeError("df unavailable")
    assert "error" in ops.capacity_forecast(conn, daily_growth_bytes=10)


@pytest.mark.unit
def test_conf_value_unwraps_list_shape():
    from ceph_aiops.ops import clusterops as ops

    conn = MagicMock(name="conn")
    conn.get.return_value = {"value": [{"section": "osd", "value": "3"}]}
    assert ops._conf_value(conn, "osd_max_backfills") == "3"


@pytest.mark.unit
def test_conf_value_scalar_and_unreadable():
    from ceph_aiops.ops import clusterops as ops

    conn = MagicMock(name="conn")
    conn.get.return_value = {"value": "7"}
    assert ops._conf_value(conn, "opt") == "7"

    conn.get.side_effect = RuntimeError("no such option")
    assert ops._conf_value(conn, "opt") is None


@pytest.mark.unit
def test_throttle_recovery_both_settings_applied():
    from ceph_aiops.ops import clusterops as ops

    conn = MagicMock(name="conn")
    conn.get.return_value = {"value": "5"}
    conn.post.return_value = {}
    out = ops.throttle_recovery(conn, max_backfills=1, recovery_max_active=2)
    assert out["applied"] == {
        "osd_max_backfills": "1",
        "osd_recovery_max_active": "2",
    }
    assert conn.post.call_count == 2
