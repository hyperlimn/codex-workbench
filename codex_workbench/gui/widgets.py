from __future__ import annotations

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gtk  # noqa: E402

from .state import AccountItem, ConfidenceItem, ProjectItem


def clear(widget: Gtk.Widget) -> None:
    child = widget.get_first_child()
    while child is not None:
        following = child.get_next_sibling()
        if isinstance(widget, Gtk.ListBox):
            widget.remove(child)
        elif isinstance(widget, Gtk.FlowBox):
            widget.remove(child)
        elif isinstance(widget, Gtk.Box):
            widget.remove(child)
        child = following


def make_label(
    text: str = "",
    *classes: str,
    xalign: float = 0.0,
    wrap: bool = False,
    selectable: bool = False,
) -> Gtk.Label:
    label = Gtk.Label(
        label=text,
        xalign=xalign,
        wrap=wrap,
        selectable=selectable,
    )
    for css_class in classes:
        label.add_css_class(css_class)
    return label


def icon_button(
    icon: str,
    tooltip: str,
    *,
    css_class: str = "flat",
) -> Gtk.Button:
    button = Gtk.Button(icon_name=icon)
    button.set_tooltip_text(tooltip)
    button.add_css_class(css_class)
    return button


class ProjectRow(Gtk.ListBoxRow):
    def __init__(self, item: ProjectItem):
        super().__init__()
        self.project_name = item.name
        self.set_activatable(True)
        self.add_css_class("project-row")
        if item.warning:
            self.add_css_class("has-warning")
        if item.worktree == "modified":
            self.add_css_class("is-modified")

        content = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL,
            spacing=3,
            margin_top=9,
            margin_bottom=9,
            margin_start=12,
            margin_end=10,
        )
        heading = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=7)
        dot = make_label("●", "project-dot")
        title = make_label(item.name, "project-title")
        title.set_hexpand(True)
        heading.append(dot)
        heading.append(title)
        if item.session:
            session = make_label(item.session, "session-badge")
            session.set_ellipsize(3)
            session.set_max_width_chars(14)
            heading.append(session)
        content.append(heading)

        secondary = make_label(
            f"{item.account}  ·  {item.branch}",
            "project-meta",
        )
        secondary.set_margin_start(17)
        secondary.set_ellipsize(3)
        content.append(secondary)
        self.set_child(content)


class AccountChip(Gtk.ToggleButton):
    def __init__(self, item: AccountItem):
        super().__init__()
        self.account_name = item.name
        self.add_css_class("account-chip")
        self.add_css_class(f"usage-{item.level}")
        self.set_active(item.selected)
        self.set_tooltip_text(
            "\n".join(
                part
                for part in (
                    item.name,
                    item.reset and f"Reset: {item.reset}",
                    item.error and f"Unavailable: {item.error}",
                    "Click to set the intended account; this does not launch Codex.",
                )
                if part
            )
        )
        box = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL,
            spacing=1,
            margin_top=6,
            margin_bottom=6,
            margin_start=10,
            margin_end=10,
        )
        name = make_label(item.name, "account-name")
        usage = make_label(
            f"{item.five_hour_label}   {item.weekly_label}",
            "account-usage",
        )
        box.append(name)
        box.append(usage)
        self.set_child(box)


class ConfidenceCell(Gtk.Box):
    ICONS = {"valid": "✓", "warning": "!", "error": "×", "neutral": "·"}

    def __init__(self, item: ConfidenceItem):
        super().__init__(
            orientation=Gtk.Orientation.VERTICAL,
            spacing=3,
            margin_top=7,
            margin_bottom=7,
            margin_start=10,
            margin_end=10,
        )
        self.add_css_class("confidence-cell")
        self.add_css_class(f"tone-{item.tone}")
        top = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        top.append(
            make_label(self.ICONS.get(item.tone, "·"), "confidence-icon")
        )
        top.append(make_label(item.label.upper(), "confidence-label"))
        self.append(top)
        value = make_label(item.value, "confidence-value")
        value.set_ellipsize(3)
        value.set_max_width_chars(30)
        self.append(value)
        if item.detail:
            detail = make_label(item.detail, "confidence-detail")
            detail.set_ellipsize(3)
            detail.set_max_width_chars(34)
            self.append(detail)
        self.set_tooltip_text(
            f"{item.label}\n{item.value}"
            + (f"\n{item.detail}" if item.detail else "")
        )


class ActionButton(Gtk.Button):
    def __init__(
        self,
        label: str,
        icon_name: str,
        tooltip: str,
        *,
        important: bool = False,
    ):
        super().__init__()
        self.set_tooltip_text(tooltip)
        self.add_css_class("action-button")
        if important:
            self.add_css_class("suggested-action")
        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        box.append(Gtk.Image.new_from_icon_name(icon_name))
        box.append(make_label(label, "action-label"))
        self.set_child(box)


class SectionHeader(Gtk.Box):
    def __init__(self, title: str, subtitle: str = ""):
        super().__init__(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self.add_css_class("section-heading")
        title_label = make_label(title.upper(), "section-title")
        title_label.set_hexpand(True)
        self.append(title_label)
        if subtitle:
            self.append(make_label(subtitle, "section-subtitle"))
