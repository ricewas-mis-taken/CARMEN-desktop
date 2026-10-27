"""A yes/no question in the same look as every other popup.

QMessageBox takes its colours from the operating system. On a computer set to dark mode
that gave a dark box with the app's dark text on it, which read as an empty popup. This
one is drawn by the app, so it looks the same everywhere.
"""
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QDialog, QHBoxLayout, QPushButton, QVBoxLayout

from qt_ui.ui_kit import accent, danger, dialog_title, role_label


class ConfirmDialog(QDialog):
    def __init__(self, parent, title, text, yes="OK", no="Cancel", risky=False):
        super().__init__(parent, Qt.Dialog | Qt.WindowStaysOnTopHint)
        self.setObjectName("PopupBg")
        self.setWindowTitle(title)
        self.setModal(True)
        self.setMinimumWidth(420)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 22, 24, 20)
        layout.setSpacing(10)
        layout.addWidget(dialog_title(title))
        layout.addWidget(role_label(text, "subtitle", wrap=True))
        layout.addSpacing(8)
        row = QHBoxLayout()
        row.addStretch(1)
        self.no_button = QPushButton(no)
        self.no_button.clicked.connect(self.reject)
        row.addWidget(self.no_button)
        self.yes_button = (danger if risky else accent)(QPushButton(yes))
        self.yes_button.clicked.connect(self.accept)
        self.yes_button.setDefault(True)
        row.addWidget(self.yes_button)
        layout.addLayout(row)


def ask(parent, title, text, yes="OK", no="Cancel", risky=False):
    """True if the person pressed the yes button."""
    return ConfirmDialog(parent, title, text, yes, no, risky).exec() == QDialog.Accepted
