"""Unit tests for OSD inventory + guarded lifecycle (ops + MCP tools).

Proves: _norm_osd computes usedPercent from kb/kb_used, osd_df flags near/full
outliers and sorts most-full first, osd_perf sorts slowest first, the writes
capture BEFORE state into priorState (flag set, reweight, mark, purge), and the
MCP twins carry the correct risk tiers, gate destructive ops behind dry_run, and
record a prior-state undo. No real Ceph cluster is needed — the connection is a
MagicMock.
"""

from unittest.mock import MagicMock

import pytest

_OSD = "/api/osd"


def _osd_rows():
    return [
        {"osd": 0, "up": 1, "in": 1, "weight": 1.0, "crush_weight": 0.9,
         "device_class": "ssd", "host": "node1",
         "osd_stats": {"kb": 1000, "kb_used": 900}},
        {"osd": 1, "up": 1, "in": 1, "weight": 1.0,
         "osd_stats": {"kb": 1000, "kb_used": 500}},
        {"osd": 2, "up": 0, "in": 0, "weight": 0.0,
         "osd_stats": {"kb": 1000, "kb_used": 970}},
    ]


@pytest.mark.unit
def test_norm_osd_computes_used_percent_and_state():
    from ceph_aiops.ops import osd as ops

    conn = MagicMock(name="conn")
    conn.get.return_value = _osd_rows()
    rows = ops.osd_tree(conn)
    conn.get.assert_called_once_with(_OSD)
    first = next(r for r in rows if r["id"] == 0)
    assert first["up"] is True and first["in"] is True
    assert first["usedPercent"] == 90.0
    assert first["weight"] == 1.0
    assert first["crushWeight"] == 0.9
    assert first["host"] == "node1"
    assert first["deviceClass"] == "ssd"


@pytest.mark.unit
def test_norm_osd_real_dashboard_shape_host_dict_and_nested_crush_weight():
    """Regression: the reef Dashboard /api/osd shape, verified live 2026-07-31.

    The real Dashboard returns ``host`` as a CRUSH *bucket dict* (not a string)
    and puts ``crush_weight`` under a nested ``tree`` object — never at the top
    level. The flat mock shape hid both: ``host`` leaked a Python dict repr and
    ``crushWeight`` came back null on every real cluster.
    """
    from ceph_aiops.ops import osd as ops

    real_row = {
        "osd": 0, "up": 1, "in": 1, "weight": 1.0,
        "host": {"id": -2, "name": "node1", "type": "host", "children": [0]},
        "tree": {"id": 0, "type": "osd", "crush_weight": 0.097686767578125,
                 "reweight": 1.0, "name": "osd.0"},
        "osd_stats": {"kb": 1000, "kb_used": 30},
    }
    conn = MagicMock(name="conn")
    conn.get.return_value = [real_row]
    row = ops.osd_tree(conn)[0]
    assert row["host"] == "node1", "host must be the bucket name, not a dict repr"
    assert isinstance(row["host"], str)
    assert row["crushWeight"] == 0.097686767578125, "crush_weight lives under tree"


@pytest.mark.unit
def test_osd_df_flags_and_sorts_most_full_first():
    from ceph_aiops.ops import osd as ops

    conn = MagicMock(name="conn")
    conn.get.return_value = _osd_rows()
    rows = ops.osd_df(conn)
    # Sorted most-full first: 97% (osd2), 90% (osd0), 50% (osd1).
    assert [r["id"] for r in rows] == [2, 0, 1]
    top = rows[0]
    assert top["usedPercent"] == 97.0
    assert top["nearfull"] is True
    assert top["full"] is True
    mid = rows[1]
    assert mid["nearfull"] is True   # 90 >= 85
    assert mid["full"] is False      # 90 < 95
    bottom = rows[2]
    assert bottom["nearfull"] is False
    assert bottom["full"] is False


@pytest.mark.unit
def test_osd_perf_sorts_slowest_first():
    from ceph_aiops.ops import osd as ops

    conn = MagicMock(name="conn")
    conn.get.return_value = [
        {"osd": 0, "perf_stats": {"commit_latency_ms": 5, "apply_latency_ms": 4}},
        {"osd": 1, "perf_stats": {"commit_latency_ms": 40, "apply_latency_ms": 33}},
    ]
    rows = ops.osd_perf(conn)
    conn.get.assert_called_once_with(f"{_OSD}/perf")
    assert [r["id"] for r in rows] == [1, 0]
    assert rows[0]["commitLatencyMs"] == 40
    assert rows[0]["applyLatencyMs"] == 33


