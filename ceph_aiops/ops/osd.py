"""OSD inventory + guarded lifecycle (read + writes).

Reads normalise the OSD map/utilisation; writes cover the exact destructive ops
operators most fear — reweight (drain step), mark in/out, and purge. Each write
captures the OSD's BEFORE state into ``priorState`` so the harness can record a
faithful undo (reweight back, mark back in) or a truthful audit row (purge has
no safe inverse).
"""

from __future__ import annotations

from typing import Any

from ceph_aiops.ops._util import _seg, as_list, as_obj, opt_s, s

_OSD = "/api/osd"
_FLAGS = "/api/osd/flags"


def _host_name(raw: dict) -> Any:
    """Extract the OSD's host name from the Dashboard payload.

    The Dashboard's ``/api/osd`` returns ``host`` as a CRUSH bucket *dict*
    (``{"id": -2, "name": "node1", "type": "host", ...}``), not a bare string —
    stringifying it whole leaks a Python dict repr into the result. Prefer the
    bucket's ``name``; tolerate older/flat shapes where ``host`` is already a
    string. Verified against Ceph 18 (reef) Dashboard REST, 2026-07-31.
    """
    host = raw.get("host")
    if isinstance(host, dict):
        return host.get("name")
    if host:
        return host
    tree = raw.get("tree") or {}
    th = tree.get("host")
    return th.get("name") if isinstance(th, dict) else th


def _norm_osd(raw: dict) -> dict:
    """Fold one raw OSD record into the stable inventory shape."""
    stats = raw.get("osd_stats") or raw.get("stats") or {}
    kb = stats.get("kb")
    kb_used = stats.get("kb_used")
    used_pct = round(100.0 * kb_used / kb, 1) if kb and kb_used else None
    tree = raw.get("tree") or {}
    return {
        "id": raw.get("osd") if raw.get("osd") is not None else raw.get("id"),
        "up": bool(raw.get("up", 0)),
        "in": bool(raw.get("in", 0)),
        "weight": raw.get("weight"),
        # crush_weight is nested under ``tree`` on the Dashboard REST (reef);
        # keep the top-level lookup for flatter/mocked shapes.
        "crushWeight": raw.get("crush_weight", tree.get("crush_weight")),
        "usedPercent": used_pct,
        "host": opt_s(_host_name(raw)),
        # Fall back to the tree only when the key is truly ABSENT — an explicit
        # "" upstream must survive as "" (null-vs-empty invariant), not collapse
        # to null via a falsy `or`.
        "deviceClass": opt_s(
            raw["device_class"] if "device_class" in raw else tree.get("device_class")
        ),
    }


def osd_tree(conn: Any) -> list[dict]:
    """[READ] All OSDs with up/in state, weight, host, device class."""
    return [_norm_osd(r) for r in as_list(conn.get(_OSD))]


def osd_df(conn: Any) -> list[dict]:
    """[READ] Per-OSD utilisation; flags near/backfill-full outliers.

    Rows are sorted most-full first so the offenders an operator is hunting sit
    at the top.
    """
    rows = [_norm_osd(r) for r in as_list(conn.get(_OSD))]
    for r in rows:
        pct = r.get("usedPercent")
        r["nearfull"] = bool(pct is not None and pct >= 85.0)
        r["full"] = bool(pct is not None and pct >= 95.0)
    return sorted(rows, key=lambda r: (r.get("usedPercent") or -1), reverse=True)


def osd_perf(conn: Any) -> list[dict]:
    """[READ] Per-OSD commit/apply latency; slowest OSDs first."""
    rows = []
    for r in as_list(conn.get(f"{_OSD}/perf")):
        perf = r.get("perf_stats") or r
        rows.append({
            "id": r.get("osd") if r.get("osd") is not None else r.get("id"),
            "commitLatencyMs": perf.get("commit_latency_ms"),
            "applyLatencyMs": perf.get("apply_latency_ms"),
        })
    return sorted(rows, key=lambda r: (r.get("commitLatencyMs") or -1), reverse=True)


# ── writes ───────────────────────────────────────────────────────────────


def set_cluster_flag(conn: Any, flag: str, enable: bool = True) -> dict:
    """[WRITE][medium] Set/unset a cluster flag (noout/nobackfill/norecover/noscrub/…).

    Captures the current flag set as priorState so it can be restored.
    """
    current = as_obj(conn.get(_FLAGS)).get("flags") or []
    flags = set(current)
    if enable:
        flags.add(flag)
    else:
        flags.discard(flag)
    conn.put(_FLAGS, json={"flags": sorted(flags)})
    return {"action": "set_cluster_flag", "flag": s(flag), "enabled": enable,
            "priorState": {"flags": [s(f) for f in current]}}


def _osd_raw(conn: Any, osd_id: int) -> dict:
    for r in as_list(conn.get(_OSD)):
        oid = r.get("osd") if r.get("osd") is not None else r.get("id")
        if oid == osd_id:
            return r
    raise KeyError(f"OSD {osd_id} not found.")


def osd_reweight(conn: Any, osd_id: int, weight: float) -> dict:
    """[WRITE][medium] Set an OSD's reweight (0.0 = drain). Reversible → prior weight."""
    prior = _osd_raw(conn, osd_id).get("weight")
    conn.post(f"{_OSD}/{_seg(osd_id)}/reweight", json={"weight": weight})
    return {"action": "osd_reweight", "osdId": osd_id, "weight": weight,
            "priorState": {"weight": prior}}


def preview_reweight(conn: Any, osd_id: int, weight: float) -> dict:
    """[READ] Preview an osd_reweight — reads the current weight, changes nothing.

    Reads the same OSD record the real reweight captures as priorState, so the
    preview reports the exact current→target transition without issuing the POST.
    """
    current = _osd_raw(conn, osd_id).get("weight")
    return {"osdId": osd_id, "currentWeight": current, "targetWeight": weight}


def mark_osd(conn: Any, osd_id: int, action: str) -> dict:
    """[WRITE] Mark an OSD in/out/down. Captures prior up/in state for undo/audit."""
    raw = _osd_raw(conn, osd_id)
    prior = {"in": bool(raw.get("in", 0)), "up": bool(raw.get("up", 0))}
    conn.post(f"{_OSD}/{_seg(osd_id)}/mark", json={"action": action})
    return {"action": f"mark_osd_{action}", "osdId": osd_id, "priorState": prior}


def osd_purge(conn: Any, osd_id: int) -> dict:
    """[WRITE][high] Purge an OSD (destroy + crush rm + auth del). Irreversible."""
    raw = _osd_raw(conn, osd_id)
    prior = {"in": bool(raw.get("in", 0)), "up": bool(raw.get("up", 0)),
             "host": opt_s(_host_name(raw))}
    conn.delete(f"{_OSD}/{_seg(osd_id)}")
    return {"action": "osd_purge", "osdId": osd_id, "priorState": prior}
