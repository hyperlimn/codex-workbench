from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import GLib, Gtk  # noqa: E402

from ..terminal import EmbeddedShellSpec

try:
    gi.require_version("Vte", "3.91")
    from gi.repository import Vte  # type: ignore[attr-defined]  # noqa: E402
except (ImportError, ValueError):
    Vte = None


@dataclass(frozen=True)
class EmbeddedTerminalCapability:
    available: bool
    backend: str
    detail: str


class VteTerminalSession:
    def __init__(
        self,
        terminal: object,
        spec: EmbeddedShellSpec,
        *,
        on_exit: Callable[[], None] | None = None,
    ):
        self.terminal = terminal
        self.spec = spec
        self.on_exit = on_exit
        self.closed = False
        self.exited = False
        terminal.connect("child-exited", self._child_exited)

    @property
    def widget(self) -> Gtk.Widget:
        return self.terminal

    def focus(self) -> None:
        self.terminal.grab_focus()

    @property
    def alive(self) -> bool:
        return not self.closed and not self.exited

    def paste_text(self, text: str) -> None:
        if not self.closed and text:
            self.terminal.paste_text(text)

    def send_command(self, command: str) -> None:
        if self.closed:
            return
        payload = f"{command.rstrip()}\n"
        self.feed_input(payload.encode("utf-8"))

    def feed_input(self, payload: bytes) -> None:
        """Write bytes to this session without exposing terminal contents."""

        if self.closed or not payload:
            return
        try:
            self.terminal.feed_child(payload)
        except TypeError:
            self.terminal.feed_child(list(payload))

    def _child_exited(self, *_args: object) -> None:
        self.exited = True
        if self.on_exit and not self.closed:
            self.on_exit()

    def close(self) -> None:
        if self.closed:
            return
        self.closed = True
        try:
            pty = self.terminal.get_pty()
            if pty is not None:
                pty.close()
        except (AttributeError, GLib.Error):
            pass


class VteTerminalBackend:
    """GTK4 VTE adapter; the core never imports this module."""

    @property
    def available(self) -> bool:
        return Vte is not None

    @property
    def unavailable_reason(self) -> str:
        return (
            ""
            if self.available
            else (
                "GTK4 VTE introspection (gir1.2-vte-3.91) is not installed. "
                "Embedded shell is disabled; external terminal launch remains available."
            )
        )

    @property
    def capability(self) -> EmbeddedTerminalCapability:
        return EmbeddedTerminalCapability(
            self.available,
            "vte-3.91",
            (
                "PTY-backed GTK4 VTE terminal"
                if self.available
                else self.unavailable_reason
            ),
        )

    def create(
        self,
        spec: EmbeddedShellSpec,
        *,
        on_exit: Callable[[], None] | None = None,
    ) -> VteTerminalSession:
        if Vte is None:
            raise RuntimeError(self.unavailable_reason)
        terminal = Vte.Terminal()
        terminal.set_hexpand(True)
        terminal.set_vexpand(True)
        terminal.set_scrollback_lines(10_000)
        for method_name, value in (
            ("set_scroll_on_output", False),
            ("set_scroll_on_keystroke", True),
            ("set_mouse_autohide", True),
            ("set_enable_fallback_scrolling", True),
        ):
            method = getattr(terminal, method_name, None)
            if callable(method):
                method(value)
        add_controller = getattr(terminal, "add_controller", None)
        if callable(add_controller):
            paste_click = Gtk.GestureClick.new()
            paste_click.set_button(3)
            paste_click.connect(
                "pressed",
                lambda gesture, *_args: (
                    terminal.paste_clipboard(),
                    gesture.set_state(Gtk.EventSequenceState.CLAIMED),
                ),
            )
            add_controller(paste_click)
        argv = list(spec.argv)
        envv = list(spec.environment) if spec.environment is not None else None
        # The callback is intentionally optional: child-exited is the durable
        # lifecycle signal and avoids binding application state to a PID.
        try:
            terminal.spawn_async(
                Vte.PtyFlags.DEFAULT,
                str(spec.cwd),
                argv,
                envv,
                GLib.SpawnFlags.DEFAULT,
                None,
                None,
                -1,
                None,
                None,
                None,
            )
        except TypeError:
            # Older 3.91 introspection builds omit child_setup_data.
            terminal.spawn_async(
                Vte.PtyFlags.DEFAULT,
                str(spec.cwd),
                argv,
                envv,
                GLib.SpawnFlags.DEFAULT,
                None,
                -1,
                None,
                None,
                None,
            )
        return VteTerminalSession(terminal, spec, on_exit=on_exit)
