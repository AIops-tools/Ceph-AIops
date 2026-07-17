"""CLI secret-store command tests (encrypted store redirected to a tmp home).

Proves: `secret set` stores an encrypted key (never plaintext on disk), `secret
list` shows the stored name (never the value), `secret rm` deletes it, and the
list command reports the empty state. Uses the isolated_home fixture so nothing
touches the real ~/.ceph-aiops and the master password is supplied via env.
"""

import pytest
from typer.testing import CliRunner

runner = CliRunner()


@pytest.mark.unit
def test_secret_set_list_rm_roundtrip(isolated_home):
    from ceph_aiops.cli import app

    set_res = runner.invoke(app, ["secret", "set", "lab1", "--value", "s3cr3t-key"])
    assert set_res.exit_code == 0, set_res.output
    assert "Stored encrypted" in set_res.output

    # The value must not be on disk in plaintext.
    blob = (isolated_home / "secrets.enc").read_text()
    assert "s3cr3t-key" not in blob

    list_res = runner.invoke(app, ["secret", "list"])
    assert list_res.exit_code == 0, list_res.output
    assert "lab1" in list_res.output
    assert "s3cr3t-key" not in list_res.output

    rm_res = runner.invoke(app, ["secret", "rm", "lab1"])
    assert rm_res.exit_code == 0, rm_res.output

    empty = runner.invoke(app, ["secret", "list"])
    assert empty.exit_code == 0, empty.output
    assert "No secrets stored" in empty.output


@pytest.mark.unit
def test_secret_migrate_nothing_to_do(isolated_home):
    from ceph_aiops.cli import app

    # No legacy .env present → migrate reports nothing to do (exit 0).
    result = runner.invoke(app, ["secret", "migrate"])
    assert result.exit_code == 0, result.output
    assert "Nothing to migrate" in result.output
