from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .workspace import ProjectWorkspace


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _string_list(value: Any) -> list[str]:
    return [str(item) for item in value] if isinstance(value, list) else []


@dataclass
class GitIdentity:
    """An optional expected Git identity, not a repository snapshot."""

    name: str = ""
    email: str = ""
    # Compatibility alias for callers that used the v1 constructor.
    github_account: str = ""

    @property
    def configured(self) -> bool:
        return bool(self.name or self.email)


@dataclass
class GitHubIdentity:
    """Expected GitHub context; repository ownership may differ from account."""

    account: str = ""
    host: str = "github.com"
    owner: str = ""


@dataclass
class RepositorySettings:
    remote: str = "origin"
    expected_url: str = ""
    remotes: dict[str, str] = field(default_factory=dict)


@dataclass
class TerminalPreferences:
    adapter: str = "tilix"
    layout: str = ""
    mode: str = "external"


@dataclass
class ThemeMetadata:
    color: str = ""
    label: str = ""


@dataclass
class ChatGPTThread:
    url: str
    label: str = ""
    notes: str = ""

    @property
    def display_label(self) -> str:
        return self.label or self.url

    def to_dict(self) -> dict[str, str]:
        return {"url": self.url, "label": self.label, "notes": self.notes}

    @classmethod
    def from_value(cls, raw: object) -> "ChatGPTThread | None":
        if isinstance(raw, cls):
            return cls(raw.url, raw.label, raw.notes)
        if isinstance(raw, str):
            url = raw.strip()
            return cls(url) if url else None
        if not isinstance(raw, dict):
            return None
        url = str(raw.get("url") or raw.get("reference") or "").strip()
        if not url:
            return None
        return cls(
            url=url,
            label=str(raw.get("label") or ""),
            notes=str(raw.get("notes") or ""),
        )


ASSOCIATED_PATH_ROLES = (
    "source",
    "toolchain",
    "build",
    "docs",
    "assets",
    "data",
    "deploy",
    "secondary-repo",
    "other",
)


@dataclass
class AssociatedPath:
    """A labeled working root belonging to a conceptual project.

    Git state is deliberately detected at read/action time rather than trusted
    from persistence. The required flag only affects READY; optional missing
    roots are warnings and never make the canonical project unusable.
    """

    label: str
    path: str
    role: str = "other"
    open_shell: bool = True
    required: bool = False
    extra: dict[str, Any] = field(default_factory=dict, repr=False)

    @property
    def resolved_path(self) -> Path:
        return Path(self.path).expanduser().resolve(strict=False)

    def to_dict(self) -> dict[str, Any]:
        result = dict(self.extra)
        result.update(
            {
                "label": self.label,
                "path": self.path,
                "role": self.role,
                "open_shell": self.open_shell,
                "required": self.required,
            }
        )
        return result

    @classmethod
    def from_value(cls, raw: object) -> "AssociatedPath | None":
        if isinstance(raw, cls):
            return cls(
                raw.label,
                raw.path,
                raw.role,
                raw.open_shell,
                raw.required,
                dict(raw.extra),
            )
        if not isinstance(raw, dict):
            return None
        label = str(raw.get("label") or raw.get("name") or "").strip()
        path = str(raw.get("path") or "").strip()
        if not label or not path:
            return None
        known = {
            "label",
            "name",
            "path",
            "role",
            "type",
            "open_shell",
            "required",
        }
        return cls(
            label=label,
            path=path,
            role=str(raw.get("role") or raw.get("type") or "other").strip()
            or "other",
            open_shell=bool(raw.get("open_shell", True)),
            required=bool(raw.get("required", False)),
            extra={key: value for key, value in raw.items() if key not in known},
        )


