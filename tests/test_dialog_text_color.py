"""Dialogs opened from inside a tab must not inherit ContentArea's dark label
colour: on a dark-mode dialog that left message-box text invisible."""
import os

from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QApplication, QLabel, QMessageBox, QVBoxLayout, QWidget

QSS = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "styles.qss")


def test_message_box_text_is_not_forced_dark(qtbot):
    app = QApplication.instance()
    old = app.styleSheet()
    app.setStyleSheet(open(QSS, encoding="utf-8").read())
    try:
        area = QWidget()
        area.setObjectName("ContentArea")
        child = QWidget()
        QVBoxLayout(area).addWidget(child)
        qtbot.addWidget(area)
        box = QMessageBox(QMessageBox.Question, "t", "hello", QMessageBox.Yes | QMessageBox.No, child)
        qtbot.addWidget(box)
        label = box.findChild(QLabel, "qt_msgbox_label")
        label.ensurePolished()
        assert label.palette().color(QPalette.WindowText) != QColor("#1F2328")
        plain = QLabel("page label", child)
        plain.ensurePolished()
        assert plain.palette().color(QPalette.WindowText) == QColor("#1F2328")
    finally:
        app.setStyleSheet(old)
