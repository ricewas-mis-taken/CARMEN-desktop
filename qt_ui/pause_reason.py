"""Pausing: one big Pause button (what it was is asked when you resume), or one tap on a reason. Break,
interrupted and stuck are free; checking something else comes out of a small budget (see pause_budget), so a
pause can't quietly turn into an escape. The budget itself shows on the task card and the mini timer."""
from PySide6.QtCore import QRectF, QTimer, Qt
from PySide6.QtGui import QColor, QFont, QPainter, QPen
from PySide6.QtWidgets import (
    QGridLayout, QHBoxLayout, QLabel, QLineEdit, QPushButton, QSpinBox, QVBoxLayout, QWidget,
)

import pause_budget
import session_manager
from qt_ui.ui_kit import INK, MUTED

_OPEN = []

_AMBER = "#B7791F"
_GREEN = "#2F9E44"
_RED = "#D92D20"


def clock(seconds):
    seconds = max(0, int(seconds))
    return f"{seconds // 60}:{seconds % 60:02d}"


EXPLAIN = ("Pause budget: time you can spend paused on 'Checking something else'. Break, interrupted and "
           "stuck pauses are free. Past the budget, every 3 minutes costs a point.")


def budget_note(status):
    """(short line, colour) for the running session's pause budget, or None with no session."""
    live = pause_budget.live(status)
    if not live:
        return None
    if live["over_seconds"]:
        return f"Pause budget used up, {clock(live['over_seconds'])} over", _RED
    low = live["left_seconds"] < live["budget_seconds"] * 0.3
    return f"Pause budget: {clock(live['left_seconds'])} left", _AMBER if low else _GREEN


def ask(anchor, done=None, on_stuck=None):
    """Shows the reasons. Pressing Pause pauses the session with the one picked, then calls `done()`.
    Closing the popup without pausing leaves the session running."""
    status = session_manager.get_status()
    if status.get("isPaused"):
        if done:
            done()
        return None
    popup = _Popup(done, on_stuck, status)
    _OPEN.append(popup)
    popup.show()
    popup.raise_()
    return popup


_CHIP = ("QPushButton{background:#F4F5F8;color:%s;border:none;border-radius:14px;padding:9px 14px;"
         "font-size:13.5px;font-weight:600;}QPushButton:hover{background:#E7E3FB;}"
         "QPushButton:checked{background:#E7E3FB;border:1.5px solid #8B7BE8;}")
_MINUTE_CHIPS = (15, 30, 60, 90)
_TIPS = {
    "break": "A real rest. Free.",
    "long_break": "A rest you set a length for. Free for that long, then it counts as checking.",
    "interrupted": "Something outside your control. Free.",
    "stuck": "You are blocked on the work. Free, and Carmen asks what is blocking you.",
    "checking": "Looking at something else. Comes out of your pause budget.",
}


