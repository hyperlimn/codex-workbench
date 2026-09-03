from __future__ import annotations

from datetime import datetime
from pathlib import Path
from threading import Event
from typing import Callable, Sequence

import gi

gi.require_version("Gdk", "4.0")
gi.require_version("Gtk", "4.0")
from gi.repository import Gdk, Gio, GLib, Gtk  # noqa: E402

from ..clone import infer_repository_name
from ..models import ASSOCIATED_PATH_ROLES, AssociatedPath, Project
from ..preflight import PreflightReport
from ..services import CommitPlan, PushPlan, ResumePlan, ShellTarget
from ..settings import WorkbenchSettings
from ..transcripts import TranscriptCandidate
from .chooser import ChooserBackend, GtkChooserBackend
from .state import AccountItem, PaletteCommand, WorkspaceState
from .widgets import clear, icon_button, make_label


def _dialog(
    parent: Gtk.Window,
    title: str,
    *,
    width: int = 680,
    height: int = 560,
) -> Gtk.Dialog:
    dialog = Gtk.Dialog(title=title, transient_for=parent, modal=True)
    dialog.set_default_size(width, height)
    dialog.add_css_class("dialog-window")
    return dialog


def _content(dialog: Gtk.Dialog, *, spacing: int = 12) -> Gtk.Box:
    box = Gtk.Box(
        orientation=Gtk.Orientation.VERTICAL,
        spacing=spacing,
        margin_top=18,
        margin_bottom=18,
        margin_start=18,
        margin_end=18,
    )
    box.add_css_class("dialog-content")
    dialog.get_content_area().append(box)
    return box


def _detail_row(label: str, value: str, *, monospace: bool = False) -> Gtk.Box:
    row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
    key = make_label(label.upper(), "confidence-label")
    key.set_size_request(130, -1)
    row.append(key)
    result = make_label(
        value or "—",
        *("detail-value",) if monospace else (),
        wrap=True,
        selectable=True,
    )
    result.set_hexpand(True)
    row.append(result)
    return row


def _text_view(
    text: str,
    *,
    css_class: str = "context-view",
    editable: bool = False,
) -> tuple[Gtk.ScrolledWindow, Gtk.TextView]:
    view = Gtk.TextView(
        editable=editable,
        cursor_visible=editable,
        monospace=True,
        wrap_mode=Gtk.WrapMode.NONE,
        left_margin=8,
        right_margin=8,
        top_margin=8,
        bottom_margin=8,
    )
    view.get_buffer().set_text(text)
    view.add_css_class(css_class)
    scroll = Gtk.ScrolledWindow(
        hscrollbar_policy=Gtk.PolicyType.AUTOMATIC,
        vscrollbar_policy=Gtk.PolicyType.AUTOMATIC,
        child=view,
    )
    scroll.set_vexpand(True)
    return scroll, view


def show_preflight(
    parent: Gtk.Window,
    report: PreflightReport,
    *,
    launch_anyway: Callable[[], None] | None = None,
) -> None:
    dialog = _dialog(parent, f"Ready · {report.project.name}", height=520)
    box = _content(dialog)
    heading = make_label("CONTEXT CONFIDENCE", "dialog-title")
    box.append(heading)
    box.append(
        make_label(
            "Required mismatches remain prominent; warnings are advisory.",
            "muted",
            wrap=True,
        )
    )

    rows = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
    rows.add_css_class("boxed-list")
    icons = {"pass": "✓", "warn": "!", "fail": "×"}
    for check in report.checks:
        row = Gtk.ListBoxRow(activatable=False)
        body = Gtk.Box(
            orientation=Gtk.Orientation.HORIZONTAL,
            spacing=10,
            margin_top=8,
            margin_bottom=8,
            margin_start=10,
            margin_end=10,
        )
        marker = make_label(icons.get(check.level, "·"), f"tone-{check.level}")
        marker.set_size_request(18, -1)
        body.append(marker)
        labels = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        labels.set_hexpand(True)
        labels.append(make_label(check.label, "thread-label"))
        labels.append(make_label(check.value, "detail-value", wrap=True, selectable=True))
        if check.detail:
            labels.append(make_label(check.detail, "muted", wrap=True))
        body.append(labels)
        row.set_child(body)
        rows.append(row)
    scroll = Gtk.ScrolledWindow(
        child=rows,
        vexpand=True,
        hscrollbar_policy=Gtk.PolicyType.NEVER,
    )
    box.append(scroll)
    dialog.add_button("Close", Gtk.ResponseType.CLOSE)
    if launch_anyway is not None:
        dialog.add_button("Open Codex", Gtk.ResponseType.APPLY)

    def response(_dialog: Gtk.Dialog, response_id: int) -> None:
        dialog.close()
        if response_id == Gtk.ResponseType.APPLY and launch_anyway:
            launch_anyway()

    dialog.connect("response", response)
    dialog.present()


def show_status(parent: Gtk.Window, title: str, text: str) -> None:
    dialog = _dialog(parent, title, width=780, height=650)
    box = _content(dialog)
    scroll, _view = _text_view(text)
    box.append(scroll)
    dialog.add_button("Close", Gtk.ResponseType.CLOSE)
    dialog.connect("response", lambda *_args: dialog.close())
    dialog.present()


def show_copy_fallback(
    parent: Gtk.Window, text: str, error: str
) -> None:
    dialog = _dialog(parent, "Workspace context", width=780, height=650)
    box = _content(dialog)
    banner = make_label(
        f"Clipboard copy was unavailable: {error}. Select the context below.",
        "dialog-banner",
        wrap=True,
        selectable=True,
    )
    box.append(banner)
    scroll, view = _text_view(text)
    buffer = view.get_buffer()
    buffer.select_range(buffer.get_start_iter(), buffer.get_end_iter())
    box.append(scroll)
    dialog.add_button("Close", Gtk.ResponseType.CLOSE)
    dialog.connect("response", lambda *_args: dialog.close())
    dialog.connect("map", lambda *_args: view.grab_focus())
    dialog.present()


