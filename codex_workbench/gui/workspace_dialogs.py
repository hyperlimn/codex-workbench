from __future__ import annotations

from collections.abc import Callable, Sequence

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gtk  # noqa: E402

from ..command_discovery import CommandSuggestion
from ..workspace import ProjectCommand
from .widgets import make_label


class ProjectCommandDialog:
    SAVE = 1
    REMOVE = 2

    def __init__(
        self,
        parent: Gtk.Window,
        *,
        command: ProjectCommand | None = None,
        roots: Sequence[str] = (),
        on_save: Callable[[dict[str, str]], None],
        on_remove: Callable[[], None] | None = None,
    ):
        self.command = command
        self.on_save = on_save
        self.on_remove = on_remove
        self.dialog = Gtk.Dialog(
            title="Edit project command" if command else "Add project command",
            transient_for=parent,
            modal=True,
        )
        self.dialog.set_default_size(580, 430)
        self.dialog.add_css_class("dialog-window")
        box = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL,
            spacing=10,
            margin_top=18,
            margin_bottom=18,
            margin_start=18,
            margin_end=18,
        )
        self.dialog.get_content_area().append(box)
        box.append(
            make_label(
                "EDIT PROJECT COMMAND" if command else "ADD PROJECT COMMAND",
                "dialog-title",
            )
        )
        box.append(
            make_label(
                "Commands are saved to this project and run visibly in a Workbench terminal.",
                "muted",
                wrap=True,
            )
        )
        self.name = Gtk.Entry(text=command.name if command else "")
        self.category = Gtk.Entry(
            text=command.category if command else "Development"
        )
        self.command_entry = Gtk.Entry(text=command.command if command else "")
        self.description = Gtk.Entry(
            text=command.description if command else ""
        )
        root_values = ["Canonical root", *roots]
        self.roots = Gtk.DropDown.new_from_strings(root_values)
        selected_root = command.working_directory if command else ""
        selected_index = 0
        if selected_root in roots:
            selected_index = root_values.index(selected_root)
        self.roots.set_selected(selected_index)
        for title, widget in (
            ("NAME", self.name),
            ("CATEGORY", self.category),
            ("COMMAND", self.command_entry),
            ("DESCRIPTION", self.description),
            ("WORKING DIRECTORY", self.roots),
        ):
            box.append(make_label(title, "confidence-label"))
            box.append(widget)
        self.error = make_label("", "dialog-banner", wrap=True)
        self.error.set_visible(False)
        box.append(self.error)
        self.dialog.add_button("Cancel", Gtk.ResponseType.CANCEL)
        if command is not None and on_remove is not None:
            remove = self.dialog.add_button("Remove", self.REMOVE)
            remove.add_css_class("destructive-action")
        save = self.dialog.add_button("Save", self.SAVE)
        save.add_css_class("suggested-action")
        self.dialog.connect("response", self._response)

    def _response(self, _dialog: Gtk.Dialog, response: int) -> None:
        if response == self.REMOVE and self.on_remove is not None:
            self.dialog.close()
            self.on_remove()
            return
        if response != self.SAVE:
            self.dialog.close()
            return
        name = self.name.get_text().strip()
        command = self.command_entry.get_text().strip()
        if not name or not command:
            self.error.set_text("Name and command are required.")
            self.error.set_visible(True)
            return
        selected = self.roots.get_selected()
        root = ""
        if selected > 0:
            value = self.roots.get_selected_item()
            root = value.get_string() if value is not None else ""
        self.dialog.close()
        self.on_save(
            {
                "name": name,
                "command": command,
                "description": self.description.get_text().strip(),
                "category": self.category.get_text().strip() or "Other",
                "working_directory": root,
            }
        )

    def present(self) -> None:
        self.dialog.present()


class CommandSuggestionsDialog:
    def __init__(
        self,
        parent: Gtk.Window,
        suggestions: Sequence[CommandSuggestion],
        *,
        on_add: Callable[[CommandSuggestion], None],
    ):
        self.dialog = Gtk.Dialog(
            title="Project command suggestions",
            transient_for=parent,
            modal=True,
        )
        self.dialog.set_default_size(680, 540)
        box = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL,
            spacing=10,
            margin_top=18,
            margin_bottom=18,
            margin_start=18,
            margin_end=18,
        )
        self.dialog.get_content_area().append(box)
        box.append(make_label("COMMAND SUGGESTIONS", "dialog-title"))
        box.append(
            make_label(
                "Read-only discovery found these candidates. Nothing is saved or run until you choose Add.",
                "muted",
                wrap=True,
            )
        )
        listing = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
        listing.add_css_class("boxed-list")
        if not suggestions:
            listing.append(
                make_label("No supported command sources were found.", "muted")
            )
        for suggestion in suggestions:
            row = Gtk.Box(
                orientation=Gtk.Orientation.HORIZONTAL,
                spacing=8,
                margin_top=7,
                margin_bottom=7,
                margin_start=8,
                margin_end=8,
            )
            labels = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
            labels.set_hexpand(True)
            labels.append(
                make_label(
                    f"{suggestion.category} · {suggestion.name}", "thread-label"
                )
            )
            labels.append(make_label(suggestion.command, "monospace"))
            labels.append(
                make_label(
                    f"{suggestion.description} · {suggestion.source}",
                    "muted",
                    wrap=True,
                )
            )
            row.append(labels)
            add = Gtk.Button(label="Add")
            add.connect(
                "clicked",
                lambda _button, item=suggestion: on_add(item),
            )
            row.append(add)
            listing.append(row)
        scroll = Gtk.ScrolledWindow(
            child=listing,
            vexpand=True,
            hscrollbar_policy=Gtk.PolicyType.NEVER,
        )
        box.append(scroll)
        self.dialog.add_button("Close", Gtk.ResponseType.CLOSE)
        self.dialog.connect("response", lambda *_args: self.dialog.close())

    def present(self) -> None:
        self.dialog.present()
