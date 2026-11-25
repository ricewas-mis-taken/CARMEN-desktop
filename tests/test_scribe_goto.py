"""The live coordinates in the top bar and the Go to popup."""
import pytest
from PySide6.QtCore import QEvent, QPoint, QPointF, Qt
from PySide6.QtGui import QMouseEvent
from PySide6.QtTest import QTest

import scribe_store
from qt_ui.scribe_tab import ScribeTab, _GoToPopup


@pytest.fixture
def tab(qtbot):
    tab = ScribeTab()
    qtbot.addWidget(tab)
    tab.resize(1300, 800)
    tab.show()
    qtbot.waitExposed(tab)
    return tab


def _hover(canvas, x, y):
    point = QPointF(x, y)
    canvas.mouseMoveEvent(QMouseEvent(QEvent.MouseMove, point, canvas.mapToGlobal(point), Qt.NoButton,
                                      Qt.NoButton, Qt.NoModifier))


def test_places_are_written_with_separators_and_a_decimal_only_when_zoomed_in():
    assert ScribeTab.format_place(665799.4, -46971.6) == "x 665,799   y -46,972"
    assert ScribeTab.format_place(12.34, -0.5, zoom=16) == "x 12.3   y -0.5"
    assert ScribeTab.format_place(0, 0) == "x 0   y 0"


def test_the_top_bar_shows_where_the_board_is_until_the_pointer_is_over_it(tab):
    canvas = tab.canvas
    canvas.view.update(x=665799.0, y=-46971.0)
    canvas.changed.emit()
    assert tab._coords_button.text() == "x 665,799   y -46,971"
    _hover(canvas, canvas.width() / 2 + 100, canvas.height() / 2)                    # 100 pixels right of the middle
    assert tab._coords_button.text() == tab.format_place(665799.0 + 100 / canvas.zoom, -46971.0, canvas.zoom)
    canvas.cursorLeft.emit()
    assert tab._coords_button.text() == "x 665,799   y -46,971"


def test_the_pointer_place_follows_the_zoom(tab):
    canvas = tab.canvas
    canvas.view.update(x=0.0, y=0.0, zoom=2.0)
    _hover(canvas, canvas.width() / 2 + 100, canvas.height() / 2 + 40)
    assert tab._coords_button.text() == "x 50   y 20"


def test_go_to_puts_the_middle_of_the_board_on_the_point_and_can_set_the_zoom(tab):
    canvas = tab.canvas
    canvas.go_to(665799, -46971)
    assert canvas.view["x"] == 665799 and canvas.view["y"] == -46971 and canvas.zoom == pytest.approx(1.0)
    canvas.go_to(10, 20, zoom=0.25)
    assert canvas.view["x"] == 10 and canvas.zoom == pytest.approx(0.25)
    assert not canvas.grab().isNull()


def test_the_popup_goes_where_you_typed_and_keeps_the_zoom_by_default(tab):
    canvas = tab.canvas
    canvas.set_zoom(0.5)
    popup = _GoToPopup(canvas, tab._coords_button)
    popup.show()
    popup.fields["x"].setValue(1234.5)
    popup.fields["y"].setValue(-99.0)
    popup.go_button.click()
    assert canvas.view["x"] == 1234.5 and canvas.view["y"] == -99.0 and canvas.zoom == pytest.approx(0.5)
    assert not popup.isVisible()


def test_the_popup_can_set_the_zoom_too(tab):
    canvas = tab.canvas
    popup = _GoToPopup(canvas, tab._coords_button)
    popup.show()
    popup.keep_zoom.setChecked(False)
    popup.fields["zoom"].setValue(400.0)
    popup.go_button.click()
    assert canvas.zoom == pytest.approx(4.0)


def test_the_popup_shortcuts(tab):
    canvas = tab.canvas
    canvas.doc["layers"][0]["items"] = [{"type": "rect", "a": [1000, 2000], "b": [1200, 2100], "stroke": "#000000",
                                         "width": 2, "fill": None, "dashed": False, "round": True, "scale": True}]
    popup = _GoToPopup(canvas, tab._coords_button)
    assert not popup.buttons["Selection"].isEnabled()                                  # nothing selected yet
    popup.buttons["Origin"].click()
    assert (canvas.view["x"], canvas.view["y"]) == (0.0, 0.0)
    canvas._sel = canvas.doc["layers"][0]["items"][:]
    again = _GoToPopup(canvas, tab._coords_button)
    assert again.buttons["Selection"].isEnabled()
    again.buttons["Selection"].click()
    assert (canvas.view["x"], canvas.view["y"]) == (1100.0, 2050.0)
    third = _GoToPopup(canvas, tab._coords_button)
    third.buttons["Everything"].click()
    assert canvas.zoom > 1.0 and canvas.view["x"] == pytest.approx(1100.0)


def test_ctrl_g_and_the_button_open_it_and_a_second_press_closes_it(tab, qtbot):
    tab._open_go_to()
    assert tab._go_to.isVisible()
    tab._open_go_to()
    assert not tab._go_to.isVisible()
    tab._coords_button.click()
    assert tab._go_to.isVisible()
    tab._go_to.close()


def test_the_landing_ring_grows_and_then_goes(tab, qtbot):
    from qt_ui import motion
    motion.FORCE = True
    try:
        tab.canvas.go_to(5, 5)
        qtbot.waitUntil(lambda: tab.canvas._arrival is not None, timeout=1000)
        assert not tab.canvas.grab().isNull()                                            # paints with the ring on
        qtbot.waitUntil(lambda: tab.canvas._arrival is None, timeout=3000)
    finally:
        motion.FORCE = None


def test_the_coordinates_hide_in_a_narrow_window(qtbot):
    from qt_ui.main_window import _scrolling
    narrow = ScribeTab()
    holder = _scrolling(narrow)                          # as in the app: the page sits in a scroll area
    qtbot.addWidget(holder)
    holder.resize(1300, 700)
    holder.show()
    qtbot.waitExposed(holder)
    assert narrow._coords_button.isVisible()
    holder.resize(700, 560)
    qtbot.wait(50)
    assert not narrow._coords_button.isVisible()
