# GPT-5.6 routing matrix

Last verified: 2026-07-11

This file owns model-specific policy. Update it without rewriting the stable orchestration skills.

| Level | Shape | Default | Typical work | Escalation signal |
|---|---|---|---|---|
| L0 | Mechanical bundle with one deterministic verifier | Luna Low or Medium | related tiny edits, inventories, formatting, extraction, docs, repetitive checks | hidden dependencies or verifier failure |
| L1 | Bounded deliverable with known scope | Luna Medium | one behavior or fix, test sharding, mechanical implementation | substantial context or judgment |
| L2 | Multi-file change inside one subsystem | Terra Medium or High | routine feature, contextual bug fix, codebase tracing, large-file review | ambiguous contracts or repeated failure |
| L3 | Cross-cutting or difficult | Sol High specialist, then Terra writer when separable | architecture-sensitive work, difficult debugging, migrations, performance | critical risk or unresolved competing designs |
| L4 | Critical judgment | Sol XHigh; Max or Ultra only exceptionally | security, concurrency, irreversible migration, final adjudication | user decision or external blocker |

## Preset mapping

- `luna-worker`: `gpt-5.6-luna`, Medium, workspace write.
- `terra-explorer`: `gpt-5.6-terra`, Medium, read-only.
- `terra-worker`: `gpt-5.6-terra`, High, workspace write.
- `sol-specialist`: `gpt-5.6-sol`, High, read-only by default.
- `independent-reviewer`: `gpt-5.6-sol`, High, read-only.

## Root policy

Use GPT-5.6 Sol High as the normal orchestrator. Use XHigh for highly ambiguous or high-risk projects. Use Max or Ultra only when the additional reasoning is worth the token cost.

## Evidence

- Official Codex subagent guidance: https://developers.openai.com/codex/subagents
- GPT-5.6 release and positioning: https://openai.com/index/gpt-5-6/
- Official GPT-5.6 model catalog: https://developers.openai.com/api/docs/models/all
- Codex custom-agent configuration discussion: https://github.com/openai/codex/issues/11701

The Luna assignment policy is an operational default to benchmark. Re-evaluate it when official Codex guidance or observed behavior changes.
