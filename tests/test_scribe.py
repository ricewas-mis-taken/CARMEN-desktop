"""Scribe: the always-saved infinite board. Store + shape maths are tested
without Qt; the tab is driven with real mouse events. The autouse
isolate_real_data_paths fixture points scribe_store at a throwaway dir."""
import json
import os

from PySide6.QtCore import QPoint, Qt
from PySide6.QtTest import QTest

import scribe_geometry as geo
import scribe_store
from qt_ui.scribe_tab import ScribeTab


# ---- store -----------------------------------------------------------------
def test_load_with_no_file_gives_default_board():
    doc = scribe_store.load()
    assert doc["prefs"]["tool"] == "freehand"
    assert doc["view"] == {"zoom": 1.0, "x": 0.0, "y": 0.0}
    assert len(doc["layers"]) == 1 and doc["layers"][0]["items"] == []


def test_save_and_load_round_trip_keeps_view_and_prefs():
    doc = scribe_store.default_doc()
    doc["view"] = {"zoom": 2.5, "x": -400.0, "y": 1200.0}
    doc["prefs"].update(tool="circle", stroke="#ff0000", background="darkgrid", width=9)
    doc["layers"][0]["items"].append({"type": "line", "a": [0, 0], "b": [5, 5], "stroke": "#000000", "width": 4})
    scribe_store.save(doc)
    again = scribe_store.load()
    assert again["view"] == {"zoom": 2.5, "x": -400.0, "y": 1200.0}
    assert again["prefs"]["tool"] == "circle" and again["prefs"]["background"] == "darkgrid"
    assert again["layers"][0]["items"][0]["b"] == [5, 5]


def test_damaged_file_falls_back_to_defaults():
    os.makedirs(os.path.dirname(scribe_store.PATH), exist_ok=True)
    with open(scribe_store.PATH, "w") as f:
        f.write("{not json")
    assert scribe_store.load()["prefs"]["background"] == "grid"


def test_clean_doc_repairs_bad_values():
    doc = scribe_store.clean_doc({
        "layers": [{"items": "nope"}, 5], "activeLayer": 9,
        "view": {"zoom": 1e9, "x": "a", "y": float("nan")},
        "prefs": {"tool": "laser", "background": "plaid", "width": -3, "stroke": "red", "dashed": "yes"},
    })
    assert len(doc["layers"]) == 1 and doc["layers"][0]["items"] == []
    assert doc["activeLayer"] == 0
    assert doc["view"]["zoom"] == scribe_store.MAX_ZOOM and doc["view"]["x"] == 0.0 and doc["view"]["y"] == 0.0
    prefs = doc["prefs"]
    assert prefs["tool"] == "freehand" and prefs["background"] == "grid"
    assert prefs["width"] == 1 and prefs["stroke"] == "#000000" and prefs["dashed"] is False


def test_images_are_copied_and_paths_cannot_escape():
    src = os.path.join(os.path.dirname(scribe_store.PATH), "pic.png")
    os.makedirs(os.path.dirname(src), exist_ok=True)
    with open(src, "wb") as f:
        f.write(b"png")
    name = scribe_store.store_image_file(src)
    assert os.path.exists(scribe_store.image_path(name))
    assert scribe_store.image_path("../scribe.json") is None
    assert scribe_store.image_path("") is None


# ---- geometry --------------------------------------------------------------
def test_hit_line_and_miss():
    line = {"type": "line", "a": [0, 0], "b": [100, 0], "width": 4}
    assert geo.hit_item(line, 50, 3, 2)
    assert not geo.hit_item(line, 50, 20, 2)


def test_filled_rect_hit_inside_but_empty_rect_only_on_edge():
    rect = {"type": "rect", "a": [0, 0], "b": [100, 100], "width": 2, "fill": None}
    assert not geo.hit_item(rect, 50, 50, 2)
    assert geo.hit_item(rect, 0, 50, 2)
    rect["fill"] = "#ffcc00"
    assert geo.hit_item(rect, 50, 50, 2)


