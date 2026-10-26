"""Composes MonthView (top) + DayView (bottom) into the Calendar tab's full
page, replacing the Tk version's tk.PanedWindow split. Selecting a date in
the month grid drives which day the schedule view below shows -- the same
relationship calendar_gui.py's _state["selected_date"] maintained via its
shared refresh_callbacks list, just wired directly here since both views
live in one page instead of one shared module-level dict.
"""
from datetime import date

from PySide6.QtWidgets import QSplitter, QVBoxLayout, QWidget
from PySide6.QtCore import Qt

from qt_ui.day_view import DayView
from qt_ui.month_view import MonthView
from qt_ui.next_up_widget import NextUpLabel


class CalendarPage(QWidget):
    def __init__(self):
        super().__init__()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        next_up = NextUpLabel()
        next_up.setContentsMargins(24, 4, 24, 4)
        layout.addWidget(next_up)

        splitter = QSplitter(Qt.Vertical)
        splitter.setChildrenCollapsible(False)
        splitter.setHandleWidth(6)

        self._day_view = DayView(on_event_clicked=self._on_event_clicked)
        self._month_view = MonthView(on_date_selected=self._day_view.show_date)

        splitter.addWidget(self._month_view)
        splitter.addWidget(self._day_view)
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 2)
        layout.addWidget(splitter)

        self._day_view.show_date(date.today())

    def _on_event_clicked(self, event):
        import qt_ui.event_editor as event_editor
        event_editor.open_event_editor(event_id=event["id"])

    def refresh(self):
        self._month_view.refresh()
        self._day_view.refresh()
