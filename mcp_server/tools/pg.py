"""Placement Group health + scrub MCP tools (read + low-risk writes).

Reads decode the pgmap/health checks into a state histogram, a stuck-PG list,
and an overdue-scrub view. The scrub writes schedule work rather than mutate
data, so no undo/dry-run is needed — but they are still writes, so they carry
risk=medium and are tagged ``[WRITE]`` like every other state-changing tool.
"""

from typing import Optional

from ceph_aiops.governance import governed_tool
from ceph_aiops.ops import pg as ops
from mcp_server._shared import _get_connection, mcp, tool_errors

# ── reads ────────────────────────────────────────────────────────────────


@mcp.tool()
@governed_tool(risk_level="low")
@tool_errors("dict")
def pg_summary(target: Optional[str] = None, limit: int = 200) -> dict:
    """[READ] PG state histogram + every PG that is not active+clean.

    Call this to answer "are my PGs healthy?" — it counts PGs by state and lists
    the ones needing attention. ``unhealthyCount`` is the true total; the
    ``unhealthy`` list is capped at ``limit`` and sets ``truncated: true`` when
    there were more. Re-run with a higher limit rather than treating a truncated
    result as complete.

    Args:
        target: Ceph target name from config; omit for the default.
        limit: Maximum unhealthy PG rows to return. Default 200.
    """
    return ops.pg_summary(_get_connection(target), limit=limit)


@mcp.tool()
@governed_tool(risk_level="low")
@tool_errors("dict")
def pg_dump_stuck(target: Optional[str] = None, limit: int = 200) -> dict:
    """[READ] Stuck PGs (inactive/unclean/stale/undersized/degraded) with implicated OSDs.

    Use this when a PG is not recovering — it surfaces the OSD ids to investigate.
    Returns ``{"stuck": [...], "returned": N, "limit": L, "truncated": bool}``;
    when ``truncated`` is true there are more stuck PGs than were returned.

    Args:
        target: Ceph target name from config; omit for the default.
        limit: Maximum stuck-PG rows to return. Default 200.
    """
    return ops.pg_dump_stuck(_get_connection(target), limit=limit)


@mcp.tool()
@governed_tool(risk_level="low")
@tool_errors("dict")
def scrub_status(target: Optional[str] = None) -> dict:
    """[READ] PGs overdue for shallow / deep scrub (decodes PG_NOT(_DEEP)_SCRUBBED).

    Args:
        target: Ceph target name from config; omit for the default.
    """
    return ops.scrub_status(_get_connection(target))


# ── writes ───────────────────────────────────────────────────────────────


@mcp.tool()
@governed_tool(risk_level="medium")
@tool_errors("dict")
def trigger_scrub(pgid: str, target: Optional[str] = None) -> dict:
    """[WRITE][risk=medium] Schedule a shallow scrub on a PG (clears a PG_NOT_SCRUBBED warn).

    Args:
        pgid: Placement group id, e.g. "2.1a" (from pg_summary / scrub_status).
        target: Ceph target name from config; omit for the default.
    """
    return ops.trigger_scrub(_get_connection(target), pgid)


@mcp.tool()
@governed_tool(risk_level="medium")
@tool_errors("dict")
def trigger_deep_scrub(pgid: str, target: Optional[str] = None) -> dict:
    """[WRITE][risk=medium] Schedule a deep (data-integrity) scrub on a PG.

    Args:
        pgid: Placement group id, e.g. "2.1a" (from pg_summary / scrub_status).
        target: Ceph target name from config; omit for the default.
    """
    return ops.trigger_deep_scrub(_get_connection(target), pgid)
