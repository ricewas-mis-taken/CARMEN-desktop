"""Long text wraps onto more rows (never slides sideways); QuickNote strikethroughs."""
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest

import quicknote_store
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


def test_ticking_strikes_unticking_restores_and_text_click_does_not_toggle(qtbot):
    rows = _note(qtbot, ["a", "b"])
    first = rows._edits[0]
    first.checkbox.setChecked(True)
    assert first.struck
    first.checkbox.setChecked(False)
    assert not first.struck
    QTest.mouseClick(first.viewport(), Qt.LeftButton)
    assert not first.struck and not first.checkbox.isChecked()


def test_closing_removes_ticked_items_and_saves(qtbot, isolate_state):
    window = quicknote.QuickNoteWindow()
    qtbot.addWidget(window)
    window.show()
    window._list.set_items(["a", "b", "c"])
    window._list._edits[1].checkbox.setChecked(True)
    assert window._list.items() == ["a", "b", "c"]          # still there until it is closed
    window.close()
    assert window._list.items() == ["a", "c"]
    assert quicknote_store.load()[0] == ["a", "c"]
