"""The Board: a flat, importance-ranked task list distinct from the Tasks
tab's recurring-with-minutes tracker. A board task is just a name + a 1-10
importance rank (list always sorted highest-first) + optional info (any mix
of text/photo/link, shown together in the detail popup) + an optional
recurrence: either a specific set of weekdays, or a fixed weekly/monthly/
yearly schedule (see board_store.py). Finished recurring tasks wait in the
Upcoming tab until their next occurrence instead of the Finished tab.

Detail popup layout borrows from qt_ui/review_tab.py's problem popup, but a
board task has no "review count" -- the popup shows the date it was first
opened instead of a review history.

Both "finishing" a fresh Add Task form and marking an existing task done
trigger qt_ui/confetti.py's screen-wide confetti fall.
"""
import os
from datetime import datetime

from PySide6.QtCore import Qt, QTimer, QUrl
from PySide6.QtGui import QColor, QDesktopServices, QFont, QPixmap
from PySide6.QtWidgets import (
    QFileDialog,
    QGraphicsDropShadowEffect,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMenu,
    QPushButton,
    QScrollArea,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)
from qt_ui.wrap_edit import WrapEdit

import board_store
from qt_ui.safe_link import make_link_label
from qt_ui.ui_kit import DANGER_STYLE, INK, MUTED, NUM_FONTS, SECONDARY_STYLE, button, link_button

# Not a real preset tag -- board_store.PRESET_TAGS only holds tags a task can
# actually be assigned, and this one can't be (a task either has tags or it
# doesn't). It exists purely so "no tags" has a Hide Tags menu entry of its
# own; deliberately no color/bg, since it's never rendered anywhere on a card.
UNLABELED_TAG = {"id": "unlabeled", "label": "Un-labeled"}
from qt_ui.confetti import show_confetti
from tasks_store import WEEKDAY_CODES

REACTIVATE_CHECK_MS = 60_000

_DAY_LABELS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]


def _format_date(iso_string):
    if not iso_string:
        return None
    try:
        parsed = datetime.fromisoformat(iso_string)
    except ValueError:
        return iso_string
    # %-d (no leading zero) is a Linux/macOS-only strftime flag -- Windows
    # (this app's target platform) raises ValueError on it, so the day is
    # formatted by hand instead.
    return f"{parsed.strftime('%b')} {parsed.day}, {parsed.year}"


_PATTERN_LABELS = {"weekly": "Weekly", "monthly": "Monthly", "yearly": "Yearly"}


def _is_recurring_task(task):
    return bool(task.get("recurringDays")) or task.get("recurrencePattern") in _PATTERN_LABELS


def _recurring_text(task):
    pattern = task.get("recurrencePattern")
    if pattern in _PATTERN_LABELS:
        return f"Repeats: {_PATTERN_LABELS[pattern]}"
    recurring_days = task.get("recurringDays")
    if not recurring_days:
        return None
    ordered = [label for code, label in zip(WEEKDAY_CODES, _DAY_LABELS) if code in recurring_days]
    return "Repeats: " + ", ".join(ordered)


def _build_info_content(layout, task):
    """Unlike review_tab's single-description-type popup, a board task's
    text/photo/link fields are all independently optional -- render
    whichever ones are actually filled in, in order."""
    any_content = False

    text = (task.get("descriptionText") or "").strip()
    if text:
        any_content = True
        text_view = QTextEdit()
        text_view.setReadOnly(True)
        text_view.setPlainText(text)
        text_view.setMaximumHeight(160)
        # No parent styling reaches this widget reliably (it's not under
        # #ContentArea or #PopupBg), so without an explicit color it falls
        # back to the OS palette -- white text on Windows dark mode, which
        # is unreadable against the panel's light background.
        text_view.setStyleSheet(
            "background: #FAFBFC; border: 1px solid #E3E5E9; border-radius: 8px; "
            "padding: 4px 6px; color: #1F2328;"
        )
        layout.addWidget(text_view)

    photo_path = task.get("descriptionPhotoPath")
    if photo_path:
        pixmap = QPixmap(photo_path) if os.path.exists(photo_path) else None
        if pixmap and not pixmap.isNull():
            any_content = True
            image_label = QLabel()
            image_label.setAlignment(Qt.AlignCenter)
            image_label.setPixmap(pixmap.scaled(420, 300, Qt.KeepAspectRatio, Qt.SmoothTransformation))
            layout.addWidget(image_label)

    link = (task.get("descriptionLink") or "").strip()
    if link:
        any_content = True
        link_label = make_link_label(link)
        link_label.setWordWrap(True)
        layout.addWidget(link_label)

    if not any_content:
        empty_label = QLabel("No extra info added.")
        empty_label.setStyleSheet("color: #8A8F98;")
        layout.addWidget(empty_label)


def importance_colors(value):
    """(background, text) for an importance 1-10: cool for the low end, amber in the
    middle, red for what matters most -- so the list reads at a glance."""
    if value >= 9:
        return "#FDE1DE", "#B42318"
    if value >= 7:
        return "#FFE6D5", "#B54708"
    if value >= 4:
        return "#FEF0C7", "#93370D"
    return "#E4ECFB", "#2F4D9A"


