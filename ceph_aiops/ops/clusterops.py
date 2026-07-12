"""Cluster-level ops: mon/mgr status, slow ops, capacity forecast, recovery throttle.

These are the questions operators ask *after* the health check has told them
something is wrong: is quorum intact, which mgr is driving, which OSDs are
sitting on blocked requests, when will I hit nearfull, and — the #1 tuning ask —
turn recovery/backfill down so client IO stops suffering.

Reads are resilient by design: a failing call degrades to an ``error`` field
rather than a raised traceback (a health probe must survive the thing it probes).
``throttle_recovery`` is a reversible write: it captures the prior config values
into ``priorState`` so the harness can record a faithful undo.
"""

from __future__ import annotations

from typing import Any

from ceph_aiops.ops._util import as_obj, s

_HEALTH_FULL = "/api/health/full"
_MONITOR = "/api/monitor"
_CLUSTER_CONF = "/api/cluster_conf"
_NEARFULL_RATIO = 0.85


def _names(items: Any) -> list[str]:
    """Pull a list of mon names out of dicts-or-scalars, sanitized."""
    out: list[str] = []
    for it in items or []:
        if isinstance(it, dict):
            name = it.get("name")
            if name is not None:
                out.append(s(name))
        elif it is not None:
            out.append(s(it))
    return out


def _summary_msg(check: dict) -> str:
    msg = (check.get("summary") or {}).get("message") if isinstance(check, dict) else ""
    return s(msg or "")


def _checks(raw: dict) -> dict:
    health = as_obj(raw.get("health")) or raw
    checks = health.get("checks")
    return checks if isinstance(checks, dict) else {}


def mon_status(conn: Any) -> dict:
    """[READ] Monitor quorum: which mons are in/out of quorum + the monmap epoch."""
    try:
        raw = as_obj(conn.get(_MONITOR))
    except Exception as exc:  # noqa: BLE001 — report as partial
        return {"error": s(exc, 200)}

    status = as_obj(raw.get("mon_status"))
    monmap = as_obj(status.get("monmap"))
    return {
        "inQuorum": _names(raw.get("in_quorum")),
        "outOfQuorum": _names(raw.get("out_quorum")),
        "epoch": monmap.get("epoch") or status.get("election_epoch"),
    }


def mgr_status(conn: Any) -> dict:
    """[READ] Which mgr is active, its standbys, and the enabled mgr modules."""
    try:
        raw = as_obj(conn.get(_HEALTH_FULL))
    except Exception as exc:  # noqa: BLE001 — report as partial
        return {"error": s(exc, 200)}

    mgr_map = as_obj(raw.get("mgr_map"))
    return {
        "active": s(mgr_map.get("active_name")),
        "standbys": _names(mgr_map.get("standbys")),
        "modules": [s(m) for m in (mgr_map.get("modules") or []) if m is not None],
    }


def slow_ops(conn: Any) -> dict:
    """[READ] Blocked/slow requests decoded from the SLOW_OPS health check.

    Returns a per-OSD blocked list plus the check summary; ``count`` is 0 and the
    list empty when no SLOW_OPS check is active.
    """
    try:
        raw = as_obj(conn.get(_HEALTH_FULL))
    except Exception as exc:  # noqa: BLE001 — report as partial
        return {"error": s(exc, 200)}

    check = as_obj(_checks(raw).get("SLOW_OPS"))
    if not check:
        return {"count": 0, "byOsd": []}

    by_osd: list[dict] = []
    for item in (check.get("detail") or []):
        if not isinstance(item, dict):
            continue
        by_osd.append({
            "osd": item.get("osd"),
            "blockedMs": item.get("blockedMs") or item.get("blocked_ms"),
        })
    reported = (check.get("summary") or {}).get("count")
    count = reported if isinstance(reported, int) else len(by_osd)
    return {"count": count, "byOsd": by_osd, "summary": _summary_msg(check)}


def capacity_forecast(conn: Any, daily_growth_bytes: int | None = None) -> dict:
    """[READ] Cluster capacity + a deterministic days-to-nearfull projection.

    Nearfull is ``_NEARFULL_RATIO`` (0.85) of total. With no positive growth rate
    the projection is ``insufficient-data`` (``daysToNearfull=None``); otherwise it
    is a pure arithmetic extrapolation — no clock, no randomness.
    """
    try:
        raw = as_obj(conn.get(_HEALTH_FULL))
    except Exception as exc:  # noqa: BLE001 — report as partial
        return {"error": s(exc, 200)}

    stats = as_obj(as_obj(raw.get("df")).get("stats"))
    total = stats.get("total_bytes")
    used = stats.get("total_used_raw_bytes")
    avail = stats.get("total_avail_bytes")
    used_pct = round(100.0 * used / total, 1) if total and used is not None else None

    days: int | None = None
    forecast = "insufficient-data"
    if daily_growth_bytes and daily_growth_bytes > 0 and total and used is not None:
        headroom = _NEARFULL_RATIO * total - used
        days = max(0, int(headroom / daily_growth_bytes))
        forecast = "ok"

    return {
        "usedBytes": used,
        "totalBytes": total,
        "availBytes": avail,
        "usedPercent": used_pct,
        "daysToNearfull": days,
        "forecast": forecast,
    }


# ── writes ───────────────────────────────────────────────────────────────


def _conf_value(conn: Any, name: str) -> Any:
    """Best-effort current value of an osd config option; None if unreadable."""
    try:
        raw = as_obj(conn.get(f"{_CLUSTER_CONF}/{name}"))
    except Exception:  # noqa: BLE001 — prior state is best-effort
        return None
    value = raw.get("value")
    if isinstance(value, list):
        first = value[0] if value else {}
        return first.get("value") if isinstance(first, dict) else first
    return value


def throttle_recovery(
    conn: Any,
    max_backfills: int | None = None,
    recovery_max_active: int | None = None,
) -> dict:
    """[WRITE][medium] Tune osd_max_backfills / osd_recovery_max_active down (or up).

    Captures the prior values into ``priorState`` (best-effort — a config it can't
    read records None) so the harness can restore them. One POST per provided
    setting; a setting left as None is untouched.
    """
    prior_mb = _conf_value(conn, "osd_max_backfills")
    prior_rma = _conf_value(conn, "osd_recovery_max_active")

    applied: dict[str, str] = {}
    if max_backfills is not None:
        conn.post(_CLUSTER_CONF, json={"name": "osd_max_backfills", "value": str(max_backfills)})
        applied["osd_max_backfills"] = str(max_backfills)
    if recovery_max_active is not None:
        conn.post(
            _CLUSTER_CONF,
            json={"name": "osd_recovery_max_active", "value": str(recovery_max_active)},
        )
        applied["osd_recovery_max_active"] = str(recovery_max_active)

    return {
        "action": "throttle_recovery",
        "applied": applied,
        "priorState": {
            "osd_max_backfills": prior_mb,
            "osd_recovery_max_active": prior_rma,
        },
    }
