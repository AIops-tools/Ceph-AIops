"""RBD block image inventory + guarded lifecycle MCP tools (read + writes).

Deleting an RBD image or snapshot destroys data with no safe inverse, so both
deletes are risk=high with a ``dry_run`` preview. Image and snapshot creates are
lower risk and mutate nothing on dry paths.
"""

from typing import Optional

from ceph_aiops.governance import governed_tool
from ceph_aiops.ops import rbd as ops
from mcp_server._shared import _get_connection, mcp, tool_errors

# ── reads ────────────────────────────────────────────────────────────────


@mcp.tool()
@governed_tool(risk_level="low")
@tool_errors("dict")
def rbd_ls(pool: Optional[str] = None, target: Optional[str] = None) -> list:
    """[READ] RBD images (optionally one pool): size, features, snapshot count.

    Args:
        pool: Restrict to a single pool name; omit for all pools.
        target: Ceph target name from config; omit for the default.
    """
    return ops.list_images(_get_connection(target), pool=pool)


# ── writes ───────────────────────────────────────────────────────────────


@mcp.tool()
@governed_tool(risk_level="medium")
@tool_errors("dict")
def rbd_image_create(pool_name: str, name: str, size_bytes: int,
                     target: Optional[str] = None) -> dict:
    """[WRITE][risk=medium] Create a new RBD image in a pool.

    Args:
        pool_name: Pool the image lives in (from a pool listing).
        name: New image name.
        size_bytes: Image size in bytes.
        target: Ceph target name from config; omit for the default.
    """
    return ops.create_image(_get_connection(target), pool_name, name, size_bytes)


@mcp.tool()
@governed_tool(risk_level="medium")
@tool_errors("dict")
def rbd_snapshot_create(pool_name: str, image_name: str, snap_name: str,
                        target: Optional[str] = None) -> dict:
    """[WRITE][risk=medium] Snapshot an RBD image. Reversible → delete the snapshot.

    Args:
        pool_name: Pool the image lives in.
        image_name: Image to snapshot (from rbd_ls).
        snap_name: Name for the new snapshot.
        target: Ceph target name from config; omit for the default.
    """
    return ops.create_snapshot(_get_connection(target), pool_name, image_name, snap_name)


@mcp.tool()
@governed_tool(risk_level="high")
@tool_errors("dict")
def rbd_image_delete(pool_name: str, image_name: str, dry_run: bool = False,
                     target: Optional[str] = None) -> dict:
    """[WRITE][risk=high] Delete an RBD image and ALL its data. Irreversible.

    Pass dry_run=True to preview.

    Args:
        pool_name: Pool the image lives in.
        image_name: Image to delete (from rbd_ls).
        dry_run: If True, preview without deleting.
        target: Ceph target name from config; omit for the default.
    """
    conn = _get_connection(target)
    if dry_run:
        return {"dryRun": True, "wouldDelete": {"imageSpec": f"{pool_name}/{image_name}"}}
    return ops.delete_image(conn, pool_name, image_name)


@mcp.tool()
@governed_tool(risk_level="high")
@tool_errors("dict")
def rbd_snapshot_delete(pool_name: str, image_name: str, snap_name: str,
                        dry_run: bool = False, target: Optional[str] = None) -> dict:
    """[WRITE][risk=high] Delete an RBD image snapshot. Irreversible.

    Pass dry_run=True to preview.

    Args:
        pool_name: Pool the image lives in.
        image_name: Image the snapshot belongs to.
        snap_name: Snapshot to delete.
        dry_run: If True, preview without deleting.
        target: Ceph target name from config; omit for the default.
    """
    conn = _get_connection(target)
    if dry_run:
        return {"dryRun": True,
                "wouldDelete": {"imageSpec": f"{pool_name}/{image_name}", "snapName": snap_name}}
    return ops.delete_snapshot(conn, pool_name, image_name, snap_name)