@pytest.mark.unit
def test_set_cluster_flag_captures_prior_and_puts_sorted():
    from ceph_aiops.ops import osd as ops

    conn = MagicMock(name="conn")
    conn.get.return_value = {"flags": ["noout"]}
    conn.put.return_value = {}
    out = ops.set_cluster_flag(conn, "noscrub", enable=True)
    assert out["action"] == "set_cluster_flag"
    assert out["flag"] == "noscrub"
    assert out["enabled"] is True
    assert out["priorState"]["flags"] == ["noout"]
    _, kwargs = conn.put.call_args
    assert kwargs["json"]["flags"] == ["noout", "noscrub"]


@pytest.mark.unit
def test_set_cluster_flag_disable_discards():
    from ceph_aiops.ops import osd as ops

    conn = MagicMock(name="conn")
    conn.get.return_value = {"flags": ["noout", "noscrub"]}
    conn.put.return_value = {}
    ops.set_cluster_flag(conn, "noout", enable=False)
    _, kwargs = conn.put.call_args
    assert kwargs["json"]["flags"] == ["noscrub"]


@pytest.mark.unit
def test_osd_reweight_captures_prior_weight_and_posts():
    from ceph_aiops.ops import osd as ops

    conn = MagicMock(name="conn")
    conn.get.return_value = _osd_rows()
    conn.post.return_value = {}
    out = ops.osd_reweight(conn, 0, 0.5)
    assert out["priorState"]["weight"] == 1.0
    assert out["weight"] == 0.5
    conn.post.assert_called_once_with(f"{_OSD}/0/reweight", json={"weight": 0.5})


@pytest.mark.unit
def test_mark_osd_captures_prior_up_in():
    from ceph_aiops.ops import osd as ops

    conn = MagicMock(name="conn")
    conn.get.return_value = _osd_rows()
    conn.post.return_value = {}
    out = ops.mark_osd(conn, 0, "out")
    assert out["action"] == "mark_osd_out"
    assert out["priorState"] == {"in": True, "up": True}
    conn.post.assert_called_once_with(f"{_OSD}/0/mark", json={"action": "out"})


@pytest.mark.unit
def test_osd_purge_captures_prior_and_deletes():
    from ceph_aiops.ops import osd as ops

    conn = MagicMock(name="conn")
    conn.get.return_value = _osd_rows()
    conn.delete.return_value = {}
    out = ops.osd_purge(conn, 0)
    assert out["action"] == "osd_purge"
    assert out["priorState"]["host"] == "node1"
    conn.delete.assert_called_once_with(f"{_OSD}/0")


@pytest.mark.unit
def test_osd_raw_raises_keyerror_for_missing_osd():
    from ceph_aiops.ops import osd as ops

    conn = MagicMock(name="conn")
    conn.get.return_value = _osd_rows()
    with pytest.raises(KeyError):
        ops._osd_raw(conn, 999)


# ── MCP twins ──────────────────────────────────────────────────────────────


@pytest.mark.unit
def test_osd_write_tools_have_correct_risk_tiers():
    from mcp_server.tools import osd as o

    assert o.osd_mark_out._risk_level == "high"
    assert o.osd_purge._risk_level == "high"
    assert o.osd_reweight._risk_level == "medium"
    assert o.osd_mark_in._risk_level == "medium"
    assert o.cluster_flag_set._risk_level == "medium"  # a write (non-low risk_level)


@pytest.mark.unit
def test_osd_mark_out_dry_run_does_not_mutate(monkeypatch):
    from mcp_server.tools import osd as o

    conn = MagicMock(name="conn")
    monkeypatch.setattr(o, "_get_connection", lambda target=None: conn)
    out = o.osd_mark_out(osd_id=3, dry_run=True)
    assert out["dryRun"] is True
    assert out["wouldMarkOut"]["osdId"] == 3
    conn.post.assert_not_called()


