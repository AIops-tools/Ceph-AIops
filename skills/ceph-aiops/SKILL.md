---
name: ceph-aiops
description: >
  Use this skill whenever the user needs to operate or diagnose a Ceph cluster via its ceph-mgr Dashboard REST API — decode a HEALTH_WARN/ERR state into cause + action (cluster_health), read the cluster status, inspect OSDs (tree/df/perf), placement groups (summary/stuck/scrub), pools (list/usable capacity), RBD images and snapshots, CephFS/MDS and RGW status, monitors/managers, slow ops and capacity forecast — plus governed writes (set cluster flags, reweight/mark-in/mark-out/purge OSDs, trigger scrubs, set pool quota/pg_num/autoscale/size, create/delete pools, create/delete RBD images and snapshots, throttle recovery/backfill).
  Always use this skill for "ceph health", "what does this HEALTH_WARN mean", "PG_DEGRADED / OSD_NEARFULL / SLOW_OPS / MON_DOWN", "ceph -s", "which OSD is most full", "drain an OSD", "purge an OSD", "stuck PGs", "overdue scrub", "pool usable capacity", "set pool size / quota", "rebalance is too slow / throttle backfill", "RBD image or snapshot", "MDS behind on trimming", "RGW large omap", "mon quorum", or "days to nearfull" when the context is a Ceph cluster (cephadm, hypervisor-bundled Ceph, or MicroCeph).
  Do NOT use when the target is not Ceph — a hypervisor, a different storage appliance, a backup product, a Kubernetes cluster, or a network device. Route those to the appropriate other AIops-tools skill (negative routing hint only).
  Preview — common Ceph ops with a built-in governance harness (audit, policy, token budget, undo, risk-tiers). Mock-validated only, not yet verified against a live cluster.
installer:
  kind: uv
  package: ceph-aiops
argument-hint: "[ceph question or describe your cluster task]"
allowed-tools:
  - Bash
metadata: {"openclaw":{"requires":{"env":["CEPH_AIOPS_CONFIG"],"bins":["ceph-aiops"],"config":["~/.ceph-aiops/config.yaml","~/.ceph-aiops/secrets.enc"]},"optional":{"env":["CEPH_AIOPS_MASTER_PASSWORD"]},"primaryEnv":"CEPH_AIOPS_CONFIG","homepage":"https://github.com/AIops-tools/Ceph-AIops","emoji":"🐙","os":["macos","linux"]}}
compatibility: >
  Standalone, self-governed Ceph operations (preview). The governance harness (audit, policy, token/runaway budget, undo, risk-tiers) is bundled in the package — no external skill-family dependency. Works against vanilla ceph-mgr (cephadm / hypervisor-bundled Ceph / MicroCeph); no croit and no Kubernetes dependency.
  All write operations are audited to a local SQLite DB under ~/.ceph-aiops/ (relocatable via CEPH_AIOPS_HOME).
  Connection: the ceph-mgr Dashboard REST API over HTTPS (default port 8443). Authentication is username + password exchanged for a short-lived JWT at POST /api/auth; the mgr 'dashboard' module must be enabled. The username lives in config.yaml; the password is stored ENCRYPTED in ~/.ceph-aiops/secrets.enc (Fernet/AES-128 + scrypt-derived key) — never plaintext on disk. Run 'ceph-aiops init' to onboard, or 'ceph-aiops secret set <target>' to add one. The store is unlocked by a master password from CEPH_AIOPS_MASTER_PASSWORD (non-interactive/MCP/CI) or an interactive prompt (CLI on a TTY). A legacy plaintext env var CEPH_<TARGET_NAME_UPPER>_PASSWORD is still honoured as a fallback with a deprecation warning (migrate with 'ceph-aiops secret migrate'). The password is held only in memory and exchanged for a JWT at request time; secrets are never logged or echoed.
  State-changing operations require double confirmation at the CLI layer and support --dry-run. All write tools pass through the @governed_tool decorator (pre-check + budget guard + audit + risk-tier gate). High-risk destructive ops (osd_mark_out, osd_purge, pool_delete, set_pool_size, rbd_image_delete, rbd_snapshot_delete) require dry-run + double confirmation; reversible writes (osd_reweight, cluster_flag_set, set_pool_quota/pg_num/autoscale, throttle_recovery) capture the prior state and record an inverse undo descriptor.
  Webhooks: none — no outbound network calls beyond the configured ceph-mgr Dashboard REST API.
  SSL: verify_ssl defaults to true; disable only for self-signed lab certificates.
  Transitive dependencies: httpx (HTTP client) and the MCP SDK. No post-install scripts or background services.
  PREVIEW: mock-validated only; multi-node rebalance behaviour and the write ops need live verification (a single-node MicroCeph running 'ceph-aiops doctor' is the cheapest live path). The Dashboard API has no ETag/pagination, so none are exposed.
