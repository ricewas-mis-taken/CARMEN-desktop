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


def test_damaged_shapes_are_dropped_not_crashed_on():
    doc = scribe_store.clean_doc({"layers": [{"items": [
        {"type": "rect"},                                   # no corners
        {"type": "stroke", "pts": []},                      # empty
        {"type": "line", "a": [0, 0], "b": ["x", 1]},       # bad point
        {"type": "text", "a": [1, 2]},                      # no text
        {"type": "rect", "a": [0, 0], "b": [5, 5], "width": "fat", "stroke": "red"},
    ]}]})
    items = doc["layers"][0]["items"]
    assert len(items) == 1 and items[0]["width"] == 4 and items[0]["stroke"] == "#000000"


def test_a_run_of_width_changes_is_one_undo_step(qtbot):
    tab = ScribeTab()
    qtbot.addWidget(tab)
    tab.resize(900, 700)
    tab.show()
    canvas = tab.canvas
    canvas.set_tool("rect")
    _drag(canvas, (100, 100), (220, 180))
    canvas.set_tool("select")
    canvas._sel = list(canvas._active_items())
    before = len(canvas._undo)
    for width in (5, 6, 7, 8):
        canvas.set_width(width)
    assert len(canvas._undo) == before + 1
    canvas.undo()
    assert canvas._active_items()[0]["width"] == 4


def test_scribe_styling_does_not_leak_into_its_dialogs(qtbot):
    """Regression: the tab's stylesheet gave every label under it dark text, so
    the New-board confirm (parented to the tab) was blank on a dark dialog."""
    from PySide6.QtGui import QColor, QPalette
    from PySide6.QtWidgets import QLabel, QMessageBox
    tab = ScribeTab()
    qtbot.addWidget(tab)
    box = QMessageBox(QMessageBox.Question, "New board", "Clear everything?", QMessageBox.Yes | QMessageBox.No, tab)
    qtbot.addWidget(box)
    label = box.findChild(QLabel, "qt_msgbox_label")
    label.ensurePolished()
    assert label.palette().color(QPalette.WindowText) != QColor("#1F2328")
    # ...while the tab's own panel labels keep the dark text on their light panels.
    tab.show()
    tab._zoom_label.ensurePolished()
    assert tab._zoom_label.palette().color(QPalette.WindowText) == QColor("#1F2328")


def test_freehand_stroke_never_moves_after_drawing(qtbot):
    """Regression: strokes were re-smoothed while drawing and on release, so the
    line jittered. Points must be stored exactly as drawn, and earlier points
    must not change as the pen keeps moving."""
    import math
    tab = ScribeTab()
    qtbot.addWidget(tab)
    tab.resize(900, 700)
    tab.show()
    canvas = tab.canvas
    canvas.set_tool("freehand")
    QTest.mousePress(canvas, Qt.LeftButton, Qt.NoModifier, QPoint(100, 300))
    snapshots = []
    for i in range(1, 80):
        QTest.mouseMove(canvas, QPoint(100 + i * 5, int(300 + 40 * math.sin(i / 5))))
        snapshots.append([list(p) for p in canvas._draft["pts"]])
    for earlier, later in zip(snapshots, snapshots[1:]):
        assert later[:len(earlier)] == earlier
    drawn = [list(p) for p in canvas._draft["pts"]]
    QTest.mouseRelease(canvas, Qt.LeftButton, Qt.NoModifier, QPoint(495, 300))
    assert canvas._active_items()[0]["pts"] == drawn


# ------------------------------------------------------ redesigned chrome ----

def test_every_tool_has_an_icon_and_a_tooltip_with_its_key(qtbot):
    tab = ScribeTab()
    qtbot.addWidget(tab)
    assert set(tab._tool_buttons) == {"select", "freehand", "line", "curve", "arrow", "rect", "circle", "text", "fill"}
    for key, button in tab._tool_buttons.items():
        assert not button.icon().isNull(), key
        assert button.toolTip()
    assert "(1)" in tab._tool_buttons["line"].toolTip() and "(7)" in tab._tool_buttons["select"].toolTip()


