"""Unit tests for the shared ops helpers (_util).

Proves: _seg URL-encodes a path segment (path-traversal defence), as_list
normalises both bare arrays and {data|result: [...]} envelopes (dropping
non-dicts), as_obj coerces non-dicts to {}, s sanitizes/bounds a value, and
bytes_h renders human-readable sizes (and '' for non-numeric).
"""

import pytest


@pytest.mark.unit
def test_seg_encodes_path_traversal():
    from ceph_aiops.ops._util import _seg

    assert _seg("../admin") == "..%2Fadmin"
    assert _seg(5) == "5"
    assert _seg("a b/c") == "a%20b%2Fc"


@pytest.mark.unit
def test_as_list_normalises_shapes():
    from ceph_aiops.ops._util import as_list

    assert as_list([{"a": 1}, "junk", {"b": 2}]) == [{"a": 1}, {"b": 2}]
    assert as_list({"data": [{"x": 1}]}) == [{"x": 1}]
    assert as_list({"result": [{"y": 2}]}) == [{"y": 2}]
    assert as_list(None) == []
    assert as_list({"other": 1}) == []


@pytest.mark.unit
def test_as_obj_coerces_non_dict():
    from ceph_aiops.ops._util import as_obj

    assert as_obj({"a": 1}) == {"a": 1}
    assert as_obj(None) == {}
    assert as_obj([1, 2]) == {}


@pytest.mark.unit
def test_s_sanitizes_and_bounds():
    from ceph_aiops.ops._util import s

    assert s(None) == ""
    assert s(42) == "42"
    assert len(s("x" * 500, 10)) <= 10


@pytest.mark.unit
def test_bytes_h_renders_units():
    from ceph_aiops.ops._util import bytes_h

    assert bytes_h(512) == "512.0B"
    assert bytes_h(1024) == "1.0KiB"
    assert bytes_h(1024 * 1024) == "1.0MiB"
    assert bytes_h(1024 ** 4) == "1.0TiB"
    assert bytes_h("not-a-number") == ""


@pytest.mark.unit
def test_bytes_h_exabyte_overflow():
    from ceph_aiops.ops._util import bytes_h

    assert bytes_h(1024 ** 7).endswith("EiB")
