import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from codex_workbench.gui import workspace as gui_workspace
from codex_workbench.gui.clipboard import GdkClipboardService
from codex_workbench.gui.dock import WorkspaceDock
from codex_workbench.gui.workspace import (
    BrowserPaneProvider,
    TerminalSurface,
    browser_capability,
)
from codex_workbench.models import Project
from codex_workbench.projects import ProjectRegistry
from codex_workbench.services import Workbench
from codex_workbench.sessions import SessionStore
from codex_workbench.store import load_projects, save_projects
from codex_workbench.workspace import (
    ProjectWorkspace,
    SplitLayout,
    WorkspacePane,
)
from codex_workbench.workspace_runtime import WorkspaceRuntimeRegistry


class FakeCodex:
    available = True
    launcher = "/fake/codex-start"

    def list_accounts(self):
        return []

    def read_status(self, account):
        return None

    def command(self, account, *, initial_prompt=""):
        result = [self.launcher, account]
        if initial_prompt:
            result.extend(("--", initial_prompt))
        return result


class WorkspaceV05Tests(unittest.TestCase):
    def make_app(self, base: Path) -> Workbench:
        return Workbench(
            projects=ProjectRegistry(base / "config" / "projects.json"),
            sessions=SessionStore(base / "data" / "sessions"),
            codex=FakeCodex(),
        )

    def add_project(self, app: Workbench, base: Path, name: str) -> Project:
        root = base / name
        root.mkdir()
        project = Project(name, str(root), codex_account="account-a")
        app.projects.save(project)
        return project

    def test_v04_registry_migrates_to_v05_with_backup(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "projects.json"
            original = {
                "version": 3,
                "document_future": True,
                "projects": [
                    {
                        "id": "demo",
                        "name": "Demo",
                        "directory": tmp,
                        "project_future": {"keep": True},
                    }
                ],
            }
            path.write_text(json.dumps(original), encoding="utf-8")

            project = load_projects(path)["demo"]
            self.assertEqual(project.workspace, ProjectWorkspace())
            save_projects({"demo": project}, path)
            migrated = json.loads(path.read_text(encoding="utf-8"))
            backup = json.loads(
                path.with_name("projects.json.v0.4.0.bak").read_text(
                    encoding="utf-8"
                )
            )

        self.assertEqual(migrated["version"], 4)
        self.assertEqual(backup, original)
        self.assertTrue(migrated["document_future"])
        self.assertTrue(migrated["projects"][0]["project_future"]["keep"])
        self.assertEqual(migrated["projects"][0]["workspace"]["panes"], [])

    def test_project_info_collapsed_state_is_isolated_and_persistent(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            app = self.make_app(base)
            self.add_project(app, base, "one")
            self.add_project(app, base, "two")

            app.set_project_info_collapsed("one", True)

            self.assertTrue(app.project("one").workspace.info_collapsed)
            self.assertFalse(app.project("two").workspace.info_collapsed)

    def test_prompt_hold_is_isolated_per_project(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            app = self.make_app(base)
            self.add_project(app, base, "one")
            self.add_project(app, base, "two")

            app.hold_prompt("one", "frozen prompt")

            self.assertEqual(app.project("one").workspace.prompt_hold, "frozen prompt")
            self.assertEqual(app.project("two").workspace.prompt_hold, "")
            app.clear_prompt_hold("one")
            self.assertEqual(app.project("one").workspace.prompt_hold, "")

    def test_project_commands_persist_with_stable_identity_and_target(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            app = self.make_app(base)
            project = self.add_project(app, base, "demo")
            command = app.add_project_command(
                "demo",
                name="Tests",
                command="python3 -m unittest",
                description="Run tests",
                category="Tests",
                command_id="command-tests",
            )

            reloaded, cwd = app.project_command_target("demo", command.id)
            document = json.loads(app.projects.path.read_text(encoding="utf-8"))

        self.assertEqual(reloaded.id, "command-tests")
        self.assertEqual(reloaded.description, "Run tests")
        self.assertEqual(cwd, project.path)
        self.assertEqual(document["projects"][0]["workspace"]["commands"][0]["id"], "command-tests")

    def test_workspace_layout_and_panes_are_isolated_between_projects(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            app = self.make_app(base)
            self.add_project(app, base, "one")
            self.add_project(app, base, "two")

            terminal = app.add_workspace_pane(
                "one", "terminal", pane_id="pane-terminal"
            )
            browser = app.add_workspace_pane(
                "one",
                "browser",
                pane_id="pane-browser",
                anchor_id=terminal.id,
                placement="below",
            )
            app.add_workspace_pane("two", "files", pane_id="pane-files")
            one = app.project("one").workspace
            two = app.project("two").workspace

        self.assertEqual(set(one.layout_ids()), {terminal.id, browser.id})
        self.assertEqual(two.layout_ids(), ("pane-files",))
        self.assertFalse(set(one.layout_ids()) & set(two.layout_ids()))

    def test_split_layout_serializes_and_restores_identity_and_ratio(self):
        layout = SplitLayout.split(
            "vertical",
            SplitLayout.pane("codex-1"),
            SplitLayout.split(
                "horizontal",
                SplitLayout.pane("terminal-1"),
                SplitLayout.pane("browser-1"),
                0.63,
            ),
            0.42,
        )
        restored = SplitLayout.from_value(layout.to_dict())

        self.assertEqual(restored.pane_ids(), ("codex-1", "terminal-1", "browser-1"))
        self.assertEqual(restored.orientation, "vertical")
        self.assertAlmostEqual(restored.ratio, 0.42)
        self.assertAlmostEqual(restored.second.ratio, 0.63)

    def test_repeated_codex_focus_deduplicates_and_explicit_new_does_not(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            app = self.make_app(base)
            self.add_project(app, base, "demo")

            first, first_created = app.ensure_codex_pane("demo")
            repeated, repeated_created = app.ensure_codex_pane("demo")
            another, another_created = app.ensure_codex_pane("demo", new=True)
            workspace = app.project("demo").workspace

        self.assertTrue(first_created)
        self.assertFalse(repeated_created)
        self.assertEqual(first.id, repeated.id)
        self.assertTrue(another_created)
        self.assertNotEqual(first.id, another.id)
        self.assertEqual(len(workspace.panes_of_type("codex")), 2)

    def test_runtime_registry_preserves_project_process_objects_on_switch(self):
        registry = WorkspaceRuntimeRegistry()
        one = object()
        two = object()
        registry.put("project-one", "terminal", one)
        registry.put("project-two", "terminal", two)

        self.assertIs(registry.get("project-one", "terminal"), one)
        self.assertIs(registry.get("project-two", "terminal"), two)
        self.assertEqual(registry.project("project-one"), (one,))

    def test_dock_state_persists_through_project_registry(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            app = self.make_app(base)
            self.add_project(app, base, "demo")
            pane = app.add_workspace_pane(
                "demo", "terminal", pane_id="terminal-one"
            )

            app.set_pane_docked("demo", pane.id, False)
            undocked = app.project("demo").workspace.pane(pane.id)
            app.set_pane_docked("demo", pane.id, True)
            docked = app.project("demo").workspace.pane(pane.id)

        self.assertFalse(undocked.docked)
        self.assertTrue(docked.docked)
        self.assertEqual(docked.id, pane.id)

    def test_dock_undock_and_focus_do_not_replace_split_layout(self):
        workspace = ProjectWorkspace()
        first = workspace.add_pane("terminal", pane_id="one")
        second = workspace.add_pane(
            "files", pane_id="two", anchor_id=first.id, placement="below"
        )
        original = workspace.layout.to_dict()

        workspace.focus(second.id)
        self.assertEqual(workspace.layout.to_dict(), original)
        workspace.focus("")
        self.assertEqual(workspace.layout.to_dict(), original)

        workspace.undock_pane(second.id)
        self.assertFalse(second.docked)
        self.assertEqual(workspace.layout_ids(), (first.id,))
        workspace.dock_pane(second.id)
        self.assertTrue(second.docked)
        self.assertEqual(set(workspace.layout_ids()), {first.id, second.id})

    def test_missing_browser_dependency_is_a_graceful_capability(self):
        with mock.patch.object(gui_workspace, "WebKit", None):
            capability = browser_capability()
            provider = BrowserPaneProvider()
            provider_available = provider.available

        self.assertFalse(capability.available)
        self.assertFalse(provider_available)
        self.assertIn("gir1.2-webkit-6.0", capability.detail)

    def test_command_discovery_only_suggests_and_never_persists(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            app = self.make_app(base)
            project = self.add_project(app, base, "demo")
            (project.path / "package.json").write_text(
                json.dumps({"scripts": {"test": "vitest", "dev": "vite"}}),
                encoding="utf-8",
            )

            suggestions = app.command_suggestions("demo")
            persisted = app.project("demo").workspace.commands

        self.assertEqual({item.command for item in suggestions}, {"npm run test", "npm run dev"})
        self.assertEqual(persisted, [])

    def test_command_execution_creates_terminal_when_none_is_suitable(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            project = Project("demo", str(root), workspace=ProjectWorkspace())
            pane = WorkspacePane(
                "terminal-created",
                "terminal",
                "Terminal · root",
                {"cwd": str(root)},
            )

            class Session:
                alive = True

                def __init__(self):
                    self.commands = []

                @property
                def widget(self):
                    return object()

                def send_command(self, command):
                    self.commands.append(command)

            surface = TerminalSurface(Session(), pane.provider_state)
            added = []
            dock = SimpleNamespace(
                project=project,
                report=self.fail,
                add_pane=lambda *args, **kwargs: (added.append((args, kwargs)) or pane),
                _runtime=lambda _pane_id: SimpleNamespace(surface=surface),
                focus_pane=lambda _pane_id: None,
            )

            executed = WorkspaceDock.run_command(dock, "npm test", root)

        self.assertTrue(executed)
        self.assertEqual(added[0][0], ("terminal",))
        self.assertEqual(added[0][1]["state"], {"cwd": str(root)})
        self.assertEqual(surface.session.commands, ["npm test"])

    def test_prompt_hold_clipboard_read_is_one_shot_text_snapshot(self):
        class Clipboard:
            reads = 0

            def read_text_async(self, _cancel, callback):
                self.reads += 1
                callback(self, object())

            def read_text_finish(self, _result):
                return "snapshot"

        clipboard = Clipboard()
        values = []
        service = GdkClipboardService(
            display_getter=lambda: SimpleNamespace(
                get_clipboard=lambda: clipboard
            )
        )

        started = service.read_text(
            lambda text, error: values.append((text, error))
        )

        self.assertTrue(started)
        self.assertEqual(clipboard.reads, 1)
        self.assertEqual(values, [("snapshot", "")])

    def test_command_execution_is_fed_to_matching_visible_terminal(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            workspace = ProjectWorkspace()
            pane = workspace.add_pane(
                "terminal",
                pane_id="terminal-visible",
                provider_state={"cwd": str(root)},
            )
            project = Project("demo", str(root), workspace=workspace)

            class Session:
                def __init__(self):
                    self.commands = []

                
                def widget(self):
                    return object()

                def send_command(self, command):
                    self.commands.append(command)

            session = Session()
            surface = TerminalSurface(session, pane.provider_state)
            focused = []
            dock = SimpleNamespace()
            dock.project = project
            dock.report = self.fail
            dock._runtime = lambda _pane_id: SimpleNamespace(surface=surface)
            dock.focus_pane = focused.append

            executed = WorkspaceDock.run_command(dock, "npm test", root)

        self.assertTrue(executed)
        self.assertEqual(session.commands, ["npm test"])
        self.assertEqual(focused, ["terminal-visible"])


if __name__ == "__main__":
    unittest.main()
