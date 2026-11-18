"""The eraser: wipes whole shapes the pointer touches, one undo step per drag."""
import pytest
from PySide6.QtCore import QEvent, QPoint, QPointF, Qt
from PySide6.QtGui import QMouseEvent
from PySide6.QtTest import QTest

import scribe_store
from qt_ui.scribe_tab import ScribeCanvas, ScribeTab


def _line(x0, y0, x1, y1):
    return {"type": "line", "a": [x0, y0], "b": [x1, y1], "stroke": "#000000", "width": 3, "fill": None,
            "dashed": False, "round": True, "scale": True}


def _canvas(qtbot, items):
    doc = scribe_store.default_doc()
    doc["layers"][0]["items"] = items
    doc["prefs"]["tool"] = "eraser"
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


def _move(canvas, pos):
    point = QPointF(pos)
    canvas.mouseMoveEvent(QMouseEvent(QEvent.MouseMove, point, canvas.mapToGlobal(point), Qt.NoButton,
                                      Qt.LeftButton, Qt.NoModifier))


def _items(canvas):
    return canvas.doc["layers"][0]["items"]


def test_a_click_on_a_shape_erases_it_and_only_it(qtbot):
    near, far = _line(-50, 0, 50, 0), _line(-50, 200, 50, 200)
    canvas = _canvas(qtbot, [near, far])
    QTest.mouseClick(canvas, Qt.LeftButton, Qt.NoModifier, _at(canvas, 0, 2))
    assert _items(canvas) == [far]


def test_a_click_on_empty_board_erases_nothing_and_leaves_no_undo_step(qtbot):
    canvas = _canvas(qtbot, [_line(-50, 0, 50, 0)])
    before = len(canvas._undo)
    QTest.mouseClick(canvas, Qt.LeftButton, Qt.NoModifier, _at(canvas, 0, 150))
    assert len(_items(canvas)) == 1 and len(canvas._undo) == before


def test_a_fast_sweep_wipes_everything_on_its_path_in_one_undo_step(qtbot):
    items = [_line(x, -40, x, 40) for x in range(-200, 201, 50)]              # nine upright lines
    canvas = _canvas(qtbot, list(items))
    before = len(canvas._undo)
    QTest.mousePress(canvas, Qt.LeftButton, Qt.NoModifier, _at(canvas, -260, 0))
    _move(canvas, _at(canvas, 260, 0))                                         # one big jump across all of them
    QTest.mouseRelease(canvas, Qt.LeftButton, Qt.NoModifier, _at(canvas, 260, 0))
    assert _items(canvas) == []
    assert len(canvas._undo) == before + 1
    canvas.undo()
    assert _items(canvas) == items                                             # all back, in their old order
    canvas.redo()
    assert _items(canvas) == []


def test_the_eraser_reach_stays_the_same_size_on_screen_at_any_zoom(qtbot):
    canvas = _canvas(qtbot, [_line(-50, 0, 50, 0)])
    canvas.view["zoom"] = 8.0
    QTest.mouseClick(canvas, Qt.LeftButton, Qt.NoModifier, _at(canvas, 0, 3))     # 24 pixels away at 8x: out of reach
    assert len(_items(canvas)) == 1
    QTest.mouseClick(canvas, Qt.LeftButton, Qt.NoModifier, _at(canvas, 0, 0.6))
    assert _items(canvas) == []


def test_it_only_touches_visible_layers(qtbot):
    canvas = _canvas(qtbot, [_line(-50, 0, 50, 0)])
    canvas.doc["layers"][0]["visible"] = False
    QTest.mouseClick(canvas, Qt.LeftButton, Qt.NoModifier, _at(canvas, 0, 0))
    assert len(_items(canvas)) == 1


def test_erasing_a_selected_shape_drops_it_from_the_selection(qtbot):
    line = _line(-50, 0, 50, 0)
    canvas = _canvas(qtbot, [line])
    canvas._sel = [line]
    QTest.mouseClick(canvas, Qt.LeftButton, Qt.NoModifier, _at(canvas, 0, 0))
    assert canvas._sel == [] and _items(canvas) == []


def test_the_pointer_is_the_ring_not_the_system_arrow(qtbot):
    canvas = _canvas(qtbot, [])
    canvas.set_tool("eraser")
    assert canvas.cursor().shape() == Qt.BlankCursor
    canvas._cursor_world = (0.0, 0.0)
    assert not canvas.grab().isNull()


def test_the_e_key_picks_the_eraser_and_the_tool_is_remembered(qtbot):
    tab = ScribeTab()
    qtbot.addWidget(tab)
    tab.show()
    QTest.keyClick(tab.canvas, Qt.Key_E)
    assert tab.canvas.tool == "eraser" and tab._tool_buttons["eraser"].isChecked()
    assert scribe_store.clean_doc({"prefs": {"tool": "eraser"}})["prefs"]["tool"] == "eraser"
    assert "(E)" in tab._tool_buttons["eraser"].toolTip()


def test_erasing_on_a_big_board_checks_boxes_first(qtbot, monkeypatch):
    import scribe_geometry as geo
    items = [_line(i * 30.0, 0, i * 30.0 + 10, 5) for i in range(600)]
    canvas = _canvas(qtbot, items)
    checked = []
    real = geo.hit_item
    monkeypatch.setattr(geo, "hit_item", lambda *a, **k: checked.append(1) or real(*a, **k))
    QTest.mouseClick(canvas, Qt.LeftButton, Qt.NoModifier, _at(canvas, 300 * 30.0 * 0 + 5, 2))
    assert len(checked) < 10                                # not one distance check per shape on the board