class CommitDialog:
    COMMIT_SELECTED = 1
    COMMIT_ALL = 2

    def __init__(
        self,
        parent: Gtk.Window,
        plan: CommitPlan,
        on_commit: Callable[
            [str, tuple[str, ...], bool, bool, CommitPlan], None
        ],
    ):
        self.plan = plan
        self.on_commit = on_commit
        self.dialog = _dialog(
            parent, f"Commit · {plan.project.name}", width=760, height=700
        )
        box = _content(self.dialog, spacing=10)
        box.append(make_label("GUARDED COMMIT", "dialog-title"))
        box.append(
            _detail_row(
                "Git identity",
                " · ".join(
                    value
                    for value in (plan.git.user_name, plan.git.user_email)
                    if value
                )
                or "Not configured",
            )
        )
        if plan.project.git.configured:
            box.append(
                _detail_row(
                    "Expected",
                    " · ".join(
                        value
                        for value in (
                            plan.project.git.name,
                            plan.project.git.email,
                        )
                        if value
                    ),
                )
            )

        self.hard_block = any(
            "not configured" in problem for problem in plan.blocking
        )
        mismatches = [
            problem
            for problem in plan.blocking
            if "differs from expected" in problem
        ]
        for problem in plan.blocking:
            box.append(make_label(problem, "dialog-banner", wrap=True))
        for warning in plan.warnings:
            box.append(make_label(f"! {warning}", "muted", wrap=True))

        file_header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        file_header.append(make_label("CHANGED FILES", "section-title"))
        file_header.set_hexpand(True)
        box.append(file_header)

        self.file_checks: list[tuple[Gtk.CheckButton, str]] = []
        file_list = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
        file_list.add_css_class("boxed-list")
        for change in plan.changes:
            row = Gtk.ListBoxRow(activatable=False)
            body = Gtk.Box(
                orientation=Gtk.Orientation.HORIZONTAL,
                spacing=9,
                margin_top=6,
                margin_bottom=6,
                margin_start=8,
                margin_end=8,
            )
            check = Gtk.CheckButton()
            if change.staged:
                check.set_active(True)
                check.set_sensitive(False)
                check.set_tooltip_text(
                    "Already staged outside Workbench; it will be included."
                )
            body.append(check)
            kind = make_label(
                "STAGED" if change.staged else change.category.upper(),
                "git-file-kind",
            )
            body.append(kind)
            path = make_label(change.path, "git-file-path", selectable=True)
            path.set_hexpand(True)
            path.set_ellipsize(3)
            body.append(path)
            row.set_child(body)
            file_list.append(row)
            self.file_checks.append((check, change.path))
        file_scroll = Gtk.ScrolledWindow(
            child=file_list,
            min_content_height=165,
            vexpand=True,
            hscrollbar_policy=Gtk.PolicyType.NEVER,
        )
        box.append(file_scroll)

        diff_button = Gtk.ToggleButton(label="Show Diff")
        diff_button.add_css_class("flat")
        box.append(diff_button)
        diff_revealer = Gtk.Revealer()
        diff_scroll, _view = _text_view(
            plan.diff or "(No textual diff; untracked files are listed above.)",
            css_class="diff-view",
        )
        diff_scroll.set_min_content_height(180)
        diff_revealer.set_child(diff_scroll)
        diff_revealer.set_reveal_child(False)
        box.append(diff_revealer)
        diff_button.connect(
            "toggled",
            lambda button: diff_revealer.set_reveal_child(button.get_active()),
        )

        self.message = Gtk.Entry(
            placeholder_text="Commit message",
            activates_default=True,
        )
        box.append(self.message)
        self.override = Gtk.CheckButton(
            label="I reviewed and explicitly accept the configured identity mismatch"
        )
        self.override.set_visible(bool(mismatches))
        box.append(self.override)

        self.dialog.add_button("Cancel", Gtk.ResponseType.CANCEL)
        selected_button = self.dialog.add_button(
            "Commit Selected", self.COMMIT_SELECTED
        )
        all_button = self.dialog.add_button(
            "Stage All + Commit", self.COMMIT_ALL
        )
        all_button.add_css_class("suggested-action")
        if self.hard_block:
            selected_button.set_sensitive(False)
            all_button.set_sensitive(False)
        self.dialog.set_default_response(self.COMMIT_SELECTED)
        self.dialog.connect("response", self._response)

    def _response(self, _dialog: Gtk.Dialog, response_id: int) -> None:
        if response_id not in {self.COMMIT_SELECTED, self.COMMIT_ALL}:
            self.dialog.close()
            return
        message = self.message.get_text().strip()
        if not message:
            self.message.add_css_class("error")
            self.message.grab_focus()
            return
        if self.override.get_visible() and not self.override.get_active():
            self.override.add_css_class("error")
            return
        stage_all = response_id == self.COMMIT_ALL
        files = tuple(
            path
            for check, path in self.file_checks
            if check.get_active() and check.get_sensitive()
        )
        already_staged = any(
            change.staged for change in self.plan.changes
        )
        if not stage_all and not files and not already_staged:
            self.message.set_tooltip_text("Select at least one file to stage.")
            return
        self.dialog.close()
        self.on_commit(
            message,
            files,
            stage_all,
            self.override.get_active(),
            self.plan,
        )

    def present(self) -> None:
        self.dialog.present()


class PushDialog:
    PUSH = 1

    def __init__(
        self,
        parent: Gtk.Window,
        plan: PushPlan,
        on_push: Callable[[PushPlan, bool, bool, bool], None],
    ):
        self.plan = plan
        self.on_push = on_push
        self.dialog = _dialog(
            parent, f"Push preview · {plan.git.branch or 'unknown'}",
            width=690,
            height=610,
        )
        box = _content(self.dialog)
        box.append(make_label("DESTINATION PREVIEW", "dialog-title"))
        for label, value, mono in (
            ("Repository", plan.remote_url or "Not configured", True),
            ("Remote", plan.remote, True),
            ("Branch", plan.branch or "Unknown", True),
            ("Destination", plan.destination, True),
            ("Upstream", plan.upstream or "None", True),
            (
                "Ahead / behind",
                f"{plan.git.ahead if plan.git.ahead is not None else '—'} / "
                f"{plan.git.behind if plan.git.behind is not None else '—'}",
                True,
            ),
            (
                "GitHub expected",
                plan.expected_github_account or "Not configured",
                False,
            ),
            (
                "GitHub detected",
                plan.detected_github_account or "Unavailable",
                False,
            ),
            ("Remote owner", plan.remote_owner or "Unknown", False),
        ):
            box.append(_detail_row(label, value, monospace=mono))

        for warning in plan.warnings:
            box.append(make_label(f"! {warning}", "muted", wrap=True))
        for problem in plan.blocking:
            box.append(make_label(problem, "dialog-banner", wrap=True))

        self.destination_override = Gtk.CheckButton(
            label="I reviewed and accept the configured destination mismatch"
        )
        self.destination_override.set_visible(
            any("remote URL differs" in item for item in plan.blocking)
        )
        box.append(self.destination_override)
        self.identity_override = Gtk.CheckButton(
            label="I reviewed and accept the GitHub identity/owner mismatch"
        )
        self.identity_override.set_visible(
            any(
                "GitHub CLI account" in item or "remote owner" in item
                for item in plan.blocking
            )
        )
        box.append(self.identity_override)
        self.set_upstream = Gtk.CheckButton(
            label=f"Set upstream to {plan.destination}"
        )
        self.set_upstream.set_active(not bool(plan.upstream))
        box.append(self.set_upstream)

        unoverridable = [
            item
            for item in plan.blocking
            if "remote URL differs" not in item
            and "GitHub CLI account" not in item
            and "remote owner" not in item
        ]
        self.dialog.add_button("Cancel", Gtk.ResponseType.CANCEL)
        push_button = self.dialog.add_button("Push", self.PUSH)
        push_button.add_css_class("destructive-action")
        push_button.set_sensitive(not unoverridable)
        self.dialog.connect("response", self._response)

    def _response(self, _dialog: Gtk.Dialog, response_id: int) -> None:
        if response_id != self.PUSH:
            self.dialog.close()
            return
        if (
            self.destination_override.get_visible()
            and not self.destination_override.get_active()
        ):
            self.destination_override.add_css_class("error")
            return
        if (
            self.identity_override.get_visible()
            and not self.identity_override.get_active()
        ):
            self.identity_override.add_css_class("error")
            return
        self.dialog.close()
        self.on_push(
            self.plan,
            self.set_upstream.get_active(),
            self.destination_override.get_active(),
            self.identity_override.get_active(),
        )

    def present(self) -> None:
        self.dialog.present()


