"""CephFS + RGW status tests for ceph-aiops.

Proves: cephfs_status normalises MDS ranks and the behind-on-trimming signal,
cephfs_status degrades to an error field when the read fails, rgw_status returns
daemons + large-omap findings and stays partial-safe when the bucket call fails,
and both MCP tools are governed and risk=low. No real Ceph cluster is needed —
the connection is a MagicMock.
"""

from unittest.mock import MagicMock

import pytest


@pytest.mark.unit
def test_cephfs_status_normalizes_ranks_and_behind_on_trimming():
    from ceph_aiops.ops import filesystem as ops

    conn = MagicMock(name="conn")
    conn.get.return_value = [
        {
            "mdsmap": {
                "fs_name": "cephfs",
                "num_clients": 7,
                "info": {
                    "gid_100": {"rank": 0, "state": "up:active", "behind_on_trimming": True,
                                "num_segments": 4200},
                    "gid_200": {"rank": 1, "state": "up:active", "behind_on_trimming": False,
                                "num_segments": 30},
                    "gid_300": {"rank": -1, "state": "up:standby"},
                },
            }
        }
    ]
    out = ops.cephfs_status(conn)
    fs = out["filesystems"][0]
    assert fs["fsName"] == "cephfs"
    assert fs["clientCount"] == 7
    assert fs["standbyCount"] == 1
    ranks = {r["rank"]: r for r in fs["mdsRanks"]}
    assert ranks[0]["behindOnTrimming"] is True
    assert ranks[0]["state"] == "up:active"
    assert ranks[1]["behindOnTrimming"] is False


@pytest.mark.unit
def test_cephfs_status_resilient_to_failure():
    from ceph_aiops.ops import filesystem as ops

    conn = MagicMock(name="conn")
    conn.get.side_effect = RuntimeError("mds down")
    out = ops.cephfs_status(conn)
    assert "error" in out


@pytest.mark.unit
def test_rgw_status_returns_daemons_and_is_partial_safe():
    from ceph_aiops.ops import filesystem as ops

    daemons = [{"id": "rgw.a", "zonegroup": "default", "zone": "default"}]

    # Healthy path: daemons + a large-omap finding.
    conn = MagicMock(name="conn")
    conn.get.side_effect = [
        daemons,
        [{"bucket": "photos", "num_shards": 1,
          "usage": {"rgw.main": {"num_objects": 250000}}}],
    ]
    out = ops.rgw_status(conn)
    assert out["daemons"][0]["id"] == "rgw.a"
    assert out["bucketCount"] == 1
    assert out["largeOmapFindings"][0]["bucket"] == "photos"
    assert out["largeOmapFindings"][0]["signal"] == "LARGE_OMAP"

    # Partial path: bucket call fails -> daemons kept, findings empty.
    conn2 = MagicMock(name="conn2")
    conn2.get.side_effect = [daemons, RuntimeError("bucket api down")]
    partial = ops.rgw_status(conn2)
    assert partial["daemons"][0]["id"] == "rgw.a"
    assert partial["largeOmapFindings"] == []
    assert partial["bucketCount"] is None


@pytest.mark.unit
def test_filesystem_tools_are_governed_and_low_risk():
    from mcp_server.tools import filesystem

    assert filesystem.cephfs_status._risk_level == "low"
    assert filesystem.rgw_status._risk_level == "low"
    assert getattr(filesystem.cephfs_status, "_is_governed_tool", False)
    assert getattr(filesystem.rgw_status, "_is_governed_tool", False)
