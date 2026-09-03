from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .paths import settings_file
from .store import write_json


@dataclass
class WorkbenchSettings:
    """Small, durable preferences shared by CLI and GUI front ends."""

    preferred_terminal: str = "tilix"
    shell_mode: str = "embedded"
    launcher_path: str = ""
    clipboard_mode: str = "auto"
    low_usage_threshold: int = 15
    critical_usage_threshold: int = 5
    theme: str = "dark"
    last_project: str = ""

    def normalized(self) -> "WorkbenchSettings":
        self.low_usage_threshold = max(1, min(100, int(self.low_usage_threshold)))
        self.critical_usage_threshold = max(
            0, min(self.low_usage_threshold, int(self.critical_usage_threshold))
        )
        if self.clipboard_mode not in {"auto", "disabled"}:
            self.clipboard_mode = "auto"
        if self.theme not in {"dark", "system"}:
            self.theme = "dark"
        self.preferred_terminal = self.preferred_terminal.strip() or "tilix"
        if self.shell_mode not in {"embedded", "external"}:
            self.shell_mode = "embedded"
        self.launcher_path = self.launcher_path.strip()
        self.last_project = self.last_project.strip()
        return self

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": 2,
            "preferred_terminal": self.preferred_terminal,
            "shell_mode": self.shell_mode,
            "launcher_path": self.launcher_path,
            "clipboard_mode": self.clipboard_mode,
            "low_usage_threshold": self.low_usage_threshold,
            "critical_usage_threshold": self.critical_usage_threshold,
            "theme": self.theme,
            "last_project": self.last_project,
        }

    @classmethod
    def from_dict(cls, raw: object) -> "WorkbenchSettings":
        if not isinstance(raw, dict):
            return cls()
        # A v0.3 settings document only described an external terminal.
        legacy_shell_mode = (
            "external" if "shell_mode" not in raw else raw.get("shell_mode")
        )
        return cls(
            preferred_terminal=str(raw.get("preferred_terminal") or "tilix"),
            shell_mode=str(legacy_shell_mode or "embedded"),
            launcher_path=str(raw.get("launcher_path") or ""),
            clipboard_mode=str(raw.get("clipboard_mode") or "auto"),
            low_usage_threshold=_integer(raw.get("low_usage_threshold"), 15),
            critical_usage_threshold=_integer(
                raw.get("critical_usage_threshold"), 5
            ),
            theme=str(raw.get("theme") or "dark"),
            last_project=str(raw.get("last_project") or ""),
        ).normalized()


def _integer(value: object, fallback: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return fallback


class SettingsStore:
    def __init__(self, path: Path | None = None):
        self.path = path or settings_file()

    def load(self) -> WorkbenchSettings:
        try:
            return WorkbenchSettings.from_dict(
                json.loads(self.path.read_text(encoding="utf-8"))
            )
        except (OSError, json.JSONDecodeError):
            return WorkbenchSettings()

    def save(self, settings: WorkbenchSettings) -> Path:
        write_json(self.path, settings.normalized().to_dict())
        return self.path

    def update(self, **changes: object) -> WorkbenchSettings:
        settings = self.load()
        for field_name, value in changes.items():
            if value is not None and hasattr(settings, field_name):
                setattr(settings, field_name, value)
        self.save(settings)
        return settings
