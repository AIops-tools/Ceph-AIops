"""Unit tests for the remaining PG reads/writes not covered by test_pg.py.

Proves: pg_dump_stuck filters to the stuck-marker states and surfaces implicated
OSDs, scrub_status reads the PG_NOT(_DEEP)_SCRUBBED check details (and falls back
to per-PG last-scrub markers), the reads degrade to an error on a failing call,
and trigger_deep_scrub posts to the deep_scrub path. No real Ceph cluster is
needed — the connection is a MagicMock.
"""

from unittest.mock import MagicMock

import pytest

_HEALTH_FULL = "/api/health/full"


@pytest.mark.unit
def test_pg_dump_stuck_filters_and_reports_osds():
    from ceph_aiops.ops import pg as ops

    conn = MagicMock(name="conn")
    conn.get.return_value = {
        "pg_info": {"pgs": [
            {"pgid": "2.0", "state": "active+clean", "up": [0, 1]},
            {"pgid": "2.1", "state": "active+undersized+degraded", "up": [3], "acting": [3]},
            {"pgid": "2.2", "state": "stale+peering", "blocked_by": [7]},
        ]}
    }
    out = ops.pg_dump_stuck(conn)
    pgids = {r["pgid"] for r in out}
    assert pgids == {"2.1", "2.2"}
    stuck = {r["pgid"]: r for r in out}
    assert stuck["2.1"]["implicatedOsds"] == [3]
    assert stuck["2.2"]["implicatedOsds"] == [7]


@pytest.mark.unit
def test_pg_dump_stuck_resilient_to_failure():
    from ceph_aiops.ops import pg as ops

    conn = MagicMock(name="conn")
    conn.get.side_effect = RuntimeError("mgr down")
    out = ops.pg_dump_stuck(conn)
    assert out and "error" in out[0]


@pytest.mark.unit
def test_scrub_status_reads_check_detail():
    from ceph_aiops.ops import pg as ops

    conn = MagicMock(name="conn")
    conn.get.return_value = {
        "health": {"checks": {
            "PG_NOT_SCRUBBED": {"detail": [
                {"pgid": "2.5", "message": "not scrubbed since 2026-07-01"},
            ]},
            "PG_NOT_DEEP_SCRUBBED": {"detail": [
                {"pg": "2.6", "summary": "deep scrub overdue"},
            ]},
        }}
    }
    out = ops.scrub_status(conn)
    assert out["overdueScrub"][0]["pgid"] == "2.5"
    assert "not scrubbed" in out["overdueScrub"][0]["message"]
    assert out["overdueDeepScrub"][0]["pgid"] == "2.6"
    assert out["overdueDeepScrub"][0]["message"] == "deep scrub overdue"


@pytest.mark.unit
def test_scrub_status_falls_back_to_pg_state_markers():
    from ceph_aiops.ops import pg as ops

    conn = MagicMock(name="conn")
    conn.get.return_value = {
        "pg_info": {"pgs": [
            {"pgid": "3.0", "state": "active+clean+not scrubbed"},
            {"pgid": "3.1", "state": "active+clean+not deep-scrubbed"},
        ]}
    }
    out = ops.scrub_status(conn)
    assert out["overdueScrub"][0]["pgid"] == "3.0"
    assert out["overdueDeepScrub"][0]["pgid"] == "3.1"


@pytest.mark.unit
def test_scrub_status_resilient_to_failure():
    from ceph_aiops.ops import pg as ops

    conn = MagicMock(name="conn")
    conn.get.side_effect = RuntimeError("boom")
    assert "error" in ops.scrub_status(conn)


@pytest.mark.unit
def test_trigger_deep_scrub_posts_to_deep_scrub_path():
    from ceph_aiops.ops import pg as ops

    conn = MagicMock(name="conn")
    conn.post.return_value = {}
    out = ops.trigger_deep_scrub(conn, "2.1a")
    conn.post.assert_called_once_with("/api/pg/2.1a/deep_scrub", json={})
    assert out == {"action": "trigger_deep_scrub", "pgid": "2.1a"}


@pytest.mark.unit
def test_pg_records_reads_pg_stats_shape():
    from ceph_aiops.ops import pg as ops

    raw = {"pgmap": {"pg_stats": [{"pgid": "1.0", "state": "active+clean"}]}}
    recs = ops._pg_records(raw)
    assert recs == [{"pgid": "1.0", "state": "active+clean"}]
    # Unknown shape → empty list, not a crash.
    assert ops._pg_records({"statuses": {"active+clean": 5}}) == []
