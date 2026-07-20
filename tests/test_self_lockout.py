"""Refuse deleting the pool this tool's own transport depends on.

``delete_pool`` had no name guard at all. Deleting ``.mgr`` breaks ceph-mgr,
which serves the Dashboard REST API that EVERY call in this package goes
through — so the damage is not confined to that pool's data: later calls,
including undos for entirely unrelated work, lose their transport. Ceph's own
``mon_allow_pool_delete`` (off by default) is a real external guard, but it is
not this tool's, and operators routinely turn it on for the duration of a
cleanup — which is exactly when an agent is most likely to be enumerating pools.

The name check is static and exact. The application_metadata check catches a
mgr pool under a non-default name and FAILS OPEN on any read error: an
unreadable pool is UNKNOWN, and unknown must never read as "it is protected"
(that would block legitimate cleanup on a flaky cluster).
"""

from unittest.mock import MagicMock

import pytest

from ceph_aiops.ops import pool as ops
from ceph_aiops.ops.pool import SelfLockout

pytestmark = pytest.mark.unit


def _conn(applications=None):
    conn = MagicMock(name="conn")
    conn.get.return_value = {
        "pool_name": "data", "size": 3,
        "application_metadata": applications if applications is not None else {"rbd": {}},
    }
    return conn


# ── the protected names ─────────────────────────────────────────────────────


@pytest.mark.parametrize("pool_name", [".mgr", ".rgw.root"])
def test_the_transport_critical_pools_are_refused_by_name(pool_name):
    conn = _conn()
    with pytest.raises(SelfLockout):
        ops.delete_pool(conn, pool_name)
    conn.delete.assert_not_called()


def test_the_mgr_refusal_explains_the_transport_and_the_way_out():
    with pytest.raises(SelfLockout) as ei:
        ops.delete_pool(_conn(), ".mgr")
    msg = str(ei.value)
    assert "ceph-mgr" in msg, "must name what breaks"
    assert "Dashboard REST API" in msg, "must connect it to this tool's transport"
    assert "ceph osd pool delete" in msg, "must offer the route that does work"


def test_the_name_check_tolerates_surrounding_whitespace():
    with pytest.raises(SelfLockout):
        ops.delete_pool(_conn(), "  .mgr  ")


# ── a mgr pool under a non-default name ─────────────────────────────────────


@pytest.mark.parametrize("application", ["mgr", "mgr_devicehealth"])
def test_a_mgr_owned_pool_is_refused_whatever_it_is_called(application):
    conn = _conn(applications={application: {}})
    with pytest.raises(SelfLockout, match="application_metadata"):
        ops.delete_pool(conn, "custom-mgr-pool")
    conn.delete.assert_not_called()


def test_the_application_check_reads_a_list_shaped_metadata_too():
    """Ceph reports application_metadata as a dict or a list depending on version."""
    conn = _conn(applications=["mgr"])
    with pytest.raises(SelfLockout):
        ops.delete_pool(conn, "custom-mgr-pool")


# ── exactness: ordinary pools stay deletable ────────────────────────────────


def test_an_ordinary_pool_is_still_deleted():
    conn = _conn()
    out = ops.delete_pool(conn, "data")
    assert out["action"] == "pool_delete"
    conn.delete.assert_called_once()


def test_a_pool_named_similarly_is_not_swept_up():
    """'.mgr-backup' is not '.mgr'; the check is exact, not a prefix match."""
    conn = _conn()
    out = ops.delete_pool(conn, ".mgr-backup")
    assert out["action"] == "pool_delete"
    conn.delete.assert_called_once()


def test_a_pool_with_an_unrelated_application_is_still_deleted():
    conn = _conn(applications={"cephfs": {}})
    ops.delete_pool(conn, "cephfs-data")
    conn.delete.assert_called_once()


# ── fail open: an unreadable pool is unknown, not protected ─────────────────


def test_delete_proceeds_when_the_metadata_read_fails():
    """A flaky cluster must not make every pool undeletable."""
    conn = MagicMock(name="conn")
    conn.get.side_effect = RuntimeError("mgr unreachable")
    with pytest.raises(RuntimeError):
        # The guard fails open, so the failure surfaces from the real read that
        # follows — not as a bogus SelfLockout.
        ops.delete_pool(conn, "data")


def test_the_name_guard_still_fires_when_the_metadata_read_fails():
    """Fail-open on the read must not weaken the static name list."""
    conn = MagicMock(name="conn")
    conn.get.side_effect = RuntimeError("mgr unreachable")
    with pytest.raises(SelfLockout):
        ops.delete_pool(conn, ".mgr")
    conn.delete.assert_not_called()


# ── guard reachability (the MCP wrapper calls it before its dry_run return) ──


def test_the_guard_is_reachable_without_performing_the_delete():
    conn = _conn()
    ops.guard_delete_pool(conn, "data")  # an ordinary pool is silently allowed
    with pytest.raises(SelfLockout):
        ops.guard_delete_pool(conn, ".mgr")
    conn.delete.assert_not_called()


def test_the_mcp_dry_run_refuses_a_protected_pool(monkeypatch):
    """A preview must report the refusal, not a green wouldDelete."""
    from mcp_server.tools import pool as gov

    conn = _conn()
    monkeypatch.setattr(gov, "_get_connection", lambda target=None: conn)
    refused = gov.pool_delete(pool_name=".mgr", dry_run=True)
    assert "error" in refused and "ceph-mgr" in refused["error"]
    assert "wouldDelete" not in refused
    conn.delete.assert_not_called()


def test_the_mcp_dry_run_still_previews_an_ordinary_pool(monkeypatch):
    from mcp_server.tools import pool as gov

    conn = _conn()
    monkeypatch.setattr(gov, "_get_connection", lambda target=None: conn)
    preview = gov.pool_delete(pool_name="data", dry_run=True)
    assert preview["dryRun"] is True and preview["wouldDelete"]["poolName"] == "data"
    conn.delete.assert_not_called()


def test_self_lockout_is_a_valueerror():
    """CLI/MCP error handling keys off ValueError; keep it in that family."""
    assert issubclass(SelfLockout, ValueError)
