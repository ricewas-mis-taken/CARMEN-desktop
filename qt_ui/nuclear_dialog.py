"""Qt port of tray.py's nuclear-end reason dialog. Nuclear-ending
mid-session is a deliberate, disruptive act -- this asks why before it
happens so the reason lands in session_history.json alongside it."""
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget
from qt_ui.wrap_edit import WrapEdit

import session_manager

_open_windows = set()


def open_nuclear_reason_dialog(icon, format_end_summary):
    """icon: the pystray Icon (for .notify()/.update_menu()).
    format_end_summary: tray.format_end_summary, passed in rather than
    imported to avoid a qt_ui -> tray import cycle (tray.py is what opens
    this dialog in the first place)."""
    win = _NuclearReasonDialog(icon, format_end_summary)
    _open_windows.add(win)
    win.destroyed.connect(lambda: _open_windows.discard(win))
    win.show()


class _NuclearReasonDialog(QWidget):
    def __init__(self, icon, format_end_summary):
        super().__init__(None, Qt.WindowStaysOnTopHint)
        self.setObjectName("PopupBg")
        self.setWindowTitle("Carmen Focus — Nuclear End")
        self.resize(360, 180)
        self._icon = icon
        self._format_end_summary = format_end_summary

        layout = QVBoxLayout(self)

        prompt = QLabel("Why are you ending this session early?")
        prompt.setWordWrap(True)
        prompt.setAlignment(Qt.AlignCenter)
        layout.addWidget(prompt)

        self._reason_edit = WrapEdit()
        layout.addWidget(self._reason_edit)

        self._status_label = QLabel()
        self._status_label.setStyleSheet("color: #c62828;")
        self._status_label.setAlignment(Qt.AlignCenter)
        layout.addWidget(self._status_label)

        button_row = QHBoxLayout()
        confirm_button = QPushButton("End Session (Nuclear)")
        cancel_button = QPushButton("Cancel")
        confirm_button.clicked.connect(self._confirm)
        cancel_button.clicked.connect(self.close)
        button_row.addStretch(1)
        button_row.addWidget(confirm_button)
        button_row.addWidget(cancel_button)
        button_row.addStretch(1)
        layout.addLayout(button_row)

        self._reason_edit.returnPressed.connect(self._confirm)
        self._reason_edit.setFocus()

    def _confirm(self):
        reason = self._reason_edit.text().strip()
        if not reason:
            self._status_label.setText("Enter a reason before ending.")
            return
        summary = session_manager.end_session(end_type="nuclear", reason=reason)
        self._icon.notify(self._format_end_summary(summary), title="Carmen Focus")
        self._icon.update_menu()
        self.close()
