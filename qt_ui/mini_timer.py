"""Small always-on-top timer window, opened from the tray menu.

Counts down inside a blue ring for timed sessions; for count-up sessions
(until burnout, reviews) it shows a plain stopwatch with no ring. One Pause /
Resume button, nothing else. Frameless: drag anywhere to move, drag the
bottom-right grip to resize.
"""
from PySide6.QtCore import QPointF, QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QFont, QPainter, QPen
from PySide6.QtWidgets import QPushButton, QSizeGrip, QWidget

import session_manager
import tasks_store

REFRESH_MS = 500
RING_BLUE = QColor("#2D8CFF")
RING_TRACK = QColor(128, 128, 128, 45)
BACKGROUND = QColor(24, 26, 30, 235)
TEXT = QColor("#F2F4F7")
MUTED = QColor("#8A8F98")


def format_clock(seconds):
    seconds = max(0, int(seconds))
    hours, rest = divmod(seconds, 3600)
    minutes, secs = divmod(rest, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes:02d}:{secs:02d}"


def describe(status):
    """What the window should show for a /status-shaped dict:
    (label, clock_text, ring_fraction_or_None, can_pause)."""
    if not status["isActive"]:
        return "No session", "--:--", None, False
    if status.get("reviewProblemName"):
        label = f"Review: {status['reviewProblemName']}"
    else:
        label = status.get("eventTitle") or "Focus session"
    pomo = status.get("pomodoro")
    if pomo is not None:
        phase = "Break" if status.get("isBreak") else "Focus"
        label = f"{phase} {pomo['currentCycle']}/{pomo['totalCycles']} - {label}"
    if status["isPaused"]:
        label += " (paused)"

    counts_up = status.get("isBurnout") or status.get("reviewProblemName")
    if counts_up:
        elapsed = tasks_store.worked_seconds(
            status.get("startTime"), None, status.get("violationLog")
        ) if status.get("startTime") else 0
        return label, format_clock(elapsed), None, True

    remaining = status["secondsRemaining"]
    if pomo is not None:
        total = (pomo["breakMinutes"] if status.get("isBreak") else pomo["focusMinutes"]) * 60
    else:
        worked = tasks_store.worked_seconds(
            status.get("startTime"), None, status.get("violationLog")
        ) if status.get("startTime") else 0
        total = worked + remaining
    done = 1 - remaining / total if total > 0 else 0
    return label, format_clock(remaining), min(1.0, max(0.0, done)), True


class MiniTimer(QWidget):
    def __init__(self):
        super().__init__(None, Qt.Tool | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setMinimumSize(140, 160)
        self.resize(200, 230)
        self.setWindowTitle("Carmen Focus timer")
        self._drag_offset = None
        self._label = ""
        self._clock = "--:--"
        self._fraction = None

        self._button = QPushButton("Pause", self)
        self._button.setCursor(Qt.PointingHandCursor)
        self._button.setStyleSheet(
            "QPushButton { background: transparent; color: #F2F4F7; border: 1px solid #5A5F69;"
            " border-radius: 10px; padding: 3px 14px; font-size: 12px; }"
            "QPushButton:hover { border-color: #2D8CFF; }"
            "QPushButton:disabled { color: #5A5F69; }"
        )
        self._button.clicked.connect(self._toggle_pause)
        self._grip = QSizeGrip(self)

        self._timer = QTimer(self)
        self._timer.timeout.connect(self.refresh)
        self._timer.start(REFRESH_MS)
        self.refresh()

    def _toggle_pause(self):
        if session_manager.get_status()["isPaused"]:
            session_manager.resume_session()
        else:
            session_manager.pause_session()
        self.refresh()

    def refresh(self):
        status = session_manager.get_status()
        self._label, self._clock, self._fraction, can_pause = describe(status)
        self._button.setEnabled(can_pause)
        self._button.setText("Resume" if status["isPaused"] else "Pause")
        self.update()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._button.adjustSize()
        self._button.move((self.width() - self._button.width()) // 2,
                          self.height() - self._button.height() - 12)
        self._grip.move(self.width() - self._grip.width(), self.height() - self._grip.height())

    def paintEvent(self, _event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setPen(Qt.NoPen)
        painter.setBrush(BACKGROUND)
        painter.drawRoundedRect(self.rect(), 14, 14)

        top = 10
        label_height = 22
        bottom = self._button.y() - 6
        side = min(self.width() - 24, bottom - top - label_height)
        box = QRectF((self.width() - side) / 2, top + label_height, side, side)

        painter.setPen(MUTED)
        font = QFont(self.font())
        font.setPixelSize(12)
        painter.setFont(font)
        painter.drawText(QRectF(10, top, self.width() - 20, label_height), Qt.AlignCenter,
                         painter.fontMetrics().elidedText(self._label, Qt.ElideRight, self.width() - 20))

        if self._fraction is not None:
            width = max(4.0, side * 0.06)
            ring = box.adjusted(width / 2, width / 2, -width / 2, -width / 2)
            painter.setBrush(Qt.NoBrush)
            painter.setPen(QPen(RING_TRACK, width))
            painter.drawEllipse(ring)
            painter.setPen(QPen(RING_BLUE, width, Qt.SolidLine, Qt.RoundCap))
            painter.drawArc(ring, 90 * 16, -int(self._fraction * 360 * 16))

        font.setPixelSize(max(14, int(side * (0.24 if self._fraction is not None else 0.3))))
        font.setWeight(QFont.Light)
        painter.setFont(font)
        painter.setPen(TEXT)
        painter.drawText(box, Qt.AlignCenter, self._clock)

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self._drag_offset = event.globalPosition() - QPointF(self.frameGeometry().topLeft())

    def mouseMoveEvent(self, event):
        if self._drag_offset is not None and event.buttons() & Qt.LeftButton:
            self.move((event.globalPosition() - self._drag_offset).toPoint())

    def mouseReleaseEvent(self, _event):
        self._drag_offset = None


_win = None


def open_mini_timer():
    """Shows the one mini timer window (a second click just raises it)."""
    global _win
    if _win is None:
        _win = MiniTimer()
    _win.refresh()
    _win.show()
    _win.raise_()
