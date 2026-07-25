"""Qt port of calendar_gui.py's Finished tab (_build_finished_tab): a
read-only log of completed focus sessions, with the same month-grid/
day-schedule layout language as the Calendar page, sourced from
session_history.py (logged, completed focus sessions) instead of
calendar_store.py. Session start/status/pause controls live in their own
Focus tab (qt_ui/focus_tab.py) rather than here -- entries in this tab are
appended by session_manager.end_session(), never authored by hand, so
unlike the Calendar page there's no "+ New Event" affordance and clicking a
session opens a read-only detail popup instead of an editor.
"""
import calendar as calendar_module
from datetime import date, datetime, timedelta

from PySide6.QtCore import QRectF, QSize, Qt, QTimer
from PySide6.QtGui import QColor, QFont, QFontMetrics, QPainterPath, QPen
from PySide6.QtWidgets import (
    QFrame,
    QGraphicsDropShadowEffect,
    QGraphicsItem,
    QGraphicsScene,
    QGraphicsView,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSplitter,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

import history_gui
import session_history
import tasks_store
from qt_ui.day_layout import contrasting_text_color, layout_day_blocks
from qt_ui.history_viewer import format_session_html

WEEKDAY_NAMES = ["SUN", "MON", "TUE", "WED", "THU", "FRI", "SAT"]
MAX_PILLS_PER_CELL = 3
HOUR_HEIGHT = 52
LABEL_WIDTH = 60
EVENT_LEFT_MARGIN = 10
EVENT_RIGHT_EDGE = 620
MIN_BLOCK_HEIGHT = 16
MIN_BLOCK_DURATION = timedelta(minutes=(MIN_BLOCK_HEIGHT / HOUR_HEIGHT) * 60)
# Sessions shorter than this are the sub-minute test/glitch kind that pile
# up in tight clusters -- too brief to be worth a label even once the block
# is clamped up to a visible size. Real sessions (even short ones) are
# unaffected: _SessionBlockItem now vertically centers text in whatever
# height it gets, so a MIN_BLOCK_HEIGHT block still shows a clean line.
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
    view is meant to be scrolled, never shown in full. Same fix as
    qt_ui/day_view.py's _ScheduleGraphicsView, for this tab's independent
    QGraphicsView/QGraphicsScene pair."""

    def sizeHint(self):
        return QSize(SCENE_WIDTH, VIEW_HEIGHT_HINT)

SESSION_END_COLORS = {
    "manual": "#5B8DEF",
    "nuclear": "#e53935",
    "timeout": "#fb8c00",
}


def _session_color(session, task_colors=None):
    """Sessions started from a task (source == "task") are colored to match
    that task's own color, same as its card in the Tasks tab, so a
    session's origin is visible at a glance in the Finished log. Anything
    else (manual/calendar-event sessions) keeps the old end-type coloring.
    `task_colors` is an optional {task_id: color} map so callers rendering
    many sessions at once (month grid, day view) can look tasks up once
    instead of hitting tasks_store per session."""
    if session.get("source") == "task" and session.get("eventId"):
        if task_colors is not None:
            color = task_colors.get(session["eventId"])
        else:
            task = tasks_store.get_task(session["eventId"])
            color = task.get("color") if task else None
        if color:
            return color
    return SESSION_END_COLORS.get(session.get("endType", "manual"), "#5B8DEF")


def _load_task_colors():
    return {task["id"]: task.get("color", "#5B8DEF") for task in tasks_store.load_tasks()}


def _session_title(session):
    return session.get("eventTitle") or "Focus session"


def _parse_session_dt(iso_string):
    if not iso_string:
        return None
    try:
        return datetime.fromisoformat(iso_string)
    except ValueError:
        return None


def _format_duration_short(total_seconds):
    minutes, seconds = divmod(max(0, total_seconds), 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours}h {minutes}m"
    if minutes:
        return f"{minutes}m {seconds}s"
    return f"{seconds}s"


class FinishedTab(QWidget):
    def __init__(self):
        super().__init__()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        title = QLabel("Finished")
        title.setStyleSheet("font-size: 20px; font-weight: 700;")
        title.setContentsMargins(24, 22, 24, 8)
        layout.addWidget(title)

        self._last_session_label = QLabel()
        self._last_session_label.setStyleSheet("color: #8A8F98; font-size: 12px;")
        self._last_session_label.setContentsMargins(24, 4, 24, 4)
        layout.addWidget(self._last_session_label)
        self._refresh_last_session()

        splitter = QSplitter(Qt.Vertical)
        splitter.setChildrenCollapsible(False)
        splitter.setHandleWidth(6)

        self._day_view = _FinishedDayView(on_session_clicked=self._open_detail)
        self._month_view = _FinishedMonthView(on_date_selected=self._day_view.show_date)

        splitter.addWidget(self._month_view)
        splitter.addWidget(self._day_view)
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 2)
        layout.addWidget(splitter)

        self._day_view.show_date(date.today())

    def refresh(self):
        self._refresh_last_session()
        self._month_view.refresh()
        self._day_view.refresh()

    def _refresh_last_session(self):
        sessions = session_history.load_all()
        if not sessions:
            self._last_session_label.setText("No finished sessions yet.")
            return
        last = sessions[-1]
        start = _parse_session_dt(last.get("startTime"))
        end = _parse_session_dt(last.get("endTime"))
        when = start.strftime("%a %I:%M %p").replace(" 0", " ") if start else "?"
        duration = _format_duration_short(int((end - start).total_seconds())) if start and end else "?"
        self._last_session_label.setText(f"Last session: {_session_title(last)} — {when} ({duration})")

    def _open_detail(self, session):
        _SessionDetailPopup(session).show()


class _FinishedMonthView(QWidget):
    def __init__(self, on_date_selected=None):
        super().__init__()
        self._cursor = date.today().replace(day=1)
        self._selected_date = date.today()
        self._on_date_selected = on_date_selected

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 10, 24, 8)
        layout.setSpacing(8)
        layout.addLayout(self._build_header())

        self._grid_container = QWidget()
        self._grid_layout = QGridLayout(self._grid_container)
        self._grid_layout.setSpacing(6)
        layout.addWidget(self._grid_container, 1)

        self._render()

    def refresh(self):
        self._render()

    def _build_header(self):
        header = QHBoxLayout()
        header.setSpacing(10)

        self._month_label = QLabel()
        self._month_label.setObjectName("MonthLabel")
        header.addWidget(self._month_label)

        prev_button = QPushButton("‹")
        prev_button.setProperty("class", "SecondaryButton")
        prev_button.setFixedWidth(34)
        prev_button.clicked.connect(self._prev_month)
        header.addWidget(prev_button)

        next_button = QPushButton("›")
        next_button.setProperty("class", "SecondaryButton")
        next_button.setFixedWidth(34)
        next_button.clicked.connect(self._next_month)
        header.addWidget(next_button)

        today_button = QPushButton("Today")
        today_button.setProperty("class", "SecondaryButton")
        today_button.clicked.connect(self._jump_today)
        header.addWidget(today_button)

        header.addStretch(1)

        self._search_edit = QLineEdit()
        self._search_edit.setObjectName("SearchBox")
        self._search_edit.setPlaceholderText("🔍  Search sessions")
        self._search_edit.setFixedWidth(200)
        self._search_edit.textChanged.connect(self._render)
        header.addWidget(self._search_edit)

        view_log_button = QPushButton("View Full Log…")
        view_log_button.setProperty("class", "SecondaryButton")
        view_log_button.clicked.connect(history_gui.open_history_viewer)
        header.addWidget(view_log_button)

        return header

    def _prev_month(self):
        c = self._cursor
        self._cursor = (c.replace(day=1) - timedelta(days=1)).replace(day=1)
        self._render()

    def _next_month(self):
        c = self._cursor
        days_in_month = calendar_module.monthrange(c.year, c.month)[1]
        self._cursor = (c + timedelta(days=days_in_month)).replace(day=1)
        self._render()

    def _jump_today(self):
        self._cursor = date.today().replace(day=1)
        self._selected_date = date.today()
        self._render()
        if self._on_date_selected is not None:
            self._on_date_selected(self._selected_date)

    def _matching_sessions(self):
        query = self._search_edit.text().strip().lower()
        sessions = session_history.load_all()
        if query:
            sessions = [
                s for s in sessions
                if query in _session_title(s).lower() or query in (s.get("reason") or "").lower()
            ]
        return sessions

    def _render(self):
        self._month_label.setText(self._cursor.strftime("%B %Y"))

        while self._grid_layout.count():
            item = self._grid_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()

        for col, name in enumerate(WEEKDAY_NAMES):
            label = QLabel(name)
            label.setProperty("class", "WeekdayHeader")
            label.setAlignment(Qt.AlignCenter)
            self._grid_layout.addWidget(label, 0, col)

        cal = calendar_module.Calendar(firstweekday=6)
        month_days = list(cal.itermonthdates(self._cursor.year, self._cursor.month))

        sessions_by_day = {}
        for session in self._matching_sessions():
            start = _parse_session_dt(session.get("startTime"))
            if start:
                sessions_by_day.setdefault(start.date(), []).append(session)

        task_colors = _load_task_colors()

        for row in range(6):
            self._grid_layout.setRowStretch(row + 1, 1)
        for col in range(7):
            self._grid_layout.setColumnStretch(col, 1)

        for idx, day in enumerate(month_days):
            row, col = divmod(idx, 7)
            day_sessions = sorted(sessions_by_day.get(day, []), key=lambda s: s.get("startTime") or "")
            cell = _SessionDayCell(
                day, self._cursor, self._selected_date, day_sessions, task_colors, on_click=self._on_day_clicked,
            )
            self._grid_layout.addWidget(cell, row + 1, col)

    def _on_day_clicked(self, day):
        self._selected_date = day
        self._render()
        if self._on_date_selected is not None:
            self._on_date_selected(day)


class _SessionDayCell(QFrame):
    def __init__(self, day, cursor_month, selected_date, day_sessions, task_colors, on_click):
        super().__init__()
        self.setProperty("class", "DayCell")
        self.setCursor(Qt.PointingHandCursor)

        in_month = day.month == cursor_month.month
        is_today = day == date.today()
        is_selected = day == selected_date
        self.setProperty("inMonth", in_month)
        self.setProperty("isToday", is_today)
        self.setProperty("isSelected", is_selected)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(2)

        day_number = QLabel(str(day.day))
        day_number.setProperty("class", "DayNumber")
        day_number.setProperty("isToday", is_today)
        day_number.setProperty("inMonth", in_month)
        day_number.setAlignment(Qt.AlignRight)
        layout.addWidget(day_number)

        for session in day_sessions[:MAX_PILLS_PER_CELL]:
            pill = QLabel()
            pill.setProperty("class", "EventPill")
            pill.setStyleSheet(f"background: {_session_color(session, task_colors)};")
            metrics = QFontMetrics(pill.font())
            pill.setText(metrics.elidedText(_session_title(session), Qt.ElideRight, 110))
            layout.addWidget(pill)

        overflow = len(day_sessions) - MAX_PILLS_PER_CELL
        if overflow > 0:
            more_label = QLabel(f"+{overflow} more")
            more_label.setProperty("class", "OverflowLabel")
            layout.addWidget(more_label)

        layout.addStretch(1)
        self._day = day
        self._on_click = on_click

        shadow = QGraphicsDropShadowEffect(self)
        shadow.setBlurRadius(10)
        shadow.setOffset(0, 1)
        shadow.setColor(QColor(31, 35, 40, 25))
        self.setGraphicsEffect(shadow)

    def mousePressEvent(self, event):
        self._on_click(self._day)
        super().mousePressEvent(event)


class _FinishedDayView(QWidget):
    def __init__(self, on_session_clicked=None):
        super().__init__()
        self._selected_date = None
        self._on_session_clicked = on_session_clicked
        self._last_scrolled_date = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 4, 24, 20)
        layout.setSpacing(8)

        self._title_label = QLabel()
        self._title_label.setStyleSheet("font-size: 13px; font-weight: 700; color: #1F2328;")
        layout.addWidget(self._title_label)

        self._scene = QGraphicsScene()
        self._view = _ScheduleGraphicsView(self._scene)
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

    def show_date(self, selected_date):
        self._selected_date = selected_date
        self._render()

    def refresh(self):
        self._render()

    def _matching_sessions(self):
        return session_history.load_all()

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
        task_colors = _load_task_colors()

        day_sessions = []
        for session in self._matching_sessions():
            start = _parse_session_dt(session.get("startTime"))
            if start and start.date() == selected:
                end = _parse_session_dt(session.get("endTime")) or start
                day_sessions.append((start, end, session))

        block_x0 = LABEL_WIDTH + EVENT_LEFT_MARGIN
        for occ_start, occ_end, session, col, cols in layout_day_blocks(day_sessions, min_duration=MIN_BLOCK_DURATION):
            start_minutes = max(0, (occ_start - range_start).total_seconds() / 60)
            end_minutes = min(24 * 60, (occ_end - range_start).total_seconds() / 60)
            y0 = (start_minutes / 60) * HOUR_HEIGHT
            y1 = (end_minutes / 60) * HOUR_HEIGHT
            y1 = max(y1, y0 + MIN_BLOCK_HEIGHT)

            col_width = (EVENT_RIGHT_EDGE - block_x0) / cols
            gap = 4 if cols > 1 else 0
            x0 = block_x0 + col * col_width
            x1 = x0 + col_width - gap

            # Sub-minute sessions are almost always test/glitch noise, and a
            # cluster of them crowds the day view with labels nobody reads
            # -- skip the text for those and leave a plain colored block.
            # Anything longer gets a label regardless of how short the
            # rendered block ends up (text is vertically centered so it
            # stays readable even at MIN_BLOCK_HEIGHT).
            if (occ_end - occ_start) < MIN_DURATION_FOR_TEXT:
                label_text = ""
            else:
                duration = _format_duration_short(int((occ_end - occ_start).total_seconds()))
                title = _session_title(session)
                start_time_only = occ_start.strftime("%I:%M%p").lstrip("0")
                label_text = _fit_session_label(f"{title}  ·  {duration}", title, start_time_only, x1 - x0 - 16)

            item = _SessionBlockItem(x0, y0, x1 - x0, y1 - y0, _session_color(session, task_colors), label_text, session)
            if self._on_session_clicked is not None:
                item.clicked.connect(self._on_session_clicked)
            self._scene.addItem(item)

        if self._last_scrolled_date != selected:
            self._last_scrolled_date = selected
            current_hour = datetime.now().hour
            target_y = max(0, current_hour - 1) * HOUR_HEIGHT
            QTimer.singleShot(0, lambda: self._view.verticalScrollBar().setValue(int(target_y)))


def _fit_session_label(*candidates_and_width):
    *candidates, max_width = candidates_and_width
    font = QFont("Segoe UI", 9)
    metrics = QFontMetrics(font)
    if max_width <= 4:
        return ""
    for text in candidates:
        if metrics.horizontalAdvance(text) <= max_width:
            return text
    return metrics.elidedText(candidates[-1], Qt.ElideRight, int(max_width))


class _SessionBlockItem(QGraphicsItem):
    class _Signal:
        def __init__(self):
            self._slots = []

        def connect(self, fn):
            self._slots.append(fn)

        def emit(self, value):
            for fn in self._slots:
                fn(value)

    def __init__(self, x, y, width, height, color_hex, text, session):
        super().__init__()
        self.setPos(x, y)
        self._width = width
        self._height = height
        self._color = QColor(color_hex)
        self._text_color = QColor(contrasting_text_color(color_hex))
        self._text = text
        self._session = session
        self.clicked = _SessionBlockItem._Signal()
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
        # Vertically centered with no top/bottom inset -- at MIN_BLOCK_HEIGHT
        # (16px) this still fits one line of 9pt text without clipping,
        # unlike the old top-aligned rect that cut off short blocks' text.
        painter.drawText(QRectF(8, 0, max(0, self._width - 16), self._height), Qt.AlignLeft | Qt.AlignVCenter, self._text)

    def mousePressEvent(self, event):
        self.clicked.emit(self._session)
        super().mousePressEvent(event)


class _SessionDetailPopup(QWidget):
    def __init__(self, session):
        super().__init__()
        self.setObjectName("PopupBg")
        self.setWindowTitle("Focus Session Details")
        self.resize(560, 480)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        text = QTextEdit()
        text.setReadOnly(True)
        text.setHtml(
            f'<div style="font-family: Consolas, monospace; font-size: 10pt;">{format_session_html(session)}</div>'
        )
        layout.addWidget(text)

        _register_popup(self)


_popup_refs = set()


def _register_popup(popup):
    _popup_refs.add(popup)
    popup.destroyed.connect(lambda: _popup_refs.discard(popup))
    return popup
