"""Options: the popup behind the profile menu. For now it holds the colours."""
import os

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QApplication, QColorDialog, QGridLayout, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget,
)

import theme
from qt_ui import ui_kit
from qt_ui.ui_kit import SECONDARY_STYLE, accent, dialog_title, field_label, role_label


def reapply_stylesheet():
    """Repaint the app's stylesheet now. Colours written straight into a widget's own
    style (ink buttons, numerals, Home's cards) update the next time the app starts."""
    path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "styles.qss")
    try:
        with open(path, encoding="utf-8") as f:
            QApplication.instance().setStyleSheet(
                ui_kit.with_check_icon(theme.apply_to_stylesheet(f.read(), theme.current_colours())))
    except OSError:
        pass


class OptionsDialog(QWidget):
    def __init__(self):
        super().__init__(None, Qt.WindowStaysOnTopHint)
        self.setObjectName("PopupBg")
        self.setWindowTitle("Options")
        self.resize(500, 420)
        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(24, 22, 24, 20)
        self._layout.setSpacing(10)
        self._layout.addWidget(dialog_title("Options", "Colours apply to this app and the browser extension."))
        self._layout.addWidget(field_label("Colours"))
        self._grid = QGridLayout()
        self._grid.setSpacing(10)
        self._layout.addLayout(self._grid)
        self._note = role_label("", "hint", wrap=True)
        self._layout.addWidget(self._note)
        self._layout.addWidget(role_label(
            "Buttons and numbers pick up a new colour the next time Carmen starts.", "hint", wrap=True))
        self._layout.addStretch(1)
        row = QHBoxLayout()
        row.addStretch(1)
        done = accent(QPushButton("Done"))
        done.clicked.connect(self.close)
        row.addWidget(done)
        self._layout.addLayout(row)
        self._fill()
        self.show()

    def _fill(self):
        while self._grid.count():
            item = self._grid.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        setting = theme.load_setting()
        current = setting.get("name") if isinstance(setting, dict) else theme.DEFAULT_THEME
        for i, (name, t) in enumerate(theme.THEMES.items()):
            tile = QPushButton(name)
            tile.setCursor(Qt.PointingHandCursor)
            tile.setToolTip(t["why"])
            tile.setFixedHeight(52)
            border = f"2px solid {t['accent']}" if name == current else "1px solid #E7EAF0"
            tile.setStyleSheet(
                f"QPushButton{{background:{t['page']};border:{border};border-radius:12px;"
                f"color:{t['ink']};font-size:13px;font-weight:600;text-align:left;padding:0 14px;}}")
            tile.clicked.connect(lambda _c=False, n=name: self._choose(n))
            self._grid.addWidget(tile, i // 3, i % 3)
        custom = QPushButton("Make your own")
        custom.setCursor(Qt.PointingHandCursor)
        custom.setFixedHeight(52)
        custom.setStyleSheet(SECONDARY_STYLE)
        custom.clicked.connect(self._open_custom)
        self._grid.addWidget(custom, len(theme.THEMES) // 3, len(theme.THEMES) % 3)
        self._note.setText(theme.THEMES[current]["why"] if current in theme.THEMES else "Your own colours.")

    def _choose(self, name):
        theme.save_setting({"name": name})
        reapply_stylesheet()
        self._fill()

    def _open_custom(self):
        self._dialog = _CustomThemeDialog(on_saved=lambda: (reapply_stylesheet(), self._fill()))


class _CustomThemeDialog(QWidget):
    LABELS = {
        "ink": "Text and buttons", "muted": "Secondary text", "faint": "Hints",
        "accent": "Accent", "page": "Page background", "sidebar": "Sidebar",
    }

    def __init__(self, on_saved):
        super().__init__(None, Qt.WindowStaysOnTopHint)
        self.setObjectName("PopupBg")
        self.setWindowTitle("Your colours")
        self.resize(420, 520)
        self._on_saved = on_saved
        self._colours = dict(theme.current_colours())

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 22, 24, 20)
        layout.setSpacing(8)
        layout.addWidget(dialog_title("Your colours", "Cards stay white. The rest is yours."))
        self._swatches = {}
        for key in theme.TOKENS:
            row = QHBoxLayout()
            row.addWidget(QLabel(self.LABELS[key]), 1)
            swatch = QPushButton()
            swatch.setFixedSize(76, 30)
            swatch.setCursor(Qt.PointingHandCursor)
            swatch.clicked.connect(lambda _c=False, k=key: self._pick(k))
            self._swatches[key] = swatch
            row.addWidget(swatch)
            layout.addLayout(row)
            self._paint(key)
        layout.addStretch(1)
        buttons = QHBoxLayout()
        buttons.addStretch(1)
        cancel = QPushButton("Cancel")
        cancel.clicked.connect(self.close)
        buttons.addWidget(cancel)
        save = accent(QPushButton("Use these"))
        save.clicked.connect(self._save)
        buttons.addWidget(save)
        layout.addLayout(buttons)
        self.show()

    def _paint(self, key):
        self._swatches[key].setStyleSheet(
            f"background:{self._colours[key]};border:1px solid #0003;border-radius:8px;")

    def _pick(self, key):
        chosen = QColorDialog.getColor(QColor(self._colours[key]), self, self.LABELS[key])
        if chosen.isValid():
            self._colours[key] = chosen.name().upper()
            self._paint(key)

    def _save(self):
        theme.save_setting({"name": "Custom", "custom": dict(self._colours)})
        self.close()
        self._on_saved()
