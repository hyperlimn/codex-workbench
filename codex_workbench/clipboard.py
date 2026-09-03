from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass
from typing import Callable, Mapping, Protocol, Sequence

from .platform import PlatformBackend


@dataclass(frozen=True)
class ClipboardAttempt:
    helper: str
    error: str


@dataclass(frozen=True)
class ClipboardResult:
    copied: bool
    session_type: str
    helper: str = ""
    attempts: tuple[ClipboardAttempt, ...] = ()

    @property
    def error_summary(self) -> str:
        if self.attempts:
            return "; ".join(
                f"{attempt.helper}: {attempt.error}" for attempt in self.attempts
            )
        if self.session_type == "disabled":
            return "clipboard integration is disabled"
        return "no compatible clipboard helper was available"


class ClipboardBackend(Protocol):
    def copy(self, text: str) -> ClipboardResult:
        ...


class UnsupportedClipboardBackend:
    def __init__(self, platform_name: str):
        self.platform_name = platform_name

    def copy(self, _text: str) -> ClipboardResult:
        return ClipboardResult(False, f"unsupported:{self.platform_name}")


def detect_desktop_session(environ: Mapping[str, str] | None = None) -> str:
    env = os.environ if environ is None else environ
    declared = env.get("XDG_SESSION_TYPE", "").strip().casefold()
    wayland_display = env.get("WAYLAND_DISPLAY", "").strip()
    x11_display = env.get("DISPLAY", "").strip()

    # An explicit X11 session wins over a stale WAYLAND_DISPLAY inherited from
    # another process. This is the failure mode that motivated this adapter.
    if declared == "x11" and x11_display:
        return "x11"
    if declared == "wayland" and wayland_display:
        return "wayland"
    if wayland_display and declared != "x11":
        return "wayland"
    if x11_display:
        return "x11"
    return declared if declared in {"wayland", "x11", "tty"} else "none"


class ClipboardService:
    def __init__(
        self,
        *,
        environ: Mapping[str, str] | None = None,
        which: Callable[[str], str | None] = shutil.which,
        runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
        timeout: float = 3.0,
    ):
        self.environ = dict(os.environ if environ is None else environ)
        self.which = which
        self.runner = runner
        self.timeout = timeout

    def _candidates(self, session_type: str) -> Sequence[list[str]]:
        x11 = (
            ["xclip", "-selection", "clipboard"],
            ["xsel", "--clipboard", "--input"],
        )
        if session_type == "wayland":
            candidates: list[list[str]] = []
            if self.environ.get("WAYLAND_DISPLAY"):
                candidates.append(["wl-copy"])
            # XWayland is a useful fallback if the native helper fails.
            if self.environ.get("DISPLAY"):
                candidates.extend(x11)
            return candidates
        if session_type == "x11" and self.environ.get("DISPLAY"):
            return x11
        return ()

    def copy(self, text: str) -> ClipboardResult:
        session_type = detect_desktop_session(self.environ)
        attempts: list[ClipboardAttempt] = []
        for command in self._candidates(session_type):
            helper = command[0]
            try:
                installed = self.which(helper)
            except Exception as error:
                attempts.append(
                    ClipboardAttempt(helper, f"helper discovery failed: {error}")
                )
                continue
            if not installed:
                continue
            try:
                result = self.runner(
                    command,
                    input=text,
                    text=True,
                    capture_output=True,
                    check=False,
                    timeout=self.timeout,
                    env=self.environ,
                )
            except subprocess.TimeoutExpired:
                attempts.append(ClipboardAttempt(helper, "timed out"))
                continue
            except (OSError, subprocess.SubprocessError) as error:
                attempts.append(ClipboardAttempt(helper, str(error)))
                continue
            except Exception as error:
                # Clipboard integration is optional and must never abort the
                # context-producing action.
                attempts.append(ClipboardAttempt(helper, str(error)))
                continue
            if result.returncode == 0:
                return ClipboardResult(
                    True, session_type, helper, tuple(attempts)
                )
            detail = (result.stderr or result.stdout or "").strip()
            attempts.append(
                ClipboardAttempt(
                    helper,
                    detail.splitlines()[0] if detail else f"exit {result.returncode}",
                )
            )
        return ClipboardResult(False, session_type, attempts=tuple(attempts))


def select_clipboard_backend(platform: PlatformBackend) -> ClipboardBackend:
    if platform.name == "linux":
        return ClipboardService(which=platform.executable)
    return UnsupportedClipboardBackend(platform.name)


def copy_text(text: str) -> bool:
    """Compatibility helper for callers of the original CLI function."""

    return ClipboardService().copy(text).copied
