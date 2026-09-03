from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from dataclasses import replace
from hashlib import sha256
from pathlib import Path
from threading import Event
from typing import Callable, Sequence
from urllib.parse import urlparse

from .activity import ActivityRecord, ActivityStore
from .associated import (
    AssociatedPathState,
    inspect_associated_paths,
    resolve_project_path,
)
from .clipboard import (
    ClipboardBackend,
    ClipboardResult,
    select_clipboard_backend,
)
from .clone import (
    CloneProgress,
    CloneRequest,
    CloneResult,
    GitCloneService,
    infer_repository_name,
)
from .codex import CodexAdapter, CodexStatus
from .desktop import DesktopAdapter, DesktopOpenResult
from .context import render as render_context
from .git import (
    GitState,
    inspect,
    run_git,
    set_local_identity,
    staged_changes,
    working_diff,
)
from .git import GitFileChange
from .github import GitHubAdapter, GitHubAuthState, parse_remote_url
from .handoff import HandoffBundle, HandoffService, continuation_instruction
from .models import AssociatedPath, ChatGPTThread, Project, WorkSession
from .platform import PlatformBackend, PlatformCapabilities, select_platform_backend
from .preflight import PreflightReport, build_preflight
from .projects import ProjectRegistry
from .sessions import SessionStore
from .settings import SettingsStore, WorkbenchSettings
from .transcripts import (
    ProjectTranscriptDiscovery,
    TranscriptCandidate,
    TranscriptDiscoveryAdapter,
)
from .terminal import TerminalAdapter
from .workspace_service import WorkspaceServiceMixin


class WorkbenchError(RuntimeError):
    def __init__(self, message: str, exit_code: int = 2):
        super().__init__(message)
        self.exit_code = exit_code


def _commit_snapshot(path: Path) -> str:
    """Fingerprint staged, tracked, and untracked content for a commit review."""

    digest = sha256()

    def add(value: str) -> None:
        encoded = value.encode("utf-8", errors="surrogateescape")
        digest.update(len(encoded).to_bytes(8, "big"))
        digest.update(encoded)

    for args in (
        ("diff", "--no-ext-diff", "--no-textconv", "--binary"),
        (
            "diff",
            "--cached",
            "--no-ext-diff",
            "--no-textconv",
            "--binary",
        ),
    ):
        result = run_git(path, *args)
        add("\0".join(args))
        add(str(result.returncode))
        add(result.stdout)
        add(result.stderr)

    untracked = run_git(
        path,
        "ls-files",
        "--others",
        "--exclude-standard",
        "-z",
    )
    add(str(untracked.returncode))
    add(untracked.stdout)
    add(untracked.stderr)
    for relative_path in filter(None, untracked.stdout.split("\0")):
        blob = run_git(
            path,
            "hash-object",
            "--no-filters",
            "--",
            relative_path,
        )
        add(relative_path)
        add(str(blob.returncode))
        add(blob.stdout)
        add(blob.stderr)
    return digest.hexdigest()


@dataclass(frozen=True)
class RegistrationResult:
    project: Project
    config_path: Path
    local_identity_updated: bool
    git: GitState | None = None


@dataclass(frozen=True)
class EditProjectResult:
    project: Project
    config_path: Path
    git: GitState
    directory_changed: bool
    session_context_warning: bool


@dataclass(frozen=True)
class RemovalResult:
    project: Project
    config_path: Path
    preserved_sessions: int


@dataclass(frozen=True)
class CloneProjectResult:
    clone: CloneResult
    registration: RegistrationResult | None = None

    @property
    def project(self) -> Project | None:
        return self.registration.project if self.registration else None

    @property
    def registered(self) -> bool:
        return self.registration is not None


@dataclass(frozen=True)
class ShellTarget:
    label: str
    path: Path
    role: str = "canonical"
    canonical: bool = False


@dataclass(frozen=True)
class StatusResult:
    project: Project
    git: GitState
    session: WorkSession | None
    codex: CodexStatus | None
    github_auth: GitHubAuthState | None
    text: str
    associated: tuple[AssociatedPathState, ...] = ()


@dataclass(frozen=True)
class CopyAllResult:
    status: StatusResult
    clipboard: ClipboardResult


@dataclass(frozen=True)
class CommitResult:
    before: GitState
    diff: str
    staged: str
    output: str
    returncode: int
    committed: bool
    reason: str = ""


@dataclass(frozen=True)
class CommitPlan:
    project: Project
    git: GitState
    changes: tuple[GitFileChange, ...]
    diff: str
    blocking: tuple[str, ...]
    warnings: tuple[str, ...]
    snapshot: str = ""


@dataclass(frozen=True)
class ProjectOverview:
    project: Project
    git: GitState
    session: WorkSession | None

    @property
    def account(self) -> str:
        return (
            (self.session.codex_account if self.session else "")
            or self.project.codex_account
        )

    @property
    def warning(self) -> bool:
        return (
            not self.git.directory_exists
            or not self.git.is_repository
            or not self.git.user_name
            or not self.git.user_email
            or not self.account
        )


