"""QuickNote: a small always-on-top scratchpad of bullet points.

Enter starts a new bullet (splitting the text at the cursor), Backspace on an
empty item removes it, Up/Down move between items. Long items wrap onto more rows.
Each item has a checkbox: ticking it greys the item out with a strikethrough
(untick to undo); ticked items disappear when the window is closed. Everything is saved as you
type to quicknote_store (and so is shared with your other computers).
"""
from PySide6.QtCore import QRectF, Qt, QTimer, Signal
from PySide6.QtGui import (
    QColor, QFontMetrics, QGuiApplication, QPainter, QPainterPath, QPen, QTextCharFormat, QTextCursor,
)
from PySide6.QtWidgets import QCheckBox, QHBoxLayout, QScrollArea, QVBoxLayout, QWidget

import config
import quicknote_store
from qt_ui.wrap_edit import WrapEdit

SAVE_DELAY_MS = 400
TEXT_PX = 15
REMOTE_CHECK_MS = 1000
DEFAULT_SIZE = (340, 420)

_STYLE = """
QWidget#QuickNote { background: #111317; }
QScrollArea { background: transparent; border: none; }
QWidget#QuickNoteRows { background: transparent; }
QPlainTextEdit#WrapEdit { background: transparent; border: none; color: #F2F4F7; padding: 0px 2px; }
QPlainTextEdit#WrapEdit:focus { background: rgba(255, 255, 255, 0.05); border-radius: 4px; }
"""

GREY = QColor("#6B7280")
NORMAL = QColor("#F2F4F7")
TICK_GREEN = QColor("#22C55E")


class _Tick(QCheckBox):
    """A square exactly one line of text tall: empty outline, or green with a white tick."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._draw = 1.0 if self.isChecked() else 0.0     # 0..1: how much of the tick is drawn
        self._anim = None
        self.toggled.connect(self._animate)

    def _animate(self, on):
        from qt_ui import motion
        motion.stop(self._anim)

        def step(value):
            self._draw = value
            self.update()
        self._anim = motion.tween(self, self._draw, 1.0 if on else 0.0, motion.SMALL_MS if on else motion.HOVER_MS, step)

    def paintEvent(self, _event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        side = min(self.width(), self.height()) - 3
        box = QRectF((self.width() - side) / 2, (self.height() - side) / 2, side, side)
        radius = side * 0.25
        if self._draw < 1.0 or not self.isChecked():
            painter.setPen(QPen(QColor("#5B8DEF") if self.underMouse() else GREY, 1.5))
            painter.setBrush(Qt.NoBrush)
            painter.drawRoundedRect(box, radius, radius)
        if self._draw > 0.0:
            fill = QColor(TICK_GREEN)
            fill.setAlphaF(min(1.0, self._draw * 2.2))
            painter.setPen(Qt.NoPen)
            painter.setBrush(fill)
            painter.drawRoundedRect(box, radius, radius)
            pen = QPen(QColor("#FFFFFF"), max(1.6, side * 0.13), Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin)
            painter.setPen(pen)
            painter.setBrush(Qt.NoBrush)
            a = (box.left() + side * 0.24, box.top() + side * 0.54)
            b = (box.left() + side * 0.43, box.top() + side * 0.72)
            c = (box.left() + side * 0.78, box.top() + side * 0.30)
            first = min(1.0, self._draw / 0.4)
            second = max(0.0, (self._draw - 0.4) / 0.6)
            tick = QPainterPath()
            tick.moveTo(*a)
            tick.lineTo(a[0] + (b[0] - a[0]) * first, a[1] + (b[1] - a[1]) * first)
            if second > 0:
                tick.lineTo(b[0] + (c[0] - b[0]) * second, b[1] + (c[1] - b[1]) * second)
            painter.drawPath(tick)


class _BulletEdit(WrapEdit):
    """One item's text. It wraps onto more rows when long. When its checkbox is
    ticked it is greyed out with a strikethrough."""
    enter_pressed = Signal(object)        # self
    backspace_on_empty = Signal(object)
    move = Signal(object, int)            # self, -1 up / +1 down

    def __init__(self, text=""):
        super().__init__(text, max_rows=50, boxed=False)
        self.struck = False
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
        """Grey + line-through while ticked."""
        marked = self.struck
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


class NoteList(QWidget):
    """The bullets themselves: a column of one-line editors."""
    changed = Signal()

    def __init__(self):
        super().__init__()
        self.setObjectName("QuickNoteRows")
        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(0, 0, 0, 0)
        self._layout.setSpacing(7)
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
        row_layout.setSpacing(10)
        edit = _BulletEdit(text)
        font = edit.font()
        font.setPixelSize(TEXT_PX)
        edit.setFont(font)
        edit.document().setDocumentMargin(0)               # the first line starts at the very top
        edit._fit_height()
        # The checkbox is exactly one line of text tall and wide, level with the first line.
        line = QFontMetrics(font).lineSpacing()
        side = round(line * 0.9)               # a touch smaller than the line, so it reads as a tick box, not a block
        box = _Tick()
        box.setFixedSize(side, side)
        edit.checkbox = box
        box.toggled.connect(edit.set_struck)
        edit.enter_pressed.connect(self._on_enter)
        edit.backspace_on_empty.connect(self._on_backspace_on_empty)
        edit.move.connect(self._on_move)
        edit.textChanged.connect(self.changed.emit)
        holder = QVBoxLayout()
        holder.setContentsMargins(0, (line - side) // 2, 0, 0)       # centred on the first line of text
        holder.addWidget(box, 0, Qt.AlignTop)
        row_layout.addLayout(holder)
        row_layout.addWidget(edit, 1)
        self._layout.insertWidget(index, row)
        self._edits.insert(index, edit)
        return edit

    def _on_enter(self, edit):
        index = self._edits.index(edit)
        cursor = edit.cursorPosition()
        text = edit.text()
        edit.setText(text[:cursor])
        edit._paint()            # setText drops the ticked look
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

    def remove_checked(self):
        """Drops every ticked item (what closing the window does)."""
        ticked = [edit for edit in self._edits if edit.checkbox.isChecked()]
        if not ticked:
            return
        for edit in ticked:
            self._edits.remove(edit)
            self._discard_row(edit)
        if not self._edits:
            self._add_row(0, "")
        self.changed.emit()

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
        layout.setContentsMargins(18, 16, 18, 16)
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

    def bring_forward(self):
        """Shows the window on top, exactly where it was: a minimised or buried one
        comes back without being moved or resized."""
        if self.isMinimized():
            self.setWindowState((self.windowState() & ~Qt.WindowMinimized) | Qt.WindowActive)
        self.show()
        self.raise_()
        self.activateWindow()

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
        self._list.remove_checked()
        self._save()
        self._save_geometry()
        super().closeEvent(event)

    def hideEvent(self, event):
        self._list.remove_checked()
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
    _win.bring_forward()
    _win._list.focus_last()
