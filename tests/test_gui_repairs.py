import unittest

from codex_workbench.gui.clipboard import GdkClipboardService
from codex_workbench.gui.dialogs import AddProjectDialog, CommandPalette
from codex_workbench.gui.state import PaletteCommand

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gdk, GLib, Gtk  # noqa: E402


class FakeEntry:
    def __init__(self, text=""):
        self.text = text
        self.removed_classes = []
        self.focused = False

    def get_text(self):
        return self.text

    def set_text(self, text):
        self.text = text

    def remove_css_class(self, css_class):
        self.removed_classes.append(css_class)

    def grab_focus(self):
        self.focused = True
        return True


class FakeLabel:
    def __init__(self):
        self.text = ""
        self.visible = False

    def set_text(self, text):
        self.text = text

    def set_visible(self, visible):
        self.visible = visible


class FakeButton:
    def __init__(self):
        self.sensitive = True

    def set_sensitive(self, sensitive):
        self.sensitive = sensitive


class FakeFile:
    def __init__(self, path):
        self.path = path

    def get_path(self):
        return self.path


class FakeFileDialog:
    def __init__(self, *, path="/tmp/demo", error=None):
        self.path = path
        self.error = error
        self.parent = None
        self.cancellable = None
        self.callback = None

    def select_folder(self, parent, cancellable, callback):
        self.parent = parent
        self.cancellable = cancellable
        self.callback = callback

    def select_folder_finish(self, _result):
        if self.error is not None:
            raise self.error
        return FakeFile(self.path)

    def complete(self):
        self.callback(self, object())


class FakePaletteDialog:
    def __init__(self):
        self.focus = None
        self.presented = False
        self.closed = 0
        self.active = False

    def set_focus(self, widget):
        self.focus = widget

    def present(self):
        self.presented = True

    def close(self):
        self.closed += 1

    def is_active(self):
        return self.active


class AddProjectDialogRepairTests(unittest.TestCase):
    @staticmethod
    def make_dialog(picker_factory):
        dialog = AddProjectDialog.__new__(AddProjectDialog)
        dialog._folder_dialog_factory = picker_factory
        dialog._folder_dialog = None
        dialog._closed = False
        dialog.dialog = object()
        dialog.browse = FakeButton()
        dialog.directory_error = FakeLabel()
        dialog.directory = FakeEntry()
        dialog.name = FakeEntry()
        dialog.on_add = lambda *_args: (_ for _ in ()).throw(
            AssertionError("folder selection must not register a project")
        )
        return dialog

    def test_async_folder_selection_is_owned_and_only_populates_fields(self):
        picker = FakeFileDialog(path="/tmp/project-alpha")
        created_with = {}

        def factory(**properties):
            created_with.update(properties)
            return picker

        dialog = self.make_dialog(factory)
        dialog._choose_directory(None)

        self.assertIs(dialog._folder_dialog, picker)
        self.assertFalse(dialog.browse.sensitive)
        self.assertIs(picker.parent, dialog.dialog)
        self.assertIsNone(picker.cancellable)
        self.assertTrue(created_with["modal"])

        picker.complete()

        self.assertEqual(dialog.directory.text, "/tmp/project-alpha")
        self.assertEqual(dialog.name.text, "project-alpha")
        self.assertIn("error", dialog.directory.removed_classes)
        self.assertIsNone(dialog._folder_dialog)
        self.assertTrue(dialog.browse.sensitive)

    def test_folder_picker_cancellation_is_silent_and_cleans_up(self):
        dismissed = GLib.Error.new_literal(
            Gtk.dialog_error_quark(),
            "Dismissed",
            Gtk.DialogError.DISMISSED,
        )
        picker = FakeFileDialog(error=dismissed)
        dialog = self.make_dialog(lambda **_properties: picker)

        dialog._choose_directory(None)
        picker.complete()

        self.assertFalse(dialog.directory_error.visible)
        self.assertEqual(dialog.directory.text, "")
        self.assertIsNone(dialog._folder_dialog)
        self.assertTrue(dialog.browse.sensitive)

    def test_folder_picker_open_failure_is_recoverable(self):
        def broken_factory(**_properties):
            raise RuntimeError("portal unavailable")

        dialog = self.make_dialog(broken_factory)
        dialog._choose_directory(None)

        self.assertTrue(dialog.directory_error.visible)
        self.assertIn("portal unavailable", dialog.directory_error.text)
        self.assertIsNone(dialog._folder_dialog)
        self.assertTrue(dialog.browse.sensitive)

    def test_folder_picker_async_failure_is_recoverable(self):
        failed = GLib.Error.new_literal(
            Gtk.dialog_error_quark(),
            "Backend failed",
            Gtk.DialogError.FAILED,
        )
        picker = FakeFileDialog(error=failed)
        dialog = self.make_dialog(lambda **_properties: picker)

        dialog._choose_directory(None)
        picker.complete()

        self.assertTrue(dialog.directory_error.visible)
        self.assertIn("Backend failed", dialog.directory_error.text)
        self.assertIsNone(dialog._folder_dialog)
        self.assertTrue(dialog.browse.sensitive)

    def test_late_folder_callback_does_not_touch_closed_parent(self):
        picker = FakeFileDialog(path="/tmp/late-result")
        dialog = self.make_dialog(lambda **_properties: picker)

        dialog._choose_directory(None)
        dialog._closed = True
        picker.complete()

        self.assertEqual(dialog.directory.text, "")
        self.assertEqual(dialog.name.text, "")
        self.assertIsNone(dialog._folder_dialog)


