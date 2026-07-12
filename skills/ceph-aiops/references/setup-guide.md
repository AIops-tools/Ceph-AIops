# ceph-aiops setup & security guide

> Preview / mock-only — not yet validated against a live Ceph cluster. The
> cheapest live check is a single-node MicroCeph running `ceph-aiops doctor`.

## 1. Install

```bash
uv tool install ceph-aiops
```

## 2. Enable the ceph-mgr Dashboard module

ceph-aiops talks to the **ceph-mgr Dashboard REST API** (HTTPS, default port
`8443`). The mgr **dashboard** module must be enabled and a Dashboard user must
exist:

```bash
ceph mgr module enable dashboard
ceph dashboard ac-user-create <username> -i <password-file> administrator
# find the URL/port: ceph mgr services   → e.g. https://<host>:8443/
```

ceph-aiops authenticates by exchanging the **username + password** for a
short-lived **JWT** at `POST /api/auth`; the token is cached in memory and used
as a Bearer header for subsequent calls.

## 3. Onboard

```bash
ceph-aiops init
```

The wizard collects (non-secret) connection details into
`~/.ceph-aiops/config.yaml` and stores the Dashboard **password** encrypted into
`~/.ceph-aiops/secrets.enc`. Example config:

```yaml
targets:
  - name: ceph1
    host: 10.0.0.30
    port: 8443
    username: admin
    verify_ssl: false          # self-signed lab certs only
```

The `username` lives in the config file (it is not a secret); the password never
does.

## 4. Non-interactive use (MCP server / CI / cron)

Export the master password so the encrypted store can be unlocked without a
prompt:

```bash
export CEPH_AIOPS_MASTER_PASSWORD='your-master-password'
```

## Credential security

- The Dashboard password is **never** written to disk in plaintext. It lives only
  in `~/.ceph-aiops/secrets.enc`, encrypted with Fernet (AES-128-CBC + HMAC), the
  key derived from your master password via scrypt. Only a per-store random salt
  and the ciphertext are on disk (chmod 600); the master password itself is never
  stored.
- A legacy plaintext env var `CEPH_<TARGET_NAME_UPPER>_PASSWORD` is still honoured
  as a fallback with a deprecation warning — migrate with
  `ceph-aiops secret migrate` (it imports then renames the old `.env`).
- The password is held only in memory, exchanged for a JWT at request time, and
  is never logged or echoed; exception text and tracebacks are scrubbed of
  secret-shaped strings before being written to the audit log.

## Approver env vars (high-risk ops)

High-risk destructive ops (`osd_purge`, `osd_mark_out`, `pool_delete`,
`set_pool_size`, `rbd_image_delete`, `rbd_snapshot_delete`) can require a named
approver:

```bash
export CEPH_AUDIT_APPROVED_BY='you@example.com'
export CEPH_AUDIT_RATIONALE='why this destructive op is justified'
```

## Governance harness state

State lives under `~/.ceph-aiops/` (relocate with `CEPH_AIOPS_HOME`):

- `audit.db` — every tool call (SQLite), with risk tier, approver, rationale
- `rules.yaml` — policy: deny rules, maintenance windows, approval tiers
- `undo.db` — inverse descriptors for reversible writes (e.g. `osd_reweight`,
  `set_pool_quota`, `throttle_recovery`)
- budget / runaway guard — caps cumulative tool calls and wall-time; trips on
  tight poll/retry loops

## Self-test free with MicroCeph

The cheapest **live** path — a single-node cluster on one box:

```bash
snap install microceph
microceph cluster bootstrap
microceph disk add loop,4G,3        # 3 loop-file OSDs
# enable dashboard + create a user (see step 2), then:
ceph-aiops init
ceph-aiops doctor
```

A 3-node Vagrant cluster exercises real rebalance/backfill behaviour (draining an
OSD, changing pool size) that a single node cannot.

## Note: no ETag / pagination

The ceph-mgr Dashboard API offers neither ETag caching nor pagination, so
ceph-aiops exposes none — nothing is missing, the upstream API simply doesn't
provide them.

## Verify

```bash
ceph-aiops doctor
```

`doctor` checks the config file, the encrypted store and its permissions, that a
password is present per target, and (unless `--skip-auth`) connectivity by
performing the JWT login against the mgr Dashboard.