def test_clicking_a_tool_on_the_rail_selects_it(qtbot):
    tab = ScribeTab()
    qtbot.addWidget(tab)
    tab._tool_buttons["circle"].click()
    assert tab.canvas.tool == "circle" and tab._tool_buttons["circle"].isChecked()


def test_the_saved_indicator_follows_the_save(qtbot):
    tab = ScribeTab()
    qtbot.addWidget(tab)
    assert tab._saved_label.text() == "Saved"
    tab.resize(900, 700)
    tab.show()
    tab.canvas.set_tool("rect")
    _drag(tab.canvas, (100, 100), (220, 180))
    assert tab._saved_label.text() == "Saving…"
    tab.flush()
    assert tab._saved_label.text() == "Saved"


def test_a_quick_colour_sets_the_stroke(qtbot):
    tab = ScribeTab()
    qtbot.addWidget(tab)
    tab._quick_stroke("#E03131")
    assert tab.canvas.prefs["stroke"] == "#E03131"
    assert tab._color_well.stroke == "#E03131" and tab._quick_dots["#E03131"].isChecked()


def test_width_slider_and_box_stay_in_step(qtbot):
    tab = ScribeTab()
    qtbot.addWidget(tab)
    tab._width_slider.setValue(17)
    assert tab._width.value() == 17 and tab.canvas.prefs["width"] == 17
    tab._width.setValue(120)  # beyond the slider's range: the slider pins at its end
    assert tab._width_slider.value() == 60 and tab.canvas.prefs["width"] == 120


def test_the_snap_menu_drives_the_view_options(qtbot):
    tab = ScribeTab()
    qtbot.addWidget(tab)
    action = tab._pref_checks["showRulers"]
    before = tab.canvas.prefs["showRulers"]
    action.setChecked(not before)
    assert tab.canvas.prefs["showRulers"] is (not before)


# ------------------------------------------------------------- the inspector ----

def test_the_colour_well_follows_stroke_and_fill(qtbot):
    tab = ScribeTab()
    qtbot.addWidget(tab)
    tab._set_fill("#FFD54F")
    assert tab._color_well.fill == "#FFD54F" and tab.canvas.prefs["fill"] == "#FFD54F"
    tab._set_fill(None)
    assert tab._color_well.fill is None and tab.canvas.prefs["fill"] is None


def test_swap_trades_line_and_fill(qtbot):
    tab = ScribeTab()
    qtbot.addWidget(tab)
    tab._quick_stroke("#1971C2")
    tab._set_fill("#FAB005")
    tab._swap_colors()
    assert (tab.canvas.prefs["stroke"], tab.canvas.prefs["fill"]) == ("#FAB005", "#1971C2")
    tab._set_fill(None)
    tab._swap_colors()   # nothing to swap with: left alone
    assert tab.canvas.prefs["stroke"] == "#FAB005" and tab.canvas.prefs["fill"] is None


def test_a_right_click_on_a_quick_colour_sets_the_fill(qtbot):
    tab = ScribeTab()
    qtbot.addWidget(tab)
    tab.show()
    dot = tab._quick_dots["#2F9E44"]
    QTest.mouseClick(dot, Qt.RightButton)
    assert tab.canvas.prefs["fill"] == "#2F9E44"
    QTest.mouseClick(dot, Qt.LeftButton)
    assert tab.canvas.prefs["stroke"] == "#2F9E44"


def test_the_preview_shows_the_line_you_will_draw(qtbot):
    tab = ScribeTab()
    qtbot.addWidget(tab)
    tab._quick_stroke("#7048E8")
    tab._width.setValue(9)
    tab._pref_checks["dashed"].setChecked(True)
    tab.canvas.changed.emit()
    assert tab._stroke_preview._style == ("#7048E8", 9, True, tab.canvas.prefs["roundCorners"])


