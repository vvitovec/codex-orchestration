# Codex Orchestration

A configurable orchestration toolkit for OpenAI Codex and the GPT‑5.6 model family.

It turns the strongest model into a control plane: the root agent understands the project, divides it into useful deliverables, routes each lane to Luna, Terra, or Sol, supervises the work, and verifies the integrated result. The root does not implement changes itself.

## Why I built this

GPT‑5.6 Ultra is positioned as the most capable Codex option for large projects. In my experience, though, it falls short in a few frustrating ways: it tends to give subagents the same heavyweight GPT‑5.6 setup at extreme reasoning effort, then orchestrates them one by one. Both choices can burn an absurd number of tokens without producing proportionally better work.

That is why I built this: a **programmatic, scalable, and configurable** way to orchestrate your agents.

## Why use it?

- Spend expensive reasoning at decision points instead of on routine edits.
- Keep the root context clean by moving searches, logs, tests, and implementation into worker threads.
- Avoid both delegation extremes: agents receive coherent deliverables, not single-line chores or entire unresolved codebases.
- Keep one writer by default while parallelizing safe exploration, testing, and independent review.
- Scale homogeneous workloads through a durable, bounded `codex exec` queue.
- Update model routing independently from the stable orchestration workflow.

## What is included?

Five cooperating skills:

- `orchestrate-work` — task graph and control loop
- `route-subagents` — model, effort, and escalation policy
- `compose-delegation` — short worker prompt contract
- `integrate-and-verify` — evidence review and final acceptance
- `scale-agent-pool` — large programmatic batch fan-out

Five custom agents cover Luna implementation, Terra exploration and implementation, and Sol specialist/reviewer work. `scripts/orchestrate.py` adds a resumable execution backend when exact launch configuration matters more than interactive native-agent steering.

## Install

```bash
git clone https://github.com/vvitovec/codex-orchestration.git
cd codex-orchestration
./scripts/install-personal.sh
```

Restart Codex after installation. The installer copies skills to `~/.codex/skills`, agent presets to `~/.codex/agents`, and runner scripts to `~/.codex/orchestration/scripts`; it refuses to overwrite existing destinations.

For project-only use, copy `agents/*.toml` into `<project>/.codex/agents/` and load or install the plugin using its `.codex-plugin/plugin.json` manifest.

## Use

Ask naturally and mention orchestration when you want it explicitly:

```text
Orchestrate this feature. Keep the root as the control plane, delegate all
implementation, use the cheapest reliable GPT-5.6 workers, and verify the
integrated result.
```

The skills can also trigger from requests for subagents, delegation, parallel work, or large homogeneous batches.

### Configure scale

Copy the example configuration:

```bash
mkdir -p .codex
cp config/orchestration.example.toml .codex/orchestration.toml
```

Choose one mode:

| Mode | Intended use |
|---|---|
| `conservative` | Small or quota-sensitive work |
| `balanced` | Default projects |
| `large` | Tens of independent workers |
| `adaptive-unrestricted` | No toolkit-defined job-count ceiling |

`adaptive-unrestricted` is not infinite simultaneous spawning. It continuously replenishes available slots and backs off when Codex, the model provider, tools, rate limits, or hardware impose a ceiling.

Write concurrency remains `1` by default. Enable parallel writers only for provably disjoint ownership scopes.

### Run a programmatic pool

Jobs are strict JSONL. Each line contains a stable `id`, bounded `prompt`, allowlisted `model` and `effort`, sandbox, and working directory:

```json
{"id":"repo-summary","prompt":"Inspect this repository without editing it. Return its purpose and verification commands.","model":"gpt-5.6-luna","effort":"low","sandbox":"read-only","workdir":"."}
```

Validate the launch plan, then run it:

```bash
python3 scripts/orchestrate.py examples/jobs.read-only.jsonl --dry-run
python3 scripts/orchestrate.py examples/jobs.read-only.jsonl \
  --concurrency 3 --write-concurrency 1 --timeout 1800 --retries 1
```

From another project after personal installation, invoke `python3 ~/.codex/orchestration/scripts/orchestrate.py <jobs.jsonl>`.

The runner:

- selects the newest compatible Codex CLI (minimum `0.144.2`) from the ChatGPT/Codex app bundle or `PATH`; set `CODEX_BIN=/path/to/codex` to override it;
- sends prompts over stdin and builds subprocess arguments without a shell;
- limits total concurrency and keeps one workspace writer by default;
- writes an atomic manifest, JSONL events, stderr logs, final responses, and discovered thread IDs under `.codex/orchestration-runs/<job-file>/`;
- retries with exponential backoff, enforces per-attempt timeouts, skips unchanged successful jobs on rerun, and exits nonzero for terminal failures.

The CLI route is deliberately labeled `requested-via-cli-arguments-not-runtime-attested`. It proves which command/configuration was launched, but current `codex exec --json` output does not prove the effective backend model and effort. Native interactive subagents remain useful for steering and follow-ups, but their current spawn interface cannot reliably enforce those fields either. An App Server backend with effective-route reporting is the intended next backend behind the same job/manifest contract.

## Model routing

- **Luna:** narrow, high-volume, automatically verifiable work
- **Terra:** context-heavy exploration and bounded subsystem implementation
- **Sol:** architecture, security, concurrency, difficult debugging, and critical review

The complete and updateable matrix is in `skills/route-subagents/references/model-matrix.md`.

## Verify or contribute

```bash
python3 scripts/validate.py
python3 -m unittest discover -s tests -v
```

The package uses the MIT License. Routing defaults are intentionally isolated so new model behavior can be incorporated without rewriting the entire workflow.