def _importance_style(value, size):
    bg, fg = importance_colors(value)
    return (f"background:{bg};color:{fg};border-radius:{size // 3}px;font-size:{size // 2}px;"
            "font-family:'Bahnschrift SemiBold','Segoe UI Semibold','Segoe UI';")


def _importance_button_style(value, radius):
    bg, fg = importance_colors(value)
    return (
        f"QPushButton {{ border-radius: {radius}px; font-size: 12px; font-weight: 600; "
        "background: #EEF0F4; color: #1B2230; border: none; padding: 0; }"
        f"QPushButton:hover {{ background: {bg}; color: {fg}; }}"
        f"QPushButton:checked {{ background: {fg}; color: #FFFFFF; }}"
    )


def _numeral_font(pixel_size):
    font = QFont()
    font.setFamilies(NUM_FONTS)
    font.setPixelSize(pixel_size)
    return font


_CHIP_ON = f"QPushButton{{background:{INK};color:#FFFFFF;border:1px solid {INK};border-radius:13px;"
_CHIP_STYLE = (
    f"QPushButton{{background:#FFFFFF;color:{INK};border:1px solid #E1E5EC;border-radius:13px;"
    "font-size:12.5px;font-weight:600;padding:0 13px;}"
    "QPushButton:hover{background:#F6F7FA;}"
    f"QPushButton:checked{{background:{INK};color:#FFFFFF;border:1px solid {INK};}}")


class BoardTab(QWidget):
    def __init__(self):
        super().__init__()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(28, 24, 28, 20)
        layout.setSpacing(14)

        self._tag_filter = "all"
        self._hidden_tags = set()
        layout.addLayout(self._build_header())
        layout.addLayout(self._build_tag_chips())

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.NoFrame)
        scroll.setStyleSheet("QScrollArea{background: transparent; border: none;}")
        self._list_container = QWidget()
        self._list_container.setObjectName("BoardList")
        self._list_container.setStyleSheet("QWidget#BoardList{background: transparent;}")
        self._list_layout = QVBoxLayout(self._list_container)
        self._list_layout.setContentsMargins(2, 2, 6, 16)
        self._list_layout.setSpacing(10)
        self._list_layout.setAlignment(Qt.AlignTop)
        scroll.setWidget(self._list_container)
        layout.addWidget(scroll, 1)

        self.refresh()

        # Recurring board tasks reactivate on their next scheduled weekday --
        # this periodic refresh is what notices that without requiring the
        # user to switch tabs and back.
        self._reactivate_timer = QTimer(self)
        self._reactivate_timer.timeout.connect(self._refresh_if_changed)
        self._reactivate_timer.start(REACTIVATE_CHECK_MS)

    def _board_signature(self):
        return (
            tuple(t["id"] for t in board_store.list_active_tasks()),
            len(board_store.list_finished_tasks()),
            len(board_store.list_upcoming_tasks()),
        )

    def _refresh_if_changed(self):
        # A blind rebuild every minute collapsed whatever card the user had
        # open (details, difficulty editor) mid-read, so only rebuild when a
        # task actually reactivated or moved.
        if self._board_signature() != self._rendered_signature:
            self.refresh()

    def _build_header(self):
        header = QHBoxLayout()
        header.setSpacing(8)
        titles = QVBoxLayout()
        titles.setSpacing(2)
        title = QLabel("The Board")
        title.setStyleSheet(f"font-size: 26px; font-weight: 700; color: {INK}; background: transparent;")
        titles.addWidget(title)
        self._subtitle = QLabel()
        self._subtitle.setStyleSheet(f"font-size: 13px; color: {MUTED}; background: transparent;")
        titles.addWidget(self._subtitle)
        header.addLayout(titles)
        header.addStretch(1)

        self._hide_tags_button = link_button("Hide Tags")
        self._hide_tags_button.clicked.connect(self._open_hide_tags_menu)
        header.addWidget(self._hide_tags_button)

        self._upcoming_button = link_button("Upcoming")
        self._upcoming_button.clicked.connect(self._open_upcoming_dialog)
        header.addWidget(self._upcoming_button)

        self._finished_button = link_button("Finished")
        self._finished_button.clicked.connect(self._open_finished_dialog)
        header.addWidget(self._finished_button)
        header.addSpacing(10)

        add_button = button("+ Add task", f"QPushButton{{background:{INK};color:#FFFFFF;border:none;border-radius:12px;"
                            "font-size:13.5px;font-weight:600;padding:0 18px;}"
                            "QPushButton:hover{background:#2B3445;}", height=38)
        add_button.clicked.connect(self._open_add_dialog)
        header.addWidget(add_button)
        return header

    def _build_tag_chips(self):
        """One chip per tag, instead of a drop-down: click one to show only that tag."""
        row = QHBoxLayout()
        row.setSpacing(6)
        self._tag_chips = {}
        entries = [("all", "All")] + [(t["id"], t["label"]) for t in board_store.PRESET_TAGS]
        for tag_id, label in entries:
            chip = QPushButton(label)
            chip.setCheckable(True)
            chip.setCursor(Qt.PointingHandCursor)
            chip.setFixedHeight(28)
            chip.setStyleSheet(_CHIP_STYLE)
            chip.setChecked(tag_id == self._tag_filter)
            chip.clicked.connect(lambda _checked=False, t=tag_id: self._set_tag_filter(t))
            self._tag_chips[tag_id] = chip
            row.addWidget(chip)
        row.addStretch(1)
        return row

    def _set_tag_filter(self, tag_id):
        self._tag_filter = tag_id
        for key, chip in self._tag_chips.items():
            chip.setChecked(key == tag_id)
        self.refresh()

    def _open_hide_tags_menu(self):
        menu = QMenu(self)
        for tag in list(board_store.PRESET_TAGS) + [UNLABELED_TAG]:
            action = menu.addAction(tag["label"])
            action.setCheckable(True)
            action.setChecked(tag["id"] in self._hidden_tags)
            action.toggled.connect(
                lambda checked, tag_id=tag["id"]: self._toggle_hidden_tag(tag_id, checked)
            )
        menu.exec(self._hide_tags_button.mapToGlobal(self._hide_tags_button.rect().bottomLeft()))

    def _toggle_hidden_tag(self, tag_id, checked):
        if checked:
            self._hidden_tags.add(tag_id)
        else:
            self._hidden_tags.discard(tag_id)
        self.refresh()

    def _is_hidden(self, task):
        tags = task.get("tags") or []
        if not tags:
            return "unlabeled" in self._hidden_tags
        return any(tag_id in self._hidden_tags for tag_id in tags)

    def refresh(self):
        self._rendered_signature = self._board_signature()
        while self._list_layout.count():
            item = self._list_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()

        self._hide_tags_button.setText(
            f"Hide Tags ({len(self._hidden_tags)})" if self._hidden_tags else "Hide Tags"
        )

        tasks = board_store.list_active_tasks()
        if self._tag_filter != "all":
            tasks = [t for t in tasks if self._tag_filter in (t.get("tags") or [])]
        tasks_before_hide = tasks
        if self._hidden_tags:
            tasks = [t for t in tasks if not self._is_hidden(t)]

        if not tasks:
            if tasks_before_hide and self._hidden_tags:
                empty_text = "All tasks are hidden right now -- adjust Hide Tags to see them."
            elif self._tag_filter != "all":
                empty_text = "No tasks with this tag."
            else:
                empty_text = "Nothing on the board yet. Click “+ Add task” to put the first thing on it."
            empty_label = QLabel(empty_text)
            empty_label.setWordWrap(True)
            empty_label.setAlignment(Qt.AlignCenter)
            empty_label.setStyleSheet(
                f"color: {MUTED}; font-size: 14px; background: #FFFFFF; border: 1px dashed #D0D5DD; "
                "border-radius: 16px; padding: 36px 24px;")
            self._list_layout.addWidget(empty_label)
        for task in tasks:
            self._list_layout.addWidget(_BoardCard(task, on_changed=self.refresh))

        finished_count = len(board_store.list_finished_tasks())
        self._finished_button.setText(f"Finished ({finished_count})" if finished_count else "Finished")

        upcoming_count = len(board_store.list_upcoming_tasks())
        self._upcoming_button.setText(f"Upcoming ({upcoming_count})" if upcoming_count else "Upcoming")

        active_count = len(board_store.list_active_tasks())
        parts = [f"{active_count} task{'s' if active_count != 1 else ''}"]
        if len(tasks) != active_count:
            parts[0] = f"{len(tasks)} of {active_count} tasks"
        parts.append("most important first")
        self._subtitle.setText(" · ".join(parts))

    def _open_add_dialog(self):
        _AddTaskDialog(on_added=lambda _t: self.refresh())

    def _open_finished_dialog(self):
        _FinishedListDialog(on_changed=self.refresh)

    def _open_upcoming_dialog(self):
        _UpcomingListDialog(on_changed=self.refresh)


