from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

from . import __version__
from .desktop_entry import install_desktop_entry
from .projects import ProjectNotFound
from .services import Workbench, WorkbenchError
from .sessions import SessionNotFound, render_session


def _app() -> Workbench:
    return Workbench()


def cmd_add(args: argparse.Namespace) -> int:
    result = _app().register_project(
        args.name,
        args.directory,
        codex_account=args.codex_account,
        git_name=args.git_name,
        git_email=args.git_email,
        github_account=args.github_account,
        github_host=args.github_host,
        github_owner=args.github_owner,
        remote=args.remote,
        expected_remote_url=args.expected_remote_url,
        gpt_threads=args.gpt_thread,
        instructions=args.instruction,
        objective=args.objective,
        terminal=args.terminal,
        terminal_layout=args.terminal_layout,
        terminal_mode=args.shell_mode,
        theme_color=args.theme_color,
        theme_label=args.theme_label,
    )
    print(f"Saved {result.project.name} -> {result.project.path}")
    if result.local_identity_updated:
        print("Updated repository-local Git identity.")
    return 0


def cmd_projects(_: argparse.Namespace) -> int:
    projects = _app().projects.all()
    if not projects:
        print("No projects yet. Use: cwb add NAME DIR")
        return 0
    for project in projects.values():
        print(
            f"{project.name:20} "
            f"{project.codex_account or '-':14} {project.path}"
            f"  (+{len(project.associated_paths)} paths)"
        )
    return 0


def cmd_ready(args: argparse.Namespace) -> int:
    report = _app().preflight(args.project)
    markers = {"pass": "✓", "warn": "•", "fail": "!"}
    for check in report.checks:
        detail = f" — {check.detail}" if check.detail else ""
        print(
            f"{markers[check.level]} {check.label:18} "
            f"{check.value}{detail}"
        )
    return 0 if report.ok else 2


def cmd_status(args: argparse.Namespace) -> int:
    print(_app().status(args.project).text, end="")
    return 0


def cmd_copy_all(args: argparse.Namespace) -> int:
    result = _app().copy_all(args.project)
    clipboard = result.clipboard
    if clipboard.copied:
        print(
            "Copied project context to clipboard "
            f"with {clipboard.helper} ({clipboard.session_type})."
        )
    else:
        print(result.status.text, end="")
        print(
            "\nClipboard copy unavailable "
            f"({clipboard.session_type}): {clipboard.error_summary}. "
            "Context was printed instead.",
            file=sys.stderr,
        )
    return 0


def cmd_open(args: argparse.Namespace) -> int:
    app = _app()
    return (
        app.open_codex(args.project)
        if args.what == "codex"
        else app.open_shell(args.project)
    )


def cmd_codex(args: argparse.Namespace) -> int:
    return _app().open_codex(args.project)


def cmd_shell(args: argparse.Namespace) -> int:
    return _app().open_shell(args.project, target=args.path or "")


def cmd_clone(args: argparse.Namespace) -> int:
    result = _app().clone_project(
        args.name,
        args.repository_url,
        args.destination_parent,
        destination_folder=args.folder or "",
        codex_account=args.codex_account or "",
        github_account=args.github_account or "",
        on_progress=(
            (lambda item: print(item.message, file=sys.stderr))
            if args.progress
            else None
        ),
    )
    if not result.registered:
        print(result.clone.stderr or result.clone.stdout, end="", file=sys.stderr)
        print(f"error: {result.clone.summary}", file=sys.stderr)
        return 2
    print(
        f"Cloned and saved {result.project.name} -> "
        f"{result.project.path}"
    )
    return 0


def cmd_edit_project(args: argparse.Namespace) -> int:
    result = _app().edit_project(
        args.project,
        display_name=args.name,
        directory=args.directory,
        codex_account=args.codex_account,
        github_account=args.github_account,
        terminal_mode=args.shell_mode,
    )
    print(f"Updated {result.project.name} -> {result.project.path}")
    if result.session_context_warning:
        print(
            "WARNING: canonical directory changed; review preserved session context."
        )
    return 0


