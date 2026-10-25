"""Qt port of calendar_gui.py's hourly day-schedule view (the bottom pane
of _build_calendar_tab's PanedWindow). Rendered with QGraphicsView/
QGraphicsScene rather than a plain painted QWidget, since that's what lets
each event block get both real rounded corners (a QPainterPath in
_EventBlockItem.paint) and its own QGraphicsDropShadowEffect -- a
per-widget effect stack, like month_view.py's day cells, applied here per
scene item instead.

Column layout for overlapping/partially-overlapping events comes from
qt_ui/day_layout.py, ported verbatim from the Tk version -- this module
only owns the rendering, not the overlap math.
"""
from datetime import date, datetime, timedelta

from PySide6.QtCore import QRectF, QSize, Qt, QTimer
from PySide6.QtGui import QColor, QFont, QFontMetrics, QPainterPath, QPen
from PySide6.QtWidgets import (
    QGraphicsDropShadowEffect,
    QGraphicsItem,
    QGraphicsScene,
    QGraphicsView,
    QLabel,
    QVBoxLayout,
    QWidget,
)

import calendar_recurrence as recurrence
import calendar_store as store
from qt_ui.day_layout import contrasting_text_color, layout_day_blocks

HOUR_HEIGHT = 52
LABEL_WIDTH = 60
EVENT_LEFT_MARGIN = 10
EVENT_RIGHT_EDGE = 620
MIN_BLOCK_HEIGHT = 16
MIN_BLOCK_DURATION = timedelta(minutes=(MIN_BLOCK_HEIGHT / HOUR_HEIGHT) * 60)
# Events shorter than this are too brief to be worth a label even once the
# block is clamped up to a visible size. Real events are unaffected:
# _EventBlockItem now vertically centers text in whatever height it gets,
# so a MIN_BLOCK_HEIGHT block still shows a clean line.
MIN_DURATION_FOR_TEXT = timedelta(seconds=60)
SCENE_WIDTH = 640
# A handful of hours' worth -- enough to read as "a schedule", not a
# recommendation to show the full 24-hour scene at once (that's what the
# view's own scrolling is for).
VIEW_HEIGHT_HINT = 400


class _ScheduleGraphicsView(QGraphicsView):
    """QGraphicsView.sizeHint() reflects the *whole scene's* size once a
    scene is set (here, the full 24-hour timeline, HOUR_HEIGHT * 24 =
    1248px) rather than a sensible "visible portion" -- even though this
    view is meant to be scrolled, never shown in full. Left unbounded, that
    huge sizeHint bubbles up through DayView -> the calendar page's
    QSplitter and got applied to the whole main window during a native
    title-bar drag, instantly stretching it into a tall rectangle. Capping
    the hint here removes the number at its source."""

    def sizeHint(self):
        return QSize(SCENE_WIDTH, VIEW_HEIGHT_HINT)