def _make_tag_pill(tag):
    pill = QLabel(tag["label"])
    pill.setStyleSheet(
        f"background: {tag['bg']}; color: {tag['color']}; "
        f"border-radius: 9px; padding: 1px 9px; font-size: 11px; font-weight: 600;"
    )
    return pill


class _BoardCard(QFrame):
    def __init__(self, task, on_changed):
        super().__init__()
        self._task = task
        self._on_changed = on_changed
        self._details_panel = None
        self._expanded = False
        self._importance_editor = None
        self.setProperty("class", "BoardCard")
        self.setAttribute(Qt.WA_StyledBackground, True)
        # "Un-labeled" (no tags) is deliberately invisible on the card itself
        # -- no pill, no tint, nothing a user sees. It only exists as a
        # filterable concept for the Hide Tags menu (see BoardTab._is_hidden),
        # derived purely from an empty tags list.

        shadow = QGraphicsDropShadowEffect(self)
        shadow.setBlurRadius(20)
        shadow.setOffset(0, 4)
        shadow.setColor(QColor(16, 24, 40, 16))
        self.setGraphicsEffect(shadow)

        self._outer = QVBoxLayout(self)
        self._outer.setContentsMargins(16, 14, 16, 14)
        self._outer.setSpacing(10)

        top_row = QHBoxLayout()
        top_row.setSpacing(14)

        self._badge = _ImportanceBadge(task["importance"], on_double_click=self._toggle_importance_editor)
        top_row.addWidget(self._badge)

        text_col = QVBoxLayout()
        text_col.setSpacing(3)
        name_row = QHBoxLayout()
        name_row.setSpacing(8)
        name_label = QLabel(task.get("name") or "(untitled task)")
        name_label.setWordWrap(True)
        name_label.setStyleSheet(f"font-size: 15.5px; font-weight: 650; color: {INK}; background: transparent;")
        name_row.addWidget(name_label, 1)
        text_col.addLayout(name_row)

        meta_row = QHBoxLayout()
        meta_row.setSpacing(6)
        tag_ids = task.get("tags") or []
        for tag_id in tag_ids:
            tag = board_store.PRESET_TAGS_BY_ID.get(tag_id)
            if tag:
                meta_row.addWidget(_make_tag_pill(tag))
        recurring_text = _recurring_text(task)
        if recurring_text:
            recur_label = QLabel(f"↻ {recurring_text.replace('Repeats: ', '')}")
            recur_label.setStyleSheet(f"font-size: 12px; color: {MUTED}; background: transparent;")
            meta_row.addWidget(recur_label)
        next_due = _format_date(task.get("nextDueDate")) if task.get("finished") else None
        if next_due:
            next_label = QLabel(f"Next: {next_due}")
            next_label.setStyleSheet(f"font-size: 12px; color: {MUTED}; background: transparent;")
            meta_row.addWidget(next_label)
        meta_row.addStretch(1)
        text_col.addLayout(meta_row)
        top_row.addLayout(text_col, 1)

        self._details_button = button("Details", SECONDARY_STYLE, height=34)
        self._details_button.clicked.connect(self._toggle_details)
        top_row.addWidget(self._details_button, 0, Qt.AlignVCenter)

        self._outer.addLayout(top_row)

    def _toggle_details(self):
        if self._expanded:
            self._collapse()
        else:
            self._expand()

    def _expand(self):
        # mark_opened stamps firstOpenedAt on the first-ever expand, same as
        # the old popup's open action.
        task = board_store.mark_opened(self._task["id"])
        if task is None:
            return
        self._task = task
        self._details_panel = _build_details_panel(
            task,
            on_deleted=self._handle_delete_one,
            on_deleted_all=self._handle_delete_all,
            on_marked_done=self._handle_mark_done,
            on_edit=self._open_edit_dialog,
        )
        self._outer.addWidget(self._details_panel)
        self._expanded = True
        self._details_button.setText("Hide")

    def _collapse(self):
        if self._details_panel is not None:
            self._details_panel.deleteLater()
            self._details_panel = None
        self._expanded = False
        self._details_button.setText("Details")

    def _toggle_importance_editor(self):
        if self._importance_editor is not None:
            self._collapse_importance_editor()
        else:
            self._expand_importance_editor()

    def _expand_importance_editor(self):
        self._importance_editor = _build_importance_editor(
            self._task["importance"],
            on_save=self._save_importance,
            on_cancel=self._collapse_importance_editor,
        )
        # Right under the top row, ahead of the (possibly open) details panel.
        self._outer.insertWidget(1, self._importance_editor)

    def _collapse_importance_editor(self):
        if self._importance_editor is not None:
            self._importance_editor.deleteLater()
            self._importance_editor = None

    def _save_importance(self, new_value):
        board_store.update_importance(self._task["id"], new_value)
        self._task["importance"] = new_value
        self._badge.setText(str(new_value))
        self._collapse_importance_editor()
        if self._on_changed:
            self._on_changed()

    def _handle_mark_done(self):
        board_store.finish_task(self._task["id"])
        show_confetti()
        if self._on_changed:
            self._on_changed()

    def _handle_delete_one(self):
        # For a recurring task this just skips the current occurrence
        # (same recurrence math as Mark Done, minus the confetti) rather
        # than ending the series -- Delete All is what removes it for good.
        if _is_recurring_task(self._task):
            board_store.finish_task(self._task["id"])
        else:
            board_store.delete_task(self._task["id"])
        if self._on_changed:
            self._on_changed()

    def _handle_delete_all(self):
        board_store.delete_task(self._task["id"])
        if self._on_changed:
            self._on_changed()

    def _open_edit_dialog(self):
        _EditTaskDialog(self._task, on_saved=lambda _t: self._on_changed() if self._on_changed else None)


