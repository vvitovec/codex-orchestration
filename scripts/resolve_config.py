#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import tomllib
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
REPO_DEFAULTS = SCRIPT_DIR.parent / "skills/scale-agent-pool/references/defaults.toml"
INSTALLED_DEFAULTS = SCRIPT_DIR.parent / "config/defaults.toml"
MODES = {"conservative": 2, "balanced": 3, "large": 8}


def merge(base: dict, override: dict) -> dict:
    result = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = merge(result[key], value)
        else:
            result[key] = value
    return result


def load(path: Path) -> dict:
    with path.open("rb") as handle:
        return tomllib.load(handle)


def defaults_path() -> Path:
    for candidate in (REPO_DEFAULTS, INSTALLED_DEFAULTS):
        if candidate.is_file():
            return candidate
    raise FileNotFoundError("orchestration defaults.toml not found beside repository or installation")


def config_paths(
    explicit: Path | None = None, cwd: Path | None = None, codex_home: Path | None = None,
) -> list[Path]:
    cwd = (cwd or Path.cwd()).resolve()
    if codex_home is None:
        codex_home = Path(os.environ.get("CODEX_HOME", Path.home() / ".codex")).expanduser()
    if explicit and not explicit.expanduser().is_file():
        raise FileNotFoundError(f"explicit config does not exist: {explicit}")
    paths = [codex_home / "orchestration.toml", cwd / ".codex/orchestration.toml"]
    if explicit:
        paths.append(explicit.expanduser().resolve())
    result: list[Path] = []
    for path in paths:
        if path.is_file() and path not in result:
            result.append(path)
    return result


def resolve(
    path: Path | None = None, cwd: Path | None = None, codex_home: Path | None = None,
) -> dict:
    config = load(defaults_path())
    overrides: dict = {}
    sources = [defaults_path()]
    for candidate in config_paths(path, cwd, codex_home):
        value = load(candidate)
        overrides = merge(overrides, value)
        config = merge(config, value)
        sources.append(candidate)

    mode = config.get("mode")
    if mode not in MODES:
        raise ValueError(f"unknown mode: {mode}")
    pool = config.setdefault("pool", {})
    if "concurrency" not in overrides.get("pool", {}):
        pool["concurrency"] = MODES[mode]
    for key in ("concurrency", "max_retries", "job_timeout_seconds"):
        value = pool.get(key)
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            raise ValueError(f"pool.{key} must be a non-negative integer")
    if pool["concurrency"] < 1 or pool["job_timeout_seconds"] < 1:
        raise ValueError("pool concurrency and timeout must be at least 1")
    backoff = pool.get("retry_backoff_seconds")
    if not isinstance(backoff, (int, float)) or isinstance(backoff, bool) or backoff < 0:
        raise ValueError("pool.retry_backoff_seconds must be non-negative")
    writes = config.get("writes", {})
    write_concurrency = writes.get("max_write_concurrency")
    if not isinstance(write_concurrency, int) or isinstance(write_concurrency, bool) or write_concurrency < 1:
        raise ValueError("writes.max_write_concurrency must be at least 1")
    if write_concurrency > pool["concurrency"]:
        raise ValueError("write concurrency cannot exceed total concurrency")
    runner = config.get("runner", {})
    if not isinstance(runner.get("run_root"), str) or not runner["run_root"]:
        raise ValueError("runner.run_root must be a non-empty string")
    config["_sources"] = [str(source) for source in sources]
    return config


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("path", nargs="?", type=Path)
    args = parser.parse_args()
    print(json.dumps(resolve(args.path), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
