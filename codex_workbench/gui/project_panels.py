from __future__ import annotations

from pathlib import Path
from typing import Any

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gtk  # noqa: E402

from .panels import ResponsivePanelGrid
from .widgets import SectionHeader, clear, icon_button, make_label
from .workspace_dialogs import CommandSuggestionsDialog, ProjectCommandDialog


class ProjectPanelsMixin:
    """Responsive Project Info panels and their project-owned actions."""

    def _build_project_info_section(self) -> Gtk.Widget:
        section = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        self.info_toggle = Gtk.Button()
        self.info_toggle.add_css_class("project-info-rail")
        self.info_toggle_label = make_label("▾ PROJECT INFO", "section-title")
        self.info_toggle_label.set_hexpand(True)
        self.info_toggle.set_child(self.info_toggle_label)
        self.info_toggle.connect(
            "clicked", lambda *_args: self._toggle_project_info()
        )
        section.append(self.info_toggle)
        self.info_revealer = Gtk.Revealer(
            transition_type=Gtk.RevealerTransitionType.SLIDE_DOWN,
            transition_duration=120,
        )
        self.info_revealer.set_child(self._build_project_panel_grid())
        section.append(self.info_revealer)
        return section

    def _build_project_panel_grid(self) -> Gtk.Widget:
        grid = ResponsivePanelGrid()

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
        grid.append_panel(memory, large_span=2, medium_span=2)

        prompt = self._panel()
        prompt.append(SectionHeader("Prompt Hold"))
        prompt_scroll = Gtk.ScrolledWindow(
            hscrollbar_policy=Gtk.PolicyType.NEVER,
            vscrollbar_policy=Gtk.PolicyType.AUTOMATIC,
            max_content_height=150,
            propagate_natural_height=True,
        )
        self.prompt_hold_label = make_label(
            "Nothing held.", "memory-value", wrap=True, selectable=True
        )
        prompt_scroll.set_child(self.prompt_hold_label)
        prompt.append(prompt_scroll)
        prompt_actions = Gtk.Box(
            orientation=Gtk.Orientation.HORIZONTAL,
            spacing=5,
            margin_top=5,
        )
        self.prompt_hold_button = Gtk.Button(label="Hold Clipboard")
        self.prompt_copy_button = Gtk.Button(label="Copy")
        self.prompt_send_button = Gtk.Button(label="Paste Into")
        self.prompt_clear_button = Gtk.Button(label="Clear")
        self.prompt_hold_button.connect(
            "clicked", lambda *_args: self.action_hold_clipboard()
        )
        self.prompt_copy_button.connect(
            "clicked", lambda *_args: self.action_copy_held_prompt()
        )
        self.prompt_send_button.connect(
            "clicked", lambda *_args: self.action_send_held_prompt()
        )
        self.prompt_clear_button.connect(
            "clicked", lambda *_args: self.action_clear_held_prompt()
        )
        for button in (
            self.prompt_hold_button,
            self.prompt_copy_button,
            self.prompt_send_button,
            self.prompt_clear_button,
        ):
            button.add_css_class("compact-action")
            prompt_actions.append(button)
        prompt.append(prompt_actions)
        grid.append_panel(prompt)

        git_panel = self._panel()
        git_panel.append(SectionHeader("Working tree"))
        self.git_file_list = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL, spacing=0
        )
        git_panel.append(self.git_file_list)
        grid.append_panel(git_panel)

        roots = self._panel()
        roots_header = SectionHeader("Project roots")
        edit_roots = icon_button(
            "document-edit-symbolic", "Edit canonical and associated paths"
        )
        edit_roots.connect("clicked", lambda *_args: self.action_edit_project())
        roots_header.append(edit_roots)
        roots.append(roots_header)
        self.path_list = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        roots.append(self.path_list)
        grid.append_panel(roots)

        commands = self._panel()
        command_header = SectionHeader("Project commands")
        discover = icon_button(
            "system-search-symbolic", "Suggest commands from project files"
        )
        discover.connect("clicked", lambda *_args: self.action_suggest_commands())
        command_header.append(discover)
        add_command = icon_button("list-add-symbolic", "Add project command")
        add_command.connect("clicked", lambda *_args: self.action_add_command())
        command_header.append(add_command)
        commands.append(command_header)
        self.command_list = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL, spacing=0
        )
        commands.append(self.command_list)
        grid.append_panel(commands)

        threads = self._panel()
        thread_header = SectionHeader("ChatGPT threads")
        add_thread = icon_button("list-add-symbolic", "Add ChatGPT thread")
        add_thread.connect("clicked", lambda *_args: self.action_add_thread())
        thread_header.append(add_thread)
        threads.append(thread_header)
        self.thread_list = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL, spacing=0
        )
        threads.append(self.thread_list)
        grid.append_panel(threads)

        activity = self._panel()
        activity.append(SectionHeader("Recent activity"))
        self.activity_list = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL, spacing=0
        )
        activity.append(self.activity_list)
        grid.append_panel(activity)

        instructions = self._panel()
        instructions.append(SectionHeader("Project instructions"))
        self.instructions_label = make_label(
            "None recorded.", "memory-value", wrap=True, selectable=True
        )
        instructions.append(self.instructions_label)
        grid.append_panel(instructions, large_span=1, medium_span=2)
        return grid

    def _render_project_panels(self, workspace: Any) -> None:
        self._render_info_toggle(workspace)
        self._render_prompt_hold(workspace.project.workspace.prompt_hold)
        self._render_project_commands(workspace)

    def _render_info_toggle(self, workspace: Any) -> None:
        project_workspace = workspace.project.workspace
        collapsed = project_workspace.info_collapsed
        self.info_revealer.set_reveal_child(not collapsed)
        if not collapsed:
            self.info_toggle_label.set_text("▾ PROJECT INFO")
            return
        tree_count = len(workspace.status.git.file_changes)
        command_count = len(project_workspace.commands)
        prompt = "Prompt held" if project_workspace.prompt_hold else "Prompt empty"
        roots = 1 + len(workspace.project.associated_paths)
        objective = "Objective" if workspace.objective else "No objective"
        self.info_toggle_label.set_text(
            f"▸ PROJECT INFO · {objective} · Tree {tree_count} · "
            f"Commands {command_count} · {prompt} · Roots {roots}"
        )

    def _toggle_project_info(self) -> None:
        if not self.workspace:
            return
        collapsed = not self.workspace.project.workspace.info_collapsed
        self.controller.set_project_info_collapsed(
            self.workspace.project.registry_id, collapsed
        )
        self.workspace.project.workspace.info_collapsed = collapsed
        self._render_info_toggle(self.workspace)

    def _render_prompt_hold(self, text: str) -> None:
        self.prompt_hold_label.set_text(text or "Nothing held.")
        held = bool(text)
        self.prompt_hold_button.set_visible(not held)
        self.prompt_copy_button.set_visible(held)
        self.prompt_send_button.set_visible(held)
        self.prompt_clear_button.set_visible(held)

    def action_hold_clipboard(self) -> None:
        if not self.workspace:
            return

        def completed(text: str | None, error: str) -> None:
            if error:
                self.toast(f"Clipboard unavailable: {error}")
                return
            if text is None or not text:
                self.toast("The text clipboard is empty.")
                return
            self.controller.hold_prompt(
                self.workspace.project.registry_id, text
            )
            self.workspace.project.workspace.prompt_hold = text
            self._render_prompt_hold(text)
            self._render_info_toggle(self.workspace)
            self.toast("Held a clipboard snapshot for this project")

        self.native_clipboard.read_text(completed)

    def action_copy_held_prompt(self) -> None:
        if not self.workspace:
            return
        text = self.workspace.project.workspace.prompt_hold
        result = self._copy_text(text)
        self.toast(
            "Copied held prompt"
            if result.copied
            else f"Clipboard unavailable: {result.error_summary}"
        )

    def action_clear_held_prompt(self) -> None:
        if not self.workspace:
            return
        self.controller.clear_prompt_hold(self.workspace.project.registry_id)
        self.workspace.project.workspace.prompt_hold = ""
        self._render_prompt_hold("")
        self._render_info_toggle(self.workspace)
        self.toast("Cleared held prompt")

    def action_send_held_prompt(self) -> None:
        if not self.workspace:
            return
        text = self.workspace.project.workspace.prompt_hold
        if not self.workspace_dock.paste_into_active(text):
            self.toast("Open or select a Codex/Terminal pane first.")

    def _render_project_commands(self, workspace: Any) -> None:
        clear(self.command_list)
        commands = workspace.project.workspace.commands
        if not commands:
            self.command_list.append(
                make_label(
                    "No commands saved. Add one or review safe suggestions.",
                    "muted",
                    wrap=True,
                )
            )
            return
        current_category = ""
        for command in sorted(
            commands, key=lambda item: (item.category.casefold(), item.name.casefold())
        ):
            if command.category != current_category:
                current_category = command.category
                self.command_list.append(
                    make_label(current_category.upper(), "command-category")
                )
            row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=5)
            row.add_css_class("command-row")
            labels = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=1)
            labels.set_hexpand(True)
            command_label = make_label(command.command, "command-value")
            command_label.set_ellipsize(3)
            labels.append(command_label)
            if command.description:
                labels.append(
                    make_label(command.description, "muted", wrap=True)
                )
            row.append(labels)
            copy = icon_button("edit-copy-symbolic", f"Copy {command.name}")
            copy.connect(
                "clicked",
                lambda _button, value=command.command: self._copy_command(value),
            )
            run = icon_button(
                "media-playback-start-symbolic",
                f"Run {command.name} in a visible terminal",
            )
            run.connect(
                "clicked",
                lambda _button, identity=command.id: self._run_project_command(identity),
            )
            edit = icon_button("document-edit-symbolic", f"Edit {command.name}")
            edit.connect(
                "clicked",
                lambda _button, value=command: self.action_edit_command(value),
            )
            row.append(copy)
            row.append(run)
            row.append(edit)
            self.command_list.append(row)

    def _copy_command(self, command: str) -> None:
        result = self._copy_text(command)
        self.toast(
            "Copied command"
            if result.copied
            else f"Clipboard unavailable: {result.error_summary}"
        )

    def _run_project_command(self, command_id: str) -> None:
        if not self.workspace:
            return
        project = self.workspace.project.registry_id

        def ready(result: object) -> None:
            command, cwd = result
            if self.workspace_dock.run_command(command.command, cwd):
                self.toast(f"Running {command.name} in Terminal")

        self.runner.submit(
            "Could not prepare project command",
            lambda: self.controller.project_command_target(project, command_id),
            ready,
        )

    def _command_roots(self) -> tuple[str, ...]:
        if not self.workspace:
            return ()
        return tuple(item.label for item in self.workspace.project.associated_paths)

    def action_add_command(self, preset: object | None = None) -> None:
        if not self.workspace:
            return
        project = self.workspace.project.registry_id
        values = (
            {
                "name": preset.name,
                "command": preset.command,
                "description": preset.description,
                "category": preset.category,
                "working_directory": preset.working_directory,
            }
            if preset is not None
            else None
        )
        if values is not None:
            self.runner.submit(
                "Could not add command",
                lambda: self.controller.add_project_command(project, **values),
                lambda command: self._action_complete(f"Added command {command.name}"),
            )
            return
        ProjectCommandDialog(
            self,
            roots=self._command_roots(),
            on_save=lambda changes: self._save_new_command(project, changes),
        ).present()

    def _save_new_command(
        self, project: str, changes: dict[str, str]
    ) -> None:
        self.runner.submit(
            "Could not add command",
            lambda: self.controller.add_project_command(project, **changes),
            lambda command: self._action_complete(f"Added command {command.name}"),
        )

    def action_edit_command(self, command: Any) -> None:
        if not self.workspace:
            return
        project = self.workspace.project.registry_id
        ProjectCommandDialog(
            self,
            command=command,
            roots=self._command_roots(),
            on_save=lambda changes: self._update_command(
                project, command.id, changes
            ),
            on_remove=lambda: self._remove_command(project, command.id),
        ).present()

    def _update_command(
        self, project: str, command_id: str, changes: dict[str, str]
    ) -> None:
        self.runner.submit(
            "Could not update command",
            lambda: self.controller.update_project_command(
                project, command_id, **changes
            ),
            lambda command: self._action_complete(f"Updated command {command.name}"),
        )

    def _remove_command(self, project: str, command_id: str) -> None:
        self.runner.submit(
            "Could not remove command",
            lambda: self.controller.remove_project_command(project, command_id),
            lambda command: self._action_complete(f"Removed command {command.name}"),
        )

    def action_suggest_commands(self) -> None:
        if not self.workspace:
            return
        project = self.workspace.project.registry_id
        self.runner.submit(
            "Could not inspect project command sources",
            lambda: self.controller.command_suggestions(project),
            lambda suggestions: CommandSuggestionsDialog(
                self,
                suggestions,
                on_add=self.action_add_command,
            ).present(),
        )

    def _workspace_focus_changed(self, focused: bool) -> None:
        self.project_top.set_visible(not focused)

    def _workspace_model_changed(self) -> None:
        if not self.workspace:
            return
        project = self.controller.workbench.project(
            self.workspace.project.registry_id
        )
        self.workspace.project.workspace = project.workspace
        self._render_info_toggle(self.workspace)

    def _copy_text_and_toast(self, text: str) -> None:
        result = self._copy_text(text)
        self.toast(
            "Copied path"
            if result.copied
            else f"Clipboard unavailable: {result.error_summary}"
        )

    def _open_external_url(self, url: str) -> None:
        if not url:
            return
        self.runner.submit(
            "Could not open URL",
            lambda: self.controller.workbench.desktop.open_url(url),
            lambda result: (
                self.toast("Opened in system browser")
                if result.opened
                else self.toast(f"Could not open URL: {result.error}")
            ),
        )

    def _open_folder_path(self, path: Path) -> None:
        self.runner.submit(
            "Could not open folder",
            lambda: self.controller.workbench.desktop.open_folder(path),
            lambda result: (
                self.toast(f"Opened {path}")
                if result.opened
                else self.toast(f"Could not open folder: {result.error}")
            ),
        )