def test_contains_fill_only_for_closed_shapes():
    circle = {"type": "circle", "a": [0, 0], "b": [100, 100], "width": 2}
    assert geo.contains_fill(circle, 50, 50) and not geo.contains_fill(circle, 5, 5)
    assert not geo.contains_fill({"type": "line", "a": [0, 0], "b": [9, 9], "width": 2}, 4, 4)


def test_translate_moves_every_point():
    stroke = {"type": "stroke", "pts": [[0, 0], [1, 1]], "width": 2}
    geo.translate(stroke, 10, -5)
    assert stroke["pts"] == [[10, -5], [11, -4]]
    curve = {"type": "curve", "a": [0, 0], "b": [2, 0], "c": [1, 1], "width": 2}
    geo.translate(curve, 1, 1)
    assert curve["c"] == [2, 2]


def test_snap_angle_to_15_degrees():
    x, y = geo.snap_angle((0, 0), (100, 3))
    assert abs(y) < 1e-9 and abs(x - (100 ** 2 + 3 ** 2) ** 0.5) < 1e-9


def test_smoothing_zero_keeps_points_and_higher_reduces_them():
    pts = [[i, (i % 2) * 0.1] for i in range(50)]
    assert geo.smooth_stroke(pts, 0, 1.0) == pts
    assert len(geo.smooth_stroke(pts, 5, 1.0)) < len(pts)


# ---- tab -------------------------------------------------------------------
def _drag(canvas, start, end, steps=6):
    QTest.mousePress(canvas, Qt.LeftButton, Qt.NoModifier, QPoint(*start))
    for i in range(1, steps + 1):
        x = start[0] + (end[0] - start[0]) * i // steps
        y = start[1] + (end[1] - start[1]) * i // steps
        QTest.mouseMove(canvas, QPoint(x, y))
    QTest.mouseRelease(canvas, Qt.LeftButton, Qt.NoModifier, QPoint(*end))


def test_tab_opens_as_it_was_left(qtbot):
    doc = scribe_store.default_doc()
    doc["view"] = {"zoom": 3.0, "x": 250.0, "y": -80.0}
    doc["prefs"].update(tool="rect", background="black", width=12)
    scribe_store.save(doc)
    tab = ScribeTab()
    qtbot.addWidget(tab)
    assert tab.canvas.tool == "rect" and tab._tool_buttons["rect"].isChecked()
    assert tab.canvas.view == {"zoom": 3.0, "x": 250.0, "y": -80.0}
    assert tab._background.currentData() == "black" and tab._width.value() == 12


def test_drawing_a_rectangle_is_saved_without_asking(qtbot):
    tab = ScribeTab()
    qtbot.addWidget(tab)
    tab.resize(900, 700)
    tab.show()
    tab.canvas.set_tool("rect")
    _drag(tab.canvas, (100, 100), (220, 180))
    items = tab.canvas.doc["layers"][0]["items"]
    assert [i["type"] for i in items] == ["rect"]
    tab.flush()
    saved = json.load(open(scribe_store.PATH))
    assert saved["layers"][0]["items"][0]["type"] == "rect"
    assert saved["prefs"]["tool"] == "rect"


def test_undo_redo_and_select_move_delete(qtbot):
    tab = ScribeTab()
    qtbot.addWidget(tab)
    tab.resize(900, 700)
    tab.show()
    canvas = tab.canvas
    canvas.set_tool("line")
    _drag(canvas, (100, 100), (300, 100))
    assert len(canvas._active_items()) == 1
    canvas.undo()
    assert canvas._active_items() == []
    canvas.redo()
    assert len(canvas._active_items()) == 1

    canvas.set_tool("select")
    before = list(canvas._active_items()[0]["a"])
    mx, my = canvas._to_screen(*[(p + q) / 2 for p, q in zip(canvas._active_items()[0]["a"], canvas._active_items()[0]["b"])])
    _drag(canvas, (int(mx), int(my)), (int(mx), int(my) + 60))
    after = canvas._active_items()[0]["a"]
    assert after[1] > before[1]
    QTest.keyClick(canvas, Qt.Key_Delete)
    assert canvas._active_items() == []


