<!-- mcp-name: io.github.AIops-tools/ceph-aiops -->

# Ceph AIops

> **Disclaimer**: Community-maintained open-source project. **Not affiliated with, endorsed by, or sponsored by the Ceph project or any storage vendor.** Product and trademark names belong to their owners. MIT licensed.

Governed AI-ops for **Ceph** — talks to a vanilla **ceph-mgr Dashboard REST API**
(HTTPS `:8443`, username + password exchanged for a short-lived JWT at
`POST /api/auth`) with a **built-in governance harness**: unified audit log,
policy engine, token/runaway budget guard, undo-token recording, and
graduated-autonomy risk tiers. Works against stock ceph-mgr — **cephadm**,
**hypervisor-bundled Ceph**, or **MicroCeph** — with **no croit and no Kubernetes
dependency**. Self-contained: no external skill-family dependency.

## What it does

The flagship analysis, plus the guarded reads and writes around it:

- **`cluster_health` — HEALTH_WARN/ERR root-cause analysis.** Instead of echoing
  raw check codes (`PG_DEGRADED`, `OSD_NEARFULL`, `SLOW_OPS`, `MON_DOWN`,
  `LARGE_OMAP_OBJECTS`, …), it turns each *active* check into plain language:
  **what it means, the likely cause, and the suggested next action**. This is the
  differentiator vs the hobby Ceph MCPs that just proxy `ceph -s`.
- **Governed destructive ops.** The operations operators actually fear —
  `osd_purge`, `pool_delete`, `set_pool_size`, `rbd_image_delete` — carry
  **dry-run + double-confirm** and a **high** risk tier; reversible tuning
  (`osd_reweight`, `throttle_recovery`, `cluster_flag_set`, pool quota/pg_num/
  autoscale) records an **undo descriptor** capturing the prior state.

## Security: read-only mode

This tool is meant to be handed to an AI agent, so its safety story is enforced
by the server rather than requested in a prompt:

```bash
export CEPH_READ_ONLY=1
```

With that set, the **19 write tools are never registered**. An MCP client
lists **18 tools instead of 37** — the writes are not hidden, not
gated behind a flag, and not merely refused when called. They are absent from
the session. A model cannot invoke a tool it was never offered, and cannot be
argued into one.

That distinction is the whole point. A tool that exists but refuses still invites
retry loops and "I'll describe the call instead" behaviour from smaller models,
and it leaves a reviewer trusting a promise. An absent tool is a fact you can
check: connect, list the tools, and see that the writes are not there.

Enforcement is two layers deep, so the switch cannot be sidestepped by changing
entry point:

| Layer | What it does | Covers |
|---|---|---|
| `@governed_tool` harness | refuses every non-read operation outright | MCP, CLI, and in-process callers |
| MCP registration | write tools are removed from `list_tools()` | anything speaking MCP |

Read operations are unaffected, and every call is still audited to
`~/.ceph-aiops/audit.db`.

> The read/write split is derived from each tool's declared `risk_level`, and a
> test asserts that this never disagrees with the `[READ]`/`[WRITE]` tag in the
> tool's own documentation — so a write can't quietly present itself as a read.

Running a smaller / local model? See
[agent-guardrails.md](skills/ceph-aiops/references/agent-guardrails.md) — it lists
the guardrails this tool now enforces for you (so you don't spend prompt budget
restating them) and gives a ready-made system prompt for what's left.

## What works

- **CLI** (`ceph-aiops ...`): `init`, `overview`, `health detail`/`health status`,
  `osd tree/df/reweight/out/purge`, `secret set/list/rm/migrate/rotate-password`,
  `doctor`, `mcp`. `osd out` and `osd purge` require `--dry-run` + double confirm.
- **MCP server** (`ceph-aiops mcp` or `ceph-aiops-mcp`): the full **37 tools**
  (17 read, 18 write, 2 undo), every one wrapped with the bundled `@governed_tool`
  harness. The CLI is a convenience subset; the MCP surface is the whole tool.
- **Encrypted credentials**: the Dashboard password lives in an encrypted store
  `~/.ceph-aiops/secrets.enc` (Fernet + scrypt) — **never plaintext on disk**.
  Unlock with a master password from `CEPH_AIOPS_MASTER_PASSWORD` (MCP/CI) or an
  interactive prompt (CLI).
- **Reversibility**: reversible writes capture the prior state and record an
  inverse undo descriptor (e.g. `osd_reweight` → prior weight, `set_pool_quota`
  → prior quota, `throttle_recovery` → prior backfill/recovery settings).
