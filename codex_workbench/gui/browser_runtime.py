from __future__ import annotations

import shutil
import subprocess
from functools import lru_cache


@lru_cache(maxsize=2)
def browser_runtime_capability(
    webkit_installed: bool,
) -> tuple[bool, str]:
    if not webkit_installed:
        return (
            False,
            "WebKitGTK introspection (gir1.2-webkit-6.0) is not installed. "
            "Browser panes are disabled; Workbench startup remains available.",
        )
    bubblewrap = shutil.which("bwrap")
    if not bubblewrap:
        return True, "WebKitGTK project web surface"
    try:
        result = subprocess.run(
            [
                bubblewrap,
                "--ro-bind",
                "/",
                "/",
                "--proc",
                "/proc",
                "--dev",
                "/dev",
                "--unshare-all",
                "--share-net",
                "true",
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=3,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        result = None
    if result is None or result.returncode != 0:
        return (
            False,
            "WebKitGTK is installed, but its bubblewrap process sandbox cannot "
            "start in this environment. Browser panes are disabled safely.",
        )
    return True, "WebKitGTK project web surface"
