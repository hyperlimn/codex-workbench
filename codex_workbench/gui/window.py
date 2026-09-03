from __future__ import annotations

from concurrent.futures import Future, ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
from typing import Callable

import gi

gi.require_version("Adw", "1")
gi.require_version("Gtk", "4.0")
from gi.repository import Adw, Gio, GLib, Gtk  # noqa: E402

from .. import __version__
from ..clipboard import ClipboardResult
from ..terminal import (
    select_shell_backend,
)
from .clipboard import GdkClipboardService
from .controller import SwitchboardController
from .dock import WorkspaceDock
from .project_panels import ProjectPanelsMixin
from .dialogs import (
    AddProjectDialog,
    CommandPalette,
    CommitDialog,
    HandoffDialog,
    ProjectDialog,
    PushDialog,
    RemoveProjectDialog,
    ResumeDialog,
    SessionDialog,
    SettingsDialog,
    ShellDialog,
    ThreadDialog,
    show_copy_fallback,
    show_handoff_summary,
    show_preflight,
    show_status,
)
from .terminal import VteTerminalBackend
from .state import AccountItem, PaletteCommand, ProjectItem, WorkspaceState
from .widgets import (
    AccountChip,
    ActionButton,
    ConfidenceCell,
    ProjectRow,
    SectionHeader,
    clear,
    icon_button,
    make_label,
)


class AsyncRunner:
    def __init__(self, on_error: Callable[[str, BaseException], None]):
        self.executor = ThreadPoolExecutor(
            max_workers=4,
            thread_name_prefix="cwb-gui",
        )
        self.on_error = on_error
        self.closed = False

    def submit(
        self,
        label: str,
        task: Callable[[], object],
        on_success: Callable[[object], None],
        on_error: Callable[[BaseException], None] | None = None,
    ) -> None:
        if self.closed:
            return
        future = self.executor.submit(task)
        future.add_done_callback(
            lambda completed: GLib.idle_add(
                self._deliver,
                label,
                completed,
                on_success,
                on_error,
            )
        )

    def _deliver(
        self,
        label: str,
        future: Future[object],
        on_success: Callable[[object], None],
        on_error: Callable[[BaseException], None] | None,
    ) -> bool:
        if self.closed:
            return GLib.SOURCE_REMOVE
        try:
            result = future.result()
        except BaseException as error:
            if on_error:
                on_error(error)
            else:
                self.on_error(label, error)
        else:
            on_success(result)
        return GLib.SOURCE_REMOVE

    def close(self) -> None:
        self.closed = True
        self.executor.shutdown(wait=False, cancel_futures=True)


