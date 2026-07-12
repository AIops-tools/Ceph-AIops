"""``ceph-aiops overview`` — one-shot fleet health."""

from __future__ import annotations

import json

from ceph_aiops.cli._common import TargetOption, cli_errors, console, get_connection


@cli_errors
def overview_cmd(target: TargetOption = None) -> None:
    """One-shot cluster summary: HEALTH status + active checks + OSD up/in counts."""
    from ceph_aiops.ops import overview as ops

    conn, _ = get_connection(target)
    console.print_json(json.dumps(ops.fleet_overview(conn)))
