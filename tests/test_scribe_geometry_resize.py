"""Resizing shapes: scaling about a point, and fitting a group into a new box."""
import pytest

import scribe_geometry as geo


def _box(items):
    return geo.union_bounds(items)


def test_an_image_resizes_by_its_box_and_keeps_its_file():
    image = {"type": "image", "a": [10, 20], "w": 100, "h": 50, "file": "x.png"}
    geo.resize_to_box([image], _box([image]), (0, 0, 200, 100))
    assert image["a"] == [0.0, 0.0] and (image["w"], image["h"]) == (200.0, 100.0) and image["file"] == "x.png"


def test_a_group_keeps_its_arrangement_when_fitted_to_a_new_box():
    a = {"type": "rect", "a": [0, 0], "b": [10, 10], "width": 2}
    b = {"type": "circle", "a": [20, 0], "b": [40, 10], "width": 2}
    geo.resize_to_box([a, b], _box([a, b]), (100, 100, 180, 120))              # twice as wide, twice as tall, moved
    assert _box([a, b]) == pytest.approx((100, 100, 180, 120))
    assert a["a"] == pytest.approx([100, 100]) and b["b"] == pytest.approx([180, 120])


def test_strokes_scale_their_points_and_not_their_pen():
    stroke = {"type": "stroke", "pts": [[0, 0], [10, 10], [20, 0]], "width": 5}
    geo.resize_to_box([stroke], _box([stroke]), (0, 0, 40, 20))
    assert stroke["pts"][-1] == pytest.approx([40, 0]) and stroke["width"] == 5


def test_text_grows_with_its_box():
    text = {"type": "text", "a": [0, 0], "w": 100, "h": 20, "text": "hi", "size": 16}
    geo.resize_to_box([text], _box([text]), (0, 0, 200, 40))
    assert text["w"] == pytest.approx(200) and text["h"] == pytest.approx(40) and text["size"] == pytest.approx(32)
    geo.resize_to_box([text], _box([text]), (0, 0, 1, 1))
    assert text["size"] >= 4                                                    # never shrinks to nothing


def test_curve_and_line_points_all_move_together():
    curve = {"type": "curve", "a": [0, 0], "b": [10, 0], "c": [5, 8], "width": 1}
    geo.scale_item(curve, 2, 2, 0, 0)
    assert curve["a"] == [0, 0] and curve["b"] == [20, 0] and curve["c"] == [10, 16]


def test_a_flat_line_has_no_height_to_scale_so_it_only_moves_that_way():
    line = {"type": "line", "a": [0, 0], "b": [10, 0], "width": 1}
    geo.resize_to_box([line], _box([line]), (5, 7, 25, 30))
    assert line["a"] == pytest.approx([5, 7]) and line["b"] == pytest.approx([25, 7])     # stretched along, moved down


def test_flipping_through_zero_keeps_an_image_in_its_box():
    image = {"type": "image", "a": [0, 0], "w": 10, "h": 10, "file": "x.png"}
    geo.scale_item(image, -2, 1, 0, 0)
    assert image["w"] == 20 and image["a"][0] == -20


def test_resizing_replaces_lists_so_a_saved_step_is_not_changed():
    stroke = {"type": "stroke", "pts": [[0, 0], [10, 10]], "width": 3}
    saved = stroke["pts"]
    geo.scale_item(stroke, 2, 2, 0, 0)
    assert saved == [[0, 0], [10, 10]] and stroke["pts"] is not saved
