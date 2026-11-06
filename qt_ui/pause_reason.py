"""Pausing asks what the pause is for. Break, interrupted and stuck are free; checking something else comes
out of a small budget (see pause_budget), so a pause can't quietly turn into an escape. The popup shows
that budget as it stands, lets the reason be explained in a line, and pauses on one button."""
from PySide6.QtCore import QTimer, Qt
from PySide6.QtWidgets import (
    QButtonGroup, QFrame, QHBoxLayout, QLabel, QLineEdit, QPushButton, QVBoxLayout, QWidget,
)

import pause_budget
import session_manager
from qt_ui.ui_kit import INK, MUTED

_OPEN = []

_CHOICES = (
    ("break", "Break", "Free"),
    ("interrupted", "Interrupted", "Free"),
    ("stuck", "Stuck", "Free, and I'll ask what is blocking you"),
    ("checking", "Checking something else", "Comes out of your pause budget"),
)
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


class _Popup(QWidget):
    def __init__(self, done, on_stuck, status):
        super().__init__(None, Qt.Tool | Qt.WindowStaysOnTopHint)
        self.setObjectName("PopupBg")
        self.setWindowTitle("Pause")
        self.setMinimumWidth(460)
        self._done, self._on_stuck = done, on_stuck
        self._key = None
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 22, 24, 20)
        layout.setSpacing(12)

        title = QLabel("Pause the timer")
        title.setStyleSheet(f"color:{INK};font-size:20px;font-weight:700;background:transparent;")
        layout.addWidget(title)

        self._group = QButtonGroup(self)
        self._group.setExclusive(True)
        self._choices = {}
        for key, label, note in _CHOICES:
            button = QPushButton(label)
            button.setCheckable(True)
            button.setCursor(Qt.PointingHandCursor)
            button.setStyleSheet(
                f"QPushButton{{background:#FFFFFF;color:{INK};border:1px solid #E6E9EF;border-radius:12px;"
                "padding:10px 14px;font-size:14px;font-weight:600;text-align:left;}"
                "QPushButton:hover{background:#F6F4FF;border-color:#B9AEF2;}"
                "QPushButton:checked{background:#F1EEFF;border:2px solid #8B7BE8;}")
            button.clicked.connect(lambda _=False, k=key: self._pick(k))
            self._group.addButton(button)
            self._choices[key] = button
            row = QHBoxLayout()
            row.setSpacing(10)
            row.addWidget(button, 1)
            hint = QLabel(note)
            hint.setWordWrap(True)
            hint.setStyleSheet(f"color:{MUTED};font-size:12px;background:transparent;")
            hint.setFixedWidth(170)
            row.addWidget(hint)
            layout.addLayout(row)

        self._note = QLineEdit()
        self._note.setPlaceholderText("Say why, in a few words")
        self._note.setMaxLength(200)
        self._note.setStyleSheet(
            f"QLineEdit{{background:#FFFFFF;color:{INK};border:1px solid #E6E9EF;border-radius:10px;"
            "padding:9px 12px;font-size:14px;}QLineEdit:focus{border-color:#8B7BE8;}")
        self._note.textChanged.connect(self._sync)
        self._note.returnPressed.connect(self._go)
        layout.addWidget(self._note)

        self._hint = QLabel("")
        self._hint.setStyleSheet(f"color:{MUTED};font-size:12px;background:transparent;")
        layout.addWidget(self._hint)

        divider = QFrame()
        divider.setFixedHeight(1)
        divider.setStyleSheet("background:#E6E9EF;")
        layout.addWidget(divider)

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        cancel = QPushButton("Keep working")
        cancel.setCursor(Qt.PointingHandCursor)
        cancel.setStyleSheet(
            f"QPushButton{{background:transparent;color:{MUTED};border:none;padding:9px 14px;font-size:14px;}}"
            f"QPushButton:hover{{color:{INK};}}")
        cancel.clicked.connect(self.close)
        buttons.addWidget(cancel)
        self._go_button = QPushButton("Pause")
        self._go_button.setCursor(Qt.PointingHandCursor)
        self._go_button.setStyleSheet(
            "QPushButton{background:#1F2328;color:#FFFFFF;border:none;border-radius:10px;padding:9px 22px;"
            "font-size:14px;font-weight:700;}QPushButton:hover{background:#2F353D;}"
            "QPushButton:disabled{background:#D5D9E0;color:#8A919C;}")
        self._go_button.clicked.connect(self._go)
        buttons.addWidget(self._go_button)
        layout.addLayout(buttons)
        self._sync()

    def _pick(self, key):
        self._key = key
        self._sync()
        self._note.setFocus()

    def _choose(self, key, note=""):
        """Picks a reason, writes the note and presses Pause (what a click, a line of text and Enter do)."""
        self._choices[key].setChecked(True)
        self._pick(key)
        self._note.setText(note)
        self._go()

    def _sync(self, *_):
        needs_note = self._key == "checking"
        self._note.setPlaceholderText("What are you checking?" if needs_note else "Say why, in a few words (optional)")
        missing = self._key is None or (needs_note and not self._note.text().strip())
        self._go_button.setEnabled(not missing)
        self._hint.setText("Pick a reason." if self._key is None else
                           "Say what you are checking." if missing else "")

    def _go(self):
        if not self._go_button.isEnabled():
            return
        key = self._key
        note = self._note.text().strip() or None
        try:
            session_manager.pause_session(key, note)
        except TypeError:                     # a session_manager from before notes existed (not restarted yet)
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