- **Safety**: destructive ops (`osd_purge`, `osd_mark_out`, `pool_delete`,
  `set_pool_size`, `rbd_image_delete`, `rbd_snapshot_delete`) are `high` risk
  with `dry_run` and CLI double confirmation.

## Capability matrix (37 MCP tools)

| Group | Tools | Count | R/W |
|-------|-------|:-----:|:---:|
| **Health** | `cluster_health` (flagship RCA), `cluster_status` | 2 | read |
| **OSD** | `osd_tree`, `osd_df`, `osd_perf` | 3 | read |
| | `cluster_flag_set` (low, undo), `osd_reweight` (med, undo), `osd_mark_in` (med, undo) | 3 | write |
| | `osd_mark_out` (high, dry-run), `osd_purge` (high, dry-run) | 2 | write |
| **PG** | `pg_summary`, `pg_dump_stuck`, `scrub_status` | 3 | read |
| | `trigger_scrub` (low), `trigger_deep_scrub` (low) | 2 | write |
| **Pool** | `pool_ls`, `pool_df` | 2 | read |
| | `set_pool_quota` (med, undo), `set_pool_pg_num` (med, undo), `set_pool_autoscale` (med, undo), `pool_create` (med) | 4 | write |
| | `set_pool_size` (high, dry-run), `pool_delete` (high, dry-run) | 2 | write |
| **RBD** | `rbd_ls` | 1 | read |
| | `rbd_image_create` (med), `rbd_snapshot_create` (low) | 2 | write |
| | `rbd_image_delete` (high, dry-run), `rbd_snapshot_delete` (high, dry-run) | 2 | write |
| **CephFS / RGW** | `cephfs_status`, `rgw_status` | 2 | read |
| **Cluster-ops** | `mon_status`, `mgr_status`, `slow_ops`, `capacity_forecast` | 4 | read |
| | `throttle_recovery` (med, undo) | 1 | write |
| **Undo** | `undo_list`, `undo_apply` | 2 | undo |

Totals: **37 tools — 17 read, 18 write, 2 undo.**

## Quick start

```bash
uv tool install ceph-aiops          # or: pipx install ceph-aiops
ceph-aiops init                     # wizard: add a mgr target + store the Dashboard password (encrypted)
ceph-aiops doctor                   # JWT login + mgr-dashboard reachability
ceph-aiops overview                 # HEALTH status + active checks + OSD up/in
ceph-aiops health detail            # decode the active HEALTH_WARN/ERR checks (RCA)
ceph-aiops osd df                   # per-OSD utilization, most-full first, near/backfill-full flags
```

Run as an MCP server (stdio):

```bash
export CEPH_AIOPS_MASTER_PASSWORD=...   # unlock secrets non-interactively
ceph-aiops-mcp
```

## Governance

Every MCP tool passes through the bundled `@governed_tool` harness:

- **Audit** — every call (params, result, status, duration, risk tier,
  approver, rationale) is logged to `~/.ceph-aiops/audit.db` (relocatable
  via `CEPH_AIOPS_HOME`).
- **Budget / runaway guard** — token and call budgets trip a circuit breaker.
- **Risk tiers** — graduated autonomy; high-risk ops (purge/delete/replica
  change) can require a named approver
  (`CEPH_AUDIT_APPROVED_BY` / `CEPH_AUDIT_RATIONALE`).
- **Undo recording** — reversible writes record an inverse descriptor.

## Supported scope & limitations

- **Deployments**: vanilla ceph-mgr with the **dashboard** module enabled —
  cephadm, hypervisor-bundled Ceph, or MicroCeph. **No croit, no Kubernetes
  dependency.**
- **Ceph has no ETag / pagination** on the Dashboard API, so this tool exposes
  none — nothing is missing, the upstream API simply doesn't offer them.
- **Validation status**: behaviour is exercised against mocked Dashboard
  responses by the test suite; multi-node rebalance and the write ops have not
  been run against a live cluster. The cheapest live check is a single-node
  **MicroCeph** (`snap install microceph` → bootstrap → loop-file OSDs) running
  `ceph-aiops doctor`; a 3-node Vagrant cluster exercises real rebalance
  behaviour. See [`docs/VERIFICATION.md`](docs/VERIFICATION.md) for the full
  live-verification checklist.

## Missing a capability?

RGW multisite, per-daemon config sprawl, NFS-Ganesha exports, orchestrator
(cephadm) host management — not here yet. **Open an issue or send a PR** — feedback
and contributions are welcome.
