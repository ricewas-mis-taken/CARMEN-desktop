"""Small always-on-top timer window, opened from the tray menu.

Counts down inside a ring for timed sessions; for count-up sessions (until
burnout, reviews) it shows a plain stopwatch with no ring. A long Pause /
Resume button with a small End beside it, like the task cards, and arrows at
the bottom corners to switch between the timers there are (the running
session, sessions waiting behind it, reviews). It wears the chosen colour
theme. Frameless: drag anywhere to move, drag any edge or corner to resize,
the small x (top right) closes it. Position and size are remembered.
"""
import sys
import traceback

from PySide6.QtCore import QPointF, QRect, QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QGuiApplication, QPainter, QPen
from PySide6.QtWidgets import QPushButton, QWidget

import config
from calendar_log import logger
import review_store
import session_manager
import tasks_store
import theme
from qt_ui import pause_reason

REFRESH_MS = 500
RING_TRACK = QColor(255, 255, 255, 38)
TEXT = QColor("#F7F8FA")
RESIZE_MARGIN = 6
MAX_SIZE = (480, 540)     # a runaway drag can never make it huge
DEFAULT_SIZE = (250, 270)
END_SIZE = QSize(34, 18)
MIN_SIZE = (210, 200)
SAVE_DELAY_MS = 400
CLICK_SLOP = 5     # pixels of movement that still count as a click, not a drag


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
    if session_manager.is_first_attempt(status):
        label = "First attempt"
    elif status.get("reviewProblemName"):
        label = f"Review: {status['reviewProblemName']}"
    else:
        label = status.get("eventTitle") or "Focus session"
    pomo = status.get("pomodoro")
    if pomo is not None:
        phase = "Break" if status.get("isBreak") else "Focus"
        label = f"{phase} {pomo['currentCycle']}/{pomo['totalCycles']} - {label}"

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


def timers():
    """Every timer the mini window can show, running one first: [{"key", "kind", "view"}]. kind is "session"
    (the running one), "parked" (a session waiting behind it) or "review" (a review timed on its own; "active"
    in its view when it is the one running or paused beside the session)."""
    status = session_manager.get_status()
    items = []
    if status["isActive"]:
        items.append({"key": ("session",), "kind": "session", "view": status})
    for parked in status.get("parkedSessions") or []:
        items.append({"key": ("parked", parked["parkId"]), "kind": "parked",
                      "view": {**parked, "isActive": True, "isPaused": True}})
    try:
        active = review_store.get_active_review()
        if active:
            items.append({"key": ("review", active["token"]), "kind": "review", "view": {**active, "active": True}})
        for parked in review_store.list_parked_reviews():
            items.append({"key": ("review", parked["token"]), "kind": "review",
                          "view": {**parked, "active": False, "isPaused": True}})
    except Exception:
        logger.exception("mini timer: could not list the reviews")
    return items


def describe_timer(item):
    """(label, clock, ring fraction or None, action text, can act, can end, paused) for one timer."""
    kind, view = item["kind"], item["view"]
    if kind == "review":
        label = f"Review: {view['problemName']}"
        paused = bool(view.get("isPaused"))
        action = "Resume" if paused else "Pause"
        return label, format_clock(view["elapsedSeconds"]), None, action, True, bool(view.get("active")), paused
    label, clock, fraction, can_pause = describe(view)
    if kind == "parked":
        return label, clock, fraction, "Switch to this", True, True, True
    paused = bool(view.get("isPaused"))
    return label, clock, fraction, "Resume" if paused else "Pause", can_pause, can_pause, paused


class _EndButton(QPushButton):
    """End takes a double click, so one stray tap can't end a timer; one click only says so."""
    doubled = Signal()

    def mouseDoubleClickEvent(self, event):
        self.doubled.emit()


