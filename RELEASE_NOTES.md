# Ceph AIops v0.1.0 — preview

Governed AI-ops for **Ceph** via the **ceph-mgr Dashboard REST API** for AI
agents, with a built-in governance harness (audit, policy, token/runaway
budget, undo-token recording, graduated risk tiers) and an encrypted credential
store. Standalone — no external skill-family dependency. Works against vanilla
ceph-mgr (cephadm / Proxmox-hosted / MicroCeph) — no croit, no Kubernetes.

> **Preview / mock-only.** All behaviour is validated against mocked Dashboard
> REST responses; it has not been run against a live Ceph cluster. The fastest
> live check is a single-node MicroCeph running `ceph-aiops doctor`.

## Highlights

- **35 MCP tools** (17 read, 18 write), every one wrapped with `@governed_tool`:
  - **Health** — `cluster_health` (flagship: per active HEALTH_WARN/ERR check →
    plain-language cause + suggested action), `cluster_status`.
  - **OSD** — `osd_tree`, `osd_df` (most-full first + near/backfill-full flags),
    `osd_perf`; writes `cluster_flag_set`, `osd_reweight`, `osd_mark_in`,
    `osd_mark_out` (high), `osd_purge` (high).
  - **PG** — `pg_summary`, `pg_dump_stuck`, `scrub_status`; `trigger_scrub`,
    `trigger_deep_scrub`.
  - **Pool** — `pool_ls`, `pool_df` (usable capacity = raw ÷ size); writes
    `set_pool_quota`, `set_pool_pg_num`, `set_pool_autoscale`, `pool_create`,
    `set_pool_size` (high), `pool_delete` (high).
  - **RBD** — `rbd_ls`; `rbd_image_create`, `rbd_snapshot_create`,
    `rbd_image_delete` (high), `rbd_snapshot_delete` (high).
  - **CephFS / RGW** — `cephfs_status`, `rgw_status`.
  - **Cluster-ops** — `mon_status`, `mgr_status`, `slow_ops`,
    `capacity_forecast`; `throttle_recovery` (the #1 tuning ask —
    `osd_max_backfills` / `osd_recovery_max_active`).
- **HEALTH_WARN root-cause analysis** — `cluster_health` decodes each active
  check code into cause + action, the differentiator vs raw `ceph -s` proxies.
- **JWT auth** — username + password exchanged for a short-lived JWT at
  `POST /api/auth`; the mgr **dashboard** module must be enabled.
- **Encrypted secret store** (`~/.ceph-aiops/secrets.enc`, Fernet + scrypt) —
  never plaintext on disk; legacy `CEPH_<TARGET>_PASSWORD` env fallback.
- **CLI** with an `init` onboarding wizard, `secret` management, and `doctor`.
- **Dry-run + double-confirm** on the destructive ops operators fear
  (`osd_purge`, `osd_mark_out`, `pool_delete`, `set_pool_size`,
  `rbd_image_delete`); reversible writes record an undo descriptor.

## Install

```bash
uv tool install ceph-aiops
ceph-aiops init
ceph-aiops doctor
```

## Caveats

- Preview / mock-only: multi-node rebalance behaviour and the write ops are
  unverified against a real cluster.
- The Dashboard API has no ETag / pagination, so this tool exposes none.
- Out of scope for v0.1.0: RGW multisite, NFS-Ganesha exports, and cephadm
  orchestrator host management.
