from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Callable
from urllib.parse import urlparse

from .platform import PlatformBackend, UnsupportedPlatformError, select_platform_backend


@dataclass(frozen=True)
class DesktopOpenResult:
    opened: bool
    url: str
    error: str = ""


class DesktopAdapter:
    """Replaceable OS boundary for opening safe external references."""

    def __init__(
        self,
        *,
        which: Callable[[str], str | None] = shutil.which,
        launcher: Callable[..., subprocess.Popen[bytes]] = subprocess.Popen,
        platform: PlatformBackend | None = None,
    ):
        self.which = which
        self.launcher = launcher
        self.platform = platform or select_platform_backend(which=which)

    def _launch(self, command: list[str], target: str) -> DesktopOpenResult:
        try:
            self.launcher(
                command,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
        except (OSError, subprocess.SubprocessError) as error:
            return DesktopOpenResult(False, target, str(error))
        return DesktopOpenResult(True, target)

    def open_url(self, url: str) -> DesktopOpenResult:
        parsed = urlparse(url.strip())
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            return DesktopOpenResult(False, url, "only http/https URLs are supported")
        try:
            command = self.platform.open_url_argv(url)
        except UnsupportedPlatformError as error:
            return DesktopOpenResult(False, url, str(error))
        return self._launch(command, url)

    def open_folder(self, path: Path) -> DesktopOpenResult:
        target = self.platform.normalize_path(path)
        if not target.is_dir():
            return DesktopOpenResult(False, str(target), "directory does not exist")
        try:
            command = self.platform.open_folder_argv(target)
        except UnsupportedPlatformError as error:
            return DesktopOpenResult(False, str(target), str(error))
        return self._launch(command, str(target))
