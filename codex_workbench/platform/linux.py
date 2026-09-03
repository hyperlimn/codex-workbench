from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import Callable, Mapping

from .base import PlatformCapabilities, UnsupportedPlatformError


class LinuxPlatformBackend:
    name = "linux"

    def __init__(self, *, which: Callable[[str], str | None] | None = None):
        self.which = which or shutil.which

    def executable(self, name: str) -> str | None:
        return self.which(name)

    @staticmethod
    def normalize_path(value: str | os.PathLike[str]) -> Path:
        return Path(value).expanduser().resolve(strict=False)

    def capabilities(self) -> PlatformCapabilities:
        return PlatformCapabilities(
            platform=self.name,
            supported=True,
            external_terminal=any(
                self.executable(name)
                for name in ("tilix", "gnome-terminal", "x-terminal-emulator")
            ),
            # VTE is a GUI runtime capability and is filled in by the GTK
            # adapter; reporting False here avoids importing GI in the core.
            embedded_terminal=False,
            clipboard=bool(
                self.executable("wl-copy")
                or self.executable("xclip")
                or self.executable("xsel")
            ),
            folder_chooser=True,
            open_folder=bool(self.executable("xdg-open")),
            open_url=bool(self.executable("xdg-open")),
            desktop_launcher=True,
            git=bool(self.executable("git")),
            github_cli=bool(self.executable("gh")),
            detail="GTK4/libadwaita Linux backend",
        )

    def _xdg_open(self) -> str:
        executable = self.executable("xdg-open")
        if not executable:
            raise UnsupportedPlatformError("xdg-open is not installed")
        return executable

    def open_url_argv(self, url: str) -> list[str]:
        return [self._xdg_open(), url]

    def open_folder_argv(self, path: Path) -> list[str]:
        return [self._xdg_open(), str(path)]

    def shell_argv(
        self, environ: Mapping[str, str] | None = None
    ) -> list[str]:
        env = os.environ if environ is None else environ
        requested = env.get("SHELL", "").strip()
        if requested:
            candidate = Path(requested).expanduser()
            if candidate.is_absolute():
                return [str(candidate)]
            executable = self.executable(requested)
            if executable:
                return [executable]
        for candidate in ("bash", "sh"):
            executable = self.executable(candidate)
            if executable:
                return [executable]
        raise UnsupportedPlatformError("No supported login shell is installed")
