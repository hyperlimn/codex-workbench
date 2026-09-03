import tempfile
import unittest
from pathlib import Path

from codex_workbench.models import GitIdentity, Project
from codex_workbench.store import load_projects, save_projects


class StoreTests(unittest.TestCase):
    def test_round_trip(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "projects.json"
            project = Project(
                "demo",
                tmp,
                "account-one",
                GitIdentity(
                    "Example Developer",
                    "developer@example.invalid",
                    "example-org",
                ),
            )
            save_projects({"demo": project}, path)
            loaded = load_projects(path)["demo"]
            self.assertEqual(loaded.codex_account, "account-one")
            self.assertEqual(loaded.git.github_account, "example-org")


if __name__ == "__main__":
    unittest.main()
