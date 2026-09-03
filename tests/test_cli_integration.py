import io
import os
import subprocess
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

from codex_workbench.cli import main


def run_git(path, *args):
    return subprocess.run(
        ["git", "-C", str(path), *args],
        text=True,
        capture_output=True,
        check=True,
    )


class CliIntegrationSmokeTests(unittest.TestCase):
    def test_project_session_handoff_resume_and_clipboard_fallback(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            repo = base / "repo"
            repo.mkdir()
            run_git(repo, "init", "-q")
            run_git(repo, "config", "user.name", "Smoke User")
            run_git(repo, "config", "user.email", "smoke@example.com")
            run_git(repo, "commit", "--allow-empty", "-q", "-m", "initial")
            run_git(
                repo,
                "remote",
                "add",
                "origin",
                "https://github.com/example/smoke.git",
            )
            environment = {
                "XDG_CONFIG_HOME": str(base / "config"),
                "XDG_DATA_HOME": str(base / "data"),
                "XDG_SESSION_TYPE": "tty",
                "DISPLAY": "",
                "WAYLAND_DISPLAY": "",
            }
            stdout = io.StringIO()
            stderr = io.StringIO()
            with mock.patch.dict(os.environ, environment, clear=False):
                with redirect_stdout(stdout), redirect_stderr(stderr):
                    self.assertEqual(
                        main(
                            [
                                "add",
                                "smoke",
                                str(repo),
                                "--codex-account",
                                "smoke-account",
                            ]
                        ),
                        0,
                    )
                    self.assertEqual(
                        main(
                            [
                                "session",
                                "start",
                                "smoke",
                                "smoke-task",
                                "--objective",
                                "Exercise the workflow",
                                "--next-action",
                                "Create a handoff",
                            ]
                        ),
                        0,
                    )
                    self.assertEqual(
                        main(
                            [
                                "handoff",
                                "smoke",
                                "--to",
                                "second-account",
                                "--current-state",
                                "Smoke state captured",
                            ]
                        ),
                        0,
                    )
                    self.assertEqual(main(["resume", "smoke"]), 0)
                    self.assertEqual(main(["copy-all", "smoke"]), 0)

            project_sessions = (
                base / "data" / "codex-workbench" / "sessions" / "smoke"
            )
            session_files = list(project_sessions.glob("*/session.json"))
            self.assertEqual(len(session_files), 1)
            session_dir = session_files[0].parent
            self.assertTrue((session_dir / "handoff.md").is_file())
            self.assertTrue(list((session_dir / "handoffs").glob("*/handoff.md")))
            self.assertIn("CURRENT SESSION: smoke-task", stdout.getvalue())
            self.assertIn("Clipboard copy unavailable (tty)", stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
