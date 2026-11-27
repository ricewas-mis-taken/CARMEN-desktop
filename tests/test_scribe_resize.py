"""Dragging a selection's handles, and typing its position and size."""
import pytest
from PySide6.QtCore import QEvent, QPoint, QPointF, Qt
from PySide6.QtGui import QMouseEvent
from PySide6.QtTest import QTest

import scribe_geometry as geo
import scribe_store
from qt_ui.scribe_tab import ScribeCanvas


def _canvas(qtbot, items):
    doc = scribe_store.default_doc()
    doc["layers"][0]["items"] = items
    doc["prefs"]["tool"] = "select"
    doc["view"].update(zoom=1.0, x=0.0, y=0.0)
    canvas = ScribeCanvas(doc)
    qtbot.addWidget(canvas)
    canvas.resize(800, 600)
    canvas.show()
    qtbot.waitExposed(canvas)
    return canvas


def _at(canvas, x, y):
    sx, sy = canvas._to_screen(x, y)
    return QPoint(round(sx), round(sy))


def _move(canvas, pos, mods=Qt.NoModifier, buttons=Qt.LeftButton):
    """A pointer move with the keys and buttons given (QTest.mouseMove carries neither)."""
    point = QPointF(pos)
    canvas.mouseMoveEvent(QMouseEvent(QEvent.MouseMove, point, canvas.mapToGlobal(point), Qt.NoButton, buttons, mods))


def _drag(canvas, start, end, mods=Qt.NoModifier):
    QTest.mousePress(canvas, Qt.LeftButton, mods, start)
    _move(canvas, end, mods)
    QTest.mouseRelease(canvas, Qt.LeftButton, mods, end)


def _rect(a=(0, 0), b=(100, 50)):
    return {"type": "rect", "a": list(a), "b": list(b), "stroke": "#000000", "width": 2, "fill": None,
            "dashed": False, "round": True, "scale": True}


def _image(w=200, h=100):
    return {"type": "image", "a": [-100, -50], "w": w, "h": h, "file": "x.png"}


def test_dragging_the_corner_handle_resizes_a_shape(qtbot):
    rect = _rect()
    canvas = _canvas(qtbot, [rect])
    canvas._sel = [rect]
    _drag(canvas, _at(canvas, 100, 50), _at(canvas, 200, 90))
    assert geo.bounds(rect, padded=False) == pytest.approx((0, 0, 200, 90), abs=1.5)


def test_an_edge_handle_changes_only_one_side(qtbot):
    rect = _rect()
    canvas = _canvas(qtbot, [rect])
    canvas._sel = [rect]
    _drag(canvas, _at(canvas, 100, 25), _at(canvas, 160, 25))               # the right-hand middle handle
    assert geo.bounds(rect, padded=False) == pytest.approx((0, 0, 160, 50), abs=1.5)


def test_a_picture_keeps_its_shape_on_a_corner_unless_shift_is_held(qtbot):
    image = _image()
    canvas = _canvas(qtbot, [image])
    canvas._sel = [image]
    _drag(canvas, _at(canvas, 100, 50), _at(canvas, 200, 60))               # wider by a lot, taller by a little
    assert image["w"] / image["h"] == pytest.approx(2.0, rel=0.02)
    canvas.undo()
    assert (image["w"], image["h"]) == (200, 100)
    canvas._sel = [image]
    _drag(canvas, _at(canvas, 100, 50), _at(canvas, 200, 60), Qt.ShiftModifier)
    assert image["w"] / image["h"] != pytest.approx(2.0, rel=0.05)           # Shift frees it


def test_shift_keeps_a_shapes_proportions_on_a_corner(qtbot):
    rect = _rect()
    canvas = _canvas(qtbot, [rect])
    canvas._sel = [rect]
    _drag(canvas, _at(canvas, 100, 50), _at(canvas, 300, 70), Qt.ShiftModifier)
    x0, y0, x1, y1 = geo.bounds(rect, padded=False)
    assert (x1 - x0) / (y1 - y0) == pytest.approx(2.0, rel=0.02)


def test_one_resize_is_one_undo_step_and_it_redoes(qtbot):
    rect = _rect()
    canvas = _canvas(qtbot, [rect])
    canvas._sel = [rect]
    before = len(canvas._undo)
    QTest.mousePress(canvas, Qt.LeftButton, Qt.NoModifier, _at(canvas, 100, 50))
    for step in range(1, 6):
        _move(canvas, _at(canvas, 100 + step * 20, 50 + step * 10))
    QTest.mouseRelease(canvas, Qt.LeftButton, Qt.NoModifier, _at(canvas, 200, 100))
    assert len(canvas._undo) == before + 1
    canvas.undo()
    assert geo.bounds(rect, padded=False) == pytest.approx((0, 0, 100, 50))
    canvas.redo()
    assert geo.bounds(rect, padded=False)[2] == pytest.approx(200, abs=1.5)


