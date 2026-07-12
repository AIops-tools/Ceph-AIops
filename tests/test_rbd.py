"""Unit tests for the RBD domain module (ops + MCP tools).

Proves: rbd_ls normalises a raw image record, snapshot create posts to the
image's snap path, the write tools carry the correct risk tiers, and the
high-risk image delete honours dry_run (no conn.delete). No real Ceph cluster
is needed — the connection is a MagicMock.
"""

from unittest.mock import MagicMock

import pytest


@pytest.mark.unit
def test_rbd_ls_normalizes_image():
    from ceph_aiops.ops import rbd as ops

    conn = MagicMock(name="conn")
    conn.get.return_value = [{
        "pool_name": "rbd",
        "name": "vm-100-disk-0",
        "unique_id": "rbd/vm-100-disk-0",
        "size": 10737418240,
        "obj_size": 4194304,
        "features_name": ["layering", "exclusive-lock"],
        "snapshots": [{"name": "snap1"}, {"name": "snap2"}],
    }]

    out = ops.list_images(conn)
    assert len(out) == 1
    img = out[0]
    assert img["poolName"] == "rbd"
    assert img["name"] == "vm-100-disk-0"
    assert img["imageSpec"] == "rbd/vm-100-disk-0"
    assert img["sizeBytes"] == 10737418240
    assert img["objSizeBytes"] == 4194304
    assert img["features"] == ["layering", "exclusive-lock"]
    assert img["snapshotCount"] == 2


@pytest.mark.unit
def test_create_snapshot_posts_to_snap_path():
    from ceph_aiops.ops import rbd as ops

    conn = MagicMock(name="conn")
    conn.post.return_value = {}
    out = ops.create_snapshot(conn, "rbd", "vm-100-disk-0", "before-upgrade")

    conn.post.assert_called_once_with(
        "/api/block/image/rbd%2Fvm-100-disk-0/snap",
        json={"snapshot_name": "before-upgrade"},
    )
    assert out["action"] == "rbd_snapshot_create"
    assert out["imageSpec"] == "rbd/vm-100-disk-0"
    assert out["snapName"] == "before-upgrade"


@pytest.mark.unit
def test_write_tools_have_correct_risk_tiers():
    from mcp_server.tools import rbd as r

    assert r.rbd_image_delete._risk_level == "high"
    assert r.rbd_snapshot_delete._risk_level == "high"
    assert r.rbd_snapshot_create._risk_level == "low"
    assert r.rbd_image_create._risk_level == "medium"


@pytest.mark.unit
def test_rbd_image_delete_dry_run_does_not_mutate(monkeypatch):
    from mcp_server.tools import rbd as r

    conn = MagicMock(name="conn")
    monkeypatch.setattr(r, "_get_connection", lambda target=None: conn)
    result = r.rbd_image_delete(pool_name="rbd", image_name="vm-100-disk-0", dry_run=True)

    assert result["dryRun"] is True
    assert result["wouldDelete"]["imageSpec"] == "rbd/vm-100-disk-0"
    conn.delete.assert_not_called()