class SwitchboardWindow(ProjectPanelsMixin, Adw.ApplicationWindow):
    ACTIONS = (
        (
            "codex",
            "CODEX",
            "system-run-symbolic",
            "Open Codex in this project · Ctrl+Enter",
            True,
        ),
        (
            "shell",
            "SHELL",
            "utilities-terminal-symbolic",
            "Open an embedded or external shell here · Ctrl+Shift+Enter",
            False,
        ),
        (
            "ready",
            "READY",
            "emblem-ok-symbolic",
            "Run context preflight · Ctrl+Shift+R",
            False,
        ),
        (
            "status",
            "STATUS",
            "dialog-information-symbolic",
            "Show full workspace status",
            False,
        ),
        (
            "commit",
            "COMMIT",
            "document-save-symbolic",
            "Review files and create a guarded commit",
            False,
        ),
        (
            "push",
            "PUSH",
            "send-to-symbolic",
            "Preview destination, then push",
            False,
        ),
        (
            "copy",
            "COPY ALL",
            "edit-copy-symbolic",
            "Copy transferable context · Ctrl+Shift+C",
            False,
        ),
        (
            "handoff",
            "HANDOFF",
            "mail-forward-symbolic",
            "Transfer this session to another account · Ctrl+Shift+H",
            True,
        ),
        (
            "resume",
            "RESUME",
            "media-playback-start-symbolic",
            "Review and resume the stored session",
            False,
        ),
    )

    def __init__(
        self,
        application: Adw.Application,
        controller: SwitchboardController | None = None,
    ):
        super().__init__(application=application)
        self.controller = controller or SwitchboardController()
        self.native_clipboard = GdkClipboardService()
        self.runner = AsyncRunner(self._async_error)
        self.selected_project = ""
        self.workspace: WorkspaceState | None = None
        self.accounts: tuple[AccountItem, ...] = ()
        self.project_items: tuple[ProjectItem, ...] = ()
        self.embedded_terminal = VteTerminalBackend()
        self._selecting_row = False
        self._workspace_generation = 0

        self.set_title("Codex Workbench")
        self.set_default_size(1280, 820)
        self.set_size_request(960, 620)
        self.add_css_class("workbench")
        self.connect("close-request", self._close_request)

        self.toast_overlay = Adw.ToastOverlay()
        self.set_content(self.toast_overlay)
        self.root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.toast_overlay.set_child(self.root)

        self._build_header()
        self._build_account_strip()
        self._build_main()
        self._install_actions()

        self.stack.set_visible_child_name("loading")
        self.present()
        self.refresh_projects()

    def _build_header(self) -> None:
        header = Adw.HeaderBar()
        header.add_css_class("workbench-header")
        title = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        title.append(make_label("CODEX WORKBENCH", "wordmark"))
        title.append(make_label(f"v{__version__}", "version-mark"))
        header.set_title_widget(title)

        palette = icon_button(
            "system-search-symbolic",
            "Command palette · Ctrl+K",
        )
        palette.connect("clicked", lambda *_args: self.open_palette())
        header.pack_start(palette)

        refresh = icon_button(
            "view-refresh-symbolic",
            "Refresh project, Git, and account status",
        )
        refresh.connect("clicked", lambda *_args: self.refresh_all())
        header.pack_end(refresh)
        self.root.append(header)

    def _build_account_strip(self) -> None:
        strip = Gtk.Box(
            orientation=Gtk.Orientation.HORIZONTAL,
            spacing=10,
        )
        strip.add_css_class("account-strip")
        label = make_label("CODEX ACCOUNTS", "account-strip-label")
        label.set_valign(Gtk.Align.CENTER)
        strip.append(label)
        scroll = Gtk.ScrolledWindow(
            hscrollbar_policy=Gtk.PolicyType.AUTOMATIC,
            vscrollbar_policy=Gtk.PolicyType.NEVER,
            hexpand=True,
        )
        self.account_box = Gtk.Box(
            orientation=Gtk.Orientation.HORIZONTAL,
            spacing=7,
        )
        scroll.set_child(self.account_box)
        strip.append(scroll)
        self.root.append(strip)

    def _build_main(self) -> None:
        paned = Gtk.Paned(
            orientation=Gtk.Orientation.HORIZONTAL,
            position=245,
            wide_handle=False,
            vexpand=True,
        )
        paned.set_start_child(self._build_sidebar())
        paned.set_end_child(self._build_workspace_stack())
        paned.set_shrink_start_child(False)
        self.root.append(paned)

    def _build_sidebar(self) -> Gtk.Widget:
        sidebar = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL,
            spacing=0,
        )
        sidebar.add_css_class("sidebar")

        toolbar = Gtk.Box(
            orientation=Gtk.Orientation.HORIZONTAL,
            spacing=6,
        )
        toolbar.add_css_class("sidebar-toolbar")
        title = make_label("PROJECTS", "eyebrow")
        title.set_hexpand(True)
        toolbar.append(title)
        add = icon_button("list-add-symbolic", "Add project")
        add.connect("clicked", lambda *_args: self.action_add_project())
        toolbar.append(add)
        sidebar.append(toolbar)

        self.project_list = Gtk.ListBox(
            selection_mode=Gtk.SelectionMode.SINGLE,
            activate_on_single_click=True,
        )
        self.project_list.add_css_class("project-list")
        self.project_list.connect("row-selected", self._project_row_selected)
        project_scroll = Gtk.ScrolledWindow(
            child=self.project_list,
            vexpand=True,
            hscrollbar_policy=Gtk.PolicyType.NEVER,
        )
        sidebar.append(project_scroll)

        navigation = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL,
            spacing=2,
        )
        navigation.add_css_class("sidebar-nav")
        for icon, label, tooltip, callback in (
            (
                "view-list-symbolic",
                "Sessions",
                "Start or edit the current Work Session",
                self.action_session,
            ),
            (
                "system-users-symbolic",
                "Codex Accounts",
                "Select an intended account from the top strip",
                self._focus_accounts,
            ),
            (
                "emblem-system-symbolic",
                "Settings",
                "Workbench integration settings",
                self.action_settings,
            ),
        ):
            button = Gtk.Button()
            button.set_tooltip_text(tooltip)
            row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=9)
            row.append(Gtk.Image.new_from_icon_name(icon))
            text = make_label(label)
            text.set_hexpand(True)
            row.append(text)
            button.set_child(row)
            button.add_css_class("flat")
            button.connect("clicked", lambda _button, cb=callback: cb())
            navigation.append(button)
        sidebar.append(navigation)
        return sidebar

    def _build_workspace_stack(self) -> Gtk.Widget:
        self.stack = Gtk.Stack(
            transition_type=Gtk.StackTransitionType.CROSSFADE,
            transition_duration=120,
        )
        self.stack.add_named(self._build_loading(), "loading")
        self.stack.add_named(self._build_empty(), "empty")
        self.stack.add_named(self._build_workspace(), "workspace")
        return self.stack

    def _build_loading(self) -> Gtk.Widget:
        box = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL,
            spacing=12,
            halign=Gtk.Align.CENTER,
            valign=Gtk.Align.CENTER,
        )
        spinner = Gtk.Spinner(spinning=True, width_request=28, height_request=28)
        box.append(spinner)
        box.append(make_label("Reading workspace context…", "muted"))
        return box

    def _build_empty(self) -> Gtk.Widget:
        box = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL,
            spacing=10,
            halign=Gtk.Align.CENTER,
            valign=Gtk.Align.CENTER,
        )
        box.append(make_label("CODEX WORKBENCH", "empty-title"))
        box.append(make_label("No projects yet.", "empty-subtitle"))
        button = Gtk.Button(label="Add Project")
        button.add_css_class("suggested-action")
        button.set_halign(Gtk.Align.CENTER)
        button.connect("clicked", lambda *_args: self.action_add_project())
        box.append(button)
        return box

    def _build_workspace(self) -> Gtk.Widget:
        scroll = Gtk.ScrolledWindow(
            hscrollbar_policy=Gtk.PolicyType.NEVER,
            vscrollbar_policy=Gtk.PolicyType.AUTOMATIC,
        )
        scroll.add_css_class("workspace-scroll")
        workspace = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL,
            spacing=0,
        )
        workspace.add_css_class("workspace")
        scroll.set_child(workspace)
        self.project_top = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL,
            spacing=0,
        )
        workspace.append(self.project_top)

        header = Gtk.Box(
            orientation=Gtk.Orientation.HORIZONTAL,
            spacing=12,
        )
        titles = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL,
            spacing=2,
        )
        titles.set_hexpand(True)
        self.project_name_label = make_label("", "project-name")
        self.project_path_label = make_label(
            "", "project-path", selectable=True
        )
        titles.append(self.project_name_label)
        titles.append(self.project_path_label)
        header.append(titles)
        edit_project = icon_button(
            "document-edit-symbolic",
            "Edit or remove this project",
        )
        edit_project.connect(
            "clicked",
            lambda *_args: self.action_edit_project(),
        )
        header.append(edit_project)
        self.session_chip = make_label("", "session-chip")
        self.session_chip.set_valign(Gtk.Align.CENTER)
        header.append(self.session_chip)
        self.project_top.append(header)

        self.confidence_box = Gtk.FlowBox(
            selection_mode=Gtk.SelectionMode.NONE,
            max_children_per_line=6,
            min_children_per_line=2,
            homogeneous=True,
            column_spacing=0,
            row_spacing=0,
        )
        self.confidence_box.add_css_class("confidence-grid")
        self.project_top.append(self.confidence_box)

        self.action_box = Gtk.FlowBox(
            selection_mode=Gtk.SelectionMode.NONE,
            max_children_per_line=9,
            min_children_per_line=3,
            homogeneous=False,
            column_spacing=6,
            row_spacing=6,
        )
        self.action_box.add_css_class("action-bar")
        self.action_buttons: dict[str, Gtk.Button] = {}
        for key, label, icon, tooltip, important in self.ACTIONS:
            button = ActionButton(
                label,
                icon,
                tooltip,
                important=important,
            )
            button.connect(
                "clicked",
                lambda _button, action=key: self.dispatch(action),
            )
            self.action_buttons[key] = button
            self.action_box.append(button)
        self.project_top.append(self.action_box)

        self.usage_revealer = Gtk.Revealer(
            transition_type=Gtk.RevealerTransitionType.SLIDE_DOWN,
            transition_duration=140,
        )
        warning = Gtk.Box(
            orientation=Gtk.Orientation.HORIZONTAL,
            spacing=10,
        )
        warning.add_css_class("usage-warning")
        self.usage_warning_box = warning
        text = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        text.set_hexpand(True)
        self.usage_title = make_label("", "warning-title")
        self.usage_detail = make_label("", "muted", wrap=True)
        text.append(self.usage_title)
        text.append(self.usage_detail)
        warning.append(text)
        handoff = Gtk.Button(label="Handoff")
        handoff.connect("clicked", lambda *_args: self.action_handoff())
        warning.append(handoff)
        self.usage_revealer.set_child(warning)
        self.project_top.append(self.usage_revealer)

        self.project_top.append(self._build_project_info_section())

        self.workspace_dock = WorkspaceDock(
            parent=self,
            controller=self.controller,
            terminal=self.embedded_terminal,
            copy_text=self._copy_text_and_toast,
            open_url=self._open_external_url,
            open_folder=self._open_folder_path,
            report=self.toast,
            focus_changed=self._workspace_focus_changed,
            workspace_changed=self._workspace_model_changed,
        )
        workspace.append(self.workspace_dock)
        return scroll

    def _panel(self) -> Gtk.Box:
        panel = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL,
            spacing=5,
        )
        panel.add_css_class("panel")
        return panel

    def _build_left_column(self) -> Gtk.Widget:
        column = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL,
            spacing=12,
            margin_end=6,
        )

        memory = self._panel()
        header = SectionHeader("Current objective")
        edit = icon_button("document-edit-symbolic", "Edit session context")
        edit.connect("clicked", lambda *_args: self.action_session())
        header.append(edit)
        memory.append(header)
        self.objective_label = make_label(
            "—", "objective-text", wrap=True, selectable=True
        )
        memory.append(self.objective_label)
        self.completed_label = self._memory_pair(memory, "COMPLETED")
        self.current_state_label = self._memory_pair(memory, "CURRENT STATE")
        self.next_action_label = self._memory_pair(memory, "NEXT")
        self.next_action_label.add_css_class("next-value")
        column.append(memory)

        git_panel = self._panel()
        git_panel.append(SectionHeader("Working tree"))
        self.git_file_list = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL,
            spacing=0,
        )
        git_panel.append(self.git_file_list)
        column.append(git_panel)

        instructions = self._panel()
        instructions.append(SectionHeader("Project instructions"))
        self.instructions_label = make_label(
            "None recorded.",
            "memory-value",
            wrap=True,
            selectable=True,
        )
        instructions.append(self.instructions_label)
        column.append(instructions)
        return column

    def _memory_pair(self, parent: Gtk.Box, title: str) -> Gtk.Label:
        parent.append(make_label(title, "memory-label"))
        value = make_label("—", "memory-value", wrap=True, selectable=True)
        parent.append(value)
        return value

    def _build_right_column(self) -> Gtk.Widget:
        column = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL,
            spacing=12,
            margin_start=6,
        )

        roots = self._panel()
        roots_header = SectionHeader("Project roots")
        edit_roots = icon_button(
            "document-edit-symbolic",
            "Edit canonical and associated paths",
        )
        edit_roots.connect(
            "clicked",
            lambda *_args: self.action_edit_project(),
        )
        roots_header.append(edit_roots)
        roots.append(roots_header)
        self.path_list = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL,
            spacing=0,
        )
        roots.append(self.path_list)
        column.append(roots)

        threads = self._panel()
        thread_header = SectionHeader("ChatGPT threads")
        add_thread = icon_button("list-add-symbolic", "Add ChatGPT thread")
        add_thread.connect("clicked", lambda *_args: self.action_add_thread())
        thread_header.append(add_thread)
        threads.append(thread_header)
        self.thread_list = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL,
            spacing=0,
        )
        threads.append(self.thread_list)
        column.append(threads)

        activity = self._panel()
        activity.append(SectionHeader("Recent activity"))
        self.activity_list = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL,
            spacing=0,
        )
        activity.append(self.activity_list)
        column.append(activity)
        return column

    def _install_actions(self) -> None:
        app = self.get_application()
        actions = {
            "palette": self.open_palette,
            "codex": self.action_codex,
            "shell": self.action_shell,
            "copy": self.action_copy,
            "handoff": self.action_handoff,
            "ready": self.action_ready,
        }
        accelerators = {
            "palette": ["<Control>k"],
            "codex": ["<Control>Return"],
            "shell": ["<Control><Shift>Return"],
            "copy": ["<Control><Shift>c"],
            "handoff": ["<Control><Shift>h"],
            "ready": ["<Control><Shift>r"],
        }
        for name, callback in actions.items():
            action = Gio.SimpleAction.new(name, None)
            action.connect(
                "activate",
                lambda _action, _parameter, cb=callback: cb(),
            )
            self.add_action(action)
            app.set_accels_for_action(f"win.{name}", accelerators[name])

    def refresh_projects(self, *, select: str = "") -> None:
        self.runner.submit(
            "Could not read projects",
            self.controller.project_items,
            lambda result: self._projects_loaded(
                result, select=select
            ),
        )

    def _projects_loaded(
        self, result: object, *, select: str = ""
    ) -> None:
        self.project_items = tuple(result)
        clear(self.project_list)
        if not self.project_items:
            self.selected_project = ""
            self.workspace = None
            self.stack.set_visible_child_name("empty")
            self._render_accounts(())
            self.refresh_accounts()
            return
        for item in self.project_items:
            self.project_list.append(ProjectRow(item))
        names = {item.name for item in self.project_items}
        target = (
            select
            if select in names
            else (
                self.selected_project
                if self.selected_project in names
                else self.controller.initial_project(self.project_items)
            )
        )
        row = self._row_for_project(target)
        self._selecting_row = True
        self.project_list.select_row(row)
        self._selecting_row = False
        self.select_project(target)
        self.refresh_accounts()

    def _row_for_project(self, name: str) -> Gtk.ListBoxRow | None:
        index = 0
        while True:
            row = self.project_list.get_row_at_index(index)
            if row is None:
                return None
            if getattr(row, "project_name", "") == name:
                return row
            index += 1

    def _project_row_selected(
        self,
        _list: Gtk.ListBox,
        row: Gtk.ListBoxRow | None,
    ) -> None:
        if self._selecting_row or row is None:
            return
        name = getattr(row, "project_name", "")
        if name and name != self.selected_project:
            self.select_project(name)

    def select_project(self, name: str) -> None:
        if not name:
            return
        self.selected_project = name
        self._workspace_generation += 1
        generation = self._workspace_generation
        self.stack.set_visible_child_name("loading")
        self.runner.submit(
            f"Could not load {name}",
            lambda: self.controller.select_project(name),
            lambda result: self._workspace_loaded(
                name,
                generation,
                result,
            ),
        )

    def _workspace_loaded(
        self,
        name: str,
        generation: int,
        result: object,
    ) -> None:
        if generation != self._workspace_generation or name != self.selected_project:
            return
        self.workspace = result
        self._render_workspace(result)
        self.stack.set_visible_child_name("workspace")
        self.refresh_accounts()

    def refresh_accounts(self) -> None:
        selected = self.workspace.account if self.workspace else ""
        self.runner.submit(
            "Codex account status is unavailable",
            lambda: self.controller.account_items(selected),
            self._accounts_loaded,
        )

    def _accounts_loaded(self, result: object) -> None:
        self.accounts = tuple(result)
        self._render_accounts(self.accounts)
        if self.workspace:
            self._render_usage_warning(self.workspace)

    def _render_accounts(self, accounts: tuple[AccountItem, ...]) -> None:
        clear(self.account_box)
        if not accounts:
            self.account_box.append(
                make_label("Launcher account status unavailable", "muted")
            )
            return
        for item in accounts:
            chip = AccountChip(item)
            chip.connect(
                "toggled",
                lambda button, account=item.name: (
                    self._account_selected(account)
                    if button.get_active()
                    else None
                ),
            )
            self.account_box.append(chip)

    def _account_selected(self, account: str) -> None:
        if not self.selected_project or (
            self.workspace and account == self.workspace.account
        ):
            return
        project = self.selected_project
        capabilities = self.controller.workbench.platform_capabilities(
            embedded_terminal=self.embedded_terminal.available
        )
        self.runner.submit(
            "Could not set intended Codex account",
            lambda: self.controller.select_account(project, account),
            lambda result: self._account_selection_complete(account, result),
        )

    def _account_selection_complete(
        self, account: str, result: object
    ) -> None:
        self.toast(
            f"Intended account set to {account}. No launch or handoff performed."
        )
        self.workspace = result
        self._render_workspace(result)
        self.refresh_projects(select=self.selected_project)

    def _render_workspace(self, workspace: WorkspaceState) -> None:
        self.project_name_label.set_text(
            workspace.project.theme.label or workspace.project.name.upper()
        )
        self.project_path_label.set_text(
            self._compact_home(str(workspace.project.path))
        )
        if workspace.session:
            self.session_chip.set_text(workspace.session.name)
            self.session_chip.set_visible(True)
        else:
            self.session_chip.set_visible(False)

        clear(self.confidence_box)
        for item in workspace.confidence:
            self.confidence_box.append(ConfidenceCell(item))

        self.objective_label.set_text(workspace.objective or "No objective recorded.")
        completed = (
            "\n".join(f"• {item}" for item in workspace.session.completed[-4:])
            if workspace.session and workspace.session.completed
            else "None recorded."
        )
        self.completed_label.set_text(completed)
        self.current_state_label.set_text(
            (
                workspace.session.current_state
                if workspace.session
                else ""
            )
            or "Not recorded."
        )
        self.next_action_label.set_text(
            (
                workspace.session.next_action
                if workspace.session
                else ""
            )
            or "Not recorded."
        )
        self.instructions_label.set_text(
            "\n".join(f"• {item}" for item in workspace.project.instructions)
            or "None recorded."
        )
        self._render_project_panels(workspace)

        self._render_git_files(workspace)
        self._render_paths(workspace)
        self._render_threads(workspace)
        self._render_activity(workspace)
        self._render_usage_warning(workspace)
        self.workspace_dock.show_project(workspace.project)

    def _render_git_files(self, workspace: WorkspaceState) -> None:
        clear(self.git_file_list)
        changes = workspace.status.git.file_changes
        if not changes:
            self.git_file_list.append(
                make_label("Clean working tree", "muted")
            )
            return
        for change in changes[:18]:
            row = Gtk.Box(
                orientation=Gtk.Orientation.HORIZONTAL,
                spacing=8,
            )
            row.add_css_class("git-file-row")
            row.append(
                make_label(change.category.upper(), "git-file-kind")
            )
            path = make_label(
                change.path,
                "git-file-path",
                selectable=True,
            )
            path.set_hexpand(True)
            path.set_ellipsize(3)
            row.append(path)
            self.git_file_list.append(row)
        if len(changes) > 18:
            self.git_file_list.append(
                make_label(f"+ {len(changes) - 18} more", "muted")
            )

    def _render_paths(self, workspace: WorkspaceState) -> None:
        clear(self.path_list)
        values = [
            (
                "Canonical root",
                "canonical",
                workspace.project.path,
                (
                    "Git repository"
                    if workspace.status.git.is_repository
                    else "directory"
                ),
                "",
                True,
            )
        ]
        values.extend(
            (
                item.associated.label,
                item.associated.role,
                item.path,
                item.summary,
                item.associated.label,
                item.associated.open_shell,
            )
            for item in workspace.status.associated
        )
        for label, role, path, summary, target, shell_enabled in values:
            row = Gtk.Box(
                orientation=Gtk.Orientation.HORIZONTAL,
                spacing=6,
            )
            row.add_css_class("path-row")
            labels = Gtk.Box(
                orientation=Gtk.Orientation.VERTICAL,
                spacing=1,
            )
            labels.set_hexpand(True)
            labels.append(
                make_label(f"{label} · {role}", "thread-label")
            )
            path_label = make_label(
                self._compact_home(str(path)),
                "detail-value",
                selectable=True,
            )
            path_label.set_ellipsize(3)
            labels.append(path_label)
            labels.append(make_label(summary, "muted"))
            row.append(labels)
            files = icon_button(
                "folder-open-symbolic",
                f"Open {label} in Files",
            )
            files.connect(
                "clicked",
                lambda _button, selected=target: self._open_folder(selected),
            )
            row.append(files)
            shell = icon_button(
                "utilities-terminal-symbolic",
                f"Open shell in {label}",
            )
            shell.connect(
                "clicked",
                lambda _button, selected=target: self._open_shell_at(selected),
            )
            shell.set_sensitive(shell_enabled)
            row.append(shell)
            self.path_list.append(row)

    def _render_threads(self, workspace: WorkspaceState) -> None:
        clear(self.thread_list)
        threads = list(workspace.threads)
        if (
            workspace.session
            and workspace.session.gpt_thread
            and all(
                item.url != workspace.session.gpt_thread
                for item in threads
            )
        ):
            from ..models import ChatGPTThread

            threads.append(
                ChatGPTThread(
                    workspace.session.gpt_thread,
                    f"{workspace.session.name} thread",
                )
            )
        if not threads:
            self.thread_list.append(
                make_label(
                    "No thread references. Add the ChatGPT conversation for this build.",
                    "muted",
                    wrap=True,
                )
            )
            return
        for thread in threads:
            row = Gtk.Box(
                orientation=Gtk.Orientation.HORIZONTAL,
                spacing=6,
            )
            row.add_css_class("thread-row")
            labels = Gtk.Box(
                orientation=Gtk.Orientation.VERTICAL,
                spacing=1,
            )
            labels.set_hexpand(True)
            labels.append(
                make_label(thread.display_label, "thread-label")
            )
            if thread.label:
                url = make_label(thread.url, "thread-url")
                url.set_ellipsize(3)
                labels.append(url)
            if thread.notes:
                labels.append(
                    make_label(thread.notes, "muted", wrap=True)
                )
            row.append(labels)
            open_button = icon_button(
                "external-link-symbolic",
                "Open in system browser",
            )
            open_button.connect(
                "clicked",
                lambda _button, url=thread.url: self._open_thread(url),
            )
            row.append(open_button)
            copy_button = icon_button(
                "edit-copy-symbolic",
                "Copy thread link",
            )
            copy_button.connect(
                "clicked",
                lambda _button, url=thread.url: self._copy_thread(url),
            )
            row.append(copy_button)
            self.thread_list.append(row)

    def _render_activity(self, workspace: WorkspaceState) -> None:
        clear(self.activity_list)
        if not workspace.activity:
            self.activity_list.append(
                make_label("No Workbench activity yet.", "muted")
            )
            return
        for item in workspace.activity[:12]:
            row = Gtk.Box(
                orientation=Gtk.Orientation.HORIZONTAL,
                spacing=8,
            )
            row.add_css_class("activity-row")
            row.append(
                make_label(
                    self._activity_time(item.timestamp),
                    "activity-time",
                )
            )
            summary = make_label(
                item.summary,
                "activity-summary",
                wrap=True,
            )
            summary.set_hexpand(True)
            row.append(summary)
            self.activity_list.append(row)

    def _render_usage_warning(self, workspace: WorkspaceState) -> None:
        status = workspace.status.codex
        if status is None:
            self.usage_revealer.set_reveal_child(False)
            return
        settings = self.controller.workbench.settings_snapshot()
        level = status.usage_level(
            low_threshold=settings.low_usage_threshold,
            critical_threshold=settings.critical_usage_threshold,
        )
        if level not in {"low", "critical", "exhausted"}:
            self.usage_revealer.set_reveal_child(False)
            return
        remaining = (
            f"{status.five_hour_remaining}%"
            if status.five_hour_remaining is not None
            else "an unknown amount"
        )
        self.usage_title.set_text(
            "CODEX USAGE CRITICAL"
            if level in {"critical", "exhausted"}
            else "CODEX USAGE LOW"
        )
        suggestion = self._suggested_account(workspace.account)
        message = (
            f"{workspace.account} has {remaining} remaining in the current "
            "5-hour window."
        )
        if suggestion:
            message += (
                f" Suggested: {suggestion.name} · "
                f"{suggestion.five_hour_remaining}%."
            )
        self.usage_detail.set_text(message)
        if level in {"critical", "exhausted"}:
            self.usage_warning_box.add_css_class("critical")
            self.action_buttons["handoff"].add_css_class("destructive-action")
        else:
            self.usage_warning_box.remove_css_class("critical")
            self.action_buttons["handoff"].remove_css_class(
                "destructive-action"
            )
        self.usage_revealer.set_reveal_child(True)

    def _suggested_account(self, current: str) -> AccountItem | None:
        choices = [
            item
            for item in self.accounts
            if item.name != current
            and item.five_hour_remaining is not None
            and item.level not in {"unavailable", "exhausted"}
        ]
        return max(
            choices,
            key=lambda item: item.five_hour_remaining or 0,
            default=None,
        )

    def refresh_all(self) -> None:
        self.refresh_projects(select=self.selected_project)

    def dispatch(self, action: str) -> None:
        callbacks = {
            "codex": self.action_codex,
            "shell": self.action_shell,
            "ready": self.action_ready,
            "status": self.action_status,
            "commit": self.action_commit,
            "push": self.action_push,
            "copy": self.action_copy,
            "handoff": self.action_handoff,
            "resume": self.action_resume,
            "session": self.action_session,
            "edit-project": self.action_edit_project,
        }
        callback = callbacks.get(action)
        if callback:
            callback()

    def _has_project(self) -> bool:
        if self.selected_project:
            return True
        self.toast("Select or add a project first.")
        return False

    def action_codex(self) -> None:
        if not self._has_project():
            return
        project = self.selected_project
        self.runner.submit(
            "Codex preflight failed",
            lambda: self.controller.preflight(project),
            self._codex_preflight_ready,
        )

    def _codex_preflight_ready(self, result: object) -> None:
        report = result
        failures = [item for item in report.checks if item.failed]
        critical_labels = {"directory", "codex account", "codex launcher"}
        blocked = any(item.label in critical_labels for item in failures)
        context_failures = [
            item
            for item in failures
            if item.label
            in {
                "git user",
                "git email",
                "expected remote",
                "github account",
                "repository owner",
                "git repository",
            }
        ]
        if failures or context_failures:
            show_preflight(
                self,
                report,
                launch_anyway=None if blocked else self._launch_codex,
            )
        else:
            self._launch_codex()

    def _launch_codex(self) -> None:
        result = self.workspace_dock.open_codex()
        if result is None:
            return
        pane, created = result
        self.toast(
            f"Opened {pane.title}" if created else f"Focused {pane.title}"
        )

    def action_shell(self) -> None:
        if not self._has_project():
            return
        project = self.selected_project
        capabilities = self.controller.workbench.platform_capabilities(
            embedded_terminal=self.embedded_terminal.available
        )
        self.runner.submit(
            "Could not read shell targets",
            lambda: self.controller.shell_targets(project),
            lambda targets: ShellDialog(
                self,
                self.workspace.project,
                targets,
                embedded_available=self.embedded_terminal.available,
                embedded_reason=self.embedded_terminal.unavailable_reason,
                external_available=capabilities.external_terminal,
                on_open=self._shell_selected,
            ).present(),
        )

    def _shell_selected(self, mode: str, target: str) -> None:
        if mode == "embedded":
            self._open_embedded_shell(target)
        else:
            self._open_external_shell(target)

    def _open_shell_at(self, target: str) -> None:
        if not self.workspace:
            return
        capabilities = self.controller.workbench.platform_capabilities(
            embedded_terminal=self.embedded_terminal.available
        )
        selection = select_shell_backend(
            self.workspace.project.terminal.mode,
            embedded_available=capabilities.embedded_terminal,
            external_available=capabilities.external_terminal,
        )
        if selection.fallback_reason:
            self.toast(selection.fallback_reason)
        if selection.selected == "embedded":
            self._open_embedded_shell(target)
        elif selection.selected == "external":
            self._open_external_shell(target)
        else:
            self.toast(selection.fallback_reason)

    def _open_external_shell(self, target: str = "") -> None:
        if not self._has_project():
            return
        project = self.selected_project
        self.runner.submit(
            "Could not open external shell",
            lambda: self.controller.open_shell(project, target),
            lambda _result: self._action_complete(
                f"Opened external shell for {project}"
            ),
        )

    def _open_embedded_shell(self, target: str = "") -> None:
        if not self._has_project():
            return
        if not self.embedded_terminal.available:
            self.toast(self.embedded_terminal.unavailable_reason)
            return
        label = target or "root"
        pane = self.workspace_dock.add_pane(
            "terminal",
            state={"working_directory": target},
            title=f"Terminal · {label}",
        )
        if pane is not None:
            self.toast(f"Opened {pane.title}")

    def _open_folder(self, target: str = "") -> None:
        if not self._has_project():
            return
        project = self.selected_project
        self.runner.submit(
            "Could not open folder",
            lambda: self.controller.open_folder(project, target),
            lambda result: (
                self.toast(f"Opened {result.url}")
                if result.opened
                else self.toast(f"Could not open folder: {result.error}")
            ),
        )

    def action_ready(self) -> None:
        if not self._has_project():
            return
        project = self.selected_project
        self.runner.submit(
            "Ready check failed",
            lambda: self.controller.preflight(project),
            lambda result: show_preflight(self, result),
        )

    def action_status(self) -> None:
        if not self._has_project():
            return
        project = self.selected_project
        self.runner.submit(
            "Status is unavailable",
            lambda: self.controller.status(project),
            lambda result: show_status(
                self,
                f"Status · {project}",
                result.text,
            ),
        )

    def action_copy(self) -> None:
        if not self._has_project():
            return
        project = self.selected_project

        def context_ready(status: object) -> None:
            result = self.controller.complete_copy_all(
                status,
                self._copy_text(status.text),
            )
            if result.clipboard.copied:
                self.toast("Copied workspace context")
            else:
                show_copy_fallback(
                    self,
                    result.status.text,
                    result.clipboard.error_summary,
                )
            self._refresh_workspace_only()

        self.runner.submit(
            "Could not generate workspace context",
            lambda: self.controller.prepare_copy_all(project),
            context_ready,
        )

    def action_commit(self) -> None:
        if not self._has_project():
            return
        project = self.selected_project

        def planned(result: object) -> None:
            if result.git.clean:
                self.toast("Working tree is clean")
                return
            CommitDialog(
                self,
                result,
                self._commit_requested,
            ).present()

        self.runner.submit(
            "Could not prepare commit",
            lambda: self.controller.commit_plan(
                project,
                show_diff=True,
            ),
            planned,
        )

    def _commit_requested(
        self,
        message: str,
        files: tuple[str, ...],
        stage_all: bool,
        allow_identity_mismatch: bool,
        plan: object,
    ) -> None:
        project = self.selected_project

        def completed(result: object) -> None:
            if result.committed:
                self.toast("Commit created")
            else:
                show_status(
                    self,
                    "Commit not created",
                    result.reason or result.output or "Git commit failed",
                )
            self.refresh_all()

        self.runner.submit(
            "Commit failed",
            lambda: self.controller.commit(
                project,
                message,
                files=files,
                stage_all=stage_all,
                allow_identity_mismatch=allow_identity_mismatch,
                plan=plan,
            ),
            completed,
        )

    def action_push(self) -> None:
        if not self._has_project():
            return
        project = self.selected_project
        self.runner.submit(
            "Could not prepare push",
            lambda: self.controller.push_plan(project),
            lambda result: PushDialog(
                self,
                result,
                self._push_requested,
            ).present(),
        )

    def _push_requested(
        self,
        plan: object,
        set_upstream: bool,
        destination_override: bool,
        identity_override: bool,
    ) -> None:
        project = self.selected_project

        def completed(result: object) -> None:
            if result.pushed:
                self.toast(f"Pushed {result.plan.destination}")
            else:
                show_status(
                    self,
                    "Push not performed",
                    result.reason or result.output or "Git push failed",
                )
            self.refresh_all()

        self.runner.submit(
            "Push failed",
            lambda: self.controller.push(
                project,
                plan,
                set_upstream=set_upstream,
                allow_destination_mismatch=destination_override,
                allow_identity_mismatch=identity_override,
            ),
            completed,
        )

    def action_handoff(self) -> None:
        if not self._has_project() or not self.workspace:
            return
        project = self.selected_project
        workspace = self.workspace

        def prepare() -> tuple[tuple[AccountItem, ...], tuple[object, ...]]:
            accounts = self.accounts or self.controller.account_items(
                workspace.account
            )
            candidates = self.controller.transcript_candidates(
                project,
                workspace.session.id if workspace.session else None,
            )
            return tuple(accounts), tuple(candidates)

        def prepared(result: object) -> None:
            accounts, candidates = result
            HandoffDialog(
                self,
                workspace,
                accounts,
                candidates,
                self._handoff_requested,
            ).present()

        self.runner.submit(
            "Could not prepare handoff",
            prepare,
            prepared,
        )

    def _handoff_requested(
        self,
        account: str,
        transcript: Path | None,
        launch: bool,
    ) -> None:
        project = self.selected_project

        def completed(bundle: object) -> None:
            show_handoff_summary(
                self,
                bundle.session_dir,
                bundle.handoff_path,
                launched=launch,
            )
            self.refresh_all()

        self.runner.submit(
            "Handoff failed",
            lambda: self.controller.handoff(
                project,
                to_account=account,
                transcript=transcript,
                launch=launch,
            ),
            completed,
        )

    def action_resume(self) -> None:
        if not self._has_project():
            return
        project = self.selected_project
        self.runner.submit(
            "No session is available to resume",
            lambda: self.controller.resume_plan(project),
            lambda result: ResumeDialog(
                self,
                result,
                self._resume_here,
                self._resume_codex,
            ).present(),
        )

    def _resume_here(self, plan: object) -> None:
        self.runner.submit(
            "Could not resume session",
            lambda: self.controller.resume_here(plan),
            lambda _result: self._action_complete(
                f"Resumed {plan.session.name}"
            ),
        )

    def _resume_codex(self, plan: object) -> None:
        self.runner.submit(
            "Could not resume in Codex",
            lambda: self.controller.resume_in_codex(plan),
            lambda _result: self._action_complete(
                f"Resumed {plan.session.name} in Codex"
            ),
        )

    def action_session(self) -> None:
        if not self._has_project() or not self.workspace:
            return
        SessionDialog(
            self,
            self.workspace,
            self._session_save_requested,
        ).present()

    def _session_save_requested(self, changes: dict[str, object]) -> None:
        project = self.selected_project
        session_id = (
            self.workspace.session.id
            if self.workspace and self.workspace.session
            else ""
        )
        self.runner.submit(
            "Could not save session",
            lambda: self.controller.save_session_context(
                project,
                session_id,
                changes,
            ),
            lambda result: self._action_complete(
                f"Saved session {result.name}"
            ),
        )

    def action_add_project(self) -> None:
        dialog = AddProjectDialog(
            self,
            self.accounts,
            self._add_project_requested,
            on_clone=self._clone_project_requested,
        )
        self._add_project_dialog = dialog
        dialog.present()

    def _add_project_requested(
        self,
        name: str,
        directory: str,
        account: str,
        github: str,
    ) -> None:
        self.runner.submit(
            "Could not add project",
            lambda: self.controller.register_project(
                name,
                directory,
                codex_account=account,
                github_account=github,
            ),
            lambda project: self._project_added(project.name),
        )

    def _project_added(self, name: str) -> None:
        self.toast(f"Added {name}")
        self.refresh_projects(select=name)

    def _clone_project_requested(
        self,
        name: str,
        repository_url: str,
        destination_parent: str,
        destination_folder: str,
        account: str,
        github: str,
        cancel: object,
    ) -> None:
        dialog = self._add_project_dialog

        def progress(item: object) -> None:
            GLib.idle_add(
                dialog.set_clone_progress,
                item.message,
            )

        self.runner.submit(
            "Could not clone project",
            lambda: self.controller.clone_project(
                name,
                repository_url,
                destination_parent,
                destination_folder,
                codex_account=account,
                github_account=github,
                cancel=cancel,
                on_progress=progress,
            ),
            lambda result: self._clone_project_complete(
                dialog,
                name,
                result,
            ),
            on_error=lambda error: dialog.finish_clone(
                success=False,
                message=str(error),
            ),
        )

    def _clone_project_complete(
        self,
        dialog: AddProjectDialog,
        name: str,
        result: object,
    ) -> None:
        clone = result.clone
        output = "\n".join(
            value.strip()
            for value in (clone.stdout, clone.stderr)
            if value.strip()
        )
        if result.registered:
            project_name = result.project.name
            dialog.finish_clone(
                success=True,
                message=clone.summary,
                output=output,
            )
            self._project_added(project_name)
            return
        dialog.finish_clone(
            success=False,
            message=clone.summary,
            output=output,
        )

    def action_edit_project(self) -> None:
        if not self.workspace or not self._has_project():
            return
        project = self.workspace.project
        ProjectDialog(
            self,
            project,
            self.accounts,
            on_save=self._edit_project_requested,
            on_remove=self._confirm_remove_project,
        ).present()

    def _edit_project_requested(
        self,
        changes: dict[str, object],
    ) -> None:
        project = self.selected_project
        self.runner.submit(
            "Could not edit project",
            lambda: self.controller.edit_project(project, **changes),
            self._project_edited,
        )

    def _project_edited(self, result: object) -> None:
        message = f"Updated {result.project.name}"
        if result.session_context_warning:
            message += "; review active session context after directory change"
        self.toast(message)
        self.workspace_dock.close_project(result.project.registry_id)
        self.selected_project = result.project.name
        self.refresh_projects(select=result.project.name)

    def _confirm_remove_project(self) -> None:
        if not self.workspace:
            return
        project = self.workspace.project
        self.runner.submit(
            "Could not read project sessions",
            lambda: len(self.controller.sessions(project.registry_id)),
            lambda count: RemoveProjectDialog(
                self,
                project,
                session_count=count,
                on_remove=self._remove_project_requested,
            ).present(),
        )

    def _remove_project_requested(self) -> None:
        project = self.selected_project
        self.runner.submit(
            "Could not remove project",
            lambda: self.controller.remove_project(project),
            self._project_removed,
        )

    def _project_removed(self, result: object) -> None:
        self.workspace_dock.close_project(result.project.registry_id)
        self.selected_project = ""
        self.workspace = None
        self.toast(
            f"Removed {result.project.name} from Workbench; files and "
            f"{result.preserved_sessions} session(s) were preserved"
        )
        self.refresh_projects()

    def action_add_thread(self) -> None:
        if not self._has_project():
            return
        ThreadDialog(self, self._thread_add_requested).present()

    def _thread_add_requested(
        self,
        url: str,
        label: str,
        notes: str,
    ) -> None:
        project = self.selected_project
        self.runner.submit(
            "Could not add thread",
            lambda: self.controller.add_thread(
                project,
                url,
                label=label,
                notes=notes,
            ),
            lambda thread: self._action_complete(
                f"Added thread {thread.display_label}"
            ),
        )

    def _open_thread(self, url: str) -> None:
        project = self.selected_project

        def completed(result: object) -> None:
            if not result.opened:
                self.toast(f"Could not open link: {result.error}")
            else:
                self._refresh_workspace_only()

        self.runner.submit(
            "Could not open thread",
            lambda: self.controller.open_thread(project, url),
            completed,
        )

    def _copy_thread(self, url: str) -> None:
        result = self._copy_text(url)
        if result.copied:
            self.toast("Copied thread link")
        else:
            self.toast(f"Clipboard unavailable: {result.error_summary}")

    def _copy_text(self, text: str) -> ClipboardResult:
        if not self.controller.clipboard_enabled():
            return ClipboardResult(False, "disabled")
        return self.native_clipboard.copy(text)

    def action_settings(self) -> None:
        settings = self.controller.workbench.settings_snapshot()
        SettingsDialog(
            self,
            settings,
            config_path=str(self.controller.workbench.projects.path),
            data_path=str(self.controller.workbench.sessions.root.parent),
            on_save=self._settings_save_requested,
        ).present()

    def _settings_save_requested(
        self, changes: dict[str, object]
    ) -> None:
        self.runner.submit(
            "Could not save settings",
            lambda: self.controller.save_settings(changes),
            self._settings_saved,
        )

    def _settings_saved(self, settings: object) -> None:
        style = Adw.StyleManager.get_default()
        style.set_color_scheme(
            Adw.ColorScheme.DEFAULT
            if settings.theme == "system"
            else Adw.ColorScheme.FORCE_DARK
        )
        self.toast("Settings saved")
        self.refresh_all()

    def open_palette(self) -> None:
        commands = self.controller.palette()
        CommandPalette(
            self,
            commands,
            self._palette_activate,
        ).present()

    def _palette_activate(self, command: PaletteCommand) -> None:
        if command.id == "switch-project":
            row = self._row_for_project(command.project)
            if row:
                self.project_list.select_row(row)
            return
        self.dispatch(command.id)

    def _focus_accounts(self) -> None:
        if self.accounts:
            self.toast(
                "Choose an account above to set project/session intent. "
                "Selection never launches or hands off automatically."
            )
        else:
            self.toast("Codex account status is unavailable.")

    def _action_complete(self, message: str) -> None:
        self.toast(message)
        self.refresh_all()

    def _refresh_workspace_only(self) -> None:
        if self.selected_project:
            self.select_project(self.selected_project)

    def toast(self, message: str) -> None:
        toast = Adw.Toast.new(message)
        toast.set_timeout(4)
        self.toast_overlay.add_toast(toast)

    def _async_error(
        self,
        label: str,
        error: BaseException,
    ) -> None:
        self.toast(f"{label}: {error}")

    def _close_request(self, _window: Gtk.Window) -> bool:
        self.workspace_dock.shutdown()
        self.runner.close()
        return False

    @staticmethod
    def _compact_home(path: str) -> str:
        home = str(Path.home())
        return "~" + path[len(home):] if path.startswith(home) else path

    @staticmethod
    def _activity_time(value: str) -> str:
        try:
            timestamp = datetime.fromisoformat(value).astimezone()
            return timestamp.strftime("%H:%M")
        except ValueError:
            return "—"
