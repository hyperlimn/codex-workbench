from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .models import utc_now
from .paths import activity_file
from .store import write_json


@dataclass(frozen=True)
class ActivityRecord:
    id: str
    timestamp: str
    action: str
    summary: str
    project: str = ""
    session: str = ""
    account: str = ""
    detail: str = ""
    success: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            field_name: getattr(self, field_name)
            for field_name in self.__dataclass_fields__
        }

    @classmethod
    def from_dict(cls, raw: object) -> "ActivityRecord | None":
        if not isinstance(raw, dict):
            return None
        try:
            return cls(
                id=str(raw["id"]),
                timestamp=str(raw["timestamp"]),
                action=str(raw["action"]),
                summary=str(raw["summary"]),
                project=str(raw.get("project") or ""),
                session=str(raw.get("session") or ""),
                account=str(raw.get("account") or ""),
                detail=str(raw.get("detail") or ""),
                success=bool(raw.get("success", True)),
            )
        except KeyError:
            return None


class ActivityStore:
    """Bounded, human-readable local history of explicit Workbench actions."""

    def __init__(self, path: Path | None = None, *, limit: int = 250):
        self.path = path or activity_file()
        self.limit = max(10, limit)

    def all(self) -> list[ActivityRecord]:
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return []
        rows = raw.get("activity", []) if isinstance(raw, dict) else []
        records = [ActivityRecord.from_dict(item) for item in rows]
        return [item for item in records if item is not None]

    def recent(
        self, *, project: str = "", limit: int = 30
    ) -> list[ActivityRecord]:
        records = self.all()
        if project:
            records = [item for item in records if item.project == project]
        return records[: max(0, limit)]

    def record(
        self,
        action: str,
        summary: str,
        *,
        project: str = "",
        session: str = "",
        account: str = "",
        detail: str = "",
        success: bool = True,
    ) -> ActivityRecord:
        timestamp = utc_now()
        record = ActivityRecord(
            id=f"{timestamp}-{uuid.uuid4().hex[:8]}",
            timestamp=timestamp,
            action=action,
            summary=summary,
            project=project,
            session=session,
            account=account,
            detail=detail,
            success=success,
        )
        records = [record, *self.all()][: self.limit]
        write_json(
            self.path,
            {"version": 1, "activity": [item.to_dict() for item in records]},
        )
        return record