class HandoffDialog:
    GENERATE = 1
    GENERATE_LAUNCH = 2

    def __init__(
        self,
        parent: Gtk.Window,
        workspace: WorkspaceState,
        accounts: Sequence[AccountItem],
        candidates: Sequence[TranscriptCandidate],
        on_generate: Callable[[str, Path | None, bool], None],
        *,
        chooser: ChooserBackend | None = None,
    ):
        self.workspace = workspace
        self.accounts = list(accounts)
        self.candidates = list(candidates)
        self.on_generate = on_generate
        self.chooser = chooser or GtkChooserBackend()
        self.transcript_path: Path | None = None
        self.dialog = _dialog(
            parent, f"Codex handoff · {workspace.project.name}",
            width=760,
            height=720,
        )
        box = _content(self.dialog)
        box.append(make_label("CODEX HANDOFF", "dialog-title"))
        grid = Gtk.Grid(column_spacing=14, row_spacing=7)
        details = (
            ("Current account", workspace.account or "Not configured"),
            (
                "5-hour usage",
                (
                    f"{workspace.status.codex.five_hour_remaining}% remaining"
                    if workspace.status.codex
                    and workspace.status.codex.five_hour_remaining is not None
                    else "Unavailable"
                ),
            ),
            (
                "Current session",
                workspace.session.name if workspace.session else "Will create current-work",
            ),
            ("Project", workspace.project.name),
            ("Branch", workspace.status.git.branch or "Detached / unknown"),
            ("HEAD", workspace.status.git.head_short or "—"),
        )
        for index, (label, value) in enumerate(details):
            grid.attach(make_label(label.upper(), "confidence-label"), 0, index, 1, 1)
            grid.attach(make_label(value, "detail-value", selectable=True), 1, index, 1, 1)
        box.append(grid)

        box.append(make_label("TARGET ACCOUNT", "section-title"))
        account_list = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
        account_list.add_css_class("boxed-list")
        self.account_buttons: list[tuple[Gtk.CheckButton, str]] = []
        group: Gtk.CheckButton | None = None
        alternatives = [
            item for item in self.accounts if item.name != workspace.account
        ]
        alternatives.sort(
            key=lambda item: item.five_hour_remaining
            if item.five_hour_remaining is not None
            else -1,
            reverse=True,
        )
        for index, item in enumerate(alternatives):
            row = Gtk.ListBoxRow(activatable=False)
            body = Gtk.Box(
                orientation=Gtk.Orientation.HORIZONTAL,
                spacing=10,
                margin_top=8,
                margin_bottom=8,
                margin_start=10,
                margin_end=10,
            )
            radio = Gtk.CheckButton()
            if group is None:
                group = radio
            else:
                radio.set_group(group)
            radio.set_active(index == 0)
            body.append(radio)
            name = make_label(item.name, "thread-label")
            name.set_hexpand(True)
            body.append(name)
            body.append(
                make_label(
                    f"{item.five_hour_label} · {item.weekly_label}",
                    "detail-value",
                )
            )
            row.set_child(body)
            account_list.append(row)
            self.account_buttons.append((radio, item.name))
        box.append(account_list)

        box.append(make_label("TRANSCRIPT", "section-title"))
        candidate_names = ["None — handoff summary only"]
        candidate_names.extend(
            f"{item.path.name} · {item.confidence} · {self._size(item.size)}"
            for item in self.candidates
        )
        self.candidate_paths: list[Path | None] = [
            None,
            *(item.path for item in self.candidates),
        ]
        self.candidate_dropdown = Gtk.DropDown.new_from_strings(candidate_names)
        box.append(self.candidate_dropdown)
        transcript_actions = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        attach = Gtk.Button(label="Attach exported transcript…")
        transcript_actions.append(attach)
        self.transcript_label = make_label("No manual transcript selected", "muted")
        self.transcript_label.set_hexpand(True)
        transcript_actions.append(self.transcript_label)
        box.append(transcript_actions)
        attach.connect("clicked", self._choose_transcript)

        self.dialog.add_button("Cancel", Gtk.ResponseType.CANCEL)
        self.dialog.add_button("Generate handoff", self.GENERATE)
        launch_button = self.dialog.add_button(
            "Generate + Launch", self.GENERATE_LAUNCH
        )
        launch_button.add_css_class("suggested-action")
        if not self.account_buttons:
            launch_button.set_sensitive(False)
            self.dialog.get_widget_for_response(self.GENERATE).set_sensitive(False)
            box.append(
                make_label(
                    "No alternative Codex account was returned by the launcher.",
                    "dialog-banner",
                    wrap=True,
                )
            )
        self.dialog.connect("response", self._response)

    @staticmethod
    def _size(size: int) -> str:
        return f"{size / 1024:.0f} KiB" if size >= 1024 else f"{size} B"

    def _choose_transcript(self, _button: Gtk.Button) -> None:
        chooser = self.chooser.open_file_dialog(
            parent=self.dialog,
            title="Attach exported Codex transcript",
            accept_label="Attach",
        )
        file_filter = Gtk.FileFilter()
        file_filter.set_name("Text and Markdown")
        file_filter.add_mime_type("text/plain")
        file_filter.add_pattern("*.md")
        file_filter.add_pattern("*.txt")
        file_filter.add_pattern("*.log")
        chooser.add_filter(file_filter)

        def response(native: Gtk.FileChooserNative, response_id: int) -> None:
            if response_id == Gtk.ResponseType.ACCEPT:
                selected = native.get_file()
                if selected and selected.get_path():
                    self.transcript_path = Path(selected.get_path())
                    self.transcript_label.set_text(self.transcript_path.name)
            native.destroy()

        chooser.connect("response", response)
        chooser.show()

    def _response(self, _dialog: Gtk.Dialog, response_id: int) -> None:
        if response_id not in {self.GENERATE, self.GENERATE_LAUNCH}:
            self.dialog.close()
            return
        account = next(
            (
                name
                for button, name in self.account_buttons
                if button.get_active()
            ),
            "",
        )
        if not account:
            return
        transcript = self.transcript_path
        if transcript is None:
            selected = self.candidate_dropdown.get_selected()
            if selected < len(self.candidate_paths):
                transcript = self.candidate_paths[selected]
        self.dialog.close()
        self.on_generate(
            account,
            transcript,
            response_id == self.GENERATE_LAUNCH,
        )

    def present(self) -> None:
        self.dialog.present()


def show_handoff_summary(
    parent: Gtk.Window,
    bundle_path: Path,
    handoff_path: Path,
    *,
    launched: bool,
) -> None:
    dialog = _dialog(parent, "Handoff ready", width=620, height=360)
    box = _content(dialog)
    box.append(make_label("HANDOFF READY", "dialog-title"))
    box.append(_detail_row("Session directory", str(bundle_path), monospace=True))
    box.append(_detail_row("Read first", str(handoff_path), monospace=True))
    box.append(
        make_label(
            (
                "The target Codex account was launched in the same project directory."
                if launched
                else "The bundle is persistent and ready to resume or launch."
            ),
            "muted",
            wrap=True,
        )
    )
    dialog.add_button("Close", Gtk.ResponseType.CLOSE)
    dialog.connect("response", lambda *_args: dialog.close())
    dialog.present()


class ResumeDialog:
    RESUME_HERE = 1
    RESUME_CODEX = 2

    def __init__(
        self,
        parent: Gtk.Window,
        plan: ResumePlan,
        on_here: Callable[[ResumePlan], None],
        on_codex: Callable[[ResumePlan], None],
    ):
        self.plan = plan
        self.on_here = on_here
        self.on_codex = on_codex
        self.dialog = _dialog(parent, f"Resume · {plan.session.name}", height=610)
        box = _content(self.dialog)
        box.append(make_label("RESUME WORK SESSION", "dialog-title"))
        for label, value, mono in (
            ("Objective", plan.session.objective or "Not recorded", False),
            (
                "Previous account",
                (
                    plan.session.handoff_history[-1].from_codex_account
                    if plan.session.handoff_history
                    else plan.session.codex_account
                )
                or "—",
                False,
            ),
            ("Intended account", plan.account or "Not configured", False),
            ("Stored branch", plan.session.branch or "—", True),
            ("Current branch", plan.git.branch or "Detached", True),
            ("Stored HEAD", plan.session.current_head or "—", True),
            ("Current HEAD", plan.git.head or "—", True),
            (
                "Last handoff",
                (
                    plan.session.handoff_history[-1].created_at
                    if plan.session.handoff_history
                    else "None"
                ),
                False,
            ),
            ("Next action", plan.session.next_action or "Not recorded", False),
        ):
            box.append(_detail_row(label, value, monospace=mono))
        for warning in plan.warnings:
            box.append(make_label(f"! {warning}", "dialog-banner", wrap=True))
        box.append(
            make_label(
                "Resume never checks out a branch, resets HEAD, or changes the worktree.",
                "muted",
                wrap=True,
            )
        )
        self.dialog.add_button("Cancel", Gtk.ResponseType.CANCEL)
        self.dialog.add_button("Resume Here", self.RESUME_HERE)
        codex = self.dialog.add_button("Resume in Codex", self.RESUME_CODEX)
        codex.add_css_class("suggested-action")
        self.dialog.connect("response", self._response)

    def _response(self, _dialog: Gtk.Dialog, response_id: int) -> None:
        self.dialog.close()
        if response_id == self.RESUME_HERE:
            self.on_here(self.plan)
        elif response_id == self.RESUME_CODEX:
            self.on_codex(self.plan)

    def present(self) -> None:
        self.dialog.present()


