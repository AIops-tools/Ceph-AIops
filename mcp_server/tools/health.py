"""Cluster health + status MCP tools (read-only)."""

from typing import Optional

from ceph_aiops.governance import governed_tool
from ceph_aiops.ops import health as ops
from mcp_server._shared import _get_connection, mcp, tool_errors


@mcp.tool()
@governed_tool(risk_level="low")
@tool_errors("dict")
def cluster_health(target: Optional[str] = None) -> dict:
    """[READ] HEALTH status with a plain-language cause + suggested action per active check.

    Call this first on any "the cluster is unhealthy" question — it decodes raw
    HEALTH_WARN/ERR check codes (PG_DEGRADED, OSD_NEARFULL, …) into what's wrong
    and what to do next.

    Args:
        target: Ceph target name from config; omit for the default.
    """
    return ops.cluster_health(_get_connection(target))


@mcp.tool()
@governed_tool(risk_level="low")
@tool_errors("dict")
def cluster_status(target: Optional[str] = None) -> dict:
    """[READ] Compact ceph -s summary: mons, OSDs up/in, PGs, usage, objects.

    Args:
        target: Ceph target name from config; omit for the default.
    """
    return ops.cluster_status(_get_connection(target))
