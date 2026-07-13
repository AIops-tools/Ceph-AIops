"""Pool ops + MCP tool tests for ceph-aiops.

Proves: pool_ls normalises the raw pool map, the reversible writes capture
BEFORE-state, the risk tiers are correct (size/delete=high, the rest=medium),
the delete dry-run gates the destructive call, and the quota undo descriptor
restores the captured prior quota. No real Ceph cluster is needed — the
connection is a MagicMock.
"""

from unittest.mock import MagicMock

import pytest


@pytest.mark.unit
def test_pool_ls_normalizes():
    from ceph_aiops.ops import pool as ops

    conn = MagicMock(name="conn")
    conn.get.return_value = [{
        "pool_name": "rbd",
        "pool": 2,
        "size": 3,
        "min_size": 2,
        "pg_num": 128,
        "pg_autoscale_mode": "on",
        "application_metadata": {"rbd": {}},
        "quota_max_bytes": 1024,
    }]
    rows = ops.list_pools(conn)
    assert rows == [{
        "poolName": "rbd",
        "poolId": 2,
        "size": 3,
        "minSize": 2,
        "pgNum": 128,
        "autoscaleMode": "on",
        "application": "rbd",
        "quotaMaxBytes": 1024,
    }]


@pytest.mark.unit
def test_set_pool_size_captures_prior_and_puts():
    from ceph_aiops.ops import pool as ops

    conn = MagicMock(name="conn")
    conn.get.return_value = {"pool_name": "rbd", "size": 3, "min_size": 2}
    conn.put.return_value = {}
    out = ops.set_pool_size(conn, "rbd", 2)
    assert out["action"] == "set_pool_size"
    assert out["priorState"] == {"size": 3, "minSize": 2}
    conn.put.assert_called_once()
    _, kwargs = conn.put.call_args
    assert kwargs["json"]["size"] == 2


@pytest.mark.unit
def test_write_tools_have_correct_risk_tiers():
    from mcp_server.tools import pool as p

    assert p.set_pool_size._risk_level == "high"
    assert p.pool_delete._risk_level == "high"
    assert p.set_pool_quota._risk_level == "medium"
    assert p.set_pool_pg_num._risk_level == "medium"
    assert p.set_pool_autoscale._risk_level == "medium"
    assert p.pool_create._risk_level == "medium"


@pytest.mark.unit
def test_pool_delete_dry_run_does_not_mutate(monkeypatch):
    from mcp_server.tools import pool as p

    conn = MagicMock(name="conn")
    monkeypatch.setattr(p, "_get_connection", lambda target=None: conn)
    result = p.pool_delete(pool_name="rbd", dry_run=True)
    assert result["dryRun"] is True
    assert result["wouldDelete"]["poolName"] == "rbd"
    conn.delete.assert_not_called()


@pytest.mark.unit
def test_set_pool_quota_undo_restores_prior(monkeypatch):
    import ceph_aiops.governance.undo as undo_mod
    from mcp_server.tools import pool as p

    conn = MagicMock(name="conn")
    conn.get.return_value = {"quota_max_bytes": 1000, "quota_max_objects": 50}
    conn.put.return_value = {}
    monkeypatch.setattr(p, "_get_connection", lambda target=None: conn)

    recorded = {}

    class _Store:
        def record(self, *, skill, tool, undo_descriptor, orig_params):
            recorded["d"] = undo_descriptor
            return "undo-1"

    monkeypatch.setattr(undo_mod, "get_undo_store", lambda: _Store())

    result = p.set_pool_quota(pool_name="rbd", max_bytes=2000)
    assert result["priorState"]["quotaMaxBytes"] == 1000
    d = recorded["d"]
    assert d["tool"] == "set_pool_quota"
    assert d["params"]["max_bytes"] == 1000  # restore prior
    assert d["params"]["max_objects"] == 50
    assert result.get("_undo_id") == "undo-1"


@pytest.mark.unit
def test_pool_path_segments_are_url_encoded():
    """A hostile pool name containing ``../`` must never reach the mgr as a raw
    path traversal — every agent-supplied path segment is URL-encoded."""
    from ceph_aiops.ops import pool as ops

    conn = MagicMock(name="conn")
    conn.get.return_value = {"pool_name": "x", "size": 3, "min_size": 2}
    conn.put.return_value = {}
    ops.set_pool_size(conn, "../admin", 2)

    get_path = conn.get.call_args[0][0]
    put_path = conn.put.call_args[0][0]
    for path in (get_path, put_path):
        assert "../" not in path
        assert path == "/api/pool/..%2Fadmin"
