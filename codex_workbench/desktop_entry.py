from __future__ import annotations

import os
import sys
import tempfile
from dataclasses import dataclass
from importlib import resources
from pathlib import Path

from .paths import desktop_data_root
from .platform import PlatformBackend, UnsupportedPlatformError, select_platform_backend


@dataclass(frozen=True)
class DesktopInstallResult:
    desktop_file: Path
    icon_file: Path
    command: str


def _atomic_write(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", dir=path.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as output:
            output.write(content)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _desktop_quote(value: str) -> str:
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def install_desktop_entry(
    *,
    data_home: Path | None = None,
    executable: str | None = None,
    platform: PlatformBackend | None = None,
) -> DesktopInstallResult:
    """Install a per-user launcher only after an explicit CLI action."""

    backend = platform or select_platform_backend()
    if not backend.capabilities().desktop_launcher:
        raise UnsupportedPlatformError(
            "Desktop launcher installation is only implemented by the Linux backend."
        )
    data_home = data_home or desktop_data_root()
    command_path = executable or backend.executable("codex-workbench")
    command = (
        _desktop_quote(command_path)
        if command_path
        else f"{_desktop_quote(sys.executable)} -m codex_workbench gui"
    )
    package = resources.files("codex_workbench.gui.resources")
    template = package.joinpath("codex-workbench.desktop.in").read_text(
        encoding="utf-8"
    )
    desktop = template.replace("@EXEC@", command)
    icon = package.joinpath("codex-workbench.svg").read_bytes()

    desktop_path = (
        data_home
        / "applications"
        / "io.github.codex_workbench.Workbench.desktop"
    )
    icon_path = (
        data_home
        / "icons"
        / "hicolor"
        / "scalable"
        / "apps"
        / "codex-workbench.svg"
    )
    _atomic_write(desktop_path, desktop.encode("utf-8"))
    desktop_path.chmod(0o755)
    _atomic_write(icon_path, icon)
    return DesktopInstallResult(desktop_path, icon_path, command)
