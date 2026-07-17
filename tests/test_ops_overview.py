"""Unit tests for the one-shot fleet_overview read.

Proves: fleet_overview folds cluster_health + osd_tree into a single summary
(status, active checks, top findings, OSD up/in counts) and degrades to a
partial summary with an ``errors`` list when a sub-call raises rather than
propagating the traceback. No real Ceph cluster is needed — the connection is a
MagicMock dispatching per endpoint.
"""

from unittest.mock import MagicMock

import pytest

_HEALTH_FULL = "/api/health/full"
_OSD = "/api/osd"


@pytest.mark.unit
def test_fleet_overview_aggregates_health_and_osds():
    from ceph_aiops.ops import overview as ops

    def _dispatch(path, **kwargs):
        if path == _HEALTH_FULL:
            return {
                "health": {
                    "status": "HEALTH_WARN",
                    "checks": {
                        "OSD_NEARFULL": {"severity": "HEALTH_WARN",
                                         "summary": {"message": "1 nearfull"}},
                        "PG_DEGRADED": {"severity": "HEALTH_WARN",
                                        "summary": {"message": "degraded"}},
                    },
                }
            }
        if path == _OSD:
            return [
                {"osd": 0, "up": 1, "in": 1},
                {"osd": 1, "up": 1, "in": 1},
                {"osd": 2, "up": 0, "in": 1},
            ]
        raise AssertionError(f"unexpected path {path}")

    conn = MagicMock(name="conn")
    conn.get.side_effect = _dispatch
    out = ops.fleet_overview(conn)
    assert out["status"] == "HEALTH_WARN"
    assert out["activeChecks"] == 2
    assert set(out["topFindings"]) == {"OSD_NEARFULL", "PG_DEGRADED"}
    assert out["osdsTotal"] == 3
    assert out["osdsUp"] == 2
    assert out["osdsIn"] == 3
    assert out["errors"] == []


@pytest.mark.unit
def test_fleet_overview_degrades_on_osd_failure():
    from ceph_aiops.ops import overview as ops

    def _dispatch(path, **kwargs):
        if path == _HEALTH_FULL:
            return {"health": {"status": "HEALTH_OK", "checks": {}}}
        raise RuntimeError("osd api down")

    conn = MagicMock(name="conn")
    conn.get.side_effect = _dispatch
    out = ops.fleet_overview(conn)
    # health succeeded, osd sub-call failed → partial summary, not a traceback.
    assert out["status"] == "HEALTH_OK"
    assert out["osdsTotal"] == 0
    assert len(out["errors"]) == 1
    assert out["errors"][0].startswith("osd:")
