import random
import time

import scribe_store
from qt_ui import scribe_tab


def _board(count=3000, seed=3):
    rng = random.Random(seed)
    doc = scribe_store.default_doc()
    items = doc["layers"][0]["items"]
    for _ in range(count):
        x, y = rng.uniform(0, 400000), rng.uniform(0, 100000)
        pts = [[x + k * 9, y + 14 * ((k * 7) % 5)] for k in range(120)]
        items.append({"type": "stroke", "pts": pts, "stroke": "#000000", "width": 600})
    return doc


def test_a_board_zoomed_far_out_draws_quickly_and_still_shows_its_strokes(qtbot, isolate_real_data_paths):
    canvas = scribe_tab.ScribeCanvas(_board())
    qtbot.addWidget(canvas)
    canvas.resize(900, 500)
    canvas.view.update({"zoom": 0.002, "x": 200000, "y": 50000})
    started = time.perf_counter()
    image = canvas.grab().toImage()
    assert time.perf_counter() - started < 1.5
    dark = sum(1 for x in range(0, 900, 6) for y in range(0, 500, 6) if image.pixelColor(x, y).lightness() < 60)
    assert dark > 20                       # the strokes are there as marks, not dropped


def test_a_mid_zoom_stroke_is_drawn_with_fewer_points_and_a_close_one_in_full(qtbot, isolate_real_data_paths):
    canvas = scribe_tab.ScribeCanvas(_board(count=1))
    qtbot.addWidget(canvas)
    canvas.resize(900, 500)
    item = canvas.doc["layers"][0]["items"][-1]
    x0, y0 = item["pts"][0]
    canvas.view.update({"zoom": 0.02, "x": x0 + 500, "y": y0})      # about 20 px across: a few straight pieces
    canvas.grab()
    far = canvas.__dict__.get("_far_cache", {})
    assert id(item) in far and far[id(item)][2].elementCount() < len(item["pts"]) / 3
    canvas._far_cache = {}
    canvas.view.update({"zoom": 1.0, "x": x0 + 500, "y": y0})       # close: the full curve
    canvas.grab()
    assert id(item) not in canvas.__dict__.get("_far_cache", {})


def test_panning_a_big_board_reuses_one_picture_and_the_minimap_is_not_rebuilt(qtbot, isolate_real_data_paths):
    canvas = scribe_tab.ScribeCanvas(_board(count=400))
    qtbot.addWidget(canvas)
    canvas.resize(900, 500)
    canvas.view.update({"zoom": 0.01, "x": 200000, "y": 50000})
    canvas.grab()
    canvas._begin_ghost()
    assert canvas._ghost is not None
    picture = canvas._ghost["pix"]
    canvas.view["x"] += 5000
    canvas.grab()
    assert canvas._ghost["pix"] is picture                # the same picture, slid
    canvas._end_ghost()
    assert canvas._ghost is None


def test_far_off_strokes_are_fainter_not_bolder():
    assert scribe_tab._far_alpha(1.0 / 0.01, 0.01, 3) < 0.5      # 3 units wide at 1% zoom: a 0.03 px line
    assert scribe_tab._far_alpha(5, 1.0, 5) == 1.0               # a normal stroke is untouched
    assert scribe_tab._far_width(3, 0.01) == 100                 # drawn no thinner than one pixel
