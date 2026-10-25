"""Focus tab: start an a-la-carte focus session, see what is running now, and every
session waiting behind it.

  Start a session -- a length (15 / 25 / 45 / 60 or any number), soft or hard lock, and
                     the saved blocklist. Always available: if something is already
                     running it is paused and waits (picker_dialogs.start_manual_session),
                     it is never silently replaced.
  Now             -- the running session: name, a big clock, pause / rules / end.
  Waiting         -- every paused session, however many, each with its own
                     "Switch to this" and "End".

Tasks started from the Tasks tab, reviews and sessions started here all show up in the
same two lists, since they are all just sessions to session_manager.
"""
from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QButtonGroup,
    QFrame,
    QGraphicsDropShadowEffect,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)
from PySide6.QtGui import QColor

import config
import picker_gui
import session_manager
import tasks_store
import qt_ui.nuclear_dialog as nuclear_dialog
import qt_ui.picker_dialogs as picker_dialogs
from qt_ui.ui_kit import (
    AMBER,
    GREEN,
    INK,
    MUTED,
    NUM_FONTS,
    SECONDARY_STYLE,
    TEAL,
    DANGER_STYLE,
    ElidedLabel,
    button,
    format_clock,
    primary_style,
    safe_color,
)

STATUS_REFRESH_MS = 1000
PRESET_MINUTES = (15, 25, 45, 60)
DEFAULT_COLOR = "#5B8DEF"

_PILL_STYLE = (
    f"QPushButton{{background:#FFFFFF;color:{INK};border:1px solid #E1E5EC;border-radius:12px;"
    "font-size:13.5px;font-weight:600;padding:0 14px;}"
    "QPushButton:hover{background:#F6F7FA;}"
    f"QPushButton:checked{{background:{INK};color:#FFFFFF;border:1px solid {INK};}}")


def _card():
    frame = QFrame()
    frame.setObjectName("FocusCard")
    frame.setStyleSheet("QFrame#FocusCard{background:#FFFFFF;border:1px solid #E6E9EF;border-radius:18px;}")
    shadow = QGraphicsDropShadowEffect(frame)
    shadow.setBlurRadius(28)
    shadow.setOffset(0, 6)
    shadow.setColor(QColor(16, 24, 40, 22))
    frame.setGraphicsEffect(shadow)
    return frame


def _section_title(text):
    label = QLabel(text)
    label.setStyleSheet(f"font-size:13px;font-weight:700;color:{MUTED};background:transparent;")
    return label


def _session_name(session):
    """What to call a session in a list."""
    if session.get("reviewProblemName"):
        first = session.get("isFirstAttempt") or session["reviewProblemName"] == "First attempt"
        return "First attempt" if first else f"Review: {session['reviewProblemName']}"
    return session.get("eventTitle") or "Focus session"


def _session_kind(session):
    source = session.get("source")
    if session.get("reviewProblemName"):
        return "Review"
    if source == "task":
        return "Task"
    if source == "calendar-event":
        return "Calendar event"
    return "Focus session" if source in (None, "manual") else str(source).replace("-", " ").title()


def _session_color(session):
    task = tasks_store.get_task(session.get("eventId")) if session.get("eventId") else None
    return safe_color(task.get("color")) if task else DEFAULT_COLOR


def _elapsed_seconds(session):
    if not session.get("startTime"):
        return 0
    return tasks_store.worked_seconds(session["startTime"], None, session.get("violationLog"))


def _is_stopwatch(session):
    # A review and "until burnout" have no fixed length to count down to.
    return bool(session.get("reviewProblemName") or session.get("isBurnout"))


def _clock_for(session):
    """(big clock text, caption) -- the time left, or the time elapsed for a stopwatch."""
    if _is_stopwatch(session):
        return format_clock(_elapsed_seconds(session)), "elapsed"
    return format_clock(session.get("secondsRemaining", 0)), "remaining"


