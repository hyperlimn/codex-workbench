from __future__ import annotations

import os
import shutil
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from .git import GitState
from .models import HandoffRecord, Project, WorkSession, utc_now
from .sessions import SessionStore


@dataclass(frozen=True)
class HandoffBundle:
    session: WorkSession
    session_dir: Path
    archive_dir: Path
    handoff_path: Path
    transcript_path: Path | None

    @property
    def continuation_prompt(self) -> str:
        return continuation_instruction(self.handoff_path)


def continuation_instruction(handoff_path: Path) -> str:
    return (
        "Continue this existing work session. "
        f"Read {handoff_path} first. Consult transcript.md only when more "
        "historical context is necessary. Inspect the repository and Git "
        "state before making changes."
    )


def _write_bytes(path: Path, value: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", dir=path.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as output:
            output.write(value)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _copy_file(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{destination.name}.", dir=destination.parent
    )
    temporary = Path(temporary_name)
    try:
        with source.open("rb") as input_file, os.fdopen(
            descriptor, "wb"
        ) as output_file:
            shutil.copyfileobj(input_file, output_file)
            output_file.flush()
            os.fsync(output_file.fileno())
        os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)


def _items(values: list[str], empty: str) -> str:
    return "\n".join(f"- {value}" for value in values) or empty


def _render_handoff(
    project: Project,
    session: WorkSession,
    git: GitState,
    *,
    previous_account: str,
    target_account: str,
    transcript_reference: str,
) -> str:
    status = git.status or "(clean)"
    transcript = (
        f"See [{transcript_reference}]({transcript_reference})."
        if transcript_reference
        else "No raw transcript was supplied for this handoff."
    )
    return f"""# Codex Handoff

## Objective

{session.objective or 'Not recorded.'}

## Completed

{_items(session.completed, '- None recorded.')}

## Current State

{session.current_state or 'Not recorded.'}

## Current Problem

{session.current_problem or 'Not recorded.'}

## Git State

- Directory: {project.path}
- Branch: {git.branch or ('detached at ' + git.head_short if git.detached else '-')}
- Starting HEAD: {session.starting_head or '-'}
- Current HEAD: {git.head_short or git.head or '-'}
- Remote: {project.remote} {git.remote_url or '-'}
- Worktree: {'unavailable' if not git.is_repository else ('clean' if git.clean else 'modified')}

```text
{status}
```

## Next Action

{session.next_action or 'Inspect the repository and confirm the next implementation step.'}

## Previous Account

{previous_account or '-'}

## Target Account

{target_account or previous_account or '-'}

## Transcript

{transcript}

## Continuation Instruction

Continue this existing work session. Read handoff.md first. Consult the raw
transcript only when more historical context is necessary. Inspect the
repository and Git state before making changes.
"""


class HandoffService:
    def __init__(self, sessions: SessionStore | None = None):
        self.sessions = sessions or SessionStore()

    def create(
        self,
        project: Project,
        git: GitState,
        session: WorkSession,
        *,
        to_account: str = "",
        transcript: Path | None = None,
        objective: str | None = None,
        completed: list[str] | None = None,
        current_state: str | None = None,
        current_problem: str | None = None,
        next_action: str | None = None,
        notes: list[str] | None = None,
    ) -> HandoffBundle:
        if transcript is not None and not transcript.is_file():
            raise FileNotFoundError(f"Transcript does not exist: {transcript}")

        if objective is not None:
            session.objective = objective
        if current_state is not None:
            session.current_state = current_state
        if current_problem is not None:
            session.current_problem = current_problem
        if next_action is not None:
            session.next_action = next_action
        if completed:
            session.completed.extend(completed)
        if notes:
            session.notes.extend(notes)

        previous_account = session.codex_account or project.codex_account
        target_account = to_account or previous_account
        session.branch = git.branch
        session.current_head = git.head
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S-%f")
        session_dir = self.sessions.session_dir(
            project.session_key,
            session.id,
        )
        archive_dir = session_dir / "handoffs" / stamp
        archive_dir.mkdir(parents=True, exist_ok=False)

        archive_transcript = None
        root_transcript = session_dir / "transcript.md"
        if transcript is not None:
            archive_transcript = archive_dir / "transcript.md"
            _copy_file(transcript, archive_transcript)
            _copy_file(transcript, root_transcript)
            relative_transcript = str(
                archive_transcript.relative_to(session_dir)
            )
            session.transcript_paths.append(relative_transcript)

        archive_document = _render_handoff(
            project,
            session,
            git,
            previous_account=previous_account,
            target_account=target_account,
            transcript_reference=(
                "transcript.md" if archive_transcript is not None else ""
            ),
        )
        archive_handoff = archive_dir / "handoff.md"
        _write_bytes(archive_handoff, archive_document.encode("utf-8"))

        root_handoff = session_dir / "handoff.md"
        root_document = _render_handoff(
            project,
            session,
            git,
            previous_account=previous_account,
            target_account=target_account,
            transcript_reference=(
                "transcript.md" if root_transcript.is_file() else ""
            ),
        )
        _write_bytes(root_handoff, root_document.encode("utf-8"))

        session.handoff_history.append(
            HandoffRecord(
                id=stamp,
                created_at=utc_now(),
                from_codex_account=previous_account,
                to_codex_account=target_account,
                branch=git.branch,
                head=git.head,
                handoff_path=str(archive_handoff.relative_to(session_dir)),
                transcript_path=(
                    str(archive_transcript.relative_to(session_dir))
                    if archive_transcript is not None
                    else ""
                ),
            )
        )
        session.codex_account = target_account
        self.sessions.save(session)
        self.sessions.set_current(project.session_key, session.id)
        return HandoffBundle(
            session,
            session_dir,
            archive_dir,
            root_handoff,
            root_transcript if root_transcript.is_file() else None,
        )


def create_bundle(
    project: Project,
    git: GitState,
    *,
    to_account: str = "",
    transcript: Path | None = None,
) -> Path:
    """Compatibility wrapper around the persistent session handoff service."""

    sessions = SessionStore()
    session = sessions.current(project.session_key)
    if session is None:
        session = sessions.create(
            project,
            git,
            name="current-work",
            objective=project.objective,
        )
    bundle = HandoffService(sessions).create(
        project,
        git,
        session,
        to_account=to_account,
        transcript=transcript,
    )
    return bundle.session_dir
