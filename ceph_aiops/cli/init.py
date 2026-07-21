"""``ceph-aiops init`` — a friendly, interactive onboarding wizard.

Walks a new user through connecting their first Ceph target: collects
the non-secret connection details into ``config.yaml`` and the Dashboard
password into the *encrypted* store (never plaintext on disk). Designed to be
run on a terminal; everything it needs is prompted with sensible defaults.
"""

from __future__ import annotations

import getpass

import typer
import yaml

from ceph_aiops.cli._common import cli_errors, console
from ceph_aiops.config import CONFIG_DIR, CONFIG_FILE, DEFAULT_MGR_PORT, DEFAULT_USERNAME
from ceph_aiops.secretstore import SecretStore, resolve_master_password


def _load_existing_targets() -> list[dict]:
    if not CONFIG_FILE.exists():
        return []
    raw = yaml.safe_load(CONFIG_FILE.read_text("utf-8")) or {}
    return list(raw.get("targets", []))


def _write_targets(targets: list[dict]) -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    try:
        CONFIG_DIR.chmod(0o700)
    except OSError:
        pass
    CONFIG_FILE.write_text(yaml.safe_dump({"targets": targets}, sort_keys=False), "utf-8")


@cli_errors
def init_cmd() -> None:
    """Interactively set up your first Ceph connection."""
    console.print("[bold cyan]Ceph AIops — setup wizard[/]")
    console.print(
        "This collects ceph-mgr Dashboard connection details (saved to "
        "config.yaml) and your Dashboard password (saved [bold]encrypted[/] to "
        "secrets.enc).\n"
    )

    console.print("[bold]Step 1 — master password[/]")
    console.print(
        "[dim]Encrypts secrets.enc. You'll set it via the "
        "CEPH_AIOPS_MASTER_PASSWORD env var for non-interactive/MCP use.[/]"
    )
    password = resolve_master_password(confirm_if_new=True)
    store = SecretStore.unlock(password)

    targets = _load_existing_targets()
    existing_names = {t.get("name") for t in targets}

    while True:
        console.print("\n[bold]Step 2 — add a Ceph cluster[/]")
        name = typer.prompt("Target name (e.g. lab1)").strip()
        if name in existing_names:
            if not typer.confirm(f"'{name}' already exists — overwrite?", default=False):
                continue
            targets = [t for t in targets if t.get("name") != name]

        host = typer.prompt("Host (IP or FQDN of the active ceph-mgr)").strip()
        port = typer.prompt("Dashboard HTTPS port", default=DEFAULT_MGR_PORT, type=int)
        username = typer.prompt("Dashboard username", default=DEFAULT_USERNAME).strip()
        console.print("[dim]Lab / self-signed mgr certificates: answer No here.[/]")
        verify_ssl = typer.confirm(
            "Verify TLS certificate? (No for self-signed lab certs)", default=True
        )

        console.print(
            "[dim]Enter the ceph-mgr Dashboard password for this account "
            "(input hidden). The mgr 'dashboard' module must be enabled.[/]"
        )
        secret = getpass.getpass(f"Password for '{username}@{name}' (hidden): ")
        store = store.set(name, secret)

        entry = {
            "name": name,
            "host": host,
            "port": port,
            "username": username,
            "verify_ssl": verify_ssl,
        }
        targets.append(entry)
        existing_names.add(name)
        _write_targets(targets)
        console.print(f"[green]✓ Saved target '{name}' (password stored encrypted).[/]")

        if not typer.confirm("\nAdd another target?", default=False):
            break

    console.print(f"\n[green]✓ Setup complete.[/] Config: {CONFIG_FILE}")
    console.print(
        "[dim]Tip: export CEPH_AIOPS_MASTER_PASSWORD=... in your shell profile "
        "so the MCP server and CLI can unlock secrets non-interactively.[/]"
    )
    if typer.confirm("Run a connectivity check now (ceph-aiops doctor)?", default=True):
        from ceph_aiops.doctor import run_doctor

        raise typer.Exit(run_doctor())