@dataclass(frozen=True)
class PushPlan:
    git: GitState
    remote: str
    remote_url: str
    branch: str
    upstream: str
    expected_github_account: str
    detected_github_account: str
    remote_owner: str
    blocking: tuple[str, ...]
    warnings: tuple[str, ...]

    @property
    def destination(self) -> str:
        return f"{self.remote}/{self.branch}" if self.branch else self.remote


@dataclass(frozen=True)
class PushResult:
    plan: PushPlan
    output: str
    returncode: int
    pushed: bool
    reason: str = ""


@dataclass(frozen=True)
class ResumePlan:
    project: Project
    session: WorkSession
    git: GitState
    account: str
    handoff_path: Path | None
    context: str
    warnings: tuple[str, ...]
    prompt: str


class Workbench(WorkspaceServiceMixin):
    """Reusable application service called by the CLI and native GUI."""

    def __init__(
        self,
        *,
        projects: ProjectRegistry | None = None,
        sessions: SessionStore | None = None,
        codex: CodexAdapter | None = None,
        terminal: TerminalAdapter | None = None,
        clipboard: ClipboardBackend | None = None,
        github: GitHubAdapter | None = None,
        settings: SettingsStore | None = None,
        activity: ActivityStore | None = None,
        transcripts: TranscriptDiscoveryAdapter | None = None,
        desktop: DesktopAdapter | None = None,
        clone: GitCloneService | None = None,
        platform: PlatformBackend | None = None,
    ):
        self.platform = platform or select_platform_backend()
        self.projects = projects or ProjectRegistry()
        self.sessions = sessions or SessionStore()
        self.settings_store = settings or SettingsStore(
            self.projects.path.parent / "settings.json"
        )
        self.settings = self.settings_store.load()
        self._managed_codex = codex is None
        self.codex = codex or CodexAdapter(self.settings.launcher_path or None)
        self.terminal = terminal or TerminalAdapter(platform=self.platform)
        self.clipboard = clipboard or select_clipboard_backend(self.platform)
        self.github = github or GitHubAdapter(platform=self.platform)
        self.activity = activity or ActivityStore(
            self.sessions.root.parent / "activity.json"
        )
        self.transcripts = transcripts or ProjectTranscriptDiscovery()
        self.desktop = desktop or DesktopAdapter(platform=self.platform)
        self.clone_service = clone or GitCloneService(
            # Empty explicitly disables Git discovery on unsupported backends;
            # None would ask GitCloneService to search the host PATH again.
            git_executable=self.platform.executable("git") or ""
        )
        self.handoffs = HandoffService(self.sessions)

    def project(self, name: str) -> Project:
        return self.projects.get(name)

    def _record(
        self,
        action: str,
        summary: str,
        *,
        project: str = "",
        session: str = "",
        account: str = "",
        detail: str = "",
        success: bool = True,
    ) -> None:
        try:
            self.activity.record(
                action,
                summary,
                project=project,
                session=session,
                account=account,
                detail=detail,
                success=success,
            )
        except OSError:
            # History is useful context, never a reason to break an action.
            pass

    def settings_snapshot(self) -> WorkbenchSettings:
        self.settings = self.settings_store.load()
        return self.settings

    def update_settings(self, **changes: object) -> WorkbenchSettings:
        self.settings = self.settings_store.update(**changes)
        if self._managed_codex:
            self.codex = CodexAdapter(self.settings.launcher_path or None)
        return self.settings

    def remember_project(self, name: str) -> Project:
        project = self.project(name)
        if self.settings.last_project != project.registry_id:
            self.settings.last_project = project.registry_id
            self.settings_store.save(self.settings)
        return project

    def project_overviews(self) -> tuple[ProjectOverview, ...]:
        overviews = []
        for project in self.projects.all().values():
            overviews.append(
                ProjectOverview(
                    project,
                    inspect(project.path, project.remote),
                    self.sessions.current(project.session_key),
                )
            )
        return tuple(overviews)

    def account_statuses(self) -> tuple[CodexStatus, ...]:
        accounts = self.codex.list_accounts()
        if not accounts:
            return ()
        with ThreadPoolExecutor(
            max_workers=min(4, len(accounts)),
            thread_name_prefix="cwb-codex-status",
        ) as executor:
            return tuple(executor.map(self.codex.read_status, accounts))

    def set_codex_account(self, project_name: str, account: str) -> Project:
        project = self.project(project_name)
        selected = account.strip()
        if not selected:
            raise WorkbenchError("Codex account cannot be empty")
        session = self.sessions.current(project.session_key)
        if session:
            session.codex_account = selected
            self.sessions.save(session)
            session_id = session.id
        else:
            project.codex_account = selected
            self.projects.save(project)
            session_id = ""
        self._record(
            "account_selected",
            f"Selected Codex account {selected}",
            project=project.name,
            session=session_id,
            account=selected,
        )
        return project

    def thread_references(self, project_name: str) -> tuple[ChatGPTThread, ...]:
        return tuple(self.project(project_name).thread_references)

    def add_thread(
        self, project_name: str, url: str, *, label: str = "", notes: str = ""
    ) -> ChatGPTThread:
        project = self.project(project_name)
        value = url.strip()
        parsed = urlparse(value)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise WorkbenchError("Thread reference must be an http/https URL")
        existing = next(
            (item for item in project.thread_references if item.url == value),
            None,
        )
        if existing:
            return existing
        thread = ChatGPTThread(value, label.strip(), notes.strip())
        project.chatgpt_threads.append(thread)
        self.projects.save(project)
        self._record(
            "thread_added",
            f"Added ChatGPT thread {thread.display_label}",
            project=project.name,
            detail=value,
        )
        return thread

    def open_thread(self, project_name: str, url: str) -> DesktopOpenResult:
        project = self.project(project_name)
        result = self.desktop.open_url(url)
        self._record(
            "thread_opened",
            f"Opened ChatGPT thread" if result.opened else "Could not open ChatGPT thread",
            project=project.name,
            detail=result.error or url,
            success=result.opened,
        )
        return result

    def copy_thread(self, project_name: str, url: str) -> ClipboardResult:
        self.project(project_name)
        if self.settings.clipboard_mode == "disabled":
            return ClipboardResult(False, "disabled")
        return self.clipboard.copy(url)

    def discover_transcripts(
        self, project_name: str, session_id: str | None = None
    ) -> tuple[TranscriptCandidate, ...]:
        project = self.project(project_name)
        session = (
            self.sessions.load(project.session_key, session_id)
            if session_id
            else self.sessions.current(project.session_key)
        )
        return tuple(self.transcripts.discover(project, session))

    def recent_activity(
        self, *, project: str = "", limit: int = 30
    ) -> tuple[ActivityRecord, ...]:
        if not project:
            return tuple(self.activity.recent(limit=limit))
        try:
            registered = self.project(project)
        except LookupError:
            aliases = {project}
        else:
            # v0.3 activity used the original display name, which is also the
            # migrated registry ID. Include both sides of a v0.4 rename.
            aliases = {registered.name, registered.registry_id}
        records = [
            item
            for item in self.activity.all()
            if item.project in aliases
        ]
        return tuple(records[: max(0, limit)])

    def update_project_memory(
        self,
        project_name: str,
        *,
        objective: str | None = None,
        instructions: list[str] | None = None,
    ) -> Project:
        project = self.project(project_name)
        if objective is not None:
            project.objective = objective
        if instructions is not None:
            project.instructions = instructions
        self.projects.save(project)
        self._record(
            "project_updated",
            "Updated project context",
            project=project.name,
        )
        return project

    def register_project(
        self,
        name: str,
        directory: str,
        *,
        codex_account: str | None = None,
        git_name: str | None = None,
        git_email: str | None = None,
        github_account: str | None = None,
        github_host: str | None = None,
        github_owner: str | None = None,
        remote: str | None = None,
        expected_remote_url: str | None = None,
        gpt_threads: list[str] | None = None,
        instructions: list[str] | None = None,
        objective: str | None = None,
        terminal: str | None = None,
        terminal_layout: str | None = None,
        theme_color: str | None = None,
        theme_label: str | None = None,
        terminal_mode: str | None = None,
        associated_paths: list[AssociatedPath] | None = None,
        allow_update: bool = True,
    ) -> RegistrationResult:
        project_name = self.projects.validate_name(name)
        path = self.platform.normalize_path(directory)
        if not path.is_dir():
            raise WorkbenchError(f"Directory does not exist: {path}")
        existing = self.projects.all().get(project_name)
        if existing is not None and not allow_update:
            raise WorkbenchError(f"Project is already registered: {name}")
        project = existing or Project(project_name, str(path))
        project.directory = str(path)
        if existing is None and terminal is None:
            project.terminal.adapter = self.settings.preferred_terminal
        if existing is None:
            project.terminal.mode = self.settings.shell_mode

        if codex_account is not None:
            project.codex_account = codex_account
        if git_name is not None:
            project.git.name = git_name
        if git_email is not None:
            project.git.email = git_email
        if github_account is not None:
            project.github.account = github_account
            project.git.github_account = github_account
        if github_host is not None:
            project.github.host = github_host
        if github_owner is not None:
            project.github.owner = github_owner
        if remote is not None:
            project.repository.remote = remote
        if expected_remote_url is not None:
            project.repository.expected_url = expected_remote_url
            if expected_remote_url:
                project.repository.remotes[
                    project.repository.remote
                ] = expected_remote_url
        if gpt_threads is not None:
            project.gpt_threads = gpt_threads
        if instructions is not None:
            project.instructions = instructions
        if objective is not None:
            project.objective = objective
        if terminal is not None:
            project.terminal.adapter = terminal
        if terminal_layout is not None:
            project.terminal.layout = terminal_layout
        if terminal_mode is not None:
            if terminal_mode not in {"embedded", "external"}:
                raise WorkbenchError(
                    "Terminal mode must be embedded or external."
                )
            project.terminal.mode = terminal_mode
        if associated_paths is not None:
            project.associated_paths = list(associated_paths)
        if theme_color is not None:
            project.theme.color = theme_color
        if theme_label is not None:
            project.theme.label = theme_label

        state = inspect(path, project.remote)
        identity_requested = git_name is not None or git_email is not None
        local_identity_updated = False
        if state.is_repository and identity_requested:
            set_local_identity(
                path,
                git_name if git_name is not None else "",
                git_email if git_email is not None else "",
            )
            local_identity_updated = True
            state = inspect(path, project.remote)
        if state.is_repository:
            project.repository.remotes = dict(state.remotes)
        config_path = self.projects.save(project)
        self._record(
            "project_registered",
            f"Registered project {project.name}",
            project=project.name,
            account=project.codex_account,
            detail=str(project.path),
        )
        return RegistrationResult(
            project, config_path, local_identity_updated, state
        )

    def clone_project(
        self,
        name: str,
        repository_url: str,
        destination_parent: str | Path,
        *,
        destination_folder: str = "",
        codex_account: str = "",
        github_account: str = "",
        cancel: Event | None = None,
        on_progress: Callable[[CloneProgress], None] | None = None,
    ) -> CloneProjectResult:
        project_id = self.projects.validate_name(name)
        existing = self.projects.all()
        if project_id in existing or any(
            item.name.casefold() == name.strip().casefold()
            for item in existing.values()
        ):
            raise WorkbenchError(f"Project is already registered: {name}")
        folder = destination_folder.strip() or infer_repository_name(
            repository_url
        )
        if not folder:
            raise WorkbenchError(
                "Could not infer a destination folder from the repository URL."
            )
        request = CloneRequest(
            repository_url,
            Path(destination_parent),
            folder,
        )
        clone_result = self.clone_service.clone(
            request,
            cancel=cancel,
            on_progress=on_progress,
        )
        if not clone_result.succeeded:
            self._record(
                "clone_cancelled" if clone_result.cancelled else "clone_failed",
                clone_result.summary,
                project=name,
                detail=clone_result.stderr or clone_result.stdout,
                success=False,
            )
            return CloneProjectResult(clone_result)
        try:
            registration = self.register_project(
                name,
                str(clone_result.request.destination),
                codex_account=codex_account,
                github_account=github_account,
                expected_remote_url=clone_result.request.repository_url,
                allow_update=False,
            )
        except Exception as error:
            raise WorkbenchError(
                "Clone succeeded, but Workbench registration failed. "
                f"Files were left untouched at {clone_result.request.destination}: "
                f"{error}"
            ) from error
        self._record(
            "project_cloned",
            f"Cloned and registered {registration.project.name}",
            project=registration.project.name,
            detail=str(clone_result.request.destination),
        )
        return CloneProjectResult(clone_result, registration)

    def edit_project(
        self,
        project_name: str,
        *,
        display_name: str | None = None,
        directory: str | None = None,
        codex_account: str | None = None,
        github_account: str | None = None,
        associated_paths: list[AssociatedPath] | None = None,
        terminal_mode: str | None = None,
    ) -> EditProjectResult:
        project = self.project(project_name)
        old_path = project.path
        if display_name is not None:
            project.name = self.projects.validate_display_name(display_name)
        if directory is not None:
            new_path = self.platform.normalize_path(directory)
            if not new_path.is_dir():
                raise WorkbenchError(f"Directory does not exist: {new_path}")
            project.directory = str(new_path)
        if codex_account is not None:
            project.codex_account = codex_account.strip()
        if github_account is not None:
            project.github.account = github_account.strip()
            project.git.github_account = project.github.account
        if associated_paths is not None:
            project.associated_paths = list(associated_paths)
        if terminal_mode is not None:
            if terminal_mode not in {"embedded", "external"}:
                raise WorkbenchError(
                    "Terminal mode must be embedded or external."
                )
            project.terminal.mode = terminal_mode
        directory_changed = project.path != old_path
        git = inspect(project.path, project.remote)
        project.repository.remotes = dict(git.remotes)
        sessions = self.sessions.list(project.session_key)
        config_path = self.projects.save(project)
        self._record(
            "project_updated",
            f"Updated project {project.name}",
            project=project.name,
            detail=str(project.path),
        )
        return EditProjectResult(
            project,
            config_path,
            git,
            directory_changed,
            directory_changed and bool(sessions),
        )

    def remove_project(self, project_name: str) -> RemovalResult:
        project = self.project(project_name)
        preserved_sessions = len(self.sessions.list(project.session_key))
        removed, config_path = self.projects.remove(project.registry_id)
        if self.settings.last_project in {
            removed.name,
            removed.registry_id,
        }:
            self.settings.last_project = ""
            self.settings_store.save(self.settings)
        self._record(
            "project_removed",
            f"Removed {removed.name} from Workbench only",
            project=removed.name,
            detail=(
                f"Files untouched at {removed.path}; "
                f"{preserved_sessions} sessions preserved"
            ),
        )
        return RemovalResult(removed, config_path, preserved_sessions)

    def add_associated_path(
        self,
        project_name: str,
        *,
        label: str,
        path: str,
        role: str = "other",
        open_shell: bool = True,
        required: bool = False,
    ) -> EditProjectResult:
        project = self.project(project_name)
        values = [
            *project.associated_paths,
            AssociatedPath(label, path, role, open_shell, required),
        ]
        return self.edit_project(
            project.registry_id,
            associated_paths=values,
        )

    def edit_associated_path(
        self,
        project_name: str,
        label: str,
        *,
        new_label: str | None = None,
        path: str | None = None,
        role: str | None = None,
        open_shell: bool | None = None,
        required: bool | None = None,
    ) -> EditProjectResult:
        project = self.project(project_name)
        matches = [
            item
            for item in project.associated_paths
            if item.label.casefold() == label.casefold()
        ]
        if len(matches) != 1:
            raise WorkbenchError(
                f"Unknown associated path for {project.name}: {label}"
            )
        selected = matches[0]
        replacement = AssociatedPath(
            new_label if new_label is not None else selected.label,
            path if path is not None else selected.path,
            role if role is not None else selected.role,
            open_shell if open_shell is not None else selected.open_shell,
            required if required is not None else selected.required,
            dict(selected.extra),
        )
        values = [
            replacement if item is selected else item
            for item in project.associated_paths
        ]
        return self.edit_project(
            project.registry_id,
            associated_paths=values,
        )

    def remove_associated_path(
        self,
        project_name: str,
        label: str,
    ) -> EditProjectResult:
        project = self.project(project_name)
        values = [
            item
            for item in project.associated_paths
            if item.label.casefold() != label.casefold()
        ]
        if len(values) == len(project.associated_paths):
            raise WorkbenchError(
                f"Unknown associated path for {project.name}: {label}"
            )
        return self.edit_project(
            project.registry_id,
            associated_paths=values,
        )

    def preflight(self, name: str) -> PreflightReport:
        project = self.project(name)
        session = self.sessions.current(project.session_key)
        account = (
            (session.codex_account if session else "")
            or project.codex_account
        )
        return build_preflight(
            project,
            codex=self.codex,
            github=self.github,
            codex_account=account,
        )

    def status(self, name: str, *, query_codex: bool = True) -> StatusResult:
        project = self.project(name)
        git = inspect(project.path, project.remote)
        session = self.sessions.current(project.session_key)
        account = (
            (session.codex_account if session else "")
            or project.codex_account
        )
        codex_status = (
            self.codex.read_status(account)
            if query_codex and account
            else None
        )
        github_auth = (
            self.github.detect_account(project.github.host)
            if project.github.account
            else None
        )
        associated = inspect_associated_paths(project)
        text = render_context(
            project,
            git,
            codex=codex_status,
            session=session,
            github_auth=github_auth,
            associated=associated,
        )
        return StatusResult(
            project,
            git,
            session,
            codex_status,
            github_auth,
            text,
            associated,
        )

    def copy_all(self, name: str) -> CopyAllResult:
        status = self.prepare_copy_all(name)
        clipboard = (
            ClipboardResult(False, "disabled")
            if self.settings.clipboard_mode == "disabled"
            else self.clipboard.copy(status.text)
        )
        return self.complete_copy_all(status, clipboard)

    def prepare_copy_all(self, name: str) -> StatusResult:
        """Generate transferable context without touching a clipboard."""

        return self.status(name)

    def complete_copy_all(
        self,
        status: StatusResult,
        clipboard: ClipboardResult,
    ) -> CopyAllResult:
        """Record and return the result of a front-end clipboard attempt."""

        self._record(
            "context_copied",
            "Copied workspace context" if clipboard.copied else "Generated workspace context",
            project=status.project.name,
            session=status.session.id if status.session else "",
            account=(status.session.codex_account if status.session else "")
            or status.project.codex_account,
            detail=clipboard.helper or clipboard.error_summary,
            success=clipboard.copied,
        )
        return CopyAllResult(status, clipboard)

    def open_codex(
        self,
        name: str,
        *,
        in_terminal: bool = False,
        detached: bool = False,
    ) -> int:
        project = self.project(name)
        session = self.sessions.current(project.session_key)
        account = (
            (session.codex_account if session else "")
            or project.codex_account
        )
        if not account:
            raise WorkbenchError(
                f"No Codex account is configured for {project.name}"
            )
        if in_terminal:
            result = self.terminal.open_command(
                project.path,
                self.codex.command(account),
                project.terminal,
                title=f"{project.name} · {account}",
                detached=detached,
            )
        else:
            result = self.codex.launch(account, project.path)
        self._record(
            "codex_opened",
            f"Opened Codex as {account}",
            project=project.name,
            session=session.id if session else "",
            account=account,
        )
        return result

    def shell_targets(self, name: str) -> tuple[ShellTarget, ...]:
        project = self.project(name)
        targets = [
            ShellTarget("Canonical root", project.path, "canonical", True)
        ]
        targets.extend(
            ShellTarget(
                associated.label,
                associated.resolved_path,
                associated.role,
                False,
            )
            for associated in project.associated_paths
            if associated.open_shell
        )
        return tuple(targets)

    def shell_cwd(self, name: str, target: str = "") -> Path:
        project = self.project(name)
        path = resolve_project_path(
            project,
            target,
            require_shell=True,
        )
        if not path.is_dir():
            raise WorkbenchError(f"Shell directory does not exist: {path}")
        return path

    def open_shell(
        self,
        name: str,
        *,
        target: str = "",
        detached: bool = False,
    ) -> int:
        project = self.project(name)
        cwd = self.shell_cwd(name, target)
        result = self.terminal.open_shell(
            cwd, project.terminal, detached=detached
        )
        self._record(
            "shell_opened",
            "Opened project shell",
            project=project.name,
            detail=str(cwd),
        )
        return result

    def open_folder(
        self,
        name: str,
        *,
        target: str = "",
    ) -> DesktopOpenResult:
        project = self.project(name)
        path = resolve_project_path(project, target)
        result = self.desktop.open_folder(path)
        self._record(
            "folder_opened",
            "Opened project folder" if result.opened else "Could not open project folder",
            project=project.name,
            detail=result.error or str(path),
            success=result.opened,
        )
        return result

    def platform_capabilities(
        self,
        *,
        embedded_terminal: bool | None = None,
    ) -> PlatformCapabilities:
        capabilities = self.platform.capabilities()
        if embedded_terminal is None:
            return capabilities
        return replace(
            capabilities,
            embedded_terminal=embedded_terminal,
        )

    def start_session(
        self,
        project_name: str,
        session_name: str,
        *,
        objective: str = "",
        codex_account: str = "",
        gpt_thread: str = "",
        next_action: str = "",
    ) -> WorkSession:
        project = self.project(project_name)
        if not session_name.strip():
            raise WorkbenchError("Session name cannot be empty")
        git = inspect(project.path, project.remote)
        session = self.sessions.create(
            project,
            git,
            name=session_name.strip(),
            objective=objective,
            codex_account=codex_account,
            gpt_thread=gpt_thread,
            next_action=next_action,
        )
        self._record(
            "session_started",
            f"Started session {session.name}",
            project=project.name,
            session=session.id,
            account=session.codex_account,
        )
        return session

    def update_session(
        self,
        project_name: str,
        session_id: str | None = None,
        **changes: object,
    ) -> WorkSession:
        project = self.project(project_name)
        session = self.sessions.resolve(project.session_key, session_id)
        git = inspect(project.path, project.remote)
        session = self.sessions.update(session, git, **changes)
        self._record(
            "session_updated",
            f"Updated session {session.name}",
            project=project.name,
            session=session.id,
            account=session.codex_account,
        )
        return session

    def handoff(
        self,
        project_name: str,
        *,
        session_id: str | None = None,
        session_name: str = "current-work",
        to_account: str = "",
        transcript: Path | None = None,
        objective: str | None = None,
        completed: list[str] | None = None,
        current_state: str | None = None,
        current_problem: str | None = None,
        next_action: str | None = None,
        notes: list[str] | None = None,
    ) -> HandoffBundle:
        project = self.project(project_name)
        git = inspect(project.path, project.remote)
        if session_id:
            session = self.sessions.resolve(project.session_key, session_id)
        else:
            session = self.sessions.current(project.session_key)
            if session is None:
                session = self.sessions.create(
                    project,
                    git,
                    name=session_name,
                    objective=objective or project.objective,
                    next_action=next_action or "",
                )
        bundle = self.handoffs.create(
            project,
            git,
            session,
            to_account=to_account,
            transcript=transcript,
            objective=objective,
            completed=completed,
            current_state=current_state,
            current_problem=current_problem,
            next_action=next_action,
            notes=notes,
        )
        self._record(
            "handoff_created",
            f"Generated handoff to {bundle.session.codex_account or '-'}",
            project=project.name,
            session=bundle.session.id,
            account=bundle.session.codex_account,
            detail=str(bundle.handoff_path),
        )
        return bundle

    def launch_handoff(
        self,
        bundle: HandoffBundle,
        *,
        in_terminal: bool = False,
        detached: bool = False,
    ) -> int:
        account = bundle.session.codex_account
        if not account:
            raise WorkbenchError("No target Codex account was selected")
        project = self.project(bundle.session.project)
        if in_terminal:
            return self.terminal.open_command(
                project.path,
                self.codex.command(
                    account, initial_prompt=bundle.continuation_prompt
                ),
                project.terminal,
                title=f"{project.name} · handoff · {account}",
                detached=detached,
            )
        return self.codex.launch(
            account,
            project.path,
            initial_prompt=bundle.continuation_prompt,
        )

    def resume(
        self,
        project_name: str,
        *,
        session_id: str | None = None,
        account: str = "",
    ) -> ResumePlan:
        project = self.project(project_name)
        session = self.sessions.resolve(project.session_key, session_id)
        if account:
            session.codex_account = account
            self.sessions.save(session)
        selected_account = (
            session.codex_account or project.codex_account
        )
        git = inspect(project.path, project.remote)
        warnings: list[str] = []
        if session.branch and git.branch != session.branch:
            warnings.append(
                f"session branch is {session.branch}; repository is {git.branch or 'detached'}"
            )
        if session.current_head and git.head and session.current_head != git.head:
            warnings.append(
                "repository HEAD has moved since the session was last updated"
            )
        root_handoff = (
            self.sessions.session_dir(project.session_key, session.id)
            / "handoff.md"
        )
        handoff_path = root_handoff if root_handoff.is_file() else None
        codex_status = (
            self.codex.read_status(selected_account)
            if selected_account
            else None
        )
        github_auth = (
            self.github.detect_account(project.github.host)
            if project.github.account
            else None
        )
        context = render_context(
            project,
            git,
            codex=codex_status,
            session=session,
            github_auth=github_auth,
        )
        prompt = (
            continuation_instruction(handoff_path)
            if handoff_path
            else (
                "Continue this existing work session. Inspect the repository "
                "and Git state before making changes. Follow the project "
                f"objective: {session.objective or 'review current state'}."
            )
        )
        return ResumePlan(
            project,
            session,
            git,
            selected_account,
            handoff_path,
            context,
            tuple(warnings),
            prompt,
        )

    def resume_here(self, plan: ResumePlan) -> ResumePlan:
        self.sessions.set_current(plan.project.session_key, plan.session.id)
        self._record(
            "session_resumed",
            f"Resumed session {plan.session.name}",
            project=plan.project.name,
            session=plan.session.id,
            account=plan.account,
        )
        return plan

    def launch_resume(
        self,
        plan: ResumePlan,
        *,
        in_terminal: bool = False,
        detached: bool = False,
    ) -> int:
        if not plan.account:
            raise WorkbenchError("No Codex account is available for resume")
        self.resume_here(plan)
        if in_terminal:
            return self.terminal.open_command(
                plan.project.path,
                self.codex.command(plan.account, initial_prompt=plan.prompt),
                plan.project.terminal,
                title=f"{plan.project.name} · resume · {plan.account}",
                detached=detached,
            )
        return self.codex.launch(
            plan.account,
            plan.project.path,
            initial_prompt=plan.prompt,
        )

    def plan_commit(
        self, project_name: str, *, show_diff: bool = False
    ) -> CommitPlan:
        project = self.project(project_name)
        git = inspect(project.path, project.remote)
        if not git.is_repository:
            raise WorkbenchError(f"Not a Git repository: {project.path}")
        blocking: list[str] = []
        warnings: list[str] = []
        if not git.user_name:
            blocking.append("Git user is not configured")
        if not git.user_email:
            blocking.append("Git email is not configured")
        if project.git.name and git.user_name != project.git.name:
            blocking.append(
                f"Git user {git.user_name or '-'} differs from expected "
                f"{project.git.name}"
            )
        if project.git.email and git.user_email != project.git.email:
            blocking.append(
                f"Git email {git.user_email or '-'} differs from expected "
                f"{project.git.email}"
            )
        if git.identity_source != "repository-local":
            warnings.append(
                f"Git identity is {git.identity_source}, not fully repository-local"
            )
        diff = ""
        if show_diff:
            sections = []
            working = working_diff(project.path)
            cached = working_diff(project.path, staged=True)
            if working:
                sections.append("WORKING TREE DIFF\n" + working)
            if cached:
                sections.append("STAGED DIFF\n" + cached)
            diff = "\n".join(sections)
        return CommitPlan(
            project,
            git,
            git.file_changes,
            diff,
            tuple(blocking),
            tuple(warnings),
            _commit_snapshot(project.path),
        )

    def commit(
        self,
        project_name: str,
        *,
        message: str | None = None,
        show_diff: bool = False,
        stage_all: bool = False,
        files: Sequence[str] = (),
        allow_identity_mismatch: bool = False,
        plan: CommitPlan | None = None,
    ) -> CommitResult:
        project = self.project(project_name)
        plan = plan or self.plan_commit(project_name, show_diff=show_diff)
        before = plan.git
        diff = plan.diff
        current = inspect(project.path, project.remote)
        reviewed_state = (
            before.branch,
            before.head,
            before.status,
            before.user_name,
            before.user_email,
        )
        current_state = (
            current.branch,
            current.head,
            current.status,
            current.user_name,
            current.user_email,
        )
        snapshot_changed = bool(message) and (
            _commit_snapshot(project.path) != plan.snapshot
        )
        if message and (current_state != reviewed_state or snapshot_changed):
            return CommitResult(
                current,
                diff,
                staged_changes(project.path),
                "",
                2,
                False,
                "repository or Git identity changed since preview; review again",
            )
        if before.clean:
            return CommitResult(
                before, diff, "", "", 0, False, "working tree is clean"
            )
        if not message:
            return CommitResult(
                before,
                diff,
                staged_changes(project.path),
                "",
                2,
                False,
                "commit message required; no files were staged",
            )
        unresolved = [
            problem
            for problem in plan.blocking
            if not (
                allow_identity_mismatch
                and "differs from expected" in problem
            )
        ]
        if unresolved:
            return CommitResult(
                before,
                diff,
                staged_changes(project.path),
                "",
                2,
                False,
                "; ".join(unresolved),
            )
        if stage_all and files:
            raise WorkbenchError("Choose either --all or --file, not both")
        if stage_all:
            run_git(project.path, "add", "-A", check=True)
        elif files:
            run_git(project.path, "add", "--", *files, check=True)
        staged = staged_changes(project.path)
        if not staged:
            return CommitResult(
                before,
                diff,
                "",
                "",
                2,
                False,
                "nothing is staged; use --all or one or more --file options",
            )
        result = run_git(project.path, "commit", "-m", message)
        output = result.stdout or result.stderr
        committed = result.returncode == 0
        session = self.sessions.current(project.session_key)
        self._record(
            "commit_created" if committed else "commit_failed",
            "Commit created" if committed else "Commit failed",
            project=project.name,
            session=session.id if session else "",
            detail=output.strip(),
            success=committed,
        )
        return CommitResult(
            before,
            diff,
            staged,
            output,
            result.returncode,
            committed,
            "" if committed else "git commit failed",
        )

    def plan_push(self, project_name: str) -> PushPlan:
        project = self.project(project_name)
        git = inspect(project.path, project.remote)
        blocking: list[str] = []
        warnings: list[str] = []
        if not git.is_repository:
            blocking.append(f"not a Git repository: {project.path}")
        if not git.branch:
            blocking.append("current branch cannot be determined")
        if not git.remote_url:
            blocking.append(f"remote {project.remote} has no URL")

        expected_url = project.repository.expected_url
        if expected_url and git.remote_url != expected_url:
            blocking.append(
                f"remote URL differs from expected {expected_url}"
            )

        remote_repository = parse_remote_url(git.remote_url)
        if (
            project.github.owner
            and remote_repository.owner != project.github.owner
        ):
            blocking.append(
                "remote owner "
                f"{remote_repository.owner or '-'} differs from expected "
                f"{project.github.owner}"
            )

        detected_account = ""
        if project.github.account:
            auth = self.github.detect_account(project.github.host)
            detected_account = auth.account
            if detected_account and auth.authenticated is False:
                blocking.append(
                    f"GitHub CLI account {detected_account} is not authenticated"
                )
            elif detected_account and detected_account != project.github.account:
                blocking.append(
                    f"GitHub CLI account {detected_account} differs from "
                    f"expected {project.github.account}"
                )
            elif not detected_account:
                warnings.append(
                    "active Git authentication could not be verified; "
                    f"expected account is {project.github.account}"
                )
        else:
            warnings.append("no expected GitHub account is configured")
        if git.upstream and git.upstream != f"{project.remote}/{git.branch}":
            warnings.append(
                f"current upstream is {git.upstream}, not "
                f"{project.remote}/{git.branch}"
            )
        if not git.upstream:
            warnings.append("branch has no upstream")
        return PushPlan(
            git,
            project.remote,
            git.remote_url,
            git.branch,
            git.upstream,
            project.github.account,
            detected_account,
            remote_repository.owner,
            tuple(blocking),
            tuple(warnings),
        )

    def push(
        self,
        project_name: str,
        *,
        confirmed: bool = False,
        set_upstream: bool = False,
        allow_destination_mismatch: bool = False,
        allow_identity_mismatch: bool = False,
        plan: PushPlan | None = None,
    ) -> PushResult:
        project = self.project(project_name)
        plan = plan or self.plan_push(project_name)
        if confirmed:
            current = self.plan_push(project_name)
            reviewed_context = (
                plan.remote,
                plan.remote_url,
                plan.branch,
                plan.upstream,
                plan.detected_github_account,
                plan.remote_owner,
                plan.blocking,
            )
            current_context = (
                current.remote,
                current.remote_url,
                current.branch,
                current.upstream,
                current.detected_github_account,
                current.remote_owner,
                current.blocking,
            )
            if current_context != reviewed_context:
                return PushResult(
                    plan,
                    "",
                    2,
                    False,
                    "push context changed since preview; review again",
                )
        remaining = []
        for problem in plan.blocking:
            if (
                "remote URL differs" in problem
                and allow_destination_mismatch
            ):
                continue
            if (
                (
                    "GitHub CLI account" in problem
                    or "remote owner" in problem
                )
                and allow_identity_mismatch
            ):
                continue
            remaining.append(problem)
        if remaining:
            return PushResult(
                plan,
                "",
                2,
                False,
                "; ".join(remaining),
            )
        if not confirmed:
            return PushResult(
                plan,
                "",
                2,
                False,
                "explicit confirmation required; add --yes",
            )
        args = ["push"]
        if set_upstream:
            args.append("--set-upstream")
        args.extend((project.remote, plan.branch))
        result = run_git(project.path, *args)
        pushed = result.returncode == 0
        output = result.stdout or result.stderr
        self._record(
            "push_completed" if pushed else "push_failed",
            (
                f"Pushed {plan.destination}"
                if pushed
                else f"Push failed: {plan.destination}"
            ),
            project=project.name,
            detail=output.strip(),
            success=pushed,
        )
        return PushResult(
            plan,
            output,
            result.returncode,
            pushed,
            "" if pushed else "git push failed",
        )
