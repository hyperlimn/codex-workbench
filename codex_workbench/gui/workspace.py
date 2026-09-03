from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Protocol
from urllib.parse import urlparse

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gio, Gtk  # noqa: E402

from ..associated import resolve_project_path
from ..models import Project
from ..terminal import (
    embedded_codex_environment,
    embedded_command_spec,
    embedded_shell_spec,
)
from ..workspace import WorkspacePane
from .browser_runtime import browser_runtime_capability
from .terminal import VteTerminalBackend, VteTerminalSession
from .widgets import clear, icon_button, make_label

try:
    gi.require_version("WebKit", "6.0")
    from gi.repository import WebKit  # type: ignore[attr-defined]  # noqa: E402
except (ImportError, ValueError):
    WebKit = None


@dataclass(frozen=True)
class BrowserCapability:
    available: bool
    backend: str
    detail: str


def browser_capability() -> BrowserCapability:
    available, detail = browser_runtime_capability(WebKit is not None)
    return BrowserCapability(available, "webkitgtk-6.0", detail)

@dataclass
class ProviderContext:
    project: Project
    pane: WorkspacePane
    terminal: VteTerminalBackend
    codex_command: Callable[[str, str], list[str]]
    state_changed: Callable[[dict[str, Any]], None]
    copy_text: Callable[[str], None]
    open_url: Callable[[str], None]
    open_folder: Callable[[Path], None]
    shell_here: Callable[[Path], None]
    report_error: Callable[[str], None]


class PaneSurface(Protocol):
    widget: Gtk.Widget

    def focus(self) -> None:
        ...

    def close(self) -> None:
        ...

    def serialize_state(self) -> dict[str, Any]:
        ...

    def paste_text(self, text: str) -> bool:
        ...


class WorkspacePaneProvider(Protocol):
    provider_type: str

    @property
    def available(self) -> bool:
        ...

    @property
    def unavailable_reason(self) -> str:
        ...

    def create(self, context: ProviderContext) -> PaneSurface:
        ...

    def restore(self, context: ProviderContext) -> PaneSurface:
        ...

    def on_dock(self, surface: PaneSurface) -> None:
        ...

    def on_undock(self, surface: PaneSurface) -> None:
        ...


class BaseSurface:
    def __init__(self, widget: Gtk.Widget, state: dict[str, Any] | None = None):
        self.widget = widget
        self.state = dict(state or {})

    def focus(self) -> None:
        self.widget.grab_focus()

    def close(self) -> None:
        pass

    def serialize_state(self) -> dict[str, Any]:
        return dict(self.state)

    def paste_text(self, _text: str) -> bool:
        return False


class UnavailableSurface(BaseSurface):
    def __init__(self, title: str, detail: str, state: dict[str, Any]):
        content = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL,
            spacing=8,
            halign=Gtk.Align.CENTER,
            valign=Gtk.Align.CENTER,
            margin_top=24,
            margin_bottom=24,
            margin_start=24,
            margin_end=24,
        )
        content.append(make_label(title, "pane-empty-title"))
        content.append(make_label(detail, "muted", wrap=True, xalign=0.5))
        super().__init__(content, state)


def _configured_roots(project: Project) -> tuple[Path, ...]:
    return (project.path, *(item.resolved_path for item in project.associated_paths))


