"""``ceph-aiops health`` — cluster health + status reads."""

from __future__ import annotations

import json

import typer

from ceph_aiops.cli._common import TargetOption, cli_errors, console, get_connection

health_app = typer.Typer(
    name="health",
    help="Cluster health (RCA) and status.",
    no_args_is_help=True,
)


@health_app.command("detail")
@cli_errors
def health_detail(target: TargetOption = None) -> None:
    """HEALTH status with a cause + suggested action per active check."""
    from ceph_aiops.ops import health as ops

    conn, _ = get_connection(target)
    console.print_json(json.dumps(ops.cluster_health(conn)))


@health_app.command("status")
@cli_errors
def health_status(target: TargetOption = None) -> None:
    """Compact ceph -s summary (mons, OSDs, PGs, usage)."""
    from ceph_aiops.ops import health as ops

    conn, _ = get_connection(target)
    console.print_json(json.dumps(ops.cluster_status(conn)))