@dataclass
class Project:
    name: str
    directory: str
    codex_account: str = ""
    git: GitIdentity = field(default_factory=GitIdentity)
    github: GitHubIdentity = field(default_factory=GitHubIdentity)
    repository: RepositorySettings = field(default_factory=RepositorySettings)
    gpt_threads: list[str] = field(default_factory=list)
    instructions: list[str] = field(default_factory=list)
    objective: str = ""
    terminal: TerminalPreferences = field(default_factory=TerminalPreferences)
    theme: ThemeMetadata = field(default_factory=ThemeMetadata)
    chatgpt_threads: list[ChatGPTThread] = field(default_factory=list)
    associated_paths: list[AssociatedPath] = field(default_factory=list)
    # Stable storage/session identity. The display name may be edited without
    # moving or destroying session history.
    registry_id: str = ""
    workspace: ProjectWorkspace = field(default_factory=ProjectWorkspace)
    extra: dict[str, Any] = field(default_factory=dict, repr=False)

    def __post_init__(self) -> None:
        # Accept objects constructed against the small v1 Python API too.
        if isinstance(self.git, dict):
            self.git = GitIdentity(
                name=str(self.git.get("name") or ""),
                email=str(self.git.get("email") or ""),
                github_account=str(self.git.get("github_account") or ""),
            )
        if isinstance(self.github, dict):
            self.github = GitHubIdentity(**self.github)
        if isinstance(self.repository, str):
            self.repository = RepositorySettings(remote=self.repository)
        elif isinstance(self.repository, dict):
            self.repository = RepositorySettings(**self.repository)
        if isinstance(self.terminal, str):
            self.terminal = TerminalPreferences(
                adapter=self.terminal, mode="external"
            )
        elif isinstance(self.terminal, dict):
            self.terminal = TerminalPreferences(
                adapter=str(self.terminal.get("adapter") or "tilix"),
                layout=str(self.terminal.get("layout") or ""),
                mode=str(self.terminal.get("mode") or "external"),
            )
        if isinstance(self.theme, str):
            self.theme = ThemeMetadata(color=self.theme)
        elif isinstance(self.theme, dict):
            self.theme = ThemeMetadata(**self.theme)
        if self.git.github_account and not self.github.account:
            self.github.account = self.git.github_account
        self.git.github_account = self.github.account
        self.gpt_threads = [str(item) for item in self.gpt_threads if str(item)]
        normalized_threads: list[ChatGPTThread] = []
        for raw_thread in self.chatgpt_threads:
            thread = ChatGPTThread.from_value(raw_thread)
            if thread is not None:
                normalized_threads.append(thread)
        self.chatgpt_threads = normalized_threads
        normalized_paths: list[AssociatedPath] = []
        for raw_path in self.associated_paths:
            associated = AssociatedPath.from_value(raw_path)
            if associated is not None:
                normalized_paths.append(associated)
        self.associated_paths = normalized_paths
        self.registry_id = self.registry_id.strip() or self.name
        self.workspace = ProjectWorkspace.from_value(self.workspace)
        if self.terminal.mode not in {"embedded", "external"}:
            self.terminal.mode = "external"

    @property
    def path(self) -> Path:
        return Path(self.directory).expanduser().resolve(strict=False)

    @property
    def session_key(self) -> str:
        return self.registry_id

    @property
    def remote(self) -> str:
        return self.repository.remote

    @property
    def github_account(self) -> str:
        return self.github.account

    @property
    def thread_references(self) -> list[ChatGPTThread]:
        """Structured threads plus compatibility URLs, de-duplicated by URL."""

        result: list[ChatGPTThread] = []
        seen: set[str] = set()
        for thread in [*self.chatgpt_threads, *map(ChatGPTThread, self.gpt_threads)]:
            if thread.url and thread.url not in seen:
                result.append(thread)
                seen.add(thread.url)
        return result

    def to_dict(self) -> dict[str, Any]:
        result = dict(self.extra)
        result.update({
            "id": self.registry_id,
            "name": self.name,
            "directory": self.directory,
            "codex_account": self.codex_account,
            "git": {"name": self.git.name, "email": self.git.email},
            "github": {
                "account": self.github.account,
                "host": self.github.host,
                "owner": self.github.owner,
            },
            "repository": {
                "remote": self.repository.remote,
                "expected_url": self.repository.expected_url,
                "remotes": dict(self.repository.remotes),
            },
            "gpt_threads": list(self.gpt_threads),
            "chatgpt_threads": [
                thread.to_dict() for thread in self.chatgpt_threads
            ],
            "instructions": list(self.instructions),
            "objective": self.objective,
            "terminal": {
                "adapter": self.terminal.adapter,
                "layout": self.terminal.layout,
                "mode": self.terminal.mode,
            },
            "theme": {"color": self.theme.color, "label": self.theme.label},
            "associated_paths": [
                associated.to_dict() for associated in self.associated_paths
            ],
            "workspace": self.workspace.to_dict(),
        })
        return result

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "Project":
        git_raw = raw.get("git") if isinstance(raw.get("git"), dict) else {}
        github_raw = raw.get("github")
        if isinstance(github_raw, str):
            github_raw = {"account": github_raw}
        if not isinstance(github_raw, dict):
            github_raw = {}
        github = GitHubIdentity(
            account=str(github_raw.get("account") or git_raw.get("github_account") or ""),
            host=str(github_raw.get("host") or "github.com"),
            owner=str(github_raw.get("owner") or ""),
        )
        repository_raw = raw.get("repository")
        if not isinstance(repository_raw, dict):
            repository_raw = {}
        raw_remotes = repository_raw.get("remotes")
        remotes = (
            {str(key): str(value) for key, value in raw_remotes.items()}
            if isinstance(raw_remotes, dict)
            else {}
        )
        repository = RepositorySettings(
            remote=str(repository_raw.get("remote") or raw.get("remote") or "origin"),
            expected_url=str(
                repository_raw.get("expected_url")
                or raw.get("expected_remote_url")
                or ""
            ),
            remotes=remotes,
        )
        terminal_raw = raw.get("terminal", "tilix")
        terminal = (
            TerminalPreferences(
                adapter=str(terminal_raw.get("adapter") or "tilix"),
                layout=str(terminal_raw.get("layout") or ""),
                mode=str(terminal_raw.get("mode") or "external"),
            )
            if isinstance(terminal_raw, dict)
            else TerminalPreferences(
                adapter=str(terminal_raw or "tilix"), mode="external"
            )
        )
        theme_raw = raw.get("theme")
        theme = (
            ThemeMetadata(
                color=str(theme_raw.get("color") or ""),
                label=str(theme_raw.get("label") or ""),
            )
            if isinstance(theme_raw, dict)
            else ThemeMetadata(color=str(theme_raw or ""))
        )
        structured_threads = []
        raw_structured_threads = raw.get("chatgpt_threads")
        if isinstance(raw_structured_threads, list):
            structured_threads = [
                thread
                for item in raw_structured_threads
                if (thread := ChatGPTThread.from_value(item)) is not None
            ]
        raw_associated = raw.get("associated_paths")
        associated_paths = (
            [
                associated
                for item in raw_associated
                if (associated := AssociatedPath.from_value(item)) is not None
            ]
            if isinstance(raw_associated, list)
            else []
        )
        known_fields = {
            "id",
            "name",
            "directory",
            "codex_account",
            "git",
            "github",
            "repository",
            "remote",
            "expected_remote_url",
            "gpt_threads",
            "chatgpt_threads",
            "instructions",
            "objective",
            "terminal",
            "theme",
            "associated_paths",
            "workspace",
        }
        return cls(
            name=str(raw["name"]),
            directory=str(raw["directory"]),
            codex_account=str(raw.get("codex_account") or ""),
            git=GitIdentity(
                name=str(git_raw.get("name") or ""),
                email=str(git_raw.get("email") or ""),
                github_account=github.account,
            ),
            github=github,
            repository=repository,
            gpt_threads=_string_list(raw.get("gpt_threads")),
            instructions=_string_list(raw.get("instructions")),
            objective=str(raw.get("objective") or ""),
            terminal=terminal,
            theme=theme,
            chatgpt_threads=structured_threads,
            associated_paths=associated_paths,
            registry_id=str(raw.get("id") or raw["name"]),
            workspace=ProjectWorkspace.from_value(raw.get("workspace")),
            extra={
                key: value for key, value in raw.items() if key not in known_fields
            },
        )


