---
name: integrate-and-verify
description: Evaluate subagent results, inspect integrated changes, request focused corrections, escalate failures, and verify the final task without implementing from the root. Use after delegated work returns and before declaring orchestrated work complete.
---

# Integrate and Verify

The root remains read-only. It judges evidence and delegates corrections.

## Accept a worker result only when

- the requested outcome is present;
- changes or findings stay inside the assigned boundary;
- constraints and existing user work were preserved;
- verification is relevant and actually ran;
- the return identifies changed files or evidence and any residual risk.

Inspect the diff or artifact directly. Do not accept a worker's self-report as sufficient evidence.

## Response to defects

- Missing context or a small omission: send a focused follow-up to the same worker.
- Incorrect approach but still bounded: restate the invariant and expected behavior once.
- Context or judgment failure: escalate Luna to Terra or Terra to Sol.
- Cross-cutting conflict: stop integration, ask a Sol specialist to adjudicate, then assign one writer the correction.
- Implementation correction required: delegate it. The root must not patch the files.

## Independent review

Use a read-only independent reviewer for security, concurrency, migrations, critical correctness, or substantial cross-cutting changes. Do not ask the original writer to be the only reviewer of its own work.

## Final verification

After all accepted nodes are integrated:

1. Check repository status and the complete diff.
2. Run the narrow checks for each changed lane.
3. Run the project-level build/test/verification required by repository instructions.
4. Verify deployment or external state when the original task includes it.
5. Confirm no active writer or unresolved blocker remains.
6. Report outcomes, checks, model-routing deviations, and genuine blockers.

The root may run read-only verification commands. If verification requires changing tracked files, delegate that change.
