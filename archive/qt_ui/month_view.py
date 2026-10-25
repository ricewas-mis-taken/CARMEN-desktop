"""Qt port of calendar_gui.py's month-grid view (_build_calendar_tab's top
pane). Clicking an event pill opens qt_ui/event_editor.py for that event;
"+ New Event" opens a blank editor prefilled with the selected date.

Data source unchanged from the Tk version: calendar_store.list_events() +
calendar_recurrence.expand_occurrences() for the month's date range.
"""
import calendar as calendar_module
from datetime import date, datetime, timedelta

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QFontMetrics
from PySide6.QtWidgets import (
    QFrame,
    QGraphicsDropShadowEffect,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

import calendar_recurrence as recurrence
import calendar_store as store

WEEKDAY_NAMES = ["SUN", "MON", "TUE", "WED", "THU", "FRI", "SAT"]
MAX_PILLS_PER_CELL = 3


class MonthView(QWidget):
    def __init__(self, on_date_selected=None):
        super().__init__()
        self._cursor = date.today().replace(day=1)
        self._selected_date = date.today()
        self._on_date_selected = on_date_selected

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 22, 24, 20)
        layout.setSpacing(8)

        layout.addLayout(self._build_header())

        self._grid_container = QWidget()
        self._grid_layout = QGridLayout(self._grid_container)
        self._grid_layout.setSpacing(6)
        layout.addWidget(self._grid_container, 1)

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
        self._search_edit.setPlaceholderText("🔍  Search events")
        self._search_edit.setFixedWidth(220)
        self._search_edit.textChanged.connect(self._render)
        header.addWidget(self._search_edit)

        new_event_button = QPushButton("+  New Event")
        new_event_button.setProperty("class", "AccentButton")
        new_event_button.clicked.connect(self._new_event)
        header.addWidget(new_event_button)

        return header

    def _new_event(self):
        import qt_ui.event_editor as event_editor
        event_editor.open_event_editor(initial_date=self._selected_date)

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

    def refresh(self):
        self._render()

    def _matching_events(self):
        query = self._search_edit.text().strip().lower()
        events = store.list_events()
        if query:
            events = [e for e in events if query in e["title"].lower()]
        return events

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

        cal = calendar_module.Calendar(firstweekday=6)  # Sunday-start
        month_days = list(cal.itermonthdates(self._cursor.year, self._cursor.month))

        events = self._matching_events()
        range_start = datetime.combine(month_days[0], datetime.min.time())
        range_end = datetime.combine(month_days[-1] + timedelta(days=1), datetime.min.time())
        occurrences_by_day = {}
        for event in events:
            for occ_start, _occ_end in recurrence.expand_occurrences(event, range_start, range_end):
                occurrences_by_day.setdefault(occ_start.date(), []).append(event)

        for row in range(6):
            self._grid_layout.setRowStretch(row + 1, 1)
        for col in range(7):
            self._grid_layout.setColumnStretch(col, 1)

        for idx, day in enumerate(month_days):
            row, col = divmod(idx, 7)
            cell = _DayCell(
                day, self._cursor, self._selected_date, occurrences_by_day.get(day, []),
                on_click=self._on_day_clicked,
            )
            self._grid_layout.addWidget(cell, row + 1, col)

    def _on_day_clicked(self, day):
        self._selected_date = day
        self._render()
        if self._on_date_selected is not None:
            self._on_date_selected(day)


class _DayCell(QFrame):
    def __init__(self, day, cursor_month, selected_date, day_events, on_click):
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

        for event in day_events[:MAX_PILLS_PER_CELL]:
            pill = _EventPillLabel(event)
            pill.setProperty("class", "EventPill")
            pill.setStyleSheet(f"background: {event['color']};")
            metrics = QFontMetrics(pill.font())
            pill.setText(metrics.elidedText(event["title"], Qt.ElideRight, 110))
            layout.addWidget(pill)

        overflow = len(day_events) - MAX_PILLS_PER_CELL
        if overflow > 0:
            more_label = QLabel(f"+{overflow} more")
            more_label.setProperty("class", "OverflowLabel")
            layout.addWidget(more_label)

        layout.addStretch(1)
        self._day = day
        self._on_click = on_click

        # QSS has no box-shadow equivalent -- the "soft card" depth from the
        # visual spec is applied here per-widget instead, kept subtle
        # (low opacity, small blur/offset) so 42 cells' worth doesn't turn
        # into visual noise.
        shadow = QGraphicsDropShadowEffect(self)
        shadow.setBlurRadius(10)
        shadow.setOffset(0, 1)
        shadow.setColor(QColor(31, 35, 40, 25))
        self.setGraphicsEffect(shadow)

    def mousePressEvent(self, event):
        self._on_click(self._day)
        super().mousePressEvent(event)


class _EventPillLabel(QLabel):
    def __init__(self, event):
        super().__init__()
        self._event = event
        self.setCursor(Qt.PointingHandCursor)

    def mousePressEvent(self, event):
        import qt_ui.event_editor as event_editor
        event_editor.open_event_editor(event_id=self._event["id"])
        event.accept()  # don't let the click also bubble up to select the day cell
