from __future__ import annotations

import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from .platform import PlatformBackend, select_platform_backend

@dataclass(frozen=True)
class GitFileChange:
    path: str
    category: str
    index_status: str = " "
    worktree_status: str = " "
    original_path: str = ""

    @property
    def staged(self) -> bool:
        return self.index_status not in {" ", "?"}

    @property
    def unstaged(self) -> bool:
        return self.worktree_status != " "


def parse_short_status(status: str) -> tuple[GitFileChange, ...]:
    """Turn porcelain-v1 short output into display/staging choices."""

    changes: list[GitFileChange] = []
    for line in status.splitlines():
        if len(line) < 3:
            continue
        index_status, worktree_status = line[0], line[1]
        payload = line[3:]
        original_path = ""
        path = payload
        if " -> " in payload and (index_status == "R" or worktree_status == "R"):
            original_path, path = payload.split(" -> ", 1)
        statuses = {index_status, worktree_status}
        if "?" in statuses:
            category = "untracked"
        elif "D" in statuses:
            category = "deleted"
        elif "A" in statuses:
            category = "added"
        elif "R" in statuses:
            category = "renamed"
        elif "M" in statuses:
            category = "modified"
        else:
            category = "changed"
        changes.append(
            GitFileChange(
                path=path,
                category=category,
                index_status=index_status,
                worktree_status=worktree_status,
                original_path=original_path,
            )
        )
    return tuple(changes)


@dataclass
class GitState:
    directory_exists: bool = False
    is_repository: bool = False
    repository_root: str = ""
    branch: str = ""
    head: str = ""
    head_short: str = ""
    status: str = ""
    user_name: str = ""
    user_email: str = ""
    local_user_name: str = ""
    local_user_email: str = ""
    remote_name: str = "origin"
    remote_url: str = ""
    remotes: dict[str, str] = field(default_factory=dict)
    upstream: str = ""
    ahead: int | None = None
    behind: int | None = None

    @property
    def clean(self) -> bool:
        return not self.status.strip()

    @property
    def detached(self) -> bool:
        return self.is_repository and bool(self.head) and not self.branch

    @property
    def identity_source(self) -> str:
        local_fields = bool(self.local_user_name), bool(self.local_user_email)
        if all(local_fields):
            return "repository-local"
        if any(local_fields):
            return "mixed local/inherited"
        if self.user_name or self.user_email:
            return "inherited"
        return "missing"

    @property
    def changed_files(self) -> list[str]:
        return [line for line in self.status.splitlines() if line.strip()]

    @property
    def file_changes(self) -> tuple[GitFileChange, ...]:
        return parse_short_status(self.status)


def run_git(
    path: Path,
    *args: str,
    check: bool = False,
    platform: PlatformBackend | None = None,
) -> subprocess.CompletedProcess[str]:
    backend = platform or select_platform_backend()
    executable = backend.executable("git")
    command = [executable or "git", "-C", str(path), *args]
    if not executable:
        error = (
            f"Git executable is unavailable on {backend.capabilities().platform}"
        )
        if check:
            raise subprocess.CalledProcessError(
                127,
                command,
                output="",
                stderr=error,
            )
        return subprocess.CompletedProcess(command, 127, "", error)
    try:
        return subprocess.run(
            command,
            text=True,
            capture_output=True,
            check=check,
        )
    except OSError as error:
        if check:
            raise subprocess.CalledProcessError(
                127, command, output="", stderr=str(error)
            ) from error
        return subprocess.CompletedProcess(command, 127, "", str(error))


def value(path: Path, *args: str) -> str:
    result = run_git(path, *args)
    return result.stdout.strip() if result.returncode == 0 else ""


def inspect(path: Path, remote: str = "origin") -> GitState:
    state = GitState(directory_exists=path.is_dir(), remote_name=remote)
    if not state.directory_exists:
        return state
    state.is_repository = (
        value(path, "rev-parse", "--is-inside-work-tree").casefold() == "true"
    )
    if not state.is_repository:
        return state

    state.repository_root = value(path, "rev-parse", "--show-toplevel")
    state.branch = value(path, "symbolic-ref", "--quiet", "--short", "HEAD")
    state.head = value(path, "rev-parse", "HEAD")
    state.head_short = value(path, "rev-parse", "--short", "HEAD")
    state.status = value(path, "status", "--short")
    state.local_user_name = value(path, "config", "--local", "--get", "user.name")
    state.local_user_email = value(path, "config", "--local", "--get", "user.email")
    state.user_name = value(path, "config", "--get", "user.name")
    state.user_email = value(path, "config", "--get", "user.email")
    state.remote_url = value(path, "remote", "get-url", remote)
    for name in value(path, "remote").splitlines():
        url = value(path, "remote", "get-url", name)
        if url:
            state.remotes[name] = url
    state.upstream = value(
        path, "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{upstream}"
    )
    if state.upstream:
        counts = value(
            path, "rev-list", "--left-right", "--count", "HEAD...@{upstream}"
        ).split()
        if len(counts) == 2:
            try:
                state.ahead, state.behind = int(counts[0]), int(counts[1])
            except ValueError:
                pass
    return state


def set_local_identity(path: Path, name: str, email: str) -> None:
    if name:
        run_git(path, "config", "--local", "user.name", name, check=True)
    if email:
        run_git(path, "config", "--local", "user.email", email, check=True)


def working_diff(path: Path, *, staged: bool = False) -> str:
    args = ["diff"]
    if staged:
        args.append("--cached")
    result = run_git(path, *args)
    return result.stdout if result.returncode == 0 else ""


def staged_changes(path: Path) -> str:
    return value(path, "diff", "--cached", "--name-status")
