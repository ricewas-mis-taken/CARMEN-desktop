from PySide6.QtWidgets import QApplication, QLabel, QVBoxLayout, QWidget

import qt_ui.popup_fit as popup_fit


def test_a_huge_popup_is_clamped_to_the_screen_and_centred(qtbot):
    popup_fit.install()
    win = QWidget()
    win.setObjectName("PopupBg")
    box = QVBoxLayout(win)
    big = QLabel("x")
    big.setMinimumSize(100, 100)
    box.addWidget(big)
    win.resize(3000, 5000)
    qtbot.addWidget(win)
    win.show()
    qtbot.waitExposed(win)
    area = win.screen().availableGeometry()
    assert win.width() <= popup_fit.MAX_WIDTH
    assert win.height() <= area.height() * popup_fit.SCREEN_SHARE + 1
    assert area.contains(win.geometry().topLeft())


def test_a_small_popup_keeps_a_comfortable_minimum_width(qtbot):
    popup_fit.install()
    win = QWidget()
    win.setObjectName("PopupBg")
    win.resize(200, 200)
    qtbot.addWidget(win)
    win.show()
    qtbot.waitExposed(win)
    assert win.width() >= popup_fit.MIN_WIDTH


def test_a_popup_taller_than_the_screen_scrolls_instead(qtbot):
    from PySide6.QtWidgets import QLabel, QScrollArea
    popup_fit.install()
    win = QWidget()
    win.setObjectName("PopupBg")
    box = QVBoxLayout(win)
    for i in range(80):
        row = QLabel(f"row {i}")
        row.setMinimumHeight(40)
        box.addWidget(row)
    qtbot.addWidget(win)
    win.show()
    qtbot.waitExposed(win)
    area = win.screen().availableGeometry()
    assert win.height() <= area.height() * popup_fit.SCREEN_SHARE + 1
    assert win.findChild(QScrollArea) is not None
