"""Long text wraps onto more rows (never slides sideways); QuickNote strikethroughs."""
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest

import qt_ui.quicknote as quicknote
from qt_ui.wrap_edit import WrapEdit


def test_long_text_wraps_and_grows_taller(qtbot):
    edit = WrapEdit()
    qtbot.addWidget(edit)
    edit.resize(160, 30)
    edit.show()
    one_row = edit.height()
    edit.setText("word " * 40)
    assert edit.height() > one_row * 2
    assert edit.horizontalScrollBar().maximum() == 0


def test_enter_submits_and_never_adds_a_line(qtbot):
    edit = WrapEdit()
    qtbot.addWidget(edit)
    hits = []
    edit.returnPressed.connect(lambda: hits.append(1))
    edit.setText("one\ntwo")
    QTest.keyClick(edit, Qt.Key_Return)
    assert edit.text() == "one two" and hits == [1]


def _note(qtbot, items):
    rows = quicknote.NoteList()
    qtbot.addWidget(rows)
    rows.resize(220, 200)
    rows.show()
    rows.set_items(items)
    return rows


def test_a_click_strikes_a_bullet_and_closing_clears_it(qtbot):
    rows = _note(qtbot, ["a", "b"])
    rows._edits[1].setFocus()
    QTest.mouseClick(rows._edits[0].viewport(), Qt.LeftButton, pos=rows._edits[0].viewport().rect().center())
    assert rows._edits[0].struck and rows.items() == ["a", "b"]
    rows.clear_struck()
    assert not rows._edits[0].struck


def test_the_window_clears_strikes_when_closed(qtbot, isolate_state):
    window = quicknote.QuickNoteWindow()
    qtbot.addWidget(window)
    window.show()
    window._list.set_items(["a", "b"])
    window._list._edits[0].set_struck(True)
    window.close()
    assert not window._list._edits[0].struck