class DayView(QWidget):
    def __init__(self, on_event_clicked=None):
        super().__init__()
        self._selected_date = None
        self._on_event_clicked = on_event_clicked
        self._last_scrolled_date = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 4, 24, 20)
        layout.setSpacing(8)

        self._title_label = QLabel()
        self._title_label.setStyleSheet("font-size: 13px; font-weight: 700; color: #1F2328;")
        layout.addWidget(self._title_label)

        self._scene = QGraphicsScene()
        self._view = _ScheduleGraphicsView(self._scene)
        self._view.setRenderHint(self._view.renderHints())
        self._view.setFrameShape(QGraphicsView.NoFrame)
        self._view.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._view.setBackgroundBrush(QColor("#FFFFFF"))
        # sizeHint() alone wasn't enough to stop the window snapping to a
        # tall rectangle on drag -- the scene itself is still set to the
        # full 24-hour height (1248px, see _render() below) for the
        # schedule's coordinate math, and whatever native mechanism causes
        # the snap appears to key off that scene extent rather than the
        # widget's declared sizeHint. A hard maximumHeight on the view
        # itself, closer to the actual widget in question, is the second
        # layer of defense against it.
        self._view.setMaximumHeight(600)
        layout.addWidget(self._view, 1)

        self._now_line = None
        self._now_timer = QTimer(self)
        self._now_timer.setInterval(30_000)
        self._now_timer.timeout.connect(self._update_now_line)
        self._now_timer.start()

    def show_date(self, selected_date):
        self._selected_date = selected_date
        self._render()

    def refresh(self):
        # Re-renders the currently-shown date's events without re-running
        # the auto-scroll-to-current-hour behavior, which only fires on an
        # actual date change (see _last_scrolled_date below) -- a refresh
        # after saving an event shouldn't yank the view back to "now".
        self._render()

    def _render(self):
        if self._selected_date is None:
            return
        selected = self._selected_date
        self._title_label.setText(selected.strftime("%A, %B %d, %Y"))

        self._scene.clear()
        total_height = HOUR_HEIGHT * 24
        self._scene.setSceneRect(0, 0, SCENE_WIDTH, total_height)

        for hour in range(24):
            y = hour * HOUR_HEIGHT
            label_text = datetime(2000, 1, 1, hour).strftime("%I %p").lstrip("0")
            line = self._scene.addLine(LABEL_WIDTH - 8, y, SCENE_WIDTH, y, QPen(QColor("#ECEEF1")))
            line.setZValue(-1)
            text_item = self._scene.addText(label_text, QFont("Segoe UI", 8))
            text_item.setDefaultTextColor(QColor("#8A8F98"))
            metrics = QFontMetrics(text_item.font())
            text_item.setPos(LABEL_WIDTH - 14 - metrics.horizontalAdvance(label_text), y - 8)

        range_start = datetime.combine(selected, datetime.min.time())
        range_end = range_start + timedelta(days=1)
        events = store.list_events()

        day_events = []
        for event in events:
            for occ_start, occ_end in recurrence.expand_occurrences(event, range_start, range_end):
                day_events.append((occ_start, occ_end, event))

        block_x0 = LABEL_WIDTH + EVENT_LEFT_MARGIN
        for occ_start, occ_end, event, col, cols in layout_day_blocks(day_events, min_duration=MIN_BLOCK_DURATION):
            start_minutes = max(0, (occ_start - range_start).total_seconds() / 60)
            end_minutes = min(24 * 60, (occ_end - range_start).total_seconds() / 60)
            y0 = (start_minutes / 60) * HOUR_HEIGHT
            y1 = (end_minutes / 60) * HOUR_HEIGHT
            y1 = max(y1, y0 + MIN_BLOCK_HEIGHT)

            col_width = (EVENT_RIGHT_EDGE - block_x0) / cols
            gap = 4 if cols > 1 else 0
            x0 = block_x0 + col * col_width
            x1 = x0 + col_width - gap

            # Sub-minute events are too brief to be worth a label -- skip
            # the text for those and leave a plain colored block. Anything
            # longer gets a label regardless of rendered block height (text
            # is vertically centered so it stays readable even at
            # MIN_BLOCK_HEIGHT).
            if (occ_end - occ_start) < MIN_DURATION_FOR_TEXT:
                label_text = ""
            else:
                label_text = event["title"]
                if event.get("focusProfile") and event["focusProfile"].get("enabled"):
                    label_text = "🎯 " + label_text

            item = _EventBlockItem(x0, y0, x1 - x0, y1 - y0, event["color"], label_text, event)
            if self._on_event_clicked is not None:
                item.clicked.connect(self._on_event_clicked)
            self._scene.addItem(item)

        if self._last_scrolled_date != selected:
            self._last_scrolled_date = selected
            current_hour = datetime.now().hour
            target_y = max(0, current_hour - 1) * HOUR_HEIGHT
            # Deferred to the next event-loop tick: right after a
            # freshly-constructed DayView's first render, its viewport
            # hasn't been laid out to final size yet, so scrolling via
            # verticalScrollBar().setValue() here (rather than relying on
            # viewport dimensions at all) is what makes this correct
            # regardless of whether layout has settled by this point.
            QTimer.singleShot(0, lambda: self._view.verticalScrollBar().setValue(int(target_y)))

        if selected == date.today():
            now = datetime.now()
            y = ((now.hour * 60 + now.minute) / 60) * HOUR_HEIGHT
            line = self._scene.addLine(0, y, SCENE_WIDTH, y, QPen(QColor("#E5484D"), 2))
            line.setZValue(10)

    def _update_now_line(self):
        # Only re-renders while today's schedule is on screen -- viewing any
        # other date doesn't need the redline, so skip the rebuild entirely.
        if self._selected_date == date.today():
            self._render()


class _EventBlockItem(QGraphicsItem):
    """A rounded-rect event block with a soft drop shadow and truncated,
    fully-contained title text -- the Qt-native replacement for the Tk
    version's rw.draw_rounded_rect + canvas text-with-manual-ellipsis
    (_fit_block_label)."""

    class _Signal:
        # QGraphicsItem isn't a QObject and can't have Qt signals directly;
        # a tiny plain-Python callback list is enough here since the only
        # consumer is DayView wiring a single click handler per item.
        def __init__(self):
            self._slots = []

        def connect(self, fn):
            self._slots.append(fn)

        def emit(self, value):
            for fn in self._slots:
                fn(value)

    def __init__(self, x, y, width, height, color_hex, text, event):
        super().__init__()
        self.setPos(x, y)
        self._width = width
        self._height = height
        self._color = QColor(color_hex)
        self._text_color = QColor(contrasting_text_color(color_hex))
        self._text = text
        self._event = event
        self.clicked = _EventBlockItem._Signal()
        self.setAcceptHoverEvents(True)
        self.setCursor(Qt.PointingHandCursor)

        shadow = QGraphicsDropShadowEffect()
        shadow.setBlurRadius(8)
        shadow.setOffset(0, 1)
        shadow.setColor(QColor(0, 0, 0, 45))
        self.setGraphicsEffect(shadow)

    def boundingRect(self):
        return QRectF(0, 0, self._width, self._height)

    def paint(self, painter, option, widget=None):
        path = QPainterPath()
        path.addRoundedRect(QRectF(0, 0, self._width, self._height), 6, 6)
        painter.setRenderHint(painter.RenderHint.Antialiasing)
        painter.fillPath(path, self._color)

        font = QFont("Segoe UI", 9)
        painter.setFont(font)
        painter.setPen(self._text_color)
        metrics = QFontMetrics(font)
        text_max_width = max(0, self._width - 16)
        elided = metrics.elidedText(self._text, Qt.ElideRight, int(text_max_width))
        # Vertically centered with no top/bottom inset -- at MIN_BLOCK_HEIGHT
        # (16px) this still fits one line of 9pt text without clipping,
        # unlike the old top-aligned rect that cut off short blocks' text.
        painter.drawText(QRectF(8, 0, text_max_width, self._height), Qt.AlignLeft | Qt.AlignVCenter, elided)

    def mousePressEvent(self, event):
        self.clicked.emit(self._event)
        super().mousePressEvent(event)
