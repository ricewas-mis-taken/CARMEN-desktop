"""Home: the page Carmen opens on.

What is on it, and why: the answer to "what should I do now?" comes first (the task that
still asks for the most time, as a ring with a Start button), then the rest of today's
list, then plain numbers and the last week as bars.

Resizing: nothing here word-wraps (a wrapped label makes its height depend on its width,
which is what fights the window while it is dragged), and every text is an ElidedLabel
that gives way instead of pushing the page wider. Two columns when there is room, one
column when there isn't.
"""
from datetime import date, datetime

from PySide6.QtCore import QRectF, QSize, Qt
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import QFrame, QGridLayout, QHBoxLayout, QLabel, QPushButton, QScrollArea, QVBoxLayout, QWidget

import review_store
import session_history
import tasks_store
from qt_ui.history_viewer import minutes_by_day
from qt_ui.ui_kit import (
    FAINT, INK, MUTED, NUM_CSS, SECONDARY_STYLE, ElidedLabel, Ring, button, format_minutes, safe_color,
)

WIDE_FROM = 900          # page width where the two-column layout starts
COMPACT_BELOW = 640      # under this the big ring sits above its text instead of beside it
_CARD = "QFrame#HomeCard{background:#FFFFFF;border:1px solid #E7EAF0;border-radius:18px;}"
_PRIMARY = (
    f"QPushButton{{background:{INK};color:#FFFFFF;border:none;border-radius:12px;"
    "font-size:14px;font-weight:600;padding:0 22px;}"
    "QPushButton:hover{background:#2B3445;}")


def greeting(now):
    hour = now.hour
    if hour < 5:
        return "Still up"
    if hour < 12:
        return "Good morning"
    if hour < 18:
        return "Good afternoon"
    return "Good evening"


def summary(tasks, sessions, due_reviews, today):
    """Everything Home shows, as plain data (so it can be tested without a window)."""
    open_tasks, done = [], 0
    for task in tasks:
        snap = tasks_store.goal_snapshot(task, today, sessions)
        if snap["required"] <= 0:
            continue
        left = snap["required"] - snap["logged"]
        if left > 0.5:
            open_tasks.append({
                "task": task, "left": left, "required": snap["required"],
                "logged": snap["logged"], "phrase": snap["phrase"],
            })
        else:
            done += 1
    open_tasks.sort(key=lambda item: -item["left"])
    week = minutes_by_day(sessions, 7, today=today)
    return {
        "focused_today": week[-1][1],
        "open": open_tasks,
        "done": done,
        "due_reviews": due_reviews,
        "week": week,
    }


def due_review_count():
    total = 0
    for topic in review_store.list_topics():
        total += len(review_store.list_problems(topic["id"], due_only=True))
    return total


def _card():
    frame = QFrame()
    frame.setObjectName("HomeCard")
    frame.setStyleSheet(_CARD)
    return frame


def _heading(text):
    label = ElidedLabel(text.upper())
    label.setStyleSheet(f"color:{FAINT};font-size:11px;font-weight:700;letter-spacing:1px;background:transparent;")
    return label


def _text(text, size=13, colour=MUTED, weight=400):
    label = ElidedLabel(text)
    label.setStyleSheet(f"color:{colour};font-size:{size}px;font-weight:{weight};background:transparent;")
    return label


class _WeekBars(QWidget):
    def __init__(self, week):
        super().__init__()
        self._week = week
        self.setFixedHeight(120)
        self.setMinimumWidth(0)
        self.setToolTip("\n".join(f"{d.strftime('%a %d')}: {format_minutes(m)}" for d, m in week))

    def paintEvent(self, _event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        peak = max([m for _d, m in self._week] + [1])
        slot = self.width() / len(self._week)
        floor = self.height() - 20
        top = 20
        for i, (day, minutes) in enumerate(self._week):
            is_today = i == len(self._week) - 1
            height = max(4.0, (floor - top) * minutes / peak) if minutes else 4.0
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(INK) if is_today else QColor("#D5DBE6"))
            p.drawRoundedRect(QRectF(i * slot + slot * 0.18, floor - height, slot * 0.64, height), 6, 6)
            if minutes:
                p.setPen(QColor(INK if is_today else MUTED))
                p.drawText(QRectF(i * slot, floor - height - 18, slot, 16), Qt.AlignCenter, format_minutes(minutes))
            p.setPen(QColor(INK if is_today else FAINT))
            p.drawText(QRectF(i * slot, floor + 3, slot, 16), Qt.AlignCenter, day.strftime("%a"))