def test_zoom_pan_and_reset(qtbot):
    tab = ScribeTab()
    qtbot.addWidget(tab)
    tab.resize(900, 700)
    tab.show()
    canvas = tab.canvas
    canvas.zoom_by(2)
    assert canvas.zoom == 2.0 and tab._zoom_label.text() == "200%"
    canvas.set_pan_mode(True)
    _drag(canvas, (300, 300), (400, 300))
    assert canvas.view["x"] < 0
    canvas.reset_view()
    assert canvas.view == {"zoom": 1.0, "x": 0.0, "y": 0.0}


def test_fill_tool_colours_a_closed_shape(qtbot):
    tab = ScribeTab()
    qtbot.addWidget(tab)
    tab.resize(900, 700)
    tab.show()
    canvas = tab.canvas
    canvas.set_tool("circle")
    _drag(canvas, (200, 200), (400, 400))
    canvas.set_fill("#ff0000")
    canvas.set_tool("fill")
    mid = canvas._to_screen(*[(a + b) / 2 for a, b in zip(canvas._active_items()[0]["a"], canvas._active_items()[0]["b"])])
    QTest.mouseClick(canvas, Qt.LeftButton, Qt.NoModifier, QPoint(int(mid[0]), int(mid[1])))
    assert canvas._active_items()[0]["fill"] == "#ff0000"


def test_layers_background_and_export(qtbot, tmp_path):
    tab = ScribeTab()
    qtbot.addWidget(tab)
    tab.resize(900, 700)
    tab.show()
    canvas = tab.canvas
    canvas.add_layer()
    assert len(canvas.doc["layers"]) == 2 and canvas.doc["activeLayer"] == 1
    canvas.set_tool("line")
    _drag(canvas, (100, 100), (260, 140))
    assert canvas.doc["layers"][1]["items"] and not canvas.doc["layers"][0]["items"]
    for background in scribe_store.BACKGROUNDS:
        canvas.set_background(background)
        canvas.grab()  # paints without raising
    out = tmp_path / "board.png"
    assert canvas.export_png(str(out)) and out.exists()
    canvas.remove_layer()
    assert len(canvas.doc["layers"]) == 1


def test_freehand_preview_matches_what_is_saved(qtbot):
    """Regression: the stroke was only smoothed on release, so the writing
    visibly shifted when the pen lifted. The preview must use the same points."""
    import math
    tab = ScribeTab()
    qtbot.addWidget(tab)
    tab.resize(900, 700)
    tab.show()
    canvas = tab.canvas
    canvas.set_tool("freehand")
    QTest.mousePress(canvas, Qt.LeftButton, Qt.NoModifier, QPoint(100, 300))
    for i in range(1, 80):
        QTest.mouseMove(canvas, QPoint(100 + i * 5, int(300 + 40 * math.sin(i / 5))))
    shown = canvas._smoothed(canvas._draft["pts"])
    painted = []
    real_paint = canvas._paint_item
    canvas._paint_item = lambda p, item, zoom: (painted.append(item), real_paint(p, item, zoom))[1]
    canvas.grab()  # a repaint while the pen is still down
    assert [i["pts"] for i in painted if i is not canvas._draft and i.get("pts") == shown]
    QTest.mouseRelease(canvas, Qt.LeftButton, Qt.NoModifier, QPoint(495, 300))
    saved = canvas._active_items()[0]["pts"]
    assert saved == shown and len(saved) < 79  # smoothed, and identical to the preview
