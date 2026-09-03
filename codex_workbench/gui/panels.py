from __future__ import annotations

from dataclasses import dataclass

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import GLib, Gtk  # noqa: E402


@dataclass(frozen=True)
class PanelPlacement:
    """Stable sizing intent for one Project Info card."""

    key: str
    min_width: int = 240
    preferred_span: int = 1
    grow: bool = True
    priority: int = 100


@dataclass(frozen=True)
class PanelSlot:
    key: str
    column: int
    row: int
    span: int


@dataclass(frozen=True)
class PanelLayout:
    columns: int
    slots: tuple[PanelSlot, ...]


@dataclass
class PanelSpec:
    widget: Gtk.Widget
    placement: PanelPlacement



@dataclass
class _PackedPanel:
    placement: PanelPlacement
    span: int

def _minimum_width_for_columns(
    columns: int,
    placements: tuple[PanelPlacement, ...],
    spacing: int,
) -> int:
    column_width = 1
    for item in placements:
        span = min(columns, max(1, item.preferred_span))
        content_width = max(1, item.min_width - spacing * (span - 1))
        column_width = max(column_width, (content_width + span - 1) // span)
    return column_width * columns + spacing * (columns - 1)


def responsive_column_count(
    width: int,
    placements: tuple[PanelPlacement, ...],
    *,
    spacing: int = 10,
    max_columns: int = 3,
) -> int:
    """Choose columns from available width and declared card minimums."""

    if width <= 0 or not placements:
        return 1
    for columns in range(max(1, max_columns), 1, -1):
        if width >= _minimum_width_for_columns(columns, placements, spacing):
            return columns
    return 1


def plan_panel_layout(
    width: int,
    placements: tuple[PanelPlacement, ...],
    *,
    spacing: int = 10,
    max_columns: int = 3,
) -> PanelLayout:
    """Pack cards in stable order and expand eligible cards to finish rows."""

    columns = responsive_column_count(
        width,
        placements,
        spacing=spacing,
        max_columns=max_columns,
    )
    rows: list[list[_PackedPanel]] = []
    current: list[_PackedPanel] = []
    used = 0

    def finish_row() -> None:
        nonlocal current, used
        if not current:
            return
        remaining = columns - used
        growers = [
            (item.placement.priority, -index, index)
            for index, item in enumerate(current)
            if item.placement.grow
        ]
        if remaining and growers:
            _priority, _reverse_index, index = min(growers)
            current[index].span += remaining
        rows.append(current)
        current = []
        used = 0

    for item in placements:
        span = min(columns, max(1, item.preferred_span))
        if current and used + span > columns:
            finish_row()
        current.append(_PackedPanel(item, span))
        used += span
        if used == columns:
            finish_row()
    finish_row()

    slots: list[PanelSlot] = []
    for row_index, row in enumerate(rows):
        column = 0
        for item in row:
            span = item.span
            slots.append(PanelSlot(item.placement.key, column, row_index, span))
            column += span
    return PanelLayout(columns, tuple(slots))


class ResponsivePanelGrid(Gtk.Grid):
    """Allocation-aware GTK grid backed by deterministic card metadata."""

    SPACING = 10
    MAX_COLUMNS = 3

    def __init__(self):
        super().__init__(
            column_spacing=self.SPACING,
            row_spacing=self.SPACING,
            column_homogeneous=True,
            hexpand=True,
        )
        self.add_css_class("project-panel-grid")
        self._panels: list[PanelSpec] = []
        self._layout = PanelLayout(1, ())
        self._reflow_source = 0
        self._surface: object | None = None
        self._surface_layout_handler = 0
        self.connect("map", self._mapped)
        self.connect("unmap", self._unmapped)

    @property
    def placements(self) -> tuple[PanelPlacement, ...]:
        return tuple(item.placement for item in self._panels)

    def append_panel(
        self,
        widget: Gtk.Widget,
        placement: PanelPlacement,
    ) -> None:
        if any(item.placement.key == placement.key for item in self._panels):
            raise ValueError(f"Duplicate panel placement key: {placement.key}")
        widget.set_hexpand(True)
        widget.set_halign(Gtk.Align.FILL)
        widget.set_valign(Gtk.Align.FILL)
        self._panels.append(PanelSpec(widget, placement))
        self._apply_layout(self.plan_for_width(self.get_width()))

    def plan_for_width(self, width: int) -> PanelLayout:
        return plan_panel_layout(
            width,
            self.placements,
            spacing=self.SPACING,
            max_columns=self.MAX_COLUMNS,
        )

    def _mapped(self, *_args: object) -> None:
        native = self.get_native()
        surface = native.get_surface() if native is not None else None
        if surface is not None:
            self._surface = surface
            self._surface_layout_handler = surface.connect(
                "layout", self._surface_layout
            )
        self._queue_allocated_width_reflow()

    def _unmapped(self, *_args: object) -> None:
        if self._surface is not None and self._surface_layout_handler:
            self._surface.disconnect(self._surface_layout_handler)
        self._surface = None
        self._surface_layout_handler = 0
        if self._reflow_source:
            GLib.source_remove(self._reflow_source)
            self._reflow_source = 0

    def _surface_layout(self, *_args: object) -> None:
        self._queue_allocated_width_reflow()

    def _queue_allocated_width_reflow(self) -> None:
        if not self._reflow_source:
            self._reflow_source = GLib.idle_add(
                self._reflow_after_allocation
            )

    def _reflow_after_allocation(self) -> bool:
        self._reflow_source = 0
        self._apply_layout(self.plan_for_width(self.get_width()))
        return GLib.SOURCE_REMOVE

    def _apply_layout(self, layout: PanelLayout) -> None:
        widgets = {item.placement.key: item.widget for item in self._panels}
        if layout == self._layout and all(
            widget.get_parent() is self for widget in widgets.values()
        ):
            return
        for widget in widgets.values():
            if widget.get_parent() is self:
                self.remove(widget)
        for slot in layout.slots:
            self.attach(
                widgets[slot.key],
                slot.column,
                slot.row,
                slot.span,
                1,
            )
        self._layout = layout
