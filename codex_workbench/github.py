from __future__ import annotations

import re
import shutil
import subprocess
from dataclasses import dataclass
from urllib.parse import urlparse

from .platform import PlatformBackend, select_platform_backend

@dataclass(frozen=True)
class RemoteRepository:
    url: str
    host: str = ""
    owner: str = ""
    name: str = ""
    protocol: str = ""


@dataclass(frozen=True)
class GitHubAuthState:
    host: str
    account: str = ""
    authenticated: bool | None = None
    source: str = ""
    error: str = ""


def parse_remote_url(url: str) -> RemoteRepository:
    value = url.strip()
    if not value:
        return RemoteRepository(url)
    protocol = ""
    host = ""
    path = ""
    scp_match = re.match(
        r"^(?P<user>[^@/]+)@(?P<host>[^:]+):(?P<path>.+)$", value
    )
    if scp_match:
        protocol = "ssh"
        host = scp_match.group("host")
        path = scp_match.group("path")
    else:
        parsed = urlparse(value)
        if parsed.scheme and parsed.hostname:
            protocol = parsed.scheme
            host = parsed.hostname
            path = parsed.path
        else:
            return RemoteRepository(value)
    pieces = [piece for piece in path.strip("/").split("/") if piece]
    owner = pieces[-2] if len(pieces) >= 2 else ""
    name = pieces[-1] if pieces else ""
    if name.endswith(".git"):
        name = name[:-4]
    return RemoteRepository(value, host, owner, name, protocol)


def _strip_ansi(value: str) -> str:
    return re.sub(r"\x1b\[[0-9;]*m", "", value)


def _account_from_status(value: str, host: str) -> str:
    text = _strip_ansi(value)
    pattern = re.compile(
        rf"Logged in to\s+{re.escape(host)}\s+account\s+([^\s(]+)",
        re.IGNORECASE,
    )
    match = pattern.search(text)
    return match.group(1) if match else ""


class GitHubAdapter:
    """Optional GitHub CLI identity hint; Git credentials can still differ."""

    def __init__(self, *, platform: PlatformBackend | None = None):
        self.platform = platform or select_platform_backend(which=shutil.which)

    def detect_account(self, host: str = "github.com") -> GitHubAuthState:
        executable = self.platform.executable("gh")
        if not executable:
            return GitHubAuthState(
                host, source="gh", error="GitHub CLI is not installed"
            )
        commands = (
            [executable, "auth", "status", "--hostname", host, "--active"],
            [executable, "auth", "status", "--hostname", host],
        )
        last_error = ""
        for command in commands:
            try:
                result = subprocess.run(
                    command, text=True, capture_output=True, timeout=5
                )
            except (OSError, subprocess.SubprocessError) as error:
                return GitHubAuthState(host, source="gh", error=str(error))
            output = "\n".join((result.stdout, result.stderr))
            account = _account_from_status(output, host)
            if account:
                return GitHubAuthState(
                    host,
                    account=account,
                    authenticated=result.returncode == 0,
                    source="gh",
                )
            last_error = (result.stderr or result.stdout).strip()
        return GitHubAuthState(
            host,
            authenticated=False if last_error else None,
            source="gh",
            error=last_error.splitlines()[0] if last_error else "account unknown",
        )
