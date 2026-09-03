from __future__ import annotations

import os
from typing import Callable, Mapping

import gi

gi.require_version("Gdk", "4.0")
gi.require_version("GObject", "2.0")
from gi.repository import Gdk, GObject  # noqa: E402

from ..clipboard import (
    ClipboardAttempt,
    ClipboardResult,
    detect_desktop_session,
)


class GdkClipboardService:
    """Main-thread GTK clipboard adapter for the native application."""

    HELPER = "GTK/GDK"

    def __init__(
        self,
        *,
        display_getter: Callable[[], object | None] = Gdk.Display.get_default,
        environ: Mapping[str, str] | None = None,
    ):
        self.display_getter = display_getter
        self.environ = dict(os.environ if environ is None else environ)

    def read_text(
        self, callback: Callable[[str | None, str], None]
    ) -> bool:
        """Read one text snapshot asynchronously on the GTK main thread."""

        try:
            display = self.display_getter()
            clipboard = display.get_clipboard() if display is not None else None
        except Exception as error:
            callback(None, f"display lookup failed: {error}")
            return False
        if clipboard is None:
            callback(None, "no active GTK text clipboard")
            return False

        def completed(source: object, result: object) -> None:
            try:
                value = source.read_text_finish(result)
            except Exception as error:
                callback(None, str(error))
            else:
                callback(None if value is None else str(value), "")

        try:
            clipboard.read_text_async(None, completed)
        except Exception as error:
            callback(None, str(error))
            return False
        return True

    def copy(self, text: str) -> ClipboardResult:
        session_type = detect_desktop_session(self.environ)
        try:
            display = self.display_getter()
        except Exception as error:
            return self._failure(session_type, f"display lookup failed: {error}")
        if display is None:
            return self._failure(session_type, "no active GTK display")

        try:
            clipboard = display.get_clipboard()
            if clipboard is None:
                return self._failure(session_type, "display has no clipboard")

            # Newer PyGObject releases expose the GDK convenience function.
            # Ubuntu 24.04's binding exposes only gdk_clipboard_set_value(),
            # represented as Clipboard.set(GObject.Value).
            set_text = getattr(clipboard, "set_text", None)
            if callable(set_text):
                set_text(text)
            else:
                value = GObject.Value()
                value.init(GObject.TYPE_STRING)
                value.set_string(text)
                clipboard.set(value)
        except Exception as error:
            return self._failure(session_type, str(error))

        return ClipboardResult(True, session_type, self.HELPER)

    @classmethod
    def _failure(cls, session_type: str, error: str) -> ClipboardResult:
        return ClipboardResult(
            False,
            session_type,
            attempts=(ClipboardAttempt(cls.HELPER, error or "copy failed"),),
        )
