"""Reusable GTK4 status rail for an existing Codex terminal host.

This module is intentionally smaller than ``codex_terminal_ui.py`` in the
standalone launcher. It owns presentation widgets and terminal colors only;
it does not create a window, a VTE terminal, a PTY, or a Codex process.
"""

from __future__ import annotations

import html
from types import SimpleNamespace
from typing import Any

from codex_terminal_theme import (
    RailGroup,
    RailLayout,
    RailSegment,
    StatusModel,
    StatusRail,
    ThemeModel,
    responsive_rail_layout,
)


class StatusRailUnavailable(RuntimeError):
    """The optional GTK presentation stack could not be loaded."""


def _load_gtk() -> SimpleNamespace:
    try:
        import gi

        gi.require_version("Gdk", "4.0")
        gi.require_version("Gtk", "4.0")
        gi.require_version("Pango", "1.0")
        from gi.repository import Gdk, Gtk, Pango
    except (ImportError, ValueError) as error:
        raise StatusRailUnavailable(str(error)) from error
    return SimpleNamespace(Gdk=Gdk, Gtk=Gtk, Pango=Pango)


def _safe_text(value: str) -> str:
    return "".join(
        " " if ord(character) < 32 or 127 <= ord(character) < 160 else character
        for character in value
    )


def _markup(
    segments: tuple[RailSegment, ...], theme: ThemeModel
) -> str:
    result: list[str] = []
    colors = theme.as_dict()
    for segment in segments:
        weight = "bold" if segment.bold else "normal"
        size = ' size="small"' if segment.small else ""
        result.append(
            f'<span foreground="{colors[segment.theme_field]}" '
            f'weight="{weight}"{size}>'
            f"{html.escape(_safe_text(segment.text))}</span>"
        )
    return "".join(result)


_STATUS_RAIL_CLASS: type | None = None


