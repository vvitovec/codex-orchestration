import importlib.util
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
        import tomllib

        explorer = tomllib.loads((ROOT / "agents/terra-explorer.toml").read_text())
        reviewer = tomllib.loads((ROOT / "agents/independent-reviewer.toml").read_text())
        writer = tomllib.loads((ROOT / "agents/terra-worker.toml").read_text())
        self.assertEqual(explorer["sandbox_mode"], "read-only")
        self.assertEqual(reviewer["sandbox_mode"], "read-only")
        self.assertEqual(writer["sandbox_mode"], "workspace-write")

    def test_scaled_pool_is_programmatic_and_bounded(self):
        text = (ROOT / "skills/scale-agent-pool/SKILL.md").read_text()
        self.assertIn("spawn_agents_on_csv", text)
        self.assertIn("adaptive-unrestricted", text)
        self.assertIn("Never interpret unrestricted as launching all pending jobs simultaneously", text)

    def test_config_modes(self):
        resolver_spec = importlib.util.spec_from_file_location("resolver", ROOT / "scripts/resolve_config.py")
        resolver = importlib.util.module_from_spec(resolver_spec)
        assert resolver_spec.loader
        resolver_spec.loader.exec_module(resolver)
        default = resolver.resolve()
        self.assertEqual(default["mode"], "balanced")
        self.assertEqual(default["writes"]["max_write_concurrency"], 1)


if __name__ == "__main__":
    unittest.main()
