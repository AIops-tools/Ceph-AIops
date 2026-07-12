"""CephFS + RGW status (read-only) — two of the loudest operator pain points.

``cephfs_status`` surfaces the notorious **"MDS behind on trimming"** signal per
rank (a metadata-server backpressure symptom that quietly precedes stalls), and
``rgw_status`` flags the **LARGE_OMAP / unsharded bucket index** signal that is
the single most common RGW performance foot-gun.

Both are resilient by design: a failing call degrades to an ``error`` field (or a
partial result) rather than raising, so a status probe survives a sick cluster.
"""

from __future__ import annotations

from typing import Any

from ceph_aiops.ops._util import as_list, as_obj, s

_CEPHFS = "/api/cephfs"
_RGW_DAEMON = "/api/rgw/daemon"
_RGW_BUCKET = "/api/rgw/bucket"


def _norm_rank(raw: dict) -> dict:
    """Fold one raw MDS info record into the stable rank shape."""
    behind = raw.get("num_segments") is not None and bool(raw.get("behind_on_trimming"))
    if "behind_on_trimming" not in raw:
        # Some versions expose trimming state via a health/warnings flag instead.
        behind = bool(raw.get("laggy") or raw.get("behindOnTrimming"))
    return {
        "rank": raw.get("rank"),
        "state": s(raw.get("state")),
        "behindOnTrimming": behind,
    }


def _norm_fs(raw: dict) -> dict:
    """Fold one filesystem's mdsmap into the stable status shape."""
    inner = as_obj(raw.get("cephfs") or raw)
    mdsmap = as_obj(inner.get("mdsmap") or raw.get("mdsmap"))
    info = mdsmap.get("info")
    if isinstance(info, dict):
        members = [m for m in info.values() if isinstance(m, dict)]
    else:
        members = [m for m in as_list(info) if isinstance(m, dict)]
    ranks = [_norm_rank(m) for m in members if m.get("rank") is not None and m.get("rank") != -1]
    standby = [m for m in members if m.get("rank") in (None, -1)]
    return {
        "fsName": s(mdsmap.get("fs_name") or inner.get("name") or raw.get("name")),
        "mdsRanks": ranks,
        "clientCount": mdsmap.get("num_clients") or inner.get("num_clients"),
        "standbyCount": len(standby),
    }


def cephfs_status(conn: Any) -> dict:
    """[READ] Per-filesystem MDS ranks, client count, and the behind-on-trimming signal."""
    try:
        raw = conn.get(_CEPHFS)
    except Exception as exc:  # noqa: BLE001 — report as partial
        return {"error": s(exc, 200)}
    return {"filesystems": [_norm_fs(fs) for fs in as_list(raw)]}


def _large_omap_findings(buckets: list[dict]) -> list[dict]:
    """Flag buckets whose index is unsharded/oversized (the LARGE_OMAP signal)."""
    findings: list[dict] = []
    for b in buckets:
        usage = as_obj(b.get("usage"))
        main = as_obj(usage.get("rgw.main"))
        num_objects = main.get("num_objects")
        num_shards = b.get("num_shards")
        unsharded = num_shards in (0, 1, None)
        oversized = isinstance(num_objects, (int, float)) and num_objects >= 100000
        if unsharded and oversized:
            findings.append({
                "bucket": s(b.get("bucket") or b.get("bid") or b.get("name")),
                "numObjects": num_objects,
                "numShards": num_shards,
                "signal": "LARGE_OMAP",
            })
    return findings


def rgw_status(conn: Any) -> dict:
    """[READ] RGW daemons + a large-omap (unsharded bucket index) scan.

    Partial-safe: if the bucket enumeration fails, daemons are still returned with
    ``largeOmapFindings=[]`` rather than raising.
    """
    try:
        daemons_raw = conn.get(_RGW_DAEMON)
    except Exception as exc:  # noqa: BLE001 — report as partial
        return {"error": s(exc, 200)}
    daemons = [
        {"id": s(d.get("id") or d.get("daemon_id")),
         "zonegroup": s(d.get("zonegroup")),
         "zone": s(d.get("zone"))}
        for d in as_list(daemons_raw)
    ]
    try:
        buckets = as_list(conn.get(_RGW_BUCKET))
        findings = _large_omap_findings(buckets)
        bucket_count: Any = len(buckets)
    except Exception:  # noqa: BLE001 — partial: keep daemons, drop the scan
        findings = []
        bucket_count = None
    return {"daemons": daemons, "bucketCount": bucket_count, "largeOmapFindings": findings}