@pytest.mark.unit
def test_osd_purge_dry_run_does_not_mutate(monkeypatch):
    from mcp_server.tools import osd as o

    conn = MagicMock(name="conn")
    monkeypatch.setattr(o, "_get_connection", lambda target=None: conn)
    out = o.osd_purge(osd_id=3, dry_run=True)
    assert out["dryRun"] is True
    assert out["wouldPurge"]["osdId"] == 3
    conn.delete.assert_not_called()


@pytest.mark.unit
def test_osd_reweight_dry_run_reads_current_weight_without_posting(monkeypatch):
    from mcp_server.tools import osd as o

    conn = MagicMock(name="conn")
    conn.get.return_value = _osd_rows()  # OSD 0 has weight 1.0
    monkeypatch.setattr(o, "_get_connection", lambda target=None: conn)
    out = o.osd_reweight(osd_id=0, weight=0.3, dry_run=True)
    assert out["dryRun"] is True
    assert out["wouldReweight"] == {"osdId": 0, "currentWeight": 1.0, "targetWeight": 0.3}
    conn.get.assert_called()  # it read the OSD map to capture the current weight
    conn.post.assert_not_called()


@pytest.mark.unit
def test_osd_reweight_records_prior_state_undo(monkeypatch):
    import ceph_aiops.governance.undo as undo_mod
    from mcp_server.tools import osd as o

    conn = MagicMock(name="conn")
    conn.get.return_value = _osd_rows()
    conn.post.return_value = {}
    monkeypatch.setattr(o, "_get_connection", lambda target=None: conn)

    recorded = {}

    class _Store:
        def record(self, *, skill, tool, undo_descriptor, orig_params, effect_verified=True):
            recorded["d"] = undo_descriptor
            return "undo-osd-1"

    monkeypatch.setattr(undo_mod, "get_undo_store", lambda: _Store())

    out = o.osd_reweight(osd_id=0, weight=0.3)
    assert out["priorState"]["weight"] == 1.0
    d = recorded["d"]
    assert d["tool"] == "osd_reweight"
    assert d["params"]["weight"] == 1.0  # restore prior weight
    assert out.get("_undo_id") == "undo-osd-1"


@pytest.mark.unit
def test_flag_undo_toggles_back():
    from mcp_server.tools import osd as o

    undo = o._flag_undo({"flag": "noout", "enable": True}, {"action": "set_cluster_flag"})
    assert undo["tool"] == "cluster_flag_set"
    assert undo["params"] == {"flag": "noout", "enable": False}


@pytest.mark.unit
def test_mark_in_undo_only_when_was_out():
    from mcp_server.tools import osd as o

    # OSD was out before → offer mark-out undo.
    undo = o._mark_in_undo({"osd_id": 4}, {"priorState": {"in": False}})
    assert undo["tool"] == "osd_mark_out"
    assert undo["params"]["osd_id"] == 4
    # OSD was already in → no undo offered.
    assert o._mark_in_undo({"osd_id": 4}, {"priorState": {"in": True}}) is None


@pytest.mark.unit
def test_reweight_undo_none_without_prior():
    from mcp_server.tools import osd as o

    assert o._reweight_undo({"osd_id": 1}, {"priorState": {}}) is None
    assert o._reweight_undo({"osd_id": 1}, "not-a-dict") is None


@pytest.mark.unit
def test_crush_weight_falls_back_only_when_the_key_is_truly_absent():
    """``.get(k, default)`` does NOT fire its default when the key exists with a
    null value — the trap this line has hit repeatedly. A build that sends
    ``crush_weight: null`` at the top level while carrying the real number in
    ``tree`` must report the real number, and an explicit ``0`` (falsy but real)
    must survive rather than be overridden by the tree.
    """
    from ceph_aiops.ops.osd import _norm_osd

    assert _norm_osd({"crush_weight": None,
                      "tree": {"crush_weight": 1.25}})["crushWeight"] == 1.25
    assert _norm_osd({"crush_weight": 0.5,
                      "tree": {"crush_weight": 9}})["crushWeight"] == 0.5
    assert _norm_osd({"tree": {"crush_weight": 1.25}})["crushWeight"] == 1.25
    assert _norm_osd({})["crushWeight"] is None
    # 0 is a real CRUSH weight (a drained OSD), not a missing one.
    assert _norm_osd({"crush_weight": 0,
                      "tree": {"crush_weight": 9}})["crushWeight"] == 0
