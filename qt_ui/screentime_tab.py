"""Screen Time tab: an always-on, session-independent tally of how long
each app and website has had the user's attention today/this week, sourced
from screentime_store.py (populated by window_tracker.py's polling loop for
apps, and by the browser extension's POST /screentime/domain for domains --
see that module's docstring). Grouped by category
(Entertainment/Education/Games/Tools/Other, screentime_categories.py), with
anything under 5 minutes collapsed into one expandable row per category
instead of cluttering the list with noise.

The page opens on the total (a big numeral) and one bar split by category, then a
card per category with its biggest apps and sites.
"""
from datetime import date, timedelta

from PySide6.QtCore import QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import (
    QButtonGroup,
    QFrame,
    QGraphicsDropShadowEffect,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

import screentime_categories
import screentime_store
from qt_ui.ui_kit import FAINT, INK, MUTED, NUM_CSS

REFRESH_MS = 5000
GROUP_THRESHOLD_SECONDS = 5 * 60

# Same hues the browser extension's Screen Time page uses.
_CATEGORY_COLORS = {
    "Entertainment": "#D4572E",
    "Education": "#2B47E0",
    "Games": "#7A3FCF",
    "Tools": "#0F7A58",
    "Other": "#8A93A3",
}

_SEGMENT_STYLE = (
    f"QPushButton{{background:#FFFFFF;color:{INK};border:1px solid #E1E5EC;border-radius:13px;"
    "font-size:12.5px;font-weight:600;padding:0 14px;}"
    "QPushButton:hover{background:#F6F7FA;}"
    f"QPushButton:checked{{background:{INK};color:#FFFFFF;border:1px solid {INK};}}")


def _format_duration(seconds):
    seconds = int(round(seconds))
    hours, rem = divmod(seconds, 3600)
    minutes, secs = divmod(rem, 60)
    if hours:
        return f"{hours}h {minutes}m"
    if minutes:
        return f"{minutes}m {secs}s"
    return f"{secs}s"


def _format_total(seconds):
    """The hero number: hours and minutes, never seconds once it is a real amount."""
    seconds = int(round(seconds))
    hours, rem = divmod(seconds, 3600)
    minutes = rem // 60
    if hours:
        return f"{hours}h {minutes:02d}m"
    if minutes:
        return f"{minutes}m"
    return f"{seconds}s"


def _card(object_name="STCard"):
    frame = QFrame()
    frame.setObjectName(object_name)
    frame.setStyleSheet(f"QFrame#{object_name}{{background:#FFFFFF;border:1px solid #E6E9EF;border-radius:16px;}}")
    shadow = QGraphicsDropShadowEffect(frame)
    shadow.setBlurRadius(22)
    shadow.setOffset(0, 5)
    shadow.setColor(QColor(16, 24, 40, 18))
    frame.setGraphicsEffect(shadow)
    return frame


class _StackedBar(QWidget):
    """One bar split into a segment per category, each as wide as its share of the
    total -- the whole day at a glance. No charting library, just QPainter."""

    def __init__(self):
        super().__init__()
        self._entries = []  # [(label, seconds, color_hex), ...]
        self._wipe = 1.0
        self._anim = None
        self.setFixedHeight(18)

    replay_on_enter = True

    def replay(self):
        """The bar draws itself left to right (when the tab is opened or the day changes)."""
        from qt_ui import motion
        motion.stop(self._anim)
        self._anim = motion.tween(self, 0.0, 1.0, motion.MEDIUM_MS, self._set_wipe)

    def _set_wipe(self, value):
        self._wipe = value
        self.update()

    def set_entries(self, entries):
        changed = entries != self._entries
        self._entries = entries
        self.update()
        if changed and self.isVisible():
            self.replay()

    def segments(self, width):
        """[(label, x, w)] for the current entries in `width` pixels (gaps included)."""
        total = sum(seconds for _label, seconds, _color in self._entries)
        if total <= 0:
            return []
        gap = 3
        usable = max(1, width - gap * (len(self._entries) - 1))
        out, x = [], 0.0
        for label, seconds, _color in self._entries:
            w = max(4.0, usable * seconds / total)
            out.append((label, x, w))
            x += w + gap
        return out

    def paintEvent(self, _event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setPen(Qt.NoPen)
        if not self._entries:
            painter.setBrush(QColor("#EEF0F4"))
            painter.drawRoundedRect(QRectF(0, 0, self.width(), self.height()), 9, 9)
            return
        colors = {label: color for label, _s, color in self._entries}
        painter.setClipRect(QRectF(0, 0, self.width() * self._wipe + 1, self.height()))
        for label, x, w in self.segments(self.width()):
            painter.setBrush(QColor(colors[label]))
            painter.drawRoundedRect(QRectF(x, 0, w, self.height()), 6, 6)


class _MiniBar(QWidget):
    """A thin bar for one app/site, as long as its share of the category's biggest."""

    def __init__(self, fraction, color):
        super().__init__()
        self._fraction = max(0.0, min(1.0, fraction))
        self._color = QColor(color)
        self.setFixedHeight(6)
        self.setMinimumWidth(60)

    def paintEvent(self, _event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setPen(Qt.NoPen)
        track = QColor(self._color)
        track.setAlphaF(0.14)
        painter.setBrush(track)
        painter.drawRoundedRect(QRectF(0, 0, self.width(), self.height()), 3, 3)
        painter.setBrush(self._color)
        painter.drawRoundedRect(QRectF(0, 0, max(4.0, self.width() * self._fraction), self.height()), 3, 3)


class _Dot(QFrame):
    """A colour dot. A QFrame, not a QLabel, so a section's first label is its name."""

    def __init__(self, color, size=10):
        super().__init__()
        self.setFixedSize(size, size)
        self.setStyleSheet(f"background:{color};border-radius:{size // 2}px;border:none;")


class _CategorySection(QFrame):
    def __init__(self, category, entries):
        """entries: list of (name, seconds), already sorted desc."""
        super().__init__()
        self.setObjectName("STSection")
        self.setStyleSheet(
            "QFrame#STSection{background:#FFFFFF;border:1px solid #E6E9EF;border-radius:16px;}")
        self._color = _CATEGORY_COLORS.get(category, "#8A93A3")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 14, 18, 14)
        layout.setSpacing(6)

        total = sum(seconds for _name, seconds in entries)
        self._peak = max((seconds for _name, seconds in entries), default=1) or 1
        head = QWidget()
        head_row = QHBoxLayout(head)
        head_row.setContentsMargins(0, 0, 0, 4)
        head_row.setSpacing(8)
        head_row.addWidget(_Dot(self._color), 0, Qt.AlignVCenter)
        name = QLabel(category)
        name.setStyleSheet(f"font-weight:700;font-size:14px;color:{INK};background:transparent;")
        head_row.addWidget(name)
        head_row.addStretch(1)
        total_label = QLabel(_format_duration(total))
        total_label.setStyleSheet(f"font-size:13px;font-weight:600;color:{MUTED};background:transparent;")
        head_row.addWidget(total_label)
        layout.addWidget(head)

        shown = [e for e in entries if e[1] >= GROUP_THRESHOLD_SECONDS]
        grouped = [e for e in entries if e[1] < GROUP_THRESHOLD_SECONDS]

        for name_text, seconds in shown:
            layout.addWidget(self._row(name_text, seconds))

        if grouped:
            grouped_total = sum(seconds for _name, seconds in grouped)
            toggle = QPushButton(f"+ {len(grouped)} more under 5 min ({_format_duration(grouped_total)} total)")
            toggle.setCheckable(True)
            toggle.setCursor(Qt.PointingHandCursor)
            toggle.setStyleSheet(
                f"QPushButton{{background:transparent;color:{MUTED};border:none;font-size:12.5px;"
                "font-weight:600;text-align:left;padding:4px 0;}"
                f"QPushButton:hover{{color:{INK};}}")
            layout.addWidget(toggle)

            details = QWidget()
            details_layout = QVBoxLayout(details)
            details_layout.setContentsMargins(0, 2, 0, 0)
            details_layout.setSpacing(4)
            for name_text, seconds in grouped:
                details_layout.addWidget(self._row(name_text, seconds))
            details.setVisible(False)
            layout.addWidget(details)

            toggle.toggled.connect(details.setVisible)

    def _row(self, name, seconds):
        row = QWidget()
        row_layout = QHBoxLayout(row)
        row_layout.setContentsMargins(0, 2, 0, 2)
        row_layout.setSpacing(12)
        name_label = QLabel(name)
        name_label.setMinimumWidth(110)
        name_label.setStyleSheet(f"font-size:13px;color:{INK};background:transparent;")
        row_layout.addWidget(name_label, 2)
        row_layout.addWidget(_MiniBar(seconds / self._peak, self._color), 3, Qt.AlignVCenter)
        time_label = QLabel(_format_duration(seconds))
        time_label.setMinimumWidth(64)
        time_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        time_label.setStyleSheet(f"font-size:12.5px;color:{MUTED};background:transparent;")
        row_layout.addWidget(time_label)
        return row


class ScreenTimeTab(QWidget):
    def __init__(self):
        super().__init__()
        self._range = "day"  # "day" or "week"
        self._device = "this"  # "this", "all", or another computer's id
        self._device_keys = None

        outer = QVBoxLayout(self)
        outer.setContentsMargins(28, 24, 28, 20)
        outer.setSpacing(14)

        head = QHBoxLayout()
        head.setSpacing(8)
        title = QLabel("Screen Time")
        title.setStyleSheet(f"font-size:26px;font-weight:700;color:{INK};background:transparent;")
        head.addWidget(title)
        head.addStretch(1)
        self._day_btn = QPushButton("Today")
        self._week_btn = QPushButton("This week")
        group = QButtonGroup(self)
        group.setExclusive(True)
        for btn, key in ((self._day_btn, "day"), (self._week_btn, "week")):
            btn.setCheckable(True)
            btn.setCursor(Qt.PointingHandCursor)
            btn.setFixedHeight(32)
            btn.setStyleSheet(_SEGMENT_STYLE)
            btn.clicked.connect(lambda checked=False, k=key: self._set_range(k))
            group.addButton(btn)
            head.addWidget(btn)
        self._day_btn.setChecked(True)
        outer.addLayout(head)

        # One button per computer (this one, each other one, and all together);
        # only shown once another computer has sent its screen time.
        self._device_bar = QWidget()
        self._device_layout = QHBoxLayout(self._device_bar)
        self._device_layout.setContentsMargins(0, 0, 0, 0)
        self._device_layout.setSpacing(6)
        self._device_group = QButtonGroup(self)
        self._device_group.setExclusive(True)
        self._device_bar.setVisible(False)
        outer.addWidget(self._device_bar)

        outer.addWidget(self._build_hero())

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        # A bare QScrollArea's viewport autofills with the OS palette's
        # Window color (dark on a dark-mode Windows install) rather than
        # inheriting the light #F7F7F8 ContentArea background it sits in --
        # the "QScrollArea > QWidget > QWidget" chain (QScrollArea -> its
        # internal viewport -> the widget passed to setWidget()) is the
        # actual widget path QSS needs to target to fix this, plain
        # "QScrollArea { background: ... }" alone doesn't reach the
        # viewport. Without this, every dark-colored label/row drawn here
        # (all of them assume a light background, matching every other
        # popup/tab in the app) is invisible against that dark fill.
        scroll.setStyleSheet(
            "QScrollArea { background: #F7F7F8; border: none; }"
            "QScrollArea > QWidget > QWidget { background: #F7F7F8; }"
        )
        self._sections_container = QWidget()
        self._sections_layout = QVBoxLayout(self._sections_container)
        self._sections_layout.setContentsMargins(2, 2, 8, 12)
        self._sections_layout.setSpacing(12)
        self._sections_layout.addStretch(1)
        scroll.setWidget(self._sections_container)
        outer.addWidget(scroll, 1)

        self._timer = QTimer(self)
        self._timer.timeout.connect(self.refresh)
        self._timer.start(REFRESH_MS)

        self.refresh()

    def _build_hero(self):
        card = _card("STHero")
        box = QVBoxLayout(card)
        box.setContentsMargins(24, 20, 24, 20)
        box.setSpacing(10)

        top = QHBoxLayout()
        top.setSpacing(14)
        self._total_label = QLabel("0s")
        self._total_label.setStyleSheet(f"font-size:54px;{NUM_CSS}color:{INK};background:transparent;")
        top.addWidget(self._total_label, 0, Qt.AlignBottom)
        self._caption = QLabel("")
        self._caption.setStyleSheet(f"font-size:13px;color:{MUTED};background:transparent;")
        top.addWidget(self._caption, 0, Qt.AlignBottom)
        top.addStretch(1)
        box.addLayout(top)

        self._chart = _StackedBar()
        box.addWidget(self._chart)

        self._legend = QHBoxLayout()
        self._legend.setSpacing(16)
        self._legend_labels = []
        box.addLayout(self._legend)
        return card

    def _set_range(self, key):
        self._range = key
        self.refresh()

    def _set_device(self, key):
        self._device = key
        self.refresh()

    def _sync_device_buttons(self):
        """Rebuilds the computer buttons when the set of computers changed."""
        others = screentime_store.remote_devices()
        keys = tuple(d["id"] for d in others)
        if self._device not in ("this", "all") and self._device not in keys:
            self._device = "this"        # that computer is gone: back to this one
        if keys == self._device_keys:
            return
        self._device_keys = keys
        for button in list(self._device_group.buttons()):
            self._device_group.removeButton(button)
            self._device_layout.removeWidget(button)
            button.deleteLater()
        self._device_bar.setVisible(bool(others))
        if not others:
            return
        entries = [("this", "This computer")] + [(d["id"], d["name"]) for d in others] + [("all", "All computers")]
        for key, label in entries:
            button = QPushButton(label)
            button.setCheckable(True)
            button.setCursor(Qt.PointingHandCursor)
            button.setFixedHeight(28)
            button.setStyleSheet(_SEGMENT_STYLE)
            button.setChecked(key == self._device)
            button.clicked.connect(lambda checked=False, k=key: self._set_device(k))
            self._device_group.addButton(button)
            self._device_layout.addWidget(button)
        self._device_layout.addStretch(1)

    def _current_data(self):
        today = date.today()
        if self._range == "day":
            return screentime_store.get_day(today.isoformat(), self._device)
        monday = today - timedelta(days=today.weekday())
        sunday = monday + timedelta(days=6)
        return screentime_store.get_range(monday.isoformat(), sunday.isoformat(), self._device)

    def _device_caption(self):
        if self._device == "this":
            return "this computer"
        if self._device == "all":
            return "all computers"
        for device in screentime_store.remote_devices():
            if device["id"] == self._device:
                return device["name"]
        return "this computer"

    def _set_legend(self, entries):
        for label in self._legend_labels:
            self._legend.removeWidget(label)
            label.deleteLater()
        self._legend_labels = []
        while self._legend.count():
            self._legend.takeAt(0)
        for category, seconds, color in entries:
            label = QLabel(
                f'<span style="color:{color};">●</span>&nbsp;<span style="color:{INK};font-weight:600;">'
                f'{category}</span>&nbsp;<span style="color:{MUTED};">{_format_total(seconds)}</span>')
            label.setStyleSheet("font-size:12.5px;background:transparent;")
            self._legend.addWidget(label)
            self._legend_labels.append(label)
        self._legend.addStretch(1)

    def refresh(self):
        self._sync_device_buttons()
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
                chart_entries.append((category, total, _CATEGORY_COLORS.get(category, "#8A93A3")))
        self._chart.set_entries(chart_entries)
        self._set_legend(chart_entries)
        grand_total = sum(seconds for _category, seconds, _color in chart_entries)
        self._total_label.setText(_format_total(grand_total))
        when = "today" if self._range == "day" else "this week"
        self._caption.setText(f"{when} · {self._device_caption()}")

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
            empty.setAlignment(Qt.AlignCenter)
            empty.setStyleSheet(
                f"color:{FAINT};font-size:14px;background:#FFFFFF;border:1px dashed #D0D5DD;"
                "border-radius:16px;padding:32px 24px;")
            self._sections_layout.insertWidget(0, empty)