def cmd_remove_project(args: argparse.Namespace) -> int:
    app = _app()
    project = app.project(args.project)
    sessions = app.sessions.list(project.session_key)
    print(
        "This removes the project from Codex Workbench only. "
        "Files on disk are not deleted."
    )
    print(f"PROJECT: {project.name}")
    print(f"DIRECTORY: {project.path}")
    print(f"PRESERVED SESSIONS: {len(sessions)}")
    if not args.yes:
        print("No registration removed: explicit confirmation required; add --yes")
        return 2
    result = app.remove_project(project.registry_id)
    print(
        f"Removed {result.project.name}; preserved "
        f"{result.preserved_sessions} session(s)."
    )
    return 0


def cmd_path_list(args: argparse.Namespace) -> int:
    project = _app().project(args.project)
    print(f"canonical\tcanonical\t{project.path}")
    for item in project.associated_paths:
        flags = []
        if item.open_shell:
            flags.append("shell")
        if item.required:
            flags.append("required")
        print(
            f"{item.label}\t{item.role}\t{item.resolved_path}"
            f"\t{','.join(flags) or '-'}"
        )
    return 0


def cmd_path_add(args: argparse.Namespace) -> int:
    result = _app().add_associated_path(
        args.project,
        label=args.label,
        path=args.path,
        role=args.role,
        open_shell=not args.no_shell,
        required=args.required,
    )
    print(f"Added associated path to {result.project.name}: {args.label}")
    return 0


def cmd_path_remove(args: argparse.Namespace) -> int:
    result = _app().remove_associated_path(args.project, args.label)
    print(
        f"Removed associated path {args.label} from "
        f"{result.project.name}; files were not deleted."
    )
    return 0


def cmd_files(args: argparse.Namespace) -> int:
    result = _app().open_folder(args.project, target=args.path or "")
    if not result.opened:
        raise WorkbenchError(result.error)
    return 0


def cmd_commit(args: argparse.Namespace) -> int:
    app = _app()
    plan = app.plan_commit(args.project, show_diff=args.diff)
    print(
        "GIT IDENTITY: "
        f"{plan.git.user_name or '-'} <{plan.git.user_email or '-'}> "
        f"[{plan.git.identity_source}]"
    )
    for problem in plan.blocking:
        print(f"BLOCKING CHECK: {problem}")
    for warning in plan.warnings:
        print(f"WARNING: {warning}")
    preview = app.commit(
        args.project,
        message=None,
        plan=plan,
    )
    print("CHANGED/UNTRACKED FILES:")
    print(preview.before.status or "(clean)")
    if preview.diff:
        print("\n" + preview.diff.rstrip())
    if preview.before.clean:
        print("No commit made: working tree is clean")
        return 0
    if not args.message:
        print(f"No commit made: {preview.reason}")
        return preview.returncode

    # The user sees the complete pre-stage state before any mutation.
    sys.stdout.flush()
    result = app.commit(
        args.project,
        message=args.message,
        stage_all=args.all,
        files=args.file or (),
        allow_identity_mismatch=args.allow_identity_mismatch,
        plan=plan,
    )
    if result.staged:
        print("\nFILES COMMITTED:")
        print(result.staged)
    if result.output:
        print(
            "\n" + result.output,
            end="" if result.output.endswith("\n") else "\n",
        )
    if result.reason:
        print(f"No commit made: {result.reason}")
    return result.returncode


def _print_push_plan(plan: object) -> None:
    print(f"REPOSITORY: {plan.remote_url or '-'}")
    print(f"REMOTE: {plan.remote}")
    print(f"BRANCH: {plan.branch or '-'}")
    print(f"DESTINATION: {plan.destination}")
    print(f"UPSTREAM: {plan.upstream or '-'}")
    ahead = "-" if plan.git.ahead is None else plan.git.ahead
    behind = "-" if plan.git.behind is None else plan.git.behind
    print(f"UPSTREAM STATE: ahead {ahead}, behind {behind}")
    print(
        "GITHUB ACCOUNT (EXPECTED): "
        f"{plan.expected_github_account or '-'}"
    )
    print(
        "GITHUB CLI ACCOUNT (DETECTED): "
        f"{plan.detected_github_account or '-'}"
    )
    print(f"REMOTE OWNER: {plan.remote_owner or '-'}")
    for warning in plan.warnings:
        print(f"WARNING: {warning}")