def _inside(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def resolve_pane_path(
    project: Project,
    state: dict[str, Any],
    *,
    require_shell: bool = True,
) -> Path:
    explicit = str(state.get("cwd") or "").strip()
    if explicit:
        path = Path(explicit).expanduser().resolve(strict=False)
        if not any(_inside(path, root) for root in _configured_roots(project)):
            raise ValueError("Pane path must remain inside a configured project root.")
        if not path.is_dir():
            raise ValueError(f"Pane working directory does not exist: {path}")
        return path
    target = str(state.get("working_directory") or "")
    path = resolve_project_path(
        project, target, require_shell=require_shell
    )
    if not path.is_dir():
        raise ValueError(f"Pane working directory does not exist: {path}")
    return path


class TerminalSurface(BaseSurface):
    def __init__(self, session: VteTerminalSession, state: dict[str, Any]):
        super().__init__(session.widget, state)
        self.session = session

    def focus(self) -> None:
        self.session.focus()

    
    def alive(self) -> bool:
        return self.session.alive

    def close(self) -> None:
        self.session.close()

    def paste_text(self, text: str) -> bool:
        self.session.paste_text(text)
        return bool(text)

    def send_command(self, command: str) -> None:
        self.session.send_command(command)


class BaseProvider:
    provider_type = ""

    @property
    def available(self) -> bool:
        return True

    @property
    def unavailable_reason(self) -> str:
        return ""

    def restore(self, context: ProviderContext) -> PaneSurface:
        return self.create(context)

    def on_dock(self, _surface: PaneSurface) -> None:
        pass

    def on_undock(self, _surface: PaneSurface) -> None:
        pass


class TerminalPaneProvider(BaseProvider):
    provider_type = "terminal"

    def create(self, context: ProviderContext) -> PaneSurface:
        if not context.terminal.available:
            return UnavailableSurface(
                "Terminal unavailable",
                context.terminal.unavailable_reason,
                context.pane.provider_state,
            )
        cwd = resolve_pane_path(context.project, context.pane.provider_state)
        spec = embedded_shell_spec(cwd)
        session = context.terminal.create(
            spec,
            on_exit=lambda: context.state_changed({"exited": True}),
        )
        return TerminalSurface(session, context.pane.provider_state)


class CodexPaneProvider(BaseProvider):
    provider_type = "codex"

    def create(self, context: ProviderContext) -> PaneSurface:
        if not context.terminal.available:
            return UnavailableSurface(
                "Codex terminal unavailable",
                context.terminal.unavailable_reason,
                context.pane.provider_state,
            )
        state = context.pane.provider_state
        account = str(state.get("account") or "")
        if not account:
            return UnavailableSurface(
                "Codex account required",
                "Select a Codex account, then open a new Codex pane.",
                state,
            )
        cwd = resolve_pane_path(context.project, state)
        prompt = str(state.get("initial_prompt") or "")
        spec = embedded_command_spec(
            cwd,
            context.codex_command(account, prompt),
            environment=embedded_codex_environment(),
        )
        session = context.terminal.create(
            spec,
            on_exit=lambda: context.state_changed({"exited": True}),
        )
        return TerminalSurface(session, state)


class BrowserSurface(BaseSurface):
    def __init__(self, context: ProviderContext):
        state = dict(context.pane.provider_state)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        toolbar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        toolbar.add_css_class("pane-toolbar")
        back = icon_button("go-previous-symbolic", "Back")
        forward = icon_button("go-next-symbolic", "Forward")
        reload_button = icon_button("view-refresh-symbolic", "Reload")
        address = Gtk.Entry(hexpand=True)
        external = icon_button("external-link-symbolic", "Open externally")
        toolbar.append(back)
        toolbar.append(forward)
        toolbar.append(reload_button)
        toolbar.append(address)
        toolbar.append(external)
        box.append(toolbar)
        webview = WebKit.WebView()
        webview.set_hexpand(True)
        webview.set_vexpand(True)
        box.append(webview)
        super().__init__(box, state)
        self.webview = webview
        self.address = address
        self.context = context

        def load(value: str) -> None:
            url = value.strip()
            if not url:
                return
            parsed = urlparse(url)
            if not parsed.scheme:
                url = f"http://{url}"
            parsed = urlparse(url)
            if parsed.scheme not in {"http", "https"} or not parsed.netloc:
                context.report_error("Browser panes support http/https URLs.")
                return
            webview.load_uri(url)

        back.connect("clicked", lambda *_args: webview.go_back())
        forward.connect("clicked", lambda *_args: webview.go_forward())
        reload_button.connect("clicked", lambda *_args: webview.reload())
        address.connect("activate", lambda entry: load(entry.get_text()))
        external.connect(
            "clicked", lambda *_args: context.open_url(webview.get_uri() or "")
        )

        def uri_changed(*_args: object) -> None:
            uri = webview.get_uri() or ""
            address.set_text(uri)
            self.state["url"] = uri
            context.state_changed({"url": uri})
            back.set_sensitive(webview.can_go_back())
            forward.set_sensitive(webview.can_go_forward())

        webview.connect("notify::uri", uri_changed)
        load(str(state.get("url") or "http://localhost:3000"))

    def focus(self) -> None:
        self.webview.grab_focus()

    def serialize_state(self) -> dict[str, Any]:
        self.state["url"] = self.webview.get_uri() or self.state.get("url", "")
        return dict(self.state)


class BrowserPaneProvider(BaseProvider):
    provider_type = "browser"

    @property
    def available(self) -> bool:
        return browser_capability().available

    @property
    def unavailable_reason(self) -> str:
        capability = browser_capability()
        return "" if capability.available else capability.detail

    def create(self, context: ProviderContext) -> PaneSurface:
        if not self.available:
            return UnavailableSurface(
                "Browser unavailable",
                self.unavailable_reason,
                context.pane.provider_state,
            )
        return BrowserSurface(context)


class FileRow(Gtk.ListBoxRow):
    def __init__(self, path: Path):
        super().__init__()
        self.path = path
        row = Gtk.Box(
            orientation=Gtk.Orientation.HORIZONTAL,
            spacing=8,
            margin_top=6,
            margin_bottom=6,
            margin_start=8,
            margin_end=8,
        )
        row.append(
            Gtk.Image.new_from_icon_name(
                "folder-symbolic" if path.is_dir() else "text-x-generic-symbolic"
            )
        )
        label = make_label(path.name, "file-name")
        label.set_hexpand(True)
        label.set_ellipsize(3)
        row.append(label)
        self.set_child(row)


class FilesSurface(BaseSurface):
    def __init__(self, context: ProviderContext):
        state = dict(context.pane.provider_state)
        root = resolve_pane_path(
            context.project, state, require_shell=False
        )
        relative = str(state.get("path") or "")
        current = (root / relative).resolve(strict=False)
        if not _inside(current, root) or not current.is_dir():
            current = root

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        toolbar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        toolbar.add_css_class("pane-toolbar")
        up = icon_button("go-up-symbolic", "Parent folder")
        root_names = ["Root", *(item.label for item in context.project.associated_paths)]
        root_selector = Gtk.DropDown.new_from_strings(root_names)
        selected_root = str(state.get("working_directory") or "")
        if selected_root in root_names:
            root_selector.set_selected(root_names.index(selected_root))
        path_label = make_label("", "files-path", selectable=True)
        path_label.set_hexpand(True)
        path_label.set_ellipsize(1)
        copy = icon_button("edit-copy-symbolic", "Copy selected path")
        open_button = icon_button("document-open-symbolic", "Open selected item")
        reveal = icon_button("folder-open-symbolic", "Reveal in system file manager")
        shell = icon_button("utilities-terminal-symbolic", "Open terminal here")
        for item in (
            up,
            root_selector,
            path_label,
            copy,
            open_button,
            reveal,
            shell,
        ):
            toolbar.append(item)
        box.append(toolbar)
        listing = Gtk.ListBox(
            selection_mode=Gtk.SelectionMode.SINGLE,
            activate_on_single_click=False,
        )
        listing.add_css_class("files-list")
        scroll = Gtk.ScrolledWindow(
            child=listing,
            hscrollbar_policy=Gtk.PolicyType.NEVER,
            vscrollbar_policy=Gtk.PolicyType.AUTOMATIC,
            vexpand=True,
        )
        box.append(scroll)
        super().__init__(box, state)
        self.root = root
        self.current = current
        self.listing = listing
        self.path_label = path_label
        self.root_selector = root_selector
        self.context = context

        up.connect("clicked", lambda *_args: self.navigate(self.current.parent))
        root_selector.connect("notify::selected", self._root_changed)
        listing.connect("row-activated", self._activate)
        copy.connect("clicked", lambda *_args: self._copy_selected())
        open_button.connect("clicked", lambda *_args: self._open_selected())
        reveal.connect("clicked", lambda *_args: context.open_folder(self.current))
        shell.connect("clicked", lambda *_args: context.shell_here(self.current))
        self.navigate(current)

    def _root_changed(self, selector: Gtk.DropDown, *_args: object) -> None:
        index = selector.get_selected()
        if index == 0:
            root = self.context.project.path
            label = ""
        else:
            associated = self.context.project.associated_paths[index - 1]
            root = associated.resolved_path
            label = associated.label
        if not root.is_dir():
            self.context.report_error(f"Project root does not exist: {root}")
            return
        self.root = root
        self.state.pop("cwd", None)
        self.state["working_directory"] = label
        self.state["path"] = ""
        self.context.state_changed(
            {"cwd": "", "working_directory": label, "path": ""}
        )
        self.navigate(root)

    def _selected_path(self) -> Path:
        row = self.listing.get_selected_row()
        return getattr(row, "path", self.current)

    def navigate(self, path: Path) -> None:
        target = path.resolve(strict=False)
        if not _inside(target, self.root) or not target.is_dir():
            return
        self.current = target
        clear(self.listing)
        try:
            entries = sorted(
                target.iterdir(), key=lambda item: (not item.is_dir(), item.name.casefold())
            )
        except OSError as error:
            self.context.report_error(str(error))
            entries = []
        for path in entries:
            self.listing.append(FileRow(path))
        relative = str(target.relative_to(self.root))
        relative = "" if relative == "." else relative
        self.state["path"] = relative
        self.path_label.set_text(str(target))
        self.context.state_changed({"path": relative})

    def _activate(self, _listing: Gtk.ListBox, row: Gtk.ListBoxRow) -> None:
        path = getattr(row, "path", self.current)
        if path.is_dir():
            self.navigate(path)
        else:
            self._open_path(path)

    def _copy_selected(self) -> None:
        self.context.copy_text(str(self._selected_path()))

    def _open_selected(self) -> None:
        path = self._selected_path()
        if path.is_dir():
            self.navigate(path)
        else:
            self._open_path(path)

    def _open_path(self, path: Path) -> None:
        try:
            Gio.AppInfo.launch_default_for_uri(path.as_uri(), None)
        except Exception as error:
            self.context.report_error(str(error))

    def focus(self) -> None:
        self.listing.grab_focus()


class FilesPaneProvider(BaseProvider):
    provider_type = "files"

    def create(self, context: ProviderContext) -> PaneSurface:
        return FilesSurface(context)


class ProviderRegistry:
    def __init__(self):
        providers: tuple[WorkspacePaneProvider, ...] = (
            CodexPaneProvider(),
            TerminalPaneProvider(),
            BrowserPaneProvider(),
            FilesPaneProvider(),
        )
        self._providers = {item.provider_type: item for item in providers}

    def get(self, provider_type: str) -> WorkspacePaneProvider:
        try:
            return self._providers[provider_type]
        except KeyError as error:
            raise ValueError(f"Unknown workspace pane provider: {provider_type}") from error

    def capability(self, provider_type: str) -> tuple[bool, str]:
        provider = self.get(provider_type)
        return provider.available, provider.unavailable_reason
