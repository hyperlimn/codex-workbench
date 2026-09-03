from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable


def launcher_command() -> str:
    installed = shutil.which("codex-start")
    if installed:
        return installed
    bundled = (
        Path(__file__).resolve().parent.parent
        / "integrations"
        / "codex-launcher"
        / "codex-start"
    )
    return str(bundled)


@dataclass(frozen=True)
class CodexStatus:
    account: str
    model: str = ""
    cwd: str = ""
    five_hour_remaining: int | None = None
    weekly_remaining: int | None = None
    reset: str = ""
    raw: str = ""
    data: dict[str, Any] = field(default_factory=dict)
    error: str = ""

    @property
    def availability(self) -> str:
        if self.error:
            return "unavailable"
        known = [
            value
            for value in (self.five_hour_remaining, self.weekly_remaining)
            if value is not None
        ]
        if any(value <= 0 for value in known):
            return "exhausted"
        if any(value <= 10 for value in known):
            return "warning"
        return "available" if known else "unknown"

    def usage_level(
        self, *, low_threshold: int = 15, critical_threshold: int = 5
    ) -> str:
        """GUI-oriented severity without changing the v0.2 availability API."""

        if self.error:
            return "unavailable"
        known = [
            value
            for value in (self.five_hour_remaining, self.weekly_remaining)
            if value is not None
        ]
        if not known:
            return "unknown"
        lowest = min(known)
        if lowest <= 0:
            return "exhausted"
        if lowest <= critical_threshold:
            return "critical"
        if lowest <= low_threshold:
            return "low"
        return "normal"

    def as_dict(self) -> dict[str, Any]:
        result = dict(self.data)
        result.update(
            {
                "account": self.account,
                "model": self.model,
                "cwd": self.cwd,
                "five_hour_remaining": self.five_hour_remaining,
                "weekly_remaining": self.weekly_remaining,
                "reset": self.reset,
                "availability": self.availability,
            }
        )
        if self.raw:
            result["raw"] = self.raw
        if self.error:
            result["error"] = self.error
        return result

    def summary(self) -> str:
        if self.error:
            return f"unavailable ({self.error})"
        fields = []
        if self.model:
            fields.append(self.model)
        if self.five_hour_remaining is not None:
            fields.append(f"5h {self.five_hour_remaining}% left")
        if self.weekly_remaining is not None:
            fields.append(f"week {self.weekly_remaining}% left")
        if self.reset:
            fields.append(f"reset {self.reset}")
        fields.append(self.availability)
        return " | ".join(fields)


def _walk(value: Any) -> Iterable[tuple[str, Any]]:
    if isinstance(value, dict):
        for key, child in value.items():
            yield str(key), child
            yield from _walk(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk(child)


def _number(value: Any, *, used: bool = False) -> int | None:
    if isinstance(value, dict):
        for key in ("remaining_percent", "remainingPercent", "percent_left"):
            if key in value:
                return _number(value[key])
        for key in ("used_percent", "usedPercent"):
            if key in value:
                return _number(value[key], used=True)
        return None
    if isinstance(value, (int, float)):
        number = round(float(value))
        return max(0, min(100, 100 - number if used else number))
    if isinstance(value, str):
        match = re.search(r"(\d+(?:\.\d+)?)\s*%\s*(left|remaining)?", value)
        if match:
            number = round(float(match.group(1)))
            return max(0, min(100, 100 - number if used else number))
    return None


def _field(data: dict[str, Any], names: set[str]) -> Any:
    normalized = {name.casefold().replace("-", "_") for name in names}
    for key, value in _walk(data):
        if key.casefold().replace("-", "_") in normalized:
            return value
    return None


def _from_json(account: str, data: dict[str, Any], raw: str) -> CodexStatus:
    five = _number(
        _field(data, {"five_hour", "five_hour_remaining", "5h", "primary"})
    )
    week = _number(
        _field(data, {"weekly", "weekly_remaining", "week", "secondary"})
    )
    model = _field(data, {"model", "model_name"})
    cwd = _field(data, {"cwd", "current_directory", "directory"})
    reset = _field(data, {"reset", "reset_time", "resets_at"})
    return CodexStatus(
        account=account,
        model=str(model or ""),
        cwd=str(cwd or ""),
        five_hour_remaining=five,
        weekly_remaining=week,
        reset=str(reset or ""),
        raw=raw,
        data=data,
    )


def _plain_value(raw: str, label: str) -> str:
    match = re.search(
        rf"(?:^|\|)\s*{re.escape(label)}\s*:\s*(.*?)(?=\s*\||$)",
        raw,
        flags=re.IGNORECASE,
    )
    return match.group(1).strip() if match else ""


def parse_status(account: str, raw: str) -> CodexStatus:
    text = raw.strip()
    if not text:
        return CodexStatus(account, error="launcher returned no status")
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        data = None
    if isinstance(data, dict):
        return _from_json(account, data, text)
    account_field = _plain_value(text, "account")
    parsed_account = account_field.split("•", 1)[0].strip() or account
    return CodexStatus(
        account=parsed_account,
        model=_plain_value(text, "model"),
        cwd=_plain_value(text, "dir"),
        five_hour_remaining=_number(_plain_value(text, "5h")),
        weekly_remaining=_number(_plain_value(text, "week")),
        reset=_plain_value(text, "reset"),
        raw=text,
    )


class CodexAdapter:
    """Boundary around the existing codex-start engine."""

    def __init__(self, launcher: str | None = None):
        self.launcher = launcher or launcher_command()

    @property
    def available(self) -> bool:
        path = Path(self.launcher)
        return path.is_file() and os.access(path, os.X_OK)

    def read_status(self, account: str) -> CodexStatus:
        if not account:
            return CodexStatus("", error="no Codex account configured")
        try:
            result = subprocess.run(
                [self.launcher, "--status", account],
                text=True,
                capture_output=True,
                timeout=15,
            )
        except (OSError, subprocess.SubprocessError) as error:
            return CodexStatus(account, error=str(error))
        if result.returncode != 0:
            detail = (result.stderr or result.stdout).strip()
            return CodexStatus(
                account,
                raw=result.stdout.strip(),
                error=detail or f"launcher exited {result.returncode}",
            )
        return parse_status(account, result.stdout)

    def list_accounts(self) -> list[str]:
        try:
            result = subprocess.run(
                [self.launcher, "accounts"],
                text=True,
                capture_output=True,
                timeout=10,
            )
        except (OSError, subprocess.SubprocessError):
            return []
        if result.returncode != 0:
            return []
        accounts = []
        for line in result.stdout.splitlines():
            match = re.match(r"\s*\d+\.\s+([^:]+):", line)
            if match:
                accounts.append(match.group(1).strip())
        return accounts

    def command(self, account: str, *, initial_prompt: str = "") -> list[str]:
        command = [self.launcher]
        if account:
            command.append(account)
        if initial_prompt:
            command.extend(("--", initial_prompt))
        return command

    def launch(
        self, account: str, cwd: Path, *, initial_prompt: str = ""
    ) -> int:
        return subprocess.call(self.command(account, initial_prompt=initial_prompt), cwd=cwd)


def launch(account: str, cwd: Path, *, initial_prompt: str = "") -> int:
    return CodexAdapter().launch(account, cwd, initial_prompt=initial_prompt)


def status(account: str) -> dict[str, Any]:
    if not account:
        return {}
    return CodexAdapter().read_status(account).as_dict()
