"""A brief confetti-fall effect used to celebrate "Finish" actions (adding a
Board task, marking one done). A frameless, click-through, translucent
overlay sized to the screen -- not a child of any particular window -- so it
reads as "falling from the top of the screen" regardless of which popup
triggered it, and never intercepts a click meant for the app underneath it.
"""
import random

from PySide6.QtCore import QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QGuiApplication, QPainter
from PySide6.QtWidgets import QWidget

_COLORS = [
    "#e53935", "#43a047", "#fb8c00", "#8e24aa", "#5B8DEF",
    "#00acc1", "#f4511e", "#fdd835", "#3949ab", "#d81b60",
]

_TICK_MS = 16
_DURATION_MS = 2600
_GRAVITY = 0.28


class _ConfettiOverlay(QWidget):
    def __init__(self, piece_count):
        super().__init__(
            None,
            Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool | Qt.WindowDoesNotAcceptFocus,
        )
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.setAttribute(Qt.WA_DeleteOnClose)
        self.setAttribute(Qt.WA_ShowWithoutActivating)

        screen = QGuiApplication.primaryScreen()
        geometry = screen.geometry() if screen else None
        if geometry is not None:
            self.setGeometry(geometry)

        width = self.width() or 1600
        self._pieces = []
        for _ in range(piece_count):
            self._pieces.append({
                "x": random.uniform(0, width),
                "y": random.uniform(-200, 0),
                "vx": random.uniform(-1.2, 1.2),
                "vy": random.uniform(1.5, 4.0),
                "size": random.uniform(6, 12),
                "color": QColor(random.choice(_COLORS)),
                "angle": random.uniform(0, 360),
                "spin": random.uniform(-8, 8),
            })

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._advance)
        self._timer.start(_TICK_MS)

        self._elapsed = 0

    def _advance(self):
        self._elapsed += _TICK_MS
        for piece in self._pieces:
            piece["vy"] += _GRAVITY * (_TICK_MS / 16)
            piece["x"] += piece["vx"] * (_TICK_MS / 16)
            piece["y"] += piece["vy"] * (_TICK_MS / 16)
            piece["angle"] += piece["spin"]
        self.update()
        if self._elapsed >= _DURATION_MS:
            self._timer.stop()
            self.close()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        for piece in self._pieces:
            painter.save()
            painter.translate(piece["x"], piece["y"])
            painter.rotate(piece["angle"])
            painter.setBrush(piece["color"])
            painter.setPen(Qt.NoPen)
            size = piece["size"]
            painter.drawRect(QRectF(-size / 2, -size / 4, size, size / 2))
            painter.restore()


_active_overlays = set()


def show_confetti(piece_count=140):
    """Fire-and-forget: creates the overlay, shows it, and lets it clean
    itself up (WA_DeleteOnClose) once its fall finishes."""
    overlay = _ConfettiOverlay(piece_count)
    _active_overlays.add(overlay)
    overlay.destroyed.connect(lambda: _active_overlays.discard(overlay))
    overlay.show()
    return overlay
