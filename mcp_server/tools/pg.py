"""Placement Group health + scrub MCP tools (read + low-risk writes).

Reads decode the pgmap/health checks into a state histogram, a stuck-PG list,
and an overdue-scrub view. The scrub writes are risk=low — they schedule work
rather than mutate data — so no undo/dry-run is needed.
"""

from typing import Optional

from ceph_aiops.governance import governed_tool
from ceph_aiops.ops import pg as ops
from mcp_server._shared import _get_connection, mcp, tool_errors

# ── reads ────────────────────────────────────────────────────────────────


@mcp.tool()
@governed_tool(risk_level="low")
@tool_errors("dict")
def pg_summary(target: Optional[str] = None) -> dict:
    """[READ] PG state histogram + every PG that is not active+clean.

    Call this to answer "are my PGs healthy?" — it counts PGs by state and lists
    the ones needing attention.

    Args:
        target: Ceph target name from config; omit for the default.
    """
    return ops.pg_summary(_get_connection(target))


@mcp.tool()
@governed_tool(risk_level="low")
@tool_errors("dict")
def pg_dump_stuck(target: Optional[str] = None) -> list:
    """[READ] Stuck PGs (inactive/unclean/stale/undersized/degraded) with implicated OSDs.

    Use this when a PG is not recovering — it surfaces the OSD ids to investigate.

    Args:
        target: Ceph target name from config; omit for the default.
    """
    return ops.pg_dump_stuck(_get_connection(target))


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
@governed_tool(risk_level="low")
@tool_errors("dict")
def trigger_scrub(pgid: str, target: Optional[str] = None) -> dict:
    """[WRITE][risk=low] Schedule a shallow scrub on a PG (clears a PG_NOT_SCRUBBED warn).

    Args:
        pgid: Placement group id, e.g. "2.1a" (from pg_summary / scrub_status).
        target: Ceph target name from config; omit for the default.
    """
    return ops.trigger_scrub(_get_connection(target), pgid)


@mcp.tool()
@governed_tool(risk_level="low")
@tool_errors("dict")
def trigger_deep_scrub(pgid: str, target: Optional[str] = None) -> dict:
    """[WRITE][risk=low] Schedule a deep (data-integrity) scrub on a PG.

    Args:
        pgid: Placement group id, e.g. "2.1a" (from pg_summary / scrub_status).
        target: Ceph target name from config; omit for the default.
    """
    return ops.trigger_deep_scrub(_get_connection(target), pgid)