class _WaitingRow(QFrame):
    """One paused session waiting its turn: what it is, how much time it holds, and the
    two things you can do with it."""

    def __init__(self, session):
        super().__init__()
        self.park_id = session.get("parkId")
        self.setObjectName("WaitingRow")
        self.setStyleSheet(
            "QFrame#WaitingRow{background:#FFFFFF;border:1px dashed #C9CED8;border-radius:14px;}")
        row = QHBoxLayout(self)
        row.setContentsMargins(16, 12, 12, 12)
        row.setSpacing(12)
        self._dot = QLabel()
        self._dot.setFixedSize(10, 10)
        row.addWidget(self._dot, 0, Qt.AlignVCenter)
        text = QVBoxLayout()
        text.setSpacing(1)
        self.name_label = ElidedLabel("")
        self.name_label.setStyleSheet(f"font-size:14.5px;font-weight:700;color:{INK};background:transparent;")
        text.addWidget(self.name_label)
        self.detail_label = ElidedLabel("")
        self.detail_label.setStyleSheet(f"font-size:12.5px;color:{MUTED};background:transparent;")
        text.addWidget(self.detail_label)
        row.addLayout(text, 1)
        self.switch_button = button("Switch to this", SECONDARY_STYLE, height=34)
        self.end_button = button("End", DANGER_STYLE, height=34)
        row.addWidget(self.switch_button)
        row.addWidget(self.end_button)
        self.switch_button.clicked.connect(lambda: session_manager.swap_with_parked(self.park_id))
        self.end_button.clicked.connect(
            lambda: session_manager.end_parked_session(park_id=self.park_id, end_type="manual"))
        self.update_from(session)

    def update_from(self, session):
        color = _session_color(session)
        self._dot.setStyleSheet(f"background:{color};border-radius:5px;")
        self.name_label.setText(_session_name(session))
        big, caption = _clock_for(session)
        detail = f"{big} {caption} · {_session_kind(session)}"
        if session.get("pomodoro"):
            pomo = session["pomodoro"]
            detail += f" · pomodoro {pomo['currentCycle']}/{pomo['totalCycles']}"
        self.detail_label.setText(detail)


