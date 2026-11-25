"""Scribe's keyboard: nudging, layer order, zoom keys, and editing text with Enter or a double-click."""
import pytest
from PySide6.QtCore import QPoint, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QInputDialog

import scribe_geometry as geo
import scribe_store
from qt_ui.scribe_tab import ScribeCanvas


def _rect(a, b):
    return {"type": "rect", "a": list(a), "b": list(b), "stroke": "#000000", "width": 2, "fill": None,
            "dashed": False, "round": True, "scale": True}


def _text(x=0, y=0, words="hello"):
    return {"type": "text", "a": [x, y], "w": 60.0, "h": 20.0, "text": words, "size": 16, "stroke": "#000000"}


def _canvas(qtbot, items, tool="select"):
    doc = scribe_store.default_doc()
    doc["layers"][0]["items"] = items
    doc["prefs"]["tool"] = tool
    doc["view"].update(zoom=1.0, x=0.0, y=0.0)
    canvas = ScribeCanvas(doc)
    qtbot.addWidget(canvas)
    canvas.resize(800, 600)
    canvas.show()
    qtbot.waitExposed(canvas)
    canvas.setFocus()
    return canvas


def test_arrow_keys_nudge_a_pixel_and_shift_arrow_ten(qtbot):
    item = _rect((0, 0), (50, 50))
    canvas = _canvas(qtbot, [item])
    canvas._sel = [item]
    QTest.keyClick(canvas, Qt.Key_Right)
    QTest.keyClick(canvas, Qt.Key_Down)
    assert geo.bounds(item, padded=False)[:2] == pytest.approx((1, 1))
    QTest.keyClick(canvas, Qt.Key_Left, Qt.ShiftModifier)
    assert geo.bounds(item, padded=False)[0] == pytest.approx(-9)
    QTest.keyClick(canvas, Qt.Key_Up, Qt.ShiftModifier)
    assert geo.bounds(item, padded=False)[1] == pytest.approx(-9)


def test_arrows_do_nothing_with_nothing_selected(qtbot):
    item = _rect((0, 0), (50, 50))
    canvas = _canvas(qtbot, [item])
    QTest.keyClick(canvas, Qt.Key_Right)
    assert geo.bounds(item, padded=False)[0] == 0


def test_bracket_keys_change_the_stacking_order(qtbot):
    a, b, c = _rect((0, 0), (10, 10)), _rect((20, 0), (30, 10)), _rect((40, 0), (50, 10))
    canvas = _canvas(qtbot, [a, b, c])
    canvas._sel = [a]
    QTest.keyClick(canvas, Qt.Key_BracketRight, Qt.ControlModifier)                       # one step forward
    assert canvas.doc["layers"][0]["items"] == [b, a, c]
    QTest.keyClick(canvas, Qt.Key_BraceRight, Qt.ControlModifier | Qt.ShiftModifier)      # all the way to the front
    assert canvas.doc["layers"][0]["items"] == [b, c, a]
    QTest.keyClick(canvas, Qt.Key_BraceLeft, Qt.ControlModifier | Qt.ShiftModifier)       # all the way back
    assert canvas.doc["layers"][0]["items"] == [a, b, c]
    canvas._sel = [c]
    QTest.keyClick(canvas, Qt.Key_BracketLeft, Qt.ControlModifier)
    assert canvas.doc["layers"][0]["items"] == [a, c, b]


def test_zoom_keys(qtbot):
    canvas = _canvas(qtbot, [_rect((0, 0), (50, 50))])
    QTest.keyClick(canvas, Qt.Key_Plus)
    assert canvas.zoom == pytest.approx(1.25)
    QTest.keyClick(canvas, Qt.Key_Minus)
    QTest.keyClick(canvas, Qt.Key_Minus)
    assert canvas.zoom == pytest.approx(0.8)
    QTest.keyClick(canvas, Qt.Key_0, Qt.ControlModifier)
    assert canvas.zoom == pytest.approx(1.0)


