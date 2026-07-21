"""Pool inventory + guarded lifecycle (read + writes).

Reads normalise the pool map and per-pool usage; writes cover the quota / PG /
autoscale / replica-size knobs plus create and delete. Each reversible write
captures the pool's BEFORE state into ``priorState`` so the harness can record a
faithful undo (restore prior quota / pg_num / mode / size). The two footgun ops
— changing replica ``size`` on a live pool (mass data movement) and deleting a
pool (destroys all its data) — are high-risk and gated with a dry-run preview.

``delete_pool`` and ``set_pool_size`` additionally refuse the pools this tool's
own transport depends on (:class:`SelfLockout`), off the one shared
:func:`_protection_reason` check. Deleting ``.mgr`` breaks ceph-mgr, which serves
the Dashboard REST API every call in this package goes through — including undos
for unrelated work. Ceph's own ``mon_allow_pool_delete`` (off by default) is a
real external guard, but it is not this tool's, and it is routinely turned on for
the duration of a cleanup.

The two refusals differ in shape, so they are not the same rule:

* A pool delete is IRREVERSIBLE, so every delete of a protected pool is refused.
* A size change is reversible — ``set_pool_size`` captures ``priorState`` — but
  reducing ``.mgr``'s replicas degrades ceph-mgr, and the recorded undo has to
  travel over the API ceph-mgr serves. The reversal exists and cannot be
  reached. So only the DOWNWARD direction is refused; raising the replica count
  adds redundancy and stays available.
"""

from __future__ import annotations

from typing import Any

from ceph_aiops.ops._util import _seg, as_list, as_obj, opt_s, s

_POOL = "/api/pool"

# Pools whose loss severs this tool's own transport or the cluster's ability to
# describe itself. `.mgr` backs ceph-mgr, which serves the Dashboard REST API
# this package speaks exclusively; `.rgw.root` holds the RGW realm/zone map,
# without which the object-storage half of the tool cannot resolve anything.
# The list is STATIC, so there is no fail-open case for the name check.
# Each value is an identity clause ("what this pool is"), not a consequence:
# the delete guard and the size guard append their own, because losing a pool
# and thinning its replicas fail differently.
_PROTECTED_POOLS: dict[str, str] = {
    ".mgr": (
        "it backs ceph-mgr, which serves the Dashboard REST API this tool speaks"
    ),
    ".rgw.root": (
        "it holds the RGW realm/zone configuration, without which the object-storage "
        "half of this tool cannot resolve a thing"
    ),
}
# Applications that mark a pool as mgr-owned even under a non-default name.
_MGR_APPLICATIONS = {"mgr", "mgr_devicehealth"}


class SelfLockout(ValueError):  # noqa: N818 — teaching error, reads as a statement
    """Refused: the operation would sever this tool's own access to the cluster."""


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
    """[WRITE][high] Set a pool's replica size — forces mass data movement on a live pool.

    **Refuses a size REDUCTION on ``.mgr`` / ``.rgw.root`` and any mgr-owned
    pool.** Thinning the mgr pool degrades ceph-mgr, which serves the Dashboard
    REST API this tool speaks — including the undo that would restore the size.
    Raising the size on those pools is allowed.
    """
    guard_pool_size(conn, pool_name, size)
    prior = as_obj(conn.get(f"{_POOL}/{_seg(pool_name)}"))
    conn.put(f"{_POOL}/{_seg(pool_name)}", json={"pool": pool_name, "size": size})
    return {
        "action": "set_pool_size",
        "poolName": s(pool_name),
        "size": size,
        "priorState": {"size": prior.get("size"), "minSize": prior.get("min_size")},
    }


def _mgr_application_reason(conn: Any, pool_name: str) -> str | None:
    """Whether the pool is marked mgr-owned by its application_metadata.

    Catches a mgr pool under a non-default name. Returns None on any read
    failure: an unreadable pool is UNKNOWN, and unknown must never read as
    "it is the mgr pool" — the static name list still covers the default.
    """
    try:
        raw = as_obj(conn.get(f"{_POOL}/{_seg(pool_name)}"))
    except Exception:  # noqa: BLE001 — unknown, never a false "it is protected"
        return None
    apps = raw.get("application_metadata") or raw.get("applications") or {}
    names = (
        {str(k).lower() for k in apps}
        if isinstance(apps, (dict, list, tuple, set))
        else set()
    )
    if names & _MGR_APPLICATIONS:
        return (
            "its application_metadata marks it a ceph-mgr pool, and ceph-mgr serves "
            "the Dashboard REST API this tool speaks"
        )
    return None


