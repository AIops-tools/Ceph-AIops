"""Cluster health + status (read-only) — the flagship RCA surface.

``cluster_health`` is the tool operators reach for first: it takes Ceph's raw
``health detail`` check codes (PG_DEGRADED, OSD_NEARFULL, …) — which are cryptic
on their own — and folds each active check into a plain-language **cause +
suggested action**. This is the single highest-leverage read in the tool and the
thing neither existing Ceph MCP addresses.

Resilient by design: a failing call degrades to an ``error`` field, never a
raised traceback (a health probe must survive the thing it probes).
"""

from __future__ import annotations

from typing import Any

from ceph_aiops.ops._util import as_obj, opt_s, s

_HEALTH_FULL = "/api/health/full"
_HEALTH_MINIMAL = "/api/health/minimal"

# code -> (cause, suggested action). Covers the checks operators most often
# ask "why + what do I do" about (community research: HEALTH_WARN decoding is
# the #1 pain). Unknown codes fall back to the check's own summary message.
_CHECK_RCA: dict[str, tuple[str, str]] = {
    "PG_DEGRADED": (
        "Some object replicas are missing — usually an OSD is down/out and PGs "
        "are re-replicating.",
        "Find the down OSD (osd_tree) and bring it back or let recovery finish; "
        "watch osd_max_backfills if recovery is slow.",
    ),
    "PG_AVAILABILITY": (
        "One or more PGs are inactive (peering/incomplete/down) — reads/writes to "
        "them block.",
        "Inspect stuck PGs (pg_dump_stuck) and the implicated OSDs; a down OSD "
        "breaching min_size is the usual cause.",
    ),
    "OSD_NEARFULL": (
        "An OSD crossed nearfull_ratio (default 0.85) — it will soon refuse writes.",
        "Rebalance/reweight the full OSDs (osd_reweight), add capacity, or raise "
        "the ratio temporarily; check osd_df for the offenders.",
    ),
    "OSD_FULL": (
        "An OSD hit full_ratio — the cluster has stopped accepting writes to "
        "protect data.",
        "Free space or reweight immediately (osd_reweight down the full OSD); "
        "deleting snapshots/old data is faster than adding disks.",
    ),
    "OSD_BACKFILLFULL": (
        "An OSD is too full to accept backfill — recovery/rebalance is stalled.",
        "Reweight or add capacity so backfill can proceed; check osd_df VAR for "
        "imbalance.",
    ),
    "OSD_DOWN": (
        "One or more OSDs are marked down — capacity and redundancy are reduced.",
        "Check the OSD host/daemon; if it won't return, plan a drain (osd out) "
        "and replacement.",
    ),
    "MON_DOWN": (
        "A monitor is out of quorum — cluster management/consensus is degraded.",
        "Check the mon host, ports 3300/6789, and clock skew; restore quorum "
        "before other repairs.",
    ),
    "MGR_DOWN": (
        "No active mgr — dashboards/metrics/this API depend on it.",
        "Restart/fail over the mgr; a standby should take over automatically.",
    ),
    "POOL_NEARFULL": (
        "A pool is near its quota or the backing OSDs are nearfull.",
        "Raise the pool quota (set_pool_quota), free objects, or add OSD capacity.",
    ),
    "PG_NOT_SCRUBBED": (
        "PGs are overdue for a shallow scrub — often benign on busy/homelab nodes.",
        "Kick the overdue PGs (trigger_scrub) or widen the scrub window; not "
        "urgent unless persistent.",
    ),
    "PG_NOT_DEEP_SCRUBBED": (
        "PGs are overdue for a deep scrub (data-integrity check).",
        "Trigger a deep scrub (trigger_deep_scrub) or tune the deep-scrub interval; "
        "commonly a false alarm under load.",
    ),
    "SLOW_OPS": (
        "OSD/mon requests are blocked beyond the threshold — congestion, a slow "
        "disk, or recovery pressure.",
        "Identify the OSD (slow_ops), check its disk latency (osd_perf); throttle "
        "recovery if it's the cause.",
    ),
    "LARGE_OMAP_OBJECTS": (
        "A large omap object exists — usually an unsharded RGW bucket index.",
        "Reshard the offending bucket index; check rgw_status for the bucket.",
    ),
    "POOL_TOO_MANY_PGS": (
        "Too many PGs per OSD — memory/CPU overhead and slow peering.",
        "Let the PG autoscaler converge or lower pg_num (set_pool_pg_num) carefully.",
    ),
    "POOL_TOO_FEW_PGS": (
        "Too few PGs per OSD — uneven data distribution.",
        "Raise pg_num (set_pool_pg_num) or enable the autoscaler.",
    ),
    "MON_CLOCK_SKEW": (
        "Monitor clocks disagree — can destabilise quorum.",
        "Fix NTP/chrony on the mon hosts.",
    ),
    "RECENT_CRASH": (
        "A daemon crashed recently and the report is unarchived.",
        "Review the crash (ceph crash ls/info) and archive it once understood.",
    ),
}


