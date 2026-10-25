"""Tasks tab: one card per recurring task. Each card shows today's progress toward the
task's target minutes as a ring (in the task's own color), its lock rules, its banked
"vacation" time and the buttons for the task's current state. Business logic
(scheduling, pause-aware worked time, vacation balance) lives in tasks_store.py; this
module is presentation plus wiring the buttons to session_manager -- the same session
engine the Focus panel and calendar events use, via
start_session(source="task", event_id=<task id>, event_title=<task name>).

A card is always in one of these states (decided each tick by update_dynamic()):
  idle     -- today's ring, a Start button. Start opens the "how long" choices.
  armed    -- Until burnout / Custom time / Pomodoro (Pomodoro opens its own window).
  running  -- the session's own ring and countdown, Pause / End. Sub-states: paused,
              on a pomodoro break, until burnout (counts up), a review of this task.
  waiting  -- this task's session was set aside (paused) while another task runs:
              "Switch to this" brings it back.
  busy     -- another task is running: Start is disabled.

Only one session runs at a time app-wide (session_manager's model). A review of this
task's linked topic that is timed beside the task's own session (see review_store) is
listed inside the card.

The grid reflows with the window: as many cards per row as fit, between CARD_MIN and
CARD_MAX wide, flowing down and across as the window is resized.
"""
import math
from datetime import date

