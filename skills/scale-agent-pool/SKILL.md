---
name: scale-agent-pool
description: Run tens or more homogeneous Codex subagent jobs through a configurable programmatic queue instead of manually spawning every worker. Use for large inventories, test shards, independent reviews, migrations with disjoint ownership, datasets, or other map-reduce-shaped work.
---

# Scale Agent Pool

Use programmatic batch fan-out for a large collection of uniform, independent jobs. Prefer the plugin's durable `scripts/orchestrate.py` runner. It launches separate `codex exec` processes with explicit model, effort, sandbox, and working-directory arguments instead of relying on native subagent routing that may ignore those choices.

## Load configuration

Resolve configuration from low to high precedence:

1. installed/bundled defaults;
2. `~/.codex/orchestration.toml` or `$CODEX_HOME/orchestration.toml`;
3. `.codex/orchestration.toml` in the project;
4. an explicit `--config` path;
5. explicit runner flags.

Validate it with `scripts/resolve_config.py` from the plugin root when available. Project configuration overrides user configuration; absent values inherit defaults.

## Modes

- `conservative`: small fan-out for expensive or fragile environments.
- `balanced`: normal default.
- `large`: a larger fixed pool when work is independent and quotas support it.

These modes select fixed concurrency defaults. The CLI runner does not currently adapt live concurrency or implement a circuit breaker.

## Batch protocol

1. Confirm the jobs are homogeneous and independently verifiable.
2. Materialize JSONL jobs with `id`, `prompt`, `model`, `effort`, `sandbox`, and `workdir` fields. Keep IDs stable across reruns. Add optional `safe_retry: true` only for a provably idempotent writer. Parallel writers also require unique, non-empty `ownership_scope` values.
3. Use only the validated GPT-5.6 Luna/Terra/Sol routes and low/medium/high/xhigh effort.
4. Encode ownership, verifier, and the requested structured return directly in each bounded prompt.
5. Set concurrency from the resolved configuration, capped by the live Codex `agents.max_threads` and runtime/tool limits.
6. Run `python3 scripts/orchestrate.py <jobs.jsonl>` from the plugin checkout, or `python3 ~/.codex/orchestration/scripts/orchestrate.py <jobs.jsonl>` after personal installation. Use `--dry-run` first for a new batch.
7. Accept success only with a zero exit, `turn.completed`, and a non-empty persisted final output.
8. Retry read-only failures up to `max_retries` with exponential backoff. Do not retry a writer unless its job explicitly declares safe retry/idempotency.
9. On resume, skip success only when the persisted output still matches its recorded hash and size.
10. Inspect the atomic manifest and per-attempt logs under `.codex/orchestration-runs/`; pass only exceptions, summaries, and evidence to the root.

## Safety gates

- Large write batches require disjoint ownership encoded per row as `ownership_scope`.
- Keep `max_write_concurrency = 1` unless configuration sets `allow_disjoint_parallel_writers = true`. CLI overrides above one additionally require `--allow-disjoint-parallel-writers`. Every workspace writer then needs a unique, non-empty scope.
- Stop the batch when a worker requests stop, results indicate shared-state conflict, or verification shows systemic failure.
- Preserve partial results so a resumed run does not repeat completed jobs.
- Never bypass Codex, account, sandbox, or provider limits.

## Routing evidence

The CLI runner proves that the command accepted explicit model/effort configuration. Treat this as **command/config accepted, not runtime-attested**: Codex CLI JSONL currently does not attest the effective route. A future App Server backend may add effective-route attestation.

## Fallback

If the runner is unavailable, use a bounded replenishing native pool and explicitly report that model and effort are not enforceable through the native spawn interface. Do not create one root-thread tool call per item up front.
