# Release notes — ceph-aiops 0.4.1

Previous release: 0.4.0.

## Fixed: `pool_df` reported a failed query as "no pools"

It caught every exception and returned an empty list, so a flaky mgr was
indistinguishable from a cluster that genuinely has no pools — the failure read as
"nothing to worry about".

**BREAKING** — `pool_df` now returns an envelope:
`{"pools": [...], "returned": N, "error": str | None}`. A non-null `error` means the
query failed. (The MCP wrapper already declared `@tool_errors("dict")`; its return
annotation now matches.)

This is the same defect class found and fixed in `minio-aiops` 0.3.0, where an
identical `except: return []` hid a much larger bug for the life of the tool. A
probe failure must never be presentable as health.
