from __future__ import annotations

from typing import Callable, Protocol

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gtk  # noqa: E402


class ChooserBackend(Protocol):
    """GUI boundary for toolkit/platform-native chooser construction."""

    def folder_dialog(
        self,
        *,
        title: str,
        accept_label: str,
        modal: bool = True,
    ) -> object:
        ...

    def open_file_dialog(
        self,
        *,
        parent: Gtk.Window,
        title: str,
        accept_label: str,
    ) -> Gtk.FileChooserNative:
        ...


class GtkChooserBackend:
    """Linux chooser backend using GTK's portal-aware APIs."""

    def __init__(
        self,
        *,
        folder_factory: Callable[..., object] | None = None,
        file_factory: Callable[..., Gtk.FileChooserNative] | None = None,
    ):
        self.folder_factory = folder_factory or Gtk.FileDialog
        self.file_factory = file_factory or Gtk.FileChooserNative

    def folder_dialog(
        self,
        *,
        title: str,
        accept_label: str,
        modal: bool = True,
    ) -> object:
        return self.folder_factory(
            title=title,
            modal=modal,
            accept_label=accept_label,
        )

    def open_file_dialog(
        self,
        *,
        parent: Gtk.Window,
        title: str,
        accept_label: str,
    ) -> Gtk.FileChooserNative:
        return self.file_factory(
            title=title,
            transient_for=parent,
            action=Gtk.FileChooserAction.OPEN,
            accept_label=accept_label,
            cancel_label="Cancel",
        )
