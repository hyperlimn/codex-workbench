from __future__ import annotations

import json
import os
import shutil
import tempfile
from pathlib import Path

from .models import Project
from .paths import projects_file


def write_json(path: Path, value: object) -> None:
    """Atomically write human-readable JSON."""

    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temp = Path(temp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(value, fh, indent=2, sort_keys=True)
            fh.write("\n")
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)


def load_projects(path: Path | None = None) -> dict[str, Project]:
    path = path or projects_file()
    if not path.exists():
        return {}
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError(f"Invalid project registry: {path}")
    projects = raw.get("projects", [])
    if not isinstance(projects, list):
        raise ValueError(f"Invalid project registry: {path}")
    result: dict[str, Project] = {}
    for item in projects:
        if not isinstance(item, dict):
            continue
        project = Project.from_dict(item)
        result[project.registry_id] = project
    return result


def save_projects(projects: dict[str, Project], path: Path | None = None) -> Path:
    path = path or projects_file()
    extras: dict[str, object] = {}
    previous_version = 0
    if path.exists():
        try:
            previous = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            previous = {}
        if isinstance(previous, dict):
            extras = {
                key: value
                for key, value in previous.items()
                if key not in {"version", "projects"}
            }
            try:
                previous_version = int(previous.get("version") or 0)
            except (TypeError, ValueError):
                previous_version = 0
    if previous_version in {1, 2}:
        backup = path.with_name(f"{path.name}.v0.3.1.bak")
        if not backup.exists():
            shutil.copy2(path, backup)
    if previous_version == 3:
        backup = path.with_name(f"{path.name}.v0.4.0.bak")
        if not backup.exists():
            shutil.copy2(path, backup)
    document = {
        **extras,
        "version": 4,
        "projects": [
            project.to_dict()
            for project in sorted(
                projects.values(), key=lambda item: item.name.lower()
            )
        ],
    }
    write_json(path, document)
    return path