def cmd_push(args: argparse.Namespace) -> int:
    app = _app()
    plan = app.plan_push(args.project)
    _print_push_plan(plan)
    for problem in plan.blocking:
        print(f"BLOCKING CHECK: {problem}")
    # Destination, upstream, and identity checks are visible before network I/O.
    sys.stdout.flush()
    result = app.push(
        args.project,
        confirmed=args.yes,
        set_upstream=args.set_upstream,
        allow_destination_mismatch=args.allow_destination_mismatch,
        allow_identity_mismatch=args.allow_identity_mismatch,
        plan=plan,
    )
    if result.output:
        print(result.output, end="" if result.output.endswith("\n") else "\n")
    if result.reason:
        print(f"No push made: {result.reason}")
    return result.returncode


def cmd_handoff(args: argparse.Namespace) -> int:
    transcript = (
        Path(args.transcript).expanduser().resolve(strict=False)
        if args.transcript
        else None
    )
    app = _app()
    bundle = app.handoff(
        args.project,
        session_id=args.session,
        session_name=args.session_name,
        to_account=args.to or "",
        transcript=transcript,
        objective=args.objective,
        completed=args.completed,
        current_state=args.current_state,
        current_problem=args.current_problem,
        next_action=args.next_action,
        notes=args.note,
    )
    print(f"Handoff created: {bundle.session_dir}")
    print(f"Archived as: {bundle.archive_dir}")
    print(f"Read first: {bundle.handoff_path}")
    print(f"Session: {bundle.session.name} ({bundle.session.id})")
    if args.launch:
        print(f"Launching Codex account: {bundle.session.codex_account}")
        return app.launch_handoff(bundle)
    return 0


def cmd_session_start(args: argparse.Namespace) -> int:
    session = _app().start_session(
        args.project,
        args.name,
        objective=args.objective or "",
        codex_account=args.codex_account or "",
        gpt_thread=args.gpt_thread or "",
        next_action=args.next_action or "",
    )
    print(render_session(session), end="")
    return 0


def cmd_session_list(args: argparse.Namespace) -> int:
    app = _app()
    project = app.project(args.project)
    sessions = app.sessions.list(project.session_key)
    if not sessions:
        print(
            "No sessions yet. Use: "
            f"cwb session start {args.project} NAME"
        )
        return 0
    current = app.sessions.current(project.session_key)
    for session in sessions:
        marker = "*" if current and current.id == session.id else " "
        print(
            f"{marker} {session.id}  {session.name}  "
            f"{session.codex_account or '-'}  "
            f"{session.objective or '-'}"
        )
    return 0


def cmd_session_show(args: argparse.Namespace) -> int:
    app = _app()
    project = app.project(args.project)
    session = app.sessions.resolve(project.session_key, args.session)
    print(render_session(session), end="")
    return 0


def cmd_session_update(args: argparse.Namespace) -> int:
    session = _app().update_session(
        args.project,
        args.session,
        objective=args.objective,
        codex_account=args.codex_account,
        gpt_thread=args.gpt_thread,
        current_state=args.current_state,
        current_problem=args.current_problem,
        next_action=args.next_action,
        completed=args.completed,
        notes=args.note,
    )
    print(render_session(session), end="")
    return 0


def cmd_resume(args: argparse.Namespace) -> int:
    app = _app()
    plan = app.resume(
        args.project,
        session_id=args.session,
        account=args.account or "",
    )
    print(plan.context, end="")
    for warning in plan.warnings:
        print(f"WARNING: {warning}")
    if plan.handoff_path:
        print(f"HANDOFF: {plan.handoff_path}")
    print(f"RESUME ACCOUNT: {plan.account or '-'}")
    if args.launch:
        return app.launch_resume(plan)
    print("Resume context reconstructed. Add --launch to start Codex.")
    return 0


def cmd_codex_accounts(_: argparse.Namespace) -> int:
    app = _app()
    accounts = app.codex.list_accounts()
    if not accounts:
        print("No Codex accounts could be read from the launcher.")
        return 2
    for account in accounts:
        status = app.codex.read_status(account)
        remaining = (
            f"{status.five_hour_remaining}% left"
            if status.five_hour_remaining is not None
            else "--% left"
        )
        print(f"{account:20} {remaining:10} {status.availability}")
    return 0


def cmd_config_path(_: argparse.Namespace) -> int:
    print(_app().projects.path)
    return 0



def cmd_gui(args: argparse.Namespace) -> int:
    # GTK is deliberately imported only for this command.
    from .gui.app import main as gui_main

    return gui_main(["--check"] if args.check else [])


