"""Unit tests for the RBD writes not covered by test_rbd.py.

Proves: create_image posts the image body, delete_image/delete_snapshot hit the
URL-encoded pool%2Fname spec paths, list_images passes a pool filter param, and
the high-risk snapshot delete honours dry_run in the MCP twin. Connection is a
MagicMock — no real cluster.
"""

from unittest.mock import MagicMock

import pytest

_IMAGE = "/api/block/image"


@pytest.mark.unit
def test_create_image_posts_body():
    from ceph_aiops.ops import rbd as ops

    conn = MagicMock(name="conn")
    conn.post.return_value = {}
    out = ops.create_image(conn, "rbd", "disk0", 1073741824)
    conn.post.assert_called_once_with(
        _IMAGE, json={"pool_name": "rbd", "name": "disk0", "size": 1073741824})
    assert out["action"] == "rbd_image_create"
    assert out["sizeBytes"] == 1073741824


@pytest.mark.unit
def test_list_images_passes_pool_filter():
    from ceph_aiops.ops import rbd as ops

    conn = MagicMock(name="conn")
    conn.get.return_value = []
    ops.list_images(conn, pool="rbd")
    conn.get.assert_called_once_with(_IMAGE, params={"pool_name": "rbd"})


@pytest.mark.unit
def test_delete_image_targets_encoded_spec():
    from ceph_aiops.ops import rbd as ops

    conn = MagicMock(name="conn")
    conn.delete.return_value = {}
    out = ops.delete_image(conn, "rbd", "disk0")
    conn.delete.assert_called_once_with(f"{_IMAGE}/rbd%2Fdisk0")
    assert out["action"] == "rbd_image_delete"
    assert out["priorState"]["name"] == "disk0"


@pytest.mark.unit
def test_delete_snapshot_targets_encoded_snap_path():
    from ceph_aiops.ops import rbd as ops

    conn = MagicMock(name="conn")
    conn.delete.return_value = {}
    out = ops.delete_snapshot(conn, "rbd", "disk0", "snap1")
    conn.delete.assert_called_once_with(f"{_IMAGE}/rbd%2Fdisk0/snap/snap1")
    assert out["snapName"] == "snap1"


@pytest.mark.unit
def test_spec_url_encodes_hostile_names():
    from ceph_aiops.ops import rbd as ops

    conn = MagicMock(name="conn")
    conn.delete.return_value = {}
    ops.delete_image(conn, "../admin", "x")
    path = conn.delete.call_args[0][0]
    assert "../" not in path
    assert path == "/api/block/image/..%2Fadmin%2Fx"


@pytest.mark.unit
def test_rbd_snapshot_delete_dry_run_does_not_mutate(monkeypatch):
    from mcp_server.tools import rbd as r

    conn = MagicMock(name="conn")
    monkeypatch.setattr(r, "_get_connection", lambda target=None: conn)
    out = r.rbd_snapshot_delete(
        pool_name="rbd", image_name="disk0", snap_name="snap1", dry_run=True)
    assert out["dryRun"] is True
    assert out["wouldDelete"]["snapName"] == "snap1"
    conn.delete.assert_not_called()


@pytest.mark.unit
def test_rbd_image_create_via_governed_twin(monkeypatch):
    from mcp_server.tools import rbd as r

    conn = MagicMock(name="conn")
    conn.post.return_value = {}
    monkeypatch.setattr(r, "_get_connection", lambda target=None: conn)
    out = r.rbd_image_create(pool_name="rbd", name="disk0", size_bytes=1024)
    assert out["action"] == "rbd_image_create"
    conn.post.assert_called_once()
