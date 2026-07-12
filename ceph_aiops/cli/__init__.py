"""CLI package for ceph-aiops.

Re-exports ``app`` so the pyproject entry point
``ceph-aiops = "ceph_aiops.cli:app"`` works unchanged.
"""

from ceph_aiops.cli._root import app

__all__ = ["app"]
