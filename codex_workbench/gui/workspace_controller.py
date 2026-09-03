from __future__ import annotations

from typing import Any


class WorkspaceControllerMixin:
    """GTK-free presentation forwarding for project workspace workflows."""

    def set_project_info_collapsed(self, project_name: str, value: bool):
        return self.workbench.set_project_info_collapsed(project_name, value)

    def hold_prompt(self, project_name: str, text: str) -> str:
        return self.workbench.hold_prompt(project_name, text)

    def clear_prompt_hold(self, project_name: str) -> None:
        self.workbench.clear_prompt_hold(project_name)

    def add_project_command(self, project_name: str, **values: str):
        return self.workbench.add_project_command(project_name, **values)

    def update_project_command(
        self, project_name: str, command_id: str, **values: str
    ):
        return self.workbench.update_project_command(
            project_name, command_id, **values
        )

    def remove_project_command(self, project_name: str, command_id: str):
        return self.workbench.remove_project_command(project_name, command_id)

    def command_suggestions(self, project_name: str):
        return self.workbench.command_suggestions(project_name)

    def project_command_target(self, project_name: str, command_id: str):
        return self.workbench.project_command_target(project_name, command_id)

    def add_workspace_pane(
        self,
        project_name: str,
        provider_type: str,
        **values: Any,
    ):
        return self.workbench.add_workspace_pane(
            project_name, provider_type, **values
        )

    def ensure_codex_pane(self, project_name: str, *, new: bool = False):
        return self.workbench.ensure_codex_pane(project_name, new=new)

    def update_workspace_pane(
        self, project_name: str, pane_id: str, **values: Any
    ):
        return self.workbench.update_workspace_pane(
            project_name, pane_id, **values
        )

    def close_workspace_pane(self, project_name: str, pane_id: str):
        return self.workbench.close_workspace_pane(project_name, pane_id)

    def move_workspace_pane(
        self,
        project_name: str,
        pane_id: str,
        anchor_id: str,
        placement: str,
    ):
        return self.workbench.move_workspace_pane(
            project_name, pane_id, anchor_id, placement
        )

    def set_pane_docked(
        self, project_name: str, pane_id: str, docked: bool
    ):
        return self.workbench.set_pane_docked(
            project_name, pane_id, docked
        )

    def focus_workspace_pane(self, project_name: str, pane_id: str = ""):
        return self.workbench.focus_workspace_pane(project_name, pane_id)

    def save_workspace_layout(self, project_name: str, layout: object):
        return self.workbench.save_workspace_layout(
            project_name, layout
        )
