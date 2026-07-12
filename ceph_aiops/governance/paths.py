"""Home-directory resolution for the governance harness.

State lives under ``ops_home()`` — by default ``~/.ceph-aiops``, overridable
via the ``CEPH_AIOPS_HOME`` environment variable so an operator can relocate
the audit / policy / budget / undo store.
"""

from __future__ import annotations

import os
from pathlib import Path

_DEFAULT_HOME = "~/.ceph-aiops"


def ops_home() -> Path:
    """Return the harness state directory, honoring ``CEPH_AIOPS_HOME``."""
    return Path(os.environ.get("CEPH_AIOPS_HOME") or _DEFAULT_HOME).expanduser()


def ops_path(*parts: str) -> Path:
    """Resolve a file under the harness home, e.g. ``ops_path('audit.db')``."""
    return ops_home().joinpath(*parts)
