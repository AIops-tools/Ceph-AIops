"""One-shot Ceph cluster overview (read-only).

Folds health, OSD counts, and pool count into a single summary an agent can call
first. Resilient — a failing sub-call degrades to a partial summary with an
``errors`` list rather than a raised traceback.
"""

from __future__ import annotations

from typing import Any

from ceph_aiops.ops import health as h
from ceph_aiops.ops import osd as o


def fleet_overview(conn: Any) -> dict:
    """[READ] Cluster summary: HEALTH status + active checks, OSD up/in, pool count."""
    errors: list[str] = []

    def _safe(fn: Any, label: str, default: Any) -> Any:
        try:
            return fn(conn)
        except Exception as exc:  # noqa: BLE001 — collect, keep going
            errors.append(f"{label}: {str(exc)[:120]}")
            return default

    health = _safe(h.cluster_health, "health", {})
    osds = _safe(o.osd_tree, "osd", [])

    return {
        "status": health.get("status", "UNKNOWN"),
        "activeChecks": health.get("activeChecks", 0),
        "topFindings": [f["check"] for f in health.get("findings", [])[:5]],
        "osdsTotal": len(osds),
        "osdsUp": sum(1 for x in osds if x.get("up")),
        "osdsIn": sum(1 for x in osds if x.get("in")),
        "errors": errors,
    }
