"""Screen Time tab: an always-on, session-independent tally of how long
each app and website has had the user's attention today/this week, sourced
from screentime_store.py (populated by window_tracker.py's polling loop for
apps, and by the browser extension's POST /screentime/domain for domains --
see that module's docstring). Grouped by category
(Entertainment/Education/Games/Tools/Other, screentime_categories.py), with
anything under 5 minutes collapsed into one expandable row per category
instead of cluttering the list with noise.
"""
from datetime import date, timedelta

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import (
    QButtonGroup,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

import screentime_categories
import screentime_store

REFRESH_MS = 5000
GROUP_THRESHOLD_SECONDS = 5 * 60

_CATEGORY_COLORS = {
    "Entertainment": "#E27D60",
    "Education": "#4A90D9",
    "Games": "#8E44AD",
    "Tools": "#2E8B57",
    "Other": "#9AA0A8",
}


def _format_duration(seconds):
    seconds = int(round(seconds))
    hours, rem = divmod(seconds, 3600)
    minutes, secs = divmod(rem, 60)
    if hours:
        return f"{hours}h {minutes}m"
    if minutes:
        return f"{minutes}m {secs}s"
    return f"{secs}s"


class _BarChart(QWidget):
    """Simple proportional horizontal bar chart, one bar per category --
    no charting library dependency, just QPainter."""

    def __init__(self):
        super().__init__()
        self._entries = []  # [(label, seconds, color_hex), ...]
        self.setMinimumHeight(28 * 5 + 10)

    def set_entries(self, entries):
        self._entries = entries
        self.setMinimumHeight(max(1, len(entries)) * 32 + 10)
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        if not self._entries:
            painter.setPen(QColor("#888"))
            painter.drawText(self.rect(), Qt.AlignCenter, "No screen time recorded yet.")
            return

        max_seconds = max(seconds for _label, seconds, _color in self._entries) or 1
        row_height = 32
        label_width = 110
        value_width = 90
        bar_area_width = max(1, self.width() - label_width - value_width - 16)

        y = 6
        for label, seconds, color in self._entries:
            painter.setPen(QColor("#1F2328"))
            painter.drawText(0, y, label_width, row_height - 6, Qt.AlignVCenter | Qt.AlignLeft, label)

            bar_width = int(bar_area_width * (seconds / max_seconds))
            painter.setPen(Qt.NoPen)
            painter.setBrush(QColor(color))
            painter.drawRoundedRect(label_width, y + 4, max(2, bar_width), row_height - 14, 4, 4)

            painter.setPen(QColor("#5A6070"))
            painter.drawText(
                label_width + bar_area_width + 8, y, value_width, row_height - 6,
                Qt.AlignVCenter | Qt.AlignLeft, _format_duration(seconds),
            )
            y += row_height


class _CategorySection(QWidget):
    def __init__(self, category, entries):
        """entries: list of (name, seconds), already sorted desc."""
        super().__init__()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 8, 0, 8)
        layout.setSpacing(4)

        total = sum(seconds for _name, seconds in entries)
        header = QLabel(f"{category}  •  {_format_duration(total)}")
        header.setStyleSheet(f"font-weight: 700; font-size: 13px; color: {_CATEGORY_COLORS.get(category, '#1F2328')};")
        layout.addWidget(header)

        shown = [e for e in entries if e[1] >= GROUP_THRESHOLD_SECONDS]
        grouped = [e for e in entries if e[1] < GROUP_THRESHOLD_SECONDS]

        for name, seconds in shown:
            layout.addWidget(self._row(name, seconds))

        if grouped:
            grouped_total = sum(seconds for _name, seconds in grouped)
            toggle = QPushButton(f"+ {len(grouped)} more under 5 min ({_format_duration(grouped_total)} total)")
            toggle.setProperty("class", "SecondaryButton")
            toggle.setCheckable(True)
            layout.addWidget(toggle)

            details = QWidget()
            details_layout = QVBoxLayout(details)
            details_layout.setContentsMargins(16, 4, 0, 0)
            details_layout.setSpacing(2)
            for name, seconds in grouped:
                details_layout.addWidget(self._row(name, seconds))
            details.setVisible(False)
            layout.addWidget(details)

            toggle.toggled.connect(details.setVisible)

    def _row(self, name, seconds):
        row = QWidget()
        row_layout = QHBoxLayout(row)
        row_layout.setContentsMargins(0, 0, 0, 0)
        name_label = QLabel(name)
        name_label.setStyleSheet("font-size: 12px; color: #1F2328;")
        row_layout.addWidget(name_label, 1)
        time_label = QLabel(_format_duration(seconds))
        time_label.setStyleSheet("font-size: 12px; color: #5A6070;")
        row_layout.addWidget(time_label)
        return row


