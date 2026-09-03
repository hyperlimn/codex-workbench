import time
import unittest
from types import SimpleNamespace

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import GLib, Gtk  # noqa: E402

from codex_workbench.clipboard import ClipboardResult
from codex_workbench.gui.panels import (
    plan_panel_layout,
)
from codex_workbench.gui.project_panels import (
    PROJECT_INFO_CARDS,
    ProjectPanelsMixin,
)
from codex_workbench.workspace import ProjectWorkspace


class FakeWidget:
    def __init__(self):
        self.visible = True
        self.text = ""
        self.reveal_child = None

    def set_visible(self, visible):
        self.visible = visible

    def set_text(self, text):
        self.text = text

    def set_reveal_child(self, reveal):
        self.reveal_child = reveal


class FakeClipboard:
    def __init__(self, text=None, error=""):
        self.text = text
        self.error = error
        self.reads = 0

    def read_text(self, callback):
        self.reads += 1
        callback(self.text, self.error)
        return True


class FakeController:
    def __init__(self, held=None):
        self.holds = dict(held or {})
        self.held_calls = []
        self.cleared = []
        self.collapsed = []

    def hold_prompt(self, project, text):
        self.held_calls.append((project, text))
        self.holds[project] = text
        return text

    def clear_prompt_hold(self, project):
        self.cleared.append(project)
        self.holds[project] = ""

    def set_project_info_collapsed(self, project, collapsed):
        self.collapsed.append((project, collapsed))


def workspace_state(project_id="one", prompt=""):
    project_workspace = ProjectWorkspace(prompt_hold=prompt)
    project = SimpleNamespace(
        registry_id=project_id,
        workspace=project_workspace,
        associated_paths=[],
        instructions=[],
    )
    return SimpleNamespace(
        project=project,
        status=SimpleNamespace(
            git=SimpleNamespace(file_changes=[])
        ),
        objective="",
        threads=(),
        activity=(),
    )


class PromptHarness(ProjectPanelsMixin):
    def __init__(self, prompt="", project_id="one"):
        self.workspace = workspace_state(project_id, prompt)
        self.controller = FakeController({project_id: prompt})
        self.native_clipboard = FakeClipboard()
        self.messages = []
        self.copied = []
        self.prompt_hold_button = FakeWidget()
        self.prompt_hold_state_box = FakeWidget()
        self.prompt_hold_status_label = FakeWidget()
        self.info_revealer = FakeWidget()
        self.info_toggle_label = FakeWidget()

    def toast(self, message):
        self.messages.append(message)

    def _copy_text(self, text):
        self.copied.append(text)
        return ClipboardResult(True, "test", "fake")

    def _render_project_commands(self, _workspace):
        pass


class ProjectInfoWidgetHarness(ProjectPanelsMixin):
    def _panel(self):
        panel = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL,
            spacing=5,
        )
        panel.add_css_class("panel")
        return panel

    def _memory_pair(self, parent, title):
        parent.append(Gtk.Label(label=title))
        value = Gtk.Label(label="—", wrap=True, selectable=True)
        parent.append(value)
        return value