class SessionDialog:
    SAVE = 1

    def __init__(
        self,
        parent: Gtk.Window,
        workspace: WorkspaceState,
        on_save: Callable[[dict[str, object]], None],
    ):
        self.workspace = workspace
        self.on_save = on_save
        existing = workspace.session
        self.dialog = _dialog(
            parent,
            "Edit work session" if existing else "Start work session",
            width=640,
            height=610,
        )
        box = _content(self.dialog)
        box.append(
            make_label(
                "SESSION CONTEXT" if existing else "NEW WORK SESSION",
                "dialog-title",
            )
        )
        self.name = Gtk.Entry(
            text=existing.name if existing else "",
            placeholder_text="Session name",
            sensitive=existing is None,
        )
        self.objective = Gtk.Entry(
            text=(existing.objective if existing else workspace.project.objective),
            placeholder_text="Current objective",
        )
        self.state = Gtk.Entry(
            text=existing.current_state if existing else "",
            placeholder_text="Current state",
        )
        self.problem = Gtk.Entry(
            text=existing.current_problem if existing else "",
            placeholder_text="Current problem",
        )
        self.next_action = Gtk.Entry(
            text=existing.next_action if existing else "",
            placeholder_text="Next action",
        )
        self.completed = Gtk.Entry(
            placeholder_text="Add one completed item (optional)",
        )
        for label, widget in (
            ("Session name", self.name),
            ("Objective", self.objective),
            ("Current state", self.state),
            ("Current problem", self.problem),
            ("Next action", self.next_action),
            ("Completed", self.completed),
        ):
            box.append(make_label(label.upper(), "confidence-label"))
            box.append(widget)
        self.dialog.add_button("Cancel", Gtk.ResponseType.CANCEL)
        save = self.dialog.add_button(
            "Save session", self.SAVE
        )
        save.add_css_class("suggested-action")
        self.dialog.connect("response", self._response)

    def _response(self, _dialog: Gtk.Dialog, response_id: int) -> None:
        if response_id != self.SAVE:
            self.dialog.close()
            return
        if not self.name.get_text().strip():
            self.name.add_css_class("error")
            self.name.grab_focus()
            return
        changes: dict[str, object] = {
            "name": self.name.get_text().strip(),
            "objective": self.objective.get_text().strip(),
            "current_state": self.state.get_text().strip(),
            "current_problem": self.problem.get_text().strip(),
            "next_action": self.next_action.get_text().strip(),
        }
        completed = self.completed.get_text().strip()
        if completed:
            changes["completed"] = [completed]
        self.dialog.close()
        self.on_save(changes)

    def present(self) -> None:
        self.dialog.present()


class _FolderProjectDialog:
    ADD = 1

    def __init__(
        self,
        parent: Gtk.Window,
        accounts: Sequence[AccountItem],
        on_add: Callable[[str, str, str, str], None],
        *,
        folder_dialog_factory: Callable[..., object] | None = None,
    ):
        self.on_add = on_add
        chooser = GtkChooserBackend(folder_factory=folder_dialog_factory)
        self._folder_dialog_factory = chooser.folder_dialog
        self._folder_dialog: object | None = None
        self._closed = False
        self.dialog = _dialog(parent, "Add project", width=610, height=480)
        box = _content(self.dialog)
        box.append(make_label("ADD PROJECT", "dialog-title"))
        box.append(
            make_label(
                "Only name and directory are required. Git identity and remote "
                "are detected from the repository.",
                "muted",
                wrap=True,
            )
        )
        self.name = Gtk.Entry(placeholder_text="project-alpha")
        self.directory = Gtk.Entry(
            placeholder_text="Choose an existing project directory"
        )
        self.browse = icon_button("folder-open-symbolic", "Choose directory")
        directory_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        self.directory.set_hexpand(True)
        directory_row.append(self.directory)
        directory_row.append(self.browse)
        directory_group = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL,
            spacing=6,
        )
        directory_group.append(directory_row)
        self.directory_error = make_label(
            "",
            "dialog-banner",
            wrap=True,
        )
        self.directory_error.set_visible(False)
        directory_group.append(self.directory_error)
        self.account_names = ["", *(item.name for item in accounts)]
        account_labels = ["No preferred account", *(item.name for item in accounts)]
        self.account = Gtk.DropDown.new_from_strings(account_labels)
        self.github = Gtk.Entry(placeholder_text="Optional GitHub account expectation")
        for label, widget in (
            ("Name", self.name),
            ("Directory", directory_group),
            ("Codex account", self.account),
            ("GitHub expectation", self.github),
        ):
            box.append(make_label(label.upper(), "confidence-label"))
            box.append(widget)
        self.browse.connect("clicked", self._choose_directory)
        self.dialog.add_button("Cancel", Gtk.ResponseType.CANCEL)
        add = self.dialog.add_button("Add project", self.ADD)
        add.add_css_class("suggested-action")
        self.dialog.connect("response", self._response)
        self.dialog.connect("destroy", self._dialog_destroyed)

    def _choose_directory(self, _button: Gtk.Button) -> None:
        if self._closed or self._folder_dialog is not None:
            return
        self._show_directory_error("")
        try:
            picker = self._folder_dialog_factory(
                title="Choose project directory",
                modal=True,
                accept_label="Choose",
            )
            self._folder_dialog = picker
            self.browse.set_sensitive(False)
            picker.select_folder(
                self.dialog,
                None,
                self._directory_selected,
            )
        except Exception as error:
            self._folder_dialog = None
            self.browse.set_sensitive(True)
            self._show_directory_error(
                f"Could not open the directory chooser: {error}"
            )

    def _directory_selected(self, picker: object, result: object) -> None:
        selected = None
        try:
            selected = picker.select_folder_finish(result)
        except GLib.Error as error:
            if not self._closed and not self._is_picker_dismissal(error):
                self._show_directory_error(
                    f"Could not choose a directory: {error.message}"
                )
        except Exception as error:
            if not self._closed:
                self._show_directory_error(
                    f"Could not choose a directory: {error}"
                )
        finally:
            self._folder_dialog = None
            if not self._closed:
                self.browse.set_sensitive(True)

        if selected is None or self._closed:
            return
        try:
            path = selected.get_path()
        except Exception as error:
            self._show_directory_error(
                f"Could not read the selected directory: {error}"
            )
            return
        if not path:
            self._show_directory_error(
                "The selected directory is not available as a local path."
            )
            return
        self.directory.remove_css_class("error")
        self.directory.set_text(path)
        if not self.name.get_text():
            self.name.set_text(Path(path).name)

    @staticmethod
    def _is_picker_dismissal(error: GLib.Error) -> bool:
        return any(
            error.matches(domain, code)
            for domain, code in (
                (Gtk.dialog_error_quark(), Gtk.DialogError.DISMISSED),
                (Gtk.dialog_error_quark(), Gtk.DialogError.CANCELLED),
                (Gio.io_error_quark(), Gio.IOErrorEnum.CANCELLED),
            )
        )

    def _show_directory_error(self, message: str) -> None:
        self.directory_error.set_text(message)
        self.directory_error.set_visible(bool(message))

    def _dialog_destroyed(self, _dialog: Gtk.Dialog) -> None:
        self._closed = True

    def _close(self) -> None:
        self._closed = True
        self.dialog.close()

    def _response(self, _dialog: Gtk.Dialog, response_id: int) -> None:
        if response_id != self.ADD:
            self._close()
            return
        name = self.name.get_text().strip()
        directory = self.directory.get_text().strip()
        if not name:
            self.name.add_css_class("error")
            return
        if not directory:
            self.directory.add_css_class("error")
            return
        index = self.account.get_selected()
        account = self.account_names[index] if index < len(self.account_names) else ""
        self._close()
        self.on_add(name, directory, account, self.github.get_text().strip())

    def present(self) -> None:
        self.dialog.present()


