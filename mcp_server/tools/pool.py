"""Pool inventory + guarded lifecycle MCP tools (read + writes).

The footgun writes — changing replica ``size`` (mass data movement) and deleting
a pool (destroys all data) — are risk=high with a ``dry_run`` preview. Quota /
pg_num / autoscale / size are reversible and record an undo capturing prior state.
"""

from typing import Any, Optional

from ceph_aiops.governance import governed_tool
from ceph_aiops.ops import pool as ops
from mcp_server._shared import _get_connection, mcp, tool_errors


def _quota_undo(params: dict[str, Any], result: Any) -> Optional[dict]:
    """Inverse of set_pool_quota: restore the captured prior quota."""
    if not isinstance(result, dict):
        return None
    prior = result.get("priorState") or {}
    return {"tool": "set_pool_quota",
            "params": {"pool_name": params.get("pool_name"),
                       "max_bytes": prior.get("quotaMaxBytes"),
                       "max_objects": prior.get("quotaMaxObjects")},
            "note": "Restore the pool's prior quota."}


def _pgnum_undo(params: dict[str, Any], result: Any) -> Optional[dict]:
    """Inverse of set_pool_pg_num: restore the captured prior pg_num."""
    if not isinstance(result, dict):
        return None
    prior = (result.get("priorState") or {}).get("pgNum")
    if prior is None:
        return None
    return {"tool": "set_pool_pg_num",
            "params": {"pool_name": params.get("pool_name"), "pg_num": prior},
            "note": "Restore the pool's prior pg_num."}


def _autoscale_undo(params: dict[str, Any], result: Any) -> Optional[dict]:
    """Inverse of set_pool_autoscale: restore the captured prior mode."""
    if not isinstance(result, dict):
        return None
    prior = (result.get("priorState") or {}).get("autoscaleMode")
    if not prior:
        return None
    return {"tool": "set_pool_autoscale",
            "params": {"pool_name": params.get("pool_name"), "mode": prior},
            "note": "Restore the pool's prior autoscale mode."}


def _size_undo(params: dict[str, Any], result: Any) -> Optional[dict]:
    """Inverse of set_pool_size: restore the captured prior replica size."""
    if not isinstance(result, dict):
        return None
    prior = (result.get("priorState") or {}).get("size")
    if prior is None:
        return None
    return {"tool": "set_pool_size",
            "params": {"pool_name": params.get("pool_name"), "size": prior},
            "note": "Restore the pool's prior replica size (re-triggers data movement)."}


# ── reads ────────────────────────────────────────────────────────────────


@mcp.tool()
@governed_tool(risk_level="low")
@tool_errors("dict")
def pool_ls(target: Optional[str] = None) -> list:
    """[READ] All pools: size/min_size, pg_num, autoscale mode, application, quota.

    Args:
        target: Ceph target name from config; omit for the default.
    """
    return ops.list_pools(_get_connection(target))


@mcp.tool()
@governed_tool(risk_level="low")
@tool_errors("dict")
def pool_df(target: Optional[str] = None) -> list:
    """[READ] Per-pool usage: used/avail bytes, percent, objects, usable capacity.

    Args:
        target: Ceph target name from config; omit for the default.
    """
    return ops.pool_df(_get_connection(target))


# ── writes ───────────────────────────────────────────────────────────────


@mcp.tool()
@governed_tool(risk_level="medium", undo=_quota_undo)
@tool_errors("dict")
def set_pool_quota(pool_name: str, max_bytes: Optional[int] = None,
                   max_objects: Optional[int] = None, target: Optional[str] = None) -> dict:
    """[WRITE][risk=medium] Set a pool's byte/object quota. Reversible → prior quota.

    Args:
        pool_name: Pool name (from pool_ls).
        max_bytes: New byte quota (0 clears it); omit to leave unchanged.
        max_objects: New object quota (0 clears it); omit to leave unchanged.
        target: Ceph target name from config; omit for the default.
    """
    return ops.set_pool_quota(_get_connection(target), pool_name,
                              max_bytes=max_bytes, max_objects=max_objects)


@mcp.tool()
@governed_tool(risk_level="medium", undo=_pgnum_undo)
@tool_errors("dict")
def set_pool_pg_num(pool_name: str, pg_num: int, target: Optional[str] = None) -> dict:
    """[WRITE][risk=medium] Set a pool's pg_num. Reversible → prior pg_num.

    Args:
        pool_name: Pool name (from pool_ls).
        pg_num: New placement-group count.
        target: Ceph target name from config; omit for the default.
    """
    return ops.set_pool_pg_num(_get_connection(target), pool_name, pg_num)


@mcp.tool()
@governed_tool(risk_level="medium", undo=_autoscale_undo)
@tool_errors("dict")
def set_pool_autoscale(pool_name: str, mode: str, target: Optional[str] = None) -> dict:
    """[WRITE][risk=medium] Set a pool's PG autoscale mode (on/off/warn). Reversible → prior.

    Args:
        pool_name: Pool name (from pool_ls).
        mode: Autoscale mode: "on", "off", or "warn".
        target: Ceph target name from config; omit for the default.
    """
    return ops.set_pool_autoscale(_get_connection(target), pool_name, mode)


@mcp.tool()
@governed_tool(risk_level="medium")
@tool_errors("dict")
def pool_create(pool_name: str, pg_num: int = 32, size: int = 3,
                application: str = "rbd", target: Optional[str] = None) -> dict:
    """[WRITE][risk=medium] Create a replicated pool.

    Args:
        pool_name: Name for the new pool.
        pg_num: Initial placement-group count (default 32).
        size: Replica count (default 3).
        application: Pool application tag: "rbd", "cephfs", or "rgw" (default "rbd").
        target: Ceph target name from config; omit for the default.
    """
    return ops.create_pool(_get_connection(target), pool_name,
                           pg_num=pg_num, size=size, application=application)


@mcp.tool()
@governed_tool(risk_level="high", undo=_size_undo)
@tool_errors("dict")
def set_pool_size(pool_name: str, size: int, dry_run: bool = False,
                  target: Optional[str] = None) -> dict:
    """[WRITE][risk=high] Set a pool's replica size — forces mass data movement on a live pool.

    Pass dry_run=True to preview. Requires an approver (set CEPH_AUDIT_APPROVED_BY)
    under the graduated-autonomy policy. Reversible → prior size.

    Args:
        pool_name: Pool name (from pool_ls).
        size: New replica count.
        dry_run: If True, preview without changing the size.
        target: Ceph target name from config; omit for the default.
    """
    conn = _get_connection(target)
    if dry_run:
        return {"dryRun": True, "wouldSetSize": {"poolName": pool_name, "size": size}}
    return ops.set_pool_size(conn, pool_name, size)


@mcp.tool()
@governed_tool(risk_level="high")
@tool_errors("dict")
def pool_delete(pool_name: str, dry_run: bool = False, target: Optional[str] = None) -> dict:
    """[WRITE][risk=high] Delete a pool — destroys all of its data. Irreversible.

    Pass dry_run=True to preview. Requires an approver (CEPH_AUDIT_APPROVED_BY).
    The classic footgun: there is no undo once the data is gone.

    Args:
        pool_name: Pool name (from pool_ls).
        dry_run: If True, preview without deleting.
        target: Ceph target name from config; omit for the default.
    """
    conn = _get_connection(target)
    if dry_run:
        return {"dryRun": True, "wouldDelete": {"poolName": pool_name}}
    return ops.delete_pool(conn, pool_name)
