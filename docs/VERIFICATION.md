# Live verification — ceph-aiops

`ceph-aiops` is published on PyPI, the MCP Registry, and ClawHub. What it has
**not** had is an end-to-end run against a live cluster:

> The code is exercised by a mock-only test suite (`uv run pytest`, no real
> ceph-mgr). It has not yet been validated end-to-end against a live Ceph
> cluster. Until it has, we do not claim it works against a real Dashboard API.

This document defines exactly what a live verification run must cover, and the
criteria for recording this tool as live-verified. It is deliberately
checklist-shaped so the result is reproducible and auditable — not a subjective
"seems fine".

## What the mock suite already guarantees

- Every module imports; the CLI builds; every MCP tool carries the
  `@governed_tool` harness marker (`tests/test_smoke.py`).
- The JWT login flow (`POST /api/auth`) authenticates and translates errors,
  against a mocked transport (`tests/test_connection.py`).
- `cluster_health` maps synthetic HEALTH_WARN/ERR check codes to the expected
  cause + suggested action, and the OSD/PG/pool/RBD/CephFS analyses produce the
  expected findings from synthetic telemetry.
- Write tools carry the correct risk tier and record the correct inverse undo
  descriptor, and the undo executor replays it (`tests/test_undo_executor.py`).
- Governance genuinely persists: audit rows and undo tokens land in a real
  SQLite DB (`tests/test_governance_persistence.py`).

What it does **not** guarantee: that the ceph-mgr Dashboard REST call shapes,
JSON field names, and units match a real Ceph release — nor that multi-node
rebalance behaves as the analyses assume.

## Prerequisites for a live run

A reachable ceph-mgr with the **dashboard** module enabled. The cheapest path is
a single-node **MicroCeph** (`snap install microceph` → bootstrap → loop-file
OSDs); a 3-node Vagrant/cephadm cluster is needed for the rebalance items in
section 4. Create a **least-privilege Dashboard user** and a **throwaway test
pool** and **test RBD image** you are willing to resize, quota, and delete.
Never verify against a production pool.

```bash
uv tool install ceph-aiops
ceph-aiops init            # encrypted secret store, TLS verify on by default
```

## Verification checklist

Tick every box. A box that cannot be ticked is a verification gap — record it,
do not silently pass.

### 1. Connectivity (the fastest live gate)
- [ ] `ceph-aiops doctor` → all green (config, encrypted secret store, and a real
      JWT login against `POST /api/auth`).

### 2. Reads return real, well-shaped data
- [ ] `ceph-aiops overview` → HEALTH status, active checks, and OSD up/in counts
      match `ceph -s` on the cluster.
- [ ] `ceph-aiops health detail` → every **active** check code is decoded; an
      unknown/unmapped check degrades gracefully instead of crashing.
- [ ] `ceph-aiops osd df` → per-OSD utilization matches `ceph osd df`, most-full
      first, with near/backfill-full flags set correctly.
- [ ] `ceph-aiops osd tree` → the real CRUSH hierarchy (hosts, OSD ids, weights).
- [ ] `pool_df` → usable capacity equals raw ÷ `size` for a known pool (verify
      against `ceph df` by hand — this is the field most likely to be misread).
- [ ] `pg_summary` / `pg_dump_stuck` → PG state counts match `ceph pg stat`.
- [ ] `capacity_forecast`, `slow_ops`, `mon_status`, `mgr_status`,
      `cephfs_status`, `rgw_status` → no crash on missing/absent subsystems (a
      cluster with no CephFS or no RGW must return an empty/absent result, not
      an exception).

### 3. A reversible write + its undo (governance closes the loop)
- [ ] `ceph-aiops osd reweight <id> 0.9` → the CRUSH weight actually changes; the
      result carries an `_undo_id`; a row lands in `~/.ceph-aiops/audit.db`.
- [ ] `ceph-aiops undo list` then `ceph-aiops undo apply <id>` → the **prior**
      weight is restored (proves undo captured pre-state, not a guess).
- [ ] `set_pool_quota` on the test pool, then `undo apply` → the prior quota
      (including "no quota") is restored exactly.
- [ ] `throttle_recovery(max_backfills=1, recovery_max_active=1)` then
      `undo apply` → the prior `osd_max_backfills` / `osd_recovery_max_active`
      come back (check with `ceph config get osd`).

### 4. Multi-node rebalance behaviour (needs ≥3 nodes)
- [ ] `ceph-aiops osd out <id> --dry-run` → prints the call, changes nothing.
- [ ] `ceph-aiops osd out <id>` for real → the cluster starts backfilling;
      `pg_summary` reflects the degraded→recovering→`active+clean` progression.
- [ ] `osd_mark_in` reverses it and the cluster returns to `active+clean`.
- [ ] While backfilling, `throttle_recovery` measurably slows recovery (compare
      recovery throughput in `ceph -s` before/after).

### 5. Audit is unbypassable — both entry points
- [ ] Run a `high`-risk op (`osd_purge`, `pool_delete`, `set_pool_size`) over MCP
      and the same op over the CLI; confirm **both** land a row in `audit.db`, and
      that `CEPH_AUDIT_APPROVED_BY` / `CEPH_AUDIT_RATIONALE`, when set, appear on
      the row (recorded, never required — the skill authorizes nothing).
- [ ] A failed write is audited with `status=error` and records **no** undo token.
- [ ] `pool_delete .mgr` is refused (`SelfLockout`), under `dry_run` too, and an
      ordinary pool still deletes — the guard is exact, not a prefix match.
- [ ] `set_pool_size .mgr <lower-than-current>` is refused, and the message names
      the actual change it blocked. Reducing `.mgr` degrades ceph-mgr, which
      serves the Dashboard REST API the undo would have to travel over.
- [ ] `set_pool_size .mgr <higher-than-current>` **succeeds** — only the downward
      direction is a hazard, and a blanket refusal would block the one change
      that makes the mgr pool safer.
- [ ] A non-protected pool still resizes downward, proving the guard's exactness.
- [ ] A tight poll loop trips the runaway budget guard rather than hammering the
      Dashboard API.

### 6. Cleanup
- [ ] `rbd_image_delete` the test image and `pool_delete` the test pool; confirm
      both are audited and tagged `high`, and that the cluster returns to
      `HEALTH_OK`.

## Criteria to consider this tool live-verified

Record `ceph-aiops` as live-verified **only when all of the following hold**:

1. Every box in sections 1–3, 5 and 6 is ticked against at least one real Ceph
   release, and that release is recorded (e.g. "verified on Ceph 19.2 /
   MicroCeph"). Section 4 is ticked separately and recorded as
   "single-node verified" vs "multi-node verified" — do not conflate them.
2. Any field-shape, unit, or check-code mismatch found during the run is fixed
   **and covered by a test**, so the mock suite cannot regress it.
3. The run is written up in this repo's release notes with the date, the tool
   version, and the Ceph version, matching how the line records its other
   live-verified tools.

Until then this document is the accurate statement of status — and no positive
claim about real-hardware behaviour should appear in the README or SKILL.md.

## Notes for maintainers

- `ceph-aiops doctor` is the single fastest live entry point; start there.
- MicroCeph gets you sections 1–3 and 5 cheaply; only section 4 needs real nodes.
- The Dashboard API offers no ETag and no pagination, so there is nothing to
  verify there — the absence is upstream, not a gap in this tool.
- The verification story for the whole product line is tracked centrally; add
  this tool's result there once green so the verification-debt ledger stays
  accurate.