def _summary(check: dict) -> str:
    msg = (check.get("summary") or {}).get("message") if isinstance(check, dict) else ""
    return opt_s(msg)


def cluster_health(conn: Any) -> dict:
    """[READ] HEALTH status + a plain-language cause/action for each active check."""
    try:
        raw = as_obj(conn.get(_HEALTH_FULL))
    except Exception as exc:  # noqa: BLE001 — report as partial
        return {"error": s(exc, 200)}

    health = raw.get("health") or raw
    status = opt_s(health.get("status") or health.get("overall_status"))
    checks = health.get("checks") or {}
    findings: list[dict] = []
    if isinstance(checks, dict):
        items = checks.items()
    else:  # some versions return a list of {type/severity/summary}
        items = [(c.get("type", ""), c) for c in checks if isinstance(c, dict)]
    _fallback = ("See the check summary.", "Investigate via the related read tool.")
    for code, check in items:
        cause, action = _CHECK_RCA.get(code, (_summary(check) or _fallback[0], _fallback[1]))
        findings.append({
            "check": s(code),
            "severity": opt_s((check or {}).get("severity")),
            "summary": _summary(check),
            "cause": cause,
            "suggestedAction": action,
        })
    return {
        "status": status or "UNKNOWN",
        "healthy": status == "HEALTH_OK",
        "activeChecks": len(findings),
        "findings": findings,
    }


def cluster_status(conn: Any) -> dict:
    """[READ] Compact ``ceph -s``-style summary: mons, osds, pgs, usage, client IO."""
    try:
        raw = as_obj(conn.get(_HEALTH_MINIMAL))
    except Exception as exc:  # noqa: BLE001 — report as partial
        return {"error": s(exc, 200)}

    osd_map = as_obj(raw.get("osd_map"))
    osd = osd_map.get("osds")
    df = as_obj(raw.get("df")).get("stats", {})
    pg_info = as_obj(raw.get("pg_info"))
    pg = pg_info.get("object_stats", {})
    mon = as_obj(raw.get("mon_status"))
    return {
        "status": opt_s((raw.get("health") or {}).get("status")),
        "monsInQuorum": mon.get("num_mons") or mon.get("monmap", {}).get("num_mons"),
        "osdsUp": _count(osd, "up"),
        "osdsIn": _count(osd, "in"),
        "pgsTotal": pg_info.get("pgs_per_osd"),
        "usedBytes": df.get("total_used_raw_bytes"),
        "totalBytes": df.get("total_bytes"),
        "objects": pg.get("num_objects"),
    }


def _count(osds: Any, field: str) -> int | None:
    if not isinstance(osds, list):
        return None
    return sum(1 for o in osds if isinstance(o, dict) and o.get(field))