class AddProjectDialog(_FolderProjectDialog):
    """Compact local/clone project flow with owned asynchronous choosers."""

    def __init__(
        self,
        parent: Gtk.Window,
        accounts: Sequence[AccountItem],
        on_add: Callable[[str, str, str, str], None],
        *,
        on_clone: Callable[
            [str, str, str, str, str, str, Event], None
        ]
        | None = None,
        folder_dialog_factory: Callable[..., object] | None = None,
    ):
        self.on_add = on_add
        self.on_clone = on_clone
        chooser = GtkChooserBackend(folder_factory=folder_dialog_factory)
        self._folder_dialog_factory = chooser.folder_dialog
        self._folder_dialog: object | None = None
        self._parent_folder_dialog: object | None = None
        self._closed = False
        self._busy = False
        self._last_inferred = ""
        self.cancel_event = Event()
        self.dialog = _dialog(parent, "Add project", width=650, height=650)
        box = _content(self.dialog)
        box.append(make_label("ADD PROJECT", "dialog-title"))
        self.mode = Gtk.DropDown.new_from_strings(
            ["Existing local project", "Clone Git repository"]
        )
        box.append(make_label("MODE", "confidence-label"))
        box.append(self.mode)

        self.name = Gtk.Entry(placeholder_text="Project name")
        box.append(make_label("PROJECT NAME", "confidence-label"))
        box.append(self.name)

        self.mode_stack = Gtk.Stack(
            transition_type=Gtk.StackTransitionType.CROSSFADE,
            transition_duration=120,
        )
        local_page = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL,
            spacing=6,
        )
        self.directory = Gtk.Entry(
            placeholder_text="Choose an existing project directory"
        )
        self.browse = icon_button("folder-open-symbolic", "Choose directory")
        directory_row = Gtk.Box(
            orientation=Gtk.Orientation.HORIZONTAL,
            spacing=6,
        )
        self.directory.set_hexpand(True)
        directory_row.append(self.directory)
        directory_row.append(self.browse)
        local_page.append(make_label("DIRECTORY", "confidence-label"))
        local_page.append(directory_row)
        self.directory_error = make_label("", "dialog-banner", wrap=True)
        self.directory_error.set_visible(False)
        local_page.append(self.directory_error)
        self.mode_stack.add_named(local_page, "local")

        clone_page = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL,
            spacing=6,
        )
        self.repository_url = Gtk.Entry(
            placeholder_text="https://github.com/owner/repository.git"
        )
        self.destination_parent = Gtk.Entry(
            placeholder_text="Choose destination parent directory"
        )
        self.destination_browse = icon_button(
            "folder-open-symbolic",
            "Choose destination parent",
        )
        parent_row = Gtk.Box(
            orientation=Gtk.Orientation.HORIZONTAL,
            spacing=6,
        )
        self.destination_parent.set_hexpand(True)
        parent_row.append(self.destination_parent)
        parent_row.append(self.destination_browse)
        self.destination_folder = Gtk.Entry(
            placeholder_text="repository"
        )
        self.destination_preview = make_label(
            "Final path: —",
            "detail-value",
            wrap=True,
            selectable=True,
        )
        for label, widget in (
            ("REPOSITORY URL", self.repository_url),
            ("DESTINATION PARENT", parent_row),
            ("DESTINATION FOLDER", self.destination_folder),
            ("", self.destination_preview),
        ):
            if label:
                clone_page.append(make_label(label, "confidence-label"))
            clone_page.append(widget)
        self.clone_status = make_label("", "dialog-banner", wrap=True)
        self.clone_status.set_visible(False)
        clone_page.append(self.clone_status)
        self.clone_output_scroll, self.clone_output = _text_view("")
        self.clone_output_scroll.set_size_request(-1, 100)
        self.clone_output_scroll.set_visible(False)
        clone_page.append(self.clone_output_scroll)
        self.mode_stack.add_named(clone_page, "clone")
        self.mode_stack.set_visible_child_name("local")
        box.append(self.mode_stack)

        self.account_names = ["", *(item.name for item in accounts)]
        account_labels = [
            "No preferred account",
            *(item.name for item in accounts),
        ]
        self.account = Gtk.DropDown.new_from_strings(account_labels)
        self.github = Gtk.Entry(
            placeholder_text="Optional GitHub account expectation"
        )
        for label, widget in (
            ("CODEX ACCOUNT", self.account),
            ("GITHUB EXPECTATION", self.github),
        ):
            box.append(make_label(label, "confidence-label"))
            box.append(widget)

        self.browse.connect("clicked", self._choose_directory)
        self.destination_browse.connect(
            "clicked",
            self._choose_destination_parent,
        )
        self.mode.connect("notify::selected", self._mode_changed)
        self.repository_url.connect("changed", self._repository_changed)
        self.destination_parent.connect("changed", self._preview_changed)
        self.destination_folder.connect("changed", self._preview_changed)
        self.dialog.add_button("Cancel", Gtk.ResponseType.CANCEL)
        self.add_button = self.dialog.add_button("Add project", self.ADD)
        self.add_button.add_css_class("suggested-action")
        self.dialog.connect("response", self._response_v04)
        self.dialog.connect("destroy", self._dialog_destroyed)

    def _mode_changed(self, *_args: object) -> None:
        clone = self.mode.get_selected() == 1
        self.mode_stack.set_visible_child_name("clone" if clone else "local")
        self.add_button.set_label(
            "Clone and register" if clone else "Add project"
        )

    def _repository_changed(self, *_args: object) -> None:
        inferred = infer_repository_name(self.repository_url.get_text())
        if inferred and (
            not self.name.get_text()
            or self.name.get_text() == self._last_inferred
        ):
            self.name.set_text(inferred)
        if inferred and (
            not self.destination_folder.get_text()
            or self.destination_folder.get_text() == self._last_inferred
        ):
            self.destination_folder.set_text(inferred)
        self._last_inferred = inferred
        self._update_destination_preview()

    def _preview_changed(self, *_args: object) -> None:
        self._update_destination_preview()

    def _update_destination_preview(self) -> None:
        parent = self.destination_parent.get_text().strip()
        folder = self.destination_folder.get_text().strip()
        preview = str(Path(parent).expanduser() / folder) if parent and folder else "—"
        self.destination_preview.set_text(f"Final path: {preview}")

    def _choose_destination_parent(self, _button: Gtk.Button) -> None:
        if self._closed or self._parent_folder_dialog is not None:
            return
        try:
            picker = self._folder_dialog_factory(
                title="Choose destination parent",
                modal=True,
                accept_label="Choose",
            )
            self._parent_folder_dialog = picker
            self.destination_browse.set_sensitive(False)
            picker.select_folder(
                self.dialog,
                None,
                self._destination_parent_selected,
            )
        except Exception as error:
            self._parent_folder_dialog = None
            self.destination_browse.set_sensitive(True)
            self.set_clone_progress(
                f"Could not open the directory chooser: {error}"
            )

    def _destination_parent_selected(
        self,
        picker: object,
        result: object,
    ) -> None:
        selected = None
        try:
            selected = picker.select_folder_finish(result)
        except GLib.Error as error:
            if not self._closed and not self._is_picker_dismissal(error):
                self.set_clone_progress(
                    f"Could not choose a directory: {error.message}"
                )
        except Exception as error:
            if not self._closed:
                self.set_clone_progress(
                    f"Could not choose a directory: {error}"
                )
        finally:
            self._parent_folder_dialog = None
            if not self._closed:
                self.destination_browse.set_sensitive(True)
        if selected is None or self._closed:
            return
        path = selected.get_path()
        if path:
            self.destination_parent.set_text(path)
            self._update_destination_preview()

    def _selected_account(self) -> str:
        index = self.account.get_selected()
        return (
            self.account_names[index]
            if index < len(self.account_names)
            else ""
        )

    def _response_v04(
        self,
        _dialog: Gtk.Dialog,
        response_id: int,
    ) -> None:
        if response_id != self.ADD:
            if self._busy:
                self.cancel_event.set()
                self.set_clone_progress("Cancelling clone…")
                return
            self._close()
            return
        name = self.name.get_text().strip()
        if not name:
            self.name.add_css_class("error")
            self.name.grab_focus()
            return
        account = self._selected_account()
        github = self.github.get_text().strip()
        if self.mode.get_selected() == 0:
            directory = self.directory.get_text().strip()
            if not directory:
                self.directory.add_css_class("error")
                return
            self._close()
            self.on_add(name, directory, account, github)
            return
        fields = (
            self.repository_url,
            self.destination_parent,
            self.destination_folder,
        )
        if any(not field.get_text().strip() for field in fields):
            for field in fields:
                if not field.get_text().strip():
                    field.add_css_class("error")
            return
        if self.on_clone is None:
            self.set_clone_progress("Clone integration is unavailable.")
            return
        self._busy = True
        self.add_button.set_sensitive(False)
        self.mode.set_sensitive(False)
        self.clone_output.get_buffer().set_text("")
        self.clone_output_scroll.set_visible(False)
        self.set_clone_progress("Starting clone…")
        self.on_clone(
            name,
            self.repository_url.get_text().strip(),
            self.destination_parent.get_text().strip(),
            self.destination_folder.get_text().strip(),
            account,
            github,
            self.cancel_event,
        )

    def set_clone_progress(
        self,
        message: str,
        *,
        output: str = "",
    ) -> None:
        if self._closed:
            return
        self.clone_status.set_text(message)
        self.clone_status.set_visible(bool(message))
        if output:
            buffer = self.clone_output.get_buffer()
            buffer.set_text(output)
            self.clone_output_scroll.set_visible(True)

    def finish_clone(
        self,
        *,
        success: bool,
        message: str,
        output: str = "",
    ) -> None:
        if self._closed:
            return
        if success:
            self._close()
            return
        self._busy = False
        self.add_button.set_sensitive(True)
        self.mode.set_sensitive(True)
        self.set_clone_progress(message, output=output)

    def _dialog_destroyed(self, _dialog: Gtk.Dialog) -> None:
        self._closed = True
        if self._busy:
            self.cancel_event.set()


