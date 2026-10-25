"""Focus tab: start an a-la-carte focus session, see what is running now, and everything
waiting behind it.

  Start a session -- a length in minutes, soft or hard lock, and the rules (apps,
                     allowed websites, browser profiles). Always available: if something
                     is already running it is paused and waits
                     (picker_dialogs.start_manual_session), it is never silently replaced.
  Now             -- the big time. Normally the running session; while a review is being
                     timed, the REVIEW is the big time (its pause key, its task's rules and
                     violations) and the session behind it shrinks to a row with "Resume".
  Waiting         -- every paused session and set-aside review, each with its own
                     "Switch to this" and "End" where it can be done from here.

Tasks started from the Tasks tab, reviews and sessions started here all show up in the
same lists, since they are all just sessions (or reviews) to the engine.
"""
from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QColor
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

import config
import picker_gui
import review_store
import session_manager
import tasks_store
import qt_ui.nuclear_dialog as nuclear_dialog
import qt_ui.picker_dialogs as picker_dialogs
from qt_ui.ui_kit import (
    AMBER,
    GREEN,
    INK,
    MUTED,
    NUM_CSS,
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


def _review_name(review):
    return "First attempt" if review.get("isFirstAttempt") else f"Review: {review['problemName']}"


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


def _running_review(status):
    """The review being timed on its own (a first attempt too), or None. A review that IS
    the running session is not "beside" it: that case is the session itself."""
    if status.get("source") == "review":
        return None
    try:
        return review_store.get_active_review() or review_store.get_first_attempt()
    except Exception:
        return None


def pause_review(review):
    """Sets a running review aside from here. A real review is paused in review_store;
    a first attempt only its own banner can pause, so it is asked to."""
    if review.get("isFirstAttempt"):
        return review_store.request_first_attempt("pause")
    return review_store.pause_active_review()


def resume_review(review):
    if review.get("isFirstAttempt"):
        return review_store.request_first_attempt("resume")
    return review_store.resume_active_review()


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


class _NoteRow(QFrame):
    """A set-aside review: shown so it is not forgotten; it is resumed in the Review tab."""

    def __init__(self, review):
        super().__init__()
        self.token = review.get("token")
        self.setObjectName("NoteRow")
        self.setStyleSheet("QFrame#NoteRow{background:#FFFFFF;border:1px dashed #C9CED8;border-radius:14px;}")
        row = QHBoxLayout(self)
        row.setContentsMargins(16, 12, 16, 12)
        row.setSpacing(12)
        dot = QLabel()
        dot.setFixedSize(10, 10)
        dot.setStyleSheet(f"background:{DEFAULT_COLOR};border-radius:5px;")
        row.addWidget(dot, 0, Qt.AlignVCenter)
        text = QVBoxLayout()
        text.setSpacing(1)
        self.name_label = ElidedLabel(f"Review: {review['problemName']}")
        self.name_label.setStyleSheet(f"font-size:14.5px;font-weight:700;color:{INK};background:transparent;")
        text.addWidget(self.name_label)
        self.detail_label = ElidedLabel(
            f"{format_clock(review.get('elapsedSeconds', 0))} elapsed · paused · resume it in the Review tab")
        self.detail_label.setStyleSheet(f"font-size:12.5px;color:{MUTED};background:transparent;")
        text.addWidget(self.detail_label)
        row.addLayout(text, 1)


class FocusTab(QWidget):
    def __init__(self):
        super().__init__()
        self._parked_note = ""
        self._waiting_rows = {}
        self._review_rows = {}
        self._start_note_text = ""
        self._shown_review = None  # the review that is the big time right now, if any

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

        # The session that is still there while a review holds the big time.
        self._behind_title = _section_title("Behind the review")
        layout.addWidget(self._behind_title)
        self._behind_box = QVBoxLayout()
        self._behind_box.setSpacing(10)
        layout.addLayout(self._behind_box)
        self._behind_row = None

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
        length.setSpacing(10)
        length.addWidget(QLabel("For"))
        self._custom_edit = QLineEdit(f"{last_minutes:g}" if isinstance(last_minutes, float) else str(last_minutes))
        self._custom_edit.setPlaceholderText("25")
        self._custom_edit.setAlignment(Qt.AlignCenter)
        self._custom_edit.setFixedSize(84, 40)
        self._custom_edit.setStyleSheet(
            f"QLineEdit{{border:1px solid #E1E5EC;border-radius:12px;font-size:16px;font-weight:600;"
            f"color:{INK};background:#FFFFFF;}}QLineEdit:focus{{border:1.5px solid {INK};}}")
        self._custom_edit.textEdited.connect(lambda _t: self._start_error.hide())
        self._custom_edit.returnPressed.connect(self._start_clicked)
        length.addWidget(self._custom_edit)
        length.addWidget(QLabel("minutes"))
        length.addSpacing(14)
        self._soft_button = QPushButton("Soft lock")
        self._hard_button = QPushButton("Hard lock")
        lock_group = QButtonGroup(self)
        lock_group.setExclusive(True)
        for pill in (self._soft_button, self._hard_button):
            pill.setCheckable(True)
            pill.setCursor(Qt.PointingHandCursor)
            pill.setFixedHeight(36)
            pill.setStyleSheet(_PILL_STYLE)
            lock_group.addButton(pill)
            length.addWidget(pill)
        (self._hard_button if cfg.get("last_lock_mode", "soft") == "hard" else self._soft_button).setChecked(True)
        length.addStretch(1)
        box.addLayout(length)

        rules = QHBoxLayout()
        rules.setSpacing(12)
        self._rules_summary = QLabel()
        self._rules_summary.setStyleSheet(f"font-size:12.5px;color:{MUTED};background:transparent;")
        rules.addWidget(self._rules_summary, 1)
        self._rules_button = button("Pick apps, websites, profiles", SECONDARY_STYLE, height=34)
        self._rules_button.clicked.connect(self._open_rules)
        rules.addWidget(self._rules_button)
        box.addLayout(rules)

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

        self._update_rules_summary(cfg)
        return card

    def _open_rules(self):
        picker_dialogs.open_rules_picker(on_saved=self._update_rules_summary)

    def _chosen_minutes(self):
        text = self._custom_edit.text().strip()
        try:
            return float(text) if text else None
        except ValueError:
            return None

    def _update_rules_summary(self, cfg=None):
        cfg = cfg if isinstance(cfg, dict) else config.load_config()
        apps = len(cfg.get("processBlocklist", []))
        sites = len(cfg.get("domainWhitelist", []))
        profiles = len(cfg.get("browserProfileBlocklist", []))
        parts = [f"{apps} app{'s' if apps != 1 else ''} blocked",
                 f"{sites} website{'s' if sites != 1 else ''} allowed"]
        if profiles:
            parts.append(f"{profiles} browser profile{'s' if profiles != 1 else ''} blocked")
        self._rules_summary.setText(" · ".join(parts))

    def _start_clicked(self):
        minutes = self._chosen_minutes()
        if minutes is None:
            self._show_start_error("Type how many minutes to focus for.")
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
        self._clock_label.setStyleSheet(f"font-size:52px;{NUM_CSS}color:{INK};background:transparent;")
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
        """Pause or resume the big time: the review while one is the big time, else the session."""
        review = self._shown_review
        if review is not None:
            pause_review(review)
            self._refresh_status()
            return
        if session_manager.get_status()["isPaused"]:
            session_manager.resume_session()
        else:
            session_manager.pause_session()

    def _resume_session_over_review(self):
        """"Resume" on the session behind a review: the review is set aside, the session comes back."""
        review = self._shown_review
        if review is not None:
            pause_review(review)
        if session_manager.get_status()["isPaused"]:
            session_manager.resume_session()
        self._refresh_status()

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
        review = _running_review(status)
        self._shown_review = review if (review is not None and not review.get("isPaused")) else None
        try:
            set_aside = review_store.list_parked_reviews()
        except Exception:
            set_aside = []
        self._update_summary(active, status, parked, review, set_aside)
        if self._shown_review is not None:
            self._update_now_review(self._shown_review, status, active)
        else:
            self._update_now(status, active)
        self._update_behind(status, active)
        self._update_waiting(parked, set_aside)
        self._update_start_note(status, active)
        self._update_rules_summary()

    def _update_summary(self, active, status, parked, review, set_aside):
        parts = []
        if self._shown_review is not None:
            parts.append("1 reviewing")
        elif active and not status["isPaused"]:
            parts.append("1 running")
        elif active:
            parts.append("1 paused")
        waiting = len(parked) + len(set_aside) + (1 if (self._shown_review is not None and active) else 0)
        if review is not None and self._shown_review is None:
            waiting += 1  # a paused review
        if waiting:
            parts.append(f"{waiting} waiting")
        self._summary_label.setText(" · ".join(parts) if parts else "Nothing running")

    def _show_chip(self, text, tone):
        self._now_chip.setText(text)
        self._now_chip.setStyleSheet(
            f"background:{tone[0]};color:{tone[1]};border-radius:10px;padding:2px 10px;"
            "font-size:11.5px;font-weight:600;")
        self._now_chip.show()

    def _update_now_review(self, review, status, active):
        """A review is being timed: it is the big time, with the session's rules around it."""
        self._now_card.setVisible(True)
        self._pause_button.setVisible(True)
        self._nuclear_button.setVisible(False)  # a review is finished or ended from the Review tab
        self._edit_rules_button.setVisible(active)
        task = tasks_store.get_task(status.get("eventId")) if active and status.get("eventId") else None
        color = safe_color(review.get("taskColor") or review.get("subjectColor") or (task or {}).get("color"))
        self._now_dot.setStyleSheet(f"background:{color};border-radius:6px;")
        self._now_name.setText(_review_name(review))
        self._show_chip("Reviewing", GREEN)
        self._clock_label.setText(format_clock(review.get("elapsedSeconds", 0)))
        self._clock_caption.setText("elapsed")
        lines = []
        if active:
            lines.append(f"Lock mode: {status['lockMode']}   Violations: {status['violationCount']}")
            if status.get("eventTitle"):
                lines.append(f"{_session_kind(status)}: {status['eventTitle']}")
        else:
            lines.append("No session behind it, so nothing is blocked.")
        lines.append("Finish it in the Review tab.")
        self._status_label.setText("\n".join(lines))
        self._pause_button.setText("Pause")
        self._pause_button.setStyleSheet(SECONDARY_STYLE)

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
            self._show_chip("Paused", AMBER)
        elif on_break:
            self._show_chip("Break", TEAL)
        else:
            self._show_chip("Running", GREEN)
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

    def _update_behind(self, status, active):
        """While a review is the big time, the running (or paused) session is a row of its own."""
        show = self._shown_review is not None and active
        self._behind_title.setVisible(show)
        if not show:
            if self._behind_row is not None:
                self._behind_box.removeWidget(self._behind_row)
                self._behind_row.deleteLater()
                self._behind_row = None
            return
        if self._behind_row is None:
            row = _WaitingRow(status)
            row.switch_button.clicked.disconnect()
            row.switch_button.clicked.connect(self._resume_session_over_review)
            row.end_button.clicked.disconnect()
            row.end_button.clicked.connect(self._open_nuclear_dialog)
            self._behind_row = row
            self._behind_box.addWidget(row)
        row = self._behind_row
        row.update_from(status)
        paused = bool(status["isPaused"])
        row.switch_button.setText("Resume pomodoro" if status.get("pomodoro") and paused else "Resume" if paused else "Switch to this")
        row.setStyleSheet("QFrame#WaitingRow{background:#FFFFFF;border:1px solid #E6E9EF;border-radius:14px;}")

    def _update_waiting(self, parked, set_aside):
        total = len(parked) + len(set_aside)
        self._waiting_title.setVisible(bool(total))
        self._waiting_title.setText(f"Waiting · {total}")
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
        # Reviews set aside: listed after the sessions, read-only.
        keep_tokens = {r.get("token") for r in set_aside}
        for token in list(self._review_rows):
            if token not in keep_tokens:
                row = self._review_rows.pop(token)
                self._waiting_box.removeWidget(row)
                row.deleteLater()
        for review in set_aside:
            token = review.get("token")
            if token not in self._review_rows:
                row = self._review_rows[token] = _NoteRow(review)
                self._waiting_box.addWidget(row)
            else:
                self._review_rows[token].detail_label.setText(
                    f"{format_clock(review.get('elapsedSeconds', 0))} elapsed · paused · resume it in the Review tab")
        self._parked_note = ""

    def _update_start_note(self, status, active):
        if self._shown_review is not None:
            note = f"“{_review_name(self._shown_review)}” keeps running; this session starts beside it."
        elif active and not status["isPaused"]:
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
