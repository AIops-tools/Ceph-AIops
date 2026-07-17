"""MCP server wrapping ceph-aiops operations (stdio transport).

Thin adapter layer: each ``@mcp.tool()`` function (in ``mcp_server/tools/``)
delegates to the ``ceph_aiops`` ops package and is wrapped with the
ceph-aiops ``@governed_tool`` harness (audit / budget / undo / risk-tier).

Standalone, self-governed Ceph cluster operations (preview) over the ceph-mgr
Dashboard REST API: health RCA, OSD/PG/pool/RBD/CephFS/RGW, recovery, capacity.

Source: https://github.com/AIops-tools/Ceph-AIops
License: MIT
"""

import logging

from mcp_server._shared import _safe_error, mcp, tool_errors

# Importing the tool modules registers every @mcp.tool() onto the shared
# `mcp` instance. Order does not matter; each module is self-contained.
from mcp_server.tools import (  # noqa: F401 — side effects
    clusterops,
    filesystem,
    health,
    osd,
    pg,
    pool,
    rbd,
    undo,
)

__all__ = ["mcp", "main", "_safe_error", "tool_errors"]


def main() -> None:
    """Run the MCP server over stdio."""
    logging.basicConfig(level=logging.INFO)
    mcp.run(transport="stdio")
