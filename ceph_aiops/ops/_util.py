"""Shared helpers for Ceph ops modules.

Ceph Dashboard REST endpoints return either a bare JSON array (e.g. ``/api/osd``)
or a JSON object (e.g. ``/api/health/full``). ``as_list`` / ``as_obj`` normalise
both. All API-returned text reaches the caller only after ``sanitize()``
(output hygiene: control/format-char stripping + truncation). ``_seg`` URL-encodes
one path segment so agent-supplied identifiers cannot alter the request path.
"""

from __future__ import annotations

from typing import Any
from urllib.parse import quote

from ceph_aiops.governance import sanitize


def _seg(value: Any) -> str:
    """URL-encode a single REST path segment.

    Agent-supplied identifiers (pool names, OSD ids, PG ids, snapshot names)
    are interpolated into Dashboard URL paths; encoding with ``safe=""`` keeps
    a hostile value like ``../admin`` from changing the requested path.
    """
    return quote(str(value), safe="")


def as_list(data: Any) -> list[dict]:
    """Normalise a list payload (bare array or ``{"data"/"result": [...]}``) to dicts."""
    if isinstance(data, dict):
        items = data.get("data", data.get("result", []))
    else:
        items = data
    return [i for i in (items or []) if isinstance(i, dict)]


def as_obj(data: Any) -> dict:
    """Return ``data`` as a dict (empty dict if it isn't one)."""
    return data if isinstance(data, dict) else {}


def s(value: Any, limit: int = 256) -> str:
    """Sanitize an arbitrary value to a bounded, injection-safe string."""
    return sanitize(str(value if value is not None else ""), limit)


def bytes_h(n: Any) -> str:
    """Human-readable bytes (best-effort; returns '' for non-numeric)."""
    if not isinstance(n, (int, float)):
        return ""
    size = float(n)
    for unit in ("B", "KiB", "MiB", "GiB", "TiB", "PiB"):
        if abs(size) < 1024.0:
            return f"{size:.1f}{unit}"
        size /= 1024.0
    return f"{size:.1f}EiB"
