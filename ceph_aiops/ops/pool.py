"""Pool inventory + guarded lifecycle (read + writes).

Reads normalise the pool map and per-pool usage; writes cover the quota / PG /
autoscale / replica-size knobs plus create and delete. Each reversible write
captures the pool's BEFORE state into ``priorState`` so the harness can record a
faithful undo (restore prior quota / pg_num / mode / size). The two footgun ops
— changing replica ``size`` on a live pool (mass data movement) and deleting a
pool (destroys all its data) — are high-risk and gated with a dry-run preview.
"""

from __future__ import annotations

from typing import Any

from ceph_aiops.ops._util import _seg, as_list, as_obj, opt_s, s

_POOL = "/api/pool"


def _norm_pool(raw: dict) -> dict:
    """Fold one raw pool record into the stable inventory shape."""
    apps = raw.get("application_metadata") or raw.get("applications") or {}
    if isinstance(apps, dict):
        app = ",".join(sorted(str(k) for k in apps.keys()))
    elif isinstance(apps, list):
        app = ",".join(str(a) for a in apps)
    else:
        app = ""
    return {
        "poolName": opt_s(raw.get("pool_name")),
        "poolId": raw.get("pool") if raw.get("pool") is not None else raw.get("pool_id"),
        "size": raw.get("size"),
        "minSize": raw.get("min_size"),
        "pgNum": raw.get("pg_num"),
        "autoscaleMode": opt_s(raw.get("pg_autoscale_mode")),
        "application": s(app),
        "quotaMaxBytes": raw.get("quota_max_bytes"),
    }


def list_pools(conn: Any) -> list[dict]:
    """[READ] All pools: size/min_size, pg_num, autoscale mode, application, quota."""
    return [_norm_pool(r) for r in as_list(conn.get(_POOL))]


def _stat(stats: dict, *keys: str) -> Any:
    """Pull the first numeric stat among ``keys`` (Ceph nests some as {'latest': x})."""
    for k in keys:
        v = stats.get(k)
        if isinstance(v, dict):
            v = v.get("latest")
        if isinstance(v, (int, float)):
            return v
    return None


def pool_df(conn: Any) -> dict:
    """[READ] Per-pool usage: used/avail bytes, percent, objects, usable capacity.

    Returns {"pools": [...], "returned": N, "error": str | None}. A missing stats
    block or field still degrades to ``None`` per field, but a *failed call* is
    reported in ``error`` rather than rendered as an empty pool list — a flaky
    mgr must not be indistinguishable from a cluster with no pools, which would
    read as "nothing to worry about".
    """
    try:
        pools = as_list(conn.get(_POOL, params={"stats": "true"}))
    except Exception as exc:  # noqa: BLE001 — reported, never silently swallowed
        return {"pools": [], "returned": 0, "error": s(exc, 200)}
    rows: list[dict] = []
    for raw in pools:
        stats = as_obj(raw.get("stats"))
        size = raw.get("size")
        raw_avail = _stat(stats, "avail_raw", "raw_bytes_avail", "max_avail")
        usable = (
            raw_avail / size
            if isinstance(raw_avail, (int, float)) and isinstance(size, (int, float)) and size
            else None
        )
        rows.append({
            "poolName": opt_s(raw.get("pool_name")),
            "usedBytes": _stat(stats, "bytes_used", "stored"),
            "availBytes": _stat(stats, "max_avail", "avail"),
            "percentUsed": _stat(stats, "percent_used"),
            "objects": _stat(stats, "objects", "num_objects"),
            "usableCapacityBytes": usable,
        })
    return {"pools": rows, "returned": len(rows), "error": None}


# ── writes ───────────────────────────────────────────────────────────────


def set_pool_quota(
    conn: Any, pool_name: str, max_bytes: int | None = None, max_objects: int | None = None
) -> dict:
    """[WRITE][medium] Set a pool's byte/object quota. Reversible → prior quota."""
    prior = as_obj(conn.get(f"{_POOL}/{_seg(pool_name)}"))
    body: dict[str, Any] = {"pool": pool_name}
    if max_bytes is not None:
        body["quota_max_bytes"] = max_bytes
    if max_objects is not None:
        body["quota_max_objects"] = max_objects
    conn.put(f"{_POOL}/{_seg(pool_name)}", json=body)
    return {
        "action": "set_pool_quota",
        "poolName": s(pool_name),
        "priorState": {
            "quotaMaxBytes": prior.get("quota_max_bytes"),
            "quotaMaxObjects": prior.get("quota_max_objects"),
        },
    }


def set_pool_pg_num(conn: Any, pool_name: str, pg_num: int) -> dict:
    """[WRITE][medium] Set a pool's pg_num. Reversible → prior pg_num."""
    prior = as_obj(conn.get(f"{_POOL}/{_seg(pool_name)}"))
    conn.put(f"{_POOL}/{_seg(pool_name)}", json={"pool": pool_name, "pg_num": pg_num})
    return {
        "action": "set_pool_pg_num",
        "poolName": s(pool_name),
        "pgNum": pg_num,
        "priorState": {"pgNum": prior.get("pg_num")},
    }


def set_pool_autoscale(conn: Any, pool_name: str, mode: str) -> dict:
    """[WRITE][medium] Set a pool's PG autoscale mode (on/off/warn). Reversible → prior."""
    prior = as_obj(conn.get(f"{_POOL}/{_seg(pool_name)}"))
    conn.put(f"{_POOL}/{_seg(pool_name)}", json={"pool": pool_name, "pg_autoscale_mode": mode})
    return {
        "action": "set_pool_autoscale",
        "poolName": s(pool_name),
        "mode": s(mode),
        "priorState": {"autoscaleMode": prior.get("pg_autoscale_mode")},
    }


def create_pool(
    conn: Any, pool_name: str, pg_num: int = 32, size: int = 3, application: str = "rbd"
) -> dict:
    """[WRITE][medium] Create a replicated pool."""
    conn.post(_POOL, json={
        "pool": pool_name,
        "pool_type": "replicated",
        "pg_num": pg_num,
        "size": size,
        "application_metadata": [application],
    })
    return {
        "action": "pool_create",
        "poolName": s(pool_name),
        "pgNum": pg_num,
        "size": size,
        "application": s(application),
    }


def set_pool_size(conn: Any, pool_name: str, size: int) -> dict:
    """[WRITE][high] Set a pool's replica size — forces mass data movement on a live pool."""
    prior = as_obj(conn.get(f"{_POOL}/{_seg(pool_name)}"))
    conn.put(f"{_POOL}/{_seg(pool_name)}", json={"pool": pool_name, "size": size})
    return {
        "action": "set_pool_size",
        "poolName": s(pool_name),
        "size": size,
        "priorState": {"size": prior.get("size"), "minSize": prior.get("min_size")},
    }


def delete_pool(conn: Any, pool_name: str) -> dict:
    """[WRITE][high] Delete a pool — destroys all of its data. Irreversible."""
    prior = as_obj(conn.get(f"{_POOL}/{_seg(pool_name)}"))
    conn.delete(f"{_POOL}/{_seg(pool_name)}")
    return {
        "action": "pool_delete",
        "poolName": s(pool_name),
        "priorState": {
            "poolName": opt_s(prior.get("pool_name") or pool_name),
            "size": prior.get("size"),
        },
    }