def _status_rail_class(modules: SimpleNamespace) -> type:
    global _STATUS_RAIL_CLASS
    if _STATUS_RAIL_CLASS is not None:
        return _STATUS_RAIL_CLASS

    Gtk = modules.Gtk
    Pango = modules.Pango

    class StatusRailWidget(Gtk.Box):
        """Measured one/two-row Codex chrome that never owns a terminal."""

        def __init__(self, status: StatusModel, theme: ThemeModel):
            super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=0)
            self.add_css_class("codex-status-rail")
            self.set_hexpand(True)
            self.presentation = StatusRail(status, theme)
            self._group_labels: dict[str, list[Any]] = {}
            self._action_boxes: list[Any] = []
            self._action_buttons: list[Any] = []
            self._wide_natural_width = 0
            self._layout: RailLayout | None = None

            self._wide = self._build_layout(
                (("directory", "identity", "model", "five_hour", "weekly"),),
                actions_row=0,
            )
            self._narrow = self._build_layout(
                (
                    ("directory", "identity", "model"),
                    ("five_hour", "weekly"),
                ),
                actions_row=1,
            )
            self._narrow.set_visible(False)
            self.append(self._wide)
            self.append(self._narrow)

            self._css_provider = Gtk.CssProvider()
            self.get_style_context().add_provider(
                self._css_provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
            )
            self.presentation.set_change_handler(self._render)

        @property
        def responsive_layout(self) -> RailLayout | None:
            return self._layout

        @property
        def wide_natural_width(self) -> int:
            return self._wide_natural_width

        def do_size_allocate(
            self, width: int, height: int, baseline: int
        ) -> None:
            self.select_layout_for_width(width)
            Gtk.Box.do_size_allocate(self, width, height, baseline)

        def _build_layout(
            self,
            rows: tuple[tuple[str, ...], ...],
            *,
            actions_row: int,
        ) -> Any:
            container = Gtk.Box(
                orientation=Gtk.Orientation.VERTICAL, spacing=0
            )
            for row_index, names in enumerate(rows):
                row = Gtk.Box(
                    orientation=Gtk.Orientation.HORIZONTAL, spacing=0
                )
                row.add_css_class("codex-status-row")
                row.set_hexpand(True)
                for index, name in enumerate(names):
                    if index:
                        separator = Gtk.Label(label="|")
                        separator.add_css_class("codex-status-separator")
                        row.append(separator)
                    label = Gtk.Label()
                    label.add_css_class("codex-status-text")
                    label.set_xalign(0.0)
                    label.set_single_line_mode(True)
                    label.set_ellipsize(Pango.EllipsizeMode.END)
                    if name == "directory":
                        label.set_hexpand(True)
                    self._group_labels.setdefault(name, []).append(label)
                    row.append(label)
                if row_index == actions_row:
                    actions = Gtk.Box(
                        orientation=Gtk.Orientation.HORIZONTAL, spacing=2
                    )
                    actions.add_css_class("codex-status-actions")
                    row.append(actions)
                    self._action_boxes.append(actions)
                container.append(row)
            return container

        def select_layout_for_width(self, width: int) -> RailLayout | None:
            if width <= 0 or self._wide_natural_width <= 0:
                return self._layout
            self._layout = responsive_rail_layout(
                width, self._wide_natural_width
            )
            narrow = self._layout.is_two_row
            self._wide.set_visible(not narrow)
            self._narrow.set_visible(narrow)
            return self._layout

        def update(
            self,
            *,
            status: StatusModel | None = None,
            theme: ThemeModel | None = None,
        ) -> None:
            self.presentation.update(status=status, theme=theme)

        def add_action(
            self,
            label: str,
            callback: Any,
            *,
            tooltip: str | None = None,
        ) -> Any:
            """Add the same small action to both measured layout variants."""

            buttons = []
            for actions in self._action_boxes:
                button = Gtk.Button(label=label)
                button.add_css_class("flat")
                button.add_css_class("codex-status-action")
                if tooltip:
                    button.set_tooltip_text(tooltip)
                button.connect("clicked", callback)
                actions.append(button)
                buttons.append(button)
                self._action_buttons.append(button)
            self._measure_wide_layout()
            return buttons[0]

        def set_actions_sensitive(self, sensitive: bool) -> None:
            for button in self._action_buttons:
                button.set_sensitive(sensitive)

        def disconnect_updates(self) -> None:
            """Release the presentation callback when its pane closes."""

            self.presentation.set_change_handler(None, notify=False)

        def _measure_wide_layout(self) -> None:
            _minimum, natural, _minimum_baseline, _natural_baseline = (
                self._wide.measure(Gtk.Orientation.HORIZONTAL, -1)
            )
            self._wide_natural_width = natural
            self.select_layout_for_width(self.get_width())

        def _render(
            self,
            status: StatusModel,
            theme: ThemeModel,
            groups: tuple[RailGroup, ...],
        ) -> None:
            by_name = {group.name: group for group in groups}
            for name, labels in self._group_labels.items():
                group = by_name[name]
                for label in labels:
                    label.set_markup(_markup(group.segments, theme))
                    if name == "directory":
                        label.set_tooltip_text(status.directory.full)
            css = f"""
                .codex-status-rail {{
                    background: {theme.background};
                    border-bottom: 1px solid {theme.separators};
                }}
                .codex-status-row {{
                    min-height: 28px;
                    padding: 0 7px;
                }}
                .codex-status-text {{
                    padding: 5px 5px 4px 5px;
                    font-family: monospace;
                    font-size: 11pt;
                }}
                .codex-status-separator {{
                    padding: 0 3px;
                    color: {theme.separators};
                    font-family: monospace;
                    font-size: 10pt;
                }}
                .codex-status-actions {{
                    padding: 2px 0 2px 5px;
                }}
                .codex-status-action {{
                    min-height: 22px;
                    min-width: 22px;
                    padding: 1px 5px;
                    color: {theme.text};
                    font-family: monospace;
                    font-size: 9pt;
                }}
            """
            self._css_provider.load_from_data(css.encode("utf-8"))
            self._measure_wide_layout()

    _STATUS_RAIL_CLASS = StatusRailWidget
    return StatusRailWidget


def create_status_rail_widget(
    status: StatusModel,
    theme: ThemeModel,
) -> Any:
    """Create only the reusable rail for a GTK host that already owns VTE."""

    modules = _load_gtk()
    return _status_rail_class(modules)(status, theme)


def _rgba(Gdk: Any, color: str) -> Any:
    value = Gdk.RGBA()
    if not value.parse(color):
        value.parse("#080a0c")
    return value


def apply_terminal_theme(
    terminal: Any,
    theme: ThemeModel,
    inherited_background: Any,
) -> None:
    """Apply standalone-equivalent foreground/background intent to one VTE."""

    Gdk = _load_gtk().Gdk
    foreground = _rgba(Gdk, theme.text)
    configured = theme.terminal_background_color()
    background = (
        inherited_background if configured is None else _rgba(Gdk, configured)
    )
    terminal.set_colors(foreground, background, None)
    terminal.set_color_cursor(_rgba(Gdk, theme.labels))