class ScreenTimeTab(QWidget):
    def __init__(self):
        super().__init__()
        self._range = "day"  # "day" or "week"

        outer = QVBoxLayout(self)
        outer.setContentsMargins(24, 16, 24, 20)
        outer.setSpacing(10)

        title = QLabel("Screen Time")
        title.setStyleSheet("font-size: 20px; font-weight: 700;")
        outer.addWidget(title)

        toggle_row = QHBoxLayout()
        self._day_btn = QPushButton("Daily")
        self._week_btn = QPushButton("Weekly")
        group = QButtonGroup(self)
        group.setExclusive(True)
        for btn, key in ((self._day_btn, "day"), (self._week_btn, "week")):
            btn.setCheckable(True)
            btn.setProperty("class", "SecondaryButton")
            btn.clicked.connect(lambda checked=False, k=key: self._set_range(k))
            group.addButton(btn)
            toggle_row.addWidget(btn)
        toggle_row.addStretch(1)
        self._day_btn.setChecked(True)
        outer.addLayout(toggle_row)

        self._chart = _BarChart()
        outer.addWidget(self._chart)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        self._sections_container = QWidget()
        self._sections_layout = QVBoxLayout(self._sections_container)
        self._sections_layout.setContentsMargins(0, 0, 0, 0)
        self._sections_layout.addStretch(1)
        scroll.setWidget(self._sections_container)
        outer.addWidget(scroll, 1)

        self._timer = QTimer(self)
        self._timer.timeout.connect(self.refresh)
        self._timer.start(REFRESH_MS)

        self.refresh()

    def _set_range(self, key):
        self._range = key
        self.refresh()

    def _current_data(self):
        today = date.today()
        if self._range == "day":
            return screentime_store.get_day(today.isoformat())
        monday = today - timedelta(days=today.weekday())
        sunday = monday + timedelta(days=6)
        return screentime_store.get_range(monday.isoformat(), sunday.isoformat())

    def refresh(self):
        data = self._current_data()

        by_category = {cat: [] for cat in screentime_categories.CATEGORIES}
        for name, seconds in data.get("apps", {}).items():
            by_category[screentime_categories.categorize_app(name)].append((name, seconds))
        for name, seconds in data.get("domains", {}).items():
            by_category[screentime_categories.categorize_domain(name)].append((name, seconds))

        chart_entries = []
        for category in screentime_categories.CATEGORIES:
            total = sum(seconds for _name, seconds in by_category[category])
            if total > 0:
                chart_entries.append((category, total, _CATEGORY_COLORS.get(category, "#9AA0A8")))
        self._chart.set_entries(chart_entries)

        while self._sections_layout.count() > 1:
            item = self._sections_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()

        any_data = False
        for category in screentime_categories.CATEGORIES:
            entries = sorted(by_category[category], key=lambda e: -e[1])
            if not entries:
                continue
            any_data = True
            self._sections_layout.insertWidget(self._sections_layout.count() - 1, _CategorySection(category, entries))

        if not any_data:
            empty = QLabel("Nothing recorded yet today." if self._range == "day" else "Nothing recorded yet this week.")
            empty.setStyleSheet("color: #888; font-size: 13px;")
            self._sections_layout.insertWidget(0, empty)
