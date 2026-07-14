#!/usr/bin/env python3
"""Durable, bounded orchestration of homogeneous `codex exec` jobs."""
from __future__ import annotations

import argparse
import concurrent.futures
import hashlib
import json
import os
import re
import signal
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

MODELS = {"gpt-5.6-luna", "gpt-5.6-terra", "gpt-5.6-sol"}
EFFORTS = {"low", "medium", "high", "xhigh"}
SANDBOXES = {"read-only", "workspace-write"}
MIN_CODEX_VERSION = (0, 144, 2)
ROUTE_ATTESTATION = "requested-via-cli-arguments-not-runtime-attested"
APP_CODEX_PATHS = (
    Path("/Applications/ChatGPT.app/Contents/Resources/codex"),
    Path("/Applications/Codex.app/Contents/Resources/codex"),
)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def atomic_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(value, handle, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass


def parse_version(text: str) -> tuple[int, ...]:
    match = re.search(r"(\d+)\.(\d+)\.(\d+)", text)
    if not match:
        raise ValueError(f"could not parse Codex version from {text!r}")
    return tuple(int(part) for part in match.groups())


def binary_version(path: Path) -> tuple[tuple[int, ...], str]:
    result = subprocess.run(
        [str(path), "--version"], capture_output=True, text=True, timeout=10, check=False
    )
    output = (result.stdout or result.stderr).strip()
    if result.returncode:
        raise ValueError(f"{path} --version failed: {output}")
    return parse_version(output), output


def select_codex_binary() -> tuple[Path, str]:
    override = os.environ.get("CODEX_BIN")
    if override:
        candidates = [Path(override).expanduser()]
    else:
        candidates = list(APP_CODEX_PATHS)
        path_binary = shutil.which("codex")
        if path_binary:
            candidates.append(Path(path_binary))

    compatible: list[tuple[tuple[int, ...], Path, str]] = []
    errors: list[str] = []
    seen: set[Path] = set()
    for candidate in candidates:
        try:
            resolved = candidate.resolve()
            if resolved in seen or not resolved.is_file() or not os.access(resolved, os.X_OK):
                continue
            seen.add(resolved)
            version, display = binary_version(resolved)
            if version >= MIN_CODEX_VERSION:
                compatible.append((version, resolved, display))
            else:
                errors.append(f"{resolved}: {display} is older than 0.144.2")
        except (OSError, subprocess.SubprocessError, ValueError) as error:
            errors.append(f"{candidate}: {error}")
    if not compatible:
        detail = "; ".join(errors) or "no executable candidates found"
        raise RuntimeError(f"no compatible Codex CLI: {detail}")
    _, path, display = max(compatible, key=lambda item: item[0])
    return path, display


@dataclass(frozen=True)
class Job:
    id: str
    prompt: str
    model: str
    effort: str
    sandbox: str
    workdir: Path

    @property
    def fingerprint(self) -> str:
        payload = {
            "id": self.id, "prompt": self.prompt, "model": self.model,
            "effort": self.effort, "sandbox": self.sandbox, "workdir": str(self.workdir),
        }
        return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()

    def public_spec(self) -> dict[str, str]:
        return {
            "id": self.id, "model": self.model, "effort": self.effort,
            "sandbox": self.sandbox, "workdir": str(self.workdir),
            "prompt_sha256": hashlib.sha256(self.prompt.encode()).hexdigest(),
        }


def load_jobs(path: Path) -> list[Job]:
    jobs: list[Job] = []
    ids: set[str] = set()
    required = {"id", "prompt", "model", "effort", "sandbox", "workdir"}
    for line_number, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not raw.strip():
            continue
        try:
            value = json.loads(raw)
        except json.JSONDecodeError as error:
            raise ValueError(f"{path}:{line_number}: invalid JSON: {error.msg}") from error
        if not isinstance(value, dict):
            raise ValueError(f"{path}:{line_number}: each job must be an object")
        missing = required - value.keys()
        extra = value.keys() - required
        if missing or extra:
            raise ValueError(f"{path}:{line_number}: missing={sorted(missing)} extra={sorted(extra)}")
        job_id = value["id"]
        if not isinstance(job_id, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", job_id):
            raise ValueError(f"{path}:{line_number}: invalid id")
        if job_id in ids:
            raise ValueError(f"{path}:{line_number}: duplicate id {job_id!r}")
        if not isinstance(value["prompt"], str) or not value["prompt"].strip():
            raise ValueError(f"{path}:{line_number}: prompt must be non-empty")
        if value["model"] not in MODELS:
            raise ValueError(f"{path}:{line_number}: unsupported model {value['model']!r}")
        if value["effort"] not in EFFORTS:
            raise ValueError(f"{path}:{line_number}: unsupported effort {value['effort']!r}")
        if value["sandbox"] not in SANDBOXES:
            raise ValueError(f"{path}:{line_number}: unsupported sandbox {value['sandbox']!r}")
        if not isinstance(value["workdir"], str) or not value["workdir"]:
            raise ValueError(f"{path}:{line_number}: workdir must be a path string")
        workdir = Path(value["workdir"]).expanduser()
        if not workdir.is_absolute():
            workdir = (Path.cwd() / workdir).resolve()
        if not workdir.is_dir():
            raise ValueError(f"{path}:{line_number}: workdir is not a directory: {workdir}")
        ids.add(job_id)
        jobs.append(Job(job_id, value["prompt"], value["model"], value["effort"], value["sandbox"], workdir))
    if not jobs:
        raise ValueError(f"{path}: no jobs")
    return jobs


def extract_thread_ids(events_path: Path) -> list[str]:
    found: list[str] = []
    id_pattern = re.compile(r"^[0-9a-f]{8}-[0-9a-f-]{20,}$", re.IGNORECASE)

    def visit(value: Any, key: str = "") -> None:
        if isinstance(value, dict):
            for child_key, child in value.items():
                visit(child, child_key)
        elif isinstance(value, list):
            for child in value:
                visit(child, key)
        elif isinstance(value, str) and key in {"thread_id", "threadId", "session_id", "sessionId"}:
            if id_pattern.match(value) and value not in found:
                found.append(value)

    if events_path.exists():
        for raw in events_path.read_text(encoding="utf-8", errors="replace").splitlines():
            try:
                visit(json.loads(raw))
            except json.JSONDecodeError:
                continue
    return found


class Manifest:
    def __init__(self, path: Path, jobs_path: Path, codex_path: Path, codex_version: str):
        self.path = path
        self.lock = threading.Lock()
        if path.exists():
            self.data = json.loads(path.read_text(encoding="utf-8"))
            if self.data.get("jobs_file") != str(jobs_path.resolve()):
                raise ValueError(
                    f"run directory belongs to a different jobs file: {self.data.get('jobs_file')}"
                )
        else:
            self.data = {
                "schema_version": 1, "created_at": utc_now(), "jobs_file": str(jobs_path.resolve()),
                "backend": "codex-exec", "codex": {"path": str(codex_path), "version": codex_version},
                "route_attestation": ROUTE_ATTESTATION, "jobs": {},
            }
        self.data["updated_at"] = utc_now()
        atomic_json(self.path, self.data)

    def get(self, job_id: str) -> dict[str, Any] | None:
        with self.lock:
            return self.data["jobs"].get(job_id)

    def set(self, job_id: str, value: dict[str, Any]) -> None:
        with self.lock:
            self.data["jobs"][job_id] = value
            self.data["updated_at"] = utc_now()
            atomic_json(self.path, self.data)


def build_command(binary: Path, job: Job, output_path: Path) -> list[str]:
    return [
        str(binary), "exec", "--model", job.model,
        "-c", f'model_reasoning_effort="{job.effort}"',
        "--sandbox", job.sandbox, "--cd", str(job.workdir),
        "--json", "--color", "never", "--output-last-message", str(output_path), "-",
    ]


def run_job(
    job: Job, binary: Path, run_dir: Path, manifest: Manifest,
    timeout: int, retries: int, backoff: float,
) -> bool:
    prior = manifest.get(job.id)
    attempts = list(prior.get("attempts", [])) if prior and prior.get("fingerprint") == job.fingerprint else []
    entry: dict[str, Any] = {
        "spec": job.public_spec(), "fingerprint": job.fingerprint, "status": "running",
        "route_attestation": ROUTE_ATTESTATION, "attempts": attempts,
    }
    manifest.set(job.id, entry)
    job_dir = run_dir / "jobs" / job.id
    job_dir.mkdir(parents=True, exist_ok=True)
    existing_numbers = [
        int(match.group(1))
        for path in job_dir.glob("attempt-*.events.jsonl")
        if (match := re.match(r"attempt-(\d+)\.events\.jsonl$", path.name))
    ]
    next_attempt_no = max(existing_numbers, default=0) + 1

    for retry_index in range(retries + 1):
        attempt_no = next_attempt_no + retry_index
        events_path = job_dir / f"attempt-{attempt_no:03d}.events.jsonl"
        stderr_path = job_dir / f"attempt-{attempt_no:03d}.stderr.log"
        temporary_output = job_dir / f".attempt-{attempt_no:03d}.final.tmp"
        final_output = job_dir / f"attempt-{attempt_no:03d}.final.txt"
        command = build_command(binary, job, temporary_output)
        attempt: dict[str, Any] = {
            "number": attempt_no, "started_at": utc_now(), "status": "running",
            "events": str(events_path), "stderr": str(stderr_path),
            "command": command[:-1] + ["<prompt-via-stdin>"],
        }
        attempts.append(attempt)
        manifest.set(job.id, entry)
        timed_out = False
        try:
            with events_path.open("wb") as stdout, stderr_path.open("wb") as stderr:
                process = subprocess.Popen(
                    command, stdin=subprocess.PIPE, stdout=stdout, stderr=stderr,
                    start_new_session=(os.name == "posix"),
                )
                try:
                    process.communicate(input=job.prompt.encode("utf-8"), timeout=timeout)
                except subprocess.TimeoutExpired:
                    timed_out = True
                    if os.name == "posix":
                        os.killpg(process.pid, signal.SIGTERM)
                    else:
                        process.terminate()
                    try:
                        process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        if os.name == "posix":
                            os.killpg(process.pid, signal.SIGKILL)
                        else:
                            process.kill()
                        process.wait()
                returncode = process.returncode
        except OSError as error:
            returncode = 127
            stderr_path.write_text(str(error), encoding="utf-8")

        attempt.update({
            "finished_at": utc_now(), "returncode": returncode,
            "status": "timed_out" if timed_out else ("succeeded" if returncode == 0 else "failed"),
            "thread_ids": extract_thread_ids(events_path),
        })
        if temporary_output.exists():
            os.replace(temporary_output, final_output)
            attempt["final_output"] = str(final_output)
        if returncode == 0 and not timed_out:
            entry.update({
                "status": "succeeded", "finished_at": utc_now(),
                "final_output": attempt.get("final_output"), "thread_ids": attempt["thread_ids"],
            })
            manifest.set(job.id, entry)
            return True
        manifest.set(job.id, entry)
        if retry_index < retries:
            time.sleep(backoff * (2 ** retry_index))

    entry.update({"status": "failed", "finished_at": utc_now(), "terminal_error": attempts[-1]["status"]})
    manifest.set(job.id, entry)
    return False


def run_pool(
    jobs: Iterable[Job], binary: Path, run_dir: Path, manifest: Manifest,
    concurrency: int, write_concurrency: int, timeout: int, retries: int, backoff: float,
) -> bool:
    pending = list(jobs)
    running: dict[concurrent.futures.Future[bool], Job] = {}
    writer_count = 0
    all_ok = True
    with concurrent.futures.ThreadPoolExecutor(max_workers=concurrency) as executor:
        while pending or running:
            index = 0
            while len(running) < concurrency and index < len(pending):
                job = pending[index]
                is_writer = job.sandbox == "workspace-write"
                if is_writer and writer_count >= write_concurrency:
                    index += 1
                    continue
                pending.pop(index)
                if is_writer:
                    writer_count += 1
                future = executor.submit(run_job, job, binary, run_dir, manifest, timeout, retries, backoff)
                running[future] = job
            if not running:
                raise RuntimeError("scheduler deadlock")
            done, _ = concurrent.futures.wait(running, return_when=concurrent.futures.FIRST_COMPLETED)
            for future in done:
                job = running.pop(future)
                if job.sandbox == "workspace-write":
                    writer_count -= 1
                try:
                    all_ok = future.result() and all_ok
                except Exception as error:  # preserve an unexpected worker failure in the manifest
                    all_ok = False
                    manifest.set(job.id, {
                        "spec": job.public_spec(), "fingerprint": job.fingerprint,
                        "status": "failed", "finished_at": utc_now(), "terminal_error": repr(error),
                    })
    return all_ok


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("jobs", type=Path, help="JSONL job file")
    parser.add_argument("--run-dir", type=Path)
    parser.add_argument("--concurrency", type=int, default=3)
    parser.add_argument("--write-concurrency", type=int, default=1)
    parser.add_argument("--timeout", type=int, default=1800)
    parser.add_argument("--retries", type=int, default=1)
    parser.add_argument("--backoff", type=float, default=2.0)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    if args.concurrency < 1 or args.write_concurrency < 1 or args.write_concurrency > args.concurrency:
        parser.error("concurrency values must be positive and write concurrency cannot exceed total")
    if args.timeout < 1 or args.retries < 0 or args.backoff < 0:
        parser.error("timeout must be positive; retries and backoff must be non-negative")

    try:
        jobs = load_jobs(args.jobs)
        binary, version = select_codex_binary()
    except (OSError, ValueError, RuntimeError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 2
    run_dir = (args.run_dir or Path(".codex/orchestration-runs") / args.jobs.stem).resolve()
    if args.dry_run:
        result = {
            "backend": "codex-exec", "codex": {"path": str(binary), "version": version},
            "route_attestation": ROUTE_ATTESTATION, "run_dir": str(run_dir),
            "concurrency": args.concurrency, "write_concurrency": args.write_concurrency,
            "jobs": [job.public_spec() for job in jobs],
        }
        print(json.dumps(result, indent=2, sort_keys=True))
        return 0

    run_dir.mkdir(parents=True, exist_ok=True)
    try:
        manifest = Manifest(run_dir / "manifest.json", args.jobs, binary, version)
    except (OSError, ValueError, json.JSONDecodeError) as error:
        print(f"ERROR: could not open run manifest: {error}", file=sys.stderr)
        return 2
    selected: list[Job] = []
    for job in jobs:
        prior = manifest.get(job.id)
        if prior and prior.get("status") == "succeeded" and prior.get("fingerprint") == job.fingerprint:
            print(f"SKIP {job.id}: already succeeded")
        else:
            selected.append(job)
    ok = run_pool(
        selected, binary, run_dir, manifest, args.concurrency, args.write_concurrency,
        args.timeout, args.retries, args.backoff,
    ) if selected else True
    succeeded = sum(1 for job in jobs if (manifest.get(job.id) or {}).get("status") == "succeeded")
    print(f"{succeeded}/{len(jobs)} jobs succeeded; manifest: {manifest.path}")
    return 0 if ok and succeeded == len(jobs) else 1


if __name__ == "__main__":
    raise SystemExit(main())
