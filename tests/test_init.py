"""Tests for the ``ceph-aiops init`` wizard.

Driven through typer's CliRunner against an ``isolated_home`` (see conftest);
the master password comes from ``CEPH_AIOPS_MASTER_PASSWORD`` and the hidden
per-target password prompt is fed by patching ``getpass``. The trailing doctor
run is either declined via stdin or patched out.
"""

from __future__ import annotations

import pytest
import yaml
from typer.testing import CliRunner

import ceph_aiops.cli.init as init_mod
import ceph_aiops.secretstore as ss
from ceph_aiops.cli._root import app
from tests.conftest import MASTER_PW

pytestmark = pytest.mark.unit

runner = CliRunner()

MGR_PASSWORD = "mgr-secret-123"  # noqa: S105 — test fixture value

# Prompt order: name, host, port(default), username(default),
# TLS confirm(default=True), [getpass patched], add-another(No), doctor(No).
WIZARD_INPUT = "lab1\nceph.example.com\n\n\n\n\nn\n"


@pytest.fixture
def hidden_password(monkeypatch: pytest.MonkeyPatch) -> None:
    """getpass reads the TTY, not CliRunner stdin — patch it."""
    monkeypatch.setattr(init_mod.getpass, "getpass", lambda prompt="": MGR_PASSWORD)


def test_init_writes_config_and_encrypted_secret(isolated_home, hidden_password):
    result = runner.invoke(app, ["init"], input=WIZARD_INPUT)
    assert result.exit_code == 0, result.output

    config_text = (isolated_home / "config.yaml").read_text("utf-8")
    raw = yaml.safe_load(config_text)
    assert raw["targets"] == [
        {
            "name": "lab1",
            "host": "ceph.example.com",
            "port": 8443,
            "username": "admin",
            "verify_ssl": True,  # TLS confirm default=True accepted as-is
        }
    ]

    # The secret lands encrypted in secrets.enc, never in config.yaml.
    secrets_blob = (isolated_home / "secrets.enc").read_text("utf-8")
    assert MGR_PASSWORD not in config_text
    assert MGR_PASSWORD not in secrets_blob
    assert ss.SecretStore.unlock(MASTER_PW).get("lab1") == MGR_PASSWORD


def test_init_seeds_default_policy_rules(isolated_home, hidden_password):
    result = runner.invoke(app, ["init"], input=WIZARD_INPUT)
    assert result.exit_code == 0, result.output
    rules = (isolated_home / "rules.yaml").read_text("utf-8")
    assert "high-risk-requires-approver" in rules
    assert "tier: dual" in rules


def test_init_does_not_clobber_existing_rules(isolated_home, hidden_password):
    sentinel = "# operator-authored rules — do not touch\nrisk_tiers: []\n"
    (isolated_home / "rules.yaml").write_text(sentinel, "utf-8")
    result = runner.invoke(app, ["init"], input=WIZARD_INPUT)
    assert result.exit_code == 0, result.output
    assert (isolated_home / "rules.yaml").read_text("utf-8") == sentinel


def test_init_declines_tls_verification(isolated_home, hidden_password):
    # Same script but answer No at the TLS confirm.
    result = runner.invoke(app, ["init"], input="lab1\nceph.example.com\n\n\nn\n\nn\n")
    assert result.exit_code == 0, result.output
    raw = yaml.safe_load((isolated_home / "config.yaml").read_text("utf-8"))
    assert raw["targets"][0]["verify_ssl"] is False


def test_init_appends_to_existing_targets(isolated_home, hidden_password):
    assert runner.invoke(app, ["init"], input=WIZARD_INPUT).exit_code == 0
    result = runner.invoke(app, ["init"], input="lab2\nceph2.example.com\n\n\n\n\nn\n")
    assert result.exit_code == 0, result.output
    raw = yaml.safe_load((isolated_home / "config.yaml").read_text("utf-8"))
    assert [t["name"] for t in raw["targets"]] == ["lab1", "lab2"]


def test_init_overwrites_target_on_confirm(isolated_home, hidden_password):
    assert runner.invoke(app, ["init"], input=WIZARD_INPUT).exit_code == 0
    # Re-add 'lab1': confirm the overwrite, change the host.
    result = runner.invoke(app, ["init"], input="lab1\ny\nnew-ceph.example.com\n\n\n\n\nn\n")
    assert result.exit_code == 0, result.output
    raw = yaml.safe_load((isolated_home / "config.yaml").read_text("utf-8"))
    assert len(raw["targets"]) == 1
    assert raw["targets"][0]["host"] == "new-ceph.example.com"


def test_init_runs_doctor_when_accepted(isolated_home, hidden_password, monkeypatch):
    import ceph_aiops.doctor as doc

    calls: list[bool] = []

    def fake_doctor(skip_auth: bool = False) -> int:
        calls.append(True)
        return 0

    monkeypatch.setattr(doc, "run_doctor", fake_doctor)
    # Accept the trailing doctor confirm (default=True) with a blank line.
    result = runner.invoke(app, ["init"], input="lab1\nceph.example.com\n\n\n\n\n\n")
    assert result.exit_code == 0, result.output
    assert calls == [True]
