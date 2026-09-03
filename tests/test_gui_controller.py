import subprocess
import tempfile
import unittest
from pathlib import Path

from codex_workbench.codex import CodexStatus
from codex_workbench.github import GitHubAuthState
from codex_workbench.gui.controller import SwitchboardController
from codex_workbench.models import GitIdentity, Project
from codex_workbench.projects import ProjectRegistry
from codex_workbench.services import Workbench
from codex_workbench.sessions import SessionStore


def run_git(path, *args):
    return subprocess.run(
        ["git", "-C", str(path), *args],
        text=True,
        capture_output=True,
        check=True,
    )


class FakeCodex:
    available = True
    launcher = "/fake/codex-start"

    def __init__(self):
        self.launches = []

    def list_accounts(self):
        return ["low-account", "healthy-account"]

    def read_status(self, account):
        remaining = 4 if account == "low-account" else 76
        return CodexStatus(
            account,
            five_hour_remaining=remaining,
            weekly_remaining=82,
            reset="Tomorrow 14:00",
        )

    def command(self, account, *, initial_prompt=""):
        command = [self.launcher, account]
        if initial_prompt:
            command.extend(("--", initial_prompt))
        return command

    def launch(self, account, cwd, *, initial_prompt=""):
        self.launches.append((account, Path(cwd), initial_prompt))
        return 0


class FakeGitHub:
    def detect_account(self, host="github.com"):
        return GitHubAuthState(
            host,
            account="octocat",
            authenticated=True,
            source="test",
        )


class ControllerTests(unittest.TestCase):
    def make_controller(self, base):
        repo = base / "repo"
        repo.mkdir()
        run_git(repo, "init", "-q")
        run_git(repo, "config", "user.name", "Detected User")
        run_git(repo, "config", "user.email", "detected@example.com")
        (repo / "tracked.txt").write_text("one\n", encoding="utf-8")
        run_git(repo, "add", "tracked.txt")
        run_git(repo, "commit", "-q", "-m", "initial")
        run_git(
            repo,
            "remote",
            "add",
            "origin",
            "https://github.com/octocat/demo.git",
        )
        codex = FakeCodex()
        workbench = Workbench(
            projects=ProjectRegistry(base / "config" / "projects.json"),
            sessions=SessionStore(base / "data" / "sessions"),
            codex=codex,
            github=FakeGitHub(),
        )
        workbench.projects.save(
            Project(
                "demo",
                str(repo),
                "low-account",
                git=GitIdentity("Expected User", "expected@example.com"),
            )
        )
        return SwitchboardController(workbench), codex, repo

    def test_workspace_maps_mismatch_and_account_usage_without_gtk(self):
        with tempfile.TemporaryDirectory() as tmp:
            controller, _codex, _repo = self.make_controller(Path(tmp))

            projects = controller.project_items()
            workspace = controller.workspace("demo")
            accounts = controller.account_items(workspace.account)
            commands = controller.palette("switch")

        self.assertEqual(projects[0].name, "demo")
        confidence = {item.key: item for item in workspace.confidence}
        self.assertEqual(confidence["identity"].tone, "error")
        self.assertIn("does not match", confidence["identity"].detail)
        levels = {item.name: item.level for item in accounts}
        self.assertEqual(levels["low-account"], "critical")
        self.assertEqual(levels["healthy-account"], "normal")
        self.assertEqual(commands[0].project, "demo")

    def test_account_selection_sets_intent_without_launching(self):
        with tempfile.TemporaryDirectory() as tmp:
            controller, codex, _repo = self.make_controller(Path(tmp))

            controller.select_account("demo", "healthy-account")
            stored = controller.workbench.project("demo")

        self.assertEqual(stored.codex_account, "healthy-account")
        self.assertEqual(codex.launches, [])

    def test_session_context_and_last_project_reconstruct(self):
        with tempfile.TemporaryDirectory() as tmp:
            controller, _codex, _repo = self.make_controller(Path(tmp))
            controller.workbench.remember_project("demo")

            session = controller.save_session_context(
                "demo",
                "",
                {
                    "name": "catalog-search",
                    "objective": "Implement catalog search",
                    "current_state": "Tests pass",
                    "current_problem": "",
                    "next_action": "Add ranking",
                    "completed": ["Added parser"],
                },
            )
            dashboard = controller.dashboard(query_codex=False)

        self.assertEqual(session.current_state, "Tests pass")
        self.assertEqual(session.completed, ["Added parser"])
        self.assertEqual(dashboard.selected_project, "demo")
        self.assertEqual(dashboard.workspace.session.name, "catalog-search")


if __name__ == "__main__":
    unittest.main()
