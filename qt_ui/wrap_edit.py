"""A text box that wraps long text onto new rows instead of sliding sideways.

Drop-in for QLineEdit where people type sentences (names, titles, reasons): same
text()/setText()/returnPressed/textChanged()/setPlaceholderText API, but it grows
taller as the text wraps. Enter never inserts a newline (it emits returnPressed), and
pasted line breaks become spaces, so the value stays a single line of text.
"""
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QFontMetrics, QTextCursor, QTextOption
from PySide6.QtWidgets import QFrame, QPlainTextEdit, QSizePolicy

BOXED_STYLE = """
QPlainTextEdit#WrapEdit {
    background: #2C2F37; color: #F2F3F5; border: 1px solid #3F444D;
    border-radius: 8px; padding: 4px 8px;
}
QPlainTextEdit#WrapEdit:focus { border: 1px solid #5B8DEF; }
QPlainTextEdit#WrapEdit:disabled { color: #8A8F98; }
"""


class WrapEdit(QPlainTextEdit):
    returnPressed = Signal()

    def __init__(self, text="", max_rows=6, boxed=True):
        super().__init__()
        self.setObjectName("WrapEdit")
        self._max_rows = max_rows
        self.setLineWrapMode(QPlainTextEdit.WidgetWidth)
        self.setWordWrapMode(QTextOption.WrapAtWordBoundaryOrAnywhere)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setTabChangesFocus(True)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        if boxed:
            self.setStyleSheet(BOXED_STYLE)
        else:
            self.setFrameShape(QFrame.NoFrame)
        self.document().documentLayout().documentSizeChanged.connect(lambda _size: self._fit_height())
        if text:
            self.setText(text)
        self._fit_height()

    # --- QLineEdit-compatible surface ---

    def text(self):
        return self.toPlainText()

    def setText(self, text):
        self.setPlainText(" ".join(str(text or "").splitlines()))
        self.moveCursor(QTextCursor.End)                  # like QLineEdit: cursor after the text

    def setPlaceholderText(self, text):
        super().setPlaceholderText(text)

    def cursorPosition(self):
        return self.textCursor().position()

    def setCursorPosition(self, position):
        cursor = self.textCursor()
        cursor.setPosition(max(0, min(position, len(self.text()))))
        self.setTextCursor(cursor)

    # --- behaviour ---

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key_Return, Qt.Key_Enter):
            self.returnPressed.emit()
            return
        super().keyPressEvent(event)

    def insertFromMimeData(self, source):
        if source.hasText():
            self.insertPlainText(" ".join(source.text().splitlines()))

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._fit_height()

    def _fit_height(self):
        """As tall as the wrapped text (capped at max_rows, then it scrolls)."""
        metrics = QFontMetrics(self.font())
        rows = max(1, min(self._max_rows, round(self.document().size().height())))
        margin = int(self.document().documentMargin() * 2) + 2 * self.frameWidth()
        pad = 10 if self.styleSheet() else 0
        self.setFixedHeight(rows * metrics.lineSpacing() + margin + pad)
        self.setVerticalScrollBarPolicy(
            Qt.ScrollBarAsNeeded if self.document().size().height() > self._max_rows else Qt.ScrollBarAlwaysOff
        )
