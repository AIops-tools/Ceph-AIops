# Changelog

## Unreleased — 2026-08-02

### Changed (BREAKING)
- **Requires MCP SDK 2.0** (`mcp[cli]>=2.0,<3.0`). `mcp.server.fastmcp` no longer exists in 2.0; the server is now built with `MCPServer` and reports its package version in the stdio handshake.

### Fixed
- **`undo apply` works from the CLI.** Every write tool is imported lazily inside its own CLI command, so a CLI-driven undo ran in a process where the inverse tool was never registered and failed with "inverse tool is not registered" — for every write tool. Only the MCP entry point, which imports the whole server, worked. Found while live-verifying against a real cluster.
- **An undetermined outcome is audited `unknown`, not `ok`.** The harness only classified a result as undetermined when the payload *also* carried an `error` key, so a write that looked successful but had not been confirmed was recorded as a success.
- **OSD `host` leaked a Python dict repr and `crushWeight` was always null.** Live-verified against Ceph 18 (reef): `/api/osd` returns `host` as a CRUSH bucket *dict*, not a string, and `crush_weight` is nested under `tree`. Both now read the real shape.
- `crushWeight` falls back to the tree only when the key is truly **absent**. `.get(k, default)` does not fire its default for a key that exists with a null value, so a build sending `crush_weight: null` at the top level would have reported null while the real number sat in `tree`. Uses `is not None` rather than truthiness, so an explicit `0` — a real CRUSH weight for a draining OSD — survives.


## v0.7.0 — 2026-07-21

### Changed
- CLI `--dry-run` previews for the remaining write commands now route through the governed twin (run the guards, land an audit row) instead of a static unaudited banner.

See RELEASE_NOTES.md for detail.


## v0.6.0 — 2026-07-21

### Changed (BREAKING)
- **Removed the authorization layer** — read-only mode, the approver gate, and rules.yaml deny are gone. The skill no longer decides read vs write; that is the agent's judgement or the connecting account's permissions. `<PREFIX>_READ_ONLY` now has no effect (a startup warning is logged); `<PREFIX>_AUDIT_APPROVED_BY`/`_RATIONALE` are optional audit annotations.
- The retained guarantee is **unbypassable audit over MCP and CLI alike** — no unaudited entry point. Harness = audit + runaway safety guard + undo + sanitize; `risk_level` is a descriptive audit label, not a gate.

See RELEASE_NOTES.md for tool-specific changes.


## v0.5.0 — 2026-07-20

### Fixed
- **`delete_pool` refuses `.mgr` and `.rgw.root`**, and any pool whose application metadata marks it a mgr pool.
- **New `scheme:`** (default `https`) — the base URL was hardcoded..
- Harness: a write whose response is lost is audited `status=unknown`, not `error` — it may have taken effect. Undo tokens gain `effectVerified` (undo.db migrated in place).
- Harness: a dry-run no longer records an undo token, and no longer requires a named approver. Guards now run on the preview path.
- Truncated strings end in an ellipsis instead of being cut silently; error messages are capped at 800 chars, not 300.

See RELEASE_NOTES.md for the full detail.

## v0.3.0 — 2026-07-17

### Added
- **Undo executor**: `undo list` / `undo apply <id>` (CLI + MCP) — apply a recorded replayable inverse; the dispatched inverse is re-gated by its own risk tier; single-use, dry-run, double-confirm, both wrapper + inverse audited.
- Coverage: ops/CLI/connection layers now near-fully tested.

## v0.2.1 — 2026-07-16

### Fixed
- **`secrets.enc` now follows `CEPH_AIOPS_HOME`** (secretstore hardcoded the real
  home directory; config/audit/undo already relocated — found in live verification).
- **Audit fidelity**: failures sanitized into `{"error": ...}` results by the MCP error
  layer are now audited as `status=error` (they previously read as `ok`, hiding failed
  attempts from exception reports), and no undo is recorded for a call that failed.
- **doctor crash fixed**: it referenced a nonexistent `api_key` field and crashed on any configured target.

### Tests
- `doctor` and the `init` wizard are now fully covered (previously ~10–20%); plus a
  regression test for the sanitized-failure audit status.

## v0.2.0 — 2026-07-13

Security-hardening release from a line-wide code review.

### Changed (behavior)
- **Secure by default**: with no `rules.yaml`, high/critical operations now require a
  named approver (`CEPH_AUDIT_APPROVED_BY`). A fresh install no longer allows
  destructive writes unattended; `init` seeds a starter `rules.yaml` you can edit,
  and an operator-authored rules file is honoured as-is.
- `__version__` is now single-sourced from package metadata (the previous release
  self-reported a stale version string).
- Sanitize docs no longer overstate scope: it strips control/format characters and
  truncates; semantic prompt-injection resistance must come from the consuming agent.

