"""Configuration management for Ceph AIops.

Loads Ceph mgr (Dashboard REST) connection targets from a YAML config file. The
secret (the Ceph Dashboard account **password**) is NEVER stored in the config
file and never on disk in plaintext: it lives in the encrypted store
``~/.ceph-aiops/secrets.enc`` (see :mod:`ceph_aiops.secretstore`). For backward
compatibility a legacy plaintext env var (``CEPH_<TARGET>_PASSWORD``) is still
honoured as a fallback, with a warning nudging migration to the encrypted store.

A "target" is one Ceph cluster reached through its **ceph-mgr Dashboard REST
API** (default HTTPS port ``8443``). Auth is username + password exchanged for a
short-lived JWT at ``POST /api/auth`` (handled in
:mod:`ceph_aiops.connection`). ``username`` lives in the config file (it is not a
secret); the password lives encrypted.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from pathlib import Path

import yaml

from ceph_aiops.secretstore import SecretStoreError, get_secret, has_store

CONFIG_DIR = Path.home() / ".ceph-aiops"
CONFIG_FILE = CONFIG_DIR / "config.yaml"
ENV_FILE = CONFIG_DIR / ".env"

# Ceph mgr Dashboard defaults.
DEFAULT_MGR_PORT = 8443
DEFAULT_USERNAME = "admin"

# Legacy env-var prefix/suffix; also used by the migration helper.
SECRET_ENV_PREFIX = "CEPH_"  # nosec B105 — env-var name, not a secret
SECRET_ENV_SUFFIX = "_PASSWORD"  # nosec B105 — env-var name, not a secret

_log = logging.getLogger("ceph-aiops.config")


def _secret_env_key(name: str) -> str:
    """Legacy per-target password env var name, e.g. CEPH_LAB1_PASSWORD."""
    return f"{SECRET_ENV_PREFIX}{name.upper().replace('-', '_')}{SECRET_ENV_SUFFIX}"


def _resolve_secret(name: str) -> str:
    """Return a target's password: encrypted store first, then legacy env var."""
    if has_store():
        try:
            return get_secret(name)
        except SecretStoreError:
            pass  # fall through to legacy env var
    legacy = os.environ.get(_secret_env_key(name))
    if legacy:
        _log.warning(
            "Using plaintext env var %s. Migrate to the encrypted store with "
            "'ceph-aiops secret migrate'.",
            _secret_env_key(name),
        )
        return legacy
    raise OSError(
        f"No password for target '{name}'. Add one with "
        f"'ceph-aiops secret set {name}' (stored encrypted), or run "
        f"'ceph-aiops init'."
    )


@dataclass(frozen=True)
class TargetConfig:
    """A connection target for one Ceph cluster's mgr Dashboard REST API.

    The password is sourced from the encrypted secret store (see ``password``),
    never the config file. ``host`` is the active mgr host/FQDN; ``port``
    defaults to ``8443``; ``username`` (default ``admin``) is the Dashboard
    account and is not a secret.
    """

    name: str
    host: str
    port: int = DEFAULT_MGR_PORT
    username: str = DEFAULT_USERNAME
    verify_ssl: bool = True

    @property
    def password(self) -> str:
        return _resolve_secret(self.name)

    @property
    def base_url(self) -> str:
        return f"https://{self.host}:{self.port}"


@dataclass(frozen=True)
class AppConfig:
    """Top-level application config."""

    targets: tuple[TargetConfig, ...] = ()

    def get_target(self, name: str) -> TargetConfig:
        for t in self.targets:
            if t.name == name:
                return t
        available = ", ".join(t.name for t in self.targets) or "(none)"
        raise KeyError(f"Target '{name}' not found. Available: {available}")

    @property
    def default_target(self) -> TargetConfig:
        if not self.targets:
            raise ValueError("No targets configured. Check config.yaml")
        return self.targets[0]


def load_config(config_path: Path | None = None) -> AppConfig:
    """Load config from YAML; the password comes from the encrypted store."""
    path = config_path or CONFIG_FILE
    if not path.exists():
        raise FileNotFoundError(
            f"Config file not found: {path}\n"
            f"Run 'ceph-aiops init' to set up a Ceph cluster target and store its "
            f"password encrypted, or create {CONFIG_FILE} with a 'targets' list."
        )

    with open(path) as f:
        raw = yaml.safe_load(f) or {}

    targets = tuple(
        TargetConfig(
            name=t["name"],
            host=t["host"],
            port=t.get("port", DEFAULT_MGR_PORT),
            username=t.get("username", DEFAULT_USERNAME),
            verify_ssl=t.get("verify_ssl", True),
        )
        for t in raw.get("targets", [])
    )

    return AppConfig(targets=targets)
