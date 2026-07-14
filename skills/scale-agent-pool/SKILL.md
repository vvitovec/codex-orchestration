---
name: scale-agent-pool
description: Run tens or more homogeneous Codex subagent jobs through a configurable programmatic queue instead of manually spawning every worker. Use for large inventories, test shards, independent reviews, migrations with disjoint ownership, datasets, or other map-reduce-shaped work.
---

# Scale Agent Pool

Use programmatic batch fan-out for a large collection of uniform, independent jobs. Prefer the plugin's durable `scripts/orchestrate.py` runner. It launches separate `codex exec` processes with explicit model, effort, sandbox, and working-directory arguments instead of relying on native subagent routing that may ignore those choices.

## Load configuration

Read the first existing file in this order:

1. `.codex/orchestration.toml` in the project;
2. `~/.codex/orchestration.toml`;
3. `references/defaults.toml` from this skill.

Validate it with `scripts/resolve_config.py` from the plugin root when available. Project configuration overrides user configuration; absent values inherit defaults.

## Modes

- `conservative`: small fan-out for expensive or fragile environments.
- `balanced`: normal default.
- `large`: tens of workers when work is independent and quotas support it.
- `adaptive-unrestricted`: no plugin task-count ceiling. Keep feeding pending jobs as capacity becomes available. Runtime thread limits, rate limits, timeouts, memory, and tool constraints remain authoritative.

Never interpret unrestricted as launching all pending jobs simultaneously. An unbounded simultaneous burst can consume quota, duplicate context, overload tools, and leave no capacity for recovery.

## Batch protocol

1. Confirm the jobs are homogeneous and independently verifiable.
2. Materialize JSONL jobs with exactly `id`, `prompt`, `model`, `effort`, `sandbox`, and `workdir` fields. Keep IDs stable across reruns.
3. Use only the validated GPT-5.6 Luna/Terra/Sol routes and low/medium/high/xhigh effort.
4. Encode ownership, verifier, and the requested structured return directly in each bounded prompt.
5. Set concurrency from the resolved configuration, capped by the live Codex `agents.max_threads` and runtime/tool limits.
6. Run `python3 scripts/orchestrate.py <jobs.jsonl>` from the plugin checkout, or `python3 ~/.codex/orchestration/scripts/orchestrate.py <jobs.jsonl>` after personal installation. Use `--dry-run` first for a new batch.
7. Reap timed-out jobs, retain completed results, and retry only retryable failures up to `max_retries`.
8. On rate-limit or resource errors, reduce live concurrency by `backoff_factor`, wait for the runtime-provided retry window when present, and resume the queue.
9. In adaptive-unrestricted mode, increase concurrency gradually after `success_window` clean completions, never above the observed/runtime cap.
10. Inspect the atomic manifest and per-attempt logs under `.codex/orchestration-runs/`; pass only exceptions, summaries, and evidence to the root.

## Safety gates

- Large write batches require disjoint ownership encoded per row.
- Keep `max_write_concurrency = 1` unless the configuration explicitly opts into disjoint writers and each row has a unique ownership scope.
- Stop the batch when repeated failures exceed `circuit_breaker_failures`, a worker requests stop, results indicate shared-state conflict, or verification shows systemic failure.
- Preserve partial results so a resumed run does not repeat completed jobs.
- Never bypass Codex, account, sandbox, or provider limits.

## Routing evidence

The CLI runner proves that the command accepted explicit model/effort configuration. Treat this as **command/config accepted, not runtime-attested**: Codex CLI JSONL currently does not attest the effective route. A future App Server backend may add effective-route attestation.

## Fallback

If the runner is unavailable, use a bounded replenishing native pool and explicitly report that model and effort are not enforceable through the native spawn interface. Do not create one root-thread tool call per item up front.
