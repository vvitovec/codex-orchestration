import importlib.util
import json
import os
import signal
import stat
import subprocess
import sys
import tempfile
import time
import unittest
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("orchestrate", ROOT / "scripts/orchestrate.py")
orchestrate = importlib.util.module_from_spec(spec)
assert spec.loader
sys.modules[spec.name] = orchestrate
spec.loader.exec_module(orchestrate)


class RunnerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.environment = mock.patch.dict(
            os.environ, {"CODEX_HOME": str(self.root / "codex-home")}
        )
        self.environment.start()

    def tearDown(self):
        self.environment.stop()
        self.temp.cleanup()

    def make_codex(
        self, version="0.144.2", exit_code=0, sleep=0.0, output=True, completed=True,
        pid_file=None,
    ):
        path = self.root / f"codex-{version}-{exit_code}-{sleep}"
        lines = [
            "#!/bin/sh\n",
            f"if [ \"$1\" = --version ]; then echo 'codex-cli {version}'; exit 0; fi\n",
            "out=''\nprevious=''\n",
            "for argument in \"$@\"; do\n",
            "  if [ \"$previous\" = --output-last-message ]; then out=$argument; fi\n",
            "  previous=$argument\ndone\n",
            "prompt=$(cat)\n",
        ]
        if pid_file:
            lines.append(f"echo $$ > {pid_file}\n")
        lines.extend([
            f"sleep {sleep}\n",
            "printf '%s\\n' '{\"type\":\"thread.started\",\"thread_id\":\"019f629c-0dd6-7e20-8013-67f8c5334820\"}'\n",
        ])
        if completed:
            lines.append("printf '%s\\n' '{\"type\":\"turn.completed\"}'\n")
        if output:
            lines.append("printf '%s' \"$prompt\" > \"$out\"\n")
        lines.append(f"exit {exit_code}\n")
        path.write_text("".join(lines), encoding="utf-8")
        path.chmod(path.stat().st_mode | stat.S_IXUSR)
        return path

    def write_jobs(self, values):
        path = self.root / "jobs.jsonl"
        path.write_text("".join(json.dumps(value) + "\n" for value in values), encoding="utf-8")
        return path

    def job(self, job_id="a", sandbox="read-only"):
        return {
            "id": job_id, "prompt": f"prompt {job_id}", "model": "gpt-5.6-luna",
            "effort": "low", "sandbox": sandbox, "workdir": str(self.root),
        }

    def test_load_jobs_validates_allowlists_and_duplicates(self):
        path = self.write_jobs([self.job()])
        self.assertEqual(orchestrate.load_jobs(path)[0].model, "gpt-5.6-luna")
        invalid = self.job()
        invalid["model"] = "whatever"
        with self.assertRaisesRegex(ValueError, "unsupported model"):
            orchestrate.load_jobs(self.write_jobs([invalid]))
        with self.assertRaisesRegex(ValueError, "duplicate id"):
            orchestrate.load_jobs(self.write_jobs([self.job(), self.job()]))
        for field in ("model", "effort", "sandbox"):
            malformed = self.job()
            malformed[field] = ["not", "hashable"]
            with self.assertRaisesRegex(ValueError, "unsupported"):
                orchestrate.load_jobs(self.write_jobs([malformed]))

    def test_malformed_job_returns_clean_error(self):
        malformed = self.job()
        malformed["model"] = {"bad": "type"}
        errors = StringIO()
        with redirect_stderr(errors):
            result = orchestrate.main([str(self.write_jobs([malformed])), "--dry-run"])
        self.assertEqual(result, 2)
        self.assertIn("unsupported model", errors.getvalue())
        self.assertNotIn("Traceback", errors.getvalue())

    def test_command_routes_model_effort_sandbox_and_stdin(self):
        job = orchestrate.load_jobs(self.write_jobs([self.job()]))[0]
        command = orchestrate.build_command(Path("/codex"), job, Path("/out"))
        self.assertIn("gpt-5.6-luna", command)
        self.assertIn('model_reasoning_effort="low"', command)
        self.assertIn("read-only", command)
        self.assertEqual(command[-1], "-")
        self.assertNotIn(job.prompt, command)

    def test_binary_selection_prefers_newest_and_honors_override(self):
        old = self.make_codex("0.144.2")
        new = self.make_codex("0.200.0")
        with mock.patch.object(orchestrate, "APP_CODEX_PATHS", (old, new)), mock.patch(
            "shutil.which", return_value=None
        ), mock.patch.dict(os.environ, {}, clear=True):
            self.assertEqual(orchestrate.select_codex_binary()[0], new.resolve())
        with mock.patch.dict(os.environ, {"CODEX_BIN": str(old)}):
            self.assertEqual(orchestrate.select_codex_binary()[0], old.resolve())

    def test_old_override_is_rejected(self):
        old = self.make_codex("0.100.0")
        with mock.patch.dict(os.environ, {"CODEX_BIN": str(old)}):
            with self.assertRaisesRegex(RuntimeError, "older than"):
                orchestrate.select_codex_binary()

    def test_success_persists_output_events_thread_and_skips_rerun(self):
        binary = self.make_codex()
        jobs_path = self.write_jobs([self.job()])
        run_dir = self.root / "run"
        with mock.patch.dict(os.environ, {"CODEX_BIN": str(binary)}):
            self.assertEqual(orchestrate.main([str(jobs_path), "--run-dir", str(run_dir)]), 0)
        manifest = json.loads((run_dir / "manifest.json").read_text())
        entry = manifest["jobs"]["a"]
        self.assertEqual(entry["status"], "succeeded")
        self.assertEqual(entry["thread_ids"], ["019f629c-0dd6-7e20-8013-67f8c5334820"])
        self.assertEqual(Path(entry["final_output"]).read_text(), "prompt a")
        self.assertEqual(entry["final_artifact"]["size"], len("prompt a"))
        self.assertEqual(len(entry["final_artifact"]["sha256"]), 64)
        self.assertEqual(manifest["route_attestation"], orchestrate.ROUTE_ATTESTATION)
        with mock.patch.dict(os.environ, {"CODEX_BIN": str(binary)}), mock.patch.object(
            orchestrate, "run_job", side_effect=AssertionError("must skip")
        ):
            self.assertEqual(orchestrate.main([str(jobs_path), "--run-dir", str(run_dir)]), 0)

    def test_deleted_or_tampered_success_artifact_is_rerun(self):
        binary = self.make_codex()
        jobs_path = self.write_jobs([self.job()])
        run_dir = self.root / "run"
        argv = [str(jobs_path), "--run-dir", str(run_dir), "--retries", "0"]
        with mock.patch.dict(os.environ, {"CODEX_BIN": str(binary)}):
            self.assertEqual(orchestrate.main(argv), 0)
            manifest = json.loads((run_dir / "manifest.json").read_text())
            artifact = Path(manifest["jobs"]["a"]["final_output"])
            artifact.unlink()
            self.assertEqual(orchestrate.main(argv), 0)
            manifest = json.loads((run_dir / "manifest.json").read_text())
            artifact = Path(manifest["jobs"]["a"]["final_output"])
            artifact.write_text("tampered", encoding="utf-8")
            self.assertEqual(orchestrate.main(argv), 0)
        entry = json.loads((run_dir / "manifest.json").read_text())["jobs"]["a"]
        self.assertEqual(entry["attempts"][-1]["number"], 3)
        self.assertTrue(orchestrate.artifact_matches(entry))

    def test_exit_zero_requires_completion_event_and_nonempty_output(self):
        for completed, output, expected in (
            (False, True, "missing_completion_event"),
            (True, False, "missing_final_output"),
        ):
            with self.subTest(completed=completed, output=output):
                binary = self.make_codex(completed=completed, output=output)
                run_dir = self.root / f"run-{completed}-{output}"
                with mock.patch.dict(os.environ, {"CODEX_BIN": str(binary)}):
                    result = orchestrate.main([
                        str(self.write_jobs([self.job()])), "--run-dir", str(run_dir), "--retries", "0"
                    ])
                self.assertEqual(result, 1)
                entry = json.loads((run_dir / "manifest.json").read_text())["jobs"]["a"]
                self.assertEqual(entry["attempts"][-1]["status"], expected)

    def test_failure_retries_and_returns_nonzero(self):
        binary = self.make_codex(exit_code=3)
        jobs_path = self.write_jobs([self.job()])
        run_dir = self.root / "run"
        with mock.patch.dict(os.environ, {"CODEX_BIN": str(binary)}):
            result = orchestrate.main([
                str(jobs_path), "--run-dir", str(run_dir), "--retries", "1", "--backoff", "0",
            ])
        self.assertEqual(result, 1)
        entry = json.loads((run_dir / "manifest.json").read_text())["jobs"]["a"]
        self.assertEqual(entry["status"], "failed")
        self.assertEqual(len(entry["attempts"]), 2)

    def test_timeout_is_terminal_failure(self):
        binary = self.make_codex(sleep=2)
        jobs_path = self.write_jobs([self.job()])
        with mock.patch.dict(os.environ, {"CODEX_BIN": str(binary)}):
            result = orchestrate.main([
                str(jobs_path), "--run-dir", str(self.root / "run"), "--timeout", "1", "--retries", "0",
            ])
        self.assertEqual(result, 1)

    def test_writers_do_not_retry_without_explicit_idempotency(self):
        binary = self.make_codex(exit_code=3)
        writer = self.job(sandbox="workspace-write")
        jobs_path = self.write_jobs([writer])
        with mock.patch.dict(os.environ, {"CODEX_BIN": str(binary)}):
            self.assertEqual(orchestrate.main([
                str(jobs_path), "--run-dir", str(self.root / "run"),
                "--retries", "3", "--backoff", "0",
            ]), 1)
        entry = json.loads((self.root / "run/manifest.json").read_text())["jobs"]["a"]
        self.assertEqual(len(entry["attempts"]), 1)
        self.assertEqual(entry["retry_policy"]["reason"], "writer-retries-disabled")

    def test_timed_out_writer_is_not_retried_by_default(self):
        binary = self.make_codex(sleep=2)
        jobs_path = self.write_jobs([self.job(sandbox="workspace-write")])
        with mock.patch.dict(os.environ, {"CODEX_BIN": str(binary)}):
            self.assertEqual(orchestrate.main([
                str(jobs_path), "--run-dir", str(self.root / "run"),
                "--timeout", "1", "--retries", "3", "--backoff", "0",
            ]), 1)
        entry = json.loads((self.root / "run/manifest.json").read_text())["jobs"]["a"]
        self.assertEqual(len(entry["attempts"]), 1)
        self.assertEqual(entry["attempts"][0]["status"], "timed_out")

    def test_idempotent_writer_can_retry(self):
        binary = self.make_codex(exit_code=3)
        writer = self.job(sandbox="workspace-write")
        writer["safe_retry"] = True
        jobs_path = self.write_jobs([writer])
        with mock.patch.dict(os.environ, {"CODEX_BIN": str(binary)}):
            self.assertEqual(orchestrate.main([
                str(jobs_path), "--run-dir", str(self.root / "run"),
                "--retries", "1", "--backoff", "0",
            ]), 1)
        entry = json.loads((self.root / "run/manifest.json").read_text())["jobs"]["a"]
        self.assertEqual(len(entry["attempts"]), 2)

    def test_write_concurrency_defaults_to_one(self):
        jobs = orchestrate.load_jobs(self.write_jobs([
            self.job("one", "workspace-write"), self.job("two", "workspace-write")
        ]))
        active = 0
        maximum = 0

        def fake_run(*args):
            nonlocal active, maximum
            active += 1
            maximum = max(maximum, active)
            time.sleep(0.05)
            active -= 1
            return True

        class DummyManifest:
            def set(self, *args):
                pass

        with mock.patch.object(orchestrate, "run_job", side_effect=fake_run):
            self.assertTrue(orchestrate.run_pool(
                jobs, Path("codex"), self.root, DummyManifest(), 2, 1, 1, 0, 0,
                orchestrate.ProcessRegistry(),
            ))
        self.assertEqual(maximum, 1)

    def test_dry_run_does_not_create_run_directory(self):
        binary = self.make_codex()
        run_dir = self.root / "absent"
        with mock.patch.dict(os.environ, {"CODEX_BIN": str(binary)}):
            self.assertEqual(orchestrate.main([
                str(self.write_jobs([self.job()])), "--run-dir", str(run_dir), "--dry-run"
            ]), 0)
        self.assertFalse(run_dir.exists())

    def test_runner_config_and_cli_precedence(self):
        binary = self.make_codex()
        config = self.root / "explicit.toml"
        config.write_text(
            "[pool]\nconcurrency = 7\nmax_retries = 4\n"
            "[runner]\nrun_root = \"configured-runs\"\n",
            encoding="utf-8",
        )
        output = StringIO()
        with mock.patch.dict(os.environ, {"CODEX_BIN": str(binary)}), redirect_stdout(output):
            result = orchestrate.main([
                str(self.write_jobs([self.job()])), "--config", str(config),
                "--concurrency", "2", "--retries", "0", "--dry-run",
            ])
        plan = json.loads(output.getvalue())
        self.assertEqual(result, 0)
        self.assertEqual(plan["concurrency"], 2)
        self.assertEqual(plan["retries"], 0)
        self.assertIn("configured-runs", plan["run_dir"])

    def test_run_directory_lock_rejects_concurrent_owner(self):
        lock_path = self.root / "run/run.lock"
        with orchestrate.RunLock(lock_path):
            with self.assertRaisesRegex(RuntimeError, "already active"):
                with orchestrate.RunLock(lock_path):
                    pass

    def test_concurrent_runner_fails_cleanly(self):
        binary = self.make_codex(sleep=1)
        jobs_path = self.write_jobs([self.job()])
        run_dir = self.root / "concurrent-run"
        command = [
            sys.executable, str(ROOT / "scripts/orchestrate.py"), str(jobs_path),
            "--run-dir", str(run_dir), "--retries", "0",
        ]
        env = {**os.environ, "CODEX_BIN": str(binary)}
        first = subprocess.Popen(command, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        for _ in range(100):
            if (run_dir / "run.lock").exists():
                break
            time.sleep(0.01)
        second = subprocess.run(command, env=env, capture_output=True, text=True, timeout=5)
        first_stdout, first_stderr = first.communicate(timeout=5)
        self.assertEqual(first.returncode, 0, first_stderr or first_stdout)
        self.assertEqual(second.returncode, 2)
        self.assertIn("already active", second.stderr)

    def test_process_registry_terminates_process_group(self):
        process = subprocess.Popen(["sh", "-c", "sleep 30"], start_new_session=True)
        registry = orchestrate.ProcessRegistry()
        registry.add(process)
        registry.terminate_all()
        self.assertIsNotNone(process.poll())
        self.assertNotEqual(process.returncode, 0)

    def test_sigterm_cleans_up_active_codex_process(self):
        pid_file = self.root / "codex.pid"
        binary = self.make_codex(sleep=30, pid_file=pid_file)
        jobs_path = self.write_jobs([self.job()])
        process = subprocess.Popen(
            [
                sys.executable, str(ROOT / "scripts/orchestrate.py"), str(jobs_path),
                "--run-dir", str(self.root / "signal-run"), "--retries", "0",
            ],
            env={**os.environ, "CODEX_BIN": str(binary)},
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        )
        for _ in range(200):
            if pid_file.exists():
                break
            time.sleep(0.01)
        self.assertTrue(pid_file.exists())
        child_pid = int(pid_file.read_text())
        process.send_signal(signal.SIGTERM)
        stdout, stderr = process.communicate(timeout=10)
        self.assertEqual(process.returncode, 130, stderr or stdout)
        with self.assertRaises(ProcessLookupError):
            os.kill(child_pid, 0)


if __name__ == "__main__":
    unittest.main()
