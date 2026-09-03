import os
import subprocess
import tempfile
import unittest
from pathlib import Path

from codex_workbench.projects import ProjectRegistry


ROOT = Path(__file__).resolve().parents[1]
LAUNCHER = ROOT / "integrations" / "codex-launcher" / "codex-start"


class PublicFirstRunTests(unittest.TestCase):
    def test_missing_project_registry_has_no_built_in_projects(self):
        with tempfile.TemporaryDirectory() as tmp:
            registry = ProjectRegistry(Path(tmp) / "projects.json")

            self.assertEqual(registry.all(), {})
            self.assertFalse(registry.path.exists())


class BundledLauncherTests(unittest.TestCase):
    def run_launcher(self, root: Path, *arguments: str) -> subprocess.CompletedProcess[str]:
        environment = os.environ.copy()
        environment.update(
            {
                "HOME": str(root / "home"),
                "XDG_CONFIG_HOME": str(root / "config"),
                "XDG_CACHE_HOME": str(root / "cache"),
                "TERM": "dumb",
            }
        )
        environment.pop("CODEX_HOME", None)
        return subprocess.run(
            [str(LAUNCHER), *arguments],
            cwd=LAUNCHER.parent,
            env=environment,
            text=True,
            capture_output=True,
            timeout=10,
            check=False,
        )

    def test_launcher_imports_with_required_bundled_module(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = self.run_launcher(Path(tmp), "--version")

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), "codex-start 2.2.5")

    def test_empty_account_listing_does_not_create_or_invent_accounts(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            result = self.run_launcher(root, "accounts")
            accounts_file = root / "config" / "codex-start" / "accounts.json"

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout, f"accounts: {accounts_file}\n")
            self.assertFalse(accounts_file.exists())


if __name__ == "__main__":
    unittest.main()