def cmd_install_desktop(_: argparse.Namespace) -> int:
    result = install_desktop_entry()
    print(f"Desktop launcher: {result.desktop_file}")
    print(f"Application icon: {result.icon_file}")
    print(f"Command: {result.command}")
    print("Open Ubuntu's application grid and pin Codex Workbench to the dock.")
    return 0

def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(
        prog="cwb", description="Codex Workbench"
    )
    root.add_argument("--version", action="version", version=__version__)
    sub = root.add_subparsers(dest="command", required=True)

    add = sub.add_parser("add", help="register/update a project")
    add.add_argument("name")
    add.add_argument("directory")
    add.add_argument("--codex-account")
    add.add_argument("--git-name", help="optional expected/local Git name")
    add.add_argument("--git-email", help="optional expected/local Git email")
    add.add_argument("--github-account")
    add.add_argument("--github-host")
    add.add_argument("--github-owner")
    add.add_argument("--remote")
    add.add_argument("--expected-remote-url")
    add.add_argument("--gpt-thread", action="append")
    add.add_argument("--instruction", action="append")
    add.add_argument("--objective")
    add.add_argument("--terminal")
    add.add_argument("--terminal-layout")
    add.add_argument(
        "--shell-mode",
        choices=("embedded", "external"),
    )
    add.add_argument("--theme-color")
    add.add_argument("--theme-label")
    add.set_defaults(func=cmd_add)

    clone = sub.add_parser(
        "clone",
        help="clone a Git repository and register it after success",
    )
    clone.add_argument("name")
    clone.add_argument("repository_url")
    clone.add_argument("destination_parent")
    clone.add_argument("--folder")
    clone.add_argument("--codex-account")
    clone.add_argument("--github-account")
    clone.add_argument(
        "--progress",
        action="store_true",
        help="print Git clone progress to stderr",
    )
    clone.set_defaults(func=cmd_clone)

    projects = sub.add_parser("projects", help="list projects")
    projects.set_defaults(func=cmd_projects)

    edit_project = sub.add_parser(
        "edit-project",
        help="edit Workbench project registration only",
    )
    edit_project.add_argument("project")
    edit_project.add_argument("--name")
    edit_project.add_argument("--directory")
    edit_project.add_argument("--codex-account")
    edit_project.add_argument("--github-account")
    edit_project.add_argument(
        "--shell-mode",
        choices=("embedded", "external"),
    )
    edit_project.set_defaults(func=cmd_edit_project)

    remove_project = sub.add_parser(
        "remove-project",
        help="remove registration without deleting project files",
    )
    remove_project.add_argument("project")
    remove_project.add_argument("--yes", action="store_true")
    remove_project.set_defaults(func=cmd_remove_project)

    path_command = sub.add_parser(
        "path",
        help="manage associated project paths",
    )
    path_sub = path_command.add_subparsers(
        dest="path_command",
        required=True,
    )
    path_list = path_sub.add_parser("list")
    path_list.add_argument("project")
    path_list.set_defaults(func=cmd_path_list)
    path_add = path_sub.add_parser("add")
    path_add.add_argument("project")
    path_add.add_argument("label")
    path_add.add_argument("path")
    path_add.add_argument("--role", default="other")
    path_add.add_argument("--no-shell", action="store_true")
    path_add.add_argument("--required", action="store_true")
    path_add.set_defaults(func=cmd_path_add)
    path_remove = path_sub.add_parser("remove")
    path_remove.add_argument("project")
    path_remove.add_argument("label")
    path_remove.set_defaults(func=cmd_path_remove)

    for name, function in (
        ("ready", cmd_ready),
        ("status", cmd_status),
        ("copy-all", cmd_copy_all),
    ):
        action = sub.add_parser(name)
        action.add_argument("project")
        action.set_defaults(func=function)

    opened = sub.add_parser("open")
    opened.add_argument("project")
    opened.add_argument(
        "what", choices=("codex", "shell"), nargs="?", default="codex"
    )
    opened.set_defaults(func=cmd_open)

    codex = sub.add_parser("codex")
    codex.add_argument("project")
    codex.set_defaults(func=cmd_codex)

    shell = sub.add_parser("shell")
    shell.add_argument("project")
    shell.add_argument(
        "--path",
        help="associated-path label; defaults to canonical root",
    )
    shell.set_defaults(func=cmd_shell)

    files = sub.add_parser("files")
    files.add_argument("project")
    files.add_argument(
        "--path",
        help="associated-path label; defaults to canonical root",
    )
    files.set_defaults(func=cmd_files)

    commit = sub.add_parser("commit")
    commit.add_argument("project")
    commit.add_argument("-m", "--message")
    commit.add_argument(
        "--diff", action="store_true", help="show working and staged diffs"
    )
    stage = commit.add_mutually_exclusive_group()
    stage.add_argument(
        "--all", action="store_true", help="explicitly stage all changes"
    )
    stage.add_argument(
        "--file", action="append", help="stage this path (repeatable)"
    )
    commit.add_argument(
        "--allow-identity-mismatch",
        action="store_true",
        help="explicitly accept a configured Git identity mismatch",
    )
    commit.set_defaults(func=cmd_commit)

    push = sub.add_parser("push")
    push.add_argument("project")
    push.add_argument("--yes", action="store_true")
    push.add_argument("--set-upstream", action="store_true")
    push.add_argument("--allow-destination-mismatch", action="store_true")
    push.add_argument("--allow-identity-mismatch", action="store_true")
    push.set_defaults(func=cmd_push)

    handoff = sub.add_parser("handoff")
    handoff.add_argument("project")
    handoff.add_argument("--session")
    handoff.add_argument("--session-name", default="current-work")
    handoff.add_argument("--to", help="target Codex account")
    handoff.add_argument(
        "--transcript", help="path to a Codex /export transcript"
    )
    handoff.add_argument("--objective")
    handoff.add_argument("--completed", action="append")
    handoff.add_argument("--current-state")
    handoff.add_argument("--current-problem")
    handoff.add_argument("--next-action")
    handoff.add_argument("--note", action="append")
    handoff.add_argument(
        "--launch",
        action="store_true",
        help="launch target account with the handoff instruction",
    )
    handoff.set_defaults(func=cmd_handoff)

    session = sub.add_parser("session", help="manage work sessions")
    session_sub = session.add_subparsers(dest="session_command", required=True)

    start = session_sub.add_parser("start")
    start.add_argument("project")
    start.add_argument("name")
    start.add_argument("--objective")
    start.add_argument("--codex-account")
    start.add_argument("--gpt-thread")
    start.add_argument("--next-action")
    start.set_defaults(func=cmd_session_start)

    list_sessions = session_sub.add_parser("list")
    list_sessions.add_argument("project")
    list_sessions.set_defaults(func=cmd_session_list)

    show = session_sub.add_parser("show")
    show.add_argument("project")
    show.add_argument("session", nargs="?")
    show.set_defaults(func=cmd_session_show)

    update = session_sub.add_parser("update")
    update.add_argument("project")
    update.add_argument("session", nargs="?")
    update.add_argument("--objective")
    update.add_argument("--codex-account")
    update.add_argument("--gpt-thread")
    update.add_argument("--current-state")
    update.add_argument("--current-problem")
    update.add_argument("--next-action")
    update.add_argument("--completed", action="append")
    update.add_argument("--note", action="append")
    update.set_defaults(func=cmd_session_update)

    resume = sub.add_parser("resume")
    resume.add_argument("project")
    resume.add_argument("--session")
    resume.add_argument("--account")
    resume.add_argument("--launch", action="store_true")
    resume.set_defaults(func=cmd_resume)

    accounts = sub.add_parser("codex-accounts")
    accounts.set_defaults(func=cmd_codex_accounts)

    config = sub.add_parser("config-path")
    config.set_defaults(func=cmd_config_path)

    gui = sub.add_parser("gui", help="open the native Linux switchboard")
    gui.add_argument(
        "--check",
        action="store_true",
        help="verify GTK/resources without requiring a display",
    )
    gui.set_defaults(func=cmd_gui)

    desktop = sub.add_parser(
        "install-desktop",
        help="install a per-user Ubuntu launcher",
    )
    desktop.set_defaults(func=cmd_install_desktop)
    return root


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        return int(args.func(args))
    except (
        WorkbenchError,
        ProjectNotFound,
        SessionNotFound,
        FileNotFoundError,
        ValueError,
        subprocess.CalledProcessError,
    ) as error:
        print(f"error: {error}", file=sys.stderr)
        return getattr(error, "exit_code", 2)


if __name__ == "__main__":
    raise SystemExit(main())
