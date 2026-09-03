import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from codex_workbench.gui.controller import SwitchboardController
from codex_workbench.models import AssociatedPath, Project
from codex_workbench.projects import ProjectRegistry
from codex_workbench.services import Workbench, WorkbenchError
from codex_workbench.sessions import SessionStore
from codex_workbench.store import load_projects, save_projects


def run_git(path: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-C", str(path), *args],
        text=True,
        capture_output=True,
        check=True,
    )


def make_repo(path: Path, *, branch: str = "main") -> Path:
    path.mkdir()
    run_git(path, "init", "-q", "-b", branch)
    run_git(path, "config", "user.name", "Developer")
    run_git(path, "config", "user.email", "dev@example.com")
    (path / "tracked.txt").write_text("one\n", encoding="utf-8")
    run_git(path, "add", "tracked.txt")
    run_git(path, "commit", "-q", "-m", "initial")
    run_git(
        path,
        "remote",
        "add",
        "origin",
        "https://example.com/acme/demo.git",
    )
    return path


class FakeCodex:
    available = True
    launcher = "/fake/codex-start"

    def list_accounts(self):
        return []

    def read_status(self, _account):
        return None


class ProjectAndAssociatedPathTests(unittest.TestCase):
    def make_app(self, base: Path) -> Workbench:
        return Workbench(
            projects=ProjectRegistry(base / "config" / "projects.json"),
            sessions=SessionStore(base / "data" / "sessions"),
            codex=FakeCodex(),
        )

    def test_v031_fixture_migrates_with_backup_and_unknown_fields(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "projects.json"
            path.write_text(
                json.dumps(
                    {
                        "version": 2,
                        "future_document_field": {"keep": True},
                        "projects": [
                            {
                                "name": "demo",
                                "directory": tmp,
                                "codex_account": "account-a",
                                "terminal": {
                                    "adapter": "tilix",
                                    "layout": "legacy",
                                },
                                "future_project_field": "keep-me",
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            project = load_projects(path)["demo"]
            self.assertEqual(project.associated_paths, [])
            self.assertEqual(project.terminal.mode, "external")
            self.assertEqual(project.registry_id, "demo")

            save_projects({"demo": project}, path)
            document = json.loads(path.read_text(encoding="utf-8"))
            backup = path.with_name("projects.json.v0.3.1.bak")
            backup_exists = backup.is_file()

        self.assertEqual(document["version"], 4)
        self.assertTrue(document["future_document_field"]["keep"])
        self.assertEqual(
            document["projects"][0]["future_project_field"],
            "keep-me",
        )
        self.assertTrue(backup_exists)

    def test_multiple_git_and_non_git_paths_round_trip_and_render(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            canonical = make_repo(base / "canonical")
            secondary = make_repo(base / "secondary", branch="alpha-v0.1")
            docs = base / "docs"
            docs.mkdir()
            app = self.make_app(base)
            app.projects.save(
                Project(
                    "project-alpha",
                    str(canonical),
                    "account-a",
                    associated_paths=[
                        AssociatedPath(
                            "Toolchain",
                            str(secondary),
                            "toolchain/source",
                        ),
                        AssociatedPath(
                            "Docs",
                            str(docs),
                            "docs",
                            open_shell=False,
                        ),
                    ],
                )
            )

            status = app.status("project-alpha", query_codex=False)
            reloaded = app.project("project-alpha")

        self.assertEqual(len(reloaded.associated_paths), 2)
        states = {
            item.associated.label: item
            for item in status.associated
        }
        self.assertTrue(states["Toolchain"].git.is_repository)
        self.assertEqual(states["Toolchain"].git.branch, "alpha-v0.1")
        self.assertFalse(states["Docs"].git.is_repository)
        self.assertIn("ASSOCIATED PATHS:", status.text)
        self.assertIn("Toolchain [toolchain/source]", status.text)
        self.assertIn("Docs [docs]", status.text)

    def test_ready_warns_for_optional_missing_and_fails_required_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            canonical = make_repo(base / "canonical")
            app = self.make_app(base)
            app.projects.save(
                Project(
                    "demo",
                    str(canonical),
                    "account-a",
                    associated_paths=[
                        AssociatedPath(
                            "Optional",
                            str(base / "later"),
                            "build",
                        ),
                        AssociatedPath(
                            "Required",
                            str(base / "must-exist"),
                            "data",
                            required=True,
                        ),
                    ],
                )
            )
            report = app.preflight("demo")

        checks = {
            item.label: item
            for item in report.checks
            if item.label.startswith("associated:")
        }
        self.assertEqual(checks["associated: Optional"].level, "warn")
        self.assertEqual(checks["associated: Required"].level, "fail")

    def test_shell_cwd_selection_and_disabled_path_are_safe(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            canonical = make_repo(base / "canonical")
            build = base / "build;still-a-path"
            build.mkdir()
            docs = base / "docs"
            docs.mkdir()
            app = self.make_app(base)
            app.projects.save(
                Project(
                    "demo",
                    str(canonical),
                    associated_paths=[
                        AssociatedPath("Build", str(build), "build"),
                        AssociatedPath(
                            "Docs",
                            str(docs),
                            "docs",
                            open_shell=False,
                        ),
                    ],
                )
            )

            self.assertEqual(app.shell_cwd("demo"), canonical.resolve())
            self.assertEqual(app.shell_cwd("demo", "Build"), build.resolve())
            with self.assertRaisesRegex(ValueError, "disabled"):
                app.shell_cwd("demo", "Docs")

    def test_associated_path_validation_rejects_duplicate_and_canonical_roots(self):
        with tempfile.TemporaryDirectory() as tmp:
            canonical = Path(tmp).resolve()
            with self.assertRaisesRegex(ValueError, "Duplicate"):
                ProjectRegistry.validate_associated_paths(
                    [
                        AssociatedPath("Build", str(canonical / "one")),
                        AssociatedPath("build", str(canonical / "two")),
                    ],
                    canonical=canonical,
                )
            with self.assertRaisesRegex(ValueError, "canonical root"):
                ProjectRegistry.validate_associated_paths(
                    [AssociatedPath("Source", str(canonical))],
                    canonical=canonical,
                )

    def test_edit_directory_refreshes_git_and_preserves_session_identity(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            old = make_repo(base / "old", branch="old-branch")
            new = make_repo(base / "new", branch="new-branch")
            app = self.make_app(base)
            app.projects.save(Project("demo", str(old), "account-a"))
            session = app.start_session("demo", "active")
            old_marker = old / "tracked.txt"
            new_marker = new / "tracked.txt"

            result = app.edit_project(
                "demo",
                display_name="Demo Workspace",
                directory=str(new),
                github_account="octocat",
            )
            bundle = app.handoff(
                "Demo Workspace",
                to_account="account-b",
            )
            handoff_exists = bundle.handoff_path.is_file()
            handoff_path = str(bundle.handoff_path)
            session_directory = str(
                app.sessions.session_dir("demo", session.id)
            )
            loaded_session = app.sessions.load("demo", session.id)
            renamed_activity = [
                item.action
                for item in app.recent_activity(
                    project="Demo Workspace",
                    limit=20,
                )
            ]
            renamed_id = app.project("Demo Workspace").registry_id
            old_text = old_marker.read_text(encoding="utf-8")
            new_text = new_marker.read_text(encoding="utf-8")

        self.assertTrue(result.directory_changed)
        self.assertTrue(result.session_context_warning)
        self.assertEqual(result.git.branch, "new-branch")
        self.assertEqual(renamed_id, "demo")
        self.assertEqual(loaded_session.project, "demo")
        self.assertTrue(handoff_exists)
        self.assertIn(session_directory, handoff_path)
        self.assertIn("session_started", renamed_activity)
        self.assertIn("project_updated", renamed_activity)
        self.assertEqual(old_text, "one\n")
        self.assertEqual(new_text, "one\n")

    def test_edit_rejects_invalid_directory_without_changing_registration(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            canonical = make_repo(base / "canonical")
            app = self.make_app(base)
            app.projects.save(Project("demo", str(canonical)))
            with self.assertRaises(WorkbenchError):
                app.edit_project(
                    "demo",
                    directory=str(base / "missing"),
                )
            self.assertEqual(app.project("demo").path, canonical.resolve())

    def test_safe_remove_preserves_files_sessions_and_selects_other_project(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            first = make_repo(base / "first")
            second = make_repo(base / "second")
            app = self.make_app(base)
            app.projects.save(Project("first", str(first), "account-a"))
            app.projects.save(Project("second", str(second), "account-a"))
            session = app.start_session("first", "keep-history")
            handoff_marker = (
                app.sessions.session_dir("first", session.id) / "handoff.md"
            )
            handoff_marker.write_text("preserve me\n", encoding="utf-8")
            controller = SwitchboardController(app)
            app.remember_project("first")

            result = app.remove_project("first")
            dashboard = controller.dashboard(query_codex=False)
            preserved = app.sessions.load("first", session.id)
            preserved_handoff = handoff_marker.read_text(encoding="utf-8")
            git_preserved = (first / ".git").is_dir()
            source_text = (first / "tracked.txt").read_text(encoding="utf-8")
            activity_actions = [item.action for item in app.activity.all()]

        self.assertEqual(result.preserved_sessions, 1)
        self.assertTrue(git_preserved)
        self.assertEqual(source_text, "one\n")
        self.assertEqual(preserved.name, "keep-history")
        self.assertEqual(preserved_handoff, "preserve me\n")
        self.assertIn("session_started", activity_actions)
        self.assertEqual(dashboard.selected_project, "second")

    def test_associated_path_remove_never_deletes_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            canonical = make_repo(base / "canonical")
            assets = base / "assets"
            assets.mkdir()
            marker = assets / "keep.bin"
            marker.write_bytes(b"keep")
            app = self.make_app(base)
            app.projects.save(Project("demo", str(canonical)))
            app.add_associated_path(
                "demo",
                label="Assets",
                path=str(assets),
                role="assets",
            )

            app.remove_associated_path("demo", "Assets")
            associated_paths = app.project("demo").associated_paths
            marker_data = marker.read_bytes()

        self.assertEqual(associated_paths, [])
        self.assertEqual(marker_data, b"keep")


if __name__ == "__main__":
    unittest.main()