class MiniTimer(QWidget):
    def __init__(self):
        super().__init__(None, Qt.Tool | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setMouseTracking(True)
        self.setMinimumSize(*MIN_SIZE)
        self.setMaximumSize(*MAX_SIZE)
        self._resize_edges = Qt.Edge(0)
        self._resize_start = None
        self.setWindowTitle("Carmen Focus timer")
        self._drag_offset = None
        self._closed_by_user = False
        self._press_point = QPointF()
        self._press_on_clock = False
        self._dragged = False
        self._label = ""
        self._clock = "--:--"
        self._fraction = None

        self._key = ("session",)      # which timer is showing (see timers())
        self._items = []
        self._index = 0
        self._colours = None
        self._can_end = False
        self._paused = False

        self._button = QPushButton("Pause", self)
        self._button.setCursor(Qt.PointingHandCursor)
        self._button.clicked.connect(self._toggle_pause)

        self._end = _EndButton("End", self)
        self._end.setCursor(Qt.PointingHandCursor)
        self._end.setFixedSize(END_SIZE)
        self._end.clicked.connect(self._hint_end)
        self._end.doubled.connect(self._end_timer)
        self._hint = False
        self._hint_timer = QTimer(self)
        self._hint_timer.setSingleShot(True)
        self._hint_timer.setInterval(2200)
        self._hint_timer.timeout.connect(self._clear_hint)

        self._prev = QPushButton("\u2039", self)
        self._next = QPushButton("\u203a", self)
        for arrow, step in ((self._prev, -1), (self._next, 1)):
            arrow.setFixedSize(24, 30)
            arrow.setCursor(Qt.PointingHandCursor)
            arrow.setToolTip("Previous timer" if step < 0 else "Next timer")
            arrow.clicked.connect(lambda _=False, d=step: self._step(d))

        self._close = QPushButton("\u2715", self)
        self._close.setFixedSize(18, 18)
        self._close.setCursor(Qt.PointingHandCursor)
        self._close.clicked.connect(self._close_by_user)

        self._save_timer = QTimer(self)
        self._save_timer.setSingleShot(True)
        self._save_timer.setInterval(SAVE_DELAY_MS)
        self._save_timer.timeout.connect(self._save_geometry)

        self._restore_geometry()

        self._timer = QTimer(self)
        self._timer.timeout.connect(self.refresh)
        self._timer.timeout.connect(self._keep_in_view)
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

    def _close_by_user(self):
        self._closed_by_user = True
        self.close()

    def _on_a_screen(self):
        screens = [screen.availableGeometry() for screen in QGuiApplication.screens()]
        return any(screen.intersects(self.frameGeometry()) for screen in screens)

    def hideEvent(self, event):
        if not self._closed_by_user and not event.spontaneous():
            logger.warning("mini timer hidden without the x: %s", "".join(traceback.format_stack(limit=8)))
        super().hideEvent(event)

    def _keep_in_view(self):
        """A notification (or another topmost window) can push this window out of view:
        Windows drops it from the top or hides/minimises it. Unless the x was clicked,
        bring it back and re-assert always-on-top, without taking focus."""
        if self._closed_by_user:
            return
        if self.isMinimized():
            self.showNormal()
        elif not self.isVisible():
            self.show()
        if not self._on_a_screen():
            # A monitor was unplugged, slept or reconfigured and left it off every screen.
            home = QGuiApplication.primaryScreen().availableGeometry()
            self.move(home.right() - self.width() - 24, home.top() + 24)
        if sys.platform == "win32":
            try:
                import ctypes
                user32 = ctypes.windll.user32
                hwnd = int(self.winId())
                if not user32.IsWindowVisible(hwnd) or user32.IsIconic(hwnd):
                    # Windows hid it behind Qt's back (a toast or another app); Qt still thinks it is shown.
                    logger.warning("mini timer was hidden by the system (visible=%s, minimised=%s); showing it again",
                                   bool(user32.IsWindowVisible(hwnd)), bool(user32.IsIconic(hwnd)))
                    user32.ShowWindow(hwnd, 4)                                # SW_SHOWNOACTIVATE
                HWND_TOPMOST, FLAGS = -1, 0x0001 | 0x0002 | 0x0010 | 0x0040   # nosize nomove noactivate showwindow
                user32.SetWindowPos(hwnd, HWND_TOPMOST, 0, 0, 0, 0, FLAGS)
            except Exception:
                pass

    def closeEvent(self, event):
        if not self._closed_by_user:
            logger.warning("mini timer closed without the x; it will reappear")
        self._save_timer.stop()
        self._save_geometry()
        super().closeEvent(event)

    # --- behaviour ---

    def _current(self):
        for item in self._items:
            if item["key"] == self._key:
                return item
        return self._items[0] if self._items else None

    def _step(self, direction):
        if len(self._items) < 2:
            return
        here = next((i for i, item in enumerate(self._items) if item["key"] == self._key), 0)
        self._key = self._items[(here + direction) % len(self._items)]["key"]
        self.refresh()

    def _toggle_pause(self):
        item = self._current()
        if item is None:
            return
        kind, view = item["kind"], item["view"]
        if kind == "session":
            if view["isPaused"]:
                session_manager.resume_session()
                self.refresh()
            else:
                pause_reason.ask(self, self.refresh)
        elif kind == "parked":
            session_manager.swap_with_parked(view["parkId"])        # it becomes the running one
            self._key = ("session",)
            self.refresh()
        else:
            if view.get("active"):
                (review_store.resume_active_review if view.get("isPaused") else review_store.pause_active_review)()
            else:
                if review_store.get_active_review():
                    review_store.park_active_review()               # one review at a time: the other waits
                review_store.unpark_review(view["token"])
            self.refresh()

    def _hint_end(self):
        self._hint = True
        self._hint_timer.start()
        self.update()

    def _clear_hint(self):
        self._hint = False
        self.update()

    def _end_timer(self):
        self._clear_hint()
        item = self._current()
        if item is None:
            return
        kind, view = item["kind"], item["view"]
        if kind == "session":
            session_manager.end_session(end_type="manual")
        elif kind == "parked":
            session_manager.end_parked_session(park_id=view["parkId"], end_type="manual")
        elif view.get("active"):
            from qt_ui import review_tab
            review_tab.finish_review_from_card(view["token"], view["problemName"], view["elapsedSeconds"])
        self._key = ("session",)
        self.refresh()

    def refresh(self):
        self._items = timers()
        item = self._current()
        if item is None:
            self._label, self._clock, self._fraction = "No session", "--:--", None
            self._can_end, self._paused, action, can_act = False, False, "Pause", False
        else:
            self._key = item["key"]
            self._index = self._items.index(item)
            (self._label, self._clock, self._fraction, action, can_act,
             self._can_end, self._paused) = describe_timer(item)
        self._button.setText(action)
        self._button.setEnabled(can_act)
        self._end.setEnabled(self._can_end)
        self._end.setToolTip("" if self._can_end or item is None else "Resume this review to end it")
        many = len(self._items) > 1
        self._prev.setVisible(many)
        self._next.setVisible(many)
        self._apply_theme(action in ("Resume", "Switch to this"))
        self._place_buttons()
        self.update()

    def _apply_theme(self, primary):
        """Colours come from the chosen theme (accent, sidebar, faint), and the buttons look like the cards':
        a long Pause with a small End beside it, the Resume one filled."""
        colours = theme.current_colours()
        key = (tuple(colours.items()), primary)
        if key == self._colours:
            return
        self._colours = key
        accent = colours["accent"]
        long_style = (
            "QPushButton{background:%s;color:#FFFFFF;border:none;border-radius:13px;padding:0 14px;"
            "font-size:14px;font-weight:600;}QPushButton:hover{background:%s;}"
            "QPushButton:disabled{background:rgba(255,255,255,0.08);color:rgba(255,255,255,0.35);}")
        if primary:
            self._button.setStyleSheet(long_style % (accent, theme.darken(accent, 0.12)))
        else:
            self._button.setStyleSheet(long_style % ("rgba(255,255,255,0.16)", "rgba(255,255,255,0.26)"))
        self._end.setStyleSheet(
            "QPushButton{background:rgba(255,92,100,0.20);color:#FFB3B8;border:none;border-radius:9px;"
            "padding:0;font-size:11px;font-weight:600;}QPushButton:hover{background:rgba(255,92,100,0.34);}"
            "QPushButton:disabled{background:rgba(255,255,255,0.06);color:rgba(255,255,255,0.3);}")
        arrow = ("QPushButton{background:transparent;color:rgba(255,255,255,0.55);border:none;font-size:24px;"
                 "padding-bottom:3px;}QPushButton:hover{color:#FFFFFF;}")
        self._prev.setStyleSheet(arrow)
        self._next.setStyleSheet(arrow)
        self._close.setStyleSheet(
            "QPushButton{background:transparent;color:rgba(255,255,255,0.5);border:none;font-size:11px;}"
            "QPushButton:hover{color:#FFFFFF;}")

    def _place_buttons(self):
        height = 32
        y = self.height() - height - 12
        many = len(self._items) > 1
        left = 10 + (24 + 4 if many else 4)
        right = self.width() - 10 - (24 + 4 if many else 4)
        middle = self.width() / 2
        half = min(52, (right - left) / 2)                      # a smaller Pause, in the middle
        self._button.setGeometry(int(middle - half), y, int(half * 2), height)
        self._end.move(8, 6)
        self._prev.move(10, y + (height - self._prev.height()) // 2)
        self._next.move(self.width() - 10 - self._next.width(), y + (height - self._next.height()) // 2)
        self._close.move(self.width() - self._close.width() - 8, 6)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._place_buttons()
        self._save_timer.start()

    def paintEvent(self, _event):
        colours = theme.current_colours()
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(colours["sidebar"]))
        painter.drawRoundedRect(self.rect(), 16, 16)

        top = 8
        label_height = 26
        box = self._clock_rect()
        side = box.width()

        painter.setPen(QColor(colours["faint"]))
        font = QFont(self.font())
        font.setPixelSize(14)
        painter.setFont(font)
        label_width = self.width() - 2 * (END_SIZE.width() + 14)
        shown = "Double-click to end" if self._hint else self._label
        painter.drawText(QRectF((self.width() - label_width) / 2, top, label_width, label_height), Qt.AlignCenter,
                         painter.fontMetrics().elidedText(shown, Qt.ElideRight, int(label_width)))

        if self._fraction is not None:
            width = max(4.0, side * 0.06)
            ring = box.adjusted(width / 2, width / 2, -width / 2, -width / 2)
            painter.setBrush(Qt.NoBrush)
            painter.setPen(QPen(RING_TRACK, width))
            painter.drawEllipse(ring)
            painter.setPen(QPen(QColor(colours["accent"]), width, Qt.SolidLine, Qt.RoundCap))
            painter.drawArc(ring, 90 * 16, -int(self._fraction * 360 * 16))

        font.setPixelSize(max(16, int(side * (0.28 if self._fraction is not None else 0.34))))
        font.setWeight(QFont.Light)
        painter.setFont(font)
        painter.setPen(TEXT)
        painter.drawText(box, Qt.AlignCenter, self._clock)

        if len(self._items) > 1:                        # which timer of how many: one dot each
            dot, gap = 5.0, 7.0
            total = len(self._items) * dot + (len(self._items) - 1) * gap
            x = (self.width() - total) / 2
            y = self._button.y() - 11
            painter.setPen(Qt.NoPen)
            for i in range(len(self._items)):
                painter.setBrush(TEXT if i == self._index else QColor(255, 255, 255, 70))
                painter.drawEllipse(QPointF(x + dot / 2, y), dot / 2, dot / 2)
                x += dot + gap

    def _clock_rect(self):
        """The square holding the ring and the time (also what opens QuickNote)."""
        top = 8
        label_height = 26
        bottom = self._button.y() - 6 - (14 if len(self._items) > 1 else 0)
        side = max(1.0, min(self.width() - 28, bottom - top - label_height))
        return QRectF((self.width() - side) / 2, top + label_height, side, side)

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
        if edges:
            # Resized by hand, not startSystemResize: that hands the mouse to a native loop
            # that can run away (or hang the app) on a frameless translucent window.
            self._resize_edges = edges
            self._resize_start = (event.globalPosition(), QRect(self.geometry()))
            return
        self._drag_offset = event.globalPosition() - QPointF(self.frameGeometry().topLeft())
        self._press_point = event.globalPosition()
        self._press_on_clock = self._clock_rect().contains(event.position())
        self._dragged = False

    def _resize_to(self, global_pos):
        start_pos, start = self._resize_start
        delta = (global_pos - start_pos).toPoint()
        left, top, right, bottom = start.left(), start.top(), start.right(), start.bottom()
        min_w, min_h = self.minimumWidth(), self.minimumHeight()
        max_w, max_h = self.maximumWidth(), self.maximumHeight()
        if self._resize_edges & Qt.RightEdge:
            right = start.right() + delta.x()
        if self._resize_edges & Qt.LeftEdge:
            left = start.left() + delta.x()
        if self._resize_edges & Qt.BottomEdge:
            bottom = start.bottom() + delta.y()
        if self._resize_edges & Qt.TopEdge:
            top = start.top() + delta.y()
        width = max(min_w, min(max_w, right - left + 1))
        height = max(min_h, min(max_h, bottom - top + 1))
        if self._resize_edges & Qt.LeftEdge:
            left = start.right() + 1 - width
        if self._resize_edges & Qt.TopEdge:
            top = start.bottom() + 1 - height
        self.setGeometry(left, top, width, height)

    def mouseMoveEvent(self, event):
        if self._resize_start is not None and event.buttons() & Qt.LeftButton:
            self._resize_to(event.globalPosition())
            return
        if self._drag_offset is not None and event.buttons() & Qt.LeftButton:
            if (event.globalPosition() - self._press_point).manhattanLength() > CLICK_SLOP:
                self._dragged = True
            self.move((event.globalPosition() - self._drag_offset).toPoint())
        elif not event.buttons():
            edges = self._edges_at(event.position())
            if edges:
                self.setCursor(self._cursor_for(edges))
            elif self._clock_rect().contains(event.position()):
                self.setCursor(Qt.PointingHandCursor)      # click the time or the ring: QuickNote
            else:
                self.setCursor(Qt.ArrowCursor)

    def mouseReleaseEvent(self, event):
        if self._resize_start is not None:
            self._resize_start = None
            self._resize_edges = Qt.Edge(0)
            self._save_timer.start()
            return
        clicked_clock = (
            self._drag_offset is not None and self._press_on_clock and not self._dragged
            and event.button() == Qt.LeftButton
        )
        self._drag_offset = None
        if clicked_clock:
            import qt_ui.quicknote as quicknote
            quicknote.open_quicknote()


_win = None


def open_mini_timer():
    """Shows the one mini timer window (a second click just raises it)."""
    global _win
    if _win is None:
        _win = MiniTimer()
    _win._closed_by_user = False
    _win.refresh()
    _win.show()
    _win.raise_()
