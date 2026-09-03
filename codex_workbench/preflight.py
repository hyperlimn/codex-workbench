from __future__ import annotations

from dataclasses import dataclass

from .associated import AssociatedPathState, inspect_associated_paths
from .codex import CodexAdapter
from .git import GitState, inspect
from .github import GitHubAdapter, GitHubAuthState, parse_remote_url
from .models import Project


@dataclass(frozen=True)
class PreflightCheck:
    level: str
    label: str
    value: str
    detail: str = ""

    @property
    def failed(self) -> bool:
        return self.level == "fail"


@dataclass(frozen=True)
class PreflightReport:
    project: Project
    git: GitState
    checks: tuple[PreflightCheck, ...]
    github_auth: GitHubAuthState | None = None
    associated: tuple[AssociatedPathState, ...] = ()

    @property
    def ok(self) -> bool:
        return not any(check.failed for check in self.checks)


def _identity_check(
    label: str, detected: str, expected: str, source: str
) -> PreflightCheck:
    if not detected:
        return PreflightCheck("fail", label, "-", "not configured in Git")
    if expected and detected != expected:
        return PreflightCheck(
            "fail",
            label,
            detected,
            f"expected {expected}; detected from {source}",
        )
    detail = f"detected from {source}"
    if expected:
        detail = f"matches expected {expected}; {detail}"
    return PreflightCheck("pass", label, detected, detail)


def build_preflight(
    project: Project,
    *,
    git: GitState | None = None,
    codex: CodexAdapter | None = None,
    github: GitHubAdapter | None = None,
    codex_account: str | None = None,
) -> PreflightReport:
    state = git or inspect(project.path, project.remote)
    expected_codex_account = (
        project.codex_account if codex_account is None else codex_account
    )
    codex = codex or CodexAdapter()
    github = github or GitHubAdapter()
    checks: list[PreflightCheck] = [
        PreflightCheck(
            "pass" if state.directory_exists else "fail",
            "directory",
            str(project.path),
        ),
        PreflightCheck(
            "pass" if state.is_repository else "fail",
            "git repository",
            state.repository_root or "-",
        ),
    ]
    if state.branch:
        checks.append(PreflightCheck("pass", "git branch", state.branch))
    elif state.detached:
        checks.append(
            PreflightCheck(
                "warn",
                "git branch",
                f"detached at {state.head_short}",
                "commit and push targets require extra care",
            )
        )
    else:
        checks.append(PreflightCheck("fail", "git branch", "-"))

    checks.extend(
        (
            _identity_check(
                "git user",
                state.user_name,
                project.git.name,
                state.identity_source,
            ),
            _identity_check(
                "git email",
                state.user_email,
                project.git.email,
                state.identity_source,
            ),
            PreflightCheck(
                "pass" if state.remote_url else "fail",
                "remote",
                (
                    f"{project.remote} {state.remote_url}"
                    if state.remote_url
                    else f"{project.remote} -"
                ),
            ),
        )
    )

    expected_url = project.repository.expected_url
    if expected_url:
        checks.append(
            PreflightCheck(
                "pass" if state.remote_url == expected_url else "fail",
                "expected remote",
                expected_url,
                (
                    "matches detected remote"
                    if state.remote_url == expected_url
                    else f"detected {state.remote_url or '-'}"
                ),
            )
        )

    github_auth = None
    if project.github.account:
        github_auth = github.detect_account(project.github.host)
        if github_auth.account and github_auth.authenticated is False:
            checks.append(
                PreflightCheck(
                    "fail",
                    "github account",
                    github_auth.account,
                    "GitHub CLI reports invalid or expired authentication",
                )
            )
        elif github_auth.account:
            checks.append(
                PreflightCheck(
                    (
                        "pass"
                        if github_auth.account == project.github.account
                        else "fail"
                    ),
                    "github account",
                    github_auth.account,
                    f"expected {project.github.account}; detected by GitHub CLI",
                )
            )
        else:
            checks.append(
                PreflightCheck(
                    "warn",
                    "github account",
                    project.github.account,
                    "expected; active Git authentication could not be verified",
                )
            )

    remote_repository = parse_remote_url(state.remote_url)
    if project.github.owner:
        checks.append(
            PreflightCheck(
                (
                    "pass"
                    if remote_repository.owner == project.github.owner
                    else "fail"
                ),
                "repository owner",
                remote_repository.owner or "-",
                f"expected {project.github.owner}",
            )
        )

    checks.extend(
        (
            PreflightCheck(
                "pass" if bool(expected_codex_account) else "fail",
                "codex account",
                expected_codex_account or "-",
                (
                    "active work session account"
                    if expected_codex_account != project.codex_account
                    else "preferred project account"
                ),
            ),
            PreflightCheck(
                "pass" if codex.available else "fail",
                "codex launcher",
                codex.launcher,
            ),
            PreflightCheck(
                (
                    "fail"
                    if not state.is_repository
                    else ("pass" if state.clean else "warn")
                ),
                "working tree",
                (
                    "unavailable"
                    if not state.is_repository
                    else ("clean" if state.clean else "modified")
                ),
                (
                    "not a Git repository"
                    if not state.is_repository
                    else (
                        ""
                        if state.clean
                        else f"{len(state.changed_files)} changed/untracked entries"
                    )
                ),
            ),
        )
    )
    associated_states = inspect_associated_paths(project)
    for associated_state in associated_states:
        associated = associated_state.associated
        if associated_state.exists:
            level = "pass"
            detail = associated_state.summary
        else:
            level = "fail" if associated.required else "warn"
            detail = (
                "required path is missing"
                if associated.required
                else "optional path is missing"
            )
        checks.append(
            PreflightCheck(
                level,
                f"associated: {associated.label}",
                str(associated_state.path),
                f"{associated.role}; {detail}",
            )
        )
    return PreflightReport(
        project,
        state,
        tuple(checks),
        github_auth,
        associated_states,
    )