class FocusTab(QWidget):
    def __init__(self):
        super().__init__()
        self._parked_note = ""
        self._waiting_rows = {}
        self._start_note_text = ""

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setStyleSheet("QScrollArea{background:transparent;}")
        outer.addWidget(scroll)

        page = QWidget()
        page.setObjectName("FocusPage")
        page.setStyleSheet("QWidget#FocusPage{background:transparent;}")
        scroll.setWidget(page)
        layout = QVBoxLayout(page)
        layout.setContentsMargins(28, 24, 28, 28)
        layout.setSpacing(16)

        head = QHBoxLayout()
        title = QLabel("Focus")
        title.setStyleSheet(f"font-size:26px;font-weight:700;color:{INK};background:transparent;")
        head.addWidget(title)
        head.addStretch(1)
        self._summary_label = QLabel()
        self._summary_label.setStyleSheet(f"font-size:13px;color:{MUTED};background:transparent;")
        head.addWidget(self._summary_label)
        layout.addLayout(head)

        layout.addWidget(self._build_start_card())
        layout.addWidget(self._build_now_card())

        self._waiting_title = _section_title("Waiting")
        layout.addWidget(self._waiting_title)
        self._waiting_box = QVBoxLayout()
        self._waiting_box.setSpacing(10)
        layout.addLayout(self._waiting_box)
        layout.addStretch(1)

        # Tracks isActive across ticks.
        self._was_active = session_manager.get_status()["isActive"]

        self._status_timer = QTimer(self)
        self._status_timer.timeout.connect(self._refresh_status)
        self._status_timer.start(STATUS_REFRESH_MS)
        self._refresh_status()

    # --- start card ---

    def _build_start_card(self):
        card = _card()
        box = QVBoxLayout(card)
        box.setContentsMargins(22, 20, 22, 20)
        box.setSpacing(14)
        box.addWidget(_section_title("Start a session"))

        cfg = config.load_config()
        last_minutes = cfg.get("last_duration_minutes", 25)

        length = QHBoxLayout()
        length.setSpacing(8)
        self._preset_buttons = {}
        self._preset_group = QButtonGroup(self)
        self._preset_group.setExclusive(True)
        for minutes in PRESET_MINUTES:
            pill = QPushButton(f"{minutes} min")
            pill.setCheckable(True)
            pill.setCursor(Qt.PointingHandCursor)
            pill.setFixedHeight(38)
            pill.setStyleSheet(_PILL_STYLE)
            pill.clicked.connect(lambda _checked=False, m=minutes: self._pick_preset(m))
            self._preset_group.addButton(pill)
            self._preset_buttons[minutes] = pill
            length.addWidget(pill)
        self._custom_edit = QLineEdit()
        self._custom_edit.setPlaceholderText("other")
        self._custom_edit.setAlignment(Qt.AlignCenter)
        self._custom_edit.setFixedSize(76, 38)
        self._custom_edit.setStyleSheet(
            f"QLineEdit{{border:1px solid #E1E5EC;border-radius:12px;font-size:13.5px;font-weight:600;"
            f"color:{INK};background:#FFFFFF;}}QLineEdit:focus{{border:1.5px solid {INK};}}")
        self._custom_edit.textEdited.connect(self._custom_typed)
        self._custom_edit.returnPressed.connect(self._start_clicked)
        length.addWidget(self._custom_edit)
        length.addWidget(QLabel("min"))
        length.addStretch(1)
        box.addLayout(length)

        lock = QHBoxLayout()
        lock.setSpacing(8)
        self._soft_button = QPushButton("Soft lock")
        self._hard_button = QPushButton("Hard lock")
        lock_group = QButtonGroup(self)
        lock_group.setExclusive(True)
        for pill in (self._soft_button, self._hard_button):
            pill.setCheckable(True)
            pill.setCursor(Qt.PointingHandCursor)
            pill.setFixedHeight(34)
            pill.setStyleSheet(_PILL_STYLE)
            lock_group.addButton(pill)
            lock.addWidget(pill)
        (self._hard_button if cfg.get("last_lock_mode", "soft") == "hard" else self._soft_button).setChecked(True)
        lock.addSpacing(8)
        self._rules_summary = QLabel()
        self._rules_summary.setStyleSheet(f"font-size:12.5px;color:{MUTED};background:transparent;")
        lock.addWidget(self._rules_summary, 1)
        blocklist_button = button("Pick apps", SECONDARY_STYLE, height=34)
        blocklist_button.clicked.connect(picker_gui.open_blocklist_picker)
        lock.addWidget(blocklist_button)
        box.addLayout(lock)

        go = QHBoxLayout()
        go.setSpacing(14)
        self._start_button = button("Start session", primary_style(INK), height=44)
        self._start_button.setMinimumWidth(170)
        self._start_button.clicked.connect(self._start_clicked)
        go.addWidget(self._start_button)
        self._start_note = QLabel()
        self._start_note.setWordWrap(True)
        self._start_note.setStyleSheet(f"font-size:12.5px;color:{MUTED};background:transparent;")
        go.addWidget(self._start_note, 1)
        box.addLayout(go)

        self._start_error = QLabel()
        self._start_error.setStyleSheet("font-size:12.5px;color:#B42318;background:transparent;")
        self._start_error.hide()
        box.addWidget(self._start_error)

        self._duration_minutes = last_minutes if last_minutes in PRESET_MINUTES else 25
        if last_minutes in PRESET_MINUTES:
            self._preset_buttons[last_minutes].setChecked(True)
        else:
            self._custom_edit.setText(f"{last_minutes:g}" if isinstance(last_minutes, float) else str(last_minutes))
            self._duration_minutes = None
        self._update_rules_summary(cfg)
        return card

    def _pick_preset(self, minutes):
        self._duration_minutes = minutes
        self._custom_edit.clear()
        self._start_error.hide()

    def _custom_typed(self, _text):
        # Typing a length replaces the preset choice.
        self._duration_minutes = None
        self._preset_group.setExclusive(False)
        for pill in self._preset_buttons.values():
            pill.setChecked(False)
        self._preset_group.setExclusive(True)
        self._start_error.hide()

    def _chosen_minutes(self):
        text = self._custom_edit.text().strip()
        if text:
            try:
                return float(text)
            except ValueError:
                return None
        return self._duration_minutes

    def _update_rules_summary(self, cfg=None):
        cfg = cfg or config.load_config()
        apps = len(cfg.get("processBlocklist", []))
        sites = len(cfg.get("domainWhitelist", []))
        profiles = len(cfg.get("browserProfileBlocklist", []))
        text = f"{apps} app{'s' if apps != 1 else ''} blocked · {sites} site{'s' if sites != 1 else ''} ok"
        if profiles:
            text += f" · {profiles} browser profile{'s' if profiles != 1 else ''}"
        self._rules_summary.setText(text)

    def _start_clicked(self):
        minutes = self._chosen_minutes()
        if minutes is None:
            self._show_start_error("Pick a length, or type a number of minutes.")
            return
        lock_mode = "hard" if self._hard_button.isChecked() else "soft"
        error = picker_dialogs.start_manual_session(minutes, lock_mode, parent=self)
        if error:
            self._show_start_error(error)
            return
        self._start_error.hide()
        self._refresh_status()

    def _show_start_error(self, text):
        self._start_error.setText(text)
        self._start_error.show()

    # --- now card ---

    def _build_now_card(self):
        self._now_card = _card()
        box = QVBoxLayout(self._now_card)
        box.setContentsMargins(22, 20, 22, 20)
        box.setSpacing(10)

        top = QHBoxLayout()
        top.setSpacing(10)
        self._now_dot = QLabel()
        self._now_dot.setFixedSize(12, 12)
        top.addWidget(self._now_dot, 0, Qt.AlignVCenter)
        self._now_name = ElidedLabel("")
        self._now_name.setStyleSheet(f"font-size:17px;font-weight:700;color:{INK};background:transparent;")
        top.addWidget(self._now_name, 1)
        self._now_chip = QLabel()
        top.addWidget(self._now_chip)
        box.addLayout(top)

        clock_row = QHBoxLayout()
        clock_row.setSpacing(12)
        self._clock_label = QLabel("")
        big = QFont()
        big.setFamilies(NUM_FONTS)
        big.setPixelSize(52)
        self._clock_label.setFont(big)
        self._clock_label.setStyleSheet(f"color:{INK};background:transparent;")
        clock_row.addWidget(self._clock_label, 0, Qt.AlignBottom)
        self._clock_caption = QLabel("")
        self._clock_caption.setStyleSheet(f"font-size:13px;color:{MUTED};background:transparent;")
        clock_row.addWidget(self._clock_caption, 0, Qt.AlignBottom)
        clock_row.addStretch(1)
        box.addLayout(clock_row)

        self._status_label = QLabel()
        self._status_label.setWordWrap(True)
        self._status_label.setStyleSheet(f"font-size:13px;color:{MUTED};background:transparent;")
        box.addWidget(self._status_label)

        buttons = QHBoxLayout()
        buttons.setSpacing(8)
        # Pause/Resume and Nuclear End only make sense while a session is
        # actually running; _refresh_status re-evaluates this every tick so
        # these disappear on their own once a session ends.
        self._pause_button = button("Pause", SECONDARY_STYLE, height=38)
        self._pause_button.clicked.connect(self._pause_resume)
        buttons.addWidget(self._pause_button)
        # Lets processBlocklist/domainWhitelist/lockMode be changed without
        # ending and restarting the session (session_manager.update_blocklist).
        self._edit_rules_button = button("Edit rules", SECONDARY_STYLE, height=38)
        self._edit_rules_button.clicked.connect(picker_gui.open_edit_session_rules)
        buttons.addWidget(self._edit_rules_button)
        buttons.addStretch(1)
        self._nuclear_button = button("End session", DANGER_STYLE, height=38)
        self._nuclear_button.clicked.connect(self._open_nuclear_dialog)
        buttons.addWidget(self._nuclear_button)
        box.addLayout(buttons)
        return self._now_card

    def _pause_resume(self):
        if session_manager.get_status()["isPaused"]:
            session_manager.resume_session()
        else:
            session_manager.pause_session()

    def _open_nuclear_dialog(self):
        # qt_ui/nuclear_dialog.py was written for tray.py's pystray menu
        # item, so it expects an "icon" it can call .notify()/.update_menu()
        # on -- neither is meaningful from an in-window button, so this
        # passes a no-op stand-in rather than reworking the dialog's public
        # signature just for this second caller. Imported lazily: tray.py
        # imports calendar_gui -> qt_ui.main_window -> this module, so a
        # top-level "import tray" here would be circular.
        import tray

        nuclear_dialog.open_nuclear_reason_dialog(_NullIcon(), tray.format_end_summary)

    # --- refresh ---

    def _refresh_status(self):
        status = session_manager.get_status()
        active = status["isActive"]
        self._was_active = active
        parked = status.get("parkedSessions") or []
        self._update_summary(active, status, parked)
        self._update_now(status, active)
        self._update_waiting(parked)
        self._update_start_note(status, active)
        self._update_rules_summary()

    def _update_summary(self, active, status, parked):
        parts = []
        if active and not status["isPaused"]:
            parts.append("1 running")
        elif active:
            parts.append("1 paused")
        if parked:
            parts.append(f"{len(parked)} waiting")
        self._summary_label.setText(" · ".join(parts) if parts else "Nothing running")

    def _update_now(self, status, active):
        self._now_card.setVisible(True)
        self._pause_button.setVisible(active)
        self._nuclear_button.setVisible(active)
        self._edit_rules_button.setVisible(active)
        if not active:
            self._now_dot.setStyleSheet("background:#D0D5DD;border-radius:6px;")
            self._now_name.setText("Nothing running")
            self._now_chip.hide()
            self._clock_label.setText("")
            self._clock_caption.setText("")
            self._status_label.setText("No active focus session.")
            return
        paused = bool(status["isPaused"])
        on_break = bool(status.get("isBreak"))
        self._now_dot.setStyleSheet(f"background:{_session_color(status)};border-radius:6px;")
        self._now_name.setText(_session_name(status))
        if paused:
            text, tone = "Paused", AMBER
        elif on_break:
            text, tone = "Break", TEAL
        else:
            text, tone = "Running", GREEN
        self._now_chip.setText(text)
        self._now_chip.setStyleSheet(
            f"background:{tone[0]};color:{tone[1]};border-radius:10px;padding:2px 10px;"
            "font-size:11.5px;font-weight:600;")
        self._now_chip.show()
        big, caption = _clock_for(status)
        self._clock_label.setText(big)
        self._clock_caption.setText("paused" if paused else caption)
        lines = [f"Lock mode: {status['lockMode']}   Violations: {status['violationCount']}"]
        kind = _session_kind(status)
        if kind not in ("Focus session",):
            lines.append(f"{kind}: {status.get('eventTitle') or _session_name(status)}")
        self._status_label.setText("\n".join(lines))
        self._pause_button.setText("Resume" if paused else "Pause")
        self._pause_button.setStyleSheet(primary_style(INK) if paused else SECONDARY_STYLE)

    def _update_waiting(self, parked):
        self._waiting_title.setVisible(bool(parked))
        self._waiting_title.setText(f"Waiting · {len(parked)}")
        keep = {p.get("parkId") for p in parked}
        for park_id in list(self._waiting_rows):
            if park_id not in keep:
                row = self._waiting_rows.pop(park_id)
                self._waiting_box.removeWidget(row)
                row.deleteLater()
        for index, session in enumerate(parked):
            park_id = session.get("parkId")
            row = self._waiting_rows.get(park_id)
            if row is None:
                row = self._waiting_rows[park_id] = _WaitingRow(session)
                self._waiting_box.insertWidget(index, row)
            else:
                row.update_from(session)
                if self._waiting_box.indexOf(row) != index:
                    self._waiting_box.removeWidget(row)
                    self._waiting_box.insertWidget(index, row)
        self._parked_note = ""

    def _update_start_note(self, status, active):
        if active and not status["isPaused"]:
            note = f"“{_session_name(status)}” will pause and wait while this one runs."
        elif active:
            note = "The paused session will wait until this one ends."
        else:
            note = ""
        self._start_note.setText(note)
        self._start_button.setToolTip(note)


class _NullIcon:
    """Stand-in for the pystray Icon that qt_ui/nuclear_dialog.py expects,
    used when opening it from the in-window Nuclear End button rather than
    the tray menu -- there's no tray notification/menu-refresh to perform
    from here, so both calls are no-ops."""

    def notify(self, *args, **kwargs):
        pass

    def update_menu(self):
        pass
