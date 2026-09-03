from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Protocol

from .models import Project, WorkSession


@dataclass(frozen=True)
class TranscriptCandidate:
    path: Path
    modified_at: str
    size: int
    confidence: str
    reason: str


class TranscriptDiscoveryAdapter(Protocol):
    """Replaceable boundary for suggesting already-exported transcripts."""

    def discover(
        self, project: Project, session: WorkSession | None = None
    ) -> list[TranscriptCandidate]: ...


class ProjectTranscriptDiscovery:
    """Conservative filename-based discovery; it never scrapes terminals."""

    KEYWORDS = {
        "transcript": 6,
        "codex": 4,
        "export": 4,
        "conversation": 3,
        "handoff": 2,
        "session": 1,
    }
    EXTENSIONS = {".md", ".txt", ".log", ".json"}
    EXCLUDED_DIRECTORIES = {
        ".git",
        ".venv",
        "venv",
        "node_modules",
        "__pycache__",
        "dist",
        "build",
    }

    def __init__(self, *, max_depth: int = 2, max_results: int = 12):
        self.max_depth = max(0, max_depth)
        self.max_results = max(1, max_results)

    def discover(
        self, project: Project, session: WorkSession | None = None
    ) -> list[TranscriptCandidate]:
        root = project.path
        if not root.is_dir():
            return []
        scored: list[tuple[int, float, TranscriptCandidate]] = []
        for path in self._files(root):
            name = path.name.casefold()
            score = sum(
                weight for keyword, weight in self.KEYWORDS.items() if keyword in name
            )
            if score <= 0:
                continue
            if session and session.name.casefold() in name:
                score += 3
            try:
                stat = path.stat()
            except OSError:
                continue
            if stat.st_size <= 0 or stat.st_size > 50 * 1024 * 1024:
                continue
            confidence = "high" if score >= 6 else "medium" if score >= 4 else "low"
            matches = [
                keyword for keyword in self.KEYWORDS if keyword in name
            ]
            candidate = TranscriptCandidate(
                path=path.resolve(strict=False),
                modified_at=datetime.fromtimestamp(
                    stat.st_mtime, timezone.utc
                ).isoformat(),
                size=stat.st_size,
                confidence=confidence,
                reason="filename contains " + ", ".join(matches),
            )
            scored.append((score, stat.st_mtime, candidate))
        scored.sort(key=lambda item: (item[0], item[1]), reverse=True)
        return [item[2] for item in scored[: self.max_results]]

    def _files(self, root: Path) -> list[Path]:
        result: list[Path] = []
        pending: list[tuple[Path, int]] = [(root, 0)]
        while pending:
            directory, depth = pending.pop()
            try:
                children = list(directory.iterdir())
            except OSError:
                continue
            for child in children:
                if child.is_symlink():
                    continue
                if child.is_file() and child.suffix.casefold() in self.EXTENSIONS:
                    result.append(child)
                elif (
                    child.is_dir()
                    and depth < self.max_depth
                    and child.name not in self.EXCLUDED_DIRECTORIES
                    and not child.name.startswith(".")
                ):
                    pending.append((child, depth + 1))
        return result