class _ImportanceBadge(QLabel):
    """The importance tile -- double-click opens a small inline editor
    (see _build_importance_editor) to change it, separate from the larger
    Details expansion. Its colour follows the value (see importance_colors)."""

    def __init__(self, value, on_double_click):
        super().__init__(str(value))
        self._on_double_click = on_double_click
        self.setFixedSize(46, 46)
        self.setAlignment(Qt.AlignCenter)
        self.setCursor(Qt.PointingHandCursor)
        self.setToolTip("Importance. Double-click to change it")
        self.setFont(_numeral_font(24))
        self._restyle(value)

    def _restyle(self, value):
        self.setStyleSheet(_importance_style(value, 46))

    def setText(self, text):
        super().setText(text)
        try:
            self._restyle(int(text))
        except ValueError:
            pass

    def mouseDoubleClickEvent(self, event):
        self._on_double_click()
        super().mouseDoubleClickEvent(event)


def _build_importance_editor(current, on_save, on_cancel):
    panel = QFrame()
    panel.setStyleSheet("background: #F4F6F9; border-radius: 12px;")
    layout = QVBoxLayout(panel)
    layout.setContentsMargins(14, 10, 14, 10)
    layout.setSpacing(8)

    layout.addWidget(_bold_label("Importance"))
    pending = {"value": current}
    row, _buttons = _build_importance_row(current, lambda n: pending.update(value=n))
    layout.addLayout(row)

    button_row = QHBoxLayout()
    button_row.addStretch(1)
    cancel_button = button("Cancel", SECONDARY_STYLE, height=34)
    cancel_button.clicked.connect(on_cancel)
    button_row.addWidget(cancel_button)
    save_button = button("Save", f"QPushButton{{background:{INK};color:#FFFFFF;border:none;border-radius:12px;"
                         "font-size:13.5px;font-weight:600;padding:0 18px;}QPushButton:hover{background:#2B3445;}",
                         height=34)
    save_button.clicked.connect(lambda: on_save(pending["value"]))
    button_row.addWidget(save_button)
    layout.addLayout(button_row)

    return panel


