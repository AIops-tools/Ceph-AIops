"""Placement Group health + scrub (read + low-risk writes).

PGs are where Ceph's redundancy actually lives, and "why is a PG stuck?" /
"why is HEALTH_WARN complaining about scrubs?" are the two questions operators
ask most after the cluster-level health check. The reads here fold the raw
pgmap/health checks into a state histogram, a stuck-PG list with the implicated
OSDs, and an overdue-scrub view. The writes kick a shallow or deep scrub — both
low risk (they schedule work, they don't destroy data).

Reads are resilient by design: a failing call degrades to an ``error`` field
rather than a raised traceback (a health probe must survive the thing it probes).
"""

from __future__ import annotations

from typing import Any

from ceph_aiops.ops._util import _seg, as_obj, s

_HEALTH_FULL = "/api/health/full"
_PG = "/api/pg"

_CLEAN = "active+clean"
# States that mean a PG is not serving/redundant as it should be.
_STUCK_MARKERS = ("inactive", "unclean", "stale", "undersized", "degraded",
                  "incomplete", "down", "peering")


def _pg_records(raw: dict) -> list[dict]:
    """Pull the per-PG list out of whichever pgmap shape the mgr returned."""
    pg_info = as_obj(raw.get("pg_info"))
    pgmap = as_obj(raw.get("pgmap"))
    for holder in (pg_info, pgmap, raw):
        for key in ("pgs", "pg_stats", "pg_stats_sum"):
            val = holder.get(key)
            if isinstance(val, list):
                return [p for p in val if isinstance(p, dict)]
    # Some versions expose a {state: count} histogram under statuses/pgs_by_state.
    return []


def _state_of(pg: dict) -> str:
    return s(pg.get("state") or pg.get("state_name") or "")


def _pgid_of(pg: dict) -> str:
    return s(pg.get("pgid") or pg.get("pg_id") or pg.get("id") or "")


def _osds_of(pg: dict) -> list[Any]:
    """Best-effort implicated OSD ids (up/acting set)."""
    for key in ("up", "acting", "osds", "blocked_by"):
        val = pg.get(key)
        if isinstance(val, list) and val:
            return list(val)
    return []


def pg_summary(conn: Any) -> dict:
    """[READ] PG state histogram + the PGs that are not active+clean."""
    try:
        raw = as_obj(conn.get(_HEALTH_FULL))
    except Exception as exc:  # noqa: BLE001 — report as partial
        return {"error": s(exc, 200)}

    pgs = _pg_records(raw)
    states: dict[str, int] = {}
    unhealthy: list[dict] = []
    for pg in pgs:
        state = _state_of(pg)
        if state:
            states[state] = states.get(state, 0) + 1
        if state and state != _CLEAN:
            unhealthy.append({"pgid": _pgid_of(pg), "state": state})
    return {
        "states": states,
        "unhealthyCount": len(unhealthy),
        "unhealthy": unhealthy,
    }


def pg_dump_stuck(conn: Any) -> list[dict]:
    """[READ] Stuck PGs (inactive/unclean/stale/undersized/degraded) + implicated OSDs."""
    try:
        raw = as_obj(conn.get(_HEALTH_FULL))
    except Exception as exc:  # noqa: BLE001 — report as partial
        return [{"error": s(exc, 200)}]

    stuck: list[dict] = []
    for pg in _pg_records(raw):
        state = _state_of(pg)
        if not state or not any(m in state for m in _STUCK_MARKERS):
            continue
        stuck.append({
            "pgid": _pgid_of(pg),
            "state": state,
            "implicatedOsds": _osds_of(pg),
        })
    return stuck


def _checks(raw: dict) -> dict:
    health = as_obj(raw.get("health")) or raw
    checks = health.get("checks")
    return checks if isinstance(checks, dict) else {}


def _overdue_from_check(raw: dict, code: str) -> list[dict]:
    """Extract the per-PG detail lines from a PG_NOT(_DEEP)_SCRUBBED check."""
    check = as_obj(_checks(raw).get(code))
    out: list[dict] = []
    for item in (check.get("detail") or []):
        if not isinstance(item, dict):
            continue
        out.append({
            "pgid": s(item.get("pgid") or item.get("pg") or ""),
            "message": s(item.get("message") or item.get("summary") or ""),
        })
    return out


def scrub_status(conn: Any) -> dict:
    """[READ] PGs overdue for shallow / deep scrub (from the PG_NOT(_DEEP)_SCRUBBED checks)."""
    try:
        raw = as_obj(conn.get(_HEALTH_FULL))
    except Exception as exc:  # noqa: BLE001 — report as partial
        return {"error": s(exc, 200)}

    overdue_scrub = _overdue_from_check(raw, "PG_NOT_SCRUBBED")
    overdue_deep = _overdue_from_check(raw, "PG_NOT_DEEP_SCRUBBED")
    # Fall back to per-PG last-scrub markers if the checks carried no detail.
    if not overdue_scrub or not overdue_deep:
        for pg in _pg_records(raw):
            state = _state_of(pg)
            pgid = _pgid_of(pg)
            if not overdue_scrub and "not scrubbed" in state:
                overdue_scrub.append({"pgid": pgid, "message": state})
            if not overdue_deep and "not deep-scrubbed" in state:
                overdue_deep.append({"pgid": pgid, "message": state})
    return {"overdueScrub": overdue_scrub, "overdueDeepScrub": overdue_deep}


# ── writes ───────────────────────────────────────────────────────────────


def trigger_scrub(conn: Any, pgid: str) -> dict:
    """[WRITE][low] Schedule a shallow scrub on a PG. No prior state to capture."""
    conn.post(f"{_PG}/{_seg(pgid)}/scrub", json={})
    return {"action": "trigger_scrub", "pgid": s(pgid)}


def trigger_deep_scrub(conn: Any, pgid: str) -> dict:
    """[WRITE][low] Schedule a deep (data-integrity) scrub on a PG."""
    conn.post(f"{_PG}/{_seg(pgid)}/deep_scrub", json={})
    return {"action": "trigger_deep_scrub", "pgid": s(pgid)}
