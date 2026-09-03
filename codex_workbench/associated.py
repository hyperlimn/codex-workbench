from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .git import GitState, inspect
from .models import AssociatedPath, Project


@dataclass(frozen=True)
class AssociatedPathState:
    associated: AssociatedPath
    git: GitState

    @property
    def path(self) -> Path:
        return self.associated.resolved_path

    @property
    def exists(self) -> bool:
        return self.git.directory_exists

    @property
    def summary(self) -> str:
        if not self.exists:
            return "missing"
        if not self.git.is_repository:
            return "directory · not a Git repository"
        branch = self.git.branch or (
            f"detached at {self.git.head_short}" if self.git.detached else "branch unknown"
        )
        worktree = "clean" if self.git.clean else "modified"
        return f"Git {branch} · {worktree}"


def inspect_associated_paths(project: Project) -> tuple[AssociatedPathState, ...]:
    return tuple(
        AssociatedPathState(
            associated,
            inspect(associated.resolved_path),
        )
        for associated in project.associated_paths
    )


def resolve_project_path(
    project: Project,
    target: str = "",
    *,
    require_shell: bool = False,
) -> Path:
    value = target.strip()
    if not value or value.casefold() == "canonical":
        return project.path
    matches = [
        associated
        for associated in project.associated_paths
        if associated.label.casefold() == value.casefold()
        or str(associated.resolved_path) == value
    ]
    if len(matches) != 1:
        raise ValueError(f"Unknown associated path for {project.name}: {target}")
    associated = matches[0]
    if require_shell and not associated.open_shell:
        raise ValueError(
            f"Shell access is disabled for associated path {associated.label}."
        )
    return associated.resolved_path
