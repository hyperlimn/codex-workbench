"""Workbench adapter for the bundled codex-start presentation backend."""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any, Mapping


BUNDLED_LAUNCHER_DIRECTORY = (
    Path(__file__).resolve().parent.parent / "integrations" / "codex-launcher"
)
if str(BUNDLED_LAUNCHER_DIRECTORY) not in sys.path:
    sys.path.insert(0, str(BUNDLED_LAUNCHER_DIRECTORY))

import codex_start as launcher_core  # noqa: E402
from codex_terminal_rail import (  # noqa: E402
    apply_terminal_theme,
    create_status_rail_widget,
)
from codex_terminal_theme import (  # noqa: E402
    StatusModel,
    ThemeModel,
    TranscriptExporter,
    TranscriptSession,
)


class CodexRailState:
    """Pane-local launcher status/theme state without GTK or rate-limit polling."""

    def __init__(
        self,
        account_name: str,
        cwd: Path,
        *,
        environment: Mapping[str, str] | None = None,
        account: Any | None = None,
        theme_store: Any | None = None,
        tracker_factory: Any | None = None,
    ) -> None:
        self.account_name = account_name
        self.cwd = cwd
        self.environment = dict(os.environ if environment is None else environment)
        self.account = account or self._resolve_account(account_name)
        self.theme_store = theme_store or launcher_core.ThemeStore()
        make_tracker = tracker_factory or launcher_core.RolloutTracker
        self.tracker = make_tracker(self.account, cwd)
        self.closed = False

    def _resolve_account(self, account_name: str) -> Any:
        try:
            entries = launcher_core.load_accounts()
        except SystemExit:
            entries = ()
        account = launcher_core.resolve_account(account_name, entries)
        if account is not None:
            return account
        # Never let an unresolved name inherit a different account's home.
        # The child reports the unknown account while this rail stays inert.
        return launcher_core.Account(account_name, Path(os.devnull))

    @property
    def status(self) -> StatusModel:
        return launcher_core.terminal_status_model(self.tracker.snapshot)

    @property
    def theme(self) -> ThemeModel:
        return self.theme_store.theme_model_for(self.account_name)

    def refresh(
        self, child_pid: int | None = None
    ) -> tuple[StatusModel, ThemeModel]:
        if not self.closed:
            self.tracker.refresh(child_pid)
        return self.status, self.theme

    def close(self) -> None:
        self.closed = True


__all__ = (
    "CodexRailState",
    "StatusModel",
    "ThemeModel",
    "TranscriptExporter",
    "TranscriptSession",
    "apply_terminal_theme",
    "create_status_rail_widget",
    "launcher_core",
)