def _make_confirm_button(label, confirm_label, style, on_confirmed):
    """A button that arms on the first click (swapping to confirm_label)
    and only fires on_confirmed on the second -- used in place of a
    QMessageBox for delete actions, since those popups here render with no
    visible buttons."""
    btn = QPushButton(label)
    btn.setStyleSheet(style)
    state = {"armed": False}

    def _handle_click():
        if not state["armed"]:
            state["armed"] = True
            btn.setText(confirm_label)
            return
        on_confirmed()

    btn.clicked.connect(_handle_click)
    return btn


def _build_details_panel(task, on_deleted, on_deleted_all, on_marked_done, on_edit):
    panel = QFrame()
    panel.setStyleSheet("background: #F4F6F9; border-radius: 12px;")
    layout = QVBoxLayout(panel)
    layout.setContentsMargins(16, 14, 16, 14)
    layout.setSpacing(8)

    opened_text = _format_date(task.get("firstOpenedAt")) or "Just now"
    opened_label = QLabel(f"Opened: {opened_text}")
    opened_label.setStyleSheet("color: #5A6070; font-size: 12px;")
    layout.addWidget(opened_label)

    recurring_text = _recurring_text(task)
    if recurring_text:
        recur_label = QLabel(recurring_text)
        recur_label.setStyleSheet("color: #5A6070; font-size: 12px;")
        layout.addWidget(recur_label)

    if task.get("finished"):
        finished_label = QLabel(f"Completed: {_format_date(task.get('finishedAt'))}")
        finished_label.setStyleSheet("color: #43a047; font-size: 12px; font-weight: 600;")
        layout.addWidget(finished_label)
        next_due = _format_date(task.get("nextDueDate"))
        if next_due:
            next_label = QLabel(f"Next due: {next_due}")
            next_label.setStyleSheet("color: #5A6070; font-size: 12px;")
            layout.addWidget(next_label)

    _build_info_content(layout, task)

    action_row = QHBoxLayout()
    edit_button = button("Edit details", SECONDARY_STYLE, height=36)
    edit_button.clicked.connect(on_edit)
    action_row.addWidget(edit_button)
    action_row.addStretch(1)
    if not task.get("finished"):
        done_button = button(
            "Mark done",
            "QPushButton{background:#157F3B;color:#FFFFFF;border:none;border-radius:12px;"
            "font-size:13.5px;font-weight:600;padding:0 20px;}QPushButton:hover{background:#116B32;}", height=36)
        done_button.clicked.connect(on_marked_done)
        action_row.addWidget(done_button)

    delete_style = DANGER_STYLE.replace("padding:0 20px", "padding:7px 18px")
    delete_button = _make_confirm_button("Delete", "Click again to delete", delete_style, on_deleted)
    action_row.addWidget(delete_button)

    if _is_recurring_task(task):
        # Always shown for a repeating task -- Delete only skips the
        # current occurrence (the series keeps going), Delete All ends it.
        delete_all_button = _make_confirm_button(
            "Delete All", "Click again to delete all", delete_style, on_deleted_all
        )
        action_row.addWidget(delete_all_button)

    layout.addLayout(action_row)

    return panel


def _build_importance_row(current, on_change):
    row = QHBoxLayout()
    row.setSpacing(4)
    buttons = {}

    def _set(n):
        for value, btn in buttons.items():
            btn.setChecked(value == n)
        on_change(n)

    for n in range(1, 11):
        btn = QPushButton(str(n))
        btn.setFixedSize(28, 28)
        btn.setCheckable(True)
        btn.setChecked(n == current)
        btn.setStyleSheet(_importance_button_style(n, 14))
        btn.clicked.connect(lambda checked=False, n=n: _set(n))
        row.addWidget(btn)
        buttons[n] = btn
    return row, buttons