class AssociatedPathDialog:
    SAVE = 1

    def __init__(
        self,
        parent: Gtk.Window,
        on_save: Callable[[AssociatedPath], None],
        associated: AssociatedPath | None = None,
    ):
        self.on_save = on_save
        self.associated = associated
        self.dialog = _dialog(
            parent,
            "Edit associated path" if associated else "Add associated path",
            width=580,
            height=510,
        )
        box = _content(self.dialog)
        box.append(
            make_label(
                "EDIT ASSOCIATED PATH" if associated else "ADD ASSOCIATED PATH",
                "dialog-title",
            )
        )
        self.label = Gtk.Entry(
            text=associated.label if associated else "",
            placeholder_text="Buildroot",
        )
        self.path = Gtk.Entry(
            text=associated.path if associated else "",
            placeholder_text="Path to source, build, docs, assets, or data",
        )
        self.role = Gtk.Entry(
            text=associated.role if associated else "other",
            placeholder_text="source, toolchain, build, docs, assets…",
        )
        self.shell = Gtk.Switch(
            active=associated.open_shell if associated else True,
            halign=Gtk.Align.START,
        )
        self.required = Gtk.Switch(
            active=associated.required if associated else False,
            halign=Gtk.Align.START,
        )
        for label, widget in (
            ("Label", self.label),
            ("Path", self.path),
            ("Role / type", self.role),
            ("Allow shell here", self.shell),
            ("Required for READY", self.required),
        ):
            box.append(make_label(label.upper(), "confidence-label"))
            box.append(widget)
        box.append(
            make_label(
                "Suggested roles: " + ", ".join(ASSOCIATED_PATH_ROLES)
                + ". Custom role names are allowed.",
                "muted",
                wrap=True,
            )
        )
        self.dialog.add_button("Cancel", Gtk.ResponseType.CANCEL)
        save = self.dialog.add_button("Save path", self.SAVE)
        save.add_css_class("suggested-action")
        self.dialog.connect("response", self._response)

    def _response(self, _dialog: Gtk.Dialog, response_id: int) -> None:
        if response_id != self.SAVE:
            self.dialog.close()
            return
        if not self.label.get_text().strip():
            self.label.add_css_class("error")
            return
        if not self.path.get_text().strip():
            self.path.add_css_class("error")
            return
        result = AssociatedPath(
            self.label.get_text().strip(),
            self.path.get_text().strip(),
            self.role.get_text().strip() or "other",
            self.shell.get_active(),
            self.required.get_active(),
            dict(self.associated.extra) if self.associated else {},
        )
        self.dialog.close()
        self.on_save(result)

    def present(self) -> None:
        self.dialog.present()


