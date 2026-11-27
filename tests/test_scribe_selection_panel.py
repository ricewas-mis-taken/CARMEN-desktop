"""The Selection panel (typed position and size, order, duplicate, delete) and the actions behind it."""
import pytest
from PySide6.QtGui import QColor, QImage

import scribe_geometry as geo
import scribe_store
from qt_ui.scribe_tab import ScribeCanvas, ScribeTab


def _rect(a, b):
    return {"type": "rect", "a": list(a), "b": list(b), "stroke": "#000000", "width": 2, "fill": None,
            "dashed": False, "round": True, "scale": True}


@pytest.fixture
def tab(qtbot):
    tab = ScribeTab()
    qtbot.addWidget(tab)
    tab.resize(1300, 800)
    tab.canvas.doc["layers"][0]["items"] = [_rect((0, 0), (100, 50)), _rect((200, 0), (300, 80)),
                                           _rect((400, 0), (450, 40))]
    tab.show()
    qtbot.waitExposed(tab)
    return tab


def _items(tab):
    return tab.canvas.doc["layers"][0]["items"]


def test_the_panel_follows_the_selection(tab):
    assert not tab._box_fields["x"].isEnabled() and "Nothing" in tab._selection_count.text()
    tab.canvas._sel = [_items(tab)[1]]
    assert tab._box_fields["x"].value() == 200 and tab._box_fields["w"].value() == 100
    assert tab._box_fields["h"].value() == 80 and tab._selection_count.text() == "1 selected"
    tab.canvas._sel = _items(tab)[:2]
    assert tab._box_fields["w"].value() == 300 and tab._selection_count.text() == "2 selected"
    tab.canvas._sel = []
    assert not tab._box_fields["w"].isEnabled()


def test_typing_a_position_moves_the_shape_and_the_panel_shows_it(tab):
    item = _items(tab)[0]
    tab.canvas._sel = [item]
    tab._box_fields["x"].setValue(665799.0)
    tab._box_fields["x"].editingFinished.emit()
    assert geo.bounds(item, padded=False)[0] == pytest.approx(665799.0)
    assert tab._box_fields["x"].value() == pytest.approx(665799.0)


def test_typing_a_width_keeps_the_proportions_unless_turned_off(tab):
    item = _items(tab)[0]                                    # 100 wide, 50 tall
    tab.canvas._sel = [item]
    tab._box_fields["w"].setValue(300.0)
    tab._box_fields["w"].editingFinished.emit()
    assert geo.bounds(item, padded=False) == pytest.approx((0, 0, 300, 150))
    tab._keep_ratio.setChecked(False)
    tab._box_fields["h"].setValue(60.0)
    tab._box_fields["h"].editingFinished.emit()
    assert geo.bounds(item, padded=False) == pytest.approx((0, 0, 300, 60))
    tab.canvas.undo()                                        # typed edits in a row are one undo step
    assert geo.bounds(item, padded=False) == pytest.approx((0, 0, 100, 50))


def test_dragging_updates_the_boxes_as_it_goes(tab):
    item = _items(tab)[0]
    tab.canvas._sel = [item]
    geo.translate(item, 40, 10)
    tab.canvas.selectionMoved.emit()
    assert tab._box_fields["x"].value() == 40 and tab._box_fields["y"].value() == 10


def test_front_and_back_reorder_and_undo(tab):
    a, b, c = _items(tab)
    tab.canvas._sel = [a]
    tab._selection_buttons[0].click()                        # Front
    assert _items(tab) == [b, c, a]
    tab.canvas.undo()
    assert _items(tab) == [a, b, c]
    tab.canvas._sel = [c]
    tab._selection_buttons[1].click()                        # Back
    assert _items(tab) == [c, a, b]


def test_forward_and_backward_move_one_step_and_keep_group_order():
    a, b, c, d = "a", "b", "c", "d"
    assert ScribeCanvas._reordered([a, b, c, d], [b], "forward") == [a, c, b, d]
    assert ScribeCanvas._reordered([a, b, c, d], [c], "backward") == [a, c, b, d]
    assert ScribeCanvas._reordered([a, b, c, d], [d], "forward") == [a, b, c, d]            # already on top
    assert ScribeCanvas._reordered([a, b, c, d], [a, c], "forward") == [b, a, d, c]
    assert ScribeCanvas._reordered([a, b, c, d], [b, d], "front") == [a, c, b, d]
    assert ScribeCanvas._reordered([a, b, c, d], [b, d], "back") == [b, d, a, c]


def test_duplicate_and_delete_buttons(tab):
    tab.canvas._sel = [_items(tab)[0]]
    tab._selection_buttons[2].click()                        # Duplicate
    assert len(_items(tab)) == 4
    assert len(tab.canvas._sel) == 1 and tab.canvas._sel[0] is _items(tab)[-1]
    tab._selection_buttons[3].click()                        # Delete
    assert len(_items(tab)) == 3 and tab.canvas._sel == []
    tab.canvas.undo()
    assert len(_items(tab)) == 4


def test_nudging_moves_by_screen_pixels_and_a_run_is_one_undo_step(tab):
    canvas = tab.canvas
    canvas.view["zoom"] = 2.0
    item = _items(tab)[0]
    canvas._sel = [item]
    before = len(canvas._undo)
    for _ in range(5):
        canvas.nudge(1, 0)
    assert geo.bounds(item, padded=False)[0] == pytest.approx(2.5)                 # five pixels at 2x zoom
    assert len(canvas._undo) == before + 1
    canvas.undo()
    assert geo.bounds(item, padded=False)[0] == pytest.approx(0)


def test_zoom_to_selection_centres_and_fits_it(tab):
    canvas = tab.canvas
    canvas._sel = [_items(tab)[2]]                           # 50 x 40 at (400, 0)
    canvas.zoom_to_selection()
    assert canvas.view["x"] == pytest.approx(425) and canvas.view["y"] == pytest.approx(20)
    assert canvas.zoom > 3
    canvas._sel = []
    canvas.zoom_to_selection()                               # nothing selected: fits everything instead
    assert canvas.zoom < 3


def test_a_picture_goes_back_to_its_own_size_around_its_middle(tab, tmp_path):
    path = tmp_path / "pic.png"
    image = QImage(120, 80, QImage.Format_RGB32)
    image.fill(QColor("#336699"))
    assert image.save(str(path))
    name = scribe_store.store_image_file(str(path))
    picture = {"type": "image", "a": [0, 0], "w": 300, "h": 100, "file": name}
    tab.canvas.doc["layers"][0]["items"].append(picture)
    tab.canvas._sel = [picture]
    assert tab._original_size_button.isVisible()
    tab._original_size_button.click()
    assert (picture["w"], picture["h"]) == (120.0, 80.0)
    assert picture["a"] == pytest.approx([90, 10])                                    # still centred on (150, 50)
    tab.canvas.undo()
    assert (picture["w"], picture["h"]) == (300, 100)
    tab.canvas._sel = [_items(tab)[0]]
    assert not tab._original_size_button.isVisible()
