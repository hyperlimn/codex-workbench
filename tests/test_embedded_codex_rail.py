from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gtk  # noqa: E402

from codex_workbench import codex_presentation
from codex_workbench.codex_presentation import (
    CodexRailState,
    StatusModel,
    ThemeModel,
    apply_terminal_theme,
    create_status_rail_widget,
    launcher_core,
)
from codex_workbench.gui import workspace as gui_workspace
from codex_workbench.gui.workspace import (
    CodexPaneProvider,
    CodexSurface,
    ProviderContext,
)
from codex_workbench.models import Project
from codex_workbench.terminal import embedded_codex_environment
from codex_workbench.workspace import WorkspacePane
from codex_workbench.workspace_runtime import WorkspaceRuntimeRegistry


def status(account: str, *, usage: str = "80%") -> StatusModel:
    return StatusModel(
        directory=launcher_core.DirectoryPresentation.from_path(
            Path("/tmp/projects/example")
        ),
        account=account,
        plan="Plus",
        model="gpt-5 · high",
        five_hour=usage,
        five_hour_reset="12:30",
        weekly="72%",
        weekly_reset="Fri 09:00",
    )


class FakeTerminal:
    def __init__(self):
        self.inherited_background = object()
        self.color_calls = []
        self.cursor = None

    def get_color_background_for_draw(self):
        return self.inherited_background

    def set_colors(self, foreground, background, palette):
        self.color_calls.append((foreground, background, palette))

    def set_color_cursor(self, color):
        self.cursor = color


class FakeSession:
    def __init__(self):
        self.widget = Gtk.Box()
        self.terminal = FakeTerminal()
        self.closed = False
        self.exited = False
        self.input = []

    @property
    def alive(self):
        return not self.closed and not self.exited

    def focus(self):
        pass

    def paste_text(self, _text):
        pass

    def send_command(self, _command):
        pass

    def feed_input(self, payload):
        self.input.append(payload)

    def close(self):
        self.closed = True


class FakeRailState:
    def __init__(self, account: str, theme: ThemeModel | None = None):
        self.status = status(account)
        self.theme = theme or ThemeModel.default()
        self.next_status = self.status
        self.next_theme = self.theme
        self.closed = False
        self.refreshes = 0

    def refresh(self):
        self.refreshes += 1
        self.status = self.next_status
        self.theme = self.next_theme
        return self.status, self.theme

    def close(self):
        self.closed = True


def make_surface(account: str = "account-a", theme: ThemeModel | None = None):
    session = FakeSession()
    rail_state = FakeRailState(account, theme)
    with mock.patch.object(gui_workspace.GLib, "timeout_add", return_value=0):
        surface = CodexSurface(session, {"account": account}, rail_state)
    return surface, session, rail_state


