"""``ceph-aiops osd`` — OSD reads + guarded lifecycle writes."""

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
    dry_run_print,
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
    from ceph_aiops.ops import osd as ops

    if dry_run:
        dry_run_print(operation="osd_reweight",
                      api_call=f"POST /api/osd/{osd_id}/reweight",
                      parameters={"weight": weight})
        return
    conn, _ = get_connection(target)
    console.print_json(json.dumps(ops.osd_reweight(conn, osd_id, weight)))


@osd_app.command("out")
@cli_errors
def osd_out(osd_id: IdArg, target: TargetOption = None, dry_run: DryRunOption = False) -> None:
    """Mark an OSD out — drains its data (dry-run + double confirm)."""
    from ceph_aiops.ops import osd as ops

    if dry_run:
        dry_run_print(operation="mark_osd_out", api_call=f"POST /api/osd/{osd_id}/mark",
                      parameters={"action": "out"})
        return
    double_confirm("mark out OSD", str(osd_id))
    conn, _ = get_connection(target)
    console.print_json(json.dumps(ops.mark_osd(conn, osd_id, "out")))


@osd_app.command("purge")
@cli_errors
def osd_purge(osd_id: IdArg, target: TargetOption = None, dry_run: DryRunOption = False) -> None:
    """Purge an OSD — irreversible (dry-run + double confirm)."""
    from ceph_aiops.ops import osd as ops

    if dry_run:
        dry_run_print(operation="osd_purge", api_call=f"DELETE /api/osd/{osd_id}")
        return
    double_confirm("purge OSD", str(osd_id))
    conn, _ = get_connection(target)
    console.print_json(json.dumps(ops.osd_purge(conn, osd_id)))
