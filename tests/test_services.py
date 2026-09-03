import subprocess
import tempfile
import unittest
from pathlib import Path

from codex_workbench.codex import CodexStatus
from codex_workbench.github import GitHubAuthState
from codex_workbench.models import GitHubIdentity, Project, RepositorySettings
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

    def read_status(self, account):
        return CodexStatus(
            account,
            five_hour_remaining=72,
            weekly_remaining=81,
        )

    def launch(self, _account, _cwd, *, initial_prompt=""):
        return 0


class FakeGitHub:
    def __init__(self, account="", authenticated=None):
        self.account = account
        self.authenticated = (
            bool(account) if authenticated is None else authenticated
        )

    def detect_account(self, host="github.com"):
        return GitHubAuthState(
            host,
            account=self.account,
            authenticated=self.authenticated,
            source="test",
        )


class ServiceSafetyTests(unittest.TestCase):
    def make_repo(self, base):
        repo = base / "repo"
        repo.mkdir()
        run_git(repo, "init", "-q")
        run_git(repo, "config", "user.name", "Developer")
        run_git(repo, "config", "user.email", "dev@example.com")
        (repo / "tracked.txt").write_text("one\n", encoding="utf-8")
        run_git(repo, "add", "tracked.txt")
        run_git(repo, "commit", "-q", "-m", "initial")
        return repo

    def make_workbench(self, base, github_account=""):
        return Workbench(
            projects=ProjectRegistry(base / "config" / "projects.json"),
            sessions=SessionStore(base / "sessions"),
            codex=FakeCodex(),
            github=FakeGitHub(github_account),
        )

    def test_commit_message_alone_never_stages_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            repo = self.make_repo(base)
            app = self.make_workbench(base)
            app.projects.save(Project("demo", str(repo), "account-a"))
            (repo / "tracked.txt").write_text("two\n", encoding="utf-8")

            result = app.commit("demo", message="unsafe implicit staging")

            self.assertFalse(result.committed)
            self.assertEqual(result.returncode, 2)
            self.assertIn("--all", result.reason)
            self.assertEqual(
                run_git(repo, "diff", "--cached", "--name-only").stdout,
                "",
            )

    def test_selected_commit_leaves_unselected_file_untracked(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            repo = self.make_repo(base)
            app = self.make_workbench(base)
            app.projects.save(Project("demo", str(repo), "account-a"))
            (repo / "tracked.txt").write_text("two\n", encoding="utf-8")
            (repo / "extra.txt").write_text("extra\n", encoding="utf-8")

            result = app.commit(
                "demo",
                message="Update tracked file",
                files=("tracked.txt",),
            )

            self.assertTrue(result.committed)
            status = run_git(repo, "status", "--short").stdout
            self.assertIn("?? extra.txt", status)
            self.assertNotIn("tracked.txt", status)

    def test_push_blocks_detected_github_account_mismatch(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            repo = self.make_repo(base)
            url = "https://github.com/acme/demo.git"
            run_git(repo, "remote", "add", "origin", url)
            app = self.make_workbench(base, github_account="wrong-account")
            project = Project(
                "demo",
                str(repo),
                "account-a",
                github=GitHubIdentity("expected-account", "github.com", "acme"),
                repository=RepositorySettings("origin", url, {"origin": url}),
            )
            app.projects.save(project)

            result = app.push("demo", confirmed=True)

            self.assertFalse(result.pushed)
            self.assertTrue(
                any(
                    "wrong-account" in problem
                    for problem in result.plan.blocking
                )
            )

    def test_push_blocks_expired_github_authentication(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            repo = self.make_repo(base)
            url = "https://github.com/acme/demo.git"
            run_git(repo, "remote", "add", "origin", url)
            app = self.make_workbench(base)
            app.github = FakeGitHub("expected-account", authenticated=False)
            app.projects.save(
                Project(
                    "demo",
                    str(repo),
                    "account-a",
                    github=GitHubIdentity(
                        "expected-account", "github.com", "acme"
                    ),
                    repository=RepositorySettings("origin", url),
                )
            )

            plan = app.plan_push("demo")
            result = app.push("demo", confirmed=True, plan=plan)

            self.assertFalse(result.pushed)
            self.assertTrue(
                any("not authenticated" in problem for problem in plan.blocking)
            )

    def test_status_includes_session_objective_and_usage(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            repo = self.make_repo(base)
            app = self.make_workbench(base)
            project = Project(
                "demo",
                str(repo),
                "account-a",
                objective="Project objective",
            )
            app.projects.save(project)
            session = app.start_session(
                "demo",
                "current-task",
                objective="Session objective",
                next_action="Run integration tests",
            )

            status = app.status("demo")

            self.assertIn("CURRENT OBJECTIVE: Session objective", status.text)
            self.assertIn(f"CURRENT SESSION: current-task ({session.id})", status.text)
            self.assertIn("5h 72% left", status.text)
            self.assertIn("NEXT ACTION: Run integration tests", status.text)

            app.update_session(
                "demo", session.id, codex_account="account-b"
            )
            report = app.preflight("demo")
            account_check = next(
                check
                for check in report.checks
                if check.label == "codex account"
            )
            self.assertEqual(account_check.value, "account-b")
            self.assertIn("active work session", account_check.detail)


if __name__ == "__main__":
    unittest.main()
