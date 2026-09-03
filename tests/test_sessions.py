import subprocess
import tempfile
import unittest
from pathlib import Path

from codex_workbench.git import inspect
from codex_workbench.handoff import HandoffService
from codex_workbench.models import Project
from codex_workbench.sessions import SessionStore


def run_git(path, *args):
    return subprocess.run(
        ["git", "-C", str(path), *args],
        text=True,
        capture_output=True,
        check=True,
    )


class SessionAndHandoffTests(unittest.TestCase):
    def test_session_round_trip_and_repeated_handoff_history(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            repo = base / "repo"
            repo.mkdir()
            run_git(repo, "init", "-q")
            run_git(repo, "config", "user.name", "Developer")
            run_git(repo, "config", "user.email", "dev@example.com")
            (repo / "app.py").write_text("print('one')\n", encoding="utf-8")
            run_git(repo, "add", "app.py")
            run_git(repo, "commit", "-q", "-m", "initial")
            project = Project(
                "demo",
                str(repo),
                "account-a",
                objective="Build the feature",
            )
            git = inspect(repo)
            store = SessionStore(base / "sessions")
            session = store.create(
                project,
                git,
                name="feature-build",
                next_action="Implement persistence",
            )
            transcript = base / "export.md"
            transcript.write_text("# Raw transcript\n", encoding="utf-8")
            service = HandoffService(store)

            first = service.create(
                project,
                git,
                session,
                to_account="account-b",
                transcript=transcript,
                completed=["Created the model"],
                current_state="Tests are green",
                current_problem="Clipboard selection",
                next_action="Add X11 regression coverage",
            )
            second = service.create(
                project,
                git,
                first.session,
                to_account="account-c",
                completed=["Added clipboard coverage"],
            )
            loaded = store.load(project.name, session.id)

            self.assertTrue(first.handoff_path.is_file())
            self.assertTrue(first.transcript_path.is_file())
            self.assertTrue((first.archive_dir / "handoff.md").is_file())
            self.assertNotEqual(first.archive_dir, second.archive_dir)
            self.assertEqual(len(loaded.handoff_history), 2)
            self.assertEqual(loaded.codex_account, "account-c")
            self.assertEqual(store.current(project.name).id, session.id)
            document = second.handoff_path.read_text(encoding="utf-8")
            for heading in (
                "## Objective",
                "## Completed",
                "## Current State",
                "## Current Problem",
                "## Git State",
                "## Next Action",
                "## Previous Account",
                "## Transcript",
            ):
                self.assertIn(heading, document)
            self.assertIn("See [transcript.md]", document)
            self.assertTrue((second.session_dir / "session.json").is_file())

    def test_missing_transcript_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            project = Project("demo", tmp, "account-a")
            store = SessionStore(base / "sessions")
            session = store.create(
                project,
                inspect(base),
                name="work",
            )
            with self.assertRaises(FileNotFoundError):
                HandoffService(store).create(
                    project,
                    inspect(base),
                    session,
                    transcript=base / "missing.md",
                )


if __name__ == "__main__":
    unittest.main()
