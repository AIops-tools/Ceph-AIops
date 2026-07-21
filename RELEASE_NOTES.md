# Release notes — ceph-aiops 0.7.0

Previous release: 0.6.0.

## Preview fidelity

A `--dry-run` should run the same guards as the real call and leave an audit row — the line's invariant is "a dry_run MAY read; it must never write." A few write commands still showed a hand-written banner that ran no guard and audited nothing. Those are now routed through the governed twin. The real writes were always guarded and audited; only the previews were blind.


### In this tool

- `osd reweight --dry-run` now routes through the governed twin (reads the current + target weight, audits) instead of a static banner.
