from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable
from uuid import uuid4


PANE_PROVIDER_TYPES = ("codex", "terminal", "browser", "files")
PANE_PLACEMENTS = ("left", "right", "above", "below")


def stable_id(prefix: str) -> str:
    """Return a readable, persistence-safe identity for user-created items."""

    return f"{prefix}-{uuid4().hex}"


@dataclass
class ProjectCommand:
    id: str
    name: str
    command: str
    description: str = ""
    category: str = "Other"
    working_directory: str = ""
    extra: dict[str, Any] = field(default_factory=dict, repr=False)

    def to_dict(self) -> dict[str, Any]:
        result = dict(self.extra)
        result.update(
            {
                "id": self.id,
                "name": self.name,
                "command": self.command,
                "description": self.description,
                "category": self.category,
                "working_directory": self.working_directory,
            }
        )
        return result

    @classmethod
    def from_value(cls, raw: object) -> "ProjectCommand | None":
        if isinstance(raw, cls):
            return cls(
                raw.id,
                raw.name,
                raw.command,
                raw.description,
                raw.category,
                raw.working_directory,
                dict(raw.extra),
            )
        if not isinstance(raw, dict):
            return None
        name = str(raw.get("name") or "").strip()
        command = str(raw.get("command") or "").strip()
        if not name or not command:
            return None
        known = {
            "id",
            "name",
            "command",
            "description",
            "explanation",
            "category",
            "working_directory",
            "root",
        }
        return cls(
            id=str(raw.get("id") or stable_id("command")),
            name=name,
            command=command,
            description=str(
                raw.get("description") or raw.get("explanation") or ""
            ).strip(),
            category=str(raw.get("category") or "Other").strip() or "Other",
            working_directory=str(
                raw.get("working_directory") or raw.get("root") or ""
            ).strip(),
            extra={key: value for key, value in raw.items() if key not in known},
        )


@dataclass
class WorkspacePane:
    id: str
    provider_type: str
    title: str
    provider_state: dict[str, Any] = field(default_factory=dict)
    docked: bool = True
    dock_anchor: str = ""
    dock_placement: str = "right"
    extra: dict[str, Any] = field(default_factory=dict, repr=False)

    def __post_init__(self) -> None:
        self.provider_type = self.provider_type.strip().lower()
        if self.provider_type not in PANE_PROVIDER_TYPES:
            self.provider_type = "terminal"
        if self.dock_placement not in PANE_PLACEMENTS:
            self.dock_placement = "right"
        if not isinstance(self.provider_state, dict):
            self.provider_state = {}

    def to_dict(self) -> dict[str, Any]:
        result = dict(self.extra)
        result.update(
            {
                "id": self.id,
                "provider_type": self.provider_type,
                "title": self.title,
                "provider_state": dict(self.provider_state),
                "docked": self.docked,
                "dock_anchor": self.dock_anchor,
                "dock_placement": self.dock_placement,
            }
        )
        return result

    @classmethod
    def from_value(cls, raw: object) -> "WorkspacePane | None":
        if isinstance(raw, cls):
            return cls(
                raw.id,
                raw.provider_type,
                raw.title,
                dict(raw.provider_state),
                raw.docked,
                raw.dock_anchor,
                raw.dock_placement,
                dict(raw.extra),
            )
        if not isinstance(raw, dict):
            return None
        pane_id = str(raw.get("id") or "").strip()
        provider_type = str(
            raw.get("provider_type") or raw.get("provider") or ""
        ).strip().lower()
        if not pane_id or provider_type not in PANE_PROVIDER_TYPES:
            return None
        state = raw.get("provider_state")
        if not isinstance(state, dict):
            state = {}
        known = {
            "id",
            "provider_type",
            "provider",
            "title",
            "provider_state",
            "docked",
            "dock_anchor",
            "dock_placement",
        }
        return cls(
            id=pane_id,
            provider_type=provider_type,
            title=str(raw.get("title") or provider_type.title()),
            provider_state={str(key): value for key, value in state.items()},
            docked=bool(raw.get("docked", True)),
            dock_anchor=str(raw.get("dock_anchor") or ""),
            dock_placement=str(raw.get("dock_placement") or "right"),
            extra={key: value for key, value in raw.items() if key not in known},
        )