class PromptHoldUtilityTests(unittest.TestCase):
    def test_prompt_hold_is_not_a_project_info_card(self):
        keys = tuple(item.key for item in PROJECT_INFO_CARDS)
        self.assertNotIn("prompt_hold", keys)
        self.assertIn("project_roots", keys)
        self.assertIn("project_commands", keys)
        self.assertEqual(len(keys), 7)

    def test_empty_and_held_utility_states_are_compact_and_accessible(self):
        harness = PromptHarness()
        utility = harness._build_prompt_hold_utility()
        self.assertIsNotNone(utility)
        self.assertEqual(harness.prompt_hold_button.get_label(), "Hold Prompt")
        self.assertEqual(
            harness.prompt_hold_button.get_tooltip_text(),
            "Hold clipboard text for this project",
        )
        self.assertTrue(harness.prompt_hold_button.get_visible())
        self.assertFalse(harness.prompt_hold_state_box.get_visible())
        self.assertTrue(harness.prompt_hold_button.get_focusable())

        harness._render_prompt_hold_utility("hello")
        self.assertFalse(harness.prompt_hold_button.get_visible())
        self.assertTrue(harness.prompt_hold_state_box.get_visible())
        self.assertEqual(
            harness.prompt_hold_status_label.get_text(), "HELD · 5 chars"
        )
        self.assertEqual(harness.prompt_copy_button.get_label(), "Copy")
        self.assertEqual(
            harness.prompt_copy_button.get_tooltip_text(), "Copy held prompt"
        )
        self.assertEqual(harness.prompt_clear_button.get_label(), "Clear")
        self.assertEqual(
            harness.prompt_clear_button.get_tooltip_text(), "Clear held prompt"
        )
        self.assertTrue(harness.prompt_copy_button.get_focusable())
        self.assertTrue(harness.prompt_clear_button.get_focusable())

    def test_one_click_capture_reads_once_and_stores_exact_text(self):
        harness = PromptHarness()
        harness.native_clipboard = FakeClipboard("line one\nline two")
        harness.action_hold_clipboard()
        self.assertEqual(harness.native_clipboard.reads, 1)
        self.assertEqual(harness.controller.held_calls, [("one", "line one\nline two")])
        self.assertEqual(
            harness.workspace.project.workspace.prompt_hold, "line one\nline two"
        )
        self.assertTrue(harness.prompt_hold_state_box.visible)

    def test_clipboard_failure_preserves_existing_prompt(self):
        harness = PromptHarness("keep me")
        harness.native_clipboard = FakeClipboard(error="read failed")
        harness.action_hold_clipboard()
        self.assertEqual(harness.native_clipboard.reads, 1)
        self.assertEqual(harness.controller.held_calls, [])
        self.assertEqual(harness.workspace.project.workspace.prompt_hold, "keep me")
        self.assertEqual(harness.controller.holds["one"], "keep me")
        self.assertIn("Clipboard unavailable", harness.messages[-1])

    def test_non_text_clipboard_preserves_existing_prompt(self):
        harness = PromptHarness("keep me")
        harness.native_clipboard = FakeClipboard(object())
        harness.action_hold_clipboard()
        self.assertEqual(harness.controller.held_calls, [])
        self.assertEqual(harness.workspace.project.workspace.prompt_hold, "keep me")
        self.assertEqual(harness.controller.holds["one"], "keep me")
        self.assertIn("does not contain text", harness.messages[-1])

    def test_copy_writes_exactly_the_held_text(self):
        harness = PromptHarness("preserve\nspacing ")
        harness.action_copy_held_prompt()
        self.assertEqual(harness.copied, ["preserve\nspacing "])
        self.assertEqual(harness.messages[-1], "Copied held prompt")

    def test_clear_only_removes_the_current_projects_prompt(self):
        harness = PromptHarness("one text")
        harness.controller = FakeController({"one": "one text", "two": "two text"})
        harness.action_clear_held_prompt()
        self.assertEqual(harness.controller.cleared, ["one"])
        self.assertEqual(harness.controller.holds["one"], "")
        self.assertEqual(harness.controller.holds["two"], "two text")
        self.assertEqual(harness.workspace.project.workspace.prompt_hold, "")
        self.assertFalse(harness.prompt_hold_state_box.visible)

    def test_delayed_capture_remains_bound_to_the_originating_project(self):
        class DelayedClipboard:
            def __init__(self):
                self.callback = None
                self.reads = 0

            def read_text(self, callback):
                self.reads += 1
                self.callback = callback
                return True

        harness = PromptHarness(project_id="one")
        harness.controller = FakeController({"one": "", "two": ""})
        delayed = DelayedClipboard()
        harness.native_clipboard = delayed
        harness.action_hold_clipboard()
        harness.workspace = workspace_state("two")
        delayed.callback("captured for one", "")

        self.assertEqual(delayed.reads, 1)
        self.assertEqual(harness.controller.held_calls, [("one", "captured for one")])
        self.assertEqual(harness.controller.holds["one"], "captured for one")
        self.assertEqual(harness.controller.holds["two"], "")
        self.assertEqual(harness.workspace.project.workspace.prompt_hold, "")

    def test_persisted_prompt_is_rendered_by_new_utility_on_load(self):
        restored = ProjectWorkspace.from_value(
            {"prompt_hold": "persisted prompt"}
        )
        harness = PromptHarness()
        harness.workspace.project.workspace = restored
        harness._render_project_panels(harness.workspace)
        self.assertTrue(harness.prompt_hold_state_box.visible)
        self.assertEqual(harness.prompt_hold_status_label.text, "HELD · 16 chars")

    def test_collapsed_grid_hides_persists_and_restores_without_rebuild(self):
        harness = PromptHarness()
        grid = object()
        harness.project_info_grid = grid
        harness._render_info_toggle(harness.workspace)
        self.assertTrue(harness.info_revealer.reveal_child)

        harness._toggle_project_info()
        self.assertTrue(harness.workspace.project.workspace.info_collapsed)
        self.assertFalse(harness.info_revealer.reveal_child)
        self.assertEqual(harness.controller.collapsed, [("one", True)])
        self.assertIn("Commands", harness.info_toggle_label.text)
        self.assertNotIn("Prompt", harness.info_toggle_label.text)
        self.assertIs(harness.project_info_grid, grid)

        harness._toggle_project_info()
        self.assertFalse(harness.workspace.project.workspace.info_collapsed)
        self.assertTrue(harness.info_revealer.reveal_child)
        self.assertEqual(harness.controller.collapsed[-1], ("one", False))
        self.assertIs(harness.project_info_grid, grid)


