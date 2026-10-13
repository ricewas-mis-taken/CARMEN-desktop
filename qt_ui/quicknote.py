"""QuickNote: a small always-on-top scratchpad of bullet points.

Enter starts a new bullet (splitting the text at the cursor), Backspace on an
empty bullet removes it, Up/Down move between bullets. Long bullets wrap onto more
rows. Hover greys a bullet with a strikethrough; a click keeps it struck through
until the window is closed (strikes are never saved). Everything is saved as you
type to quicknote_store (and so is shared with your other computers).
"""
from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QColor, QGuiApplication, QTextCharFormat, QTextCursor
from PySide6.QtWidgets import QHBoxLayout, QLabel, QScrollArea, QVBoxLayout, QWidget

import config
import quicknote_store
from qt_ui.wrap_edit import WrapEdit

SAVE_DELAY_MS = 400
REMOTE_CHECK_MS = 1000
DEFAULT_SIZE = (320, 380)

_STYLE = """
QWidget#QuickNote { background: #111317; }
QScrollArea { background: transparent; border: none; }
QWidget#QuickNoteRows { background: transparent; }
QLabel#Bullet { color: #6B7280; font-size: 16px; }
QPlainTextEdit#WrapEdit { background: transparent; border: none; color: #F2F4F7; font-size: 15px; padding: 0px 2px; }
QPlainTextEdit#WrapEdit:focus { background: rgba(255, 255, 255, 0.05); border-radius: 4px; }
"""

GREY = QColor("#6B7280")
NORMAL = QColor("#F2F4F7")


class _BulletEdit(WrapEdit):
    """One bullet. It wraps onto more rows when the text is long. Hovering greys it
    out with a strikethrough; clicking it (when it is not being edited) strikes it
    through for good, until the QuickNote window is closed."""
    enter_pressed = Signal(object)        # self
    backspace_on_empty = Signal(object)
    move = Signal(object, int)            # self, -1 up / +1 down

    def __init__(self, text=""):
        super().__init__(text, max_rows=50, boxed=False)
        self.struck = False
        self._hovered = False
        self._click_was_unfocused = False
        self.setMouseTracking(True)
        self.viewport().setMouseTracking(True)
        self.returnPressed.connect(lambda: self.enter_pressed.emit(self))

    def keyPressEvent(self, event):
        key = event.key()
        if key == Qt.Key_Backspace and not self.text():
            self.backspace_on_empty.emit(self)
        elif key in (Qt.Key_Up, Qt.Key_Down) and not event.modifiers() and self._on_edge(key == Qt.Key_Up):
            self.move.emit(self, -1 if key == Qt.Key_Up else 1)
        else:
            super().keyPressEvent(event)

    def _on_edge(self, up):
        """True when the cursor is on the first (up) / last (down) wrapped row."""
        probe = QTextCursor(self.textCursor())
        before = probe.position()
        probe.movePosition(QTextCursor.Up if up else QTextCursor.Down)
        return probe.position() == before

    # --- strikethrough ---

    def set_struck(self, struck):
        self.struck = struck
        self._paint()

    def _paint(self):
        """Grey + line-through while struck or hovered (and not being edited)."""
        marked = self.struck or (self._hovered and not self.hasFocus())
        fmt = QTextCharFormat()
        fmt.setFontStrikeOut(marked)
        fmt.setForeground(GREY if marked else NORMAL)
        cursor = QTextCursor(self.document())
        cursor.select(QTextCursor.Document)
        blocked = self.document().blockSignals(True)      # a look, not an edit: nothing to save
        cursor.mergeCharFormat(fmt)
        self.document().blockSignals(blocked)
        merged = QTextCharFormat(fmt)
        self.setCurrentCharFormat(merged)                 # text typed next looks the same

    def enterEvent(self, event):
        self._hovered = True
        self._paint()
        super().enterEvent(event)

    def leaveEvent(self, event):
        self._hovered = False
        self._paint()
        super().leaveEvent(event)

    def mousePressEvent(self, event):
        self._click_was_unfocused = event.button() == Qt.LeftButton and not self.hasFocus()
        if self._click_was_unfocused:
            return                                        # a first click strikes, it does not edit
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event):
        if self._click_was_unfocused and event.button() == Qt.LeftButton:
            self._click_was_unfocused = False
            self.set_struck(not self.struck)
            return
        super().mouseReleaseEvent(event)

    def focusInEvent(self, event):
        super().focusInEvent(event)
        self._paint()

    def focusOutEvent(self, event):
        super().focusOutEvent(event)
        self._paint()


