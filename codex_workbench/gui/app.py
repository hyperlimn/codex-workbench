from __future__ import annotations

import sys
from importlib import resources
from typing import Sequence

import gi

gi.require_version("Adw", "1")
gi.require_version("Gdk", "4.0")
gi.require_version("Gtk", "4.0")
from gi.repository import Adw, Gdk, Gio, Gtk  # noqa: E402

from .. import __version__
from ..platform import select_platform_backend
from .controller import SwitchboardController
from .terminal import VteTerminalBackend
from .workspace import browser_capability
from .window import SwitchboardWindow


APPLICATION_ID = "io.github.codex_workbench.Workbench"


def _css_text() -> str:
    root = resources.files("codex_workbench.gui.resources")
    return "\n".join(
        root.joinpath(name).read_text(encoding="utf-8")
        for name in ("style.css", "workspace.css")
    )


def install_css() -> None:
    display = Gdk.Display.get_default()
    if display is None:
        return
    provider = Gtk.CssProvider()
    provider.load_from_string(_css_text())
    Gtk.StyleContext.add_provider_for_display(
        display,
        provider,
        Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION,
    )


def check_gui() -> int:
    """Import/resources smoke check that never requires a display server."""

    css = _css_text()
    if not css.strip():
        raise RuntimeError("GUI stylesheet is empty")
    provider = Gtk.CssProvider()
    provider.load_from_string(css)
    platform = select_platform_backend().capabilities()
    vte = VteTerminalBackend().capability
    browser = browser_capability()
    print(
        "Codex Workbench "
        f"{__version__} · GTK {Gtk.get_major_version()}."
        f"{Gtk.get_minor_version()}.{Gtk.get_micro_version()} · "
        f"libadwaita {Adw.get_major_version()}."
        f"{Adw.get_minor_version()}.{Adw.get_micro_version()} · "
        f"VTE {'available' if vte.available else 'unavailable'} · "
        f"WebKit {'available' if browser.available else 'unavailable'}"
    )
    print(
        f"Platform {platform.platform} · "
        f"{'supported' if platform.supported else 'unsupported'} · "
        f"Git {'available' if platform.git else 'unavailable'} · "
        f"external terminal "
        f"{'available' if platform.external_terminal else 'unavailable'}"
    )
    if not vte.available:
        print(vte.detail)
    if not browser.available:
        print(browser.detail)
    return 0


class WorkbenchApplication(Adw.Application):
    def __init__(self):
        super().__init__(
            application_id=APPLICATION_ID,
            flags=Gio.ApplicationFlags.DEFAULT_FLAGS,
        )
        self.window: SwitchboardWindow | None = None
        self.controller: SwitchboardController | None = None

    def do_startup(self) -> None:
        Adw.Application.do_startup(self)
        self.controller = SwitchboardController()
        settings = self.controller.workbench.settings_snapshot()
        Adw.StyleManager.get_default().set_color_scheme(
            Adw.ColorScheme.DEFAULT
            if settings.theme == "system"
            else Adw.ColorScheme.FORCE_DARK
        )
        install_css()

    def do_activate(self) -> None:
        if self.window is None:
            self.window = SwitchboardWindow(self, self.controller)
            self.window.connect("destroy", self._window_destroyed)
        self.window.present()

    def _window_destroyed(self, _window: Gtk.Window) -> None:
        self.window = None


def main(argv: Sequence[str] | None = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    if "--check" in arguments:
        return check_gui()
    if "--version" in arguments:
        print(__version__)
        return 0
    application = WorkbenchApplication()
    return int(application.run([sys.argv[0], *arguments]))


if __name__ == "__main__":
    raise SystemExit(main())
