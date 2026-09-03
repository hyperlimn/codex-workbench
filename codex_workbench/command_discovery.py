from __future__ import annotations

import json
import re
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from .models import Project


@dataclass(frozen=True)
class CommandSuggestion:
    name: str
    command: str
    description: str
    category: str
    working_directory: str = ""
    source: str = ""


class CommandSuggestionSource(Protocol):
    """Read-only discovery boundary. Sources may suggest but never persist/run."""

    def discover(
        self, root: Path, *, working_directory: str = ""
    ) -> tuple[CommandSuggestion, ...]:
        ...


class PackageJsonSource:
    def discover(
        self, root: Path, *, working_directory: str = ""
    ) -> tuple[CommandSuggestion, ...]:
        path = root / "package.json"
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return ()
        scripts = raw.get("scripts") if isinstance(raw, dict) else None
        if not isinstance(scripts, dict):
            return ()
        categories = {
            "test": "Tests",
            "build": "Build",
            "dev": "Development",
            "start": "Development",
            "lint": "Quality",
            "format": "Quality",
        }
        return tuple(
            CommandSuggestion(
                name=str(name),
                command=f"npm run {name}",
                description=f"Run package.json script: {value}",
                category=categories.get(str(name).split(":", 1)[0], "Scripts"),
                working_directory=working_directory,
                source="package.json",
            )
            for name, value in scripts.items()
            if str(name).strip() and isinstance(value, str)
        )


class MakefileSource:
    TARGET = re.compile(r"^([A-Za-z0-9][A-Za-z0-9_.-]*):(?:\s|$)")

    def discover(
        self, root: Path, *, working_directory: str = ""
    ) -> tuple[CommandSuggestion, ...]:
        path = next(
            (candidate for candidate in (root / "Makefile", root / "makefile") if candidate.is_file()),
            None,
        )
        if path is None:
            return ()
        try:
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            return ()
        ignored = {"all", ".PHONY"}
        targets: list[str] = []
        for line in lines:
            match = self.TARGET.match(line)
            if match and match.group(1) not in ignored and match.group(1) not in targets:
                targets.append(match.group(1))
        return tuple(
            CommandSuggestion(
                name=target,
                command=f"make {target}",
                description=f"Run Makefile target {target}",
                category="Make",
                working_directory=working_directory,
                source=path.name,
            )
            for target in targets[:40]
        )


class JustfileSource:
    RECIPE = re.compile(r"^([A-Za-z_][A-Za-z0-9_-]*)(?:\s+[^:=]+)?\s*:")

    def discover(
        self, root: Path, *, working_directory: str = ""
    ) -> tuple[CommandSuggestion, ...]:
        path = next(
            (candidate for candidate in (root / "justfile", root / "Justfile") if candidate.is_file()),
            None,
        )
        if path is None:
            return ()
        try:
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            return ()
        recipes: list[str] = []
        for line in lines:
            match = self.RECIPE.match(line)
            if match and match.group(1) not in recipes:
                recipes.append(match.group(1))
        return tuple(
            CommandSuggestion(
                name=recipe,
                command=f"just {recipe}",
                description=f"Run just recipe {recipe}",
                category="Just",
                working_directory=working_directory,
                source=path.name,
            )
            for recipe in recipes[:40]
        )


class PyprojectSource:
    def discover(
        self, root: Path, *, working_directory: str = ""
    ) -> tuple[CommandSuggestion, ...]:
        path = root / "pyproject.toml"
        try:
            raw = tomllib.loads(path.read_text(encoding="utf-8"))
        except (OSError, tomllib.TOMLDecodeError):
            return ()
        suggestions: list[CommandSuggestion] = []
        scripts = raw.get("project", {}).get("scripts", {})
        if isinstance(scripts, dict):
            for name, target in scripts.items():
                suggestions.append(
                    CommandSuggestion(
                        str(name),
                        str(name),
                        f"Run Python project script: {target}",
                        "Python",
                        working_directory,
                        "pyproject.toml",
                    )
                )
        tool = raw.get("tool", {})
        if isinstance(tool, dict) and "pytest" in tool:
            suggestions.append(
                CommandSuggestion(
                    "pytest",
                    "python3 -m pytest",
                    "Run the configured Python test suite",
                    "Tests",
                    working_directory,
                    "pyproject.toml",
                )
            )
        return tuple(suggestions)


class CargoSource:
    def discover(
        self, root: Path, *, working_directory: str = ""
    ) -> tuple[CommandSuggestion, ...]:
        if not (root / "Cargo.toml").is_file():
            return ()
        return (
            CommandSuggestion("cargo run", "cargo run", "Run the default Rust target", "Development", working_directory, "Cargo.toml"),
            CommandSuggestion("cargo test", "cargo test", "Run Rust tests", "Tests", working_directory, "Cargo.toml"),
            CommandSuggestion("cargo build", "cargo build", "Build Rust targets", "Build", working_directory, "Cargo.toml"),
        )


class DockerComposeSource:
    def discover(
        self, root: Path, *, working_directory: str = ""
    ) -> tuple[CommandSuggestion, ...]:
        names = (
            "compose.yaml",
            "compose.yml",
            "docker-compose.yaml",
            "docker-compose.yml",
        )
        if not any((root / name).is_file() for name in names):
            return ()
        return (
            CommandSuggestion("Compose up", "docker compose up", "Start project services visibly", "Containers", working_directory, "compose file"),
            CommandSuggestion("Compose build", "docker compose build", "Build project service images", "Build", working_directory, "compose file"),
        )


DEFAULT_SOURCES: tuple[CommandSuggestionSource, ...] = (
    PackageJsonSource(),
    MakefileSource(),
    JustfileSource(),
    PyprojectSource(),
    CargoSource(),
    DockerComposeSource(),
)


def discover_project_commands(
    project: Project,
    *,
    sources: tuple[CommandSuggestionSource, ...] = DEFAULT_SOURCES,
) -> tuple[CommandSuggestion, ...]:
    roots = [(project.path, "")]
    roots.extend(
        (associated.resolved_path, associated.label)
        for associated in project.associated_paths
        if associated.resolved_path.is_dir()
    )
    result: list[CommandSuggestion] = []
    seen: set[tuple[str, str]] = set()
    for root, label in roots:
        for source in sources:
            for suggestion in source.discover(root, working_directory=label):
                key = (suggestion.command, suggestion.working_directory.casefold())
                if key not in seen:
                    seen.add(key)
                    result.append(suggestion)
    return tuple(result)
