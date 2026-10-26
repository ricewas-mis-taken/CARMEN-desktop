"""The app has no dark theme: on a computer set to dark mode its popups used to show white text on
white boxes. The app now asks Qt for the light scheme, so they read the same everywhere."""
import pytest
from PySide6.QtCore import QPoint, QRect, Qt
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import QApplication, QLabel, QMessageBox, QWidget

import review_store
from qt_ui import ui_kit


@pytest.fixture
def os_is_dark():
    hints = QGuiApplication.styleHints()
    if not hasattr(hints, "setColorScheme"):
        pytest.skip("this Qt cannot switch colour scheme")
    hints.setColorScheme(Qt.ColorScheme.Dark)
    QApplication.instance().setStyleSheet(
        ui_kit.with_check_icon(open("styles.qss", encoding="utf-8").read()))
    yield
    hints.unsetColorScheme()
    QApplication.instance().setStyleSheet("")


def _text_is_visible(widget, label):
    image = widget.grab().toImage()
    ratio = image.devicePixelRatio() or 1
    top_left = label.mapTo(widget, QPoint(0, 0))
    rect = QRect(int(top_left.x() * ratio), int(top_left.y() * ratio), int(label.width() * ratio), int(label.height() * ratio))
    lo, hi = 255, 0
    for x in range(rect.left(), min(rect.right(), image.width()), 2):
        for y in range(rect.top(), min(rect.bottom(), image.height()), 2):
            c = image.pixelColor(x, y)
            v = (c.red() * 299 + c.green() * 587 + c.blue() * 114) // 1000
            lo, hi = min(lo, v), max(hi, v)
    return hi - lo > 40


def _message_in_a_tab(qtbot):
    host = QWidget()
    host.setObjectName("ContentArea")
    qtbot.addWidget(host)
    host.show()
    box = QMessageBox(QMessageBox.Question, "Delete topic", "Delete this topic and everything in it?",
                      QMessageBox.Yes | QMessageBox.No, host)
    box.show()
    qtbot.wait(80)
    label = next(w for w in box.findChildren(QLabel) if w.text().startswith("Delete this"))
    return host, box, label


def test_force_light_scheme_makes_it_readable(qtbot, os_is_dark):
    ui_kit.force_light_scheme()
    host, box, label = _message_in_a_tab(qtbot)
    assert _text_is_visible(box, label)


def test_the_review_sidebar_steps_aside_when_the_window_is_narrow(qtbot, isolate_review_db):
    import qt_ui.review_tab as review_tab
    topic = review_store.create_topic("T")["id"]
    view = review_tab._TopicView(topic)
    qtbot.addWidget(view)
    view.setMinimumSize(0, 0)       # in the app the window's own minimum rules, not the view's hint
    view.show()
    view.setFixedWidth(1000)
    assert view._side_card.isVisible()
    view.setFixedWidth(500)
    qtbot.wait(50)
    assert view.width() == 500, view.width()
    assert not view._side_card.isVisible()
