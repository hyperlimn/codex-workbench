from __future__ import annotations

from dataclasses import dataclass

from ..activity import ActivityRecord
from ..codex import CodexStatus
from ..models import ChatGPTThread, Project, WorkSession
from ..services import ProjectOverview, StatusResult


@dataclass(frozen=True)
class ProjectItem:
    name: str
    directory: str
    account: str
    branch: str
    session: str
    worktree: str
    warning: bool
    color: str = ""
    registry_id: str = ""


@dataclass(frozen=True)
class AccountItem:
    name: str
    five_hour_remaining: int | None
    weekly_remaining: int | None
    reset: str
    level: str
    selected: bool = False
    error: str = ""

    @property
    def five_hour_label(self) -> str:
        return (
            f"5h {self.five_hour_remaining}%"
            if self.five_hour_remaining is not None
            else "5h —"
        )

    @property
    def weekly_label(self) -> str:
        return (
            f"week {self.weekly_remaining}%"
            if self.weekly_remaining is not None
            else "week —"
        )


@dataclass(frozen=True)
class ConfidenceItem:
    key: str
    label: str
    value: str
    detail: str
    tone: str


@dataclass(frozen=True)
class WorkspaceState:
    status: StatusResult
    confidence: tuple[ConfidenceItem, ...]
    threads: tuple[ChatGPTThread, ...]
    activity: tuple[ActivityRecord, ...]

    @property
    def project(self) -> Project:
        return self.status.project

    @property
    def session(self) -> WorkSession | None:
        return self.status.session

    @property
    def account(self) -> str:
        return (
            (self.session.codex_account if self.session else "")
            or self.project.codex_account
        )

    @property
    def objective(self) -> str:
        return (
            (self.session.objective if self.session else "")
            or self.project.objective
        )


@dataclass(frozen=True)
class DashboardState:
    projects: tuple[ProjectItem, ...]
    selected_project: str
    workspace: WorkspaceState | None = None
    accounts: tuple[AccountItem, ...] = ()


@dataclass(frozen=True)
class PaletteCommand:
    id: str
    title: str
    subtitle: str = ""
    project: str = ""


def project_item(overview: ProjectOverview) -> ProjectItem:
    git = overview.git
    branch = git.branch or (f"@ {git.head_short}" if git.detached else "—")
    return ProjectItem(
        name=overview.project.name,
        directory=str(overview.project.path),
        account=overview.account or "unconfigured",
        branch=branch,
        session=overview.session.name if overview.session else "",
        worktree=(
            "unavailable"
            if not git.is_repository
            else ("clean" if git.clean else "modified")
        ),
        warning=overview.warning,
        color=overview.project.theme.color,
        registry_id=overview.project.registry_id,
    )


def account_item(
    status: CodexStatus,
    *,
    selected_account: str,
    low_threshold: int,
    critical_threshold: int,
) -> AccountItem:
    return AccountItem(
        name=status.account,
        five_hour_remaining=status.five_hour_remaining,
        weekly_remaining=status.weekly_remaining,
        reset=status.reset,
        level=status.usage_level(
            low_threshold=low_threshold,
            critical_threshold=critical_threshold,
        ),
        selected=status.account == selected_account,
        error=status.error,
    )
