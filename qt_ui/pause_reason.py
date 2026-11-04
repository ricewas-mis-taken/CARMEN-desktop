"""Pausing asks what the pause is for, with one tap. Break, interrupted and stuck are free; checking something
else comes out of a small budget (see pause_budget), so a pause can't quietly turn into an escape."""
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

import session_manager
from qt_ui.ui_kit import INK, MUTED

_OPEN = []

_CHOICES = (
    ("break", "Break", "Free"),
    ("interrupted", "Interrupted", "Free"),
    ("stuck", "Stuck", "Free. Tell me what is blocking you"),
    ("checking", "Checking something", "Uses your pause budget"),
)


def ask(anchor, done=None, on_stuck=None):
    """Shows the reasons. Choosing one pauses the session with it, then calls `done()`.
    Closing the popup without choosing leaves the session running."""
    if session_manager.get_status().get("isPaused"):
        if done:
            done()
        return None
    popup = _Popup(done, on_stuck)
    _OPEN.append(popup)
    popup.show()
    return popup


class _Popup(QWidget):
    def __init__(self, done, on_stuck):
        super().__init__(None, Qt.Tool | Qt.WindowStaysOnTopHint)
        self.setObjectName("PopupBg")
        self.setWindowTitle("Pause")
        self._done, self._on_stuck = done, on_stuck
        layout = QVBoxLayout(self)
        layout.setContentsMargins(22, 20, 22, 18)
        layout.setSpacing(8)
        title = QLabel("What is this pause for?")
        title.setStyleSheet(f"color:{INK};font-size:18px;font-weight:700;background:transparent;")
        layout.addWidget(title)
        for key, label, note in _CHOICES:
            button = QPushButton(f"{label}")
            button.setCursor(Qt.PointingHandCursor)
            button.setStyleSheet(
                f"QPushButton{{background:#FFFFFF;color:{INK};border:1px solid #E6E9EF;border-radius:12px;"
                "padding:10px 14px;font-size:14px;font-weight:600;text-align:left;}"
                "QPushButton:hover{background:#F1EEFF;border-color:#8B7BE8;}")
            button.clicked.connect(lambda _=False, k=key: self._choose(k))
            row = QHBoxLayout()
            row.setSpacing(10)
            row.addWidget(button, 1)
            hint = QLabel(note)
            hint.setStyleSheet(f"color:{MUTED};font-size:12px;background:transparent;")
            hint.setMinimumWidth(150)
            row.addWidget(hint)
            layout.addLayout(row)
        self.resize(480, 280)

    def _choose(self, key):
        session_manager.pause_session(key)
        self.close()
        if key == "stuck" and self._on_stuck:
            self._on_stuck()
        if self._done:
            self._done()

    def closeEvent(self, event):
        if self in _OPEN:
            _OPEN.remove(self)
        super().closeEvent(event)