class GdkClipboardRepairTests(unittest.TestCase):
    def test_wayland_gui_copy_uses_gdk_value_without_helper_process(self):
        class Clipboard:
            def __init__(self):
                self.text = None

            def set(self, value):
                self.text = value.get_string()

        class Display:
            def __init__(self, clipboard):
                self.clipboard = clipboard

            def get_clipboard(self):
                return self.clipboard

        clipboard = Clipboard()
        result = GdkClipboardService(
            display_getter=lambda: Display(clipboard),
            environ={
                "XDG_SESSION_TYPE": "wayland",
                "WAYLAND_DISPLAY": "wayland-0",
                "DISPLAY": ":0",
            },
        ).copy("workspace context")

        self.assertTrue(result.copied)
        self.assertEqual(result.helper, "GTK/GDK")
        self.assertEqual(result.session_type, "wayland")
        self.assertEqual(clipboard.text, "workspace context")

    def test_missing_display_and_backend_errors_are_nonfatal(self):
        missing = GdkClipboardService(
            display_getter=lambda: None,
            environ={"XDG_SESSION_TYPE": "wayland"},
        ).copy("context")

        class BrokenClipboard:
            def set(self, _value):
                raise RuntimeError("clipboard ownership failed")

        class Display:
            def get_clipboard(self):
                return BrokenClipboard()

        broken = GdkClipboardService(
            display_getter=Display,
            environ={"XDG_SESSION_TYPE": "wayland"},
        ).copy("context")

        self.assertFalse(missing.copied)
        self.assertIn("no active GTK display", missing.error_summary)
        self.assertFalse(broken.copied)
        self.assertIn("ownership failed", broken.error_summary)


class CommandPaletteRepairTests(unittest.TestCase):
    @staticmethod
    def make_palette():
        palette = CommandPalette.__new__(CommandPalette)
        palette.dialog = FakePaletteDialog()
        palette.search = FakeEntry("stale query")
        palette.commands = [
            PaletteCommand("resume", "Resume work session", ""),
            PaletteCommand("status", "Show status", ""),
        ]
        palette.on_activate = lambda _command: None
        return palette

    def test_present_clears_query_and_map_focuses_search(self):
        palette = self.make_palette()

        palette.present()
        palette._dialog_mapped(palette.dialog)
        palette.dialog.active = True
        palette._dialog_activation_changed(palette.dialog, None)

        self.assertEqual(palette.search.text, "")
        self.assertIs(palette.dialog.focus, palette.search)
        self.assertTrue(palette.search.focused)
        self.assertTrue(palette.dialog.presented)
        self.assertFalse(palette._initial_focus_pending)

    def test_typing_filters_and_enter_activation_are_preserved(self):
        palette = self.make_palette()
        populated = []
        palette._populate = lambda commands: populated.extend(commands)
        palette.search.set_text("resume")

        palette._filter(palette.search)

        self.assertEqual([item.id for item in populated], ["resume"])

        activated = []
        palette.on_activate = activated.append
        row = type("Row", (), {"command": palette.commands[0]})()
        palette._row_activated(None, row)
        self.assertEqual(activated, [palette.commands[0]])

    def test_escape_closes_without_activation_and_other_keys_propagate(self):
        palette = self.make_palette()
        activated = []
        palette.on_activate = activated.append

        self.assertFalse(palette._key_pressed(None, Gdk.KEY_Down, 0, 0))
        self.assertFalse(palette._key_pressed(None, Gdk.KEY_Return, 0, 0))
        self.assertTrue(palette._key_pressed(None, Gdk.KEY_Escape, 0, 0))

        self.assertEqual(palette.dialog.closed, 1)
        self.assertEqual(activated, [])


if __name__ == "__main__":
    unittest.main()
