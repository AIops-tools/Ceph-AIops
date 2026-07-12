"""CephFS + RGW status MCP tools (read-only)."""

from typing import Optional

from ceph_aiops.governance import governed_tool
from ceph_aiops.ops import filesystem as ops
from mcp_server._shared import _get_connection, mcp, tool_errors


@mcp.tool()
@governed_tool(risk_level="low")
@tool_errors("dict")
def cephfs_status(target: Optional[str] = None) -> dict:
    """[READ] Per-filesystem MDS ranks with the behind-on-trimming backpressure signal.

    Surfaces each MDS rank's state, the client/standby counts, and the notorious
    "MDS behind on trimming" flag that quietly precedes metadata stalls.

    Args:
        target: Ceph target name from config; omit for the default.
    """
    return ops.cephfs_status(_get_connection(target))


@mcp.tool()
@governed_tool(risk_level="low")
@tool_errors("dict")
def rgw_status(target: Optional[str] = None) -> dict:
    """[READ] RGW daemons plus a large-omap (unsharded bucket index) scan.

    Flags buckets whose index is unsharded/oversized — the LARGE_OMAP signal and
    the top RGW performance foot-gun. Partial-safe if the bucket call fails.

    Args:
        target: Ceph target name from config; omit for the default.
    """
    return ops.rgw_status(_get_connection(target))
