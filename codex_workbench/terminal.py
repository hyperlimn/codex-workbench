from __future__ import annotations

import os
import shlex
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Protocol, Sequence

from .models import TerminalPreferences
from .platform import PlatformBackend, select_platform_backend


@dataclass(frozen=True)
class ShellBackendSelection:
    requested: str
    selected: str
    fallback_reason: str = ""


def select_shell_backend(
    requested: str,
    *,
    embedded_available: bool,
    external_available: bool,
) -> ShellBackendSelection:
    preference = requested if requested in {"embedded", "external"} else "embedded"
    if preference == "embedded" and embedded_available:
        return ShellBackendSelection(preference, "embedded")
    if external_available:
        reason = (
            "Embedded terminal support is unavailable; using the external terminal."
            if preference == "embedded"
            else ""
        )
        return ShellBackendSelection(preference, "external", reason)
    if embedded_available:
        return ShellBackendSelection(
            preference,
            "embedded",
            "No external terminal is available; using the embedded terminal.",
        )
    return ShellBackendSelection(
        preference,
        "unavailable",
        "Neither VTE nor a supported external terminal is available.",
    )


def shell_requires_rebind(
    bound_project: str,
    selected_project: str,
) -> bool:
    """Compatibility helper for the retired v0.4 single-PTY rebind policy."""

    return bool(
        bound_project
        and selected_project
        and bound_project != selected_project
    )


@dataclass(frozen=True)
class EmbeddedShellSpec:
    cwd: Path
    argv: tuple[str, ...]
    environment: tuple[str, ...] | None = None


def embedded_codex_environment(
    environ: Mapping[str, str] | None = None,
) -> tuple[str, ...]:
    """Preserve the host environment while preventing a nested launcher UI."""

    values = dict(os.environ if environ is None else environ)
    values["CODEX_START_HOSTED"] = "1"
    return tuple(
        f"{key}={value}" for key, value in sorted(values.items())
    )


class EmbeddedTerminalBackend(Protocol):
    @property
    def available(self) -> bool:
        ...

    @property
    def unavailable_reason(self) -> str:
        ...

    def create(self, spec: EmbeddedShellSpec) -> object:
        ...


def embedded_shell_spec(
    cwd: Path,
    *,
    platform: PlatformBackend | None = None,
    environ: Mapping[str, str] | None = None,
) -> EmbeddedShellSpec:
    target = cwd.expanduser().resolve(strict=False)
    if not target.is_dir():
        raise ValueError(f"Shell working directory does not exist: {target}")
    backend = platform or select_platform_backend()
    return EmbeddedShellSpec(target, tuple(backend.shell_argv(environ)))


def embedded_command_spec(
    cwd: Path,
    command: Sequence[str],
    *,
    environment: Sequence[str] | None = None,
) -> EmbeddedShellSpec:
    """Build a VTE launch spec while keeping cwd and argv structurally separate."""

    target = cwd.expanduser().resolve(strict=False)
    if not target.is_dir():
        raise ValueError(f"Command working directory does not exist: {target}")
    argv = tuple(str(value) for value in command if str(value))
    if not argv:
        raise ValueError("Embedded terminal command cannot be empty")
    return EmbeddedShellSpec(
        target, argv, tuple(environment) if environment else None
    )


class TerminalAdapter:
    """Replaceable launcher boundary; it is deliberately not an emulator."""

    def __init__(self, *, platform: PlatformBackend | None = None):
        # Passing shutil.which at construction keeps the long-standing test and
        # injection seam while routing discovery through the platform backend.
        self.platform = platform or select_platform_backend(which=shutil.which)

    @staticmethod
    def _preferences(
        preferences: TerminalPreferences | str,
    ) -> TerminalPreferences:
        return (
            TerminalPreferences(adapter=preferences)
            if isinstance(preferences, str)
            else preferences
        )

    @staticmethod
    def _run(command: list[str], cwd: Path, *, detached: bool) -> int:
        if detached:
            subprocess.Popen(
                command,
                cwd=cwd,
                start_new_session=True,
            )
            return 0
        return subprocess.call(command, cwd=cwd)

    def _emulator(self, preferred: str) -> tuple[str, str] | None:
        candidates = [preferred, "tilix", "gnome-terminal", "x-terminal-emulator"]
        seen: set[str] = set()
        for name in candidates:
            if not name or name in seen:
                continue
            seen.add(name)
            executable = self.platform.executable(name)
            if executable:
                return name, executable
        return None

    def open_shell(
        self,
        cwd: Path,
        preferences: TerminalPreferences | str = "tilix",
        *,
        detached: bool = False,
    ) -> int:
        preferences = self._preferences(preferences)
        emulator = self._emulator(preferences.adapter)
        if emulator:
            name, executable = emulator
            if name == "tilix":
                command = [executable, f"--working-directory={cwd}"]
            elif name == "gnome-terminal":
                command = [executable, f"--working-directory={cwd}"]
            else:
                command = [executable]
            return self._run(command, cwd, detached=detached)
        if detached:
            raise RuntimeError("No supported terminal emulator is installed")
        shell = self.platform.shell_argv(os.environ)
        return subprocess.call(shell, cwd=cwd)

    def open_command(
        self,
        cwd: Path,
        command: Sequence[str],
        preferences: TerminalPreferences | str = "tilix",
        *,
        title: str = "",
        detached: bool = False,
    ) -> int:
        if not command:
            raise ValueError("Terminal command cannot be empty")
        preferences = self._preferences(preferences)
        emulator = self._emulator(preferences.adapter)
        if not emulator:
            raise RuntimeError("No supported terminal emulator is installed")
        name, executable = emulator
        if name == "tilix":
            launch = [executable, f"--working-directory={cwd}"]
            if title:
                launch.append(f"--title={title}")
            launch.append(f"--command={shlex.join(command)}")
        elif name == "gnome-terminal":
            launch = [executable, f"--working-directory={cwd}", "--", *command]
        else:
            launch = [executable, "-e", *command]
        return self._run(launch, cwd, detached=detached)


def open_terminal(cwd: Path, terminal: str = "tilix") -> int:
    return TerminalAdapter().open_shell(cwd, terminal)