@dataclass
class SplitLayout:
    """A small recursive split tree; leaves refer to persistent pane IDs."""

    pane_id: str = ""
    orientation: str = "horizontal"
    first: "SplitLayout | None" = None
    second: "SplitLayout | None" = None
    ratio: float = 0.5
    extra: dict[str, Any] = field(default_factory=dict, repr=False)

    @property
    def leaf(self) -> bool:
        return bool(self.pane_id)

    @classmethod
    def pane(cls, pane_id: str) -> "SplitLayout":
        return cls(pane_id=pane_id)

    @classmethod
    def split(
        cls,
        orientation: str,
        first: "SplitLayout",
        second: "SplitLayout",
        ratio: float = 0.5,
    ) -> "SplitLayout":
        return cls(
            orientation=(
                orientation if orientation in {"horizontal", "vertical"}
                else "horizontal"
            ),
            first=first,
            second=second,
            ratio=max(0.15, min(0.85, float(ratio))),
        )

    def pane_ids(self) -> tuple[str, ...]:
        if self.leaf:
            return (self.pane_id,)
        result: list[str] = []
        if self.first is not None:
            result.extend(self.first.pane_ids())
        if self.second is not None:
            result.extend(self.second.pane_ids())
        return tuple(result)

    def to_dict(self) -> dict[str, Any]:
        result = dict(self.extra)
        if self.leaf:
            result.update({"type": "pane", "pane_id": self.pane_id})
        else:
            result.update(
                {
                    "type": "split",
                    "orientation": self.orientation,
                    "ratio": self.ratio,
                    "first": self.first.to_dict() if self.first else None,
                    "second": self.second.to_dict() if self.second else None,
                }
            )
        return result

    @classmethod
    def from_value(cls, raw: object) -> "SplitLayout | None":
        if isinstance(raw, cls):
            return cls.from_value(raw.to_dict())
        if not isinstance(raw, dict):
            return None
        node_type = str(raw.get("type") or "")
        pane_id = str(raw.get("pane_id") or "").strip()
        if node_type == "pane" or pane_id:
            return cls.pane(pane_id) if pane_id else None
        first = cls.from_value(raw.get("first"))
        second = cls.from_value(raw.get("second"))
        if first is None:
            return second
        if second is None:
            return first
        try:
            ratio = float(raw.get("ratio", 0.5))
        except (TypeError, ValueError):
            ratio = 0.5
        known = {"type", "orientation", "ratio", "first", "second"}
        node = cls.split(
            str(raw.get("orientation") or "horizontal"),
            first,
            second,
            ratio,
        )
        node.extra = {
            key: value for key, value in raw.items() if key not in known
        }
        return node


def _without_pane(
    node: SplitLayout | None, pane_id: str
) -> SplitLayout | None:
    if node is None:
        return None
    if node.leaf:
        return None if node.pane_id == pane_id else node
    first = _without_pane(node.first, pane_id)
    second = _without_pane(node.second, pane_id)
    if first is None:
        return second
    if second is None:
        return first
    return SplitLayout.split(node.orientation, first, second, node.ratio)


def _insert_next_to(
    node: SplitLayout,
    anchor_id: str,
    pane_id: str,
    placement: str,
) -> tuple[SplitLayout, bool]:
    if node.leaf:
        if node.pane_id != anchor_id:
            return node, False
        orientation = (
            "horizontal" if placement in {"left", "right"} else "vertical"
        )
        added = SplitLayout.pane(pane_id)
        existing = SplitLayout.pane(anchor_id)
        if placement in {"left", "above"}:
            return SplitLayout.split(orientation, added, existing), True
        return SplitLayout.split(orientation, existing, added), True
    if node.first is not None:
        first, inserted = _insert_next_to(
            node.first, anchor_id, pane_id, placement
        )
        if inserted:
            return SplitLayout.split(
                node.orientation, first, node.second or first, node.ratio
            ), True
    if node.second is not None:
        second, inserted = _insert_next_to(
            node.second, anchor_id, pane_id, placement
        )
        if inserted:
            return SplitLayout.split(
                node.orientation, node.first or second, second, node.ratio
            ), True
    return node, False


