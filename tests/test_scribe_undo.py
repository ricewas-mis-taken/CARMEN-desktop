"""Scribe undo and redo on a big board: steps keep the same item objects (so nothing cached for an item is lost),
add and remove steps patch the minimap heat instead of walking every point, and every kind of edit comes back."""
import random

import pytest
from PySide6.QtCore import QRectF

import scribe_store
from qt_ui.scribe_tab import ScribeCanvas


def _stroke(rng, x=None, y=None, points=12):
    x = rng.uniform(-900, 900) if x is None else x
    y = rng.uniform(-600, 600) if y is None else y
    pts = []
    for _ in range(points):
        x += rng.uniform(-8, 8)
        y += rng.uniform(-8, 8)
        pts.append([x, y])
    return {"type": "stroke", "pts": pts, "stroke": "#000000", "width": 3, "fill": None,
            "dashed": False, "round": True, "scale": True}


@pytest.fixture
def board(qtbot):
    doc = scribe_store.default_doc()
    rng = random.Random(8)
    doc["layers"][0]["items"] = [_stroke(rng) for _ in range(40)]
    doc["view"]["zoom"] = 0.3
    canvas = ScribeCanvas(doc)
    qtbot.addWidget(canvas)
    canvas.resize(900, 600)
    canvas.show()
    qtbot.waitExposed(canvas)
    canvas.grab()
    return canvas


def _items(canvas):
    return canvas.doc["layers"][0]["items"]


def test_undoing_a_move_puts_the_same_objects_back(board):
    items = _items(board)
    mover = items[5]
    before_pts, objects = mover["pts"], list(items)
    board._sel = [mover]
    board._push_undo(touching=[mover])
    from scribe_geometry import translate
    translate(mover, 50, 20)
    board.undo()
    assert mover["pts"] == before_pts
    assert [id(i) for i in _items(board)] == [id(i) for i in objects]       # nothing was swapped for a copy
    board.redo()
    assert mover["pts"][0][0] == before_pts[0][0] + 50
    assert [id(i) for i in _items(board)] == [id(i) for i in objects]


def test_undoing_a_restyle_and_redoing_it(board):
    items = _items(board)
    board._sel = [items[2], items[3]]
    board.set_width(11)
    assert items[2]["width"] == 11
    board.undo()
    assert items[2]["width"] == 3 and items[3]["width"] == 3
    board.redo()
    assert items[2]["width"] == 11 and items[3]["width"] == 11


def test_delete_cut_and_clear_come_back_in_their_old_order(board):
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QKeyEvent
    from PySide6.QtCore import QEvent
    items = _items(board)
    order = [id(i) for i in items]
    board._sel = [items[10], items[11], items[30]]
    board.keyPressEvent(QKeyEvent(QEvent.KeyPress, Qt.Key_Delete, Qt.NoModifier))
    assert len(_items(board)) == 37
    board.undo()
    assert [id(i) for i in _items(board)] == order
    board.redo()
    assert len(_items(board)) == 37
    board.undo()
    board.clear_board()
    assert _items(board) == []
    board.undo()
    assert [id(i) for i in _items(board)] == order
    board._sel = [_items(board)[0]]
    assert board.cut_selection()
    assert len(_items(board)) == 39
    board.undo()
    assert [id(i) for i in _items(board)] == order


def test_layer_changes_undo_and_redo(board):
    board.add_layer()
    assert len(board.doc["layers"]) == 2 and board.doc["activeLayer"] == 1
    board.undo()
    assert len(board.doc["layers"]) == 1
    board.redo()
    assert len(board.doc["layers"]) == 2
    board.set_active_layer(0)
    first = board.doc["layers"][0]
    board.remove_layer()
    assert board.doc["layers"][0] is not first
    board.undo()
    assert board.doc["layers"][0] is first and len(first["items"]) == 40
    board.move_layer(1)
    assert board.doc["layers"][1] is first
    board.undo()
    assert board.doc["layers"][0] is first


def test_a_default_push_remembers_every_item_so_nothing_is_missed(board):
    items = _items(board)
    board._push_undo()                                   # no list given: the safe default covers everything
    items[0]["width"] = 99
    items[7]["stroke"] = "#ff0000"
    board.undo()
    assert items[0]["width"] == 3 and items[7]["stroke"] == "#000000"


def test_undoing_a_stroke_does_not_walk_the_whole_minimap_again(board, monkeypatch):
    rect = QRectF(0, 0, 250, 180)
    scale, ox, oy = board._minimap_map(rect, content_only=True)
    board._heat(rect, scale, ox, oy)                     # cells known for the board as it is
    walked = []
    real = ScribeCanvas._heat_compute
    monkeypatch.setattr(ScribeCanvas, "_heat_compute",
                        lambda self, *a, **k: walked.append(1) or real(self, *a, **k))
    item = _stroke(random.Random(3), 0, 0)
    board._push_added(item)
    board._active_items().append(item)
    board._board_edited()
    added = dict(board._heat(rect, scale, ox, oy))
    board.undo()
    after_undo = dict(board._heat(rect, scale, ox, oy))
    board.redo()
    after_redo = dict(board._heat(rect, scale, ox, oy))
    assert walked == []                                  # patched each time, never rebuilt
    monkeypatch.setattr(ScribeCanvas, "_heat_compute", real)
    assert after_redo == added == board._heat_compute(rect, scale, ox, oy)
    board.undo()
    assert after_undo == dict(board._heat(rect, scale, ox, oy)) == board._heat_compute(rect, scale, ox, oy)
    assert after_undo != added                           # and the new stroke really was taken away


def test_a_move_is_drawn_from_the_cells_known_and_rebuilt_between_paints(board, monkeypatch, qtbot):
    rect = board._minimap_rect()
    assert rect is not None
    scale, ox, oy = board._minimap_map(rect, content_only=True)
    board._heat(rect, scale, ox, oy)
    walked = []
    real = ScribeCanvas._heat_compute
    monkeypatch.setattr(ScribeCanvas, "_heat_compute",
                        lambda self, *a, **k: walked.append(1) or real(self, *a, **k))
    mover = _items(board)[4]
    board._sel = [mover]
    board._push_undo(touching=[mover])
    from scribe_geometry import translate
    translate(mover, 400, 300)
    board.grab()                                         # a paint with the old cells: no rebuild inside it
    assert walked == [] and board._heat_job is not None
    qtbot.waitUntil(lambda: board._heat_job is None, timeout=5000)
    scale, ox, oy = board._minimap_map(rect, content_only=True)
    monkeypatch.setattr(ScribeCanvas, "_heat_compute", real)
    assert board._heat_cache["heat"] == board._heat_compute(rect, scale, ox, oy)
    assert board._heat_cache.get("stale") is not True


def test_taking_away_a_stroke_leaves_no_empty_cells(board):
    rect = QRectF(0, 0, 250, 180)
    scale, ox, oy = board._minimap_map(rect, content_only=True)
    before = dict(board._heat(rect, scale, ox, oy))
    item = _stroke(random.Random(5), 10, 10)
    board._push_added(item)
    board._active_items().append(item)
    board._board_edited()
    board._heat(rect, scale, ox, oy)
    board.undo()
    after = board._heat(rect, scale, ox, oy)
    assert after == before and all(v > 0 for v in after.values())
