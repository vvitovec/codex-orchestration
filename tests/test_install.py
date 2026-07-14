import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
INSTALLER = ROOT / "scripts/install-personal.sh"


class InstallerTests(unittest.TestCase):
    def test_install_and_upgrade_preserve_unrelated_files(self):
        with tempfile.TemporaryDirectory() as temporary:
            codex_home = Path(temporary) / ".codex"
            env = {**os.environ, "CODEX_HOME": str(codex_home)}
            subprocess.run([str(INSTALLER)], env=env, check=True, capture_output=True, text=True)
            resolver = codex_home / "orchestration/scripts/resolve_config.py"
            defaults = codex_home / "orchestration/config/defaults.toml"
            self.assertTrue(resolver.is_file())
            self.assertTrue(defaults.is_file())
            result = subprocess.run(
                ["python3", str(resolver)], env=env, check=True, capture_output=True, text=True
            )
            self.assertEqual(json.loads(result.stdout)["pool"]["concurrency"], 3)

            unrelated = codex_home / "skills/unrelated-skill/keep.txt"
            unrelated.parent.mkdir()
            unrelated.write_text("keep", encoding="utf-8")
            installed = codex_home / "skills/orchestrate-work/SKILL.md"
            installed.write_text(installed.read_text() + "\nLOCAL DAMAGE\n", encoding="utf-8")
            subprocess.run(
                [str(INSTALLER), "--upgrade"], env=env, check=True, capture_output=True, text=True
            )
            self.assertEqual(unrelated.read_text(), "keep")
            self.assertNotIn("LOCAL DAMAGE", installed.read_text())

    def test_upgrade_refuses_unowned_collision(self):
        with tempfile.TemporaryDirectory() as temporary:
            codex_home = Path(temporary) / ".codex"
            conflict = codex_home / "skills/orchestrate-work/SKILL.md"
            conflict.parent.mkdir(parents=True)
            conflict.write_text("---\nname: orchestrate-work\n---\nforeign\n", encoding="utf-8")
            result = subprocess.run(
                [str(INSTALLER), "--upgrade"],
                env={**os.environ, "CODEX_HOME": str(codex_home)},
                capture_output=True, text=True,
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("unverified destination", result.stderr)
            self.assertIn("foreign", conflict.read_text())


if __name__ == "__main__":
    unittest.main()
