"""Absent fields come back as null, not as an empty string.

An empty string reads as "the Dashboard returned this field and it was empty";
a missing field is a different fact. Collapsing the two hides information from
any consumer, and a smaller local model will confidently invent the difference.
These tests pin the contract end-to-end: helper, ops layer, and the truncation
envelope that has to survive a null field in a row.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from ceph_aiops.governance import opt_str
from ceph_aiops.ops import filesystem as fs
from ceph_aiops.ops import osd as osd_ops
from ceph_aiops.ops import pg as pg_ops
from ceph_aiops.ops._util import opt_s, s


@pytest.mark.unit
def test_opt_str_distinguishes_absent_from_empty():
    assert opt_str(None) is None, "absent must stay absent"
    assert opt_str("") == "", "a genuinely empty value is not the same as absent"
    assert opt_str("osd.3", 64) == "osd.3"


@pytest.mark.unit
def test_opt_str_still_sanitizes_and_truncates():
    assert opt_str("a\x00b") == "ab"  # control character stripped
    # A cut announces itself: the ellipsis is the only signal a reader gets
    # that what they are looking at is not the whole value.
    assert opt_str("abcdef", 3) == "ab\u2026"
    assert opt_str("abc", 3) == "abc"  # exactly at the cap is not truncated


@pytest.mark.unit
def test_opt_str_accepts_non_string_values():
    assert opt_str(42) == "42"


@pytest.mark.unit
def test_opt_s_and_s_differ_only_on_absence():
    """The two ceph helpers agree on real values and disagree only on None."""
    assert s(None) == "" and opt_s(None) is None
    assert s("hdd") == opt_s("hdd") == "hdd"


@pytest.mark.unit
def test_osd_rows_report_absent_fields_as_none():
    """An OSD record with no host/device_class reports null, not ''."""
    conn = MagicMock()
    conn.get.return_value = [{"osd": 0, "up": 1, "in": 1}]
    row = osd_ops.osd_tree(conn)[0]
    assert row["id"] == 0
    assert row["host"] is None
    assert row["deviceClass"] is None


@pytest.mark.unit
def test_osd_rows_keep_empty_string_when_source_is_empty():
    """An explicitly empty upstream value is preserved as '' — not turned into null."""
    conn = MagicMock()
    conn.get.return_value = [{"osd": 0, "device_class": ""}]
    assert osd_ops.osd_tree(conn)[0]["deviceClass"] == ""


@pytest.mark.unit
def test_ops_never_drop_the_key_itself():
    """Keys are always present; only their value may be null.

    Omitting a key entirely is worse than a null — the consumer cannot tell the
    field was even considered.
    """
    conn = MagicMock()
    conn.get.return_value = [{}]
    row = osd_ops.osd_tree(conn)[0]
    for key in ("id", "up", "in", "weight", "crushWeight", "usedPercent",
                "host", "deviceClass"):
        assert key in row, f"{key} must be present even when the source omitted it"


@pytest.mark.unit
def test_mds_rank_state_is_none_when_absent():
    conn = MagicMock()
    conn.get.return_value = [{"mdsmap": {"info": {"gid_1": {"rank": 0}}}}]
    out = fs.cephfs_status(conn)
    rank = out["filesystems"][0]["mdsRanks"][0]
    assert rank["rank"] == 0
    assert rank["state"] is None
    assert out["filesystems"][0]["fsName"] is None


@pytest.mark.unit
def test_scrub_status_survives_a_pg_with_no_state():
    """A None state must not blow up the substring matching in scrub_status.

    This is the consumer that a naive absent-to-null conversion breaks: it does
    ``"not scrubbed" in state`` and a null state raises TypeError without a guard.
    """
    conn = MagicMock()
    conn.get.return_value = {"pg_info": {"pgs": [{"pgid": "2.0"}]}}  # no state key
    out = pg_ops.scrub_status(conn)
    assert out == {"overdueScrub": [], "overdueDeepScrub": []}


@pytest.mark.unit
def test_pg_summary_skips_pgs_with_no_state():
    conn = MagicMock()
    conn.get.return_value = {"pg_info": {"pgs": [{"pgid": "2.0"}]}}
    out = pg_ops.pg_summary(conn)
    assert out["states"] == {} and out["unhealthyCount"] == 0


# ── truncation announces itself ──────────────────────────────────────────


def _many_stuck(n: int) -> dict:
    return {"pg_info": {"pgs": [
        {"pgid": f"2.{i}", "state": "stale+peering", "up": [i]} for i in range(n)
    ]}}


@pytest.mark.unit
def test_pg_dump_stuck_returns_a_truncation_envelope():
    conn = MagicMock()
    conn.get.return_value = _many_stuck(5)
    out = pg_ops.pg_dump_stuck(conn, limit=2)
    assert out["returned"] == 2 and out["limit"] == 2
    assert out["truncated"] is True, "more rows existed than were returned"
    assert len(out["stuck"]) == 2


@pytest.mark.unit
def test_pg_dump_stuck_is_not_truncated_at_exactly_the_limit():
    """The boundary case a length-comparison heuristic gets wrong.

    Exactly ``limit`` rows is NOT truncation. Measuring one row past the limit
    is what makes this answerable instead of guessed.
    """
    conn = MagicMock()
    conn.get.return_value = _many_stuck(2)
    out = pg_ops.pg_dump_stuck(conn, limit=2)
    assert out["returned"] == 2 and out["truncated"] is False


@pytest.mark.unit
def test_pg_summary_truncates_the_unhealthy_list_but_keeps_the_true_count():
    conn = MagicMock()
    conn.get.return_value = _many_stuck(5)
    out = pg_ops.pg_summary(conn, limit=2)
    assert out["unhealthyCount"] == 5, "the histogram total must stay truthful"
    assert out["returned"] == 2 and out["truncated"] is True
    assert len(out["unhealthy"]) == 2


@pytest.mark.unit
def test_undo_list_envelope_measures_truncation(monkeypatch):
    from mcp_server.tools import undo as undo_tools

    rows = [
        {
            "undo_id": f"u{i}",
            "ts": "2026-07-18T00:00:00Z",
            "tool": "some_tool",
            "undo_tool": "some_inverse_tool",
            "note": "",
        }
        for i in range(4)
    ]
    captured = {}

    class _Store:
        def list(self, *, status=None, limit=50):
            captured["limit"] = limit
            return rows[:limit]

    monkeypatch.setattr(undo_tools, "get_undo_store", lambda: _Store())
    result = undo_tools.undo_list(limit=3)
    assert captured["limit"] == 4, "one extra row is fetched to measure truncation"
    assert result["returned"] == 3
    assert result["limit"] == 3
    assert result["truncated"] is True
    assert len(result["undos"]) == 3
