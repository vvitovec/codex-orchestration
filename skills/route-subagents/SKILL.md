---
name: route-subagents
description: Select the GPT-5.6 subagent model, reasoning effort, read/write role, and escalation path for a bounded lane. Use as part of orchestrated Codex work or when deciding whether Luna, Terra, or Sol should receive a task.
---

# Route Subagents

Read `references/model-matrix.md` before routing. Choose the least expensive model that can reliably finish and verify the lane.

## Decision order

1. Decide whether delegation has positive value. Avoid it when coordination costs exceed the work.
2. Classify the lane by deliverable complexity, context load, judgment risk, and verifier strength.
3. Select read-only or writer mode.
4. Select the model tier and effort from the matrix.
5. Record why the lower tier is insufficient when choosing Sol.
6. Verify the actual spawned model/effort when the runtime exposes it. Never claim a requested route was honored without evidence.

## Stable rules

- Luna is for narrow, high-volume, self-contained work with a strong verifier.
- Terra is for read-heavy context compression and bounded implementation requiring broader context.
- Sol is for difficult judgment, ambiguity, cross-cutting synthesis, critical review, or recovery.
- Medium is the default. Low requires mechanical work. High requires complex logic or edge cases. XHigh is reserved for critical judgment. Max or Ultra is an exceptional root/specialist escalation, not a routine worker setting.
- Escalate Luna to Terra for materially more context or non-mechanical judgment.
- Escalate Terra to Sol for ambiguity, cross-cutting contracts, security, concurrency, or expensive recovery.

## Runtime fallback

If exact model/effort launch configuration matters for a homogeneous or resumable job, use the plugin's `scripts/orchestrate.py` CLI runner. It passes both explicitly but must still be labeled command/config accepted, not runtime-attested. For interactive native spawning, restate the selected role, model, effort, scope, and constraints in the prompt; if the runtime cannot honor the selection, continue only when the observed model is at least as capable and the cost is acceptable.
