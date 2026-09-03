from __future__ import annotations

from pathlib import Path

from ..clipboard import ClipboardResult
from ..handoff import HandoffBundle
from ..github import parse_remote_url
from ..models import AssociatedPath, ChatGPTThread, Project, WorkSession
from ..preflight import PreflightReport
from ..services import (
    CommitPlan,
    CommitResult,
    CloneProjectResult,
    CopyAllResult,
    EditProjectResult,
    PushPlan,
    PushResult,
    ResumePlan,
    RemovalResult,
    ShellTarget,
    StatusResult,
    Workbench,
)
from ..settings import WorkbenchSettings
from ..transcripts import TranscriptCandidate
from .state import (
    AccountItem,
    ConfidenceItem,
    DashboardState,
    PaletteCommand,
    ProjectItem,
    WorkspaceState,
    account_item,
    project_item,
)
from .workspace_controller import WorkspaceControllerMixin


class SwitchboardController(WorkspaceControllerMixin):
    """Display-independent controller over the public Workbench facade."""

    ACTION_COMMANDS = (
        ("codex", "Open Codex", "Ctrl+Enter"),
        ("shell", "Open shell", "Ctrl+Shift+Enter"),
        ("ready", "Ready / preflight", "Ctrl+Shift+R"),
        ("status", "Show status", ""),
        ("commit", "Commit selected files", ""),
        ("push", "Preview push", ""),
        ("copy", "Copy all context", "Ctrl+Shift+C"),
        ("handoff", "Create Codex handoff", "Ctrl+Shift+H"),
        ("resume", "Resume work session", ""),
        ("session", "Start or edit session", ""),
        ("edit-project", "Edit or remove project", ""),
    )

    def __init__(self, workbench: Workbench | None = None):
        self.workbench = workbench or Workbench()
        self._project_cache: tuple[ProjectItem, ...] = ()

    def project_items(self) -> tuple[ProjectItem, ...]:
        self._project_cache = tuple(
            project_item(overview)
            for overview in self.workbench.project_overviews()
        )
        return self._project_cache

    def initial_project(self, projects: tuple[ProjectItem, ...]) -> str:
        preferred = self.workbench.settings_snapshot().last_project
        selected = next(
            (
                item.name
                for item in projects
                if preferred in {item.name, item.registry_id}
            ),
            "",
        )
        if selected:
            return selected
        return projects[0].name if projects else ""

    def dashboard(self, *, query_codex: bool = False) -> DashboardState:
        projects = self.project_items()
        selected = self.initial_project(projects)
        workspace = (
            self.workspace(selected, query_codex=query_codex)
            if selected
            else None
        )
        return DashboardState(projects, selected, workspace)

    def select_project(
        self, project_name: str, *, query_codex: bool = True
    ) -> WorkspaceState:
        self.workbench.remember_project(project_name)
        return self.workspace(project_name, query_codex=query_codex)

    def workspace(
        self, project_name: str, *, query_codex: bool = True
    ) -> WorkspaceState:
        status = self.workbench.status(project_name, query_codex=query_codex)
        return WorkspaceState(
            status=status,
            confidence=self._confidence(status),
            threads=self.workbench.thread_references(project_name),
            activity=self.workbench.recent_activity(
                project=project_name, limit=20
            ),
        )

    def account_items(self, selected_account: str = "") -> tuple[AccountItem, ...]:
        settings = self.workbench.settings_snapshot()
        return tuple(
            account_item(
                status,
                selected_account=selected_account,
                low_threshold=settings.low_usage_threshold,
                critical_threshold=settings.critical_usage_threshold,
            )
            for status in self.workbench.account_statuses()
        )

    def select_account(
        self, project_name: str, account: str
    ) -> WorkspaceState:
        self.workbench.set_codex_account(project_name, account)
        return self.workspace(project_name)

    def register_project(
        self,
        name: str,
        directory: str,
        *,
        codex_account: str = "",
        github_account: str = "",
        associated_paths: list[AssociatedPath] | None = None,
    ) -> Project:
        return self.workbench.register_project(
            name,
            directory,
            codex_account=codex_account,
            github_account=github_account,
            associated_paths=associated_paths,
        ).project

    def clone_project(
        self,
        name: str,
        repository_url: str,
        destination_parent: str,
        destination_folder: str,
        *,
        codex_account: str = "",
        github_account: str = "",
        cancel=None,
        on_progress=None,
    ) -> CloneProjectResult:
        return self.workbench.clone_project(
            name,
            repository_url,
            destination_parent,
            destination_folder=destination_folder,
            codex_account=codex_account,
            github_account=github_account,
            cancel=cancel,
            on_progress=on_progress,
        )

    def edit_project(
        self,
        project_name: str,
        *,
        display_name: str,
        directory: str,
        codex_account: str,
        github_account: str,
        associated_paths: list[AssociatedPath],
        terminal_mode: str,
    ) -> EditProjectResult:
        return self.workbench.edit_project(
            project_name,
            display_name=display_name,
            directory=directory,
            codex_account=codex_account,
            github_account=github_account,
            associated_paths=associated_paths,
            terminal_mode=terminal_mode,
        )

    def remove_project(self, project_name: str) -> RemovalResult:
        return self.workbench.remove_project(project_name)

    def preflight(self, project_name: str) -> PreflightReport:
        return self.workbench.preflight(project_name)

    def status(self, project_name: str) -> StatusResult:
        return self.workbench.status(project_name)

    def copy_all(self, project_name: str) -> CopyAllResult:
        return self.workbench.copy_all(project_name)

    def prepare_copy_all(self, project_name: str) -> StatusResult:
        return self.workbench.prepare_copy_all(project_name)

    def complete_copy_all(
        self,
        status: StatusResult,
        clipboard: ClipboardResult,
    ) -> CopyAllResult:
        return self.workbench.complete_copy_all(status, clipboard)

    def clipboard_enabled(self) -> bool:
        return self.workbench.settings_snapshot().clipboard_mode != "disabled"

    def open_codex(self, project_name: str) -> int:
        return self.workbench.open_codex(
            project_name, in_terminal=True, detached=True
        )

    def shell_targets(self, project_name: str) -> tuple[ShellTarget, ...]:
        return self.workbench.shell_targets(project_name)

    def shell_cwd(self, project_name: str, target: str = "") -> Path:
        return self.workbench.shell_cwd(project_name, target)

    def open_shell(self, project_name: str, target: str = "") -> int:
        return self.workbench.open_shell(
            project_name,
            target=target,
            detached=True,
        )

    def open_folder(self, project_name: str, target: str = ""):
        return self.workbench.open_folder(project_name, target=target)

    def commit_plan(
        self, project_name: str, *, show_diff: bool = False
    ) -> CommitPlan:
        return self.workbench.plan_commit(
            project_name, show_diff=show_diff
        )

    def commit(
        self,
        project_name: str,
        message: str,
        *,
        files: tuple[str, ...] = (),
        stage_all: bool = False,
        allow_identity_mismatch: bool = False,
        plan: CommitPlan | None = None,
    ) -> CommitResult:
        return self.workbench.commit(
            project_name,
            message=message,
            files=files,
            stage_all=stage_all,
            allow_identity_mismatch=allow_identity_mismatch,
            plan=plan,
        )

    def push_plan(self, project_name: str) -> PushPlan:
        return self.workbench.plan_push(project_name)

    def push(
        self,
        project_name: str,
        plan: PushPlan,
        *,
        set_upstream: bool = False,
        allow_destination_mismatch: bool = False,
        allow_identity_mismatch: bool = False,
    ) -> PushResult:
        return self.workbench.push(
            project_name,
            confirmed=True,
            set_upstream=set_upstream,
            allow_destination_mismatch=allow_destination_mismatch,
            allow_identity_mismatch=allow_identity_mismatch,
            plan=plan,
        )

    def start_session(
        self,
        project_name: str,
        name: str,
        *,
        objective: str = "",
        next_action: str = "",
        codex_account: str = "",
        gpt_thread: str = "",
    ) -> WorkSession:
        return self.workbench.start_session(
            project_name,
            name,
            objective=objective,
            next_action=next_action,
            codex_account=codex_account,
            gpt_thread=gpt_thread,
        )

    def update_session(
        self, project_name: str, session_id: str, **changes: object
    ) -> WorkSession:
        return self.workbench.update_session(
            project_name, session_id, **changes
        )

    def sessions(self, project_name: str) -> tuple[WorkSession, ...]:
        project = self.workbench.project(project_name)
        return tuple(self.workbench.sessions.list(project.session_key))

    def save_session_context(
        self,
        project_name: str,
        session_id: str,
        changes: dict[str, object],
    ) -> WorkSession:
        values = dict(changes)
        name = str(values.pop("name", "")).strip()
        if session_id:
            return self.update_session(project_name, session_id, **values)
        session = self.start_session(
            project_name,
            name,
            objective=str(values.pop("objective", "")),
            next_action=str(values.pop("next_action", "")),
        )
        follow_up = {
            key: value
            for key, value in values.items()
            if value not in (None, "", [])
        }
        return (
            self.update_session(project_name, session.id, **follow_up)
            if follow_up
            else session
        )

    def save_settings(self, changes: dict[str, object]) -> WorkbenchSettings:
        return self.workbench.update_settings(**changes)

    def transcript_candidates(
        self, project_name: str, session_id: str | None = None
    ) -> tuple[TranscriptCandidate, ...]:
        return self.workbench.discover_transcripts(project_name, session_id)

    def handoff(
        self,
        project_name: str,
        *,
        to_account: str,
        transcript: Path | None = None,
        launch: bool = False,
    ) -> HandoffBundle:
        bundle = self.workbench.handoff(
            project_name,
            to_account=to_account,
            transcript=transcript,
        )
        if launch:
            self.workbench.launch_handoff(
                bundle, in_terminal=True, detached=True
            )
        return bundle

    def resume_plan(
        self, project_name: str, *, session_id: str | None = None
    ) -> ResumePlan:
        return self.workbench.resume(project_name, session_id=session_id)

    def resume_here(self, plan: ResumePlan) -> ResumePlan:
        return self.workbench.resume_here(plan)

    def resume_in_codex(self, plan: ResumePlan) -> int:
        return self.workbench.launch_resume(
            plan, in_terminal=True, detached=True
        )

    def add_thread(
        self, project_name: str, url: str, *, label: str = "", notes: str = ""
    ) -> ChatGPTThread:
        return self.workbench.add_thread(
            project_name, url, label=label, notes=notes
        )

    def open_thread(self, project_name: str, url: str):
        return self.workbench.open_thread(project_name, url)

    def copy_thread(self, project_name: str, url: str) -> ClipboardResult:
        return self.workbench.copy_thread(project_name, url)

    def update_memory(
        self,
        project_name: str,
        *,
        objective: str | None = None,
        instructions: list[str] | None = None,
    ) -> Project:
        return self.workbench.update_project_memory(
            project_name,
            objective=objective,
            instructions=instructions,
        )

    def palette(self, query: str = "") -> tuple[PaletteCommand, ...]:
        needle = query.casefold().strip()
        commands = [
            PaletteCommand(command, title, shortcut)
            for command, title, shortcut in self.ACTION_COMMANDS
        ]
        for item in self._project_cache or self.project_items():
            commands.append(
                PaletteCommand(
                    "switch-project",
                    f"Switch to {item.name}",
                    f"{item.account} · {item.branch}",
                    item.name,
                )
            )
        if needle:
            commands = [
                item
                for item in commands
                if needle in f"{item.title} {item.subtitle}".casefold()
            ]
        return tuple(commands)

    @staticmethod
    def _confidence(status: StatusResult) -> tuple[ConfidenceItem, ...]:
        project = status.project
        git = status.git

        directory = ConfidenceItem(
            "directory",
            "Directory",
            str(project.path),
            "available" if git.directory_exists else "does not exist",
            "valid" if git.directory_exists else "error",
        )
        repository = ConfidenceItem(
            "repository",
            "Git repo",
            git.repository_root or "Not detected",
            "",
            "valid" if git.is_repository else "error",
        )

        identity_value = " · ".join(
            value for value in (git.user_name, git.user_email) if value
        ) or "Not configured"
        identity_mismatch = (
            (project.git.name and project.git.name != git.user_name)
            or (project.git.email and project.git.email != git.user_email)
        )
        identity_tone = (
            "error"
            if identity_mismatch or not git.user_name or not git.user_email
            else "valid"
        )
        identity = ConfidenceItem(
            "identity",
            "Git identity",
            identity_value,
            (
                "configured expectation does not match"
                if identity_mismatch
                else git.identity_source
            ),
            identity_tone,
        )

        auth_account = (
            status.github_auth.account if status.github_auth else ""
        )
        github_mismatch = bool(
            project.github.account
            and auth_account
            and auth_account != project.github.account
        )
        remote_repository = parse_remote_url(git.remote_url)
        owner_mismatch = bool(
            project.github.owner
            and remote_repository.owner != project.github.owner
        )
        remote_mismatch = bool(
            project.repository.expected_url
            and project.repository.expected_url != git.remote_url
        )
        remote_tone = (
            "error"
            if (
                remote_mismatch
                or github_mismatch
                or owner_mismatch
                or not git.remote_url
            )
            else (
                "warning"
                if project.github.account and not auth_account
                else "valid"
            )
        )
        if remote_mismatch:
            remote_detail = "configured destination mismatch"
        elif github_mismatch:
            remote_detail = (
                f"GitHub CLI account {auth_account} differs from "
                f"{project.github.account}"
            )
        elif owner_mismatch:
            remote_detail = (
                f"remote owner {remote_repository.owner or '-'} differs "
                f"from {project.github.owner}"
            )
        elif project.github.account:
            remote_detail = f"expected account {project.github.account}"
        else:
            remote_detail = project.remote
        remote = ConfidenceItem(
            "remote",
            "GitHub / remote",
            git.remote_url or "Not configured",
            remote_detail,
            remote_tone,
        )

        account = (
            (status.session.codex_account if status.session else "")
            or project.codex_account
        )
        codex_tone = (
            "error"
            if not account or (status.codex and status.codex.error)
            else "valid"
        )
        codex = ConfidenceItem(
            "codex",
            "Codex account",
            account or "Not configured",
            status.codex.summary() if status.codex else "status not queried",
            codex_tone,
        )
        branch = ConfidenceItem(
            "branch",
            "Branch",
            git.branch or (
                f"detached at {git.head_short}" if git.detached else "Unknown"
            ),
            git.head_short,
            (
                "valid"
                if git.branch
                else ("warning" if git.detached else "error")
            ),
        )
        return (directory, repository, identity, remote, codex, branch)
