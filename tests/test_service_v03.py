import subprocess
import tempfile
import unittest
from pathlib import Path

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


class V03ServiceSafetyTests(unittest.TestCase):
    def test_commit_identity_mismatch_blocks_before_staging_then_allows_override(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            repo = base / "repo"
            repo.mkdir()
            run_git(repo, "init", "-q")
            run_git(repo, "config", "user.name", "Actual")
            run_git(repo, "config", "user.email", "actual@example.com")
            (repo / "file.txt").write_text("one\n", encoding="utf-8")
            run_git(repo, "add", "file.txt")
            run_git(repo, "commit", "-q", "-m", "initial")
            (repo / "file.txt").write_text("two\n", encoding="utf-8")
            app = Workbench(
                projects=ProjectRegistry(base / "config" / "projects.json"),
                sessions=SessionStore(base / "data" / "sessions"),
            )
            app.projects.save(
                Project(
                    "demo",
                    str(repo),
                    git=GitIdentity("Expected", "expected@example.com"),
                )
            )
            plan = app.plan_commit("demo")
            blocked = app.commit(
                "demo",
                message="Blocked",
                files=("file.txt",),
                plan=plan,
            )
            staged_after_block = run_git(
                repo, "diff", "--cached", "--name-only"
            ).stdout
            committed = app.commit(
                "demo",
                message="Explicit override",
                files=("file.txt",),
                allow_identity_mismatch=True,
                plan=plan,
            )

        self.assertFalse(blocked.committed)
        self.assertIn("differs from expected", blocked.reason)
        self.assertEqual(staged_after_block, "")
        self.assertTrue(committed.committed)

    def test_account_selection_updates_active_session_not_project_default(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            repo = base / "repo"
            repo.mkdir()
            run_git(repo, "init", "-q")
            app = Workbench(
                projects=ProjectRegistry(base / "config" / "projects.json"),
                sessions=SessionStore(base / "data" / "sessions"),
            )
            app.projects.save(Project("demo", str(repo), "project-default"))
            session = app.start_session("demo", "task")
            app.set_codex_account("demo", "session-account")

            loaded_project = app.project("demo")
            loaded_session = app.sessions.load("demo", session.id)

        self.assertEqual(loaded_project.codex_account, "project-default")
        self.assertEqual(loaded_session.codex_account, "session-account")


    def test_commit_rejects_repository_changes_after_preview(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            repo = base / "repo"
            repo.mkdir()
            run_git(repo, "init", "-q")
            run_git(repo, "config", "user.name", "Developer")
            run_git(repo, "config", "user.email", "dev@example.com")
            file = repo / "file.txt"
            file.write_text("one\n", encoding="utf-8")
            run_git(repo, "add", "file.txt")
            run_git(repo, "commit", "-q", "-m", "initial")
            file.write_text("two\n", encoding="utf-8")
            app = Workbench(
                projects=ProjectRegistry(base / "config" / "projects.json"),
                sessions=SessionStore(base / "data" / "sessions"),
            )
            app.projects.save(Project("demo", str(repo)))
            plan = app.plan_commit("demo")
            file.write_text("three\n", encoding="utf-8")

            result = app.commit(
                "demo",
                message="Stale preview",
                files=("file.txt",),
                plan=plan,
            )
            staged = run_git(repo, "diff", "--cached", "--name-only").stdout

        self.assertFalse(result.committed)
        self.assertIn("changed since preview", result.reason)
        self.assertEqual(staged, "")

    def test_push_rejects_destination_change_after_preview_without_network(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            repo = base / "repo"
            repo.mkdir()
            run_git(repo, "init", "-q")
            run_git(repo, "config", "user.name", "Developer")
            run_git(repo, "config", "user.email", "dev@example.com")
            run_git(repo, "commit", "--allow-empty", "-q", "-m", "initial")
            run_git(
                repo,
                "remote",
                "add",
                "origin",
                "https://github.com/example/one.git",
            )
            app = Workbench(
                projects=ProjectRegistry(base / "config" / "projects.json"),
                sessions=SessionStore(base / "data" / "sessions"),
            )
            app.projects.save(Project("demo", str(repo)))
            plan = app.plan_push("demo")
            run_git(
                repo,
                "remote",
                "set-url",
                "origin",
                "https://github.com/example/two.git",
            )

            result = app.push("demo", confirmed=True, plan=plan)

        self.assertFalse(result.pushed)
        self.assertIn("changed since preview", result.reason)

if __name__ == "__main__":
    unittest.main()