from PySide6.QtCore import QEvent, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QFontMetrics, QPainter, QPen
from PySide6.QtWidgets import (
    QDialog,
    QFrame,
    QGraphicsDropShadowEffect,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

import review_store
import session_history
import session_manager
import tasks_store
from qt_ui.day_layout import contrasting_text_color
from qt_ui.task_editor import open_task_editor

STATUS_REFRESH_MS = 1000
CARD_MIN = 330
CARD_MAX = 470
GRID_GAP = 18
MAX_SUBJECT_CHIPS = 3

INK = "#1B2230"
MUTED = "#667085"
FAINT = "#98A2B3"
TEXT_FONT = "Segoe UI"
# DIN-style numerals for the ring and clocks (falls back to Segoe UI).
NUM_FONTS = ["Bahnschrift SemiBold", "Segoe UI Semibold", "Segoe UI"]

_GREEN = ("#E3F6E8", "#157F3B")
_AMBER = ("#FEF0C7", "#93370D")
_TEAL = ("#D5F5F3", "#0E7C75")
_GREY = ("#EEF0F4", "#5B6472")
_RED = ("#FEE4E2", "#B42318")
_BREAK_COLOR = "#14B8A6"


# --- small helpers ---------------------------------------------------------

def _format_minutes(total_minutes):
    total_minutes = int(round(total_minutes))
    hours, minutes = divmod(total_minutes, 60)
    if hours:
        return f"{hours}h {minutes}m"
    return f"{minutes}m"


def _days_text(days, tail):
    return f"{days} day{'s' if days != 1 else ''} {tail}"


def _format_clock(total_seconds):
    total_seconds = max(0, int(total_seconds))
    minutes, seconds = divmod(total_seconds, 60)
    if minutes >= 100:
        hours, minutes = divmod(minutes, 60)
        return f"{hours}:{minutes:02d}:{seconds:02d}"
    return f"{minutes:02d}:{seconds:02d}"


def _safe_color(value):
    color = QColor(value if isinstance(value, str) else "")
    return color.name() if color.isValid() else "#5B8DEF"


def _tint(color, alpha):
    c = QColor(color)
    return f"rgba({c.red()},{c.green()},{c.blue()},{alpha})"


def _darker(color, factor=0.62):
    c = QColor(color)
    return QColor(int(c.red() * factor), int(c.green() * factor), int(c.blue() * factor)).name()


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


def _reviews_by_task():
    """{task id: [review rows]} for reviews of a task's linked topic that are timed
    on their own (not driving a session): the running one, then the set-aside ones.
    A row is {"name", "seconds", "state": "running" | "paused", "token"}."""
    rows = {}
    try:
        def task_of(problem_id):
            problem = review_store.get_problem(problem_id)
            topic = review_store.get_topic(problem["topicId"]) if problem else None
            return topic.get("linkedTaskId") if topic else None

        active = review_store.get_active_review()
        if active:
            task_id = task_of(active["problemId"])
            if task_id:
                rows.setdefault(task_id, []).append({
                    "name": active["problemName"], "seconds": active["elapsedSeconds"],
                    "state": "paused" if active.get("isPaused") else "running", "token": active["token"],
                })
        for parked in review_store.list_parked_reviews():
            task_id = task_of(parked["problemId"])
            if task_id:
                rows.setdefault(task_id, []).append({
                    "name": parked["problemName"], "seconds": parked["elapsedSeconds"],
                    "state": "paused", "token": parked["token"],
                })
    except Exception:
        return {}
    return rows


# --- building blocks -------------------------------------------------------

class _Ring(QWidget):
    """The progress ring: a faint full track, a rounded-end arc, big numerals inside."""

    def __init__(self, size=124):
        super().__init__()
        self._color = QColor("#5B8DEF")
        self._fraction = 0.0
        self._big = ""
        self._small = ""
        self._dim = False
        self.setFixedSize(size, size)

    def set_state(self, color, fraction, big, small, dim=False):
        fraction = round(max(0.0, fraction), 4)
        if (QColor(color).name(), fraction, big, small, dim) == (
                self._color.name(), self._fraction, self._big, self._small, self._dim):
            return
        self._color, self._fraction, self._big, self._small, self._dim = QColor(color), fraction, big, small, dim
        self.update()

    def paintEvent(self, _event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        width = 11
        rect = QRectF(width / 2 + 2, width / 2 + 2, self.width() - width - 4, self.height() - width - 4)
        track = QColor(self._color)
        track.setAlphaF(0.15)
        p.setPen(QPen(track, width, Qt.SolidLine, Qt.RoundCap))
        p.drawEllipse(rect)
        if self._fraction > 0:
            arc = QColor(self._color)
            if self._dim:
                arc.setAlphaF(0.4)
            p.setPen(QPen(arc, width, Qt.SolidLine, Qt.RoundCap))
            span = min(self._fraction, 1.0) * 360
            if span >= 359.5:
                p.drawEllipse(rect)
            else:
                p.drawArc(rect, 90 * 16, int(-span * 16))
        big = QFont()
        big.setFamilies(NUM_FONTS)
        big.setPixelSize(26 if len(self._big) < 6 else 22)
        p.setFont(big)
        p.setPen(QColor(FAINT if self._dim else INK))
        p.drawText(QRectF(0, self.height() / 2 - 22, self.width(), 30), Qt.AlignCenter, self._big)
        small = QFont(TEXT_FONT)
        small.setPixelSize(11)
        p.setFont(small)
        p.setPen(QColor(FAINT if self._dim else MUTED))
        p.drawText(QRectF(0, self.height() / 2 + 6, self.width(), 18), Qt.AlignCenter, self._small)


class _ElidedLabel(QLabel):
    """One line that ends in an ellipsis instead of pushing the card wider."""

    def __init__(self, text=""):
        super().__init__(text)
        self._full = text
        self.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        self.setMinimumWidth(40)

    def setText(self, text):
        self._full = text or ""
        self.setToolTip(self._full)
        super().setText(self._full)
        self.update()

    def paintEvent(self, _event):
        p = QPainter(self)
        p.setFont(self.font())
        p.setPen(self.palette().color(self.foregroundRole()))
        shown = QFontMetrics(self.font()).elidedText(self._full, Qt.ElideRight, self.width())
        p.drawText(self.rect(), Qt.AlignLeft | Qt.AlignVCenter, shown)


def _chip(text, color=None, solid=False, tone=None):
    label = QLabel(text)
    if solid:
        css = f"background:{INK};color:#FFFFFF;"
    elif tone:
        css = f"background:{tone[0]};color:{tone[1]};"
    elif color:
        css = f"background:{_tint(color, 0.14)};color:{_darker(color, .55)};"
    else:
        css = f"background:#F2F4F7;color:{MUTED};"
    label.setStyleSheet(css + "border-radius:10px;padding:2px 10px;font-size:11.5px;font-weight:600;")
    return label


def _note(text="", color=MUTED, size=12.5):
    label = QLabel(text)
    label.setWordWrap(True)
    label.setStyleSheet(f"font-size:{size}px;color:{color};background:transparent;")
    return label


def _primary_style(color):
    return (
        f"QPushButton{{background:{color};color:{contrasting_text_color(color)};border:none;"
        f"border-radius:12px;font-size:14px;font-weight:600;padding:0 18px;}}"
        f"QPushButton:hover{{background:{_darker(color, .9)};}}"
        "QPushButton:disabled{background:#EEF0F4;color:#A3ABB8;}")


_SECONDARY_STYLE = (
    f"QPushButton{{background:#F2F4F7;color:{INK};border:none;border-radius:12px;"
    "font-size:13.5px;font-weight:600;padding:0 16px;}"
    "QPushButton:hover{background:#E7EAF0;}")


def _accent_style(color):
    return (
        f"QPushButton{{background:{_tint(color, .12)};color:{_darker(color, .55)};border:none;"
        "border-radius:12px;font-size:13.5px;font-weight:600;padding:0 16px;}"
        f"QPushButton:hover{{background:{_tint(color, .2)};}}")


_DANGER_STYLE = (
    "QPushButton{background:#FEF3F2;color:#B42318;border:none;border-radius:12px;"
    "font-size:13.5px;font-weight:600;padding:0 20px;}"
    "QPushButton:hover{background:#FEE4E2;}")

_LINK_STYLE = (
    f"QPushButton{{border:none;background:transparent;color:{MUTED};font-size:12.5px;padding:0;text-align:left;}}"
    f"QPushButton:hover:enabled{{color:{INK};text-decoration:underline;}}"
    f"QPushButton:disabled{{color:{FAINT};}}")


def _button(text, style, height=40):
    b = QPushButton(text)
    b.setCursor(Qt.PointingHandCursor)
    b.setFixedHeight(height)
    b.setStyleSheet(style)
    return b


def _link_button(text):
    b = QPushButton(text)
    b.setFlat(True)
    b.setCursor(Qt.PointingHandCursor)
    b.setStyleSheet(_LINK_STYLE)
    return b


class _VacationLine(QWidget):
    """Quiet text; a small "Use" tag pops up beside it on hover, and a click opens the
    vacation window. With nothing to use it is plain text."""

    clicked = Signal()

    def __init__(self):
        super().__init__()
        self._usable = False
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        self._label = QLabel()
        self._label.setStyleSheet(f"font-size:12.5px;color:{MUTED};background:transparent;")
        layout.addWidget(self._label)
        self._tag = QLabel("Use")
        self._tag.setStyleSheet(
            f"background:{INK};color:#FFFFFF;border-radius:8px;padding:1px 9px;font-size:11px;font-weight:600;")
        self._tag.hide()
        layout.addWidget(self._tag)
        layout.addStretch(1)

    def set_state(self, text, usable):
        self._label.setText(text)
        self._usable = usable
        self.setCursor(Qt.PointingHandCursor if usable else Qt.ArrowCursor)
        self._label.setStyleSheet(f"font-size:12.5px;color:{MUTED if usable else FAINT};background:transparent;")
        if not usable:
            self._tag.hide()

    def text(self):
        return self._label.text()

    def is_usable(self):
        return self._usable

    def tag_visible(self):
        return not self._tag.isHidden()

    def enterEvent(self, event):
        if self._usable:
            self._tag.show()
            self._label.setStyleSheet(
                f"font-size:12.5px;color:{INK};text-decoration:underline;background:transparent;")
        super().enterEvent(event)

    def leaveEvent(self, event):
        self._tag.hide()
        if self._usable:
            self._label.setStyleSheet(f"font-size:12.5px;color:{MUTED};background:transparent;")
        super().leaveEvent(event)

    def mousePressEvent(self, event):
        if self._usable and event.button() == Qt.LeftButton:
            self.clicked.emit()
        super().mousePressEvent(event)


class _ReviewBox(QFrame):
    """Reviews of this task's topic that are timed beside its session: one review reads as
    a line with its own pause button; several read as a short list, the running one first."""

    def __init__(self):
        super().__init__()
        self.setObjectName("ReviewBox")
        self.setStyleSheet("QFrame#ReviewBox{background:#F7F8FA;border-radius:12px;} QLabel{background:transparent;}")
        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(12, 10, 12, 10)
        self._layout.setSpacing(6)
        self._signature = None
        self._time_labels = []
        self.pause_buttons = []
        self.hide()

    def set_rows(self, rows, on_toggle, on_break):
        """rows: [{"name", "seconds", "state", "token"}]. Rebuilds only when the list of
        reviews or their states changes; otherwise just moves the clocks."""
        if not rows:
            self._signature = None
            self.hide()
            return
        signature = tuple((r["token"], r["name"], r["state"]) for r in rows) + (on_break,)
        if signature != self._signature:
            self._signature = signature
            self._rebuild(rows, on_toggle, on_break)
        for label, row in zip(self._time_labels, rows):
            label.setText(_format_clock(row["seconds"]))
        self.show()

    def _rebuild(self, rows, on_toggle, on_break):
        while self._layout.count():
            item = self._layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.setParent(None)
                widget.deleteLater()
            elif item.layout() is not None:
                self._drop_layout(item.layout())
        self._time_labels = []
        self.pause_buttons = []
        if len(rows) > 1:
            head = QLabel(f"Reviews · {len(rows)}")
            head.setStyleSheet(f"font-size:12px;font-weight:700;color:{MUTED};")
            self._layout.addWidget(head)
        for row in rows:
            line = QHBoxLayout()
            line.setSpacing(8)
            running = row["state"] == "running"
            if len(rows) > 1:
                mark = QLabel("●" if running else "❚❚")
                mark.setFixedWidth(14)
                mark.setStyleSheet(f"color:{'#16A34A' if running else FAINT};font-size:{9 if running else 8}px;")
                line.addWidget(mark)
            column = QVBoxLayout()
            column.setSpacing(0)
            title = _ElidedLabel(row["name"] if len(rows) > 1 else f"Reviewing {row['name']}")
            title.setStyleSheet(
                f"font-size:13px;font-weight:{'600' if running else '500'};color:{INK if running else '#475467'};")
            column.addWidget(title)
            if len(rows) == 1:
                column.addWidget(_note(
                    "Paused for the break" if on_break else ("Pauses on breaks, resumes after" if running else "Paused"),
                    MUTED, 11.5))
            line.addLayout(column, 1)
            clock = QLabel(_format_clock(row["seconds"]))
            clock.setStyleSheet(f"font-family:'{NUM_FONTS[0]}';font-size:15px;color:{INK if running else MUTED};")
            line.addWidget(clock)
            self._time_labels.append(clock)
            # Only the review that is timed right now (or the only one) can be paused or
            # resumed from here; the set-aside ones are picked up in the Review tab.
            if running or len(rows) == 1:
                toggle = QPushButton("Pause" if running else "Resume")
                toggle.setFixedHeight(26)
                toggle.setCursor(Qt.PointingHandCursor)
                toggle.setToolTip("Pause this review" if running else "Resume this review")
                toggle.setStyleSheet(
                    "QPushButton{background:#FFFFFF;border:1px solid #DDE1E8;border-radius:13px;padding:0 12px;"
                    f"font-size:12px;font-weight:600;color:{INK};}}"
                    "QPushButton:hover{background:#F2F4F7;}")
                toggle.clicked.connect(lambda _checked=False, r=row: on_toggle(r))
                line.addWidget(toggle)
                self.pause_buttons.append(toggle)
            self._layout.addLayout(line)

    @staticmethod
    def _drop_layout(layout):
        while layout.count():
            item = layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.setParent(None)
                widget.deleteLater()
            elif item.layout() is not None:
                _ReviewBox._drop_layout(item.layout())


# --- dialogs ---------------------------------------------------------------

_DIALOG_STYLE = (
    "QDialog#PopupBg{background:#FFFFFF;} "
    f"QDialog#PopupBg QLabel{{color:{INK};font-size:13px;background:transparent;}} "
    "QDialog#PopupBg QLineEdit{background:#FFFFFF;border:1px solid #D5DAE2;border-radius:10px;"
    f"padding:0 12px;color:{INK};font-size:15px;font-weight:600;min-height:40px;}} "
    "QDialog#PopupBg QLineEdit:focus{border:1.5px solid #5B8DEF;}")


class _PomodoroDialog(QDialog):
    """Modal focus/break/cycles picker for a card's Pomodoro bar. Styled inline (not just
    by the app stylesheet): it is parented to its card, and a window parented to a styled
    widget still inherits that widget's stylesheet."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("PopupBg")
        self.setStyleSheet(_DIALOG_STYLE)
        self.setWindowTitle("Start Pomodoro")
        self.setMinimumWidth(380)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(26, 24, 26, 22)
        layout.setSpacing(14)

        title = QLabel("Start a Pomodoro")
        title.setStyleSheet(f"font-size:18px;font-weight:700;color:{INK};")
        layout.addWidget(title)
        layout.addWidget(_note("Focus blocks are blocked, breaks are free."))

        row = QHBoxLayout()
        row.setSpacing(10)

        def _field(label_text, default_value):
            column = QVBoxLayout()
            column.setSpacing(5)
            label = QLabel(label_text)
            label.setStyleSheet(f"font-size:12.5px;font-weight:600;color:{MUTED};")
            column.addWidget(label)
            edit = QLineEdit(str(default_value))
            edit.setAlignment(Qt.AlignCenter)
            column.addWidget(edit)
            row.addLayout(column, 1)
            return edit

        self._focus_edit = _field("Focus (min)", 25)
        self._break_edit = _field("Break (min)", 5)
        self._cycles_edit = _field("Cycles", 4)
        layout.addLayout(row)

        self._status_label = QLabel()
        self._status_label.setStyleSheet("color:#B42318;font-size:12.5px;")
        layout.addWidget(self._status_label)

        buttons = QHBoxLayout()
        buttons.setSpacing(8)
        buttons.addStretch(1)
        cancel = _button("Cancel", _SECONDARY_STYLE)
        cancel.clicked.connect(self.reject)
        buttons.addWidget(cancel)
        start = _button("Start Pomodoro", _primary_style("#5B8DEF"))
        start.clicked.connect(self._on_start)
        buttons.addWidget(start)
        layout.addLayout(buttons)
        self._result = None

    def _on_start(self):
        try:
            focus_minutes = float(self._focus_edit.text())
            break_minutes = float(self._break_edit.text())
            cycles = int(self._cycles_edit.text())
            if (not session_manager.is_valid_duration(focus_minutes)
                    or not session_manager.is_valid_duration(break_minutes) or cycles <= 0):
                raise ValueError
        except ValueError:
            self._status_label.setText("Enter valid positive numbers.")
            return
        self._result = (focus_minutes, break_minutes, cycles)
        self.accept()

    @staticmethod
    def get_settings(parent=None):
        """Returns (focus_minutes, break_minutes, cycles) or None if cancelled."""
        dialog = _PomodoroDialog(parent)
        dialog.exec()
        return dialog._result


class _VacationDialog(QDialog):
    """"Use vacation time": spend banked minutes on today's goal."""

    def __init__(self, task_name, color, balance_minutes, usable, parent=None):
        super().__init__(parent)
        self.setObjectName("PopupBg")
        self.setStyleSheet(_DIALOG_STYLE)
        self.setWindowTitle("Use vacation time")
        self.setMinimumWidth(400)
        self._usable = usable
        self._result = None
        layout = QVBoxLayout(self)
        layout.setContentsMargins(26, 24, 26, 22)
        layout.setSpacing(14)

        title = QLabel("Use vacation time")
        title.setStyleSheet(f"font-size:18px;font-weight:700;color:{INK};")
        layout.addWidget(title)
        layout.addWidget(_note(
            f"Spend banked minutes on today's goal for {task_name}. They count as if you had worked them."))

        balance = QHBoxLayout()
        big = QLabel(_format_minutes(balance_minutes))
        big.setStyleSheet(f"font-family:'{NUM_FONTS[0]}';font-size:34px;color:{INK};")
        balance.addWidget(big)
        caption = QLabel(f"banked\n{usable} min usable today")
        caption.setStyleSheet(f"font-size:12px;color:{MUTED};")
        balance.addWidget(caption)
        balance.addStretch(1)
        layout.addLayout(balance)

        chips = QHBoxLayout()
        chips.setSpacing(6)
        options = [(str(amount), amount) for amount in (10, 15, 30) if amount < usable]
        options.append((f"All {usable}", usable))
        for label, amount in options:
            b = QPushButton(label)
            b.setCursor(Qt.PointingHandCursor)
            b.setFixedHeight(32)
            b.setStyleSheet(
                "QPushButton{border:1px solid #D5DAE2;border-radius:16px;padding:0 14px;background:#FFFFFF;"
                f"color:{INK};font-size:13px;font-weight:600;}}"
                f"QPushButton:hover{{border:1.5px solid {color};}}")
            b.clicked.connect(lambda _checked=False, a=amount: self._minutes_edit.setText(str(a)))
            chips.addWidget(b)
        chips.addStretch(1)
        layout.addLayout(chips)

        field = QVBoxLayout()
        field.setSpacing(5)
        field_label = QLabel("Minutes to use")
        field_label.setStyleSheet(f"font-size:12.5px;font-weight:600;color:{MUTED};")
        field.addWidget(field_label)
        row = QHBoxLayout()
        self._minutes_edit = QLineEdit(str(min(15, usable)))
        self._minutes_edit.returnPressed.connect(self._confirm)
        row.addWidget(self._minutes_edit, 1)
        row.addWidget(_note(f"of {usable}"))
        field.addLayout(row)
        layout.addLayout(field)

        self._status_label = QLabel()
        self._status_label.setStyleSheet("color:#B42318;font-size:12.5px;")
        layout.addWidget(self._status_label)

        buttons = QHBoxLayout()
        buttons.setSpacing(8)
        buttons.addStretch(1)
        cancel = _button("Cancel", _SECONDARY_STYLE)
        cancel.clicked.connect(self.reject)
        buttons.addWidget(cancel)
        self._use_button = _button("Use", _primary_style(color))
        self._use_button.clicked.connect(self._confirm)
        buttons.addWidget(self._use_button)
        layout.addLayout(buttons)
        self._minutes_edit.textChanged.connect(self._refresh_button)
        self._refresh_button()

    def _refresh_button(self):
        try:
            minutes = int(self._minutes_edit.text())
        except ValueError:
            minutes = None
        self._use_button.setText(f"Use {minutes} minute{'s' if minutes != 1 else ''}" if minutes else "Use")

    def _confirm(self):
        try:
            minutes = int(self._minutes_edit.text())
        except ValueError:
            self._status_label.setText("Enter a whole number of minutes.")
            return
        if minutes <= 0 or minutes > self._usable:
            self._status_label.setText(f"Enter a number between 1 and {self._usable}.")
            return
        self._result = minutes
        self.accept()

    @staticmethod
    def get_minutes(task_name, color, balance_minutes, usable, parent=None):
        dialog = _VacationDialog(task_name, color, balance_minutes, usable, parent)
        dialog.exec()
        return dialog._result


# --- the tab ---------------------------------------------------------------

class TasksTab(QWidget):
    def __init__(self):
        super().__init__()
        self.setObjectName("TasksPage")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setStyleSheet("QWidget#TasksPage{background:#F6F7F9;}")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(28, 24, 28, 20)
        layout.setSpacing(4)

        layout.addLayout(self._build_header())
        self._summary_label = QLabel()
        self._summary_label.setStyleSheet(f"color:{MUTED};font-size:13px;")
        layout.addWidget(self._summary_label)

        scroll = QScrollArea()
        self._scroll = scroll
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setStyleSheet("QScrollArea{background:transparent;border:none;}")
        scroll.viewport().installEventFilter(self)
        self._grid_container = QWidget()
        self._grid_container.setObjectName("TasksGridBg")
        self._grid_container.setStyleSheet("QWidget#TasksGridBg{background:transparent;}")
        self._grid = QGridLayout(self._grid_container)
        self._grid.setSpacing(GRID_GAP)
        self._grid.setContentsMargins(0, 10, 0, 10)
        self._grid.setAlignment(Qt.AlignLeft | Qt.AlignTop)
        scroll.setWidget(self._grid_container)
        layout.addWidget(scroll, 1)

        self._cards = {}
        self._card_order = []
        self._empty_label = None
        self._placed_columns = None
        self.refresh()

        self._status_timer = QTimer(self)
        self._status_timer.timeout.connect(self._tick_if_visible)
        self._status_timer.start(STATUS_REFRESH_MS)

    def _build_header(self):
        header = QHBoxLayout()
        title = QLabel("Tasks")
        title.setStyleSheet(f"font-size:22px;font-weight:700;color:{INK};")
        header.addWidget(title)
        header.addStretch(1)
        add_button = QPushButton("+  Add recurring task")
        add_button.setProperty("class", "AccentButton")
        add_button.clicked.connect(lambda: open_task_editor(on_saved=lambda _t: self.refresh()))
        header.addWidget(add_button)
        return header

    def refresh(self):
        """Full rebuild -- called after a task is added/edited/deleted, or when this tab
        first opens. Per-second updates (rings, countdowns) go through _tick()."""
        while self._grid.count():
            item = self._grid.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        self._cards = {}
        self._card_order = []
        self._empty_label = None

        tasks = [t for t in tasks_store.load_tasks() if not t.get("archived")]
        if not tasks:
            self._empty_label = QLabel("No tasks yet. Click “+ Add recurring task” to create one.")
            self._empty_label.setStyleSheet(f"color:{FAINT};font-size:14px;")
            self._grid.addWidget(self._empty_label, 0, 0)
        for task in tasks:
            card = _TaskCard(task, on_changed=self.refresh)
            self._cards[task["id"]] = card
            self._card_order.append(card)
        self._placed_columns = None
        self._place_cards()
        self._tick()

    # --- layout: as many cards per row as fit ---

    def _columns(self):
        width = self._scroll.viewport().width()
        return max(1, (width + GRID_GAP) // (CARD_MIN + GRID_GAP))

    def _place_cards(self):
        """(Re)seats the cards for the current width. Cards keep their natural height and
        sit at the top of their row; leftover width is shared between the columns."""
        columns = self._columns()
        if columns == self._placed_columns:
            return
        for card in self._card_order:
            self._grid.removeWidget(card)
        for column in range(max(columns, self._placed_columns or 0)):
            self._grid.setColumnStretch(column, 0)
        for index, card in enumerate(self._card_order):
            row, column = divmod(index, columns)
            self._grid.addWidget(card, row, column, Qt.AlignTop)
        for column in range(columns):
            self._grid.setColumnStretch(column, 1)
        self._placed_columns = columns

    def eventFilter(self, obj, event):
        # The viewport's own width decides the column count, and it is only final once
        # the tab's resize has been laid out.
        if obj is self._scroll.viewport() and event.type() == QEvent.Resize and self._card_order:
            self._place_cards()
        return super().eventFilter(obj, event)

    # --- per-second work ---

    def _tick_if_visible(self):
        # Reloading history and restyling every card each second is waste while another
        # tab is showing (showEvent catches up on return).
        if self.isVisible():
            self._tick()

    def showEvent(self, event):
        self._tick()
        super().showEvent(event)

    def _tick(self):
        status = session_manager.get_status()
        sessions = session_history.load_all()
        reviews = _reviews_by_task()
        reached = scheduled = 0
        for task_id, card in self._cards.items():
            card.update_dynamic(status, sessions, reviews.get(task_id, []))
            if card.goal_state == "reached":
                reached += 1
                scheduled += 1
            elif card.goal_state == "open":
                scheduled += 1
        if not self._cards:
            self._summary_label.setText("")
        elif not scheduled:
            self._summary_label.setText("Nothing scheduled today")
        else:
            self._summary_label.setText(
                f"Today · {reached} of {scheduled} goal{'s' if scheduled != 1 else ''} reached")


class _TaskCard(QFrame):
    def __init__(self, task, on_changed):
        super().__init__()
        self._task = task
        self._on_changed = on_changed
        self._color = _safe_color(task.get("color", "#5B8DEF"))
        self._armed = False
        self._custom = False
        self._cash_in_balance_int = 0
        self._logged_minutes_today = 0  # logged toward the goal's own stretch (today, or the week/month)
        self._snap = None
        self._restrictions_variants = []
        self._restrictions_laid_out = False
        self._style_key = None
        self.goal_state = "none"

        self.setObjectName("TaskCard")
        self.setMinimumWidth(CARD_MIN)
        self.setMaximumWidth(CARD_MAX)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        shadow = QGraphicsDropShadowEffect(self)
        shadow.setBlurRadius(28)
        shadow.setOffset(0, 6)
        shadow.setColor(QColor(16, 24, 40, 26))
        self.setGraphicsEffect(shadow)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(22, 20, 22, 20)
        outer.setSpacing(14)
        outer.addLayout(self._build_header())
        outer.addLayout(self._build_chips())
        outer.addLayout(self._build_body())

        self._review_box = _ReviewBox()
        outer.addWidget(self._review_box)

        self._idle_actions = self._build_idle_actions()
        outer.addWidget(self._idle_actions)
        self._armed_panel = self._build_armed_panel()
        outer.addWidget(self._armed_panel)
        self._armed_panel.hide()
        self._running_panel = self._build_running_panel()
        outer.addWidget(self._running_panel)
        self._running_panel.hide()

        self._edit_button = self._build_edit_button()
        self._apply_card_style("idle")

    # --- static pieces ---

    def _build_edit_button(self):
        """A tiny pencil in the top-right corner, shown while the pointer is over the
        card, in every state (it also edits a running task: see _TaskEditor)."""
        button = QPushButton("✎", self)
        button.setObjectName("editTaskButton")
        button.setFlat(True)
        button.setFixedSize(24, 24)
        button.setCursor(Qt.PointingHandCursor)
        button.setToolTip("Edit task")
        button.setAccessibleName("Edit task")
        button.setStyleSheet(
            f"QPushButton{{border:1px solid #E6E9EF;background:#FFFFFF;color:{MUTED};font-size:13px;"
            f"border-radius:12px;padding:0;}}"
            f"QPushButton:hover{{background:#F2F4F7;color:{INK};}}")
        button.clicked.connect(self._open_editor)
        button.hide()
        return button

    def _place_edit_button(self):
        self._edit_button.move(self.width() - self._edit_button.width() - 10, 10)
        self._edit_button.raise_()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._place_edit_button()

    def enterEvent(self, event):
        self._place_edit_button()
        self._edit_button.show()
        super().enterEvent(event)

    def leaveEvent(self, event):
        self._edit_button.hide()
        super().leaveEvent(event)

    def eventFilter(self, obj, event):
        # The restrictions line re-fits its wording whenever its column is resized.
        if obj is self._blocklist_button and event.type() == QEvent.Resize:
            self._restrictions_laid_out = True
            self._fit_restrictions()
        return super().eventFilter(obj, event)

    def _build_header(self):
        row = QHBoxLayout()
        row.setSpacing(10)
        row.setContentsMargins(0, 0, 22, 0)  # room for the hover pencil in the corner
        self._dot = QLabel()
        self._dot.setFixedSize(12, 12)
        self._dot.setStyleSheet(f"background:{self._color};border-radius:6px;")
        row.addWidget(self._dot, 0, Qt.AlignVCenter)
        self._name_label = _ElidedLabel(self._task.get("name") or "(untitled task)")
        self._name_label.setStyleSheet(f"font-size:17px;font-weight:700;color:{INK};background:transparent;")
        row.addWidget(self._name_label, 1)
        self._state_chip = QLabel()
        self._state_chip.hide()
        row.addWidget(self._state_chip)
        return row

    def _build_chips(self):
        row = QHBoxLayout()
        row.setSpacing(6)
        row.addWidget(_chip("Hard lock" if self._task.get("lockMode") == "hard" else "Soft lock",
                            solid=self._task.get("lockMode") == "hard"))
        subjects = _subjects_for_task(self._task["id"])
        for subject in subjects[:MAX_SUBJECT_CHIPS]:
            row.addWidget(_chip(subject["name"], _safe_color(subject.get("color"))))
        if len(subjects) > MAX_SUBJECT_CHIPS:
            row.addWidget(_chip(f"+{len(subjects) - MAX_SUBJECT_CHIPS}"))
        self._violation_chip = _chip("", tone=_RED)
        self._violation_chip.hide()
        row.addWidget(self._violation_chip)
        row.addStretch(1)
        return row

    def _build_body(self):
        body = QHBoxLayout()
        body.setSpacing(20)
        self._ring = _Ring()
        body.addWidget(self._ring)
        column = QVBoxLayout()
        column.setSpacing(6)
        column.addStretch(1)
        # Headline: "18 min to go" at rest; the live session's own line while running.
        self._countdown_label = QLabel()
        self._countdown_label.setWordWrap(True)
        self._countdown_label.setStyleSheet(f"font-size:15px;font-weight:600;color:{INK};background:transparent;")
        column.addWidget(self._countdown_label)
        self._detail_label = _note()
        column.addWidget(self._detail_label)
        # Opens the "what is blocked" list.
        self._blocklist_button = _link_button("")
        self._blocklist_button.clicked.connect(self._open_blocklist_viewer)
        self._blocklist_button.installEventFilter(self)
        column.addWidget(self._blocklist_button)
        self._vacation_label = _VacationLine()
        self._vacation_label.clicked.connect(self._open_vacation_dialog)
        column.addWidget(self._vacation_label)
        column.addStretch(1)
        body.addLayout(column, 1)
        return body

    def _build_idle_actions(self):
        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        self._start_button = _button("▶  Start", _primary_style(self._color))
        self._start_button.clicked.connect(self._arm)
        layout.addWidget(self._start_button)
        self._busy_note = _note("", FAINT, 12)
        self._busy_note.setAlignment(Qt.AlignCenter)
        self._busy_note.hide()
        layout.addWidget(self._busy_note)
        return panel

    def _build_armed_panel(self):
        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        self._armed_top = QWidget()
        top = QHBoxLayout(self._armed_top)
        top.setContentsMargins(0, 0, 0, 0)
        top.setSpacing(8)
        self._burnout_button = _button("Until burnout", _accent_style(self._color))
        self._burnout_button.setObjectName("burnoutButton")
        self._burnout_button.clicked.connect(self._start_burnout)
        top.addWidget(self._burnout_button, 1)
        self._custom_button = _button("Custom time", _accent_style(self._color))
        self._custom_button.clicked.connect(self._show_custom)
        top.addWidget(self._custom_button, 1)
        layout.addWidget(self._armed_top)

        self._custom_row = QWidget()
        custom = QHBoxLayout(self._custom_row)
        custom.setContentsMargins(0, 0, 0, 0)
        custom.setSpacing(8)
        self._duration_edit = QLineEdit()
        self._duration_edit.setPlaceholderText("minutes")
        self._duration_edit.setAlignment(Qt.AlignCenter)
        self._duration_edit.setFixedSize(84, 40)
        self._duration_edit.setStyleSheet(
            f"QLineEdit{{border:1.5px solid {self._color};border-radius:12px;font-size:16px;font-weight:600;"
            f"color:{INK};background:#FFFFFF;}}")
        self._duration_edit.returnPressed.connect(self._start_task)
        custom.addWidget(self._duration_edit)
        custom.addWidget(_note("minutes"))
        custom.addStretch(1)
        self._custom_start_button = _button("▶  Start", _primary_style(self._color))
        self._custom_start_button.setObjectName("startTaskButton")
        self._custom_start_button.clicked.connect(self._start_task)
        custom.addWidget(self._custom_start_button)
        layout.addWidget(self._custom_row)
        self._custom_row.hide()

        self._pomodoro_button = _button("Pomodoro", _SECONDARY_STYLE)
        self._pomodoro_button.setObjectName("pomodoroButton")
        self._pomodoro_button.clicked.connect(self._start_pomodoro)
        layout.addWidget(self._pomodoro_button)

        self._cancel_button = _link_button("Cancel")
        self._cancel_button.clicked.connect(self._back_or_cancel)
        layout.addWidget(self._cancel_button, 0, Qt.AlignCenter)
        return panel

    def _build_running_panel(self):
        panel = QWidget()
        layout = QHBoxLayout(panel)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        self._pause_button = _button("Pause", _SECONDARY_STYLE)
        self._pause_button.clicked.connect(self._pause_resume)
        layout.addWidget(self._pause_button, 1)
        self._end_button = _button("End", _DANGER_STYLE)
        self._end_button.clicked.connect(self._end_task)
        layout.addWidget(self._end_button)
        return panel

    # --- card look ---

    def _apply_card_style(self, mode):
        """The card's frame: a hairline at rest, the task's color while its own session
        runs, dashed while waiting. Only restyled when the look actually changes."""
        key = (mode, self._color)
        if key == self._style_key:
            return
        self._style_key = key
        if mode == "live":
            frame = f"2px solid {self._color}"
        elif mode == "waiting":
            frame = "1px dashed #C9CED8"
        else:
            frame = "1px solid #E6E9EF"
        self.setStyleSheet(f"QFrame#TaskCard{{background:#FFFFFF;border:{frame};border-radius:18px;}}")

    def _set_chip(self, text, tone):
        if not text:
            self._state_chip.hide()
            return
        self._state_chip.setText(text)
        self._state_chip.setStyleSheet(
            f"background:{tone[0]};color:{tone[1]};border-radius:10px;padding:2px 10px;font-size:11.5px;font-weight:600;")
        self._state_chip.show()

    # --- state transitions ---

    def _arm(self):
        if self._is_locked_by_other_session():
            return
        self._armed = True
        self._custom = False
        self._duration_edit.setText(str(self._suggested_session_minutes()))
        self._show_panel()

    def _disarm(self):
        self._armed = False
        self._custom = False
        self._show_panel()

    def _show_custom(self):
        self._custom = True
        self._show_panel()
        self._duration_edit.setFocus()
        self._duration_edit.selectAll()

    def _back_or_cancel(self):
        if self._custom:
            self._custom = False
            self._show_panel()
        else:
            self._disarm()

    def _show_panel(self):
        """Which of the idle / armed areas shows, from the armed flag (the running panel
        is driven by update_dynamic)."""
        running = not self._running_panel.isHidden()
        self._idle_actions.setVisible(not self._armed and not running)
        self._armed_panel.setVisible(self._armed and not running)
        self._armed_top.setVisible(not self._custom)
        self._custom_row.setVisible(self._custom)
        self._cancel_button.setText("Back" if self._custom else "Cancel")

    def _open_editor(self):
        open_task_editor(self._task, on_saved=lambda _t: self._on_changed())

    def _blocklist_items(self):
        return list(self._task.get("processBlocklist", [])) + list(self._task.get("domainWhitelist", []))

    def _open_blocklist_viewer(self):
        from PySide6.QtWidgets import QPlainTextEdit
        dlg = QDialog(self.window())
        dlg.setWindowTitle(f"{self._task['name']} — Restrictions")
        dlg.setObjectName("PopupBg")
        dlg.setStyleSheet(_DIALOG_STYLE)
        dlg.setMinimumWidth(360)
        lay = QVBoxLayout(dlg)
        lay.setContentsMargins(24, 22, 24, 20)
        lay.setSpacing(8)
        box_style = ("QPlainTextEdit{background:#F7F8FA;border:1px solid #E6E9EF;border-radius:10px;"
                     f"padding:6px 8px;color:{INK};font-size:13px;}}")

        for heading, items, height in (
            ("Blocked apps", self._task.get("processBlocklist", []), 100),
            ("Allowed sites", self._task.get("domainWhitelist", []), 90),
        ):
            label = QLabel(heading)
            label.setStyleSheet(f"font-weight:600;font-size:13px;color:{INK};")
            lay.addWidget(label)
            view = QPlainTextEdit()
            view.setPlainText("\n".join(items) or "(none)")
            view.setFixedHeight(height)
            view.setReadOnly(True)
            view.setStyleSheet(box_style)
            lay.addWidget(view)

        close_btn = _button("Close", _SECONDARY_STYLE)
        close_btn.clicked.connect(dlg.accept)
        lay.addWidget(close_btn)
        dlg.exec()

    def _start_task(self):
        try:
            duration_minutes = float(self._duration_edit.text())
            if not session_manager.is_valid_duration(duration_minutes):
                raise ValueError
        except ValueError:
            return
        self._begin_session(duration_minutes, is_burnout=False)

    def _start_burnout(self):
        self._begin_session(tasks_store.BURNOUT_MINUTES, is_burnout=True)

    def _start_pomodoro(self):
        settings = _PomodoroDialog.get_settings(self)
        if settings is None:
            return
        focus_minutes, break_minutes, cycles = settings
        session_manager.start_pomodoro_session(
            focus_minutes,
            break_minutes,
            cycles,
            self._task.get("lockMode", "soft"),
            self._task.get("processBlocklist", []),
            self._task.get("domainWhitelist", []),
            source="task",
            event_id=self._task["id"],
            event_title=self._task["name"],
            hide_taskbar_badges=bool(self._task.get("hideTaskbarBadges")),
            stop_taskbar_flashing=bool(self._task.get("stopTaskbarFlashing")),
        )
        self._disarm()

    def _begin_session(self, duration_minutes, is_burnout):
        session_manager.start_session(
            duration_minutes,
            self._task.get("lockMode", "soft"),
            self._task.get("processBlocklist", []),
            self._task.get("domainWhitelist", []),
            source="task",
            event_id=self._task["id"],
            event_title=self._task.get("name") or "(untitled task)",
            is_burnout=is_burnout,
            hide_taskbar_badges=bool(self._task.get("hideTaskbarBadges")),
            stop_taskbar_flashing=bool(self._task.get("stopTaskbarFlashing")),
        )
        self._disarm()

    def _parked_entry(self, status):
        """This task's session waiting behind a different one, or None."""
        if status.get("isActive") and self._belongs_to_this_task(status):
            return None
        for parked in status.get("parkedSessions") or []:
            if self._belongs_to_this_task(parked):
                return parked
        return None

    def _pause_resume(self):
        parked = self._parked_entry(session_manager.get_status())
        if parked:
            session_manager.swap_with_parked(parked["parkId"])
            return
        if session_manager.get_status()["isPaused"]:
            session_manager.resume_session()
        else:
            session_manager.pause_session()

    def _end_task(self):
        parked = self._parked_entry(session_manager.get_status())
        if parked:
            session_manager.end_parked_session(park_id=parked["parkId"], end_type="manual")
            return
        session_manager.end_session(end_type="manual")

    def _toggle_review(self, row):
        if row["state"] == "running":
            review_store.pause_active_review()
        else:
            review_store.resume_active_review()

    # --- vacation ---

    def _cash_in_max(self):
        # Capped at how much is actually left to fill today's goal -- today's required
        # minutes (already reduced by whatever was cashed in) minus what was worked.
        # Cashing in more would burn vacation minutes for no visible effect.
        remaining = max(0, int(self._today_required_minutes()) - int(self._logged_minutes_today))
        return min(self._cash_in_balance_int, remaining)

    def _open_vacation_dialog(self):
        usable = self._cash_in_max()
        if usable <= 0 or self._is_running():
            return
        minutes = _VacationDialog.get_minutes(
            self._task.get("name") or "this task", self._color, self._cash_in_balance_int, usable,
            parent=self.window())
        if minutes is None:
            return
        self._confirm_cash_in(minutes)

    def _confirm_cash_in(self, minutes):
        cap = self._cash_in_max()
        if minutes <= 0 or minutes > cap:
            QMessageBox.warning(
                self, "Carmen Focus",
                f"Enter a whole number of minutes between 1 and {cap} (today's remaining time).")
            return
        sessions = session_history.load_all()
        try:
            self._task = tasks_store.cash_in(self._task["id"], date.today(), minutes, sessions)
        except ValueError as exc:
            QMessageBox.warning(self, "Carmen Focus", str(exc))
            return
        self._on_changed()

    # --- dynamic refresh (called every tick by TasksTab) ---

    def _today_required_minutes(self):
        """What is required for the goal's own stretch (today, or the week / month for a
        period goal), after vacation cashed in against it."""
        if self._snap is not None:
            return self._snap["required"]
        return tasks_store.required_minutes_for_date(self._task, date.today())

    def _suggested_session_minutes(self):
        required = int(self._today_required_minutes())
        if self._snap is not None and self._snap["isPeriod"]:
            # A whole week's total is no length for one sitting: offer what is left, up to an hour.
            remaining = max(0, required - int(self._logged_minutes_today))
            return max(1, min(remaining or 60, 60))
        return required

    def _belongs_to_this_task(self, status):
        # "review" sessions (a review timer started against this task's linked topic,
        # see qt_ui/review_tab.py's _begin_review) run under this task's eventId too --
        # without treating them as "this task's session" the card would read as locked
        # by some other session for the whole review.
        return status.get("source") in ("task", "review") and status.get("eventId") == self._task["id"]

    def _is_locked_by_other_session(self):
        status = session_manager.get_status()
        if not status["isActive"]:
            return False
        if self._belongs_to_this_task(status):
            return False
        # A paused session doesn't block starting a different task: it just waits.
        if status["isPaused"]:
            return False
        # This task's own session may be one of the waiting ones.
        return self._parked_entry(status) is None

    def _is_running(self):
        status = session_manager.get_status()
        return bool(status.get("isActive") and self._belongs_to_this_task(status))

    def update_dynamic(self, status, sessions, reviews=None):
        today = date.today()
        snap = tasks_store.goal_snapshot(self._task, today, sessions, live_status=status)
        self._snap = snap
        required = snap["required"]
        logged_minutes = snap["logged"]
        self._logged_minutes_today = logged_minutes

        # Cashed-in vacation counts as if worked, against the ORIGINAL target --
        # cashing in visibly fills the ring instead of just shrinking the goal.
        today_cashed = snap["cashed"]
        display_required = required + today_cashed
        display_logged = logged_minutes + today_cashed
        fraction = 0.0 if display_required <= 0 else display_logged / display_required

        balance = tasks_store.vacation_balance_minutes(self._task, sessions)
        # Round, not truncate: 0.97m reads as "1m" and the amount you can spend must match.
        self._cash_in_balance_int = int(round(balance))
        today_surplus = max(0.0, logged_minutes - required) if required > 0 else 0.0
        display_balance = balance + today_surplus

        is_running = bool(status.get("isActive") and self._belongs_to_this_task(status))
        parked = self._parked_entry(status)
        is_waiting = parked is not None
        locked_by_other = self._is_locked_by_other_session()
        view = status if is_running else {**(parked or {}), "isActive": True, "isPaused": True}

        if is_running or is_waiting:
            self._armed = False
            self._custom = False
            self._update_session_view(view, is_waiting, fraction, display_logged, display_required, reviews)
        else:
            self._update_idle_view(fraction, display_logged, display_required, locked_by_other, status, reviews)

        # The vacation line and restrictions stay quiet while a session is on screen.
        in_session = is_running or is_waiting
        self._vacation_label.setVisible(not in_session)
        self._blocklist_button.setVisible(not in_session)
        if not in_session:
            usable = self._cash_in_max() > 0
            self._vacation_label.set_state(
                f"{_format_minutes(display_balance)} vacation" if display_balance >= 0.5 else "No vacation banked",
                usable)

        if self.property("locked") != locked_by_other:
            self.setProperty("locked", locked_by_other)
        self.goal_state = "none" if display_required <= 0 else ("reached" if fraction >= 1 else "open")

    def _restrictions_text(self):
        """(variants, clickable): the longest wording first, shorter ones for a narrow card."""
        apps = len(self._task.get("processBlocklist") or [])
        sites = len(self._task.get("domainWhitelist") or [])
        if not (apps or sites):
            return ["No restrictions set", "No rules"], False
        a = f"{apps} app{'s' if apps != 1 else ''}"
        t = f"{sites} site{'s' if sites != 1 else ''}"
        return [f"{a} blocked · {t} ok", f"{a} · {t}", f"{apps} · {sites}"], True

    def _set_restrictions(self, variants, clickable):
        self._restrictions_variants = variants
        self._blocklist_button.setEnabled(clickable)
        self._blocklist_button.setToolTip(variants[0] if len(variants) > 1 else "")
        self._fit_restrictions()

    def _fit_restrictions(self):
        """Shows the longest wording that fits the button; never cuts a word off."""
        variants = self._restrictions_variants
        if not variants:
            return
        button = self._blocklist_button
        room = button.width() - 14  # the link style's side padding
        fm = button.fontMetrics()
        chosen = variants[0]  # until the column has a real width, trust the full wording
        if self._restrictions_laid_out and room > 40:
            for text in variants:
                if fm.horizontalAdvance(text) <= room:
                    chosen = text
                    break
            else:
                chosen = fm.elidedText(variants[-1], Qt.ElideRight, room)
        if button.text() != chosen:
            button.setText(chosen)

    def _update_idle_view(self, fraction, logged, required, locked_by_other, status, reviews):
        self._apply_card_style("idle")
        self._set_chip("", None)
        self._violation_chip.hide()
        self._dot.setStyleSheet(f"background:{'#D0D5DD' if locked_by_other else self._color};border-radius:6px;")
        self._name_label.setStyleSheet(
            f"font-size:17px;font-weight:700;color:{'#8A93A3' if locked_by_other else INK};background:transparent;")
        snap = self._snap or {"isPeriod": False, "phrase": "today", "daysLeft": 1}
        phrase = snap["phrase"]
        period = snap["isPeriod"]
        scheduled = required > 0 or period
        done = required > 0 and fraction >= 1
        ring_color = "#B8BFCC" if locked_by_other else self._color
        detail = ""
        if not scheduled:
            self._ring.set_state(ring_color, 0.0, _format_minutes(logged), "logged today", dim=locked_by_other)
            headline = "Not scheduled today"
        elif required <= 0:
            self._ring.set_state(ring_color, 0.0, _format_minutes(logged), f"logged {phrase}", dim=locked_by_other)
            headline = "No target set"
        elif done:
            over = int(round(logged - required))
            self._ring.set_state(ring_color, 1.0, "Done", f"{_format_minutes(logged)} logged", dim=locked_by_other)
            headline = "Goal reached" if over <= 0 else f"+{over} min over"
            if period:
                detail = _days_text(snap["daysLeft"], "left in the period")
        else:
            self._ring.set_state(
                ring_color, fraction, _format_minutes(logged), f"of {_format_minutes(required)} {phrase}",
                dim=locked_by_other)
            left = max(0, int(round(required - logged)))
            headline = f"{_format_minutes(left)} to go" if period else f"{left} min to go"
            if period:
                days = max(1, snap["daysLeft"])
                pace = _format_minutes(math.ceil(left / days))
                detail = "Due today" if days == 1 else f"{_days_text(days, 'left')} · about {pace} a day"
        self._countdown_label.setText(headline)
        self._countdown_label.setStyleSheet(
            f"font-size:15px;font-weight:600;color:{'#8A93A3' if locked_by_other else INK};background:transparent;")
        self._detail_label.setText(detail)
        self._detail_label.setVisible(bool(detail))
        self._set_restrictions(*self._restrictions_text())

        self._running_panel.hide()
        self._start_button.setText("▶  Start again" if done else "▶  Start")
        self._start_button.setEnabled(not locked_by_other)
        self._busy_note.setVisible(bool(locked_by_other))
        if locked_by_other:
            self._busy_note.setText(f"{status.get('eventTitle') or 'Another session'} is running")
            self._armed = False
            self._custom = False
        self._show_panel()
        self._review_box.set_rows(reviews or [], self._toggle_review, bool(status.get("isBreak")))

    def _update_session_view(self, view, is_waiting, fraction, logged, required, reviews):
        self._dot.setStyleSheet(f"background:{self._color};border-radius:6px;")
        self._name_label.setStyleSheet(f"font-size:17px;font-weight:700;color:{INK};background:transparent;")
        paused = bool(view.get("isPaused"))
        on_break = bool(view.get("isBreak"))
        review_problem = view.get("source") == "review" and view.get("reviewProblemName")
        violations = view.get("violationCount", 0)
        pomo = view.get("pomodoro")
        elapsed = tasks_store.worked_seconds(
            view.get("startTime"), None, view.get("violationLog")) if view.get("startTime") else 0
        elapsed_m, elapsed_s = divmod(elapsed, 60)

        ring_color = _BREAK_COLOR if on_break else self._color
        if review_problem or view.get("isBurnout"):
            # A stopwatch, not a timer: no fixed duration to count down to.
            big, small = _format_clock(elapsed), "elapsed"
            ring_fraction = fraction
            if review_problem:
                headline = f"{review_problem} · {elapsed_m}m {elapsed_s}s elapsed"
            else:
                headline = f"Until burnout · {elapsed_m}m {elapsed_s}s elapsed"
        else:
            remaining = view.get("secondsRemaining", 0)
            rem_m, rem_s = divmod(remaining, 60)
            if pomo:
                phase_minutes = pomo["breakMinutes"] if on_break else pomo["focusMinutes"]
                label = "Break" if on_break else "Focus"
                headline = f"{label} {pomo['currentCycle']}/{pomo['totalCycles']} · {rem_m}m {rem_s}s remaining"
                total = phase_minutes * 60
            else:
                headline = f"{rem_m}m {rem_s}s remaining"
                total = elapsed + remaining
            big, small = _format_clock(remaining), "break" if on_break else "remaining"
            ring_fraction = 0.0 if total <= 0 else 1 - remaining / total
        if paused:
            small = "paused"
        if is_waiting:
            headline = "Waiting while another task runs"
        self._countdown_label.setText(headline)
        self._countdown_label.setStyleSheet(f"font-size:15px;font-weight:600;color:{INK};background:transparent;")
        self._ring.set_state(ring_color, ring_fraction, big, small, dim=paused or is_waiting)

        phrase = self._snap["phrase"] if self._snap else "today"
        detail = (f"{_format_minutes(logged)} of {_format_minutes(required)} {phrase}" if required > 0
                  else f"{_format_minutes(logged)} logged {phrase}")
        if is_waiting:
            detail = "Time is frozen. Nothing is blocked for this task."
        elif on_break:
            detail = "Nothing is blocked during the break."
        self._detail_label.setText(detail)
        self._detail_label.show()

        if violations:
            self._violation_chip.setText(f"{violations} violation{'s' if violations != 1 else ''}")
            self._violation_chip.show()
        else:
            self._violation_chip.hide()

        if is_waiting:
            self._apply_card_style("waiting")
            self._set_chip("Waiting", _GREY)
            self._pause_button.setText("Switch to this")
            self._pause_button.setStyleSheet(_primary_style(self._color))
        else:
            self._apply_card_style("live")
            if review_problem and not paused:
                self._set_chip("Reviewing", _GREEN)
            elif on_break:
                self._set_chip("Break", _TEAL)
            elif paused:
                self._set_chip("Paused", _AMBER)
            else:
                self._set_chip("Running", _GREEN)
            self._pause_button.setText("Resume" if paused else "Pause")
            self._pause_button.setStyleSheet(_primary_style(self._color) if paused else _SECONDARY_STYLE)

        self._idle_actions.hide()
        self._armed_panel.hide()
        self._running_panel.show()
        self._review_box.set_rows(reviews or [], self._toggle_review, on_break)
