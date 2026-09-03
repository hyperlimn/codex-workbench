from __future__ import annotations

from .associated import AssociatedPathState
from .codex import CodexStatus
from .git import GitState
from .github import GitHubAuthState
from .models import Project, WorkSession


def _identity(name: str, email: str) -> str:
    if name and email:
        return f"{name} <{email}>"
    if name:
        return name
    if email:
        return f"<{email}>"
    return "-"


def render(
    project: Project,
    git: GitState,
    *,
    codex: CodexStatus | None = None,
    session: WorkSession | None = None,
    github_auth: GitHubAuthState | None = None,
    associated: tuple[AssociatedPathState, ...] = (),
) -> str:
    instructions = (
        "\n".join(f"- {line}" for line in project.instructions) or "- none"
    )
    threads = [
        f"{thread.label}: {thread.url}" if thread.label else thread.url
        for thread in project.thread_references
    ]
    if session and session.gpt_thread and session.gpt_thread not in threads:
        threads.append(session.gpt_thread)
    thread_text = "\n".join(f"- {value}" for value in threads) or "- none"
    lines = [
        f"PROJECT: {project.name}",
        f"DIR: {project.path}",
        f"CODEX ACCOUNT: {(session.codex_account if session else '') or project.codex_account or '-'}",
        f"CODEX STATUS/USAGE: {codex.summary() if codex else 'not queried'}",
        (
            "GIT IDENTITY (DETECTED): "
            f"{_identity(git.user_name, git.user_email)} [{git.identity_source}]"
        ),
    ]
    if associated:
        associated_lines = []
        for item in associated:
            required = " · required" if item.associated.required else ""
            associated_lines.append(
                f"- {item.associated.label} [{item.associated.role}] "
                f"{item.path} — {item.summary}{required}"
            )
        lines[2:2] = [
            "ASSOCIATED PATHS:",
            *associated_lines,
        ]
    if project.git.configured:
        lines.append(
            "GIT IDENTITY (EXPECTED): "
            f"{_identity(project.git.name, project.git.email)}"
        )
    if project.github.account:
        lines.append(f"GITHUB ACCOUNT (EXPECTED): {project.github.account}")
    if github_auth and github_auth.account:
        lines.append(
            f"GITHUB CLI ACCOUNT (DETECTED): {github_auth.account}"
        )
    lines.extend(
        (
            f"GITHUB/REMOTE: {project.remote} {git.remote_url or '-'}",
            (
                f"BRANCH: {git.branch}"
                if git.branch
                else (
                    f"BRANCH: detached at {git.head_short}"
                    if git.detached
                    else "BRANCH: -"
                )
            ),
            f"HEAD: {git.head_short or git.head or '-'}",
            (
                "WORKTREE: unavailable"
                if not git.is_repository
                else f"WORKTREE: {'clean' if git.clean else 'modified'}"
            ),
            (
                "CURRENT OBJECTIVE: "
                f"{(session.objective if session else '') or project.objective or '-'}"
            ),
            (
                f"CURRENT SESSION: {session.name} ({session.id})"
                if session
                else "CURRENT SESSION: -"
            ),
            f"NEXT ACTION: {(session.next_action if session else '') or '-'}",
            "",
            "PROJECT INSTRUCTIONS:",
            instructions,
            "",
            "GPT THREADS:",
            thread_text,
            "",
            "GIT STATUS:",
            git.status or "(clean)",
        )
    )
    return "\n".join(lines) + "\n"