class PanelLayoutTests(unittest.TestCase):
    def assert_complete_rectangle(self, layout):
        expected = tuple(item.key for item in PROJECT_INFO_CARDS)
        actual = tuple(item.key for item in layout.slots)
        self.assertEqual(actual, expected)
        row_spans = {}
        for slot in layout.slots:
            row_spans.setdefault(slot.row, 0)
            row_spans[slot.row] += slot.span
        self.assertTrue(row_spans)
        self.assertTrue(
            all(value == layout.columns for value in row_spans.values())
        )

    def test_wide_layout_uses_multiple_cards_per_row(self):
        layout = plan_panel_layout(1200, PROJECT_INFO_CARDS)
        self.assertEqual(layout.columns, 3)
        self.assert_complete_rectangle(layout)
        self.assertEqual(
            tuple(
                item.key
                for item in layout.slots
                if item.row == 0
            ),
            ("objective", "working_tree"),
        )

    def test_medium_layout_repacks_with_full_width_feature_cards(self):
        layout = plan_panel_layout(700, PROJECT_INFO_CARDS)
        self.assertEqual(layout.columns, 2)
        self.assert_complete_rectangle(layout)
        spans = {item.key: item.span for item in layout.slots}
        self.assertEqual(spans["project_commands"], 2)
        self.assertEqual(spans["project_instructions"], 2)

    def test_narrow_layout_falls_back_to_one_safe_column(self):
        layout = plan_panel_layout(400, PROJECT_INFO_CARDS)
        self.assertEqual(layout.columns, 1)
        self.assert_complete_rectangle(layout)
        self.assertTrue(all(item.span == 1 for item in layout.slots))
        self.assertEqual(len({item.row for item in layout.slots}), 7)

    def test_card_metadata_and_layout_are_deterministic(self):
        first = plan_panel_layout(700, PROJECT_INFO_CARDS)
        second = plan_panel_layout(700, PROJECT_INFO_CARDS)
        self.assertEqual(first, second)
        keys = tuple(item.key for item in PROJECT_INFO_CARDS)
        self.assertEqual(len(keys), len(set(keys)))

    def test_presented_project_info_reflows_actual_card_allocations(self):
        if not Gtk.init_check():
            self.skipTest("A GTK display is required for allocation checks")

        harness = ProjectInfoWidgetHarness()
        section = harness._build_project_info_section()
        harness.info_revealer.set_transition_duration(0)
        harness.info_revealer.set_reveal_child(True)

        workspace = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        workspace.append(section)
        scroll = Gtk.ScrolledWindow(
            hscrollbar_policy=Gtk.PolicyType.NEVER,
            vscrollbar_policy=Gtk.PolicyType.AUTOMATIC,
        )
        scroll.set_child(workspace)
        window = Gtk.Window(title="Project Info allocation probe")
        window.set_child(scroll)

        grid = harness.project_info_grid
        cards = {
            item.placement.key: item.widget for item in grid._panels
        }

        def bounds(key):
            found, allocation = cards[key].compute_bounds(grid)
            self.assertTrue(found)
            return (
                round(allocation.get_x()),
                round(allocation.get_y()),
                round(allocation.get_width()),
                round(allocation.get_height()),
            )

        def wait_for_allocation(columns, width):
            deadline = time.monotonic() + 2
            while time.monotonic() < deadline:
                context = GLib.MainContext.default()
                while context.pending():
                    context.iteration(False)
                if (
                    grid._layout.columns == columns
                    and abs(grid.get_width() - width) <= 1
                    and all(widget.get_width() > 0 for widget in cards.values())
                ):
                    return
                time.sleep(0.01)
            self.fail(
                f"Expected {columns} columns at {width}px; got "
                f"{grid._layout.columns} at {grid.get_width()}px"
            )

        try:
            window.set_default_size(1200, 700)
            window.present()
            wait_for_allocation(3, 1200)
            objective = bounds("objective")
            working_tree = bounds("working_tree")
            self.assertEqual(objective[1], working_tree[1])
            self.assertNotEqual(objective[0], working_tree[0])

            window.set_default_size(700, 700)
            wait_for_allocation(2, 700)
            working_tree = bounds("working_tree")
            project_roots = bounds("project_roots")
            self.assertEqual(working_tree[1], project_roots[1])
            self.assertNotEqual(working_tree[0], project_roots[0])

            window.set_default_size(400, 700)
            wait_for_allocation(1, 400)
            narrow = tuple(bounds(item.key) for item in PROJECT_INFO_CARDS)
            self.assertEqual(len({item[0] for item in narrow}), 1)
            self.assertEqual(len({item[1] for item in narrow}), len(narrow))
        finally:
            window.destroy()
            context = GLib.MainContext.default()
            while context.pending():
                context.iteration(False)
