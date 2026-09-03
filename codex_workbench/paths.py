from __future__ import annotations

import os
from pathlib import Path


def config_root() -> Path:
    base = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    return base / "codex-workbench"


def data_root() -> Path:
    base = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share"))
    return base / "codex-workbench"


def projects_file() -> Path:
    return config_root() / "projects.json"


def sessions_root() -> Path:
    return data_root() / "sessions"


def settings_file() -> Path:
    return config_root() / "settings.json"


def activity_file() -> Path:
    return data_root() / "activity.json"


def desktop_data_root() -> Path:
    base = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share"))
    return base
