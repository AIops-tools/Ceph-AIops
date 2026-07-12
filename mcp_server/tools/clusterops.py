"""Cluster-level MCP tools: mon/mgr status, slow ops, capacity forecast, throttle.

The reads answer the follow-up questions after a health check (is quorum intact,
which mgr is active, which OSDs are blocked, when do I hit nearfull). The one
write — throttle_recovery — is risk=medium and reversible: it records an undo
restoring the prior config values captured in priorState.
"""

from typing import Any, Optional

from ceph_aiops.governance import governed_tool
from ceph_aiops.ops import clusterops as ops
from mcp_server._shared import _get_connection, mcp, tool_errors


def _throttle_undo(params: dict[str, Any], result: Any) -> Optional[dict]:
    """Inverse of throttle_recovery: restore the captured prior config values."""
    if not isinstance(result, dict):
        return None
    prior = result.get("priorState") or {}
    restore: dict[str, Any] = {}
    if prior.get("osd_max_backfills") is not None:
        restore["max_backfills"] = prior.get("osd_max_backfills")
    if prior.get("osd_recovery_max_active") is not None:
        restore["recovery_max_active"] = prior.get("osd_recovery_max_active")
    if not restore:
        return None
    return {"tool": "throttle_recovery", "params": restore,
            "note": "Restore the prior recovery/backfill throttle values."}


# ── reads ────────────────────────────────────────────────────────────────


@mcp.tool()
@governed_tool(risk_level="low")
@tool_errors("dict")
def mon_status(target: Optional[str] = None) -> dict:
    """[READ] Monitor quorum: mons in/out of quorum and the monmap epoch.

    Call this when health flags MON_DOWN or a clock-skew warn — it shows which
    monitors are actually holding quorum.

    Args:
        target: Ceph target name from config; omit for the default.
    """
    return ops.mon_status(_get_connection(target))


@mcp.tool()
@governed_tool(risk_level="low")
@tool_errors("dict")
def mgr_status(target: Optional[str] = None) -> dict:
    """[READ] Active mgr, its standbys, and the enabled mgr modules.

    Args:
        target: Ceph target name from config; omit for the default.
    """
    return ops.mgr_status(_get_connection(target))


@mcp.tool()
@governed_tool(risk_level="low")
@tool_errors("dict")
def slow_ops(target: Optional[str] = None) -> dict:
    """[READ] Blocked/slow requests per OSD (decodes the SLOW_OPS health check).

    Use this to find the OSD sitting on blocked ops when clients report stalls.

    Args:
        target: Ceph target name from config; omit for the default.
    """
    return ops.slow_ops(_get_connection(target))


@mcp.tool()
@governed_tool(risk_level="low")
@tool_errors("dict")
def capacity_forecast(
    daily_growth_bytes: Optional[int] = None, target: Optional[str] = None
) -> dict:
    """[READ] Capacity usage + a deterministic days-to-nearfull projection.

    Pass an observed daily growth in bytes to get daysToNearfull; without it the
    forecast is "insufficient-data". Nearfull is 85% of total. No clock is read —
    the projection is pure arithmetic.

    Args:
        daily_growth_bytes: Observed daily growth in bytes; omit for a usage-only view.
        target: Ceph target name from config; omit for the default.
    """
    return ops.capacity_forecast(_get_connection(target), daily_growth_bytes=daily_growth_bytes)


# ── writes ───────────────────────────────────────────────────────────────


@mcp.tool()
@governed_tool(risk_level="medium", undo=_throttle_undo)
@tool_errors("dict")
def throttle_recovery(
    max_backfills: Optional[int] = None,
    recovery_max_active: Optional[int] = None,
    target: Optional[str] = None,
) -> dict:
    """[WRITE][risk=medium] Tune osd_max_backfills / osd_recovery_max_active.

    The #1 tuning ask: turn recovery/backfill down so a recovery storm stops
    starving client IO (or back up once it's calm). Reversible — records an undo
    restoring the prior config values.

    Args:
        max_backfills: New osd_max_backfills value; omit to leave it unchanged.
        recovery_max_active: New osd_recovery_max_active value; omit to leave unchanged.
        target: Ceph target name from config; omit for the default.
    """
    return ops.throttle_recovery(
        _get_connection(target),
        max_backfills=max_backfills,
        recovery_max_active=recovery_max_active,
    )
