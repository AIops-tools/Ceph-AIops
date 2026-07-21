# Security Policy

## Disclaimer

Community-maintained open-source project. **Not affiliated with, endorsed by, or
sponsored by the Ceph project or the Ceph Foundation.** Product and trademark
names belong to their owners. Source is publicly auditable under the MIT license.

## Reporting Vulnerabilities

Report privately via a GitHub Security Advisory on
[github.com/AIops-tools/Ceph-AIops](https://github.com/AIops-tools/Ceph-AIops/security/advisories)
or email zhouwei008@gmail.com. Please do not open public issues for security
reports.

## Security Design

### Credential Management
- Per-target ceph-mgr Dashboard passwords live **encrypted** in
  `~/.ceph-aiops/secrets.enc` (Fernet/AES-128 + scrypt-derived key; chmod
  600), never in `config.yaml` and never in source. The master password is
  never stored — only a per-store random salt and the ciphertext are on disk.
- A legacy plaintext env var `CEPH_<TARGET_NAME_UPPER>_PASSWORD` is still
  honoured as a fallback with a deprecation warning (migrate with
  `ceph-aiops secret migrate`).
- The password is exchanged for a short-lived **JWT** at `POST /api/auth`; only
  the token is sent on subsequent requests (Bearer), and it is refreshed once on
  a 401. The password is held only in memory, never logged or echoed; the config
  file holds only host, port, username, and TLS settings.

### Governed Operations
Every MCP tool runs through the bundled `@governed_tool` harness
(`ceph_aiops.governance`):
- **Audit** — every call logged to a local SQLite DB under `~/.ceph-aiops/`
  (relocatable via `CEPH_AIOPS_HOME`), agent-attributed, secret-redacted.
- **Token/runaway budget** — hard ceilings (`CEPH_MAX_TOOL_CALLS` /
  `CEPH_MAX_TOOL_SECONDS`) plus an on-by-default guard that trips a tight
  poll/retry loop, preventing unbounded API consumption (e.g. polling a slow
  session).
- **Risk-tier labelling** — each tool's declared `risk_level` is carried into
  the audit row as a descriptive tier. It labels the row; it does not gate the
  call. Whether a write is permitted is the agent's or the account's decision,
  not the skill's.
- **Undo-token recording** — reversible writes capture the BEFORE state and
  record an inverse descriptor (e.g. `osd_reweight`→restore prior weight,
  `cluster_flag_set`→toggle the flag back, `throttle_recovery`→restore prior
  backfill/recovery limits) so the change can be rolled back.

### State-Changing Operations
Destructive writes — `osd_mark_out`, `osd_purge`, `set_pool_size`, `pool_delete`,
`rbd_image_delete`, `rbd_snapshot_delete` — are `risk_level=high`, accept a
`dry_run` preview. `CEPH_AUDIT_APPROVED_BY` / `CEPH_AUDIT_RATIONALE` are optional audit annotations recorded on the row, never required. The CLI additionally
double-confirms `osd out` and `osd purge` and supports `--dry-run`. Reversible
medium/low writes capture before-state and, where a safe inverse exists, record
an undo token. Ceph has no ETag/If-Match and its list endpoints return full
arrays, so there is no optimistic-concurrency token to manage.

### SSL/TLS Verification
`verify_ssl` defaults to true; disable only for self-signed lab certificates.

### Prompt-Injection Protection
All server-returned text (pool/OSD names, PG ids, health check messages, RGW
bucket names) is passed through a `sanitize()` truncate + control-character strip
before reaching the agent.

### Network Scope
No webhooks, no telemetry, no outbound calls beyond the configured ceph-mgr
Dashboard REST API endpoint. No post-install scripts or background services.

## Static Analysis

```bash
uvx bandit -r ceph_aiops/ mcp_server/
uv run ruff check .
```

## Supported Versions

The latest released version receives security fixes. This is a preview (0.x);
pin a version in production.