def test_a_click_on_a_handle_without_moving_changes_nothing(qtbot):
    rect = _rect()
    canvas = _canvas(qtbot, [rect])
    canvas._sel = [rect]
    before = len(canvas._undo)
    QTest.mouseClick(canvas, Qt.LeftButton, Qt.NoModifier, _at(canvas, 100, 50))
    assert len(canvas._undo) == before and geo.bounds(rect, padded=False) == pytest.approx((0, 0, 100, 50))


def test_the_handles_only_exist_for_the_select_tool(qtbot):
    rect = _rect()
    canvas = _canvas(qtbot, [rect])
    canvas._sel = [rect]
    assert canvas._handle_at(_at(canvas, 100, 50)) == "se"
    canvas.prefs["tool"] = "freehand"
    assert canvas._handle_at(_at(canvas, 100, 50)) is None
    canvas.prefs["tool"] = "select"
    canvas._sel = []
    assert canvas._handle_at(_at(canvas, 100, 50)) is None


def test_the_pointer_shows_a_resize_cursor_over_a_handle(qtbot):
    rect = _rect()
    canvas = _canvas(qtbot, [rect])
    canvas._sel = [rect]
    _move(canvas, _at(canvas, 100, 50), buttons=Qt.NoButton)
    assert canvas.cursor().shape() == Qt.SizeFDiagCursor
    _move(canvas, _at(canvas, 50, 0), buttons=Qt.NoButton)
    assert canvas.cursor().shape() == Qt.SizeVerCursor
    _move(canvas, _at(canvas, 400, 300), buttons=Qt.NoButton)
    assert canvas.cursor().shape() not in (Qt.SizeFDiagCursor, Qt.SizeVerCursor)


def test_a_group_resizes_together(qtbot):
    a, b = _rect((0, 0), (50, 50)), _rect((100, 0), (150, 50))
    canvas = _canvas(qtbot, [a, b])
    canvas._sel = [a, b]
    _drag(canvas, _at(canvas, 150, 50), _at(canvas, 300, 100))
    assert geo.bounds(a, padded=False)[2] == pytest.approx(100, abs=1.5)
    assert geo.bounds(b, padded=False)[2] == pytest.approx(300, abs=1.5)


def test_typing_a_position_and_size_moves_and_resizes_the_selection(qtbot):
    rect = _rect()
    canvas = _canvas(qtbot, [rect])
    canvas._sel = [rect]
    moved = []
    canvas.selectionMoved.connect(lambda: moved.append(1))
    canvas.set_selection_box(500, -200, 700, -100)
    assert geo.bounds(rect, padded=False) == pytest.approx((500, -200, 700, -100))
    assert moved
    canvas.undo()
    assert geo.bounds(rect, padded=False) == pytest.approx((0, 0, 100, 50))


def test_typing_several_values_in_a_row_is_one_undo_step(qtbot):
    rect = _rect()
    canvas = _canvas(qtbot, [rect])
    canvas._sel = [rect]
    before = len(canvas._undo)
    canvas.set_selection_box(10, 10, 110, 60)
    canvas.set_selection_box(20, 10, 120, 60)
    canvas.set_selection_box(20, 30, 120, 80)
    assert len(canvas._undo) == before + 1
    canvas.undo()
    assert geo.bounds(rect, padded=False) == pytest.approx((0, 0, 100, 50))


def test_selecting_tells_anyone_listening(qtbot):
    rect = _rect()
    canvas = _canvas(qtbot, [rect])
    seen = []
    canvas.selectionChanged.connect(lambda: seen.append(len(canvas._sel)))
    QTest.mouseClick(canvas, Qt.LeftButton, Qt.NoModifier, _at(canvas, 0, 25))              # on the rectangle's edge
    assert seen and seen[-1] == 1
    QTest.mouseClick(canvas, Qt.LeftButton, Qt.NoModifier, _at(canvas, 500, 400))           # empty board
    assert seen[-1] == 0


def test_a_click_finds_the_stroke_on_a_big_board_through_the_box_check(qtbot):
    items = [{"type": "stroke", "pts": [[i * 40.0, 0], [i * 40.0 + 20, 30]], "stroke": "#000000", "width": 3,
              "fill": None, "dashed": False, "round": True, "scale": True} for i in range(300)]
    canvas = _canvas(qtbot, items)
    assert canvas._item_at((40.0 * 150 + 10, 15)) is items[150]
    assert canvas._item_at((40.0 * 150 + 10, 400)) is None