def test_shift_1_fits_everything_and_shift_2_fits_the_selection(qtbot):
    near, far = _rect((0, 0), (20, 20)), _rect((2000, 1500), (2020, 1520))
    canvas = _canvas(qtbot, [near, far])
    QTest.keyClick(canvas, Qt.Key_Exclam, Qt.ShiftModifier)
    assert canvas.zoom < 0.5
    canvas._sel = [near]
    QTest.keyClick(canvas, Qt.Key_At, Qt.ShiftModifier)
    assert canvas.zoom > 5 and canvas.view["x"] == pytest.approx(10)


def test_the_number_keys_still_pick_tools(qtbot):
    canvas = _canvas(qtbot, [])
    QTest.keyClick(canvas, Qt.Key_5)
    assert canvas.tool == "rect"


def test_enter_edits_a_selected_text_and_it_is_one_undo_step(qtbot, monkeypatch):
    item = _text()
    canvas = _canvas(qtbot, [item])
    canvas._sel = [item]
    monkeypatch.setattr(QInputDialog, "getMultiLineText", lambda *a, **k: ("a much longer line", True))
    before = len(canvas._undo)
    QTest.keyClick(canvas, Qt.Key_Return)
    assert item["text"] == "a much longer line" and item["w"] > 60 and len(canvas._undo) == before + 1
    canvas.undo()
    assert item["text"] == "hello" and item["w"] == 60.0


def test_a_cancelled_or_unchanged_edit_changes_nothing(qtbot, monkeypatch):
    item = _text()
    canvas = _canvas(qtbot, [item])
    canvas._sel = [item]
    before = len(canvas._undo)
    monkeypatch.setattr(QInputDialog, "getMultiLineText", lambda *a, **k: ("other", False))
    QTest.keyClick(canvas, Qt.Key_Return)
    monkeypatch.setattr(QInputDialog, "getMultiLineText", lambda *a, **k: ("hello", True))
    QTest.keyClick(canvas, Qt.Key_Return)
    monkeypatch.setattr(QInputDialog, "getMultiLineText", lambda *a, **k: ("   ", True))
    QTest.keyClick(canvas, Qt.Key_Return)
    assert item["text"] == "hello" and len(canvas._undo) == before


def test_enter_does_nothing_for_a_shape(qtbot, monkeypatch):
    item = _rect((0, 0), (50, 50))
    canvas = _canvas(qtbot, [item])
    canvas._sel = [item]
    opened = []
    monkeypatch.setattr(QInputDialog, "getMultiLineText", lambda *a, **k: opened.append(1) or ("x", True))
    QTest.keyClick(canvas, Qt.Key_Return)
    assert opened == []


def test_double_clicking_a_text_with_the_select_tool_edits_it(qtbot, monkeypatch):
    item = _text(100, 100)
    canvas = _canvas(qtbot, [item])
    monkeypatch.setattr(QInputDialog, "getMultiLineText", lambda *a, **k: ("edited", True))
    sx, sy = canvas._to_screen(110, 110)
    QTest.mouseDClick(canvas, Qt.LeftButton, Qt.NoModifier, QPoint(round(sx), round(sy)))
    assert item["text"] == "edited" and canvas._sel == [item]


def test_double_clicking_with_another_tool_does_not_open_the_editor(qtbot, monkeypatch):
    item = _text(100, 100)
    canvas = _canvas(qtbot, [item], tool="rect")
    opened = []
    monkeypatch.setattr(QInputDialog, "getMultiLineText", lambda *a, **k: opened.append(1) or ("x", True))
    sx, sy = canvas._to_screen(110, 110)
    QTest.mouseDClick(canvas, Qt.LeftButton, Qt.NoModifier, QPoint(round(sx), round(sy)))
    assert opened == [] and item["text"] == "hello"