def test_the_paper_stack_sets_the_background_and_shows_the_current_one(qtbot):
    tab = ScribeTab()
    qtbot.addWidget(tab)
    stack = tab._paper_stack
    stack.picked.emit("dots")
    assert tab.canvas.prefs["background"] == "dots" and tab._background.currentData() == "dots"
    assert stack.current() == "dots"
    assert set(stack._keys) == set(scribe_store.BACKGROUNDS)
    stack.grab()      # every paper paints


def test_clicking_a_paper_strip_picks_that_paper(qtbot):
    from PySide6.QtCore import QPoint
    tab = ScribeTab()
    qtbot.addWidget(tab)
    tab.show()
    stack = tab._paper_stack
    for index, key in enumerate(stack._keys):
        y = int(stack._top(index)) + 6                  # inside this card's own strip
        qtbot.mouseClick(stack, Qt.LeftButton, pos=QPoint(40, y))
        assert tab.canvas.prefs["background"] == key, key


def test_every_paper_paints_edge_to_edge_on_the_board(qtbot):
    """Wherever the view is, the board is the chosen paper (white, black and plain paper are flat)."""
    from qt_ui.scribe_tab import _BACKGROUNDS
    tab = ScribeTab()
    qtbot.addWidget(tab)
    tab.resize(1200, 800)
    tab.show()
    for key in ("white", "black", "paper"):
        tab.canvas.set_background(key)
        tab.canvas.view.update(x=0, y=9000)
        image = tab.canvas.grab().toImage()
        assert image.pixelColor(60, 60).name() == _BACKGROUNDS[key][0].lower(), key


# ------------------------------------------------------------------ hover help ----

def _every_control(tab):
    from PySide6.QtWidgets import QCheckBox, QComboBox, QListWidget, QPushButton, QSlider, QSpinBox, QToolButton
    return [w for kind in (QPushButton, QCheckBox, QComboBox, QSlider, QSpinBox, QListWidget, QToolButton)
            for w in tab.findChildren(kind)]


def test_controls_carry_no_explanatory_sentences(qtbot):
    """Tooltips are names (a tool and its key, a colour's code), never help text."""
    tab = ScribeTab()
    qtbot.addWidget(tab)
    for widget in _every_control(tab):
        assert len(widget.toolTip()) < 40, widget.toolTip()
    assert "Click and drag" not in tab._tool_buttons["line"].toolTip()


def test_drawing_by_hand_repaints_only_the_new_line(qtbot):
    """While a freehand line is being drawn the board underneath is one remembered picture, rebuilt only
    when the view or the drawing changes (repainting everything on each mouse move made it lag)."""
    tab = ScribeTab()
    qtbot.addWidget(tab)
    tab.resize(900, 600)
    tab.show()
    canvas = tab.canvas
    canvas.doc["layers"][0]["items"].append(
        {"type": "stroke", "pts": [[0, 0], [50, 40], [100, 0]], "stroke": "#000000", "width": 3})
    canvas._draft = {"type": "stroke", "pts": [[10, 10], [20, 20]], "stroke": "#E2603B", "width": 3}
    canvas.grab()
    first = canvas._picture[1]
    canvas._draft["pts"].append([30, 10])
    canvas.grab()
    assert canvas._picture[1] is first                    # same picture: the board was not repainted
    canvas.doc["layers"][0]["items"].append(
        {"type": "stroke", "pts": [[5, 5], [9, 9]], "stroke": "#000000", "width": 3})
    canvas.grab()
    assert canvas._picture[1] is not first                # the drawing changed, so it was remade


def test_a_strokes_curve_is_built_once_and_rebuilt_when_it_changes(qtbot):
    tab = ScribeTab()
    qtbot.addWidget(tab)
    canvas = tab.canvas
    item = {"type": "stroke", "pts": [[0, 0], [10, 20], [30, 10]], "stroke": "#000000", "width": 3}
    first = canvas._stroke_path(item, False)
    assert canvas._stroke_path(item, False) is first
    item["pts"].append([50, 50])
    assert canvas._stroke_path(item, False) is not first
