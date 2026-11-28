"""A row of colour swatches plus a "+" button for any custom colour.

Used by the task editor and the review tab's subject/category forms, so a
user with more tasks or subjects than palette colours can still give each
one its own colour. Colours stay plain "#rrggbb" strings, exactly like the
palette ones, so nothing that stores or syncs them has to change.
"""
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QColorDialog, QHBoxLayout, QPushButton, QWidget


def normalize_hex(value):
    """'#ABC', 'abcdef', QColor -> '#aabbcc'; None if it isn't a valid colour."""
    color = value if isinstance(value, QColor) else QColor(str(value or "").strip() if str(value or "").strip().startswith("#") else "#" + str(value or "").strip())
    return color.name().lower() if color.isValid() else None


class ColorChoice(QWidget):
    def __init__(self, palette, initial=None, on_change=None, size=28, round=True, parent=None):
        super().__init__(parent)
        self._palette = [normalize_hex(h) for h in palette]
        self._on_change = on_change
        self._size = size
        self._radius = size // 2 if round else 4
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        self._buttons = {}
        for hexval in self._palette:
            row.addWidget(self._make(hexval))
        # one slot for a custom colour, shown only once one is chosen
        self._custom_btn = self._make(None)
        self._custom_btn.hide()
        row.addWidget(self._custom_btn)
        self._plus = QPushButton("+")
        self._plus.setFixedSize(size, size)
        self._plus.setCursor(Qt.PointingHandCursor)
        self._plus.setToolTip("Pick any colour")
        self._plus.setStyleSheet(
            f"border-radius: {self._radius}px; border: 1px dashed #8a93a6; font-weight: 700; padding: 0;")
        self._plus.clicked.connect(self._open_dialog)
        row.addWidget(self._plus)
        row.addStretch(1)
        self._value = None
        self.set_color(initial or self._palette[0], notify=False)

    def _make(self, hexval):
        btn = QPushButton()
        btn.setFixedSize(self._size, self._size)
        btn.setCursor(Qt.PointingHandCursor)
        if hexval:
            btn.setToolTip(hexval)
            btn.clicked.connect(lambda _c=False, h=hexval: self.set_color(h))
            self._buttons[hexval] = btn
        else:
            btn.clicked.connect(lambda _c=False: self.set_color(self._custom_btn.property("hex")))
        return btn

    def color(self):
        return self._value

    def set_color(self, value, notify=True):
        hexval = normalize_hex(value) or self._palette[0]
        self._value = hexval
        if hexval not in self._buttons:
            self._custom_btn.setProperty("hex", hexval)
            self._custom_btn.setToolTip(hexval)
            self._custom_btn.show()
        for h, btn in self._buttons.items():
            self._paint(btn, h, h == hexval)
        custom = self._custom_btn.property("hex")
        if custom:
            self._paint(self._custom_btn, custom, custom == hexval)
        if notify and self._on_change:
            self._on_change(hexval)

    def _paint(self, btn, hexval, selected):
        border = "3px solid #1B2230" if selected else "1px solid #0002"
        btn.setStyleSheet(f"background: {hexval}; border-radius: {self._radius}px; border: {border}; padding: 0;")

    def _open_dialog(self):
        picked = QColorDialog.getColor(QColor(self._value), self.window(), "Pick a colour")
        if picked.isValid():
            self.set_color(picked)