def _protection_reason(conn: Any, pool_name: str) -> str | None:
    """Why this pool is transport-critical, or None when it is an ordinary pool.

    The single source of truth for "is this pool protected", shared by every
    guard. The name check is static and exact. The application_metadata check
    needs a read and fails open on any error — an unreadable pool is unknown,
    and unknown must never read as "it is protected", which would block
    legitimate work on a flaky cluster.
    """
    name = str(pool_name).strip()
    return _PROTECTED_POOLS.get(name) or _mgr_application_reason(conn, name)


def _current_pool_size(conn: Any, pool_name: str) -> int | None:
    """The pool's current replica size; None when it cannot be read.

    None means UNKNOWN, never "no replicas". Callers must not treat it as a
    number.
    """
    try:
        raw = as_obj(conn.get(f"{_POOL}/{_seg(pool_name)}"))
    except Exception:  # noqa: BLE001 — unknown size, decided by the caller
        return None
    size = raw.get("size")
    return size if isinstance(size, int) and not isinstance(size, bool) else None


def guard_pool_size(conn: Any, pool_name: str, size: int) -> None:
    """Refuse a replica-size REDUCTION on a pool this tool's transport depends on.

    Called by ``set_pool_size`` itself *and* by the MCP wrapper ahead of its
    ``dry_run`` early return, so a preview cannot come back green for a call
    that is about to be refused.

    Unlike ``delete_pool`` this is not irreversible — ``set_pool_size`` captures
    ``priorState``, so the undo exists. The hazard is narrower and worse-shaped:
    thinning ``.mgr`` to fewer replicas degrades ceph-mgr, and ceph-mgr serves
    the Dashboard REST API that the undo would have to travel over. The
    reversal is recorded and unreachable.

    Deliberately DIRECTIONAL. Raising the replica count on a protected pool adds
    redundancy and is allowed; blanket-refusing every size change would block the
    one operation that makes ``.mgr`` safer. Only the downward direction is
    refused.

    When the current size cannot be read, the direction is unknown and the call
    is refused. That is a departure from the fail-open rule above, and the split
    is intentional: failing open on *whether the pool is protected* costs a
    missed guard on a pool that probably was not protected anyway, whereas
    failing open on *which direction* would let ``size=1`` through on a pool
    already known to be transport-critical.
    """
    name = str(pool_name).strip()
    reason = _protection_reason(conn, name)
    if reason is None:
        return
    current = _current_pool_size(conn, name)
    if current is not None and size >= current:
        return  # an increase (or a no-op) adds redundancy — not the hazard
    change = (
        f"from {current} to {size}" if current is not None
        else f"to {size} (its current size could not be read, so this cannot be "
             f"shown to be an increase)"
    )
    raise SelfLockout(
        f"Refusing to reduce the replica size of pool '{name}' {change}: {reason}. "
        f"Fewer replicas means this pool survives fewer OSD failures, and if it goes "
        f"unavailable the Dashboard REST API goes with it — including the undo that "
        f"would put the size back, which has to travel over that same API. RAISING "
        f"the size is allowed and is not refused. If the pool really must shrink, do "
        f"it from a node with 'ceph osd pool set {name} size {size}' where you can "
        f"recover ceph-mgr afterwards."
    )


def guard_delete_pool(conn: Any, pool_name: str) -> None:
    """Raise the :class:`SelfLockout` ``delete_pool`` would raise, without deleting.

    Called by ``delete_pool`` itself *and* by the MCP wrapper ahead of its
    ``dry_run`` early return, so a preview of a protected pool reports the
    refusal instead of a green ``wouldDelete``. Both paths run this one
    function, so the preview and the real call can never disagree.

    Protection is decided by :func:`_protection_reason`: a static exact-name
    check plus an application_metadata check that fails open on a read error.
    """
    name = str(pool_name).strip()
    lockout_reason = _protection_reason(conn, name)
    if lockout_reason is None:
        return
    raise SelfLockout(
        f"Refusing to delete pool '{name}': {lockout_reason}. A pool delete has "
        f"no undo, and this one would take the transport down with it — later "
        f"calls, including undos for unrelated work, would have nothing to talk "
        f"to. If the cluster really must lose this pool, do it from a node with "
        f"'ceph osd pool delete' where you can recover ceph-mgr afterwards."
    )


def delete_pool(conn: Any, pool_name: str) -> dict:
    """[WRITE][high] Delete a pool — destroys all of its data. Irreversible.

    **Refuses ``.mgr`` / ``.rgw.root`` and any pool whose application_metadata
    marks it a ceph-mgr pool.** Deleting the mgr pool severs the Dashboard REST
    API this tool depends on, so the loss is not confined to that pool's data —
    every later call, including undos for unrelated work, loses its transport.
    """
    guard_delete_pool(conn, pool_name)
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
