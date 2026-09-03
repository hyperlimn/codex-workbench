from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote

from .git import GitState
from .models import Project, WorkSession, utc_now
from .paths import sessions_root
from .store import write_json


class SessionNotFound(LookupError):
    pass


def _project_component(name: str) -> str:
    return quote(name, safe="._-")


def _session_component(value: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", value):
        raise ValueError(f"Invalid session id: {value}")
    return value


def make_session_id(name: str, now: datetime | None = None) -> str:
    now = now or datetime.now(timezone.utc)
    slug = re.sub(r"[^a-z0-9]+", "-", name.casefold()).strip("-") or "work"
    return f"{now.strftime('%Y%m%d-%H%M%S-%f')}-{slug[:40]}"


class SessionStore:
    """Persistent work sessions below a project in the XDG data directory."""

    def __init__(self, root: Path | None = None):
        self.root = root or sessions_root()

    def project_dir(self, project: str) -> Path:
        return self.root / _project_component(project)

    def session_dir(self, project: str, session_id: str) -> Path:
        return self.project_dir(project) / _session_component(session_id)

    def session_file(self, project: str, session_id: str) -> Path:
        return self.session_dir(project, session_id) / "session.json"

    def save(self, session: WorkSession, *, touch: bool = True) -> Path:
        if touch:
            session.updated_at = utc_now()
        path = self.session_file(session.project, session.id)
        write_json(path, session.to_dict())
        return path

    def create(
        self,
        project: Project,
        git: GitState,
        *,
        name: str,
        objective: str = "",
        codex_account: str = "",
        gpt_thread: str = "",
        next_action: str = "",
    ) -> WorkSession:
        session = WorkSession(
            id=make_session_id(name),
            name=name,
            project=project.session_key,
            objective=objective or project.objective,
            codex_account=codex_account or project.codex_account,
            gpt_thread=gpt_thread,
            branch=git.branch,
            starting_head=git.head,
            current_head=git.head,
            next_action=next_action,
        )
        path = self.session_file(session.project, session.id)
        if path.exists():
            raise FileExistsError(f"Session already exists: {session.id}")
        self.save(session, touch=False)
        self.set_current(session.project, session.id)
        return session

    def load(self, project: str, session_id: str) -> WorkSession:
        path = self.session_file(project, session_id)
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError as error:
            raise SessionNotFound(
                f"Unknown session for {project}: {session_id}"
            ) from error
        if not isinstance(raw, dict):
            raise ValueError(f"Invalid session document: {path}")
        return WorkSession.from_dict(raw)

    def list(self, project: str) -> list[WorkSession]:
        result: list[WorkSession] = []
        root = self.project_dir(project)
        if not root.is_dir():
            return result
        for path in root.glob("*/session.json"):
            try:
                raw = json.loads(path.read_text(encoding="utf-8"))
                if isinstance(raw, dict):
                    result.append(WorkSession.from_dict(raw))
            except (OSError, ValueError, json.JSONDecodeError, KeyError):
                continue
        return sorted(result, key=lambda item: item.updated_at, reverse=True)

    def set_current(self, project: str, session_id: str) -> Path:
        # Validate before persisting a pointer that could escape the project.
        self.session_dir(project, session_id)
        path = self.project_dir(project) / "current.json"
        write_json(path, {"version": 1, "session_id": session_id})
        return path

    def current(self, project: str) -> WorkSession | None:
        pointer = self.project_dir(project) / "current.json"
        try:
            raw = json.loads(pointer.read_text(encoding="utf-8"))
            session_id = raw.get("session_id") if isinstance(raw, dict) else None
            if isinstance(session_id, str) and session_id:
                return self.load(project, session_id)
        except (OSError, ValueError, json.JSONDecodeError, SessionNotFound):
            pass
        sessions = self.list(project)
        return sessions[0] if sessions else None

    def resolve(self, project: str, session_id: str | None = None) -> WorkSession:
        session = self.load(project, session_id) if session_id else self.current(project)
        if session is None:
            raise SessionNotFound(
                f"No work session for {project}. Run: cwb session start {project} NAME"
            )
        self.set_current(project, session.id)
        return session

    def update(
        self,
        session: WorkSession,
        git: GitState,
        *,
        objective: str | None = None,
        codex_account: str | None = None,
        gpt_thread: str | None = None,
        current_state: str | None = None,
        current_problem: str | None = None,
        next_action: str | None = None,
        completed: list[str] | None = None,
        notes: list[str] | None = None,
    ) -> WorkSession:
        for field_name, value in (
            ("objective", objective),
            ("codex_account", codex_account),
            ("gpt_thread", gpt_thread),
            ("current_state", current_state),
            ("current_problem", current_problem),
            ("next_action", next_action),
        ):
            if value is not None:
                setattr(session, field_name, value)
        if completed:
            session.completed.extend(completed)
        if notes:
            session.notes.extend(notes)
        session.branch = git.branch
        session.current_head = git.head
        self.save(session)
        self.set_current(session.project, session.id)
        return session


def render_session(session: WorkSession) -> str:
    completed = "\n".join(f"- {item}" for item in session.completed) or "- none"
    notes = "\n".join(f"- {item}" for item in session.notes) or "- none"
    return f"""SESSION: {session.name} ({session.id})
PROJECT: {session.project}
STATUS: {session.status}
OBJECTIVE: {session.objective or '-'}
CODEX ACCOUNT: {session.codex_account or '-'}
GPT THREAD: {session.gpt_thread or '-'}
BRANCH: {session.branch or '-'}
STARTING HEAD: {session.starting_head or '-'}
CURRENT HEAD: {session.current_head or '-'}
CREATED: {session.created_at}
UPDATED: {session.updated_at}
CURRENT STATE: {session.current_state or '-'}
CURRENT PROBLEM: {session.current_problem or '-'}
NEXT ACTION: {session.next_action or '-'}
HANDOFFS: {len(session.handoff_history)}

COMPLETED:
{completed}

NOTES:
{notes}
"""
