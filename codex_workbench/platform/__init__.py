from __future__ import annotations

import sys
from typing import Callable

from .base import (
    PlatformBackend,
    PlatformCapabilities,
    UnsupportedPlatformBackend,
    UnsupportedPlatformError,
)
from .linux import LinuxPlatformBackend


def select_platform_backend(
    platform_name: str | None = None,
    *,
    which: Callable[[str], str | None] | None = None,
) -> PlatformBackend:
    name = (platform_name or sys.platform).casefold()
    if name.startswith("linux"):
        return LinuxPlatformBackend(which=which)
    return UnsupportedPlatformBackend(name)


__all__ = [
    "LinuxPlatformBackend",
    "PlatformBackend",
    "PlatformCapabilities",
    "UnsupportedPlatformBackend",
    "UnsupportedPlatformError",
    "select_platform_backend",
]
