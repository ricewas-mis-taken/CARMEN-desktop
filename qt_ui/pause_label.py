"""After a pause made with "Decide later" ends, asks what it was. One tap; the answer is written onto that pause,
so only "Checking something" counts against the pause budget. Closing it unanswered leaves the pause as it was."""
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

import session_manager
from qt_ui.ui_kit import INK, MUTED

_OPEN = []

_CHOICES = (
    ("break", "Break", "Free"),
    ("interrupted", "Interrupted", "Free"),
    ("stuck", "Stuck", "Free"),
    ("checking", "Checking something", "Uses your pause budget"),
)


def length_text(seconds):
    minutes = max(1, round(seconds / 60))
    if minutes < 60:
        return f"{minutes} minute{'s' if minutes != 1 else ''}"
    hours, rest = divmod(minutes, 60)
    return f"{hours} h {rest} min" if rest else f"{hours} h"


def ask(timestamp, seconds, done=None):
    popup = _Popup(timestamp, seconds, done)
    _OPEN.append(popup)
    popup.show()
    popup.raise_()
    return popup


class _Popup(QWidget):
    def __init__(self, timestamp, seconds, done):
        super().__init__(None, Qt.Tool | Qt.WindowStaysOnTopHint)
        self.setObjectName("PopupBg")
        self.setWindowTitle("What was that pause?")
        self.setMinimumWidth(420)
        self._timestamp, self._done = timestamp, done
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 22, 24, 20)
        layout.setSpacing(10)
        title = QLabel(f"That pause was {length_text(seconds)}")
        title.setStyleSheet(f"color:{INK};font-size:19px;font-weight:700;background:transparent;")
        layout.addWidget(title)
        ask_label = QLabel("What was it?")
        ask_label.setStyleSheet(f"color:{MUTED};font-size:13px;background:transparent;")
        layout.addWidget(ask_label)
        self._buttons = {}
        for key, label, hint in _CHOICES:
            row = QHBoxLayout()
            row.setSpacing(10)
            button = QPushButton(label)
            button.setCursor(Qt.PointingHandCursor)
            button.setStyleSheet(
                f"QPushButton{{background:#FFFFFF;color:{INK};border:1px solid #E6E9EF;border-radius:12px;"
                "padding:10px 14px;font-size:14px;font-weight:600;text-align:left;}"
                "QPushButton:hover{background:#F1EEFF;border-color:#B9AEF2;}")
            button.clicked.connect(lambda _=False, k=key: self._pick(k))
            self._buttons[key] = button
            row.addWidget(button, 1)
            note = QLabel(hint)
            note.setFixedWidth(150)
            note.setStyleSheet(f"color:{MUTED};font-size:12px;background:transparent;")
            row.addWidget(note)
            layout.addLayout(row)

    def _pick(self, key):
        session_manager.label_pause(self._timestamp, key)
        self.close()
        if self._done:
            self._done(key)

    def keyPressEvent(self, event):
        if event.key() == Qt.Key_Escape:
            self.close()
            return
        super().keyPressEvent(event)

    def closeEvent(self, event):
        if self in _OPEN:
            _OPEN.remove(self)
        super().closeEvent(event)
