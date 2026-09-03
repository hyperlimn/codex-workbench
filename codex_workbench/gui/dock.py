from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import GLib, Gtk  # noqa: E402

from ..models import Project
from ..workspace import SplitLayout, WorkspacePane
from ..workspace_runtime import WorkspaceRuntimeRegistry
from .terminal import VteTerminalBackend
from .widgets import clear, icon_button, make_label
from .workspace import (
    PaneSurface,
    ProviderContext,
    ProviderRegistry,
    TerminalSurface,
    resolve_pane_path,
)


def detach(widget: Gtk.Widget) -> None:
    parent = widget.get_parent()
    if parent is None:
        return
    if isinstance(parent, Gtk.Box):
        parent.remove(widget)
    elif isinstance(parent, Gtk.Paned):
        if parent.get_start_child() is widget:
            parent.set_start_child(None)
        elif parent.get_end_child() is widget:
            parent.set_end_child(None)
    elif isinstance(parent, Gtk.Window):
        parent.set_child(None)
    else:
        widget.unparent()


@dataclass
class PaneRuntime:
    project_id: str
    pane_id: str
    surface: PaneSurface
    frame: Gtk.Box
    title: Gtk.Label
    window: Gtk.Window | None = None
    closing: bool = False

    def close(self) -> None:
        self.closing = True
        if self.window is not None:
            self.window.set_child(None)
            self.window.destroy()
            self.window = None
        self.surface.close()
        detach(self.frame)


