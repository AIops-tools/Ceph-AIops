# Agent guardrails — running ceph-aiops with a smaller / local model

If you drive these tools with a local model (Llama, Qwen, Mistral … via Goose,
Ollama, LM Studio, or any OpenAI-compatible runtime), you will get noticeably
better results with a short system prompt. This page gives you one, and — more
importantly — tells you which guardrails you **no longer need to write**, because
the tool now enforces them itself.

The distinction matters. A guardrail in a prompt is a request. A guardrail in the
harness is a guarantee. Anything below that we could move into the harness, we did.

## What the tool now enforces — do not waste prompt budget on these

| You might be tempted to prompt | Why you don't need to |
|---|---|
| "Work read-only, never modify the cluster" | Set `CEPH_READ_ONLY=1`. Write tools are then **not registered at all** — they never appear in the tool list, so the model cannot call one even if it tries. The `@governed_tool` harness independently refuses writes, so the CLI is covered too. This includes the benign-looking writes: `trigger_scrub`, `trigger_deep_scrub`, `cluster_flag_set`, `rbd_snapshot_create`. |
| "Don't invent a value when a field is missing" | A field the Dashboard did not return comes back as `null`, never as `""`. A missing `deviceClass`, `host`, MDS `state`, or `pg_autoscale_mode` is distinguishable from an empty one in the payload. |
| "Tell me if the output was cut off" | `pg_summary` and `pg_dump_stuck` return `{"stuck": [...], "returned": N, "limit": L, "truncated": true/false}`. Truncation is measured (one extra row is collected), not guessed. `pg_summary` also keeps `unhealthyCount` as the true total even when the list is capped. |
| "Explain what HEALTH_WARN means" | `cluster_health` already folds each active check code (`PG_DEGRADED`, `OSD_NEARFULL`, `SLOW_OPS`, `LARGE_OMAP_OBJECTS`, …) into a plain-language `cause` and `suggestedAction`. The model should quote those, not compose its own. |
| "Confirm before anything destructive" | Destructive operations (`osd_purge`, `pool_delete`, `rbd_image_delete`, `rbd_snapshot_delete`, `set_pool_size`) require a `--dry-run`-able preview + double confirmation at the CLI, and a named approver (`CEPH_AUDIT_APPROVED_BY`) for high-risk tiers. |
| "Log what you did" | Every call is audited to `~/.ceph-aiops/audit.db` regardless of what the model says it did. |

## What still needs a prompt

These are model-behaviour problems the harness cannot fix from the outside.
Copy this into your agent's system prompt:

```text
You operate a Ceph cluster through the ceph-aiops MCP tools, which talk to the
ceph-mgr Dashboard REST API.

TOOL USE
- Before answering any question about the current cluster, you MUST call a tool.
  Never answer from memory or assumption.
- Actually invoke the tool. Do not describe the call you would make, and do not
  emit an example JSON response in place of calling it.
- If a tool call fails, report the real error verbatim. Never fill the gap with
  a plausible-sounding answer. A read that fails returns an "error" field rather
  than raising — treat that as "unknown", not as "healthy".

READING RESULTS
- Read the whole result before concluding. If a result contains a "truncated"
  field that is true, say so and re-run with a higher limit instead of treating
  the partial result as complete.
- A null field means the Dashboard did not return that value. Report it as "not
  available" — never infer it.
- Report values exactly as returned. Do not normalise, translate, or prettify
  status strings (HEALTH_WARN, active+undersized+degraded), PG ids, or OSD ids.
- When cluster_health returns findings, quote each finding's "cause" and
  "suggestedAction" rather than composing your own explanation of the check code.

SCOPE
- Separate observation from interpretation. State what the tools returned, then
  any interpretation, clearly marked as such.
- Do not assert a capacity, performance, or data-loss problem unless a tool
  result supports it. HEALTH_WARN is not automatically an emergency —
  PG_NOT_DEEP_SCRUBBED on a small cluster is routine.
- Do not confuse the identifier kinds: an OSD id is a number (3), a PG id is
  pool.hex ("2.1a"), a pool name is a string, and an RBD image is addressed as
  pool/name. Never pass one where another is expected.
- capacity_forecast is arithmetic extrapolation from a growth rate you supply.
  With no growth rate it reports "insufficient-data" — do not present that as a
  prediction.
```

## Recommended setup for a local model

```bash
# Read-only until you trust the setup — this is enforced, not advisory.
export CEPH_READ_ONLY=1
ceph-aiops doctor
```

Then, when you are ready to allow writes, unset it and set an approver so the
high-risk tier has an accountable name on it:

```bash
unset CEPH_READ_ONLY
export CEPH_AUDIT_APPROVED_BY="your.name@example.com"
export CEPH_AUDIT_RATIONALE="draining osd.7 for disk replacement 2026-07-20"
```

## If your model still struggles

Some behaviours are model-capacity limits rather than prompt problems:

- **Multi-tool workflows time out or drift.** Prefer `cluster_health` and
  `fleet_overview` — they do the multi-step correlation inside one call, so the
  model does not have to chain reads and keep OSD/PG ids straight.
- **The model ignores later tool results in a long context.** Ask narrower
  questions and use `limit` deliberately rather than dumping every PG in the
  cluster; `pg_summary`'s histogram is usually the right level of detail.
- **The model describes calls instead of making them.** This is usually a
  runtime/tool-calling-format mismatch, not a prompt problem — check that your
  client advertises the tools in the format your model was trained on.

Feedback on running this with a specific local model is genuinely useful —
open an issue at
[github.com/AIops-tools/Ceph-AIops](https://github.com/AIops-tools/Ceph-AIops/issues)
with the model, runtime, and what went wrong.
