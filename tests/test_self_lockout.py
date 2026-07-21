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


# ── set_pool_size: the same protected pools, but only downward ──────────────
#
# delete_pool destroys data and has no undo, so every delete of a protected
# pool is refused. set_pool_size DOES capture priorState, so it is not bug
# class 6 — the reversal exists. What it can do is sever the transport that
# reversal needs: fewer replicas on `.mgr` degrades ceph-mgr, and the undo has
# to reach the Dashboard REST API ceph-mgr serves. Hence a directional rule.


def _sized_conn(size=3, applications=None):
    conn = MagicMock(name="conn")
    conn.get.return_value = {
        "pool_name": ".mgr", "size": size,
        "application_metadata": applications if applications is not None else {"mgr": {}},
    }
    return conn


@pytest.mark.parametrize("pool_name", [".mgr", ".rgw.root"])
def test_shrinking_a_transport_critical_pool_is_refused(pool_name):
    conn = _sized_conn()
    with pytest.raises(SelfLockout):
        ops.set_pool_size(conn, pool_name, 1)
    conn.put.assert_not_called()


def test_the_size_refusal_explains_the_direction_and_the_way_out():
    with pytest.raises(SelfLockout) as ei:
        ops.set_pool_size(_sized_conn(size=3), ".mgr", 1)
    msg = str(ei.value)
    assert "from 3 to 1" in msg, "must name the actual change it refused"
    assert "ceph-mgr" in msg, "must name what breaks"
    assert "undo" in msg, "must connect it to the rollback that would be stranded"
    assert "RAISING" in msg, "must say which direction IS allowed"
    assert "ceph osd pool set" in msg, "must offer the route that does work"


def test_a_mgr_owned_pool_under_another_name_is_refused_too():
    conn = _sized_conn(applications={"mgr_devicehealth": {}})
    with pytest.raises(SelfLockout, match="application_metadata"):
        ops.set_pool_size(conn, "custom-mgr-pool", 2)
    conn.put.assert_not_called()


# ── exactness: only the hazardous direction, only the protected pools ───────


def test_raising_the_size_of_a_protected_pool_is_allowed():
    """More replicas makes `.mgr` safer; blanket-refusing would block the fix."""
    conn = _sized_conn(size=2)
    out = ops.set_pool_size(conn, ".mgr", 3)
    assert out["action"] == "set_pool_size" and out["size"] == 3
    conn.put.assert_called_once()


def test_setting_a_protected_pool_to_its_current_size_is_allowed():
    """A no-op removes no redundancy, so there is nothing to refuse."""
    conn = _sized_conn(size=3)
    ops.set_pool_size(conn, ".mgr", 3)
    conn.put.assert_called_once()


def test_an_ordinary_pool_still_shrinks():
    """The whole point of exactness: normal pools keep working."""
    conn = _sized_conn(size=3, applications={"rbd": {}})
    out = ops.set_pool_size(conn, "data", 1)
    assert out["action"] == "set_pool_size" and out["size"] == 1
    assert out["priorState"]["size"] == 3
    conn.put.assert_called_once()


def test_a_similarly_named_pool_still_shrinks():
    """'.mgr-backup' is not '.mgr'; the name check is exact, not a prefix match."""
    conn = _sized_conn(size=3, applications={"rbd": {}})
    ops.set_pool_size(conn, ".mgr-backup", 1)
    conn.put.assert_called_once()


# ── unknown direction on a KNOWN-protected pool refuses ────────────────────


def test_an_unreadable_size_on_a_protected_pool_is_refused():
    """Fail-open applies to "is it protected", not to "which direction".

    `.mgr` is on the static list, so protection is certain. If the current size
    cannot be read the direction is unknown — and letting size=1 through on a
    pool already known to be transport-critical is the wrong way to be wrong.
    """
    conn = MagicMock(name="conn")
    conn.get.side_effect = RuntimeError("mgr unreachable")
    with pytest.raises(SelfLockout, match="could not be read"):
        ops.set_pool_size(conn, ".mgr", 1)
    conn.put.assert_not_called()


def test_an_unreadable_ordinary_pool_is_not_turned_into_a_protected_one():
    conn = MagicMock(name="conn")
    conn.get.side_effect = RuntimeError("mgr unreachable")
    with pytest.raises(RuntimeError):
        # Fails open at the protection check, so the real read's error surfaces
        # rather than a bogus SelfLockout.
        ops.set_pool_size(conn, "data", 1)
    conn.put.assert_not_called()


# ── the guard is reachable from the preview path too ───────────────────────


def test_the_size_guard_is_reachable_without_performing_the_write():
    conn = _sized_conn(size=3, applications={"rbd": {}})
    ops.guard_pool_size(conn, "data", 1)  # an ordinary pool is silently allowed
    conn.get.return_value = {"pool_name": ".mgr", "size": 3, "application_metadata": {"mgr": {}}}
    with pytest.raises(SelfLockout):
        ops.guard_pool_size(conn, ".mgr", 1)
    conn.put.assert_not_called()


def test_the_mcp_dry_run_refuses_a_protected_size_reduction(monkeypatch):
    """A preview must report the refusal, not a green wouldSetSize."""
    from mcp_server.tools import pool as gov

    conn = _sized_conn(size=3)
    monkeypatch.setattr(gov, "_get_connection", lambda target=None: conn)
    refused = gov.set_pool_size(pool_name=".mgr", size=1, dry_run=True)
    assert "error" in refused and "ceph-mgr" in refused["error"]
    assert "wouldSetSize" not in refused
    conn.put.assert_not_called()


def test_the_mcp_dry_run_still_previews_an_allowed_increase(monkeypatch):
    from mcp_server.tools import pool as gov

    conn = _sized_conn(size=2)
    monkeypatch.setattr(gov, "_get_connection", lambda target=None: conn)
    preview = gov.set_pool_size(pool_name=".mgr", size=3, dry_run=True)
    assert preview["dryRun"] is True and preview["wouldSetSize"]["size"] == 3
    conn.put.assert_not_called()
