"""Small always-on-top timer window, opened from the tray menu.

Counts down inside a blue ring for timed sessions; for count-up sessions
(until burnout, reviews) it shows a plain stopwatch with no ring. One Pause /
Resume button, nothing else. Frameless: drag anywhere to move, drag any edge
or corner to resize, the small x (top right) closes it. Position and size are
remembered between launches.
"""
from PySide6.QtCore import QPointF, QRect, QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QFont, QGuiApplication, QPainter, QPen
from PySide6.QtWidgets import QPushButton, QWidget

import config
import session_manager
import tasks_store

REFRESH_MS = 500
RING_BLUE = QColor("#2D8CFF")
RING_TRACK = QColor(128, 128, 128, 45)
BACKGROUND = QColor(0, 0, 0)
TEXT = QColor("#F2F4F7")
MUTED = QColor("#8A8F98")
RESIZE_MARGIN = 8
DEFAULT_SIZE = (220, 250)
SAVE_DELAY_MS = 400


def format_clock(seconds):
    seconds = max(0, int(seconds))
    hours, rest = divmod(seconds, 3600)
    minutes, secs = divmod(rest, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes:02d}:{secs:02d}"


def visible_geometry(saved, screens):
    """The saved [x, y, w, h] if it still lands on one of `screens` (a list of
    QRect), else None -- e.g. a monitor that has since been unplugged."""
    if not (isinstance(saved, (list, tuple)) and len(saved) == 4
            and all(isinstance(v, int) and not isinstance(v, bool) for v in saved)):
        return None
    x, y, w, h = saved
    if w < 50 or h < 50:
        return None
    rect = QRect(x, y, w, h)
    return list(saved) if any(screen.intersects(rect) for screen in screens) else None


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
        self.setMouseTracking(True)
        self.setMinimumSize(150, 170)
        self.setWindowTitle("Carmen Focus timer")
        self._drag_offset = None
        self._label = ""
        self._clock = "--:--"
        self._fraction = None

        self._button = QPushButton("Pause", self)
        self._button.setCursor(Qt.PointingHandCursor)
        self._button.setStyleSheet(
            "QPushButton { background: transparent; color: #F2F4F7; border: 1px solid #5A5F69;"
            " border-radius: 11px; padding: 4px 16px; font-size: 14px; }"
            "QPushButton:hover { border-color: #2D8CFF; }"
            "QPushButton:disabled { color: #5A5F69; }"
        )
        self._button.clicked.connect(self._toggle_pause)

        self._close = QPushButton("✕", self)
        self._close.setFixedSize(18, 18)
        self._close.setCursor(Qt.PointingHandCursor)
        self._close.setStyleSheet(
            "QPushButton { background: transparent; color: #8A8F98; border: none; font-size: 11px; }"
            "QPushButton:hover { color: #F2F4F7; }"
        )
        self._close.clicked.connect(self.close)

        self._save_timer = QTimer(self)
        self._save_timer.setSingleShot(True)
        self._save_timer.setInterval(SAVE_DELAY_MS)
        self._save_timer.timeout.connect(self._save_geometry)

        self._restore_geometry()

        self._timer = QTimer(self)
        self._timer.timeout.connect(self.refresh)
        self._timer.start(REFRESH_MS)
        self.refresh()

    # --- remembered position and size ---

    def _restore_geometry(self):
        screens = [screen.availableGeometry() for screen in QGuiApplication.screens()]
        saved = visible_geometry(config.load_config().get("miniTimerGeometry"), screens)
        if saved:
            self.setGeometry(*saved)
        else:
            self.resize(*DEFAULT_SIZE)

    def _save_geometry(self):
        geometry = self.geometry()
        value = [geometry.x(), geometry.y(), geometry.width(), geometry.height()]
        config.update_config(lambda cfg: cfg.update({"miniTimerGeometry": value}))

    def moveEvent(self, event):
        super().moveEvent(event)
        self._save_timer.start()

    def closeEvent(self, event):
        self._save_timer.stop()
        self._save_geometry()
        super().closeEvent(event)

    # --- behaviour ---

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
        self._button.adjustSize()
        self._place_buttons()
        self.update()

    def _place_buttons(self):
        self._button.move((self.width() - self._button.width()) // 2,
                          self.height() - self._button.height() - 14)
        self._close.move(self.width() - self._close.width() - 8, 6)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._place_buttons()
        self._save_timer.start()

    def paintEvent(self, _event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setPen(Qt.NoPen)
        painter.setBrush(BACKGROUND)
        painter.drawRoundedRect(self.rect(), 14, 14)

        top = 8
        label_height = 26
        bottom = self._button.y() - 6
        side = min(self.width() - 28, bottom - top - label_height)
        box = QRectF((self.width() - side) / 2, top + label_height, side, side)

        painter.setPen(MUTED)
        font = QFont(self.font())
        font.setPixelSize(14)
        painter.setFont(font)
        label_width = self.width() - 2 * (self._close.width() + 12)
        painter.drawText(QRectF((self.width() - label_width) / 2, top, label_width, label_height), Qt.AlignCenter,
                         painter.fontMetrics().elidedText(self._label, Qt.ElideRight, int(label_width)))

        if self._fraction is not None:
            width = max(4.0, side * 0.06)
            ring = box.adjusted(width / 2, width / 2, -width / 2, -width / 2)
            painter.setBrush(Qt.NoBrush)
            painter.setPen(QPen(RING_TRACK, width))
            painter.drawEllipse(ring)
            painter.setPen(QPen(RING_BLUE, width, Qt.SolidLine, Qt.RoundCap))
            painter.drawArc(ring, 90 * 16, -int(self._fraction * 360 * 16))

        font.setPixelSize(max(16, int(side * (0.28 if self._fraction is not None else 0.34))))
        font.setWeight(QFont.Light)
        painter.setFont(font)
        painter.setPen(TEXT)
        painter.drawText(box, Qt.AlignCenter, self._clock)

    # --- drag to move, drag an edge or corner to resize ---

    def _edges_at(self, pos):
        edges = Qt.Edge(0)
        if pos.x() <= RESIZE_MARGIN:
            edges |= Qt.LeftEdge
        elif pos.x() >= self.width() - RESIZE_MARGIN:
            edges |= Qt.RightEdge
        if pos.y() <= RESIZE_MARGIN:
            edges |= Qt.TopEdge
        elif pos.y() >= self.height() - RESIZE_MARGIN:
            edges |= Qt.BottomEdge
        return edges

    @staticmethod
    def _cursor_for(edges):
        horizontal = bool(edges & (Qt.LeftEdge | Qt.RightEdge))
        vertical = bool(edges & (Qt.TopEdge | Qt.BottomEdge))
        if horizontal and vertical:
            same_diagonal = bool(edges & Qt.LeftEdge) == bool(edges & Qt.TopEdge)
            return Qt.SizeFDiagCursor if same_diagonal else Qt.SizeBDiagCursor
        if horizontal:
            return Qt.SizeHorCursor
        if vertical:
            return Qt.SizeVerCursor
        return Qt.ArrowCursor

    def mousePressEvent(self, event):
        if event.button() != Qt.LeftButton:
            return
        edges = self._edges_at(event.position())
        if edges and self.windowHandle() is not None:
            self.windowHandle().startSystemResize(edges)
            return
        self._drag_offset = event.globalPosition() - QPointF(self.frameGeometry().topLeft())

    def mouseMoveEvent(self, event):
        if self._drag_offset is not None and event.buttons() & Qt.LeftButton:
            self.move((event.globalPosition() - self._drag_offset).toPoint())
        elif not event.buttons():
            self.setCursor(self._cursor_for(self._edges_at(event.position())))

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
