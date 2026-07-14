import importlib.util
import json
import os
import stat
import sys
import tempfile
import time
import unittest
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

    def tearDown(self):
        self.temp.cleanup()

    def make_codex(self, version="0.144.2", exit_code=0, sleep=0.0):
        path = self.root / f"codex-{version}-{exit_code}-{sleep}"
        path.write_text(
            "#!/bin/sh\n"
            f"if [ \"$1\" = --version ]; then echo 'codex-cli {version}'; exit 0; fi\n"
            "out=''\n"
            "previous=''\n"
            "for argument in \"$@\"; do\n"
            "  if [ \"$previous\" = --output-last-message ]; then out=$argument; fi\n"
            "  previous=$argument\n"
            "done\n"
            "prompt=$(cat)\n"
            f"sleep {sleep}\n"
            "printf '%s\\n' '{\"type\":\"thread.started\",\"thread_id\":\"019f629c-0dd6-7e20-8013-67f8c5334820\"}'\n"
            "printf '%s' \"$prompt\" > \"$out\"\n"
            f"exit {exit_code}\n",
            encoding="utf-8",
        )
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
        self.assertEqual(manifest["route_attestation"], orchestrate.ROUTE_ATTESTATION)
        with mock.patch.dict(os.environ, {"CODEX_BIN": str(binary)}), mock.patch.object(
            orchestrate, "run_job", side_effect=AssertionError("must skip")
        ):
            self.assertEqual(orchestrate.main([str(jobs_path), "--run-dir", str(run_dir)]), 0)

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
                jobs, Path("codex"), self.root, DummyManifest(), 2, 1, 1, 0, 0
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


if __name__ == "__main__":
    unittest.main()