class WorkspaceDock(Gtk.Box):
    """Project-specific live panes rendered from one persistent split tree."""

    def __init__(
        self,
        *,
        parent: Gtk.Window,
        controller: Any,
        terminal: VteTerminalBackend,
        copy_text: Callable[[str], None],
        open_url: Callable[[str], None],
        open_folder: Callable[[Path], None],
        report: Callable[[str], None],
        focus_changed: Callable[[bool], None],
        workspace_changed: Callable[[], None],
    ):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self.add_css_class("workspace-dock")
        self.parent_window = parent
        self.controller = controller
        self.terminal = terminal
        self.copy_text = copy_text
        self.open_url = open_url
        self.open_folder = open_folder
        self.report = report
        self.focus_changed = focus_changed
        self.workspace_changed = workspace_changed
        self.providers = ProviderRegistry()
        self.runtimes = WorkspaceRuntimeRegistry()
        self.project: Project | None = None
        self.active_pane_id = ""
        self._layout_save_source = 0

        header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        header.add_css_class("workspace-dock-header")
        self.heading = make_label("WORKSPACE", "section-title")
        self.heading.set_hexpand(True)
        header.append(self.heading)
        self.add_button = Gtk.MenuButton(label="+ PANE")
        self.add_button.add_css_class("pane-add")
        self.add_button.set_popover(self._new_pane_popover())
        header.append(self.add_button)
        self.append(header)
        self.content = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL,
            spacing=0,
            vexpand=True,
        )
        self.content.set_size_request(-1, 380)
        self.append(self.content)

    def _new_pane_popover(self) -> Gtk.Popover:
        popover = Gtk.Popover()
        box = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL,
            spacing=2,
            margin_top=6,
            margin_bottom=6,
            margin_start=6,
            margin_end=6,
        )
        for provider_type, label in (
            ("codex", "Codex"),
            ("terminal", "Terminal"),
            ("browser", "Browser"),
            ("files", "Files"),
        ):
            button = Gtk.Button(label=label)
            button.add_css_class("flat")
            available, reason = self.providers.capability(provider_type)
            if provider_type == "browser" and not available:
                button.set_sensitive(False)
                button.set_tooltip_text(reason)
            button.connect(
                "clicked",
                lambda _button, kind=provider_type, owner=popover: (
                    owner.popdown(),
                    self.open_codex(new=True) if kind == "codex" else self.add_pane(kind),
                ),
            )
            box.append(button)
        popover.set_child(box)
        return popover

    @property
    def project_id(self) -> str:
        return self.project.registry_id if self.project else ""

    def show_project(self, project: Project) -> None:
        if self._layout_save_source:
            GLib.source_remove(self._layout_save_source)
            self._save_layout()
        self.project = project
        self.runtimes.retain(
            project.registry_id,
            {pane.id for pane in project.workspace.panes},
            close=lambda runtime: runtime.close(),
        )
        self._render()
        for pane in project.workspace.panes:
            if not pane.docked:
                self._show_undocked(pane.id, project.registry_id)

    def _reload_project(self) -> Project:
        if self.project is None:
            raise RuntimeError("No project workspace is selected")
        self.project = self.controller.workbench.project(self.project.registry_id)
        return self.project

    def _render(self) -> None:
        clear(self.content)
        if self.project is None:
            return
        workspace = self.project.workspace
        self.heading.set_text(
            f"WORKSPACE · {len(workspace.panes)} PANE"
            f"{'S' if len(workspace.panes) != 1 else ''}"
        )
        self.focus_changed(bool(workspace.focused_pane_id))
        node = workspace.layout
        if workspace.focused_pane_id:
            node = SplitLayout.pane(workspace.focused_pane_id)
        if node is None:
            empty = Gtk.Box(
                orientation=Gtk.Orientation.VERTICAL,
                spacing=8,
                halign=Gtk.Align.CENTER,
                valign=Gtk.Align.CENTER,
                vexpand=True,
            )
            empty.append(make_label("No workspace panes", "pane-empty-title"))
            empty.append(
                make_label(
                    "Use + PANE or CODEX to restore this project's working environment.",
                    "muted",
                )
            )
            self.content.append(empty)
            return
        for runtime in self.runtimes.project(self.project.registry_id):
            if runtime.window is None:
                detach(runtime.frame)
        self.content.append(self._build_layout(node))

    def _build_layout(self, node: SplitLayout) -> Gtk.Widget:
        if node.leaf:
            return self._runtime(node.pane_id).frame
        orientation = (
            Gtk.Orientation.HORIZONTAL
            if node.orientation == "horizontal"
            else Gtk.Orientation.VERTICAL
        )
        split = Gtk.Paned(orientation=orientation, wide_handle=True)
        split.add_css_class("workspace-split")
        split.set_resize_start_child(True)
        split.set_resize_end_child(True)
        split.set_shrink_start_child(False)
        split.set_shrink_end_child(False)
        if node.first is not None:
            split.set_start_child(self._build_layout(node.first))
        if node.second is not None:
            split.set_end_child(self._build_layout(node.second))

        def restore_position(*_args: object) -> bool:
            total = split.get_width() if orientation == Gtk.Orientation.HORIZONTAL else split.get_height()
            if total > 0:
                split.set_position(round(total * node.ratio))
            return GLib.SOURCE_REMOVE

        split.connect("map", lambda *_args: GLib.idle_add(restore_position))

        def position_changed(*_args: object) -> None:
            total = split.get_width() if orientation == Gtk.Orientation.HORIZONTAL else split.get_height()
            if total > 0:
                node.ratio = max(0.15, min(0.85, split.get_position() / total))
                self._schedule_layout_save()

        split.connect("notify::position", position_changed)
        return split

    def _schedule_layout_save(self) -> None:
        if self._layout_save_source:
            GLib.source_remove(self._layout_save_source)
        self._layout_save_source = GLib.timeout_add(300, self._save_layout)

    def _save_layout(self) -> bool:
        self._layout_save_source = 0
        if self.project is not None:
            self.controller.save_workspace_layout(
                self.project.registry_id, self.project.workspace.layout
            )
        return GLib.SOURCE_REMOVE

    def _runtime(self, pane_id: str) -> PaneRuntime:
        if self.project is None:
            raise RuntimeError("No project workspace is selected")
        project_id = self.project.registry_id
        runtime = self.runtimes.get(project_id, pane_id)
        if runtime is not None:
            runtime.title.set_text(self.project.workspace.pane(pane_id).title)
            return runtime
        pane = self.project.workspace.pane(pane_id)
        if pane is None:
            raise ValueError(f"Unknown workspace pane: {pane_id}")
        frame = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        frame.add_css_class("workspace-pane")
        frame.set_size_request(250, 210)
        header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        header.add_css_class("workspace-pane-header")
        provider_label = make_label(pane.provider_type.upper(), "pane-provider")
        title = make_label(pane.title, "pane-title")
        title.set_hexpand(True)
        title.set_ellipsize(3)
        header.append(provider_label)
        header.append(title)
        menu = Gtk.MenuButton(icon_name="open-menu-symbolic")
        menu.add_css_class("flat")
        menu.set_popover(self._pane_popover(pane.id, project_id))
        header.append(menu)
        frame.append(header)
        try:
            provider = self.providers.get(pane.provider_type)
            context = ProviderContext(
                project=self.project,
                pane=pane,
                terminal=self.terminal,
                codex_command=lambda account, prompt: self.controller.workbench.codex.command(
                    account, initial_prompt=prompt
                ),
                state_changed=lambda state, identity=pane.id, project=project_id: self._state_changed(
                    project, identity, state
                ),
                copy_text=self.copy_text,
                open_url=self.open_url,
                open_folder=self.open_folder,
                shell_here=lambda path, project=project_id: self.shell_here(
                    path, project
                ),
                report_error=self.report,
            )
            surface = provider.restore(context)
        except Exception as error:
            self.report(f"Could not restore {pane.title}: {error}")
            from .workspace import UnavailableSurface

            surface = UnavailableSurface(
                f"{pane.title} unavailable", str(error), pane.provider_state
            )
        frame.append(surface.widget)
        runtime = PaneRuntime(project_id, pane.id, surface, frame, title)
        self.runtimes.put(project_id, pane.id, runtime)
        click = Gtk.GestureClick.new()
        click.set_button(1)
        click.connect(
            "pressed",
            lambda *_args, identity=pane.id, project=project_id: self._activate(
                identity, project
            ),
        )
        header.add_controller(click)
        return runtime

    def _pane_popover(
        self, pane_id: str, project_id: str
    ) -> Gtk.Popover:
        popover = Gtk.Popover()
        box = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL,
            spacing=2,
            margin_top=5,
            margin_bottom=5,
            margin_start=5,
            margin_end=5,
        )
        actions = (
            ("Focus / restore", lambda: self.toggle_focus(pane_id, project_id)),
            ("Dock / undock", lambda: self.toggle_dock(pane_id, project_id)),
            ("Move left", lambda: self.move(pane_id, "left", project_id)),
            ("Move right", lambda: self.move(pane_id, "right", project_id)),
            ("Move above", lambda: self.move(pane_id, "above", project_id)),
            ("Move below", lambda: self.move(pane_id, "below", project_id)),
            ("Close pane", lambda: self.close_pane(pane_id, project_id)),
        )
        for label, callback in actions:
            button = Gtk.Button(label=label)
            button.add_css_class("flat")
            if label == "Close pane":
                button.add_css_class("destructive-action")
            button.connect(
                "clicked",
                lambda _button, cb=callback, owner=popover: (
                    owner.popdown(),
                    cb(),
                ),
            )
            box.append(button)
        popover.set_child(box)
        return popover

    def _activate(self, pane_id: str, project_id: str = "") -> None:
        if self.project is not None and (
            not project_id or project_id == self.project.registry_id
        ):
            self.active_pane_id = pane_id

    def _state_changed(
        self, project_id: str, pane_id: str, state: dict[str, Any]
    ) -> None:
        try:
            self.controller.update_workspace_pane(
                project_id, pane_id, provider_state=state
            )
        except Exception as error:
            self.report(str(error))

    def add_pane(
        self,
        provider_type: str,
        *,
        state: dict[str, Any] | None = None,
        title: str = "",
    ) -> WorkspacePane | None:
        if self.project is None:
            return None
        pane = self.controller.add_workspace_pane(
            self.project.registry_id,
            provider_type,
            title=title,
            provider_state=state or {},
            anchor_id=self.active_pane_id,
        )
        self._reload_project()
        self._render()
        self.focus_pane(pane.id)
        self.workspace_changed()
        return pane

    def open_codex(self, *, new: bool = False) -> tuple[WorkspacePane, bool] | None:
        if self.project is None:
            return None
        pane, created = self.controller.ensure_codex_pane(
            self.project.registry_id, new=new
        )
        runtime = self.runtimes.get(self.project.registry_id, pane.id)
        if (
            runtime is not None
            and isinstance(runtime.surface, TerminalSurface)
            and not runtime.surface.alive
        ):
            self.runtimes.pop(
                self.project.registry_id,
                pane.id,
                close=lambda value: value.close(),
            )
        self._reload_project()
        self._render()
        if not pane.docked:
            self._show_undocked(pane.id)
        self.focus_pane(pane.id)
        self.workspace_changed()
        return pane, created

    def focus_pane(self, pane_id: str) -> None:
        if self.project is None:
            return
        runtime = self._runtime(pane_id)
        self.active_pane_id = pane_id
        if runtime.window is not None:
            runtime.window.present()
        runtime.surface.focus()

    def toggle_focus(
        self, pane_id: str, project_id: str = ""
    ) -> None:
        if self.project is None:
            return
        if project_id and project_id != self.project.registry_id:
            self.report("Dock this pane before using Focus mode.")
            return
        current = self.project.workspace.focused_pane_id
        self.controller.focus_workspace_pane(
            self.project.registry_id, "" if current == pane_id else pane_id
        )
        self._reload_project()
        self._render()
        self.workspace_changed()
        self.focus_pane(pane_id)

    def toggle_dock(
        self, pane_id: str, project_id: str = ""
    ) -> None:
        if self.project is None:
            return
        target_id = project_id or self.project.registry_id
        project = self.controller.workbench.project(target_id)
        pane = project.workspace.pane(pane_id)
        if pane is None:
            return
        if pane.docked:
            self.undock(pane_id)
        else:
            self.dock(pane_id, target_id)

    def undock(self, pane_id: str) -> None:
        if self.project is None:
            return
        self.controller.set_pane_docked(self.project.registry_id, pane_id, False)
        self._reload_project()
        self._render()
        self._show_undocked(pane_id)
        self.workspace_changed()

    def _show_undocked(
        self, pane_id: str, project_id: str = ""
    ) -> None:
        if self.project is None:
            return
        target_id = project_id or self.project.registry_id
        project = self.controller.workbench.project(target_id)
        pane = project.workspace.pane(pane_id)
        if pane is None or pane.docked:
            return
        runtime = self.runtimes.get(target_id, pane_id)
        if runtime is None:
            if target_id != self.project.registry_id:
                return
            runtime = self._runtime(pane_id)
        if runtime.window is not None:
            runtime.window.present()
            return
        detach(runtime.frame)
        window = Gtk.Window(
            application=self.parent_window.get_application(),
            title=f"{pane.title} · {project.name}",
        )
        window.add_css_class("workbench")
        window.add_css_class("undocked-pane-window")
        window.set_default_size(840, 560)
        window.set_child(runtime.frame)
        runtime.window = window

        def closing(_window: Gtk.Window) -> bool:
            if runtime.closing:
                return False
            self.dock(pane_id, target_id)
            return True

        window.connect("close-request", closing)
        self.providers.get(pane.provider_type).on_undock(runtime.surface)
        window.present()

    def dock(
        self, pane_id: str, project_id: str = ""
    ) -> None:
        if self.project is None:
            return
        target_id = project_id or self.project.registry_id
        runtime = self.runtimes.get(target_id, pane_id)
        if runtime is not None and runtime.window is not None:
            runtime.window.set_child(None)
            runtime.closing = True
            runtime.window.destroy()
            runtime.window = None
            runtime.closing = False
        self.controller.set_pane_docked(target_id, pane_id, True)
        if target_id != self.project.registry_id:
            return
        self._reload_project()
        self._render()
        runtime = self._runtime(pane_id)
        pane = self.project.workspace.pane(pane_id)
        if pane is not None:
            self.providers.get(pane.provider_type).on_dock(runtime.surface)
        self.workspace_changed()
        self.focus_pane(pane_id)

    def move(
        self, pane_id: str, placement: str, project_id: str = ""
    ) -> None:
        if self.project is None:
            return
        if project_id and project_id != self.project.registry_id:
            return
        ids = [item for item in self.project.workspace.layout_ids() if item != pane_id]
        if not ids:
            return
        anchor = ids[0] if placement in {"left", "above"} else ids[-1]
        self.controller.move_workspace_pane(
            self.project.registry_id, pane_id, anchor, placement
        )
        self._reload_project()
        self._render()
        self.workspace_changed()

    def close_pane(
        self, pane_id: str, project_id: str = ""
    ) -> None:
        if self.project is None:
            return
        target_id = project_id or self.project.registry_id
        self.controller.close_workspace_pane(target_id, pane_id)
        self.runtimes.pop(
            target_id,
            pane_id,
            close=lambda runtime: runtime.close(),
        )
        if target_id == self.project.registry_id:
            self._reload_project()
            self._render()
            self.workspace_changed()

    def shell_here(self, path: Path, project_id: str = "") -> None:
        if self.project is None:
            return
        target_id = project_id or self.project.registry_id
        if target_id != self.project.registry_id:
            pane = self.controller.add_workspace_pane(
                target_id,
                "terminal",
                title=f"Terminal · {path.name or 'root'}",
                provider_state={"cwd": str(path)},
            )
            self.report(f"Added {pane.title} to its project workspace")
            return
        self.add_pane(
            "terminal",
            state={"cwd": str(path)},
            title=f"Terminal · {path.name or 'root'}",
        )

    def run_command(self, command: str, cwd: Path) -> bool:
        if self.project is None:
            return False
        pane: WorkspacePane | None = None
        for candidate in self.project.workspace.panes_of_type("terminal"):
            try:
                if resolve_pane_path(self.project, candidate.provider_state) == cwd:
                    pane = candidate
                    break
            except ValueError:
                continue
        if pane is None:
            pane = self.add_pane(
                "terminal",
                state={"cwd": str(cwd)},
                title=f"Terminal · {cwd.name or 'root'}",
            )
        if pane is None:
            return False
        runtime = self._runtime(pane.id)
        if not isinstance(runtime.surface, TerminalSurface):
            self.report("A VTE terminal is required to run project commands.")
            return False
        if not runtime.surface.alive:
            self.close_pane(pane.id)
            pane = self.add_pane(
                "terminal",
                state={"cwd": str(cwd)},
                title=f"Terminal · {cwd.name or 'root'}",
            )
            if pane is None:
                return False
            runtime = self._runtime(pane.id)
            if not isinstance(runtime.surface, TerminalSurface):
                return False
        runtime.surface.send_command(command)
        self.focus_pane(pane.id)
        return True

    def paste_into_active(self, text: str) -> bool:
        if self.project is None or not text:
            return False
        candidates = [self.active_pane_id, self.project.workspace.focused_pane_id]
        candidates.extend(
            pane.id
            for pane in self.project.workspace.panes
            if pane.provider_type in {"codex", "terminal"}
        )
        for pane_id in candidates:
            if not pane_id or self.project.workspace.pane(pane_id) is None:
                continue
            runtime = self._runtime(pane_id)
            if runtime.surface.paste_text(text):
                self.focus_pane(pane_id)
                return True
        return False

    def close_project(self, project_id: str) -> None:
        self.runtimes.retain(
            project_id, set(), close=lambda runtime: runtime.close()
        )

    def shutdown(self) -> None:
        if self._layout_save_source:
            GLib.source_remove(self._layout_save_source)
            self._save_layout()

        def close_runtime(runtime: PaneRuntime) -> None:
            try:
                self.controller.update_workspace_pane(
                    runtime.project_id,
                    runtime.pane_id,
                    provider_state=runtime.surface.serialize_state(),
                )
            except (OSError, ValueError, LookupError):
                pass
            runtime.close()

        self.runtimes.close_all(close_runtime)
