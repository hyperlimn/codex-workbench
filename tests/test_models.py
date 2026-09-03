import json
import tempfile
import unittest
from pathlib import Path

from codex_workbench.models import (
    GitHubIdentity,
    GitIdentity,
    Project,
    RepositorySettings,
    TerminalPreferences,
)
from codex_workbench.store import load_projects, save_projects


class ModelMigrationTests(unittest.TestCase):
    def test_v1_project_is_migrated_in_memory(self):
        project = Project.from_dict(
            {
                "name": "demo",
                "directory": "/tmp/demo",
                "codex_account": "account-a",
                "git": {
                    "name": "Developer",
                    "email": "dev@example.com",
                    "github_account": "octocat",
                },
                "remote": "upstream",
                "terminal": "tilix",
            }
        )

        self.assertEqual(project.github.account, "octocat")
        self.assertEqual(project.git.github_account, "octocat")
        self.assertEqual(project.repository.remote, "upstream")
        self.assertEqual(project.terminal.adapter, "tilix")

    def test_v4_round_trip_keeps_workspace_metadata(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "projects.json"
            project = Project(
                "demo",
                tmp,
                "account-a",
                GitIdentity("Developer", "dev@example.com"),
                GitHubIdentity("octocat", "github.com", "acme"),
                RepositorySettings(
                    "origin",
                    "git@github.com:acme/demo.git",
                    {"origin": "git@github.com:acme/demo.git"},
                ),
                ["https://chatgpt.com/c/thread"],
                ["Keep changes modular."],
                "Finish session support",
                TerminalPreferences("tilix", "workbench-layout"),
            )
            save_projects({"demo": project}, path)
            loaded = load_projects(path)["demo"]
            document = json.loads(path.read_text(encoding="utf-8"))

        self.assertEqual(document["version"], 4)
        self.assertEqual(loaded.objective, "Finish session support")
        self.assertEqual(loaded.github.owner, "acme")
        self.assertEqual(loaded.repository.expected_url, "git@github.com:acme/demo.git")
        self.assertEqual(loaded.terminal.layout, "workbench-layout")


if __name__ == "__main__":
    unittest.main()