class _Popup(QWidget):
    """One big Pause button (what it was is asked when you resume), and small reason chips for saying it
    now: a chip pauses with one tap. Only a long break (how long) and checking (what) need one more bit."""

    def __init__(self, done, on_stuck, status):
        super().__init__(None, Qt.Tool | Qt.WindowStaysOnTopHint)
        self.setObjectName("PopupBg")
        self.setWindowTitle("Pause")
        self.setFixedWidth(380)
        self._done, self._on_stuck = done, on_stuck
        self._key = None
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 22, 24, 18)
        layout.setSpacing(10)

        title = QLabel("Pause the timer")
        title.setStyleSheet(f"color:{INK};font-size:20px;font-weight:700;background:transparent;")
        layout.addWidget(title)

        self._go_button = QPushButton("Pause")
        self._go_button.setCursor(Qt.PointingHandCursor)
        self._go_button.setMinimumHeight(46)
        self._go_button.setToolTip("Pause now. Carmen asks what it was when you resume.")
        self._go_button.setStyleSheet(
            "QPushButton{background:#1F2328;color:#FFFFFF;border:none;border-radius:14px;"
            "font-size:15px;font-weight:700;}QPushButton:hover{background:#2F353D;}")
        self._go_button.clicked.connect(lambda: self._choose("later"))
        layout.addWidget(self._go_button)

        hint = QLabel("Or say why now")
        hint.setStyleSheet(f"color:{MUTED};font-size:12.5px;background:transparent;margin-top:4px;")
        layout.addWidget(hint)

        grid = QGridLayout()
        grid.setHorizontalSpacing(8)
        grid.setVerticalSpacing(8)
        self._choices = {}
        for index, (key, label) in enumerate((("break", "Break"), ("long_break", "Long break"),
                                              ("interrupted", "Interrupted"), ("stuck", "Stuck"),
                                              ("checking", "Checking something"))):
            chip = QPushButton(label)
            chip.setCheckable(key in ("long_break", "checking"))
            chip.setCursor(Qt.PointingHandCursor)
            chip.setToolTip(_TIPS[key])
            chip.setStyleSheet(_CHIP % INK)
            chip.clicked.connect(lambda _=False, k=key: self._tap(k))
            self._choices[key] = chip
            grid.addWidget(chip, index // 2, index % 2)
        layout.addLayout(grid)

        # Long break: how long, one tap. A custom number is there too.
        self._minutes_row = QWidget()
        mrow = QHBoxLayout(self._minutes_row)
        mrow.setContentsMargins(0, 0, 0, 0)
        mrow.setSpacing(6)
        for quick in _MINUTE_CHIPS:
            chip = QPushButton(f"{quick} min")
            chip.setCursor(Qt.PointingHandCursor)
            chip.setStyleSheet(_CHIP % INK)
            chip.clicked.connect(lambda _=False, m=quick: self._choose("long_break", minutes=m))
            mrow.addWidget(chip)
        self._minutes = QSpinBox()
        self._minutes.setRange(1, pause_budget.LONG_BREAK_MAX_MINUTES)
        self._minutes.setValue(30)
        self._minutes.setSuffix(" min")
        self._minutes.setStyleSheet(
            f"QSpinBox{{background:#FFFFFF;color:{INK};border:1px solid #E6E9EF;border-radius:10px;"
            "padding:6px 8px;font-size:13px;}")
        self._minutes.setToolTip("Another length, then press Enter")
        self._minutes.lineEdit().returnPressed.connect(lambda: self._choose("long_break"))
        mrow.addWidget(self._minutes)
        self._minutes_row.hide()
        layout.addWidget(self._minutes_row)

        # Checking: what, then Pause.
        self._note_row = QWidget()
        nrow = QHBoxLayout(self._note_row)
        nrow.setContentsMargins(0, 0, 0, 0)
        nrow.setSpacing(8)
        self._note = QLineEdit()
        self._note.setMaxLength(200)
        self._note.setPlaceholderText("What are you checking?")
        self._note.setStyleSheet(
            f"QLineEdit{{background:#FFFFFF;color:{INK};border:1px solid #E6E9EF;border-radius:10px;"
            "padding:9px 12px;font-size:13.5px;}QLineEdit:focus{border-color:#8B7BE8;}")
        self._note.returnPressed.connect(lambda: self._choose("checking", self._note.text()))
        nrow.addWidget(self._note, 1)
        confirm = QPushButton("Pause")
        confirm.setCursor(Qt.PointingHandCursor)
        confirm.setStyleSheet(
            "QPushButton{background:#1F2328;color:#FFFFFF;border:none;border-radius:10px;padding:9px 16px;"
            "font-size:13.5px;font-weight:700;}QPushButton:hover{background:#2F353D;}")
        confirm.clicked.connect(lambda: self._choose("checking", self._note.text()))
        nrow.addWidget(confirm)
        self._note_row.hide()
        layout.addWidget(self._note_row)

        cancel = QPushButton("Keep working")
        cancel.setCursor(Qt.PointingHandCursor)
        cancel.setStyleSheet(
            f"QPushButton{{background:transparent;color:{MUTED};border:none;padding:6px;font-size:13px;}}"
            f"QPushButton:hover{{color:{INK};}}")
        cancel.clicked.connect(self.close)
        layout.addWidget(cancel, 0, Qt.AlignHCenter)

    def _tap(self, key):
        """A chip was pressed: most pause right away; the two that need a detail show it first."""
        if key in ("long_break", "checking"):
            self._reveal(key)
        else:
            self._choose(key)

    def _reveal(self, key):
        self._key = key
        self._minutes_row.setVisible(key == "long_break")
        self._note_row.setVisible(key == "checking")
        for k in ("long_break", "checking"):
            self._choices[k].setChecked(k == key)
        if key == "checking":
            self._note.setFocus()
        self.adjustSize()

    def _choose(self, key, note="", minutes=None):
        """Pauses with this reason. Checking needs a word on what; without one it shows the box and waits."""
        note = (note or "").strip()
        if key == "checking" and not note:
            self._reveal("checking")
            return
        minutes = (minutes or self._minutes.value()) if key == "long_break" else None
        try:
            session_manager.pause_session(key, note or None, minutes)
        except TypeError:                     # a session_manager from before notes/long breaks (not restarted yet)
            session_manager.pause_session(key)
        self.close()
        if key == "stuck" and self._on_stuck:
            self._on_stuck()
        if self._done:
            self._done()

    def keyPressEvent(self, event):
        if event.key() == Qt.Key_Escape:
            self.close()
            return
        super().keyPressEvent(event)

    def closeEvent(self, event):
        if self in _OPEN:
            _OPEN.remove(self)
        super().closeEvent(event)


class BudgetRing(QWidget):
    """A small ring that drains as the pause budget is used, with the minutes left inside. Hover for what it is."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(38, 38)
        self._fraction, self._text, self._colour = 1.0, "", _GREEN
        self.hide()

    def update_from(self, status):
        live = pause_budget.live(status)
        if not live:
            self.hide()
            return
        over = live["over_seconds"] > 0
        self._fraction = 1.0 if over else live["left_seconds"] / max(1, live["budget_seconds"])
        self._text = "!" if over else str(max(0, live["left_seconds"]) // 60 + (1 if live["left_seconds"] % 60 else 0))
        self._colour = _RED if over else (_AMBER if self._fraction < 0.3 else _GREEN)
        note = budget_note(status)
        self.setToolTip(f"{note[0]}\n{EXPLAIN}" if note else EXPLAIN)
        self.show()
        self.update()

    def paintEvent(self, _event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        rect = QRectF(self.rect()).adjusted(4, 4, -4, -4)
        p.setPen(QPen(QColor(128, 128, 128, 55), 3.5))
        p.drawEllipse(rect)
        p.setPen(QPen(QColor(self._colour), 3.5, Qt.SolidLine, Qt.RoundCap))
        p.drawArc(rect, 90 * 16, -int(self._fraction * 360 * 16))
        font = QFont(self.font())
        font.setPixelSize(13)
        font.setWeight(QFont.DemiBold)
        p.setFont(font)
        p.setPen(QColor(self._colour))
        p.drawText(self.rect(), Qt.AlignCenter, self._text)
