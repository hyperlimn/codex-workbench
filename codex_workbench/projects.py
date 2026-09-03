from __future__ import annotations

import re
from pathlib import Path

from .models import AssociatedPath, Project
from .paths import projects_file
from .store import load_projects, save_projects


class ProjectNotFound(LookupError):
    pass


class ProjectRegistry:
    """Persistent project definitions with an injectable path for tests/UI."""

    def __init__(self, path: Path | None = None):
        self.path = path or projects_file()

    @staticmethod
    def validate_name(name: str) -> str:
        value = name.strip()
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", value):
            raise ValueError(
                "Project names must start with a letter or number and contain "
                "only letters, numbers, dots, underscores, or hyphens."
            )
        return value

    @staticmethod
    def validate_display_name(name: str) -> str:
        value = name.strip()
        if not value or any(ord(character) < 32 for character in value):
            raise ValueError("Project display name cannot be empty or contain control characters.")
        if len(value) > 120:
            raise ValueError("Project display name cannot exceed 120 characters.")
        return value

    @staticmethod
    def validate_associated_paths(
        values: list[AssociatedPath] | tuple[AssociatedPath, ...],
        *,
        canonical: Path | None = None,
    ) -> list[AssociatedPath]:
        normalized: list[AssociatedPath] = []
        labels: set[str] = set()
        canonical_path = canonical.resolve(strict=False) if canonical else None
        for raw in values:
            associated = AssociatedPath.from_value(raw)
            if associated is None:
                raise ValueError("Associated paths require a label and path.")
            label = associated.label.strip()
            if not label or any(ord(character) < 32 for character in label):
                raise ValueError("Associated path labels cannot be empty or contain control characters.")
            label_key = label.casefold()
            if label_key in labels or label_key == "canonical":
                raise ValueError(f"Duplicate associated path label: {label}")
            labels.add(label_key)
            role = associated.role.strip().casefold() or "other"
            if not re.fullmatch(r"[a-z0-9][a-z0-9+/_-]*", role):
                raise ValueError(f"Invalid associated path role: {associated.role}")
            path = associated.resolved_path
            if path.exists() and not path.is_dir():
                raise ValueError(f"Associated path is not a directory: {path}")
            if canonical_path is not None and path == canonical_path:
                raise ValueError(
                    f"Associated path duplicates the canonical root: {path}"
                )
            normalized.append(
                AssociatedPath(
                    label=label,
                    path=str(path),
                    role=role,
                    open_shell=associated.open_shell,
                    required=associated.required,
                    extra=dict(associated.extra),
                )
            )
        return normalized

    def all(self) -> dict[str, Project]:
        return load_projects(self.path)

    def get(self, name: str) -> Project:
        projects = self.all()
        project = projects.get(name)
        if project is None:
            matches = [item for item in projects.values() if item.name == name]
            project = matches[0] if len(matches) == 1 else None
        if project is None:
            raise ProjectNotFound(f"Unknown project: {name}. Run: cwb projects")
        return project

    def save(self, project: Project) -> Path:
        project.registry_id = self.validate_name(
            project.registry_id or project.name
        )
        project.name = self.validate_display_name(project.name)
        project.associated_paths = self.validate_associated_paths(
            project.associated_paths,
            canonical=project.path,
        )
        projects = self.all()
        for key, existing in projects.items():
            if (
                key != project.registry_id
                and existing.name.casefold() == project.name.casefold()
            ):
                raise ValueError(
                    f"A project named {project.name} is already registered."
                )
        projects[project.registry_id] = project
        return save_projects(projects, self.path)

    def remove(self, name: str) -> tuple[Project, Path]:
        project = self.get(name)
        projects = self.all()
        del projects[project.registry_id]
        return project, save_projects(projects, self.path)
