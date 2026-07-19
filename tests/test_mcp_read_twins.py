"""Smoke tests that drive each read MCP tool body through a mocked connection.

Proves the governed read twins delegate to their ops function and return the
normalised payload (not an error envelope) when the connection succeeds. The
connection is mocked at each tool module's _get_connection seam — no cluster.
These cover the thin tool-body lines that the ops-layer tests don't reach.
"""

from unittest.mock import MagicMock

import pytest


def _patch(monkeypatch, module, conn):
    monkeypatch.setattr(module, "_get_connection", lambda target=None: conn)


@pytest.mark.unit
def test_pg_read_twins(monkeypatch):
    from mcp_server.tools import pg

    conn = MagicMock(name="conn")
    conn.get.return_value = {"pg_info": {"pgs": [
        {"pgid": "2.0", "state": "active+clean"},
        {"pgid": "2.1", "state": "stale+peering"},
    ]}}
    _patch(monkeypatch, pg, conn)

    summary = pg.pg_summary()
    assert summary["unhealthyCount"] == 1
    stuck = pg.pg_dump_stuck()
    assert stuck["stuck"][0]["pgid"] == "2.1"
    assert stuck["truncated"] is False
    assert "overdueScrub" in pg.scrub_status()


@pytest.mark.unit
def test_clusterops_read_twins(monkeypatch):
    from mcp_server.tools import clusterops as c

    conn = MagicMock(name="conn")
    conn.get.return_value = {
        "in_quorum": [{"name": "a"}], "out_quorum": [],
        "mon_status": {"monmap": {"epoch": 3}},
        "mgr_map": {"active_name": "a", "standbys": [], "modules": []},
        "health": {"checks": {}},
        "df": {"stats": {"total_bytes": 1000, "total_used_raw_bytes": 100,
                         "total_avail_bytes": 900}},
    }
    _patch(monkeypatch, c, conn)

    assert c.mon_status()["epoch"] == 3
    assert c.mgr_status()["active"] == "a"
    assert c.slow_ops()["count"] == 0
    assert c.capacity_forecast(daily_growth_bytes=None)["forecast"] == "insufficient-data"


@pytest.mark.unit
def test_health_and_filesystem_read_twins(monkeypatch):
    from mcp_server.tools import filesystem as fs
    from mcp_server.tools import health as h

    conn = MagicMock(name="conn")
    conn.get.return_value = {"status": "HEALTH_OK", "checks": {}}
    _patch(monkeypatch, h, conn)
    assert h.cluster_health()["healthy"] is True
    assert "status" in h.cluster_status()

    conn2 = MagicMock(name="conn2")
    conn2.get.return_value = []
    _patch(monkeypatch, fs, conn2)
    assert fs.cephfs_status() == {"filesystems": []}
    assert fs.rgw_status()["daemons"] == []


@pytest.mark.unit
def test_inventory_read_twins(monkeypatch):
    from mcp_server.tools import osd as o
    from mcp_server.tools import pool as p
    from mcp_server.tools import rbd as r

    conn = MagicMock(name="conn")
    conn.get.return_value = []
    _patch(monkeypatch, o, conn)
    _patch(monkeypatch, p, conn)
    _patch(monkeypatch, r, conn)
    assert o.osd_tree() == []
    assert o.osd_df() == []
    assert o.osd_perf() == []
    assert p.pool_ls() == []
    assert p.pool_df() == []
    assert r.rbd_ls() == []
