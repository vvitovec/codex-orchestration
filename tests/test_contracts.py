import importlib.util
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("validator", ROOT / "scripts/validate.py")
validator = importlib.util.module_from_spec(spec)
assert spec.loader
spec.loader.exec_module(validator)


class PackageTests(unittest.TestCase):
    def test_package_validates(self):
        validator.validate()

    def test_root_never_implements(self):
        text = (ROOT / "skills/orchestrate-work/SKILL.md").read_text().lower()
        self.assertIn("must delegate all edits", text)
        self.assertIn("do not author implementation changes", text)

    def test_delegation_contract_is_complete(self):
        text = (ROOT / "skills/compose-delegation/SKILL.md").read_text()
        for field in ("Outcome:", "Scope:", "Context:", "Constraints:", "Verify:", "Return:"):
            self.assertIn(field, text)

    def test_one_writer_default(self):
        text = (ROOT / "skills/orchestrate-work/SKILL.md").read_text().lower()
        self.assertIn("one active writer", text)
        self.assertIn("provably disjoint", text)

    def test_escalation_chain(self):
        text = (ROOT / "skills/route-subagents/SKILL.md").read_text()
        self.assertIn("Luna to Terra", text)
        self.assertIn("Terra to Sol", text)

    def test_agent_write_boundaries(self):
        explorer = validator.toml_loads((ROOT / "agents/terra-explorer.toml").read_text())
        reviewer = validator.toml_loads((ROOT / "agents/independent-reviewer.toml").read_text())
        writer = validator.toml_loads((ROOT / "agents/terra-worker.toml").read_text())
        self.assertEqual(explorer["sandbox_mode"], "read-only")
        self.assertEqual(reviewer["sandbox_mode"], "read-only")
        self.assertEqual(writer["sandbox_mode"], "workspace-write")

    def test_scaled_pool_is_programmatic_and_bounded(self):
        text = (ROOT / "skills/scale-agent-pool/SKILL.md").read_text()
        self.assertIn("scripts/orchestrate.py", text)
        self.assertIn("command/config accepted, not runtime-attested", text)
        self.assertIn("fixed concurrency defaults", text)
        self.assertIn("does not currently adapt live concurrency", text)

    def test_config_modes(self):
        resolver_spec = importlib.util.spec_from_file_location("resolver", ROOT / "scripts/resolve_config.py")
        resolver = importlib.util.module_from_spec(resolver_spec)
        assert resolver_spec.loader
        resolver_spec.loader.exec_module(resolver)
        default = resolver.resolve()
        self.assertEqual(default["mode"], "balanced")
        self.assertEqual(default["pool"]["concurrency"], 3)
        self.assertEqual(default["writes"]["max_write_concurrency"], 1)

    def test_config_precedence(self):
        resolver_spec = importlib.util.spec_from_file_location("resolver_precedence", ROOT / "scripts/resolve_config.py")
        resolver = importlib.util.module_from_spec(resolver_spec)
        assert resolver_spec.loader
        resolver_spec.loader.exec_module(resolver)
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            home = root / "home"
            project = root / "project"
            (project / ".codex").mkdir(parents=True)
            home.mkdir()
            (home / "orchestration.toml").write_text("[pool]\nconcurrency = 4\n")
            (project / ".codex/orchestration.toml").write_text("[pool]\nconcurrency = 5\n")
            explicit = root / "explicit.toml"
            explicit.write_text("[pool]\nconcurrency = 6\n")
            config = resolver.resolve(explicit, cwd=project, codex_home=home)
            self.assertEqual(config["pool"]["concurrency"], 6)
            self.assertEqual(len(config["_sources"]), 4)

    def test_parallel_writer_config_requires_boolean_opt_in(self):
        resolver_spec = importlib.util.spec_from_file_location("resolver_writers", ROOT / "scripts/resolve_config.py")
        resolver = importlib.util.module_from_spec(resolver_spec)
        assert resolver_spec.loader
        resolver_spec.loader.exec_module(resolver)
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            config = root / "parallel.toml"
            config.write_text(
                "[pool]\nconcurrency = 2\n"
                "[writes]\nmax_write_concurrency = 2\n"
                "allow_disjoint_parallel_writers = false\n"
            )
            with self.assertRaisesRegex(ValueError, "must be true"):
                resolver.resolve(config, cwd=root, codex_home=root / "home")
            config.write_text(config.read_text().replace("false", "true"))
            self.assertEqual(
                resolver.resolve(config, cwd=root, codex_home=root / "home")["writes"]["max_write_concurrency"],
                2,
            )
            config.write_text(config.read_text().replace("true", '"yes"'))
            with self.assertRaisesRegex(ValueError, "must be a boolean"):
                resolver.resolve(config, cwd=root, codex_home=root / "home")


if __name__ == "__main__":
    unittest.main()
