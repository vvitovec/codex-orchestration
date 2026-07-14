#!/usr/bin/env python3
"""Durable, bounded orchestration of homogeneous `codex exec` jobs."""
from __future__ import annotations

import argparse
import concurrent.futures
import fcntl
import hashlib
import importlib.util
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
from typing import Any, Iterable, Optional

MODELS = {"gpt-5.6-luna", "gpt-5.6-terra", "gpt-5.6-sol"}
EFFORTS = {"low", "medium", "high", "xhigh"}
SANDBOXES = {"read-only", "workspace-write"}
MIN_CODEX_VERSION = (0, 144, 2)
ROUTE_ATTESTATION = "requested-via-cli-arguments-not-runtime-attested"
APP_CODEX_PATHS = (
    Path("/Applications/ChatGPT.app/Contents/Resources/codex"),
    Path("/Applications/Codex.app/Contents/Resources/codex"),
)
USER_CODEX_PATH = Path.home() / ".local/bin/codex"


def load_config(path: Optional[Path] = None) -> dict[str, Any]:
    resolver_path = Path(__file__).with_name("resolve_config.py")
    spec = importlib.util.spec_from_file_location("orchestration_resolve_config", resolver_path)
    if not spec or not spec.loader:
        raise RuntimeError(f"cannot load config resolver: {resolver_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.resolve(path)


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
        candidates = list(APP_CODEX_PATHS) + [USER_CODEX_PATH]
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
    safe_retry: bool = False
    ownership_scope: Optional[str] = None

    @property
    def fingerprint(self) -> str:
        payload = {
            "id": self.id, "prompt": self.prompt, "model": self.model,
            "effort": self.effort, "sandbox": self.sandbox, "workdir": str(self.workdir),
            "safe_retry": self.safe_retry,
            "ownership_scope": self.ownership_scope,
        }
        return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()

    def public_spec(self) -> dict[str, Any]:
        return {
            "id": self.id, "model": self.model, "effort": self.effort,
            "sandbox": self.sandbox, "workdir": str(self.workdir),
            "safe_retry": self.safe_retry,
            "ownership_scope": self.ownership_scope,
            "prompt_sha256": hashlib.sha256(self.prompt.encode()).hexdigest(),
        }


def load_jobs(path: Path) -> list[Job]:
    jobs: list[Job] = []
    ids: set[str] = set()
    required = {"id", "prompt", "model", "effort", "sandbox", "workdir"}
    optional = {"safe_retry", "ownership_scope"}
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
        extra = value.keys() - required - optional
        if missing or extra:
            raise ValueError(f"{path}:{line_number}: missing={sorted(missing)} extra={sorted(extra)}")
        job_id = value["id"]
        if not isinstance(job_id, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", job_id):
            raise ValueError(f"{path}:{line_number}: invalid id")
        if job_id in ids:
            raise ValueError(f"{path}:{line_number}: duplicate id {job_id!r}")
        if not isinstance(value["prompt"], str) or not value["prompt"].strip():
            raise ValueError(f"{path}:{line_number}: prompt must be non-empty")
        if not isinstance(value["model"], str) or value["model"] not in MODELS:
            raise ValueError(f"{path}:{line_number}: unsupported model {value['model']!r}")
        if not isinstance(value["effort"], str) or value["effort"] not in EFFORTS:
            raise ValueError(f"{path}:{line_number}: unsupported effort {value['effort']!r}")
        if not isinstance(value["sandbox"], str) or value["sandbox"] not in SANDBOXES:
            raise ValueError(f"{path}:{line_number}: unsupported sandbox {value['sandbox']!r}")
        safe_retry = value.get("safe_retry", False)
        if not isinstance(safe_retry, bool):
            raise ValueError(f"{path}:{line_number}: safe_retry must be a boolean")
        ownership_scope = value.get("ownership_scope")
        if ownership_scope is not None and (
            not isinstance(ownership_scope, str) or not ownership_scope.strip()
        ):
            raise ValueError(f"{path}:{line_number}: ownership_scope must be null or a non-empty string")
        if isinstance(ownership_scope, str):
            ownership_scope = ownership_scope.strip()
        if not isinstance(value["workdir"], str) or not value["workdir"]:
            raise ValueError(f"{path}:{line_number}: workdir must be a path string")
        workdir = Path(value["workdir"]).expanduser()
        if not workdir.is_absolute():
            workdir = (Path.cwd() / workdir).resolve()
        if not workdir.is_dir():
            raise ValueError(f"{path}:{line_number}: workdir is not a directory: {workdir}")
        ids.add(job_id)
        jobs.append(Job(
            job_id, value["prompt"], value["model"], value["effort"],
            value["sandbox"], workdir, safe_retry, ownership_scope,
        ))
    if not jobs:
        raise ValueError(f"{path}: no jobs")
    return jobs


def validate_parallel_write_scopes(jobs: Iterable[Job], write_concurrency: int) -> None:
    if write_concurrency <= 1:
        return
    seen: set[str] = set()
    for job in jobs:
        if job.sandbox != "workspace-write":
            continue
        if not job.ownership_scope:
            raise ValueError(
                f"workspace writer {job.id!r} requires ownership_scope when write concurrency exceeds 1"
            )
        if job.ownership_scope in seen:
            raise ValueError(f"duplicate workspace writer ownership_scope: {job.ownership_scope!r}")
        seen.add(job.ownership_scope)


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


def has_completed_turn(events_path: Path) -> bool:
    if not events_path.exists():
        return False
    for raw in events_path.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            event = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if not isinstance(event, dict) or event.get("type") != "turn.completed":
            continue
        turn = event.get("turn")
        status = turn.get("status") if isinstance(turn, dict) else event.get("status")
        if status in {"failed", "cancelled", "canceled", "interrupted"}:
            continue
        return True
    return False


def artifact_metadata(path: Path) -> Optional[dict[str, Any]]:
    try:
        content = path.read_bytes()
    except OSError:
        return None
    if not content.strip():
        return None
    return {"path": str(path), "size": len(content), "sha256": hashlib.sha256(content).hexdigest()}


def artifact_matches(entry: dict[str, Any]) -> bool:
    artifact = entry.get("final_artifact")
    if not isinstance(artifact, dict) or not isinstance(artifact.get("path"), str):
        return False
    current = artifact_metadata(Path(artifact["path"]))
    return bool(
        current
        and current["size"] == artifact.get("size")
        and current["sha256"] == artifact.get("sha256")
    )


class RunLock:
    def __init__(self, path: Path):
        self.path = path
        self.handle: Any = None

    def __enter__(self) -> "RunLock":
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.handle = self.path.open("a+", encoding="utf-8")
        try:
            fcntl.flock(self.handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            self.handle.close()
            self.handle = None
            raise RuntimeError(f"run directory is already active: {self.path.parent}") from error
        self.handle.seek(0)
        self.handle.truncate()
        self.handle.write(f"pid={os.getpid()} started_at={utc_now()}\n")
        self.handle.flush()
        return self

    def __exit__(self, *_: Any) -> None:
        if self.handle:
            fcntl.flock(self.handle.fileno(), fcntl.LOCK_UN)
            self.handle.close()
            self.handle = None


class ProcessRegistry:
    def __init__(self):
        self.lock = threading.Lock()
        self.processes: set[subprocess.Popen[bytes]] = set()
        self.stopping = threading.Event()

    def spawn(self, command: list[str], **kwargs: Any) -> subprocess.Popen[bytes]:
        with self.lock:
            if self.stopping.is_set():
                raise RuntimeError("runner is stopping")
            process = subprocess.Popen(command, **kwargs)
            self.processes.add(process)
            return process

    def add(self, process: subprocess.Popen[bytes]) -> None:
        with self.lock:
            self.processes.add(process)

    def discard(self, process: subprocess.Popen[bytes]) -> None:
        with self.lock:
            self.processes.discard(process)

    def terminate_all(self) -> None:
        self.stopping.set()
        with self.lock:
            processes = list(self.processes)
        for process in processes:
            if process.poll() is not None:
                continue
            try:
                if os.name == "posix":
                    os.killpg(process.pid, signal.SIGTERM)
                else:
                    process.terminate()
            except ProcessLookupError:
                pass
        deadline = time.monotonic() + 2
        for process in processes:
            remaining = max(0, deadline - time.monotonic())
            try:
                process.wait(timeout=remaining)
            except subprocess.TimeoutExpired:
                try:
                    if os.name == "posix":
                        os.killpg(process.pid, signal.SIGKILL)
                    else:
                        process.kill()
                except ProcessLookupError:
                    pass
                try:
                    process.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    pass


class SignalCleanup:
    def __init__(self, registry: ProcessRegistry):
        self.registry = registry
        self.previous: dict[int, Any] = {}

    def __enter__(self) -> "SignalCleanup":
        if threading.current_thread() is threading.main_thread():
            for signum in (signal.SIGINT, signal.SIGTERM):
                self.previous[signum] = signal.getsignal(signum)
                signal.signal(signum, self.handle)
        return self

    def handle(self, signum: int, _frame: Any) -> None:
        self.registry.terminate_all()
        raise KeyboardInterrupt(f"received signal {signum}")

    def __exit__(self, *_: Any) -> None:
        for signum, handler in self.previous.items():
            signal.signal(signum, handler)


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
                "schema_version": 2, "created_at": utc_now(), "jobs_file": str(jobs_path.resolve()),
                "backend": "codex-exec", "codex": {"path": str(codex_path), "version": codex_version},
                "route_attestation": ROUTE_ATTESTATION, "jobs": {},
            }
        self.data["schema_version"] = 2
        self.data["updated_at"] = utc_now()
        atomic_json(self.path, self.data)

    def get(self, job_id: str) -> Optional[dict[str, Any]]:
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
    timeout: int, retries: int, backoff: float, registry: ProcessRegistry,
) -> bool:
    prior = manifest.get(job.id)
    attempts = list(prior.get("attempts", [])) if prior and prior.get("fingerprint") == job.fingerprint else []
    entry: dict[str, Any] = {
        "spec": job.public_spec(), "fingerprint": job.fingerprint, "status": "running",
        "route_attestation": ROUTE_ATTESTATION, "attempts": attempts,
    }
    retry_limit = retries if job.sandbox == "read-only" or job.safe_retry else 0
    entry["retry_policy"] = {
        "max_retries": retry_limit,
        "reason": "read-only" if job.sandbox == "read-only" else (
            "writer-explicitly-idempotent" if job.safe_retry else "writer-retries-disabled"
        ),
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

    for retry_index in range(retry_limit + 1):
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
                process = registry.spawn(
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
                finally:
                    registry.discard(process)
                returncode = process.returncode
        except OSError as error:
            returncode = 127
            stderr_path.write_text(str(error), encoding="utf-8")

        completed_turn = has_completed_turn(events_path)
        attempt.update({
            "finished_at": utc_now(), "returncode": returncode,
            "turn_completed": completed_turn,
            "thread_ids": extract_thread_ids(events_path),
        })
        artifact = None
        if temporary_output.exists():
            os.replace(temporary_output, final_output)
            artifact = artifact_metadata(final_output)
            attempt["final_output"] = str(final_output)
        if artifact:
            attempt["final_artifact"] = artifact
        succeeded = returncode == 0 and not timed_out and completed_turn and artifact is not None
        attempt["status"] = "succeeded" if succeeded else (
            "timed_out" if timed_out else (
                "missing_completion_event" if returncode == 0 and not completed_turn else (
                    "missing_final_output" if returncode == 0 and not artifact else "failed"
                )
            )
        )
        if succeeded:
            entry.update({
                "status": "succeeded", "finished_at": utc_now(),
                "final_output": str(final_output), "final_artifact": artifact,
                "thread_ids": attempt["thread_ids"],
            })
            manifest.set(job.id, entry)
            return True
        manifest.set(job.id, entry)
        if retry_index < retry_limit and not registry.stopping.is_set():
            time.sleep(backoff * (2 ** retry_index))
        elif registry.stopping.is_set():
            break

    entry.update({"status": "failed", "finished_at": utc_now(), "terminal_error": attempts[-1]["status"]})
    manifest.set(job.id, entry)
    return False


def run_pool(
    jobs: Iterable[Job], binary: Path, run_dir: Path, manifest: Manifest,
    concurrency: int, write_concurrency: int, timeout: int, retries: int, backoff: float,
    registry: ProcessRegistry,
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
                future = executor.submit(
                    run_job, job, binary, run_dir, manifest, timeout, retries, backoff, registry
                )
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


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("jobs", type=Path, help="JSONL job file")
    parser.add_argument("--config", type=Path, help="highest-precedence TOML configuration")
    parser.add_argument("--run-dir", type=Path)
    parser.add_argument("--concurrency", type=int)
    parser.add_argument("--write-concurrency", type=int)
    parser.add_argument(
        "--allow-disjoint-parallel-writers", action="store_true",
        help="explicitly opt into parallel writers with unique ownership_scope values",
    )
    parser.add_argument("--timeout", type=int)
    parser.add_argument("--retries", type=int)
    parser.add_argument("--backoff", type=float)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    try:
        config = load_config(args.config)
        jobs = load_jobs(args.jobs)
        binary, version = select_codex_binary()
    except (OSError, ValueError, RuntimeError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 2
    pool_config = config["pool"]
    concurrency = args.concurrency if args.concurrency is not None else pool_config["concurrency"]
    write_concurrency = (
        args.write_concurrency if args.write_concurrency is not None
        else config["writes"]["max_write_concurrency"]
    )
    timeout = args.timeout if args.timeout is not None else pool_config["job_timeout_seconds"]
    retries = args.retries if args.retries is not None else pool_config["max_retries"]
    backoff = args.backoff if args.backoff is not None else pool_config["retry_backoff_seconds"]
    if concurrency < 1 or write_concurrency < 1 or write_concurrency > concurrency:
        parser.error("concurrency values must be positive and write concurrency cannot exceed total")
    if timeout < 1 or retries < 0 or backoff < 0:
        parser.error("timeout must be positive; retries and backoff must be non-negative")
    config_allows_parallel_writers = config["writes"]["allow_disjoint_parallel_writers"]
    if (
        args.write_concurrency is not None and args.write_concurrency > 1
        and not args.allow_disjoint_parallel_writers
    ):
        print(
            "ERROR: --write-concurrency above 1 requires --allow-disjoint-parallel-writers",
            file=sys.stderr,
        )
        return 2
    parallel_writers_allowed = (
        config_allows_parallel_writers or args.allow_disjoint_parallel_writers
    )
    if write_concurrency > 1 and not parallel_writers_allowed:
        print(
            "ERROR: parallel writers require writes.allow_disjoint_parallel_writers=true "
            "or --allow-disjoint-parallel-writers",
            file=sys.stderr,
        )
        return 2
    try:
        validate_parallel_write_scopes(jobs, write_concurrency)
    except ValueError as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 2
    run_root = Path(config["runner"]["run_root"]).expanduser()
    if not run_root.is_absolute():
        run_root = Path.cwd() / run_root
    run_dir = (args.run_dir or run_root / args.jobs.stem).resolve()
    if args.dry_run:
        result = {
            "backend": "codex-exec", "codex": {"path": str(binary), "version": version},
            "route_attestation": ROUTE_ATTESTATION, "run_dir": str(run_dir),
            "config_sources": config["_sources"],
            "concurrency": concurrency, "write_concurrency": write_concurrency,
            "allow_disjoint_parallel_writers": parallel_writers_allowed,
            "timeout": timeout, "retries": retries, "backoff": backoff,
            "jobs": [job.public_spec() for job in jobs],
        }
        print(json.dumps(result, indent=2, sort_keys=True))
        return 0

    run_dir.mkdir(parents=True, exist_ok=True)
    try:
        run_lock = RunLock(run_dir / "run.lock")
        run_lock.__enter__()
    except (OSError, RuntimeError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 2
    try:
        try:
            manifest = Manifest(run_dir / "manifest.json", args.jobs, binary, version)
        except (OSError, ValueError, json.JSONDecodeError) as error:
            print(f"ERROR: could not open run manifest: {error}", file=sys.stderr)
            return 2
        selected: list[Job] = []
        for job in jobs:
            prior = manifest.get(job.id)
            if (
                prior and prior.get("status") == "succeeded"
                and prior.get("fingerprint") == job.fingerprint and artifact_matches(prior)
            ):
                print(f"SKIP {job.id}: verified successful artifact")
            else:
                if prior and prior.get("status") == "succeeded":
                    print(f"RERUN {job.id}: successful artifact missing, changed, or job changed")
                selected.append(job)
        registry = ProcessRegistry()
        try:
            with SignalCleanup(registry):
                ok = run_pool(
                    selected, binary, run_dir, manifest, concurrency, write_concurrency,
                    timeout, retries, backoff, registry,
                ) if selected else True
        except KeyboardInterrupt:
            registry.terminate_all()
            print("INTERRUPTED: active Codex process groups terminated", file=sys.stderr)
            return 130
        succeeded = sum(
            1 for job in jobs
            if (entry := manifest.get(job.id))
            and entry.get("status") == "succeeded" and artifact_matches(entry)
        )
        print(f"{succeeded}/{len(jobs)} jobs succeeded; manifest: {manifest.path}")
        return 0 if ok and succeeded == len(jobs) else 1
    finally:
        run_lock.__exit__(None, None, None)


if __name__ == "__main__":
    raise SystemExit(main())