class _Bar(QWidget):
    """A thin progress bar in a task's colour."""

    def __init__(self, fraction, colour):
        super().__init__()
        self._fraction = max(0.0, min(1.0, fraction))
        self._colour = QColor(safe_color(colour))
        self.setFixedHeight(6)

    def paintEvent(self, _event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(Qt.NoPen)
        track = QColor(self._colour)
        track.setAlphaF(0.16)
        p.setBrush(track)
        p.drawRoundedRect(QRectF(0, 0, self.width(), 6), 3, 3)
        if self._fraction > 0:
            p.setBrush(self._colour)
            p.drawRoundedRect(QRectF(0, 0, max(6.0, self.width() * self._fraction), 6), 3, 3)


class HomeTab(QWidget):
    def __init__(self, on_navigate=None):
        super().__init__()
        self._on_navigate = on_navigate or (lambda key: None)
        self._wide = None
        self._compact = None
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setObjectName("HomeScroll")
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        outer.addWidget(scroll)
        self._body = QWidget()
        self._body.setObjectName("HomePage")
        scroll.setWidget(self._body)
        self._grid = QGridLayout(self._body)
        self._grid.setContentsMargins(32, 28, 32, 32)
        self._grid.setHorizontalSpacing(16)
        self._grid.setVerticalSpacing(16)
        self._blocks = []
        self.refresh()

    # a page that can shrink with its window
    def minimumSizeHint(self):
        return QSize(0, 0)

    def showEvent(self, event):
        self.refresh()
        super().showEvent(event)

    def resizeEvent(self, event):
        width = event.size().width()
        compact = width < COMPACT_BELOW
        if compact != self._compact:
            self.refresh(width)       # the hero is built differently, so rebuild it
        elif (width >= WIDE_FROM) != self._wide:
            self._place(width >= WIDE_FROM)
        super().resizeEvent(event)

    def _go(self, key):
        self._on_navigate(key)

    def refresh(self, width=None):
        width = self.width() if width is None else width
        self._compact = width < COMPACT_BELOW
        for block in self._blocks:
            self._grid.removeWidget(block)
            block.deleteLater()
        now = datetime.now()
        data = summary(tasks_store.load_tasks(), session_history.load_all(), due_review_count(), date.today())
        self._blocks = [
            self._header(now),
            self._hero(data),
            self._today_list(data),
            self._stats(data),
            self._week(data),
        ]
        self._place(width >= WIDE_FROM)

    def _place(self, wide):
        """Header across the top; hero + list on the left and stats + week on the right when wide."""
        self._wide = wide
        header, hero, today, stats, week = self._blocks
        for block in self._blocks:
            self._grid.removeWidget(block)
        for column in range(2):
            self._grid.setColumnStretch(column, 0)
        if wide:
            self._grid.addWidget(header, 0, 0, 1, 2)
            self._grid.addWidget(hero, 1, 0)
            self._grid.addWidget(today, 2, 0)
            self._grid.addWidget(stats, 1, 1)
            self._grid.addWidget(week, 2, 1)
            self._grid.setColumnStretch(0, 3)
            self._grid.setColumnStretch(1, 2)
        else:
            for row, block in enumerate((header, hero, stats, today, week)):
                self._grid.addWidget(block, row, 0)
            self._grid.setColumnStretch(0, 1)
        self._grid.setRowStretch(5, 1)
        for block in self._blocks:
            block.show()

    # ---- blocks ----------------------------------------------------------------------

    def _header(self, now):
        box = QWidget()
        layout = QVBoxLayout(box)
        layout.setContentsMargins(0, 0, 0, 4)
        layout.setSpacing(2)
        title = ElidedLabel(greeting(now))
        title.setStyleSheet(f"font-size:30px;font-weight:700;color:{INK};background:transparent;")
        title.setMinimumHeight(40)
        layout.addWidget(title)
        layout.addWidget(_text(now.strftime("%A, %B ") + str(now.day), 14))
        return box

    def _big(self, text):
        label = ElidedLabel(text)
        label.setMinimumHeight(32)
        label.setStyleSheet(f"font-size:24px;font-weight:700;color:{INK};background:transparent;")
        return label

    def _hero(self, data):
        card = _card()
        row = QVBoxLayout(card) if self._compact else QHBoxLayout(card)
        row.setContentsMargins(26, 24, 26, 24)
        row.setSpacing(18 if self._compact else 24)
        text = QVBoxLayout()
        text.setSpacing(4)
        if data["open"]:
            top = data["open"][0]
            colour = safe_color(top["task"].get("color"))
            ring = Ring(116 if self._compact else 132)
            ring.set_state(colour, top["logged"] / top["required"], format_minutes(top["left"]), "left")
            row.addWidget(ring, 0, Qt.AlignLeft if self._compact else Qt.AlignVCenter)
            text.addStretch(1)
            text.addWidget(_heading("Start with"))
            text.addWidget(self._big(top["task"].get("name", "Task")))
            text.addWidget(_text(f"{format_minutes(top['logged'])} of {format_minutes(top['required'])} {top['phrase']}"))
            action = button("Start a session", _PRIMARY, height=44)
            action.clicked.connect(lambda: self._go("focus"))
        elif data["due_reviews"]:
            text.addWidget(_heading("Start with"))
            text.addWidget(self._big(f"{data['due_reviews']} reviews are due"))
            text.addWidget(_text("Every task goal for today is met."))
            action = button("Open Review", _PRIMARY, height=44)
            action.clicked.connect(lambda: self._go("review"))
        else:
            text.addWidget(self._big("Nothing is waiting for you"))
            text.addWidget(_text("Today's goals are met and no reviews are due."))
            action = button("Plan something", SECONDARY_STYLE, height=44)
            action.clicked.connect(lambda: self._go("tasks"))
        text.addSpacing(8)
        text.addWidget(action, 0, Qt.AlignLeft)
        text.addStretch(1)
        row.addLayout(text, 1)
        return card

    def _today_list(self, data):
        card = _card()
        layout = QVBoxLayout(card)
        layout.setContentsMargins(22, 18, 22, 18)
        layout.setSpacing(12)
        done = data["done"]
        layout.addWidget(_heading("Today" + (f"  ·  {done} done" if done else "")))
        rest = data["open"][1:6]
        if not rest:
            layout.addWidget(_text("Nothing else is open." if data["open"] else "All goals met."))
        for item in rest:
            colour = safe_color(item["task"].get("color"))
            line = QHBoxLayout()
            line.setSpacing(12)
            dot = QLabel()
            dot.setFixedSize(10, 10)
            dot.setStyleSheet(f"background:{colour};border-radius:5px;")
            line.addWidget(dot)
            name = ElidedLabel(item["task"].get("name", "Task"))
            name.setStyleSheet(f"color:{INK};font-size:14px;font-weight:600;background:transparent;")
            line.addWidget(name, 1)
            left = QLabel(f"{format_minutes(item['left'])} left")
            left.setStyleSheet(f"color:{MUTED};font-size:12.5px;background:transparent;")
            line.addWidget(left)
            layout.addLayout(line)
            layout.addWidget(_Bar(item["logged"] / item["required"], colour))
        more = len(data["open"]) - 6
        if more > 0:
            layout.addWidget(_text(f"and {more} more", 12.5, FAINT))
        link = button("All tasks", SECONDARY_STYLE, height=34)
        link.clicked.connect(lambda: self._go("tasks"))
        layout.addWidget(link, 0, Qt.AlignLeft)
        return card

    def _stats(self, data):
        card = _card()
        row = QHBoxLayout(card)
        row.setContentsMargins(8, 18, 8, 18)
        row.setSpacing(0)
        items = [
            (format_minutes(data["focused_today"]), "focused today", None),
            (str(len(data["open"])), "tasks open", "tasks"),
            (str(data["due_reviews"]), "reviews due", "review"),
        ]
        for i, (value, caption, target) in enumerate(items):
            cell = QPushButton()
            cell.setFlat(True)
            cell.setCursor(Qt.PointingHandCursor if target else Qt.ArrowCursor)
            cell.setStyleSheet("QPushButton{background:transparent;border:none;text-align:center;}")
            inner = QVBoxLayout(cell)
            inner.setContentsMargins(8, 0, 8, 0)
            inner.setSpacing(0)
            number = QLabel(value)
            number.setAlignment(Qt.AlignCenter)
            number.setAttribute(Qt.WA_TransparentForMouseEvents)
            number.setStyleSheet(f"color:{INK};font-size:28px;font-weight:600;background:transparent;{NUM_CSS}")
            inner.addWidget(number)
            label = QLabel(caption)
            label.setAlignment(Qt.AlignCenter)
            label.setAttribute(Qt.WA_TransparentForMouseEvents)
            label.setStyleSheet(f"color:{MUTED};font-size:12px;background:transparent;")
            inner.addWidget(label)
            cell.setMinimumSize(0, 66)
            if target:
                cell.clicked.connect(lambda _c=False, t=target: self._go(t))
            row.addWidget(cell, 1)
            if i < len(items) - 1:
                rule = QFrame()
                rule.setFixedWidth(1)
                rule.setStyleSheet("background:#E7EAF0;")
                row.addWidget(rule)
        return card

    def _week(self, data):
        card = _card()
        layout = QVBoxLayout(card)
        layout.setContentsMargins(22, 18, 22, 16)
        layout.setSpacing(8)
        total = sum(m for _d, m in data["week"])
        layout.addWidget(_heading("Last 7 days  ·  " + format_minutes(total)))
        layout.addWidget(_WeekBars(data["week"]))
        return card