class ProjectDialog(_FolderProjectDialog):
    SAVE = 1
    REMOVE = 2

    def __init__(
        self,
        parent: Gtk.Window,
        project: Project,
        accounts: Sequence[AccountItem],
        *,
        on_save: Callable[[dict[str, object]], None],
        on_remove: Callable[[], None],
        folder_dialog_factory: Callable[..., object] | None = None,
    ):
        self.project = project
        self.on_save = on_save
        self.on_remove = on_remove
        self.associated_paths = [
            AssociatedPath.from_value(item)
            for item in project.associated_paths
        ]
        self.associated_paths = [
            item for item in self.associated_paths if item is not None
        ]
        chooser = GtkChooserBackend(folder_factory=folder_dialog_factory)
        self._folder_dialog_factory = chooser.folder_dialog
        self._folder_dialog = None
        self._closed = False
        self.dialog = _dialog(parent, "Edit project", width=680, height=720)
        box = _content(self.dialog)
        box.append(make_label("EDIT PROJECT", "dialog-title"))
        box.append(
            make_label(
                "Changing the canonical directory refreshes Git context but "
                "does not move or modify files. Existing sessions are preserved.",
                "muted",
                wrap=True,
            )
        )
        self.name = Gtk.Entry(text=project.name)
        self.directory = Gtk.Entry(text=str(project.path))
        self.browse = icon_button(
            "folder-open-symbolic",
            "Choose canonical directory",
        )
        directory_row = Gtk.Box(
            orientation=Gtk.Orientation.HORIZONTAL,
            spacing=6,
        )
        self.directory.set_hexpand(True)
        directory_row.append(self.directory)
        directory_row.append(self.browse)
        self.directory_error = make_label("", "dialog-banner", wrap=True)
        self.directory_error.set_visible(False)
        directory_group = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL,
            spacing=6,
        )
        directory_group.append(directory_row)
        directory_group.append(self.directory_error)

        account_names = [item.name for item in accounts]
        if project.codex_account and project.codex_account not in account_names:
            account_names.insert(0, project.codex_account)
        self.account_names = ["", *account_names]
        self.account = Gtk.DropDown.new_from_strings(
            ["No preferred account", *account_names]
        )
        try:
            self.account.set_selected(
                self.account_names.index(project.codex_account)
            )
        except ValueError:
            self.account.set_selected(0)
        self.github = Gtk.Entry(text=project.github.account)
        self.terminal_modes = ["embedded", "external"]
        self.terminal_mode = Gtk.DropDown.new_from_strings(
            ["Embedded shell", "External terminal"]
        )
        self.terminal_mode.set_selected(
            0 if project.terminal.mode == "embedded" else 1
        )
        for label, widget in (
            ("Display name", self.name),
            ("Canonical directory", directory_group),
            ("Preferred Codex account", self.account),
            ("GitHub expectation", self.github),
            ("Default shell", self.terminal_mode),
        ):
            box.append(make_label(label.upper(), "confidence-label"))
            box.append(widget)

        path_header = Gtk.Box(
            orientation=Gtk.Orientation.HORIZONTAL,
            spacing=6,
        )
        path_title = make_label("ASSOCIATED PATHS", "confidence-label")
        path_title.set_hexpand(True)
        path_header.append(path_title)
        add_path = icon_button("list-add-symbolic", "Add associated path")
        add_path.connect("clicked", lambda *_args: self._add_path())
        path_header.append(add_path)
        box.append(path_header)
        self.path_list = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL,
            spacing=0,
        )
        self.path_list.add_css_class("boxed-list")
        path_scroll = Gtk.ScrolledWindow(
            child=self.path_list,
            min_content_height=150,
            max_content_height=220,
            propagate_natural_height=True,
        )
        box.append(path_scroll)
        self._render_paths()
        self.browse.connect("clicked", self._choose_directory)
        self.dialog.add_button("Cancel", Gtk.ResponseType.CANCEL)
        remove = self.dialog.add_button(
            "Remove from Workbench",
            self.REMOVE,
        )
        remove.add_css_class("destructive-action")
        save = self.dialog.add_button("Save changes", self.SAVE)
        save.add_css_class("suggested-action")
        self.dialog.connect("response", self._response_project)
        self.dialog.connect("destroy", self._dialog_destroyed)

    def _render_paths(self) -> None:
        clear(self.path_list)
        if not self.associated_paths:
            self.path_list.append(
                make_label("No associated paths.", "muted")
            )
            return
        for index, associated in enumerate(self.associated_paths):
            row = Gtk.Box(
                orientation=Gtk.Orientation.HORIZONTAL,
                spacing=6,
            )
            row.add_css_class("dialog-row")
            labels = Gtk.Box(
                orientation=Gtk.Orientation.VERTICAL,
                spacing=1,
            )
            labels.set_hexpand(True)
            labels.append(
                make_label(
                    f"{associated.label} · {associated.role}",
                    "thread-label",
                )
            )
            path = make_label(
                associated.path,
                "detail-value",
                selectable=True,
            )
            path.set_ellipsize(3)
            labels.append(path)
            row.append(labels)
            edit = icon_button("document-edit-symbolic", "Edit path")
            edit.connect(
                "clicked",
                lambda _button, item_index=index: self._edit_path(item_index),
            )
            row.append(edit)
            remove = icon_button("list-remove-symbolic", "Remove path")
            remove.connect(
                "clicked",
                lambda _button, item_index=index: self._remove_path(item_index),
            )
            row.append(remove)
            self.path_list.append(row)

    def _add_path(self) -> None:
        AssociatedPathDialog(
            self.dialog,
            self._path_added,
        ).present()

    def _path_added(self, associated: AssociatedPath) -> None:
        self.associated_paths.append(associated)
        self._render_paths()

    def _edit_path(self, index: int) -> None:
        AssociatedPathDialog(
            self.dialog,
            lambda associated: self._path_edited(index, associated),
            self.associated_paths[index],
        ).present()

    def _path_edited(
        self,
        index: int,
        associated: AssociatedPath,
    ) -> None:
        self.associated_paths[index] = associated
        self._render_paths()

    def _remove_path(self, index: int) -> None:
        self.associated_paths.pop(index)
        self._render_paths()

    def _response_project(
        self,
        _dialog: Gtk.Dialog,
        response_id: int,
    ) -> None:
        if response_id == self.REMOVE:
            self._close()
            self.on_remove()
            return
        if response_id != self.SAVE:
            self._close()
            return
        if not self.name.get_text().strip():
            self.name.add_css_class("error")
            return
        if not self.directory.get_text().strip():
            self.directory.add_css_class("error")
            return
        account_index = self.account.get_selected()
        account = (
            self.account_names[account_index]
            if account_index < len(self.account_names)
            else ""
        )
        terminal_index = self.terminal_mode.get_selected()
        changes: dict[str, object] = {
            "display_name": self.name.get_text().strip(),
            "directory": self.directory.get_text().strip(),
            "codex_account": account,
            "github_account": self.github.get_text().strip(),
            "associated_paths": list(self.associated_paths),
            "terminal_mode": self.terminal_modes[terminal_index],
        }
        self._close()
        self.on_save(changes)

    def present(self) -> None:
        self.dialog.present()


class RemoveProjectDialog:
    REMOVE = 1

    def __init__(
        self,
        parent: Gtk.Window,
        project: Project,
        *,
        session_count: int,
        on_remove: Callable[[], None],
    ):
        self.on_remove = on_remove
        self.dialog = _dialog(
            parent,
            f"Remove {project.name}",
            width=590,
            height=390,
        )
        box = _content(self.dialog)
        box.append(make_label("REMOVE PROJECT?", "dialog-title"))
        box.append(
            make_label(
                "This removes the project from Codex Workbench only. "
                "Files on disk are not deleted.",
                "dialog-banner",
                wrap=True,
            )
        )
        box.append(_detail_row("Project", project.name))
        box.append(
            _detail_row("Canonical root", str(project.path), monospace=True)
        )
        box.append(
            make_label(
                f"{session_count} stored work session(s) will be preserved.",
                "muted",
                wrap=True,
            )
        )
        self.dialog.add_button("Cancel", Gtk.ResponseType.CANCEL)
        remove = self.dialog.add_button(
            "Remove from Workbench",
            self.REMOVE,
        )
        remove.add_css_class("destructive-action")
        self.dialog.connect("response", self._response)

    def _response(self, _dialog: Gtk.Dialog, response_id: int) -> None:
        self.dialog.close()
        if response_id == self.REMOVE:
            self.on_remove()

    def present(self) -> None:
        self.dialog.present()


class ShellDialog:
    OPEN = 1

    def __init__(
        self,
        parent: Gtk.Window,
        project: Project,
        targets: Sequence[ShellTarget],
        *,
        embedded_available: bool,
        embedded_reason: str,
        external_available: bool = True,
        on_open: Callable[[str, str], None],
    ):
        self.on_open = on_open
        self.targets = list(targets)
        self.mode_values: list[str] = []
        mode_labels: list[str] = []
        if embedded_available:
            self.mode_values.append("embedded")
            mode_labels.append("Embedded shell")
        if external_available:
            self.mode_values.append("external")
            mode_labels.append("External terminal")
        self.dialog = _dialog(parent, "Open shell", width=590, height=420)
        box = _content(self.dialog)
        box.append(make_label("PROJECT SHELL", "dialog-title"))
        self.target = Gtk.DropDown.new_from_strings(
            [
                f"{item.label} · {item.path}"
                for item in self.targets
            ]
        )
        self.mode = Gtk.DropDown.new_from_strings(
            mode_labels or ["No shell backend available"]
        )
        if project.terminal.mode in self.mode_values:
            self.mode.set_selected(
                self.mode_values.index(project.terminal.mode)
            )
        for label, widget in (
            ("Working root", self.target),
            ("Open with", self.mode),
        ):
            box.append(make_label(label.upper(), "confidence-label"))
            box.append(widget)
        if not embedded_available or not external_available:
            details = []
            if not embedded_available:
                details.append(embedded_reason)
            if not external_available:
                details.append(
                    "No supported external terminal emulator is installed."
                )
            box.append(
                make_label(
                    " ".join(details),
                    "dialog-banner",
                    wrap=True,
                )
            )
        box.append(
            make_label(
                "The embedded pane is a single PTY. Switching projects "
                "rebinds it to the newly selected project's canonical root.",
                "muted",
                wrap=True,
            )
        )
        self.dialog.add_button("Cancel", Gtk.ResponseType.CANCEL)
        opened = self.dialog.add_button("Open shell", self.OPEN)
        opened.add_css_class("suggested-action")
        opened.set_sensitive(bool(self.mode_values))
        self.dialog.connect("response", self._response)

    def _response(self, _dialog: Gtk.Dialog, response_id: int) -> None:
        if response_id != self.OPEN:
            self.dialog.close()
            return
        if not self.mode_values:
            return
        target_index = self.target.get_selected()
        mode_index = self.mode.get_selected()
        target = self.targets[target_index]
        target_label = "" if target.canonical else target.label
        mode = self.mode_values[mode_index]
        self.dialog.close()
        self.on_open(mode, target_label)

    def present(self) -> None:
        self.dialog.present()


