---
name: scale-agent-pool
description: Run tens or more homogeneous Codex subagent jobs through a configurable programmatic queue instead of manually spawning every worker. Use for large inventories, test shards, independent reviews, migrations with disjoint ownership, datasets, or other map-reduce-shaped work.
---

# Scale Agent Pool

Use programmatic batch fan-out for a large collection of uniform, independent jobs. Prefer Codex `spawn_agents_on_csv` when available because it owns the spawn, concurrency, collection, timeout, and result-reporting loop.

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
2. Materialize or identify a CSV with a stable `item_id`, input fields, ownership scope, and verifier.
3. Define one short instruction template using `{column_name}` placeholders.
4. Define a structured result schema with status, summary, evidence, checks, and blocker.
5. Set concurrency from the resolved configuration, capped by the live Codex `agents.max_threads` and runtime/tool limits.
6. Call `spawn_agents_on_csv`. Do not manually call `spawn_agent` for every row when the batch primitive exists.
7. Reap timed-out jobs, retain completed results, and retry only retryable failures up to `max_retries`.
8. On rate-limit or resource errors, reduce live concurrency by `backoff_factor`, wait for the runtime-provided retry window when present, and resume the queue.
9. In adaptive-unrestricted mode, increase concurrency gradually after `success_window` clean completions, never above the observed/runtime cap.
10. Reduce results into a compact manifest and pass only exceptions, summaries, and evidence to the root.

## Safety gates

- Large write batches require disjoint ownership encoded per row.
- Keep `max_write_concurrency = 1` unless the configuration explicitly opts into disjoint writers and each row has a unique ownership scope.
- Stop the batch when repeated failures exceed `circuit_breaker_failures`, a worker requests stop, results indicate shared-state conflict, or verification shows systemic failure.
- Preserve partial results so a resumed run does not repeat completed jobs.
- Never bypass Codex, account, sandbox, or provider limits.

## Fallback

If `spawn_agents_on_csv` is unavailable, use a bounded replenishing pool: spawn up to the current cap, wait for a completion, collect and close it, then spawn the next pending job. Do not create one root-thread tool call per item up front.
