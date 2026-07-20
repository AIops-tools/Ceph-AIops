"""Unit tests for the pool reads/writes not covered by test_pool.py.

Proves: pool_df folds the nested per-pool stats (and computes usable capacity /
degrades to [] on failure), _norm_pool handles the list-form application field,
the pg_num/autoscale/create/delete writes hit the right endpoints and capture
prior state, and the MCP twins record prior-state undos + gate the high-risk
size change behind dry_run. Connection is a MagicMock — no real cluster.
"""

from unittest.mock import MagicMock

import pytest

_POOL = "/api/pool"


@pytest.mark.unit
def test_pool_df_folds_nested_stats_and_usable_capacity():
    from ceph_aiops.ops import pool as ops

    conn = MagicMock(name="conn")
    conn.get.return_value = [{
        "pool_name": "rbd",
        "size": 3,
        "stats": {
            "bytes_used": {"latest": 3000},
            "max_avail": {"latest": 900},
            "percent_used": {"latest": 0.25},
            "objects": {"latest": 12},
            "avail_raw": {"latest": 2700},
        },
    }]
    rows = ops.pool_df(conn)["pools"]
    conn.get.assert_called_once_with(_POOL, params={"stats": "true"})
    row = rows[0]
    assert row["poolName"] == "rbd"
    assert row["usedBytes"] == 3000
    assert row["availBytes"] == 900
    assert row["percentUsed"] == 0.25
    assert row["objects"] == 12
    # usableCapacityBytes = avail_raw / size = 2700 / 3
    assert row["usableCapacityBytes"] == 900


@pytest.mark.unit
def test_pool_df_reports_failure_instead_of_empty_list():
    """A flaky mgr must not be indistinguishable from a cluster with no pools."""
    from ceph_aiops.ops import pool as ops

    conn = MagicMock(name="conn")
    conn.get.side_effect = RuntimeError("mgr flaky")
    out = ops.pool_df(conn)
    assert out["pools"] == []
    assert out["returned"] == 0
    assert "mgr flaky" in out["error"]


@pytest.mark.unit
def test_pool_df_usable_none_without_size():
    from ceph_aiops.ops import pool as ops

    conn = MagicMock(name="conn")
    conn.get.return_value = [{"pool_name": "x", "stats": {"bytes_used": 10}}]
    row = ops.pool_df(conn)["pools"][0]
    assert row["usableCapacityBytes"] is None


@pytest.mark.unit
def test_norm_pool_application_from_list():
    from ceph_aiops.ops import pool as ops

    conn = MagicMock(name="conn")
    conn.get.return_value = [{
        "pool_name": "cephfs_data",
        "pool_id": 5,
        "applications": ["cephfs", "rgw"],
    }]
    row = ops.list_pools(conn)[0]
    assert row["poolId"] == 5
    assert row["application"] == "cephfs,rgw"


@pytest.mark.unit
def test_set_pool_pg_num_captures_prior():
    from ceph_aiops.ops import pool as ops

    conn = MagicMock(name="conn")
    conn.get.return_value = {"pg_num": 128}
    conn.put.return_value = {}
    out = ops.set_pool_pg_num(conn, "rbd", 256)
    assert out["priorState"]["pgNum"] == 128
    assert out["pgNum"] == 256
    _, kwargs = conn.put.call_args
    assert kwargs["json"] == {"pool": "rbd", "pg_num": 256}


@pytest.mark.unit
def test_set_pool_autoscale_captures_prior():
    from ceph_aiops.ops import pool as ops

    conn = MagicMock(name="conn")
    conn.get.return_value = {"pg_autoscale_mode": "on"}
    conn.put.return_value = {}
    out = ops.set_pool_autoscale(conn, "rbd", "warn")
    assert out["priorState"]["autoscaleMode"] == "on"
    assert out["mode"] == "warn"


@pytest.mark.unit
def test_create_pool_posts_replicated_body():
    from ceph_aiops.ops import pool as ops

    conn = MagicMock(name="conn")
    conn.post.return_value = {}
    out = ops.create_pool(conn, "newpool", pg_num=64, size=2, application="cephfs")
    conn.post.assert_called_once_with(_POOL, json={
        "pool": "newpool",
        "pool_type": "replicated",
        "pg_num": 64,
        "size": 2,
        "application_metadata": ["cephfs"],
    })
    assert out["action"] == "pool_create"
    assert out["application"] == "cephfs"


@pytest.mark.unit
def test_delete_pool_captures_prior_and_deletes():
    from ceph_aiops.ops import pool as ops

    conn = MagicMock(name="conn")
    conn.get.return_value = {"pool_name": "rbd", "size": 3}
    conn.delete.return_value = {}
    out = ops.delete_pool(conn, "rbd")
    assert out["action"] == "pool_delete"
    assert out["priorState"]["poolName"] == "rbd"
    assert out["priorState"]["size"] == 3
    conn.delete.assert_called_once_with(f"{_POOL}/rbd")


# ── MCP twins ──────────────────────────────────────────────────────────────


@pytest.mark.unit
def test_set_pool_size_dry_run_does_not_mutate(monkeypatch):
    from mcp_server.tools import pool as p

    conn = MagicMock(name="conn")
    monkeypatch.setattr(p, "_get_connection", lambda target=None: conn)
    out = p.set_pool_size(pool_name="rbd", size=2, dry_run=True)
    assert out["dryRun"] is True
    assert out["wouldSetSize"] == {"poolName": "rbd", "size": 2}
    conn.put.assert_not_called()


@pytest.mark.unit
def test_pool_create_via_governed_twin(monkeypatch):
    from mcp_server.tools import pool as p

    conn = MagicMock(name="conn")
    conn.post.return_value = {}
    monkeypatch.setattr(p, "_get_connection", lambda target=None: conn)
    out = p.pool_create(pool_name="np", pg_num=32, size=3)
    assert out["action"] == "pool_create"
    conn.post.assert_called_once()


@pytest.mark.unit
def test_set_pool_pg_num_records_prior_state_undo(monkeypatch):
    import ceph_aiops.governance.undo as undo_mod
    from mcp_server.tools import pool as p

    conn = MagicMock(name="conn")
    conn.get.return_value = {"pg_num": 128}
    conn.put.return_value = {}
    monkeypatch.setattr(p, "_get_connection", lambda target=None: conn)

    recorded = {}

    class _Store:
        def record(self, *, skill, tool, undo_descriptor, orig_params, effect_verified=True):
            recorded["d"] = undo_descriptor
            return "undo-pg-1"

    monkeypatch.setattr(undo_mod, "get_undo_store", lambda: _Store())

    out = p.set_pool_pg_num(pool_name="rbd", pg_num=256)
    assert out["priorState"]["pgNum"] == 128
    assert recorded["d"]["params"]["pg_num"] == 128
    assert out.get("_undo_id") == "undo-pg-1"


@pytest.mark.unit
def test_pgnum_undo_none_without_prior():
    from mcp_server.tools import pool as p

    assert p._pgnum_undo({"pool_name": "x"}, {"priorState": {}}) is None
    assert p._autoscale_undo({"pool_name": "x"}, {"priorState": {}}) is None
    assert p._size_undo({"pool_name": "x"}, {"priorState": {}}) is None
    assert p._quota_undo({}, "not-a-dict") is None
