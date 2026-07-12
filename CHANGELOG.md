# Changelog

All notable changes to ceph-aiops are documented here. This project adheres
to [Semantic Versioning](https://semver.org/).

## [0.1.0] — preview

Initial preview release: governed AI-ops for **Ceph** via the ceph-mgr Dashboard
REST API, with a bundled governance harness. Works against vanilla ceph-mgr
(cephadm / Proxmox-hosted / MicroCeph) — no croit, no Kubernetes.
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
