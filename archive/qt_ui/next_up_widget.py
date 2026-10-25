"""Small self-refreshing "next up" label, shown above both the Calendar
page (qt_ui/calendar_page.py) and the Focus tab (qt_ui/focus_tab.py) --
ported from calendar_gui.py's
_register_next_up_widget, which both of the Tk tabs shared for the same
reason. A QTimer replaces the original's refresh_callbacks-list pattern
(each Tk widget registered its own refresh function into
_state["refresh_callbacks"], invoked by _refresh_all_calendar_views() after
any edit); a periodic timer is simpler here since there's no longer a
single shared mutable _state dict driving every view.
"""
from datetime import datetime

from PySide6.QtWidgets import QLabel
from PySide6.QtCore import QTimer

import calendar_recurrence as recurrence
import calendar_store as store

REFRESH_MS = 5000


class NextUpLabel(QLabel):
    def __init__(self):
        super().__init__()
        self.setStyleSheet("color: #8A8F98; font-size: 12px;")
        self.setWordWrap(True)
        self._timer = QTimer(self)
        self._timer.timeout.connect(self.refresh)
        self._timer.start(REFRESH_MS)
        self.refresh()

    def refresh(self):
        events = store.list_events()
        upcoming = recurrence.next_occurrences(events, datetime.now(), count=2)
        if not upcoming:
            self.setText("No upcoming events.")
            return
        lines = []
        for occ_start, _occ_end, event in upcoming:
            when = occ_start.strftime("%a %I:%M %p").replace(" 0", " ")
            lines.append(f"Next up: {event['title']} — {when}")
        self.setText("\n".join(lines))
