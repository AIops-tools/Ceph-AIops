"""RBD block images: inventory + guarded lifecycle (read + writes).

Reads normalise the Dashboard's ``/api/block/image`` records into a stable
inventory shape; writes cover image/snapshot create and the two data-destroying
deletes. Deleting an image or a snapshot has no safe inverse (the objects are
gone), so those are the high-risk ops the MCP layer gates behind a dry-run.

The Dashboard addresses a single image by an URL-encoded ``pool%2Fname`` spec;
we build that spec locally so callers pass plain pool/name arguments.
"""

from __future__ import annotations

from typing import Any

from ceph_aiops.ops._util import as_list, s

_IMAGE = "/api/block/image"


def _spec(pool_name: str, image_name: str) -> str:
    """URL-encoded ``pool%2Fname`` spec the Dashboard uses to address one image."""
    return f"{pool_name}%2F{image_name}"


def _norm_image(raw: dict) -> dict:
    """Fold one raw RBD image record into the stable inventory shape."""
    snaps = raw.get("snapshots") or []
    features = raw.get("features_name") or raw.get("features") or []
    return {
        "imageSpec": s(raw.get("unique_id") or f"{raw.get('pool_name')}/{raw.get('name')}"),
        "poolName": s(raw.get("pool_name")),
        "name": s(raw.get("name")),
        "sizeBytes": raw.get("size"),
        "objSizeBytes": raw.get("obj_size"),
        "features": [s(f) for f in features] if isinstance(features, list) else [],
        "snapshotCount": len(snaps) if isinstance(snaps, list) else 0,
    }


def list_images(conn: Any, pool: str | None = None) -> list[dict]:
    """[READ] All RBD images (optionally one pool): size, features, snapshot count."""
    params = {"pool_name": pool} if pool else None
    return [_norm_image(r) for r in as_list(conn.get(_IMAGE, params=params))]


# ── writes ───────────────────────────────────────────────────────────────


def create_image(conn: Any, pool_name: str, name: str, size_bytes: int) -> dict:
    """[WRITE][medium] Create a new RBD image in a pool."""
    conn.post(_IMAGE, json={"pool_name": pool_name, "name": name, "size": size_bytes})
    return {"action": "rbd_image_create", "poolName": s(pool_name), "name": s(name),
            "sizeBytes": size_bytes}


def create_snapshot(conn: Any, pool_name: str, image_name: str, snap_name: str) -> dict:
    """[WRITE][low] Snapshot an RBD image. Reversible → delete the snapshot."""
    conn.post(f"{_IMAGE}/{_spec(pool_name, image_name)}/snap",
              json={"snapshot_name": snap_name})
    return {"action": "rbd_snapshot_create", "imageSpec": f"{pool_name}/{image_name}",
            "snapName": s(snap_name)}


def delete_image(conn: Any, pool_name: str, image_name: str) -> dict:
    """[WRITE][high] Delete an RBD image and all its data. Irreversible."""
    prior = {"name": s(image_name)}
    conn.delete(f"{_IMAGE}/{_spec(pool_name, image_name)}")
    return {"action": "rbd_image_delete", "imageSpec": f"{pool_name}/{image_name}",
            "priorState": prior}


def delete_snapshot(conn: Any, pool_name: str, image_name: str, snap_name: str) -> dict:
    """[WRITE][high] Delete an RBD image snapshot. Irreversible."""
    conn.delete(f"{_IMAGE}/{_spec(pool_name, image_name)}/snap/{snap_name}")
    return {"action": "rbd_snapshot_delete", "imageSpec": f"{pool_name}/{image_name}",
            "snapName": s(snap_name)}