### Fixed
- Agent-supplied ids are percent-encoded in REST URL paths (path-traversal hardening, 20 sites).
- `init` TLS verification prompt now defaults to ON.
- Docs wording: deployment flavors now vendor-neutral ("hypervisor-bundled Ceph").
- Cached HTTP connections are closed at process exit.

### Tests
- Governance persistence is now tested against REAL `audit.db`/`undo.db` files
  (write → audit row + inverse undo row with captured prior state).
- The CLI confirmed-write path (dry-run / double-confirm / governed execution) is
  covered end-to-end.
- `pytest-cov` added to the dev dependencies.

## v0.1.1

- Fix: `CEPH_AIOPS_HOME` now also relocates `config.yaml` (was hardcoded to `~/.ceph-aiops`).
- Fix: **CLI writes are now audited + undo-recorded** via the governance path — previously only the MCP tools recorded audit/undo; CLI `manage`/`remediate`/etc. writes now go through the same `@governed_tool` layer (they keep their dry-run + double-confirm). CLI write output is now the governed JSON result. No API/tool changes.


All notable changes to ceph-aiops are documented here. This project adheres
to [Semantic Versioning](https://semver.org/).

## [0.1.0] — preview

Initial preview release: governed AI-ops for **Ceph** via the ceph-mgr Dashboard
REST API, with a bundled governance harness. Works against vanilla ceph-mgr
(cephadm / hypervisor-bundled / MicroCeph) — no croit, no Kubernetes.
**Mock-validated only — not yet verified against a live cluster.**

### Added

- **35 MCP tools** (17 read, 18 write), every one wrapped with the bundled
  `@governed_tool` harness (audit, policy, token/runaway budget, undo,
  risk-tiers):
  - **Health** — `cluster_health` (flagship RCA: per active HEALTH_WARN/ERR
    check → plain-language cause + suggested action), `cluster_status`
    (`ceph -s` summary).
  - **OSD** — `osd_tree`, `osd_df` (most-full first + near/backfill-full flags),
    `osd_perf` (slowest first); `cluster_flag_set` (low, undo — noout/noscrub/
    nobackfill/norecover), `osd_reweight` (med, undo → prior weight; 0.0=drain),
    `osd_mark_in` (med, undo), `osd_mark_out` (high, dry-run — drains data),
    `osd_purge` (high, dry-run — irreversible).
  - **PG** — `pg_summary` (state histogram + non-active+clean), `pg_dump_stuck`
    (stuck PGs + implicated OSDs), `scrub_status` (overdue scrub/deep-scrub);
    `trigger_scrub` (low), `trigger_deep_scrub` (low).
  - **Pool** — `pool_ls`, `pool_df` (usable capacity = raw ÷ size);
    `set_pool_quota` (med, undo), `set_pool_pg_num` (med, undo),
    `set_pool_autoscale` (med, undo), `pool_create` (med), `set_pool_size`
    (high, dry-run — replica change forces data movement), `pool_delete`
    (high, dry-run — destroys all data).
  - **RBD** — `rbd_ls`; `rbd_image_create` (med), `rbd_snapshot_create` (low),
    `rbd_image_delete` (high, dry-run), `rbd_snapshot_delete` (high, dry-run).
  - **CephFS / RGW** — `cephfs_status` (MDS ranks + "behind on trimming" +
    client count), `rgw_status` (daemons + buckets + LARGE_OMAP /
    unsharded-index findings).
  - **Cluster-ops** — `mon_status` (quorum / out-of-quorum), `mgr_status`
    (active/standbys/modules), `slow_ops` (blocked requests by OSD),
    `capacity_forecast` (days-to-nearfull); `throttle_recovery` (med, undo —
    `osd_max_backfills` / `osd_recovery_max_active`).
- **JWT authentication** — username + password exchanged for a short-lived JWT
  at `POST /api/auth` against the mgr Dashboard (`https://<host>:8443`); the mgr
  **dashboard** module must be enabled.
- **Encrypted secret store** — the Dashboard password is stored encrypted in
  `~/.ceph-aiops/secrets.enc` (Fernet + scrypt); never plaintext on disk. Legacy
  `CEPH_<TARGET>_PASSWORD` env var honoured as a fallback.
- **CLI** (`ceph-aiops`) — `init` wizard, `secret` management, `doctor`,
  `overview`, and the `health` / `osd` sub-commands.
- **Teaching connection layer** — JWT login with centralised, human-readable
  error translation (e.g. dashboard-module-not-enabled, auth failure).

### Known limitations

- Preview / mock-only: multi-node rebalance behaviour and the write ops are
  unverified against a real Ceph cluster. Fastest live check: a single-node
  MicroCeph running `ceph-aiops doctor`.
- The ceph-mgr Dashboard API has no ETag / pagination, so this tool exposes none.
- Out of scope by design (v0.1.0): RGW multisite, NFS-Ganesha exports, and
  cephadm orchestrator host management.