class ThreadDialog:
    ADD = 1

    def __init__(
        self,
        parent: Gtk.Window,
        on_add: Callable[[str, str, str], None],
    ):
        self.on_add = on_add
        self.dialog = _dialog(parent, "Add ChatGPT thread", width=590, height=420)
        box = _content(self.dialog)
        box.append(make_label("THREAD REFERENCE", "dialog-title"))
        self.label = Gtk.Entry(placeholder_text="Research / architecture / build")
        self.url = Gtk.Entry(placeholder_text="https://chatgpt.com/c/…")
        self.notes = Gtk.Entry(placeholder_text="Optional context note")
        for label, widget in (
            ("Label", self.label),
            ("URL", self.url),
            ("Notes", self.notes),
        ):
            box.append(make_label(label.upper(), "confidence-label"))
            box.append(widget)
        self.dialog.add_button("Cancel", Gtk.ResponseType.CANCEL)
        add = self.dialog.add_button("Add thread", self.ADD)
        add.add_css_class("suggested-action")
        self.dialog.connect("response", self._response)

    def _response(self, _dialog: Gtk.Dialog, response_id: int) -> None:
        if response_id != self.ADD:
            self.dialog.close()
            return
        url = self.url.get_text().strip()
        if not url:
            self.url.add_css_class("error")
            return
        self.dialog.close()
        self.on_add(
            url,
            self.label.get_text().strip(),
            self.notes.get_text().strip(),
        )

    def present(self) -> None:
        self.dialog.present()


class SettingsDialog:
    SAVE = 1

    def __init__(
        self,
        parent: Gtk.Window,
        settings: WorkbenchSettings,
        *,
        config_path: str,
        data_path: str,
        on_save: Callable[[dict[str, object]], None],
    ):
        self.on_save = on_save
        self.dialog = _dialog(parent, "Settings", width=640, height=610)
        box = _content(self.dialog)
        box.append(make_label("WORKBENCH SETTINGS", "dialog-title"))
        self.terminal = Gtk.Entry(text=settings.preferred_terminal)
        self.shell_modes = ["embedded", "external"]
        self.shell_mode = Gtk.DropDown.new_from_strings(
            ["Embedded shell", "External terminal"]
        )
        self.shell_mode.set_selected(
            0 if settings.shell_mode == "embedded" else 1
        )
        self.launcher = Gtk.Entry(
            text=settings.launcher_path,
            placeholder_text="Auto-detect codex-start from PATH",
        )
        self.clipboard = Gtk.Switch(
            active=settings.clipboard_mode != "disabled",
            halign=Gtk.Align.START,
        )
        self.low = Gtk.SpinButton.new_with_range(1, 100, 1)
        self.low.set_value(settings.low_usage_threshold)
        self.critical = Gtk.SpinButton.new_with_range(0, 100, 1)
        self.critical.set_value(settings.critical_usage_threshold)
        self.system_theme = Gtk.Switch(
            active=settings.theme == "system",
            halign=Gtk.Align.START,
        )
        for label, widget in (
            ("Preferred terminal", self.terminal),
            ("Default shell mode", self.shell_mode),
            ("Codex launcher path", self.launcher),
            ("Clipboard integration", self.clipboard),
            ("Low usage threshold", self.low),
            ("Critical threshold", self.critical),
            ("Follow system theme", self.system_theme),
        ):
            box.append(make_label(label.upper(), "confidence-label"))
            box.append(widget)
        box.append(Gtk.Separator())
        box.append(_detail_row("Config", config_path, monospace=True))
        box.append(_detail_row("Data", data_path, monospace=True))
        self.dialog.add_button("Cancel", Gtk.ResponseType.CANCEL)
        save = self.dialog.add_button("Save settings", self.SAVE)
        save.add_css_class("suggested-action")
        self.dialog.connect("response", self._response)

    def _response(self, _dialog: Gtk.Dialog, response_id: int) -> None:
        if response_id != self.SAVE:
            self.dialog.close()
            return
        self.dialog.close()
        self.on_save(
            {
                "preferred_terminal": self.terminal.get_text().strip(),
                "shell_mode": self.shell_modes[
                    self.shell_mode.get_selected()
                ],
                "launcher_path": self.launcher.get_text().strip(),
                "clipboard_mode": (
                    "auto" if self.clipboard.get_active() else "disabled"
                ),
                "low_usage_threshold": self.low.get_value_as_int(),
                "critical_usage_threshold": self.critical.get_value_as_int(),
                "theme": "system" if self.system_theme.get_active() else "dark",
            }
        )

    def present(self) -> None:
        self.dialog.present()


class CommandPalette:
    def __init__(
        self,
        parent: Gtk.Window,
        commands: Sequence[PaletteCommand],
        on_activate: Callable[[PaletteCommand], None],
    ):
        self.commands = list(commands)
        self.on_activate = on_activate
        self.dialog = _dialog(parent, "Command palette", width=620, height=520)
        box = _content(self.dialog, spacing=8)
        self.search = Gtk.SearchEntry(placeholder_text="Switch project or run an action…")
        box.append(self.search)
        self.list = Gtk.ListBox(
            selection_mode=Gtk.SelectionMode.SINGLE,
            activate_on_single_click=True,
        )
        self.list.add_css_class("boxed-list")
        scroll = Gtk.ScrolledWindow(
            child=self.list,
            vexpand=True,
            hscrollbar_policy=Gtk.PolicyType.NEVER,
        )
        box.append(scroll)
        self.search.connect("search-changed", self._filter)
        self.search.connect("activate", self._activate_selected)
        self.list.connect("row-activated", self._row_activated)
        self.dialog.add_button("Close", Gtk.ResponseType.CLOSE)
        self.dialog.connect("response", lambda *_args: self.dialog.close())
        self._initial_focus_pending = True
        self.dialog.connect("map", self._dialog_mapped)
        self.dialog.connect(
            "notify::is-active",
            self._dialog_activation_changed,
        )
        self.key_controller = Gtk.EventControllerKey()
        self.key_controller.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        self.key_controller.connect("key-pressed", self._key_pressed)
        self.dialog.add_controller(self.key_controller)
        self._populate(self.commands)

    def _populate(self, commands: Sequence[PaletteCommand]) -> None:
        clear(self.list)
        for command in commands:
            row = Gtk.ListBoxRow()
            row.command = command
            body = Gtk.Box(
                orientation=Gtk.Orientation.HORIZONTAL,
                spacing=10,
                margin_top=8,
                margin_bottom=8,
                margin_start=10,
                margin_end=10,
            )
            labels = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
            labels.set_hexpand(True)
            labels.append(make_label(command.title, "command-title"))
            if command.project and command.subtitle:
                labels.append(make_label(command.subtitle, "muted"))
            body.append(labels)
            if command.subtitle and not command.project:
                body.append(make_label(command.subtitle, "command-shortcut"))
            row.set_child(body)
            self.list.append(row)
        first = self.list.get_row_at_index(0)
        if first:
            self.list.select_row(first)

    def _filter(self, entry: Gtk.SearchEntry) -> None:
        query = entry.get_text().casefold().strip()
        commands = [
            item
            for item in self.commands
            if query in f"{item.title} {item.subtitle}".casefold()
        ]
        self._populate(commands)

    def _activate_selected(self, _entry: Gtk.SearchEntry) -> None:
        row = self.list.get_selected_row()
        if row:
            self._row_activated(self.list, row)

    def _row_activated(self, _list: Gtk.ListBox, row: Gtk.ListBoxRow) -> None:
        command = row.command
        self.dialog.close()
        self.on_activate(command)

    def _dialog_mapped(self, _dialog: Gtk.Dialog) -> None:
        self._focus_search()

    def _dialog_activation_changed(
        self,
        dialog: Gtk.Dialog,
        _property: object,
    ) -> None:
        if self._initial_focus_pending and dialog.is_active():
            self._focus_search()

    def _focus_search(self) -> None:
        self.dialog.set_focus(self.search)
        self.search.grab_focus()
        if self.dialog.is_active():
            self._initial_focus_pending = False

    def _key_pressed(
        self,
        _controller: Gtk.EventControllerKey,
        keyval: int,
        _keycode: int,
        _state: Gdk.ModifierType,
    ) -> bool:
        if keyval != Gdk.KEY_Escape:
            return False
        self.dialog.close()
        return True

    def present(self) -> None:
        self._initial_focus_pending = True
        self.search.set_text("")
        self.dialog.set_focus(self.search)
        self.dialog.present()
