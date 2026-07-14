#!/usr/bin/env python3
from __future__ import annotations

import json
import re
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SKILLS = {
    "orchestrate-work",
    "route-subagents",
    "compose-delegation",
    "integrate-and-verify",
    "scale-agent-pool",
}
AGENTS = {
    "luna-worker": ("gpt-5.6-luna", "medium"),
    "terra-explorer": ("gpt-5.6-terra", "medium"),
    "terra-worker": ("gpt-5.6-terra", "high"),
    "sol-specialist": ("gpt-5.6-sol", "high"),
    "independent-reviewer": ("gpt-5.6-sol", "high"),
}


def fail(message: str) -> None:
    raise ValueError(message)


def parse_frontmatter(path: Path) -> dict[str, str]:
    text = path.read_text()
    match = re.match(r"^---\n(.*?)\n---\n", text, re.DOTALL)
    if not match:
        fail(f"missing frontmatter: {path}")
    values: dict[str, str] = {}
    for line in match.group(1).splitlines():
        if ":" not in line:
            fail(f"invalid frontmatter line in {path}: {line}")
        key, value = line.split(":", 1)
        values[key.strip()] = value.strip()
    return values


def validate() -> None:
    manifest = json.loads((ROOT / ".codex-plugin/plugin.json").read_text())
    if manifest.get("skills") != "./skills/":
        fail("plugin manifest must expose ./skills/")

    actual_skills = {p.name for p in (ROOT / "skills").iterdir() if p.is_dir()}
    if actual_skills != SKILLS:
        fail(f"skill set mismatch: {actual_skills}")
    for name in sorted(SKILLS):
        skill_dir = ROOT / "skills" / name
        frontmatter = parse_frontmatter(skill_dir / "SKILL.md")
        if frontmatter.get("name") != name:
            fail(f"frontmatter name mismatch: {name}")
        if not frontmatter.get("description"):
            fail(f"missing description: {name}")
        if not (skill_dir / "agents/openai.yaml").is_file():
            fail(f"missing agents/openai.yaml: {name}")

    actual_agents = {p.stem for p in (ROOT / "agents").glob("*.toml")}
    if actual_agents != set(AGENTS):
        fail(f"agent set mismatch: {actual_agents}")
    for name, expected in AGENTS.items():
        data = tomllib.loads((ROOT / "agents" / f"{name}.toml").read_text())
        if data.get("name") != name:
            fail(f"agent name mismatch: {name}")
        observed = (data.get("model"), data.get("model_reasoning_effort"))
        if observed != expected:
            fail(f"agent route mismatch for {name}: {observed}")
        for field in ("description", "developer_instructions", "sandbox_mode"):
            if not data.get(field):
                fail(f"missing {field}: {name}")

    matrix = (ROOT / "skills/route-subagents/references/model-matrix.md").read_text()
    for model in ("gpt-5.6-luna", "gpt-5.6-terra", "gpt-5.6-sol"):
        if model not in matrix:
            fail(f"matrix missing {model}")

    resolver_path = ROOT / "scripts/resolve_config.py"
    if not resolver_path.is_file():
        fail("missing scalable-pool config resolver")

    runner_path = ROOT / "scripts/orchestrate.py"
    if not runner_path.is_file():
        fail("missing Codex CLI orchestration runner")
    runner_text = runner_path.read_text()
    for value in ("gpt-5.6-luna", "gpt-5.6-terra", "gpt-5.6-sol", "CODEX_BIN"):
        if value not in runner_text:
            fail(f"runner missing route contract: {value}")
    example_jobs = ROOT / "examples/jobs.read-only.jsonl"
    if not example_jobs.is_file():
        fail("missing read-only runner example")
    installer = (ROOT / "scripts/install-personal.sh").read_text()
    if "orchestration/scripts" not in installer or "orchestrate.py" not in installer:
        fail("personal installer does not install the orchestration runner")


if __name__ == "__main__":
    try:
        validate()
    except (OSError, ValueError, json.JSONDecodeError, tomllib.TOMLDecodeError) as error:
        print(f"INVALID: {error}", file=sys.stderr)
        raise SystemExit(1)
    print("VALID: Codex orchestration plugin")
