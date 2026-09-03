from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Protocol


class UnsupportedPlatformError(RuntimeError):
    pass


@dataclass(frozen=True)
class PlatformCapabilities:
    platform: str
    supported: bool
    external_terminal: bool = False
    embedded_terminal: bool = False
    clipboard: bool = False
    folder_chooser: bool = False
    open_folder: bool = False
    open_url: bool = False
    desktop_launcher: bool = False
    git: bool = False
    github_cli: bool = False
    detail: str = ""


class PlatformBackend(Protocol):
    name: str

    def capabilities(self) -> PlatformCapabilities:
        ...

    def executable(self, name: str) -> str | None:
        ...

    def normalize_path(self, value: str | os.PathLike[str]) -> Path:
        ...

    def open_url_argv(self, url: str) -> list[str]:
        ...

    def open_folder_argv(self, path: Path) -> list[str]:
        ...

    def shell_argv(
        self, environ: Mapping[str, str] | None = None
    ) -> list[str]:
        ...


class UnsupportedPlatformBackend:
    def __init__(self, name: str):
        self.name = name or "unknown"

    def capabilities(self) -> PlatformCapabilities:
        return PlatformCapabilities(
            platform=self.name,
            supported=False,
            detail=(
                f"{self.name} does not have a Codex Workbench platform backend. "
                "Linux is the supported runtime in v0.5."
            ),
        )

    def executable(self, _name: str) -> str | None:
        return None

    @staticmethod
    def normalize_path(value: str | os.PathLike[str]) -> Path:
        return Path(value).expanduser().resolve(strict=False)

    def _unsupported(self, action: str) -> UnsupportedPlatformError:
        return UnsupportedPlatformError(
            f"{action} is unavailable on {self.name}; "
            "Linux is the supported runtime in Codex Workbench v0.5."
        )

    def open_url_argv(self, _url: str) -> list[str]:
        raise self._unsupported("Opening URLs")

    def open_folder_argv(self, _path: Path) -> list[str]:
        raise self._unsupported("Opening folders")

    def shell_argv(
        self, _environ: Mapping[str, str] | None = None
    ) -> list[str]:
        raise self._unsupported("Shell integration")