---

# Ceph AIops (preview)

> **Disclaimer**: Community-maintained open-source project, **not affiliated with, endorsed by, or sponsored by the Ceph project or any storage vendor.** Product and trademark names belong to their owners. Source at [github.com/AIops-tools/Ceph-AIops](https://github.com/AIops-tools/Ceph-AIops) under the MIT license.

Governed Ceph operations via the **ceph-mgr Dashboard REST API** — **35 MCP tools**, every one wrapped with the bundled `@governed_tool` harness: a local unified audit log under `~/.ceph-aiops/`, policy engine, token/runaway budget guard, undo-token recording, and graduated-autonomy risk tiers. The Dashboard password is stored **encrypted** (`~/.ceph-aiops/secrets.enc`, Fernet + scrypt) — never plaintext on disk. The flagship `cluster_health` turns raw HEALTH_WARN/ERR check codes into plain-language cause + suggested action.

> **Standalone**: the governance harness is bundled in the package (`ceph_aiops.governance`) — ceph-aiops has no external skill-family dependency. Works against vanilla ceph-mgr (cephadm / hypervisor-bundled / MicroCeph); no croit, no Kubernetes. **Preview / mock-only**: not yet validated against a live cluster.

## What This Skill Does

| Group | Tools | Count | Read or Write |
|-------|-------|:-----:|:-------------:|
| **Health** | cluster_health (flagship RCA), cluster_status | 2 | 2 read |
| **OSD** | osd_tree, osd_df, osd_perf | 3 | 3 read |
| | cluster_flag_set, osd_reweight, osd_mark_in, osd_mark_out, osd_purge | 5 | 5 write |
| **PG** | pg_summary, pg_dump_stuck, scrub_status | 3 | 3 read |
| | trigger_scrub, trigger_deep_scrub | 2 | 2 write |
| **Pool** | pool_ls, pool_df | 2 | 2 read |
| | set_pool_quota, set_pool_pg_num, set_pool_autoscale, pool_create, set_pool_size, pool_delete | 6 | 6 write |
| **RBD** | rbd_ls | 1 | 1 read |
| | rbd_image_create, rbd_snapshot_create, rbd_image_delete, rbd_snapshot_delete | 4 | 4 write |
| **CephFS / RGW** | cephfs_status, rgw_status | 2 | 2 read |
| **Cluster-ops** | mon_status, mgr_status, slow_ops, capacity_forecast | 4 | 4 read |
| | throttle_recovery | 1 | 1 write |

Totals: **35 tools — 17 read, 18 write.** The MCP server exposes all 35; the CLI is a convenience subset.

## Quick Install

```bash
uv tool install ceph-aiops
ceph-aiops init       # interactive wizard: mgr host/port/username + encrypted Dashboard password
ceph-aiops doctor
```

## When to Use This Skill

- Decode a **HEALTH_WARN/ERR** state (`cluster_health` / `health detail`) — cause + action per active check (`PG_DEGRADED`, `OSD_NEARFULL`, `SLOW_OPS`, `MON_DOWN`, `LARGE_OMAP_OBJECTS`, …)
- One-shot triage (`overview`): HEALTH status + active checks + OSD up/in counts
- Inspect OSDs (`osd_tree` / `osd_df` most-full first / `osd_perf` slowest first), PGs (`pg_summary` / `pg_dump_stuck` / `scrub_status`), pools (`pool_ls` / `pool_df` usable capacity)
- Investigate slow requests (`slow_ops`), MDS trimming lag (`cephfs_status`), RGW large-omap (`rgw_status`), mon quorum (`mon_status`), and days-to-nearfull (`capacity_forecast`)
- Safely **drain + purge** an OSD, change **pool size/quota**, or **throttle a slow rebalance** (governed writes with dry-run + undo)

**Do NOT use when** the target is not Ceph (a hypervisor, another storage appliance, a backup product, a container cluster, or a network device). Route those to the appropriate **other AIops-tools** skill.

## Related Skills — Skill Routing

| If the user wants… | Use |
|--------------------|-----|
| Ceph: HEALTH_WARN RCA, OSD/PG/pool/RBD/CephFS/RGW, rebalance, slow ops | **ceph-aiops** (this skill) |
| Any non-Ceph target (hypervisor, other storage, backup, cluster, network) | the appropriate **other AIops-tools** skill |

## Common Workflows

### Decode a HEALTH_WARN

1. `ceph-aiops overview` → HEALTH status + the list of active checks
2. `ceph-aiops health detail` (or the `cluster_health` tool) → each active check code translated into **what it means, likely cause, suggested action**
3. Drill into the implicated resource — e.g. `PG_DEGRADED` → `pg_dump_stuck`; `OSD_NEARFULL` → `osd_df`; `SLOW_OPS` → `slow_ops`

### Safely drain + purge an OSD (governed)

1. `ceph-aiops osd df` → confirm which OSD, and that the cluster has room to rebalance
2. `ceph-aiops osd reweight <id> 0.0` → start draining (reversible → prior weight recorded)
3. `ceph-aiops osd out <id> --dry-run` then re-run without `--dry-run` (double confirm, **high** risk — set `CEPH_AUDIT_APPROVED_BY`/`CEPH_AUDIT_RATIONALE`) → marks out, drains data
4. Wait for backfill to finish (`pg_summary` shows all active+clean)
5. `ceph-aiops osd purge <id> --dry-run` then re-run without `--dry-run` (double confirm, **high**, irreversible)

### Throttle a slow rebalance

`throttle_recovery(max_backfills=1, recovery_max_active=1)` (med, undo → prior `osd_max_backfills` / `osd_recovery_max_active`) — the #1 tuning ask when recovery is starving client I/O. Raise the values again later to speed it back up.

### Pool capacity & usable capacity

1. `pool_df` → per-pool usage with **usable capacity = raw ÷ size** (so a size=3 pool reports a third of raw)
2. If a pool is short and needs a replica change, `set_pool_size` is **high** risk + dry-run because changing `size` forces data movement across the cluster

## Governance & Safety

- Every tool is audited to `~/.ceph-aiops/audit.db` (relocatable via `CEPH_AIOPS_HOME`).
- High-risk ops (`osd_purge`, `osd_mark_out`, `pool_delete`, `set_pool_size`, `rbd_image_delete`, `rbd_snapshot_delete`) can require a named approver: set `CEPH_AUDIT_APPROVED_BY` and `CEPH_AUDIT_RATIONALE`.
- Destructive writes support `--dry-run` and double confirmation at the CLI.
- Reversible writes record an inverse descriptor capturing the prior state.

## References

- `references/capabilities.md` — full tool → API-path → returns reference
- `references/cli-reference.md` — CLI command reference
- `references/setup-guide.md` — onboarding, credentials, and connectivity
