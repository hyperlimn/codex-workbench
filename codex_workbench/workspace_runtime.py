from __future__ import annotations

from collections.abc import Callable
from typing import Any


class WorkspaceRuntimeRegistry:
    """Runtime-only pane objects isolated by durable project identity."""

    def __init__(self) -> None:
        self._projects: dict[str, dict[str, Any]] = {}

    def get(self, project_id: str, pane_id: str) -> Any | None:
        return self._projects.get(project_id, {}).get(pane_id)

    def put(self, project_id: str, pane_id: str, runtime: Any) -> Any:
        self._projects.setdefault(project_id, {})[pane_id] = runtime
        return runtime

    def project(self, project_id: str) -> tuple[Any, ...]:
        return tuple(self._projects.get(project_id, {}).values())

    def pop(
        self,
        project_id: str,
        pane_id: str,
        *,
        close: Callable[[Any], None] | None = None,
    ) -> Any | None:
        values = self._projects.get(project_id)
        runtime = values.pop(pane_id, None) if values is not None else None
        if runtime is not None and close is not None:
            close(runtime)
        if values == {}:
            self._projects.pop(project_id, None)
        return runtime

    def retain(
        self,
        project_id: str,
        pane_ids: set[str],
        *,
        close: Callable[[Any], None] | None = None,
    ) -> None:
        for pane_id in tuple(self._projects.get(project_id, {})):
            if pane_id not in pane_ids:
                self.pop(project_id, pane_id, close=close)

    def close_all(self, close: Callable[[Any], None]) -> None:
        for values in tuple(self._projects.values()):
            for runtime in tuple(values.values()):
                close(runtime)
        self._projects.clear()
