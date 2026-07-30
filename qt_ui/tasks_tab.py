"""Tasks tab: one "card" per recurring task (dashboard-card style, like the
Calendar/Finished month grid's day cells), each showing today's progress
toward its target minutes and its banked "vacation" time. Business logic
(scheduling, pause-aware worked-time, vacation balance) lives in
tasks_store.py -- this module is presentation + wiring a card's Start/Pause/
End buttons to session_manager, the same session engine the Focus panel
(qt_ui/finished_tab.py) and calendar events (calendar_scheduler.py) use, via
start_session(source="task", event_id=<task id>, event_title=<task name>).

Card states (per-card, not global):
  idle    -- shows today's progress + vacation bars. Clicking the card
             (anywhere except the gear icon) arms it.
  armed   -- the progress/vacation content is blurred; a Start Task overlay
             (duration field, "Until I burnout", Start/Cancel buttons) sits
             on top, unblurred, as *sibling* widgets rather than children of
             the blurred content -- QGraphicsBlurEffect blurs its whole
             widget subtree, so the trigger controls can't live inside it.
             Clicking the card background (or the Start button) starts the
             task; clicking into the duration field or Cancel does not,
             since Qt delivers the press to that child widget instead of
             bubbling it up to the card's own mousePressEvent.
  running -- shown when session_manager reports an active session whose
             source/eventId matches this task (polled on the shared status
             timer below, not stored as card state) -- countdown, Pause/
             Resume, End Task.

Only one session can run at a time app-wide (session_manager's model), so
any card other than the one actually running is dimmed and ignores clicks
while some session -- task or otherwise -- is active.
"""
from datetime import date

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QFont, QFontMetrics
from PySide6.QtWidgets import (
    QFrame,
    QGraphicsBlurEffect,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

import review_store
import session_history
import session_manager
import tasks_store
from qt_ui.task_editor import open_task_editor

STATUS_REFRESH_MS = 1000
CARD_WIDTH = 450
CARD_HEIGHT = 360
CARD_MARGIN = 22
CARDS_PER_ROW = 3
# Content width available to a full-width row inside the card, after the
# left/right card margins -- used to elide text to the pixel budget instead
# of letting Qt clip it mid-word or overflow past the card edge.
CARD_CONTENT_WIDTH = CARD_WIDTH - 2 * CARD_MARGIN


def _format_minutes(total_minutes):
    total_minutes = int(round(total_minutes))
    hours, minutes = divmod(total_minutes, 60)
    if hours:
        return f"{hours}h {minutes}m"
    return f"{minutes}m"


def _pastelize(hex_color, mix=0.22):
    """Blend a (possibly saturated) task color toward white so it reads as
    a soft pastel card fill. The un-blended color is still used for the
    progress bar chunk and the color-picker swatches, where full saturation
    is what makes it legible/identifiable -- only the large card background
    needs softening."""
    hex_color = hex_color.lstrip("#")
    if len(hex_color) != 6:
        return "#FFFFFF"
    r, g, b = (int(hex_color[i:i + 2], 16) for i in (0, 2, 4))
    r = round(r + (255 - r) * mix)
    g = round(g + (255 - g) * mix)
    b = round(b + (255 - b) * mix)
    return f"#{r:02X}{g:02X}{b:02X}"


def _subjects_for_task(task_id):
    try:
        subjects = []
        for topic in review_store.list_topics():
            for s in review_store.list_subjects(topic["id"]):
                if s.get("linkedTaskId") == task_id:
                    subjects.append(s)
        return subjects
    except Exception:
        return []


class TasksTab(QWidget):
    def __init__(self):
        super().__init__()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 22, 24, 20)
        layout.setSpacing(12)

        layout.addLayout(self._build_header())

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.NoFrame)
        scroll.setStyleSheet("background: #FFFFFF; border: none;")
        self._grid_container = QWidget()
        self._grid_container.setObjectName("TasksGridBg")
        self._grid = QGridLayout(self._grid_container)
        self._grid.setSpacing(16)
        self._grid.setAlignment(Qt.AlignLeft | Qt.AlignTop)
        scroll.setWidget(self._grid_container)
        layout.addWidget(scroll, 1)

        self._cards = {}
        self.refresh()

        self._status_timer = QTimer(self)
        self._status_timer.timeout.connect(self._tick)
        self._status_timer.start(STATUS_REFRESH_MS)

    def _build_header(self):
        header = QHBoxLayout()
        title = QLabel("Tasks")
        title.setStyleSheet("font-size: 20px; font-weight: 700;")
        header.addWidget(title)
        header.addStretch(1)
        add_button = QPushButton("+ Add Recurring Task")
        add_button.setProperty("class", "AccentButton")
        add_button.clicked.connect(lambda: open_task_editor(on_saved=lambda _t: self.refresh()))
        header.addWidget(add_button)
        return header

    def refresh(self):
        """Full rebuild -- called after a task is added/edited/deleted, or
        when this tab first opens. Cheaper per-tick updates (progress bars,
        countdown) go through _tick()/each card's update_dynamic() instead."""
        while self._grid.count():
            item = self._grid.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        self._cards = {}

        tasks = [t for t in tasks_store.load_tasks() if not t.get("archived")]
        if not tasks:
            empty_label = QLabel("No tasks yet -- click “+ Add Recurring Task” to create one.")
            empty_label.setStyleSheet("color: #8A8F98; font-size: 14px;")
            self._grid.addWidget(empty_label, 0, 0)
        for index, task in enumerate(tasks):
            row, col = divmod(index, CARDS_PER_ROW)
            card = _TaskCard(task, on_changed=self.refresh)
            self._grid.addWidget(card, row, col, Qt.AlignLeft | Qt.AlignTop)
            self._cards[task["id"]] = card

        # A divider that always sits directly under the last row of cards --
        # placed one grid row past whatever row the last card landed in, so
        # it shifts down on its own as more cards are added instead of
        # needing to be repositioned by hand.
        if tasks:
            last_row = -(-len(tasks) // CARDS_PER_ROW)  # ceil division
            divider = QFrame()
            divider.setFrameShape(QFrame.HLine)
            divider.setFixedHeight(1)
            divider.setStyleSheet("background: rgba(0,0,0,0.12); border: none;")
            self._grid.addWidget(divider, last_row, 0, 1, CARDS_PER_ROW)

        self._tick()

    def _tick(self):
        status = session_manager.get_status()
        sessions = session_history.load_all()
        for card in self._cards.values():
            card.update_dynamic(status, sessions)


class _TaskCard(QFrame):
    def __init__(self, task, on_changed):
        super().__init__()
        self._task = task
        self._on_changed = on_changed
        self._armed = False
        self._active_is_burnout = False
        self._duration_minutes_text = ""
        self._hovering = False
        self._cash_in_balance_int = 0
        self._logged_minutes_today = 0

        self.setProperty("class", "TaskCard")
        self.setFixedWidth(CARD_WIDTH)
        self.setMinimumHeight(CARD_HEIGHT)
        self.setCursor(Qt.PointingHandCursor)
        self.setAttribute(Qt.WA_StyledBackground, True)
        color = self._task.get("color", "#5B8DEF")
        self.setStyleSheet(
            f"QFrame.TaskCard {{ background: {color}; border: 1px solid rgba(0,0,0,0.12); "
            f"border-radius: 12px; }} "
            f"QFrame.TaskCard QWidget {{ background: transparent; }}"
        )

        outer = QVBoxLayout(self)
        outer.setContentsMargins(CARD_MARGIN, 20, CARD_MARGIN, 20)
        outer.setSpacing(6)

        outer.addLayout(self._build_header_row())
        outer.addWidget(self._build_description_section())

        # Everything that gets blurred while armed lives in this one
        # sub-widget -- see module docstring for why the trigger controls
        # must NOT be inside it.
        self._content = QWidget()
        content_layout = QVBoxLayout(self._content)
        content_layout.setContentsMargins(0, 0, 0, 0)
        content_layout.setSpacing(10)
        content_layout.addLayout(self._build_subjects_row())
        content_layout.addLayout(self._build_progress_section())
        content_layout.addLayout(self._build_vacation_section())
        outer.addWidget(self._content)

        self._armed_overlay = self._build_armed_overlay()
        outer.addWidget(self._armed_overlay)
        self._armed_overlay.setVisible(False)

        self._running_panel = self._build_running_panel()
        outer.addWidget(self._running_panel)
        self._running_panel.setVisible(False)

        self._blur = QGraphicsBlurEffect(self._content)
        self._blur.setBlurRadius(0)
        self._content.setGraphicsEffect(self._blur)

    # --- static sections ---

    def _build_header_row(self):
        row = QHBoxLayout()
        name_label = QLabel()
        name_label.setStyleSheet("font-size: 30px; font-weight: 700; color: #1F2328;")
        full_name = self._task["name"]
        metrics = QFontMetrics(name_label.font())
        # Elided to one line (rather than word-wrapped) so every idle card
        # has the same header height -- long names no longer stretch a
        # card's overall size relative to its neighbors in the grid.
        available_width = CARD_CONTENT_WIDTH - 26 - 6  # gear button + spacing
        name_label.setText(metrics.elidedText(full_name, Qt.ElideRight, available_width))
        if name_label.text() != full_name:
            name_label.setToolTip(full_name)
        row.addWidget(name_label, 1)

        # Hidden until the card is hovered (see enterEvent/leaveEvent below)
        # so the idle card reads as a clean, decluttered tile.
        self._gear_button = QPushButton("⚙")
        self._gear_button.setFixedSize(26, 26)
        self._gear_button.setProperty("class", "SecondaryButton")
        self._gear_button.setStyleSheet("font-size: 14px; padding: 0;")
        # "Segoe UI" (this app's base font, see styles.qss) has no glyph for
        # U+2699 GEAR -- it rendered as an empty tofu box. Segoe UI Symbol
        # covers the Miscellaneous Symbols block and is present on every
        # Windows version this app targets.
        gear_font = QFont(self._gear_button.font())
        gear_font.setFamilies(["Segoe UI Symbol", "Segoe UI Emoji", gear_font.family()])
        self._gear_button.setFont(gear_font)
        self._gear_button.setToolTip("Edit task")
        self._gear_button.clicked.connect(self._open_editor)
        row.addWidget(self._gear_button)
        return row

    def enterEvent(self, event):
        self._hovering = True
        self._refresh_cash_in_visibility()
        super().enterEvent(event)

    def leaveEvent(self, event):
        self._hovering = False
        self._refresh_cash_in_visibility()
        super().leaveEvent(event)

    def _build_subjects_row(self):
        row = QHBoxLayout()
        row.setSpacing(6)
        subjects = _subjects_for_task(self._task["id"])
        for subject in subjects:
            pill = QLabel(subject["name"])
            pill.setStyleSheet(
                f"background: {subject['color']}; color: white; "
                f"border-radius: 10px; padding: 3px 10px; font-size: 11px; font-weight: 600;"
            )
            row.addWidget(pill)
        row.addStretch(1)
        return row

    def _build_description_section(self):
        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(3)

        self._lock_label = QLabel()
        self._lock_label.setStyleSheet("font-size: 36px; font-weight: 600; color: #262A32;")
        layout.addWidget(self._lock_label)

        self._whitelist_button = QPushButton("(see whitelist)")
        self._whitelist_button.setFlat(True)
        self._whitelist_button.setCursor(Qt.PointingHandCursor)
        self._whitelist_button.setStyleSheet(
            "color: #3A3F48; font-size: 36px; text-align: left; border: none; "
            "background: transparent; padding: 0;"
        )
        self._whitelist_button.clicked.connect(self._open_whitelist_viewer)
        layout.addWidget(self._whitelist_button)

        self._refresh_description()
        return container

    def _whitelist_items(self):
        return list(self._task.get("processWhitelist", [])) + list(self._task.get("domainWhitelist", []))

    def _refresh_description(self):
        self._lock_label.setText("Hard Lock" if self._task.get("lockMode") == "hard" else "Soft Lock")

    def _open_whitelist_viewer(self):
        from PySide6.QtWidgets import QDialog, QPlainTextEdit
        dlg = QDialog(self.window())
        dlg.setWindowTitle(f"{self._task['name']} — Whitelist")
        dlg.setObjectName("PopupBg")
        dlg.setMinimumWidth(340)
        lay = QVBoxLayout(dlg)
        lay.setContentsMargins(20, 16, 20, 16)
        lay.setSpacing(8)

        proc_lbl = QLabel("Allowed apps:")
        proc_lbl.setStyleSheet("font-weight: 600; font-size: 13px; color: #1F2328;")
        lay.addWidget(proc_lbl)
        proc_view = QPlainTextEdit()
        proc_view.setPlainText("\n".join(self._task.get("processWhitelist", [])) or "(none)")
        proc_view.setFixedHeight(100)
        proc_view.setReadOnly(True)
        lay.addWidget(proc_view)

        dom_lbl = QLabel("Allowed domains:")
        dom_lbl.setStyleSheet("font-weight: 600; font-size: 13px; color: #1F2328;")
        lay.addWidget(dom_lbl)
        dom_view = QPlainTextEdit()
        dom_view.setPlainText("\n".join(self._task.get("domainWhitelist", [])) or "(none)")
        dom_view.setFixedHeight(80)
        dom_view.setReadOnly(True)
        lay.addWidget(dom_view)

        close_btn = QPushButton("Close")
        close_btn.clicked.connect(dlg.accept)
        lay.addWidget(close_btn)

        dlg.exec()

    def _build_progress_section(self):
        col = QVBoxLayout()
        col.setSpacing(3)
        self._progress_bar = QProgressBar()
        self._progress_bar.setRange(0, 100)
        self._progress_bar.setTextVisible(False)
        self._progress_bar.setFixedHeight(14)
        self._progress_bar.setProperty("class", "TaskProgressBar")
        self._progress_bar.setStyleSheet(
            "QProgressBar.TaskProgressBar { background: rgba(0,0,0,0.18); "
            "border: 1px solid rgba(0,0,0,0.25); border-radius: 6px; } "
            "QProgressBar.TaskProgressBar::chunk { background: rgba(255,255,255,0.85); "
            "border-radius: 5px; }"
        )
        col.addWidget(self._progress_bar)
        below_row = QHBoxLayout()
        below_row.addStretch(1)
        self._progress_label = QLabel()
        self._progress_label.setStyleSheet("font-size: 17px; color: rgba(0,0,0,0.55);")
        below_row.addWidget(self._progress_label)
        col.addLayout(below_row)
        return col

    def _build_vacation_section(self):
        col = QVBoxLayout()
        col.setSpacing(4)

        # The balance text itself is the cash-in trigger (clickable, like
        # _whitelist_button's "(see whitelist)") rather than a separate
        # hover-only button -- always visible so there's no discoverability
        # gap, and disabled (no pointer cursor, no click) when there's
        # nothing banked to cash in.
        self._vacation_label = QPushButton()
        self._vacation_label.setFlat(True)
        self._vacation_label.setCursor(Qt.PointingHandCursor)
        self._vacation_label.setStyleSheet(
            "QPushButton { font-size: 18px; font-weight: 400; color: #5A6070; "
            "text-align: left; border: none; background: transparent; padding: 0; }"
            "QPushButton:hover:enabled { color: #1F2328; text-decoration: underline; }"
        )
        self._vacation_label.clicked.connect(self._open_cash_in_editor)
        col.addWidget(self._vacation_label)
        self._vacation_label_budget = CARD_CONTENT_WIDTH

        self._cash_in_row = QWidget()
        cash_layout = QHBoxLayout(self._cash_in_row)
        cash_layout.setContentsMargins(0, 0, 0, 0)
        cash_layout.setSpacing(6)
        self._cash_in_edit = QLineEdit()
        self._cash_in_edit.setFixedWidth(56)
        self._cash_in_edit.setPlaceholderText("0")
        self._cash_in_edit.setStyleSheet(
            "font-size: 13px; color: #1F2328; background: #FFFFFF; "
            "border: 1px solid rgba(0,0,0,0.2); border-radius: 6px; padding: 3px 6px;"
        )
        self._cash_in_edit.returnPressed.connect(self._confirm_cash_in)
        cash_layout.addWidget(self._cash_in_edit)
        self._cash_in_max_label = QLabel("/ 0")
        self._cash_in_max_label.setStyleSheet("font-size: 13px; color: #1F2328;")
        cash_layout.addWidget(self._cash_in_max_label)
        confirm_button = QPushButton("✓")
        confirm_button.setFixedSize(26, 26)
        confirm_button.setProperty("class", "SecondaryButton")
        confirm_button.setStyleSheet("font-size: 13px; padding: 0;")
        confirm_button.clicked.connect(self._confirm_cash_in)
        cash_layout.addWidget(confirm_button)
        cancel_button = QPushButton("✕")
        cancel_button.setFixedSize(26, 26)
        cancel_button.setProperty("class", "SecondaryButton")
        cancel_button.setStyleSheet("font-size: 13px; padding: 0;")
        cancel_button.clicked.connect(self._close_cash_in_editor)
        cash_layout.addWidget(cancel_button)
        cash_layout.addStretch(1)
        self._cash_in_row.setVisible(False)
        col.addWidget(self._cash_in_row)

        return col

    def _build_armed_overlay(self):
        overlay = QWidget()
        layout = QVBoxLayout(overlay)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)

        duration_row = QHBoxLayout()
        self._duration_edit = QLineEdit()
        self._duration_edit.setPlaceholderText("minutes")
        self._duration_edit.setStyleSheet(
            "font-size: 13px; color: #1F2328; background: #FFFFFF; "
            "border: 1px solid rgba(0,0,0,0.15); border-radius: 6px; padding: 4px 6px;"
        )
        duration_row.addWidget(self._duration_edit)
        self._burnout_button = QPushButton("Until I burnout")
        self._burnout_button.setObjectName("burnoutButton")
        self._burnout_button.setStyleSheet("font-size: 13px;")
        self._burnout_button.clicked.connect(self._start_burnout)
        duration_row.addWidget(self._burnout_button)
        layout.addLayout(duration_row)

        button_row = QHBoxLayout()
        cancel_button = QPushButton("Cancel")
        cancel_button.setProperty("class", "SecondaryButton")
        cancel_button.setStyleSheet("font-size: 13px;")
        cancel_button.clicked.connect(self._disarm)
        button_row.addWidget(cancel_button)
        start_button = QPushButton("Start Task")
        start_button.setObjectName("startTaskButton")
        start_button.setStyleSheet("font-size: 13px;")
        start_button.clicked.connect(self._start_task)
        button_row.addWidget(start_button)
        layout.addLayout(button_row)

        return overlay

    def _build_running_panel(self):
        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)

        self._countdown_label = QLabel()
        self._countdown_label.setStyleSheet("font-size: 24px; font-weight: 700; color: #1F2328;")
        layout.addWidget(self._countdown_label)

        _running_btn_style = (
            "background: white; color: #1F2328; "
            "border: 2px solid black; border-radius: 6px; "
            "font-size: 26px; padding: 4px 14px;"
        )
        button_row = QHBoxLayout()
        self._pause_button = QPushButton("Pause")
        self._pause_button.setStyleSheet(_running_btn_style)
        self._pause_button.clicked.connect(self._pause_resume)
        button_row.addWidget(self._pause_button)
        end_button = QPushButton("End Task")
        end_button.setStyleSheet(_running_btn_style)
        end_button.clicked.connect(self._end_task)
        button_row.addWidget(end_button)
        layout.addLayout(button_row)

        return panel

    # --- state transitions ---

    def mousePressEvent(self, event):
        # Only reachable for clicks that landed on the card itself or a
        # non-interactive child (labels, bars) -- QLineEdit/QPushButton
        # children consume their own press events and never bubble here.
        if self._is_locked_by_other_session():
            super().mousePressEvent(event)
            return
        if not self._armed and not self._running_panel.isVisible():
            self._arm()
        elif self._armed:
            self._start_task()
        super().mousePressEvent(event)

    def _arm(self):
        self._armed = True
        self._blur.setBlurRadius(6)
        self._armed_overlay.setVisible(True)
        self._duration_minutes_text = str(self._today_required_minutes())
        self._duration_edit.setText(self._duration_minutes_text)

    def _disarm(self):
        self._armed = False
        self._blur.setBlurRadius(0)
        self._armed_overlay.setVisible(False)

    def _open_editor(self):
        open_task_editor(self._task, on_saved=lambda _t: self._on_changed())

    def _start_task(self):
        try:
            duration_minutes = float(self._duration_edit.text())
            if duration_minutes <= 0:
                raise ValueError
        except ValueError:
            return
        self._active_is_burnout = False
        self._begin_session(duration_minutes)

    def _start_burnout(self):
        self._active_is_burnout = True
        self._begin_session(tasks_store.BURNOUT_MINUTES)

    def _begin_session(self, duration_minutes):
        session_manager.start_session(
            duration_minutes,
            self._task.get("lockMode", "soft"),
            self._task.get("processWhitelist", []),
            self._task.get("domainWhitelist", []),
            source="task",
            event_id=self._task["id"],
            event_title=self._task["name"],
        )
        self._disarm()

    def _pause_resume(self):
        if session_manager.get_status()["isPaused"]:
            session_manager.resume_session()
        else:
            session_manager.pause_session()

    def _end_task(self):
        session_manager.end_session(end_type="manual")

    def _cash_in_max(self):
        # Capped at how much is actually left to fill the bar today --
        # today's required minutes (already reduced by whatever's been
        # cashed in so far) minus whatever's genuinely been worked, not the
        # whole banked balance. Cashing in more than that would just burn
        # vacation minutes for no visible effect.
        remaining = max(0, int(self._today_required_minutes()) - int(self._logged_minutes_today))
        return min(self._cash_in_balance_int, remaining)

    def _open_cash_in_editor(self):
        if self._cash_in_max() <= 0:
            return
        self._cash_in_edit.setText("")
        self._cash_in_max_label.setText(f"/ {self._cash_in_max()}")
        self._cash_in_row.setVisible(True)
        self._cash_in_edit.setFocus()

    def _close_cash_in_editor(self):
        self._cash_in_row.setVisible(False)

    def _confirm_cash_in(self):
        try:
            minutes = int(self._cash_in_edit.text())
        except ValueError:
            return
        cap = self._cash_in_max()
        if minutes <= 0 or minutes > cap:
            QMessageBox.warning(
                self, "Carmen Focus",
                f"Enter a whole number of minutes between 1 and {cap} (today's remaining time).",
            )
            return
        sessions = session_history.load_all()
        try:
            self._task = tasks_store.cash_in(self._task["id"], date.today(), minutes, sessions)
        except ValueError as exc:
            QMessageBox.warning(self, "Carmen Focus", str(exc))
            return
        self._close_cash_in_editor()
        self._on_changed()

    def _refresh_cash_in_visibility(self):
        can_cash_in = self._cash_in_max() > 0 and not self._is_running()
        self._vacation_label.setEnabled(can_cash_in)
        self._vacation_label.setCursor(Qt.PointingHandCursor if can_cash_in else Qt.ArrowCursor)

    # --- dynamic refresh (called every tick by TasksTab) ---

    def _today_required_minutes(self):
        return tasks_store.required_minutes_for_date(self._task, date.today())

    def _is_locked_by_other_session(self):
        status = session_manager.get_status()
        if not status["isActive"]:
            return False
        return not (status.get("source") == "task" and status.get("eventId") == self._task["id"])

    def update_dynamic(self, status, sessions):
        today = date.today()
        required = self._today_required_minutes()
        logged_seconds = tasks_store.logged_seconds_for_date(self._task, today, sessions, live_status=status)
        logged_minutes = logged_seconds / 60
        self._logged_minutes_today = logged_minutes

        # The bar/label count today's cashed-in vacation minutes as if they
        # were worked, against the *original* (un-reduced) target -- so
        # cashing in visibly fills the bar instead of just shrinking the
        # goal underneath an unchanged 0/X display. required_minutes_for_date
        # already subtracts today's cash-ins from the target, so adding them
        # back on both sides recovers the original target as the denominator.
        today_cashed = (self._task.get("cashedInDates") or {}).get(today.isoformat(), 0)
        display_required = required + today_cashed
        display_logged = logged_minutes + today_cashed

        pct = 100 if display_required <= 0 else min(100, int(display_logged / display_required * 100))
        self._progress_bar.setValue(pct if (display_required > 0 or display_logged > 0) else 0)
        if display_required <= 0:
            self._progress_label.setText(f"{_format_minutes(display_logged)} logged")
        else:
            self._progress_label.setText(f"{_format_minutes(display_logged)} of {_format_minutes(display_required)}")

        balance = tasks_store.vacation_balance_minutes(self._task, sessions)
        # Round rather than truncate -- a 0.97m balance reads as "1m banked"
        # to the user (that's what _format_minutes below shows them), so the
        # cashable amount must round the same way or the displayed number
        # and the actually-clickable amount disagree (looked like the button
        # "didn't work" when it truncated to 0 while showing "1m").
        self._cash_in_balance_int = int(round(balance))
        # Today's surplus (logged beyond today's required) isn't cashable yet
        # (the day isn't over), but show it so the user can see they're earning.
        today_surplus = max(0.0, logged_minutes - required) if required > 0 else 0.0
        display_balance = balance + today_surplus
        vacation_text = f"{_format_minutes(display_balance)} vacation banked"
        metrics = QFontMetrics(self._vacation_label.font())
        elided = metrics.elidedText(vacation_text, Qt.ElideRight, max(self._vacation_label_budget, 0))
        self._vacation_label.setText(elided)
        self._vacation_label.setToolTip(vacation_text if elided != vacation_text else "")

        is_running = status.get("isActive") and status.get("source") == "task" and status.get("eventId") == self._task["id"]
        locked_by_other = self._is_locked_by_other_session()
        self._refresh_cash_in_visibility()

        if is_running:
            if self._armed:
                self._disarm()
            self._close_cash_in_editor()
            self._content.setVisible(False)
            self._armed_overlay.setVisible(False)
            self._running_panel.setVisible(True)
            paused = " (paused)" if status.get("isPaused") else ""
            violations = status.get("violationCount", 0)
            violation_text = f"  •  {violations} violation{'s' if violations != 1 else ''}" if violations else ""
            # Elapsed, not remaining, for both burnout and fixed-duration
            # sessions -- computed pause-aware from startTime/violationLog
            # (same math as the day's logged-minutes tally) rather than from
            # secondsRemaining against a duration this card would otherwise
            # have to remember, which also makes it correct even for a card
            # that's rebuilt mid-session (e.g. after the app restarts).
            elapsed_seconds = tasks_store.worked_seconds(
                status.get("startTime"), None, status.get("violationLog")
            ) if status.get("startTime") else 0
            el_minutes, el_seconds = divmod(elapsed_seconds, 60)
            if self._active_is_burnout:
                self._countdown_label.setText(
                    f"UNTIL BURNOUT - elapsed {el_minutes}m {el_seconds}s{paused}{violation_text}"
                )
            else:
                self._countdown_label.setText(f"{el_minutes}m {el_seconds}s elapsed{paused}{violation_text}")
            self._pause_button.setText("Resume" if status.get("isPaused") else "Pause")
        else:
            self._running_panel.setVisible(False)
            self._content.setVisible(True)
            if self._armed and locked_by_other:
                self._disarm()

        self.setProperty("locked", locked_by_other)
        self.style().unpolish(self)
        self.style().polish(self)

    def _is_running(self):
        status = session_manager.get_status()
        return status.get("isActive") and status.get("source") == "task" and status.get("eventId") == self._task["id"]
