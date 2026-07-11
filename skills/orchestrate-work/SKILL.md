---
name: orchestrate-work
description: Orchestrate a non-trivial task through GPT-5.6 subagents while the root remains a control plane. Use when the user asks for subagents, orchestration, delegation, parallel work, or a project whose independent lanes benefit from specialized agents. The root may inspect, decompose, synthesize, review, and verify, but must not implement edits.
---

# Orchestrate Work

Run the task as a control plane. Do not author implementation changes from the root thread.

## Hard boundary

The root may:

- inspect the environment and applicable instructions;
- resolve user intent and acceptance criteria;
- build and revise the work graph;
- spawn, steer, interrupt, and follow up with agents;
- inspect diffs and evidence;
- run final read-only checks and synthesize the result.

The root must delegate all edits, generated deliverables, migrations, configuration changes, and implementation fixes. If no subagent facility is available, stop and report that orchestration cannot meet this skill's contract. Do not silently implement.

## Control loop

1. Ground in the real environment. Read applicable `AGENTS.md`, repository state, entrypoints, tests, and deployment instructions.
2. State the requested outcome and concrete acceptance checks.
3. Build a work graph of coherent deliverables. Each node must have one ownership boundary and one verification boundary.
4. Use `$route-subagents` to choose the least expensive reliable agent and effort.
5. Use `$compose-delegation` for every assignment.
6. For a normal work graph, spawn independent read-heavy lanes in parallel. Default to at most three initial workers.
7. For tens or more homogeneous items, invoke `$scale-agent-pool` and use programmatic batch fan-out instead of manual per-item spawning.
8. Keep one active writer unless ownership is provably disjoint. Name the exact disjoint modules before allowing another writer.
9. Wait for dependencies. Reuse the same worker thread for a focused follow-up on the same deliverable.
10. Use `$integrate-and-verify` to accept, reject, refine, or escalate each result.
11. Finish only after project-level verification covers the integrated result.

## Granularity

Delegate one coherent deliverable, not one line and not an unresolved project.

- Bundle tiny related edits that share purpose and checks.
- Split a large task at behavior, subsystem, ownership, or verification boundaries.
- Delegate exploration before implementation when file ownership or architecture is unclear.
- Never send one worker a vague instruction such as "refactor the codebase".
- Do not create more lanes than useful independent outcomes.

## Work graph record

For each node track: ID, outcome, dependencies, owner scope, read/write mode, selected agent, effort, verification, state, and returned evidence. Keep this concise and internal unless the user asks to see it.

## Failure policy

- Do not repeat an unchanged failed prompt.
- First refine missing context or scope on the same thread.
- Escalate model tier when failure reflects context, judgment, or recovery difficulty.
- Stop conflicting writers before integration.
- Report the observed agent/model behavior when it differs from the requested route.
