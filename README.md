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

Restart Codex after installation. The installer copies skills to `~/.codex/skills`, agent presets to `~/.codex/agents`, runner scripts to `~/.codex/orchestration/scripts`, and its defaults beside the installed resolver. It refuses existing destinations on first install. Use `./scripts/install-personal.sh --upgrade` to replace destinations recorded as package-owned (or structurally verified from a legacy installation) while preserving unrelated files.

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
| `large` | A larger fixed pool for independent workers |

Write concurrency remains `1` by default. Configuration with `max_write_concurrency > 1` is rejected unless `allow_disjoint_parallel_writers = true`. A CLI override above one additionally requires `--allow-disjoint-parallel-writers`. Every workspace writer in such a run must provide a unique, non-empty `ownership_scope`.

Configuration is resolved in this order: bundled defaults, `~/.codex/orchestration.toml` (or `$CODEX_HOME/orchestration.toml`), project `.codex/orchestration.toml`, then an explicit `--config` file. Explicit CLI flags take final precedence. The runner consumes concurrency, write concurrency, timeout, retries, retry backoff, and run root from the resolved configuration.

### Run a programmatic pool

Jobs are strict JSONL. Each line contains a stable `id`, bounded `prompt`, allowlisted `model` and `effort`, sandbox, and working directory. `safe_retry: true` is optional and must only be used for an idempotent workspace writer:

```json
{"id":"repo-summary","prompt":"Inspect this repository without editing it. Return its purpose and verification commands.","model":"gpt-5.6-luna","effort":"low","sandbox":"read-only","workdir":"."}
```

Parallel writer jobs declare disjoint ownership explicitly:

```json
{"id":"api","prompt":"Implement and verify the bounded API change.","model":"gpt-5.6-terra","effort":"high","sandbox":"workspace-write","workdir":".","ownership_scope":"src/api","safe_retry":false}
{"id":"docs","prompt":"Update and verify the related documentation.","model":"gpt-5.6-luna","effort":"medium","sandbox":"workspace-write","workdir":".","ownership_scope":"docs","safe_retry":true}
```

Launch that batch with both the concurrency value and explicit safety opt-in:

```bash
python3 scripts/orchestrate.py parallel-writers.jsonl \
  --concurrency 2 --write-concurrency 2 --allow-disjoint-parallel-writers
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
- rejects parallel writers without explicit opt-in and unique ownership scopes;
- takes an OS lock on each run directory and terminates active process groups on SIGINT/SIGTERM;
- writes an atomic manifest, JSONL events, stderr logs, final responses, output hashes/sizes, and discovered thread IDs under `.codex/orchestration-runs/<job-file>/`;
- accepts success only when the process exits zero, emits `turn.completed`, and creates a non-empty final output;
- retries read-only jobs with exponential backoff, but never retries writers unless their job explicitly declares `safe_retry: true`;
- skips only successful jobs whose output artifact still matches the persisted hash and size, and exits nonzero for terminal failures.

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
