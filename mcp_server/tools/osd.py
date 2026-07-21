"""OSD inventory + guarded lifecycle MCP tools (read + writes).

Destructive OSD ops (mark out, purge) are risk=high with a ``dry_run`` preview.
Reweight and mark-in are reversible and record an undo capturing prior state.
"""

from typing import Any, Optional

from ceph_aiops.governance import governed_tool
from ceph_aiops.ops import osd as ops
from mcp_server._shared import _get_connection, mcp, tool_errors


def _flag_undo(params: dict[str, Any], result: Any) -> Optional[dict]:
    """Inverse of set_cluster_flag: toggle the same flag the other way."""
    if not isinstance(result, dict):
        return None
    return {"tool": "cluster_flag_set",
            "params": {"flag": params.get("flag"), "enable": not params.get("enable", True)},
            "note": "Toggle the flag back to its prior state."}


def _reweight_undo(params: dict[str, Any], result: Any) -> Optional[dict]:
    """Inverse of osd_reweight: restore the captured prior weight."""
    if not isinstance(result, dict):
        return None
    prior = (result.get("priorState") or {}).get("weight")
    if prior is None:
        return None
    return {"tool": "osd_reweight",
            "params": {"osd_id": params.get("osd_id"), "weight": prior},
            "note": "Restore the OSD's prior reweight value."}


def _mark_in_undo(params: dict[str, Any], result: Any) -> Optional[dict]:
    """Inverse of mark-in: only offer an undo if the OSD was 'out' before."""
    if not isinstance(result, dict):
        return None
    if (result.get("priorState") or {}).get("in") is False:
        return {"tool": "osd_mark_out", "params": {"osd_id": params.get("osd_id")},
                "note": "OSD was out before; mark it out again."}
    return None


# ── reads ────────────────────────────────────────────────────────────────


@mcp.tool()
@governed_tool(risk_level="low")
@tool_errors("dict")
def osd_tree(target: Optional[str] = None) -> list:
    """[READ] All OSDs: up/in state, weight, host, device class.

    Args:
        target: Ceph target name from config; omit for the default.
    """
    return ops.osd_tree(_get_connection(target))


@mcp.tool()
@governed_tool(risk_level="low")
@tool_errors("dict")
def osd_df(target: Optional[str] = None) -> list:
    """[READ] Per-OSD utilisation (most-full first) with near/backfill-full flags.

    Args:
        target: Ceph target name from config; omit for the default.
    """
    return ops.osd_df(_get_connection(target))


@mcp.tool()
@governed_tool(risk_level="low")
@tool_errors("dict")
def osd_perf(target: Optional[str] = None) -> list:
    """[READ] Per-OSD commit/apply latency (slowest first) — find the slow disk.

    Args:
        target: Ceph target name from config; omit for the default.
    """
    return ops.osd_perf(_get_connection(target))


# ── writes ───────────────────────────────────────────────────────────────


@mcp.tool()
@governed_tool(risk_level="medium", undo=_flag_undo)
@tool_errors("dict")
def cluster_flag_set(flag: str, enable: bool = True, target: Optional[str] = None) -> dict:
    """[WRITE][risk=medium] Set/unset a cluster flag (noout/nobackfill/norecover/noscrub…).

    Heavily used before maintenance (e.g. set noout while rebooting a host).
    Reversible — records an undo that toggles the flag back.

    Args:
        flag: Flag name, e.g. "noout", "noscrub", "nobackfill", "norecover".
        enable: True to set the flag, False to clear it.
        target: Ceph target name from config; omit for the default.
    """
    return ops.set_cluster_flag(_get_connection(target), flag, enable=enable)


@mcp.tool()
@governed_tool(risk_level="medium", undo=_reweight_undo)
@tool_errors("dict")
def osd_reweight(
    osd_id: int, weight: float, dry_run: bool = False, target: Optional[str] = None
) -> dict:
    """[WRITE][risk=medium] Set an OSD's reweight (0.0 drains it). Reversible → prior weight.

    Pass dry_run=True to preview the current→target weight without applying it.

    Args:
        osd_id: Numeric OSD id (from osd_tree).
        weight: New reweight 0.0–1.0 (0.0 = full drain).
        dry_run: If True, read the current weight and preview without reweighting.
        target: Ceph target name from config; omit for the default.
    """
    conn = _get_connection(target)
    if dry_run:
        return {"dryRun": True, "wouldReweight": ops.preview_reweight(conn, osd_id, weight)}
    return ops.osd_reweight(conn, osd_id, weight)


@mcp.tool()
@governed_tool(risk_level="medium", undo=_mark_in_undo)
@tool_errors("dict")
def osd_mark_in(osd_id: int, target: Optional[str] = None) -> dict:
    """[WRITE][risk=medium] Mark an OSD 'in' (return a drained/repaired OSD to service).

    Args:
        osd_id: Numeric OSD id (from osd_tree).
        target: Ceph target name from config; omit for the default.
    """
    return ops.mark_osd(_get_connection(target), osd_id, "in")


@mcp.tool()
@governed_tool(risk_level="high")
@tool_errors("dict")
def osd_mark_out(osd_id: int, dry_run: bool = False, target: Optional[str] = None) -> dict:
    """[WRITE][risk=high] Mark an OSD 'out' — drains its data (recovery storm / min_size risk).

    Destructive to redundancy — pass dry_run=True to preview.

    Args:
        osd_id: Numeric OSD id (from osd_tree).
        dry_run: If True, preview without marking out.
        target: Ceph target name from config; omit for the default.
    """
    conn = _get_connection(target)
    if dry_run:
        return {"dryRun": True, "wouldMarkOut": {"osdId": osd_id}}
    return ops.mark_osd(conn, osd_id, "out")


@mcp.tool()
@governed_tool(risk_level="high")
@tool_errors("dict")
def osd_purge(osd_id: int, dry_run: bool = False, target: Optional[str] = None) -> dict:
    """[WRITE][risk=high] Purge an OSD (destroy + crush rm + auth del). Irreversible.

    Pass dry_run=True to preview.
    Drain first (osd_reweight 0 → mark out → wait active+clean) before purging.

    Args:
        osd_id: Numeric OSD id (from osd_tree).
        dry_run: If True, preview without purging.
        target: Ceph target name from config; omit for the default.
    """
    conn = _get_connection(target)
    if dry_run:
        return {"dryRun": True, "wouldPurge": {"osdId": osd_id}}
    return ops.osd_purge(conn, osd_id)