class _FinishedListDialog(QWidget):
    """Same board-card rows as the main board, filtered to one-off tasks
    that are actually done -- recurring tasks show up in
    _UpcomingListDialog instead (see board_store.list_finished_tasks)."""

    def __init__(self, on_changed=None):
        super().__init__(None, Qt.WindowStaysOnTopHint)
        self.setObjectName("PopupBg")
        self.setWindowTitle("Finished Tasks")
        self.resize(480, 560)
        self._on_changed = on_changed

        layout = QVBoxLayout(self)
        layout.addWidget(_bold_label("Finished Tasks"))

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.NoFrame)
        self._list_container = QWidget()
        self._list_layout = QVBoxLayout(self._list_container)
        self._list_layout.setSpacing(12)
        self._list_layout.setAlignment(Qt.AlignTop)
        scroll.setWidget(self._list_container)
        layout.addWidget(scroll, 1)

        close_button = QPushButton("Close")
        close_button.clicked.connect(self.close)
        layout.addWidget(close_button)

        self._refresh()
        self.show()
        _register_popup(self)

    def _refresh(self):
        while self._list_layout.count():
            item = self._list_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()

        finished = board_store.list_finished_tasks()
        if not finished:
            empty_label = QLabel("Nothing finished yet.")
            empty_label.setStyleSheet("color: #8A8F98;")
            self._list_layout.addWidget(empty_label)
        for task in finished:
            self._list_layout.addWidget(_BoardCard(task, on_changed=self._handle_changed))

    def _handle_changed(self):
        self._refresh()
        if self._on_changed:
            self._on_changed()


class _UpcomingListDialog(QWidget):
    """Recurring tasks currently off the board, waiting on their
    next_due_date to reactivate -- see board_store.list_upcoming_tasks().
    Same board-card rows as the main board and Finished list."""

    def __init__(self, on_changed=None):
        super().__init__(None, Qt.WindowStaysOnTopHint)
        self.setObjectName("PopupBg")
        self.setWindowTitle("Upcoming Tasks")
        self.resize(480, 560)
        self._on_changed = on_changed

        layout = QVBoxLayout(self)
        layout.addWidget(_bold_label("Upcoming Tasks"))

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.NoFrame)
        self._list_container = QWidget()
        self._list_layout = QVBoxLayout(self._list_container)
        self._list_layout.setSpacing(12)
        self._list_layout.setAlignment(Qt.AlignTop)
        scroll.setWidget(self._list_container)
        layout.addWidget(scroll, 1)

        close_button = QPushButton("Close")
        close_button.clicked.connect(self.close)
        layout.addWidget(close_button)

        self._refresh()
        self.show()
        _register_popup(self)

    def _refresh(self):
        while self._list_layout.count():
            item = self._list_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()

        upcoming = board_store.list_upcoming_tasks()
        if not upcoming:
            empty_label = QLabel("Nothing recurring waiting to come back yet.")
            empty_label.setStyleSheet("color: #8A8F98;")
            self._list_layout.addWidget(empty_label)
        for task in upcoming:
            self._list_layout.addWidget(_BoardCard(task, on_changed=self._handle_changed))

    def _handle_changed(self):
        self._refresh()
        if self._on_changed:
            self._on_changed()


def _build_tag_picker(layout, initial_tags=None):
    """Checkable pills for each preset tag (board_store.PRESET_TAGS). Returns
    a dict of tag id -> checkable QPushButton, styled like the tag pills
    themselves so the picker previews what the pill will look like."""
    initial_tags = initial_tags or []
    buttons = {}

    layout.addWidget(_bold_label("Tags (optional)"))
    row = QHBoxLayout()
    for tag in board_store.PRESET_TAGS:
        btn = QPushButton(tag["label"])
        btn.setCheckable(True)
        btn.setChecked(tag["id"] in initial_tags)
        btn.setStyleSheet(
            "QPushButton { border-radius: 9px; padding: 3px 12px; font-size: 12px; font-weight: 600; "
            f"background: #F0F1F5; color: {tag['color']}; border: 1px solid {tag['color']}; }}"
            f"QPushButton:checked {{ background: {tag['bg']}; }}"
        )
        row.addWidget(btn)
        buttons[tag["id"]] = btn
    row.addStretch(1)
    layout.addLayout(row)
    return buttons


def _build_recurrence_pickers(layout, initial_days=None, initial_pattern=None):
    """Builds the "specific days" + "fixed schedule" button rows shared by
    the Add and Edit dialogs, wired so picking one clears the other (a task
    recurs one way or the other, not both). Returns (day_buttons,
    pattern_buttons) dicts of checkable QPushButtons."""
    initial_days = initial_days or []
    day_buttons = {}
    pattern_buttons = {}

    def _on_day_toggled(btn):
        if btn.isChecked():
            for pattern_btn in pattern_buttons.values():
                pattern_btn.setChecked(False)

    def _on_pattern_toggled(key):
        btn = pattern_buttons[key]
        if btn.isChecked():
            for other_key, other_btn in pattern_buttons.items():
                if other_key != key:
                    other_btn.setChecked(False)
            for day_btn in day_buttons.values():
                day_btn.setChecked(False)

    layout.addWidget(_bold_label("Repeats on specific days (optional)"))
    day_row = QHBoxLayout()
    for code, label in zip(WEEKDAY_CODES, _DAY_LABELS):
        btn = QPushButton(label)
        btn.setCheckable(True)
        btn.setChecked(code in initial_days)
        btn.setProperty("class", "SecondaryButton")
        btn.clicked.connect(lambda checked=False, b=btn: _on_day_toggled(b))
        day_row.addWidget(btn)
        day_buttons[code] = btn
    layout.addLayout(day_row)

    layout.addWidget(_bold_label("Or repeats on a fixed schedule (optional)"))
    pattern_row = QHBoxLayout()
    for key, label in _PATTERN_LABELS.items():
        btn = QPushButton(label)
        btn.setCheckable(True)
        btn.setChecked(key == initial_pattern)
        btn.setProperty("class", "SecondaryButton")
        btn.clicked.connect(lambda checked=False, k=key: _on_pattern_toggled(k))
        pattern_row.addWidget(btn)
        pattern_buttons[key] = btn
    layout.addLayout(pattern_row)

    return day_buttons, pattern_buttons


