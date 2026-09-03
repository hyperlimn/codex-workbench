from __future__ import annotations

from pathlib import Path
from typing import Any

from .associated import resolve_project_path
from .command_discovery import CommandSuggestion, discover_project_commands
from .workspace import (
    PANE_PROVIDER_TYPES,
    ProjectCommand,
    ProjectWorkspace,
    WorkspacePane,
    stable_id,
)


class WorkspaceServiceMixin:
    """Project-workspace workflows mixed into the shared Workbench facade."""

    def project_workspace(self, project_name: str) -> ProjectWorkspace:
        return self.project(project_name).workspace

    def _save_workspace_project(self, project: Any, summary: str = "") -> Any:
        self.projects.save(project)
        if summary:
            self._record("workspace_updated", summary, project=project.name)
        return project

    def set_project_info_collapsed(
        self, project_name: str, collapsed: bool
    ) -> ProjectWorkspace:
        project = self.project(project_name)
        project.workspace.info_collapsed = bool(collapsed)
        self._save_workspace_project(project)
        return project.workspace

    def hold_prompt(self, project_name: str, text: str) -> str:
        project = self.project(project_name)
        project.workspace.prompt_hold = str(text)
        self._save_workspace_project(
            project,
            "Held prompt text" if text else "Cleared held prompt",
        )
        return project.workspace.prompt_hold

    def clear_prompt_hold(self, project_name: str) -> None:
        self.hold_prompt(project_name, "")

    @staticmethod
    def _command_values(
        *,
        name: str,
        command: str,
        description: str = "",
        category: str = "Other",
        working_directory: str = "",
    ) -> dict[str, str]:
        values = {
            "name": name.strip(),
            "command": command.strip(),
            "description": description.strip(),
            "category": category.strip() or "Other",
            "working_directory": working_directory.strip(),
        }
        if not values["name"]:
            raise ValueError("Project command name cannot be empty.")
        if not values["command"]:
            raise ValueError("Project command cannot be empty.")
        if "\x00" in values["command"]:
            raise ValueError("Project command cannot contain a NUL byte.")
        return values

    def add_project_command(
        self,
        project_name: str,
        *,
        name: str,
        command: str,
        description: str = "",
        category: str = "Other",
        working_directory: str = "",
        command_id: str = "",
    ) -> ProjectCommand:
        project = self.project(project_name)
        values = self._command_values(
            name=name,
            command=command,
            description=description,
            category=category,
            working_directory=working_directory,
        )
        if values["working_directory"]:
            resolve_project_path(
                project, values["working_directory"], require_shell=True
            )
        identity = command_id.strip() or stable_id("command")
        if any(item.id == identity for item in project.workspace.commands):
            raise ValueError(f"Duplicate project command ID: {identity}")
        value = ProjectCommand(identity, **values)
        project.workspace.commands.append(value)
        self._save_workspace_project(project, f"Added command {value.name}")
        return value

    def update_project_command(
        self,
        project_name: str,
        command_id: str,
        **changes: str,
    ) -> ProjectCommand:
        project = self.project(project_name)
        value = next(
            (
                item
                for item in project.workspace.commands
                if item.id == command_id
            ),
            None,
        )
        if value is None:
            raise ValueError(f"Unknown project command: {command_id}")
        current = {
            "name": value.name,
            "command": value.command,
            "description": value.description,
            "category": value.category,
            "working_directory": value.working_directory,
        }
        current.update(
            {key: str(item) for key, item in changes.items() if key in current}
        )
        values = self._command_values(**current)
        if values["working_directory"]:
            resolve_project_path(
                project, values["working_directory"], require_shell=True
            )
        for key, item in values.items():
            setattr(value, key, item)
        self._save_workspace_project(project, f"Updated command {value.name}")
        return value

    def remove_project_command(
        self, project_name: str, command_id: str
    ) -> ProjectCommand:
        project = self.project(project_name)
        value = next(
            (
                item
                for item in project.workspace.commands
                if item.id == command_id
            ),
            None,
        )
        if value is None:
            raise ValueError(f"Unknown project command: {command_id}")
        project.workspace.commands = [
            item for item in project.workspace.commands if item.id != command_id
        ]
        self._save_workspace_project(project, f"Removed command {value.name}")
        return value

    def command_suggestions(
        self, project_name: str
    ) -> tuple[CommandSuggestion, ...]:
        return discover_project_commands(self.project(project_name))

    def project_command_target(
        self, project_name: str, command_id: str
    ) -> tuple[ProjectCommand, Path]:
        project = self.project(project_name)
        value = next(
            (
                item
                for item in project.workspace.commands
                if item.id == command_id
            ),
            None,
        )
        if value is None:
            raise ValueError(f"Unknown project command: {command_id}")
        cwd = resolve_project_path(
            project, value.working_directory, require_shell=True
        )
        if not cwd.is_dir():
            raise ValueError(f"Command working directory does not exist: {cwd}")
        return value, cwd

    @staticmethod
    def _pane_defaults(
        provider_type: str, *, account: str = "", target: str = ""
    ) -> tuple[str, dict[str, Any]]:
        label = target or "root"
        if provider_type == "codex":
            return (
                f"Codex · {account}" if account else "Codex",
                {"account": account, "working_directory": target},
            )
        if provider_type == "terminal":
            return f"Terminal · {label}", {"working_directory": target}
        if provider_type == "browser":
            return "Browser", {"url": "http://localhost:3000"}
        return f"Files · {label}", {"working_directory": target, "path": ""}

    def add_workspace_pane(
        self,
        project_name: str,
        provider_type: str,
        *,
        title: str = "",
        provider_state: dict[str, Any] | None = None,
        anchor_id: str = "",
        placement: str = "right",
        pane_id: str = "",
    ) -> WorkspacePane:
        provider = provider_type.strip().lower()
        if provider not in PANE_PROVIDER_TYPES:
            raise ValueError(f"Unknown workspace pane provider: {provider_type}")
        project = self.project(project_name)
        default_title, default_state = self._pane_defaults(provider)
        default_state.update(provider_state or {})
        pane = project.workspace.add_pane(
            provider,
            title=title or default_title,
            provider_state=default_state,
            anchor_id=anchor_id,
            placement=placement,
            pane_id=pane_id,
        )
        self._save_workspace_project(project, f"Added {pane.title} pane")
        return pane

    def ensure_codex_pane(
        self, project_name: str, *, new: bool = False
    ) -> tuple[WorkspacePane, bool]:
        project = self.project(project_name)
        session = self.sessions.current(project.session_key)
        account = (session.codex_account if session else "") or project.codex_account
        session_id = session.id if session else ""
        if not new:
            existing = next(
                (
                    pane
                    for pane in project.workspace.panes
                    if pane.provider_type == "codex"
                    and str(pane.provider_state.get("account") or "") == account
                    and str(pane.provider_state.get("session_id") or "")
                    == session_id
                ),
                None,
            )
            if existing is not None:
                return existing, False
        title, state = self._pane_defaults("codex", account=account)
        state["session_id"] = session_id
        pane = project.workspace.add_pane(
            "codex", title=title, provider_state=state
        )
        self._save_workspace_project(project, f"Added {pane.title} pane")
        return pane, True

    def update_workspace_pane(
        self,
        project_name: str,
        pane_id: str,
        *,
        title: str | None = None,
        provider_state: dict[str, Any] | None = None,
    ) -> WorkspacePane:
        project = self.project(project_name)
        pane = project.workspace.pane(pane_id)
        if pane is None:
            raise ValueError(f"Unknown workspace pane: {pane_id}")
        if title is not None:
            pane.title = title.strip() or pane.provider_type.title()
        if provider_state is not None:
            pane.provider_state.update(provider_state)
        self._save_workspace_project(project)
        return pane

    def close_workspace_pane(
        self, project_name: str, pane_id: str
    ) -> WorkspacePane:
        project = self.project(project_name)
        pane = project.workspace.close_pane(pane_id)
        if pane is None:
            raise ValueError(f"Unknown workspace pane: {pane_id}")
        self._save_workspace_project(project, f"Closed {pane.title} pane")
        return pane

    def move_workspace_pane(
        self,
        project_name: str,
        pane_id: str,
        anchor_id: str,
        placement: str,
    ) -> ProjectWorkspace:
        project = self.project(project_name)
        project.workspace.move_pane(pane_id, anchor_id, placement)
        self._save_workspace_project(project)
        return project.workspace

    def set_pane_docked(
        self, project_name: str, pane_id: str, docked: bool
    ) -> WorkspacePane:
        project = self.project(project_name)
        pane = (
            project.workspace.dock_pane(pane_id)
            if docked
            else project.workspace.undock_pane(pane_id)
        )
        self._save_workspace_project(
            project, f"{'Docked' if docked else 'Undocked'} {pane.title}"
        )
        return pane

    def focus_workspace_pane(
        self, project_name: str, pane_id: str = ""
    ) -> ProjectWorkspace:
        project = self.project(project_name)
        project.workspace.focus(pane_id)
        self._save_workspace_project(project)
        return project.workspace

    def save_workspace_layout(
        self, project_name: str, layout: object
    ) -> ProjectWorkspace:
        project = self.project(project_name)
        from .workspace import SplitLayout

        project.workspace.layout = SplitLayout.from_value(layout)
        project.workspace._normalize_layout()
        self._save_workspace_project(project)
        return project.workspace
