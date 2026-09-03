import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from codex_workbench.context import render
from codex_workbench.git import inspect
from codex_workbench.models import GitIdentity, Project
from codex_workbench.preflight import build_preflight


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


class GitInspectionTests(unittest.TestCase):
    def test_detects_repository_local_identity_and_nested_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "repo"
            root.mkdir()
            run_git(root, "init", "-q")
            run_git(root, "config", "--local", "user.name", "Local User")
            run_git(root, "config", "--local", "user.email", "local@example.com")
            (root / "file.txt").write_text("one\n", encoding="utf-8")
            run_git(root, "add", "file.txt")
            run_git(root, "commit", "-q", "-m", "initial")
            run_git(
                root,
                "remote",
                "add",
                "origin",
                "https://github.com/example/repo.git",
            )
            nested = root / "src"
            nested.mkdir()

            state = inspect(nested)

        self.assertTrue(state.is_repository)
        self.assertEqual(state.user_name, "Local User")
        self.assertEqual(state.identity_source, "repository-local")
        self.assertTrue(state.branch)
        self.assertEqual(state.remote_url, "https://github.com/example/repo.git")

    def test_context_uses_detected_identity_without_empty_expected_line(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            run_git(root, "init", "-q")
            run_git(root, "config", "--local", "user.name", "Detected User")
            run_git(root, "config", "--local", "user.email", "detected@example.com")
            state = inspect(root)
            text = render(Project("demo", tmp), state)

        self.assertIn(
            "GIT IDENTITY (DETECTED): Detected User <detected@example.com>",
            text,
        )
        self.assertNotIn("GIT IDENTITY (EXPECTED)", text)
        self.assertNotIn("- <->", text)

    def test_preflight_accepts_detected_identity_without_duplication(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            run_git(root, "init", "-q")
            run_git(root, "config", "--local", "user.name", "Detected User")
            run_git(root, "config", "--local", "user.email", "detected@example.com")
            (root / "file.txt").write_text("one\n", encoding="utf-8")
            run_git(root, "add", "file.txt")
            run_git(root, "commit", "-q", "-m", "initial")
            run_git(
                root,
                "remote",
                "add",
                "origin",
                "https://github.com/example/repo.git",
            )
            project = Project("demo", tmp, "account-a")
            report = build_preflight(
                project,
                codex=FakeCodex(),
            )

        self.assertTrue(report.ok)
        user_check = next(
            check for check in report.checks if check.label == "git user"
        )
        self.assertEqual(user_check.value, "Detected User")
        self.assertIn("repository-local", user_check.detail)

    def test_expected_identity_mismatch_is_explicit(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            run_git(root, "init", "-q")
            run_git(root, "config", "--local", "user.name", "Actual")
            run_git(root, "config", "--local", "user.email", "actual@example.com")
            project = Project(
                "demo",
                tmp,
                "account-a",
                GitIdentity("Expected", "expected@example.com"),
            )
            report = build_preflight(
                project,
                codex=FakeCodex(),
            )

        self.assertFalse(report.ok)
        failures = [check.detail for check in report.checks if check.failed]
        self.assertTrue(any("expected Expected" in detail for detail in failures))


if __name__ == "__main__":
    unittest.main()