class _AddTaskDialog(QWidget):
    def __init__(self, on_added):
        super().__init__(None, Qt.WindowStaysOnTopHint)
        self.setObjectName("PopupBg")
        self.setWindowTitle("Carmen Focus — Add Board Task")
        self.resize(440, 620)
        self._on_added = on_added
        self._photo_path = None
        self._importance = 5

        layout = QVBoxLayout(self)

        layout.addWidget(_bold_label("Name"))
        self._name_edit = WrapEdit()
        layout.addWidget(self._name_edit)

        layout.addWidget(_bold_label("Importance"))
        self._importance_row, self._importance_buttons = self._build_importance_picker()
        layout.addLayout(self._importance_row)

        self._day_buttons, self._pattern_buttons = _build_recurrence_pickers(layout)
        self._tag_buttons = _build_tag_picker(layout)

        layout.addWidget(_bold_label("Info (any mix of text, photo, link)"))
        self._text_edit = QTextEdit()
        self._text_edit.setPlaceholderText("Text (optional)")
        self._text_edit.setMaximumHeight(120)
        layout.addWidget(self._text_edit)

        photo_row = QHBoxLayout()
        choose_button = QPushButton("Choose Photo")
        choose_button.clicked.connect(self._choose_photo)
        photo_row.addWidget(choose_button)
        self._photo_preview = QLabel("No photo selected.")
        self._photo_preview.setAlignment(Qt.AlignCenter)
        self._photo_preview.setFixedHeight(120)
        photo_row.addWidget(self._photo_preview, 1)
        layout.addLayout(photo_row)

        self._link_edit = WrapEdit()
        self._link_edit.setPlaceholderText("Link (optional)")
        layout.addWidget(self._link_edit)

        self._status_label = QLabel()
        self._status_label.setStyleSheet("color: #c62828;")
        layout.addWidget(self._status_label)

        button_row = QHBoxLayout()
        button_row.addStretch(1)
        cancel_button = QPushButton("Cancel")
        cancel_button.clicked.connect(self.close)
        button_row.addWidget(cancel_button)
        finish_button = QPushButton("Finish")
        finish_button.setProperty("class", "AccentButton")
        finish_button.clicked.connect(self._submit)
        button_row.addWidget(finish_button)
        layout.addLayout(button_row)

        self.show()
        _register_popup(self)

    def _build_importance_picker(self):
        row = QHBoxLayout()
        row.setSpacing(4)
        buttons = {}
        for n in range(1, 11):
            btn = QPushButton(str(n))
            btn.setFixedSize(30, 30)
            btn.setCheckable(True)
            btn.setStyleSheet(_importance_button_style(n, 15))
            btn.clicked.connect(lambda checked=False, n=n: self._set_importance(n))
            row.addWidget(btn)
            buttons[n] = btn
        buttons[5].setChecked(True)
        return row, buttons

    def _set_importance(self, n):
        self._importance = n
        for value, btn in self._importance_buttons.items():
            btn.setChecked(value == n)

    def _choose_photo(self):
        path, _ = QFileDialog.getOpenFileName(self, "Choose Image", "", "Images (*.png *.jpg *.jpeg *.gif *.bmp)")
        if not path:
            return
        self._photo_path = path
        pixmap = QPixmap(path)
        if not pixmap.isNull():
            self._photo_preview.setPixmap(pixmap.scaled(200, 120, Qt.KeepAspectRatio, Qt.SmoothTransformation))
        else:
            self._photo_preview.setText("Could not preview this image.")

    def _submit(self):
        name = self._name_edit.text().strip()
        if not name:
            self._status_label.setText("Name is required.")
            return

        recurring_days = [code for code, btn in self._day_buttons.items() if btn.isChecked()]
        recurrence_pattern = next(
            (key for key, btn in self._pattern_buttons.items() if btn.isChecked()), None
        )

        photo_bytes = None
        photo_filename = None
        if self._photo_path:
            try:
                with open(self._photo_path, "rb") as f:
                    photo_bytes = f.read()
            except OSError:
                self._status_label.setText("Could not read the chosen photo -- choose it again.")
                return
            photo_filename = os.path.basename(self._photo_path)

        tags = [tag_id for tag_id, btn in self._tag_buttons.items() if btn.isChecked()]

        task = board_store.create_task(
            name,
            self._importance,
            recurring_days=recurring_days,
            recurrence_pattern=recurrence_pattern,
            description_text=self._text_edit.toPlainText().strip(),
            description_link=self._link_edit.text().strip(),
            photo_bytes=photo_bytes,
            photo_filename=photo_filename,
            tags=tags,
        )

        self.close()
        show_confetti()
        self._on_added(task)


