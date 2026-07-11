# Codex Orchestration

A plugin-ready set of Codex skills and custom-agent presets for a GPT-5.6 control-plane workflow.

The root agent may inspect, decompose, synthesize, review, and verify. It must not implement edits. Implementation is delegated to one active writer by default; independent exploration and review may run in parallel.

## Contents

- `skills/orchestrate-work`: end-to-end control loop and task graph
- `skills/route-subagents`: model, effort, and escalation policy
- `skills/compose-delegation`: concise worker prompt contract
- `skills/integrate-and-verify`: evidence review, follow-ups, and acceptance
- `skills/scale-agent-pool`: programmatic batch fan-out with adaptive concurrency
- `agents/`: Luna, Terra, and Sol custom-agent presets
- `scripts/`: validation and personal-install helpers

## Install for local Codex

Run `./scripts/install-personal.sh` to copy the four skills and five agent presets into `~/.codex`. Existing destinations are refused rather than overwritten. Restart Codex after installation.

For a project-local installation, copy `agents/*.toml` into `.codex/agents/` and install or load the plugin through the Codex plugin workflow.

## Validate

```bash
python3 scripts/validate.py
python3 -m unittest discover -s tests -v
```

## Update policy

Model-specific routing is centralized in `skills/route-subagents/references/model-matrix.md`. Refresh that file first when model behavior or Codex spawning changes. Keep stable delegation and verification contracts unchanged unless testing shows a workflow defect.

## Scale modes

Copy `config/orchestration.example.toml` to `.codex/orchestration.toml` or `~/.codex/orchestration.toml` and choose `conservative`, `balanced`, `large`, or `adaptive-unrestricted`.

`adaptive-unrestricted` does not launch an infinite number of agents simultaneously. It removes the plugin's task-count ceiling and uses a replenishing queue whose live concurrency is still bounded by Codex `agents.max_threads`, rate limits, available resources, and backoff. For homogeneous work, the orchestrator uses Codex's programmatic `spawn_agents_on_csv` primitive instead of manually issuing one spawn per row.
