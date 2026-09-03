from __future__ import annotations

from dataclasses import dataclass

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gtk  # noqa: E402


@dataclass
class PanelSpec:
    widget: Gtk.Widget
    large_span: int = 1
    medium_span: int = 1


class ResponsivePanelGrid(Gtk.Grid):
    """Breakpoint-based GTK grid with spans and no freeform positioning."""

    def __init__(self):
        super().__init__(
            column_spacing=12,
            row_spacing=12,
            column_homogeneous=True,
        )
        self.add_css_class("project-panel-grid")
        self._panels: list[PanelSpec] = []
        self._columns = 0
        self.connect("notify::width", self._width_changed)

    def append_panel(
        self,
        widget: Gtk.Widget,
        *,
        large_span: int = 1,
        medium_span: int = 1,
    ) -> None:
        self._panels.append(
            PanelSpec(widget, max(1, large_span), max(1, medium_span))
        )
        self._reflow(self._column_count(self.get_width()))

    @staticmethod
    def _column_count(width: int) -> int:
        if width >= 930:
            return 3
        if width >= 590:
            return 2
        return 1

    def _width_changed(self, *_args: object) -> None:
        self._reflow(self._column_count(self.get_width()))

    def _reflow(self, columns: int) -> None:
        if columns == self._columns and all(
            item.widget.get_parent() is self for item in self._panels
        ):
            return
        self._columns = columns
        for item in self._panels:
            if item.widget.get_parent() is self:
                self.remove(item.widget)
        row = 0
        column = 0
        for item in self._panels:
            span = (
                min(columns, item.large_span)
                if columns == 3
                else min(columns, item.medium_span)
                if columns == 2
                else 1
            )
            if column + span > columns:
                row += 1
                column = 0
            self.attach(item.widget, column, row, span, 1)
            column += span
            if column >= columns:
                row += 1
                column = 0