@dataclass
class HandoffRecord:
    id: str
    created_at: str
    from_codex_account: str = ""
    to_codex_account: str = ""
    branch: str = ""
    head: str = ""
    handoff_path: str = ""
    transcript_path: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            field_name: getattr(self, field_name)
            for field_name in self.__dataclass_fields__
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "HandoffRecord":
        return cls(
            **{
                field_name: str(raw.get(field_name) or "")
                for field_name in cls.__dataclass_fields__
            }
        )


@dataclass
class WorkSession:
    id: str
    name: str
    project: str
    objective: str = ""
    codex_account: str = ""
    gpt_thread: str = ""
    branch: str = ""
    starting_head: str = ""
    current_head: str = ""
    created_at: str = field(default_factory=utc_now)
    updated_at: str = field(default_factory=utc_now)
    handoff_history: list[HandoffRecord] = field(default_factory=list)
    transcript_paths: list[str] = field(default_factory=list)
    completed: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    current_state: str = ""
    current_problem: str = ""
    next_action: str = ""
    status: str = "active"

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": 1,
            "id": self.id,
            "name": self.name,
            "project": self.project,
            "objective": self.objective,
            "codex_account": self.codex_account,
            "gpt_thread": self.gpt_thread,
            "branch": self.branch,
            "starting_head": self.starting_head,
            "current_head": self.current_head,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "handoff_history": [
                record.to_dict() for record in self.handoff_history
            ],
            "transcript_paths": list(self.transcript_paths),
            "completed": list(self.completed),
            "notes": list(self.notes),
            "current_state": self.current_state,
            "current_problem": self.current_problem,
            "next_action": self.next_action,
            "status": self.status,
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "WorkSession":
        raw_history = raw.get("handoff_history")
        history = raw_history if isinstance(raw_history, list) else []
        return cls(
            id=str(raw["id"]),
            name=str(raw.get("name") or raw["id"]),
            project=str(raw["project"]),
            objective=str(raw.get("objective") or ""),
            codex_account=str(raw.get("codex_account") or ""),
            gpt_thread=str(raw.get("gpt_thread") or ""),
            branch=str(raw.get("branch") or ""),
            starting_head=str(raw.get("starting_head") or ""),
            current_head=str(raw.get("current_head") or ""),
            created_at=str(raw.get("created_at") or utc_now()),
            updated_at=str(
                raw.get("updated_at") or raw.get("created_at") or utc_now()
            ),
            handoff_history=[
                HandoffRecord.from_dict(item)
                for item in history or []
                if isinstance(item, dict)
            ],
            transcript_paths=_string_list(raw.get("transcript_paths")),
            completed=_string_list(raw.get("completed")),
            notes=_string_list(raw.get("notes")),
            current_state=str(raw.get("current_state") or ""),
            current_problem=str(raw.get("current_problem") or ""),
            next_action=str(raw.get("next_action") or ""),
            status=str(raw.get("status") or "active"),
        )
