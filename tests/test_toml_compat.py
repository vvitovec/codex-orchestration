import ast
import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))
import toml_compat

resolver_spec = importlib.util.spec_from_file_location("fallback_resolver", SCRIPTS / "resolve_config.py")
resolver = importlib.util.module_from_spec(resolver_spec)
assert resolver_spec.loader
resolver_spec.loader.exec_module(resolver)


class TomlCompatibilityTests(unittest.TestCase):
    def test_fallback_parses_supported_scalar_grammar(self):
        value = toml_compat.loads(
            "mode = \"balanced#literal\" # comment\n"
            "[pool]\nconcurrency = 3\nretry_backoff_seconds = 1.25\n"
            "enabled = true\nnegative = -2\n",
            force_fallback=True,
        )
        self.assertEqual(value["mode"], "balanced#literal")
        self.assertEqual(value["pool"]["concurrency"], 3)
        self.assertEqual(value["pool"]["retry_backoff_seconds"], 1.25)
        self.assertTrue(value["pool"]["enabled"])
        self.assertEqual(value["pool"]["negative"], -2)

    def test_fallback_resolves_defaults_user_project_and_explicit_precedence(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            home = root / "home"
            project = root / "project"
            home.mkdir()
            (project / ".codex").mkdir(parents=True)
            (home / "orchestration.toml").write_text("[pool]\nconcurrency = 4 # user\n")
            (project / ".codex/orchestration.toml").write_text("[pool]\nconcurrency = 5\n")
            explicit = root / "explicit.toml"
            explicit.write_text(
                "[pool]\nretry_backoff_seconds = 1.5\n"
                "[runner]\nrun_root = 'fallback-runs'\n"
            )

            def fallback_load(handle):
                return toml_compat.load(handle, force_fallback=True)

            with mock.patch.object(resolver, "toml_load", side_effect=fallback_load):
                config = resolver.resolve(explicit, cwd=project, codex_home=home)
            self.assertEqual(config["pool"]["concurrency"], 5)
            self.assertEqual(config["pool"]["retry_backoff_seconds"], 1.5)
            self.assertEqual(config["runner"]["run_root"], "fallback-runs")
            self.assertEqual(len(config["_sources"]), 4)

    def test_fallback_supports_packaged_agent_files(self):
        for path in (ROOT / "agents").glob("*.toml"):
            value = toml_compat.loads(path.read_text(), force_fallback=True)
            self.assertTrue(value["developer_instructions"])
            self.assertIsInstance(value["nickname_candidates"], list)

    def test_fallback_rejects_unsupported_or_malformed_syntax(self):
        cases = (
            "[pool.options]\nvalue = 1\n",
            "[pool]\nconcurrency = nope\n",
            "[pool]\nvalue = { inline = true }\n",
            "[pool]\nname = \"unterminated\n",
        )
        for text in cases:
            with self.subTest(text=text):
                with self.assertRaisesRegex(ValueError, "line"):
                    toml_compat.loads(text, force_fallback=True)

    def test_installed_scripts_parse_as_python_39(self):
        for name in ("orchestrate.py", "resolve_config.py", "toml_compat.py", "validate.py"):
            ast.parse((SCRIPTS / name).read_text(), filename=name, feature_version=(3, 9))


if __name__ == "__main__":
    unittest.main()
