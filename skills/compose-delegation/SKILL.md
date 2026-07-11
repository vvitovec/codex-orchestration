---
name: compose-delegation
description: Turn a work-graph node into a short, bounded Codex subagent prompt with explicit ownership, constraints, verification, and return evidence. Use immediately before every subagent spawn or focused follow-up.
---

# Compose Delegation

Prefer the shortest prompt that makes the assignment decision-complete.

## Required contract

Use exactly these fields unless one is genuinely irrelevant:

```text
Outcome: <one concrete result>
Scope: <owned files/module, or the exact read-only question>
Context: <only paths, decisions, and facts needed>
Constraints: <important invariants, exclusions, and collaboration rules>
Verify: <exact checks or evidence>
Return: <summary, changed files/findings, checks, blockers>
```

Add `Model:` and `Effort:` only when the spawn mechanism needs an explicit override or runtime verification.

## Prompt rules

- Lead with the deliverable, not a persona or a long procedure.
- Give paths and symbols instead of pasting the entire parent history.
- State whether the task is read-only or authorizes edits.
- For writers, name the ownership boundary and warn that other agents or the user may have changes.
- Include acceptance checks the worker can actually run.
- Ask for distilled evidence, not raw logs.
- Do not tell a capable worker how to perform ordinary implementation steps.
- Do not combine unrelated outcomes merely to reduce agent count.
- Do not split a coherent outcome into file-by-file microtasks.

## Quality gate

Do not spawn until the assignment has one outcome, non-overlapping scope, known constraints, a verifier, and a useful return shape. If any is missing, delegate discovery first or resolve it from the environment.
