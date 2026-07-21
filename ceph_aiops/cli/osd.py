"""``ceph-aiops osd`` — OSD reads + guarded lifecycle writes.

Real writes (reweight, mark-out, purge) are delegated to the
``@governed_tool``-wrapped functions in ``mcp_server.tools.osd`` so that CLI
writes are audited + undo-recorded on the SAME governance path as the MCP tools
(the CLI keeps its human double-confirm on top).

``--dry-run`` goes through that same governed twin with ``dry_run=True``, so a
preview runs every guard the real write would and lands its own audit row; the
result is rendered into the human DRY-RUN banner rather than dumped as JSON. A
refused preview prints the refusal and exits non-zero instead of a green banner
for a call that is about to be rejected.
"""

from __future__ import annotations

import json
from typing import Annotated

import typer

from ceph_aiops.cli._common import (
    DryRunOption,
    TargetOption,
    cli_errors,
    console,
    double_confirm,
    dry_run_preview,
    get_connection,
)

osd_app = typer.Typer(
    name="osd",
    help="OSDs: tree/df/perf, reweight, mark in/out, purge.",
    no_args_is_help=True,
)

IdArg = Annotated[int, typer.Argument(help="Numeric OSD id (from 'osd tree')")]


@osd_app.command("tree")
@cli_errors
def osd_tree(target: TargetOption = None) -> None:
    """List OSDs (up/in, weight, host, device class)."""
    from ceph_aiops.ops import osd as ops

    conn, _ = get_connection(target)
    console.print_json(json.dumps(ops.osd_tree(conn)))


@osd_app.command("df")
@cli_errors
def osd_df(target: TargetOption = None) -> None:
    """Per-OSD utilisation (most-full first)."""
    from ceph_aiops.ops import osd as ops

    conn, _ = get_connection(target)
    console.print_json(json.dumps(ops.osd_df(conn)))


@osd_app.command("reweight")
@cli_errors
def osd_reweight(
    osd_id: IdArg,
    weight: Annotated[float, typer.Argument(help="New reweight 0.0–1.0 (0.0 = drain)")],
    target: TargetOption = None,
    dry_run: DryRunOption = False,
) -> None:
    """Set an OSD's reweight (0.0 drains it)."""
    from mcp_server.tools import osd as gov

    if dry_run:
        # Routed through the governed twin with dry_run=True: the preview reads
        # the current weight through the same guarded path the real write uses,
        # so it lands an audit row and a refusal exits non-zero (not a banner).
        dry_run_preview(
            gov.osd_reweight(osd_id=osd_id, weight=weight, dry_run=True, target=target),
            operation="osd_reweight", api_call=f"POST /api/osd/{osd_id}/reweight",
            parameters={"weight": weight})
        return
    console.print_json(json.dumps(gov.osd_reweight(osd_id=osd_id, weight=weight, target=target)))


@osd_app.command("out")
@cli_errors
def osd_out(osd_id: IdArg, target: TargetOption = None, dry_run: DryRunOption = False) -> None:
    """Mark an OSD out — drains its data (dry-run + double confirm)."""
    from mcp_server.tools import osd as gov

    if dry_run:
        dry_run_preview(
            gov.osd_mark_out(osd_id=osd_id, dry_run=True, target=target),
            operation="mark_osd_out", api_call=f"POST /api/osd/{osd_id}/mark",
            parameters={"action": "out"})
        return
    double_confirm("mark out OSD", str(osd_id))
    console.print_json(json.dumps(gov.osd_mark_out(osd_id=osd_id, target=target)))


@osd_app.command("purge")
@cli_errors
def osd_purge(osd_id: IdArg, target: TargetOption = None, dry_run: DryRunOption = False) -> None:
    """Purge an OSD — irreversible (dry-run + double confirm)."""
    from mcp_server.tools import osd as gov

    if dry_run:
        dry_run_preview(
            gov.osd_purge(osd_id=osd_id, dry_run=True, target=target),
            operation="osd_purge", api_call=f"DELETE /api/osd/{osd_id}")
        return
    double_confirm("purge OSD", str(osd_id))
    console.print_json(json.dumps(gov.osd_purge(osd_id=osd_id, target=target)))