class NoteList(QWidget):
    """The bullets themselves: a column of one-line editors."""
    changed = Signal()

    def __init__(self):
        super().__init__()
        self.setObjectName("QuickNoteRows")
        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(0, 0, 0, 0)
        self._layout.setSpacing(2)
        self._layout.addStretch(1)
        self._edits = []
        self.set_items([""])

    def items(self):
        return [edit.text() for edit in self._edits]

    def set_items(self, items):
        for edit in self._edits:
            self._discard_row(edit)
        self._edits = []
        for text in (items or [""]):
            self._add_row(len(self._edits), text)

    def _discard_row(self, edit):
        """Takes a bullet's row out of the layout right now (not at some later
        event-loop turn, when a new row could already have been put beside it)."""
        row = edit.parentWidget()
        self._layout.removeWidget(row)
        row.setParent(None)
        row.deleteLater()

    def _add_row(self, index, text=""):
        row = QWidget()
        row_layout = QHBoxLayout(row)
        row_layout.setContentsMargins(0, 0, 0, 0)
        row_layout.setSpacing(6)
        bullet = QLabel("•")
        bullet.setObjectName("Bullet")
        edit = _BulletEdit(text)
        edit.enter_pressed.connect(self._on_enter)
        edit.backspace_on_empty.connect(self._on_backspace_on_empty)
        edit.move.connect(self._on_move)
        edit.textChanged.connect(self.changed.emit)
        row_layout.addWidget(bullet, 0, Qt.AlignTop)
        row_layout.addWidget(edit, 1)
        self._layout.insertWidget(index, row)
        self._edits.insert(index, edit)
        return edit

    def _on_enter(self, edit):
        index = self._edits.index(edit)
        cursor = edit.cursorPosition()
        text = edit.text()
        edit.setText(text[:cursor])
        new = self._add_row(index + 1, text[cursor:])
        new.setFocus()
        new.setCursorPosition(0)
        self.changed.emit()

    def _on_backspace_on_empty(self, edit):
        if len(self._edits) == 1:
            return                               # the last bullet stays, empty
        index = self._edits.index(edit)
        self._edits.pop(index)
        self._discard_row(edit)
        target = self._edits[max(0, index - 1)]
        target.setFocus()
        target.setCursorPosition(len(target.text()))
        self.changed.emit()

    def _on_move(self, edit, step):
        index = self._edits.index(edit) + step
        if 0 <= index < len(self._edits):
            self._edits[index].setFocus()
            self._edits[index].setCursorPosition(len(self._edits[index].text()))

    def clear_struck(self):
        for edit in self._edits:
            if edit.struck:
                edit.set_struck(False)

    def focus_last(self):
        edit = self._edits[-1]
        edit.setFocus()
        edit.setCursorPosition(len(edit.text()))

    def has_focus(self):
        return any(edit.hasFocus() for edit in self._edits)


class QuickNoteWindow(QWidget):
    def __init__(self):
        super().__init__(None, Qt.Window | Qt.WindowStaysOnTopHint)
        self.setObjectName("QuickNote")
        self.setWindowTitle("QuickNote")
        self.setMinimumSize(220, 160)
        self.setStyleSheet(_STYLE)

        self._list = NoteList()
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(self._list)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 12, 14, 12)
        layout.addWidget(scroll)

        self._dirty = False
        self._saved_stamp = None
        self._save_timer = QTimer(self)
        self._save_timer.setSingleShot(True)
        self._save_timer.setInterval(SAVE_DELAY_MS)
        self._save_timer.timeout.connect(self._save)
        self._list.changed.connect(self._on_changed)

        self._remote_timer = QTimer(self)
        self._remote_timer.setInterval(REMOTE_CHECK_MS)
        self._remote_timer.timeout.connect(self._pick_up_remote_change)
        self._remote_timer.start()

        self._restore_geometry()
        self._load()

    # --- saving and loading ---

    def _load(self):
        items, stamp = quicknote_store.load()
        self._list.set_items(items)
        self._saved_stamp = stamp
        self._dirty = False

    def _on_changed(self):
        self._dirty = True
        self._save_timer.start()

    def _save(self):
        self._save_timer.stop()
        if not self._dirty:
            return
        quicknote_store.save(self._list.items())
        self._dirty = False
        self._saved_stamp = quicknote_store.export()["updatedAt"]

    def _pick_up_remote_change(self):
        """The note was changed from outside (another computer): show it, unless
        something typed here is still waiting to be saved."""
        if self._dirty:
            return
        stamp = quicknote_store.export()["updatedAt"]
        if stamp != self._saved_stamp:
            had_focus = self._list.has_focus()
            self._load()
            if had_focus:
                self._list.focus_last()

    # --- remembered position ---

    def _restore_geometry(self):
        import qt_ui.mini_timer as mini_timer
        screens = [screen.availableGeometry() for screen in QGuiApplication.screens()]
        saved = mini_timer.visible_geometry(config.load_config().get("quickNoteGeometry"), screens)
        if saved:
            self.setGeometry(*saved)
        else:
            self.resize(*DEFAULT_SIZE)

    def _save_geometry(self):
        g = self.geometry()
        value = [g.x(), g.y(), g.width(), g.height()]
        config.update_config(lambda cfg: cfg.update({"quickNoteGeometry": value}))

    def closeEvent(self, event):
        self._list.clear_struck()
        self._save()
        self._save_geometry()
        super().closeEvent(event)

    def hideEvent(self, event):
        self._list.clear_struck()
        self._save()
        super().hideEvent(event)


_win = None


def open_quicknote():
    """Shows the one QuickNote window (a second call just brings it forward)."""
    global _win
    if _win is None:
        _win = QuickNoteWindow()
    else:
        _win._pick_up_remote_change()
    _win.show()
    _win.raise_()
    _win.activateWindow()
    _win._list.focus_last()
