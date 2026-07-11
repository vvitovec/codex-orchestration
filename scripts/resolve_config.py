#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULTS = ROOT / "skills/scale-agent-pool/references/defaults.toml"
MODES = {
    "conservative": {"initial_concurrency": 2, "max_concurrency": 3},
    "balanced": {"initial_concurrency": 3, "max_concurrency": 6},
    "large": {"initial_concurrency": 8, "max_concurrency": 32},
    "adaptive-unrestricted": {"initial_concurrency": 4, "max_concurrency": 0},
}


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


def resolve(path: Path | None = None) -> dict:
    config = load(DEFAULTS)
    if path:
        config = merge(config, load(path))
    mode = config.get("mode")
    if mode not in MODES:
        raise ValueError(f"unknown mode: {mode}")
    pool = config.setdefault("pool", {})
    supplied = load(path).get("pool", {}) if path else {}
    for key, value in MODES[mode].items():
        if key not in supplied:
            pool[key] = value
    integers = (
        "initial_concurrency", "max_concurrency", "max_pending_tasks", "max_retries",
        "job_timeout_seconds", "success_window", "circuit_breaker_failures",
    )
    for key in integers:
        value = pool.get(key)
        if not isinstance(value, int) or value < 0:
            raise ValueError(f"pool.{key} must be a non-negative integer")
    if pool["initial_concurrency"] < 1:
        raise ValueError("pool.initial_concurrency must be at least 1")
    cap = pool["max_concurrency"]
    if cap and pool["initial_concurrency"] > cap:
        raise ValueError("initial_concurrency cannot exceed max_concurrency")
    factor = pool.get("backoff_factor")
    if not isinstance(factor, (int, float)) or not 0 < factor < 1:
        raise ValueError("pool.backoff_factor must be between 0 and 1")
    writes = config.get("writes", {})
    if not isinstance(writes.get("max_write_concurrency"), int) or writes["max_write_concurrency"] < 1:
        raise ValueError("writes.max_write_concurrency must be at least 1")
    return config


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("path", nargs="?", type=Path)
    args = parser.parse_args()
    print(json.dumps(resolve(args.path), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