class EmbeddedCodexRailTests(unittest.TestCase):
    def test_embedded_codex_surface_places_shared_rail_above_existing_vte(self):
        surface, session, _rail_state = make_surface()

        self.assertIs(surface.widget.get_first_child(), surface.status_rail)
        self.assertIs(surface.status_rail.get_next_sibling(), session.widget)
        self.assertIs(surface.session, session)
        surface.close()

    def test_rail_uses_shared_launcher_status_and_theme_models(self):
        model = status("shared-account")
        theme = ThemeModel.from_preset("cobalt")
        rail = create_status_rail_widget(model, theme)

        self.assertIs(rail.presentation.status, model)
        self.assertIs(rail.presentation.theme, theme)
        self.assertIsInstance(rail.presentation.theme, ThemeModel)
        self.assertEqual(
            tuple(group.name for group in rail.presentation.groups),
            ("directory", "identity", "model", "five_hour", "weekly"),
        )

    def test_launcher_tracker_refresh_updates_directory_model_effort_and_limits(self):
        now = int(launcher_core.time.time())
        account = launcher_core.Account("live-account", Path(os.devnull))
        initial = launcher_core.StatusSnapshot(
            account,
            "--",
            launcher_core.ModelSettings("gpt-5", "medium"),
            Path("/tmp/initial"),
            None,
        )
        updated = launcher_core.StatusSnapshot(
            account,
            "Pro",
            launcher_core.ModelSettings("gpt-5.6", "xhigh"),
            Path("/tmp/changed/project"),
            {
                "primary": {
                    "window_minutes": 300,
                    "used_percent": 23,
                    "resets_at": now + 3_600,
                },
                "secondary": {
                    "window_minutes": 10_080,
                    "used_percent": 41,
                    "resets_at": now + 86_400,
                },
            },
        )

        class Tracker:
            def __init__(self, _account, _cwd):
                self.snapshot = initial

            def refresh(self, _child_pid=None):
                self.snapshot = updated
                return updated

        state = CodexRailState(
            account.name,
            initial.cwd,
            account=account,
            theme_store=SimpleNamespace(
                theme_model_for=lambda _name: ThemeModel.default()
            ),
            tracker_factory=Tracker,
        )

        refreshed, _theme = state.refresh()

        self.assertEqual(refreshed.directory.full, "/tmp/changed/project")
        self.assertEqual(refreshed.plan, "Pro")
        self.assertEqual(refreshed.model, "gpt-5.6 xhigh")
        self.assertEqual(refreshed.five_hour, "77%")
        self.assertEqual(refreshed.weekly, "59%")
        self.assertNotEqual(refreshed.five_hour_reset, "--")
        self.assertNotEqual(refreshed.weekly_reset, "--")

    def test_same_theme_store_drives_standalone_model_and_workbench_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "codex-start"
            root.mkdir()
            (root / "themes.json").write_text(
                json.dumps(
                    {
                        "version": 2,
                        "accounts": {
                            "same-account": {
                                "preset": "forest",
                                "account": "#123456",
                                "terminal_background_mode": "themed",
                            }
                        },
                    }
                ),
                encoding="utf-8",
            )
            store = launcher_core.ThemeStore(root)
            account = launcher_core.Account("same-account", Path(tmp) / "home")

            class Tracker:
                def __init__(self, selected, cwd):
                    self.snapshot = launcher_core.StatusSnapshot(
                        selected,
                        "Plus",
                        launcher_core.ModelSettings("gpt-5", "high"),
                        cwd,
                        None,
                    )

                def refresh(self, _child_pid=None):
                    return self.snapshot

            rail_state = CodexRailState(
                account.name,
                Path(tmp),
                account=account,
                theme_store=store,
                tracker_factory=Tracker,
            )

            self.assertEqual(rail_state.theme, store.theme_model_for(account.name))
            self.assertEqual(rail_state.theme.account, "#123456")
            self.assertEqual(rail_state.theme.preset, "forest")

    def test_real_gtk_allocation_reflows_narrow_rail_to_two_rows(self):
        rail = create_status_rail_widget(status("narrow"), ThemeModel.default())
        self.assertGreater(rail.wide_natural_width, 0)

        rail.do_size_allocate(rail.wide_natural_width - 1, 56, -1)

        self.assertTrue(rail.responsive_layout.is_two_row)
        self.assertFalse(rail._wide.get_visible())
        self.assertTrue(rail._narrow.get_visible())

    def test_real_gtk_allocation_keeps_wide_rail_on_one_row(self):
        rail = create_status_rail_widget(status("wide"), ThemeModel.default())

        rail.do_size_allocate(rail.wide_natural_width, 28, -1)

        self.assertFalse(rail.responsive_layout.is_two_row)
        self.assertTrue(rail._wide.get_visible())
        self.assertFalse(rail._narrow.get_visible())

    def test_terminal_background_modes_apply_independently_to_vte(self):
        base = ThemeModel.default()
        inherited = object()

        for mode in ("inherit", "neutral", "themed"):
            with self.subTest(mode=mode):
                terminal = FakeTerminal()
                theme = ThemeModel.from_mapping(
                    base.as_dict(),
                    terminal_background_mode=mode,
                    neutral_terminal_background="#121416",
                )
                apply_terminal_theme(terminal, theme, inherited)
                _foreground, background, palette = terminal.color_calls[-1]
                self.assertIsNone(palette)
                if mode == "inherit":
                    self.assertIs(background, inherited)
                else:
                    expected = (
                        theme.background if mode == "themed" else "#121416"
                    )
                    actual = tuple(
                        round(value * 255)
                        for value in (background.red, background.green, background.blue)
                    )
                    self.assertEqual(
                        actual,
                        tuple(int(expected[index : index + 2], 16) for index in (1, 3, 5)),
                    )

    def test_pane_a_refresh_cannot_update_pane_b(self):
        surface_a, _session_a, state_a = make_surface("account-a")
        surface_b, _session_b, state_b = make_surface(
            "account-b", ThemeModel.from_preset("crimson")
        )
        before_b = surface_b.status_rail.presentation.status
        state_a.next_status = status("account-a", usage="31%")

        surface_a._poll_status()

        self.assertEqual(surface_a.status_rail.presentation.status.five_hour, "31%")
        self.assertIs(surface_b.status_rail.presentation.status, before_b)
        self.assertEqual(state_b.refreshes, 0)
        surface_a.close()
        surface_b.close()

    def test_project_switching_registry_preserves_each_rail_association(self):
        registry = WorkspaceRuntimeRegistry()
        surface_a, _session_a, _state_a = make_surface("account-a")
        surface_b, _session_b, _state_b = make_surface("account-b")
        runtime_a = SimpleNamespace(surface=surface_a)
        runtime_b = SimpleNamespace(surface=surface_b)
        registry.put("project-a", "codex", runtime_a)
        registry.put("project-b", "codex", runtime_b)

        self.assertIs(registry.get("project-a", "codex").surface, surface_a)
        self.assertEqual(
            registry.get("project-a", "codex")
            .surface.status_rail.presentation.status.account,
            "account-a",
        )
        self.assertEqual(
            registry.get("project-b", "codex")
            .surface.status_rail.presentation.status.account,
            "account-b",
        )
        surface_a.close()
        surface_b.close()

    def test_closing_pane_disconnects_refresh_and_presentation_callbacks(self):
        session = FakeSession()
        rail_state = FakeRailState("closing-account")
        with mock.patch.object(
            gui_workspace.GLib, "timeout_add", return_value=41
        ), mock.patch.object(gui_workspace.GLib, "source_remove") as remove:
            surface = CodexSurface(session, {}, rail_state)
            surface.close()

        remove.assert_called_once_with(41)
        self.assertTrue(session.closed)
        self.assertTrue(rail_state.closed)
        self.assertIsNone(surface.status_rail.presentation._on_change)

    def test_embedded_provider_keeps_hosted_flag_and_never_calls_host_window(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            project = Project("demo", str(root))
            pane = WorkspacePane(
                "codex-one",
                "codex",
                "Codex · account-a",
                {"account": "account-a", "cwd": str(root)},
            )
            captured = {}

            class Backend:
                available = True
                unavailable_reason = ""

                def create(self, spec, *, on_exit=None):
                    captured["spec"] = spec
                    return FakeSession()

            context = ProviderContext(
                project=project,
                pane=pane,
                terminal=Backend(),
                codex_command=lambda account, prompt: ["/codex-start", account],
                state_changed=lambda _state: None,
                copy_text=lambda _text: None,
                open_url=lambda _url: None,
                open_folder=lambda _path: None,
                shell_here=lambda _path: None,
                report_error=self.fail,
            )
            fake_state = FakeRailState("account-a")
            with mock.patch.object(
                gui_workspace, "CodexRailState", return_value=fake_state
            ), mock.patch.object(
                gui_workspace.GLib, "timeout_add", return_value=0
            ), mock.patch.object(
                launcher_core, "_launch_terminal_host"
            ) as nested_host:
                surface = CodexPaneProvider().create(context)

        environment = dict(
            item.split("=", 1) for item in captured["spec"].environment
        )
        self.assertEqual(environment["CODEX_START_HOSTED"], "1")
        nested_host.assert_not_called()
        self.assertNotIn("codex_terminal_ui", sys.modules)
        surface.close()

    def test_missing_and_corrupt_theme_config_fall_back_without_writes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "config" / "codex-start"
            store = launcher_core.ThemeStore(root)
            missing = store.theme_model_for("account-a")
            self.assertFalse(root.exists())
            root.mkdir(parents=True)
            store.path.write_text("{broken", encoding="utf-8")

            corrupt = store.theme_model_for("account-a")

        self.assertEqual(missing, ThemeModel.default())
        self.assertEqual(corrupt, ThemeModel.default())

    def test_default_theme_path_uses_xdg_codex_start_directory(self):
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(
            os.environ, {"XDG_CONFIG_HOME": tmp}
        ):
            store = launcher_core.ThemeStore()

        self.assertEqual(
            store.path, Path(tmp) / "codex-start" / "themes.json"
        )

    def test_only_generic_presets_are_built_in(self):
        self.assertEqual(
            tuple(launcher_core.THEME_PRESETS),
            ("default", "crimson", "cobalt", "forest", "graphite"),
        )
        self.assertEqual(
            tuple(
                launcher_core.THEME_PRESETS[name].label
                for name in launcher_core.THEME_PRESETS
            ),
            ("Default", "Crimson", "Cobalt", "Forest", "Graphite"),
        )

    def test_copy_transcript_is_write_only_and_bound_to_active_session(self):
        surface, session, _rail_state = make_surface("transcript-account")
        button = SimpleNamespace(tooltip="", set_tooltip_text=lambda value: None)

        surface._copy_full_transcript(button)

        self.assertEqual(session.input, [b"/export\r\x1b[B\r"])
        surface.close()

    def test_bundle_contains_rail_but_not_standalone_host_machinery(self):
        bundled = codex_presentation.BUNDLED_LAUNCHER_DIRECTORY
        self.assertTrue((bundled / "codex_terminal_rail.py").is_file())
        self.assertFalse((bundled / "codex_terminal_ui.py").exists())
        self.assertFalse((bundled / "codex_terminal_bridge.py").exists())
        self.assertFalse((bundled / "codex_theme_ui.py").exists())
        source = (bundled / "codex_terminal_rail.py").read_text(encoding="utf-8")
        self.assertNotIn("Gtk.ApplicationWindow", source)
        self.assertNotIn("Vte.Terminal", source)

    def test_hosted_environment_contract_remains_enabled(self):
        environment = dict(
            item.split("=", 1) for item in embedded_codex_environment({"PATH": "/bin"})
        )

        self.assertEqual(environment["CODEX_START_HOSTED"], "1")


if __name__ == "__main__":
    unittest.main()