def _layout_with_allowed(
    node: SplitLayout | None, allowed: set[str], seen: set[str]
) -> SplitLayout | None:
    if node is None:
        return None
    if node.leaf:
        if node.pane_id not in allowed or node.pane_id in seen:
            return None
        seen.add(node.pane_id)
        return SplitLayout.pane(node.pane_id)
    first = _layout_with_allowed(node.first, allowed, seen)
    second = _layout_with_allowed(node.second, allowed, seen)
    if first is None:
        return second
    if second is None:
        return first
    return SplitLayout.split(node.orientation, first, second, node.ratio)


@dataclass
class ProjectWorkspace:
    info_collapsed: bool = False
    prompt_hold: str = ""
    commands: list[ProjectCommand] = field(default_factory=list)
    panes: list[WorkspacePane] = field(default_factory=list)
    layout: SplitLayout | None = None
    focused_pane_id: str = ""
    extra: dict[str, Any] = field(default_factory=dict, repr=False)

    def __post_init__(self) -> None:
        normalized_commands: list[ProjectCommand] = []
        command_ids: set[str] = set()
        for raw in self.commands:
            command = ProjectCommand.from_value(raw)
            if command is None:
                continue
            if command.id in command_ids:
                command.id = stable_id("command")
            command_ids.add(command.id)
            normalized_commands.append(command)
        self.commands = normalized_commands

        normalized_panes: list[WorkspacePane] = []
        pane_ids: set[str] = set()
        for raw in self.panes:
            pane = WorkspacePane.from_value(raw)
            if pane is None:
                continue
            if pane.id in pane_ids:
                pane.id = stable_id("pane")
            pane_ids.add(pane.id)
            normalized_panes.append(pane)
        self.panes = normalized_panes
        self.layout = SplitLayout.from_value(self.layout)
        self._normalize_layout()
        if self.focused_pane_id not in pane_ids:
            self.focused_pane_id = ""

    def _normalize_layout(self) -> None:
        docked = {pane.id for pane in self.panes if pane.docked}
        seen: set[str] = set()
        self.layout = _layout_with_allowed(self.layout, docked, seen)
        for pane in self.panes:
            if not pane.docked or pane.id in seen:
                continue
            if self.layout is None:
                self.layout = SplitLayout.pane(pane.id)
            else:
                self.layout = SplitLayout.split(
                    "horizontal", self.layout, SplitLayout.pane(pane.id)
                )
            seen.add(pane.id)

    def pane(self, pane_id: str) -> WorkspacePane | None:
        return next((pane for pane in self.panes if pane.id == pane_id), None)

    def panes_of_type(self, provider_type: str) -> tuple[WorkspacePane, ...]:
        return tuple(
            pane for pane in self.panes if pane.provider_type == provider_type
        )

    def add_pane(
        self,
        provider_type: str,
        *,
        title: str = "",
        provider_state: dict[str, Any] | None = None,
        anchor_id: str = "",
        placement: str = "right",
        pane_id: str = "",
    ) -> WorkspacePane:
        provider = provider_type.strip().lower()
        if provider not in PANE_PROVIDER_TYPES:
            raise ValueError(f"Unknown workspace pane provider: {provider_type}")
        if placement not in PANE_PLACEMENTS:
            raise ValueError(f"Unknown pane placement: {placement}")
        defaults = {
            "codex": "Codex",
            "terminal": "Terminal · root",
            "browser": "Browser",
            "files": "Files · root",
        }
        pane = WorkspacePane(
            id=pane_id or stable_id("pane"),
            provider_type=provider,
            title=title.strip() or defaults[provider],
            provider_state=dict(provider_state or {}),
        )
        if self.pane(pane.id) is not None:
            raise ValueError(f"Duplicate workspace pane ID: {pane.id}")
        self.panes.append(pane)
        self.layout = self._insert(pane.id, anchor_id, placement)
        return pane

    def _insert(
        self, pane_id: str, anchor_id: str, placement: str
    ) -> SplitLayout:
        if self.layout is None:
            return SplitLayout.pane(pane_id)
        anchor = anchor_id or self.layout.pane_ids()[-1]
        inserted, found = _insert_next_to(
            self.layout, anchor, pane_id, placement
        )
        if found:
            return inserted
        return SplitLayout.split(
            "horizontal", self.layout, SplitLayout.pane(pane_id)
        )

    def close_pane(self, pane_id: str) -> WorkspacePane | None:
        pane = self.pane(pane_id)
        if pane is None:
            return None
        self.layout = _without_pane(self.layout, pane_id)
        self.panes = [item for item in self.panes if item.id != pane_id]
        if self.focused_pane_id == pane_id:
            self.focused_pane_id = ""
        for item in self.panes:
            if item.dock_anchor == pane_id:
                item.dock_anchor = ""
        return pane

    def move_pane(self, pane_id: str, anchor_id: str, placement: str) -> None:
        pane = self.pane(pane_id)
        if pane is None or not pane.docked:
            raise ValueError(f"Unknown docked pane: {pane_id}")
        if placement not in PANE_PLACEMENTS:
            raise ValueError(f"Unknown pane placement: {placement}")
        if anchor_id == pane_id or self.pane(anchor_id) is None:
            candidates = [item for item in self.layout_ids() if item != pane_id]
            anchor_id = candidates[-1] if candidates else ""
        self.layout = _without_pane(self.layout, pane_id)
        self.layout = self._insert(pane_id, anchor_id, placement)

    def layout_ids(self) -> tuple[str, ...]:
        return self.layout.pane_ids() if self.layout else ()

    def undock_pane(self, pane_id: str) -> WorkspacePane:
        pane = self.pane(pane_id)
        if pane is None:
            raise ValueError(f"Unknown workspace pane: {pane_id}")
        if not pane.docked:
            return pane
        ids = self.layout_ids()
        try:
            index = ids.index(pane_id)
        except ValueError:
            index = -1
        if len(ids) > 1:
            if index > 0:
                pane.dock_anchor = ids[index - 1]
                pane.dock_placement = "right"
            else:
                pane.dock_anchor = ids[1]
                pane.dock_placement = "left"
        else:
            pane.dock_anchor = ""
        pane.docked = False
        self.layout = _without_pane(self.layout, pane_id)
        if self.focused_pane_id == pane_id:
            self.focused_pane_id = ""
        return pane

    def dock_pane(self, pane_id: str) -> WorkspacePane:
        pane = self.pane(pane_id)
        if pane is None:
            raise ValueError(f"Unknown workspace pane: {pane_id}")
        if pane.docked:
            return pane
        pane.docked = True
        anchor = (
            pane.dock_anchor
            if pane.dock_anchor in self.layout_ids()
            else ""
        )
        self.layout = self._insert(pane.id, anchor, pane.dock_placement)
        return pane

    def focus(self, pane_id: str = "") -> None:
        if pane_id and (self.pane(pane_id) is None or pane_id not in self.layout_ids()):
            raise ValueError(f"Unknown docked pane: {pane_id}")
        self.focused_pane_id = pane_id

    def to_dict(self) -> dict[str, Any]:
        result = dict(self.extra)
        result.update(
            {
                "info_collapsed": self.info_collapsed,
                "prompt_hold": self.prompt_hold,
                "commands": [command.to_dict() for command in self.commands],
                "panes": [pane.to_dict() for pane in self.panes],
                "layout": self.layout.to_dict() if self.layout else None,
                "focused_pane_id": self.focused_pane_id,
            }
        )
        return result

    @classmethod
    def from_value(cls, raw: object) -> "ProjectWorkspace":
        if isinstance(raw, cls):
            return cls(
                info_collapsed=raw.info_collapsed,
                prompt_hold=raw.prompt_hold,
                commands=list(raw.commands),
                panes=list(raw.panes),
                layout=raw.layout,
                focused_pane_id=raw.focused_pane_id,
                extra=dict(raw.extra),
            )
        if not isinstance(raw, dict):
            return cls()
        commands = raw.get("commands")
        panes = raw.get("panes")
        known = {
            "info_collapsed",
            "prompt_hold",
            "commands",
            "panes",
            "layout",
            "focused_pane_id",
        }
        return cls(
            info_collapsed=bool(raw.get("info_collapsed", False)),
            prompt_hold=str(raw.get("prompt_hold") or ""),
            commands=list(commands) if isinstance(commands, list) else [],
            panes=list(panes) if isinstance(panes, list) else [],
            layout=SplitLayout.from_value(raw.get("layout")),
            focused_pane_id=str(raw.get("focused_pane_id") or ""),
            extra={key: value for key, value in raw.items() if key not in known},
        )


def first_pane_id(values: Iterable[WorkspacePane]) -> str:
    return next((pane.id for pane in values), "")