class _EditTaskDialog(QWidget):
    """Opened via the details panel's "Edit Details" button. Edits name,
    recurrence, and info (text/photo/link) -- importance has its own
    dedicated editor on the board card's badge, so it's left alone here."""

    def __init__(self, task, on_saved):
        super().__init__(None, Qt.WindowStaysOnTopHint)
        self.setObjectName("PopupBg")
        self.setWindowTitle("Carmen Focus — Edit Board Task")
        self.resize(440, 620)
        self._task_id = task["id"]
        self._on_saved = on_saved
        self._photo_path = None
        self._remove_photo = False

        layout = QVBoxLayout(self)

        layout.addWidget(_bold_label("Name"))
        self._name_edit = WrapEdit(task.get("name") or "")
        layout.addWidget(self._name_edit)

        self._day_buttons, self._pattern_buttons = _build_recurrence_pickers(
            layout, initial_days=task.get("recurringDays"), initial_pattern=task.get("recurrencePattern")
        )
        self._tag_buttons = _build_tag_picker(layout, initial_tags=task.get("tags"))

        layout.addWidget(_bold_label("Info (any mix of text, photo, link)"))
        self._text_edit = QTextEdit()
        self._text_edit.setPlainText(task.get("descriptionText") or "")
        self._text_edit.setPlaceholderText("Text (optional)")
        self._text_edit.setMaximumHeight(120)
        layout.addWidget(self._text_edit)

        photo_row = QHBoxLayout()
        choose_button = QPushButton("Choose Photo")
        choose_button.clicked.connect(self._choose_photo)
        photo_row.addWidget(choose_button)
        self._photo_preview = QLabel()
        self._photo_preview.setAlignment(Qt.AlignCenter)
        self._photo_preview.setFixedHeight(120)
        self._has_existing_photo = bool(task.get("descriptionPhotoPath"))
        existing_path = task.get("descriptionPhotoPath")
        pixmap = QPixmap(existing_path) if existing_path and os.path.exists(existing_path) else None
        if pixmap and not pixmap.isNull():
            self._photo_preview.setPixmap(pixmap.scaled(200, 120, Qt.KeepAspectRatio, Qt.SmoothTransformation))
        else:
            self._photo_preview.setText("No photo selected.")
        photo_row.addWidget(self._photo_preview, 1)
        layout.addLayout(photo_row)

        if self._has_existing_photo:
            remove_photo_button = QPushButton("Remove Photo")
            remove_photo_button.setProperty("class", "SecondaryButton")
            remove_photo_button.clicked.connect(self._mark_remove_photo)
            layout.addWidget(remove_photo_button)

        self._link_edit = WrapEdit(task.get("descriptionLink") or "")
        self._link_edit.setPlaceholderText("Link (optional)")
        layout.addWidget(self._link_edit)

        self._status_label = QLabel()
        self._status_label.setStyleSheet("color: #c62828;")
        layout.addWidget(self._status_label)

        button_row = QHBoxLayout()
        button_row.addStretch(1)
        cancel_button = QPushButton("Cancel")
        cancel_button.clicked.connect(self.close)
        button_row.addWidget(cancel_button)
        save_button = QPushButton("Save Changes")
        save_button.setProperty("class", "AccentButton")
        save_button.clicked.connect(self._submit)
        button_row.addWidget(save_button)
        layout.addLayout(button_row)

        self.show()
        _register_popup(self)

    def _choose_photo(self):
        path, _ = QFileDialog.getOpenFileName(self, "Choose Image", "", "Images (*.png *.jpg *.jpeg *.gif *.bmp)")
        if not path:
            return
        self._photo_path = path
        self._remove_photo = False
        pixmap = QPixmap(path)
        if not pixmap.isNull():
            self._photo_preview.setPixmap(pixmap.scaled(200, 120, Qt.KeepAspectRatio, Qt.SmoothTransformation))
        else:
            self._photo_preview.setText("Could not preview this image.")

    def _mark_remove_photo(self):
        self._remove_photo = True
        self._photo_path = None
        self._photo_preview.setPixmap(QPixmap())
        self._photo_preview.setText("Photo will be removed.")

    def _submit(self):
        name = self._name_edit.text().strip()
        if not name:
            self._status_label.setText("Name is required.")
            return

        recurring_days = [code for code, btn in self._day_buttons.items() if btn.isChecked()]
        recurrence_pattern = next(
            (key for key, btn in self._pattern_buttons.items() if btn.isChecked()), None
        )

        photo_bytes = None
        photo_filename = None
        if self._photo_path:
            try:
                with open(self._photo_path, "rb") as f:
                    photo_bytes = f.read()
            except OSError:
                self._status_label.setText("Could not read the chosen photo -- choose it again.")
                return
            photo_filename = os.path.basename(self._photo_path)

        tags = [tag_id for tag_id, btn in self._tag_buttons.items() if btn.isChecked()]

        task = board_store.update_task(
            self._task_id,
            name,
            recurring_days=recurring_days,
            recurrence_pattern=recurrence_pattern,
            description_text=self._text_edit.toPlainText().strip(),
            description_link=self._link_edit.text().strip(),
            photo_bytes=photo_bytes,
            photo_filename=photo_filename,
            remove_photo=self._remove_photo,
            tags=tags,
        )

        self.close()
        self._on_saved(task)


_popup_refs = set()


def _register_popup(popup):
    _popup_refs.add(popup)
    popup.destroyed.connect(lambda: _popup_refs.discard(popup))
    return popup


def _bold_label(text):
    label = QLabel(text)
    label.setStyleSheet("font-weight: 700;")
    return label
