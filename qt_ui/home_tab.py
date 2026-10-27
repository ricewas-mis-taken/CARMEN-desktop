"""Home: the page Carmen opens on.

What is on it, and why: the answer to "what should I do now?" comes first -- the live
session with a big clock and Pause/Resume, or else the task that still asks for the most
time with a Start button. Under it: today's progress, what needs you (a paused attempt,
due reviews, the next event), a quick-capture line with the top of the board, and an
eight-week consistency grid with the streak.

Resizing: nothing here word-wraps (a wrapped label makes its height depend on its width,
which is what fights the window while it is dragged), and every text is an ElidedLabel
that gives way instead of pushing the page wider. Two columns when there is room, one
column when there isn't.
"""
from datetime import date, datetime, timedelta

from PySide6.QtCore import QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import (
    QFrame, QGridLayout, QHBoxLayout, QLabel, QPushButton, QScrollArea, QVBoxLayout, QWidget,
)

import board_store
import calendar_recurrence
import calendar_store
import quicknote_store
import review_store
import session_history
import session_manager
import tasks_store
from qt_ui.history_viewer import minutes_by_day
from qt_ui.mini_timer import describe as describe_session
from qt_ui.ui_kit import (
    BREAK_COLOR, FAINT, INK, MUTED, SECONDARY_STYLE, ElidedLabel, Ring, button, format_minutes, safe_color, tint,
)
from qt_ui.wrap_edit import WrapEdit

WIDE_FROM = 900          # page width where the two-column layout starts
COMPACT_BELOW = 640      # under this the big ring sits above its text instead of beside it
GRID_WEEKS = 53          # a whole year of days, like a contribution graph
TIMER_MS = 1000
_GRID_BLUE = "#5B8DEF"
_NOTE_HINT = "Add to QuickNote, then Enter"
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


def streak_days(minutes):
    """Days in a row with some focus, counted back from today (or from yesterday while today is
    still empty, so the streak does not read 0 every morning). `minutes` is [(date, minutes)]
    oldest first."""
    days = [m for _d, m in minutes]
    if days and days[-1] < 1:
        days = days[:-1]
    run = 0
    for m in reversed(days):
        if m < 1:
            break
        run += 1
    return run


def grid_cells(sessions, today, weeks=GRID_WEEKS):
    """[[(date or None, minutes)]]: `weeks` columns of 7 days (Monday first), the last column this
    week. Days after today are (None, 0)."""
    monday = today - timedelta(days=today.weekday())
    first = monday - timedelta(weeks=weeks - 1)
    span = (today - first).days + 1
    by_day = dict(minutes_by_day(sessions, span, today=today))
    columns = []
    for w in range(weeks):
        column = []
        for d in range(7):
            day = first + timedelta(weeks=w, days=d)
            column.append((day, by_day.get(day, 0.0)) if day <= today else (None, 0.0))
        columns.append(column)
    return columns


def next_event(now, events=None):
    """(start, title) of the next calendar event within two days, or None."""
    try:
        events = calendar_store.list_events() if events is None else events
        found = calendar_recurrence.next_occurrences(events, now, count=1, lookahead_days=2)
    except Exception:
        return None
    if not found:
        return None
    start, _end, event = found[0]
    return start, event.get("title") or "Event"


def waiting_sessions(status):
    """What is set aside and could be resumed: a paused live session, and any parked behind it."""
    out = []
    if status.get("isActive") and status.get("isPaused"):
        out.append({"kind": "paused", "label": describe_session(status)[0]})
    for parked in status.get("parkedSessions") or []:
        if session_manager.is_first_attempt(parked):
            label = "First attempt"
        elif parked.get("reviewProblemName"):
            label = f"Review: {parked['reviewProblemName']}"
        else:
            label = parked.get("eventTitle") or "A session"
        out.append({"kind": "parked", "label": label})
    return out


def summary(tasks, sessions, due_reviews, today):
    """Everything Home shows, as plain data (so it can be tested without a window)."""
    open_tasks, done_tasks = [], []
    total_required = total_logged = 0.0
    for task in tasks:
        snap = tasks_store.goal_snapshot(task, today, sessions)
        if snap["required"] <= 0:
            continue
        required, logged, period_left = snap["required"], snap["logged"], None
        if snap["isPeriod"]:
            # A week / month goal is shown as what today has to bring in to stay on pace:
            # what is still missing at the start of today, spread over the days left.
            logged_today = min(logged, tasks_store.logged_seconds_for_date(task, today, sessions) / 60)
            period_left = max(0.0, snap["required"] - logged)
            before_today = logged - logged_today
            required = max(0.0, snap["required"] - before_today) / max(1, snap["daysLeft"])
            logged = logged_today
            if period_left <= 0.5:
                required = logged = 0.0
        total_required += required
        total_logged += min(logged, required)
        left = required - logged
        if left > 0.5:
            open_tasks.append({
                "task": task, "left": left, "required": required, "logged": logged,
                "phrase": "today" if snap["isPeriod"] else snap["phrase"],
                "period_left": period_left, "period_phrase": snap["phrase"], "days_left": snap["daysLeft"],
            })
        else:
            done_tasks.append(task)
    open_tasks.sort(key=lambda item: -item["left"])
    week = minutes_by_day(sessions, 7, today=today)
    return {
        "focused_today": week[-1][1],
        "open": open_tasks,
        "done": len(done_tasks),
        "done_tasks": done_tasks,
        "total_required": total_required,
        "total_logged": total_logged,
        "due_reviews": due_reviews,
        "week": week,
        "streak": streak_days(minutes_by_day(sessions, 60, today=today)),
        "grid": grid_cells(sessions, today),
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
    label = ElidedLabel(text)
    label.setStyleSheet(f"color:{MUTED};font-size:13px;font-weight:600;background:transparent;")
    return label


def _text(text, size=13, colour=MUTED, weight=400):
    label = ElidedLabel(text)
    label.setStyleSheet(f"color:{colour};font-size:{size}px;font-weight:{weight};background:transparent;")
    return label


def _chip_done(task):
    label = ElidedLabel("✓  " + task.get("name", "Task"))
    label.setMaximumWidth(150)
    label.setStyleSheet(
        f"color:{INK};background:{tint(safe_color(task.get('color')), 0.16)};"
        "border-radius:10px;padding:3px 10px;font-size:12px;font-weight:600;")
    return label


class _Bar(QWidget):
    """A thin progress bar in a task's colour."""

    def __init__(self, fraction, colour, height=6):
        super().__init__()
        self._fraction = max(0.0, min(1.0, fraction))
        self._colour = QColor(safe_color(colour))
        self._height = height
        self.setFixedHeight(height)

    def paintEvent(self, _event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(Qt.NoPen)
        track = QColor(self._colour)
        track.setAlphaF(0.16)
        radius = self._height / 2
        p.setBrush(track)
        p.drawRoundedRect(QRectF(0, 0, self.width(), self._height), radius, radius)
        if self._fraction > 0:
            p.setBrush(self._colour)
            width = max(float(self._height), self.width() * self._fraction)
            p.drawRoundedRect(QRectF(0, 0, width, self._height), radius, radius)


class _Grid(QWidget):
    """A year of days, one square each, darker the more focus that day, months along the top.
    Hover a day to read it (the line under the grid and the tooltip both say it)."""
    hovered = Signal(str)
    MAX_STEP, LEFT, TOP = 17, 30, 20

    def __init__(self, columns):
        super().__init__()
        self._columns = columns
        self._peak = max([m for col in columns for _d, m in col] + [1])
        self._hover = None
        self.setMouseTracking(True)
        self.setMinimumWidth(0)
        self.setFixedHeight(self.TOP + 7 * self.MAX_STEP)

    def _step(self):
        return max(4.0, min(float(self.MAX_STEP), (self.width() - self.LEFT) / len(self._columns)))

    def _cell_at(self, pos):
        step = self._step()
        x, y = int((pos.x() - self.LEFT) // step), int((pos.y() - self.TOP) // step)
        if 0 <= x < len(self._columns) and 0 <= y < 7 and self._columns[x][y][0] is not None:
            return x, y
        return None

    def _describe(self, cell):
        day, minutes = self._columns[cell[0]][cell[1]]
        return f"{day.strftime('%A, %B %d')}: {format_minutes(minutes) if minutes >= 1 else 'no focus'}"

    def mouseMoveEvent(self, ev):
        cell = self._cell_at(ev.position())
        if cell != self._hover:
            self._hover = cell
            self.hovered.emit(self._describe(cell) if cell else "")
            self.setToolTip(self._describe(cell) if cell else "")
            self.update()
        super().mouseMoveEvent(ev)

    def leaveEvent(self, ev):
        if self._hover is not None:
            self._hover = None
            self.hovered.emit("")
            self.update()
        super().leaveEvent(ev)

    def paintEvent(self, _event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        step = self._step()
        side = step - max(2.0, step * 0.2)
        radius = max(1.5, side * 0.25)
        today = date.today()
        font = p.font()
        font.setPixelSize(11)
        p.setFont(font)
        p.setPen(QColor(FAINT))
        for row, name in ((0, "Mon"), (2, "Wed"), (4, "Fri")):
            p.drawText(QRectF(0, self.TOP + row * step - 1, self.LEFT - 6, step), Qt.AlignRight | Qt.AlignVCenter, name)
        last_month = None
        for x, column in enumerate(self._columns):
            first_day = next((d for d, _m in column if d is not None), None)
            if first_day and first_day.month != last_month:
                if last_month is not None or first_day.day <= 7:
                    p.setPen(QColor(MUTED))
                    p.drawText(QRectF(self.LEFT + x * step, 0, step * 4, self.TOP - 4), Qt.AlignLeft | Qt.AlignVCenter,
                               first_day.strftime("%b"))
                last_month = first_day.month
        for x, column in enumerate(self._columns):
            for y, (day, minutes) in enumerate(column):
                if day is None:
                    continue
                colour = QColor(_GRID_BLUE) if minutes >= 1 else QColor("#E9ECF2")
                if minutes >= 1:
                    colour.setAlphaF(0.25 + 0.75 * min(1.0, minutes / self._peak))
                rect = QRectF(self.LEFT + x * step, self.TOP + y * step, side, side)
                p.setPen(Qt.NoPen)
                p.setBrush(colour)
                p.drawRoundedRect(rect, radius, radius)
                if day == today or (x, y) == self._hover:
                    p.setBrush(Qt.NoBrush)
                    p.setPen(QColor(INK))
                    p.drawRoundedRect(rect.adjusted(0.5, 0.5, -0.5, -0.5), radius, radius)


class HomeTab(QWidget):
    def __init__(self, on_navigate=None):
        super().__init__()
        self._on_navigate = on_navigate or (lambda key: None)
        self._wide = None
        self._compact = None
        self._signature = None
        self._live = None            # (ring, colour) while a live session is on show
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
        self._timer = QTimer(self)
        self._timer.setInterval(TIMER_MS)
        self._timer.timeout.connect(self._tick)
        self.refresh()

    # a page that can shrink with its window
    def minimumSizeHint(self):
        return QSize(0, 0)

    def showEvent(self, event):
        self.refresh()
        self._timer.start()
        super().showEvent(event)

    def hideEvent(self, event):
        self._timer.stop()
        super().hideEvent(event)

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

    @staticmethod
    def _signature_of(status):
        return (
            status.get("isActive"), status.get("isPaused"), status.get("isBreak"), status.get("eventId"),
            status.get("reviewProblemName"), len(status.get("parkedSessions") or []),
        )

    def _tick(self):
        """Once a second: rebuild if the session changed, otherwise just move the clock."""
        status = session_manager.get_status()
        if self._signature_of(status) != self._signature:
            self.refresh()
            return
        if self._live is not None and status.get("isActive"):
            ring, colour = self._live
            _label, clock, fraction, _can_pause = describe_session(status)
            paused = bool(status.get("isPaused"))
            ring.set_state(colour, fraction or 0.0, clock, "paused" if paused else "", dim=paused)

    def refresh(self, width=None):
        width = self.width() if width is None else width
        self._compact = width < COMPACT_BELOW
        for block in self._blocks:
            self._grid.removeWidget(block)
            block.deleteLater()
        now = datetime.now()
        status = session_manager.get_status()
        self._signature = self._signature_of(status)
        self._live = None
        data = summary(tasks_store.load_tasks(), session_history.load_all(), due_review_count(), date.today())
        self._blocks = [
            self._header(now, data),
            self._hero(data, status),
            self._today(data),
            self._needs_you(data, status, now),
            self._capture_card(),
            self._consistency(data),
        ]
        self._place(width >= WIDE_FROM)

    def _place(self, wide):
        """Wide: header and hero across the top, then two columns. Narrow: one column."""
        self._wide = wide
        header, hero, today, needs, capture, consistency = self._blocks
        for block in self._blocks:
            self._grid.removeWidget(block)
        for column in range(2):
            self._grid.setColumnStretch(column, 0)
        if wide:
            self._grid.addWidget(header, 0, 0, 1, 2)
            self._grid.addWidget(hero, 1, 0, 1, 2)
            self._grid.addWidget(today, 2, 0)
            self._grid.addWidget(needs, 2, 1)
            self._grid.addWidget(capture, 3, 0, 1, 2)
            self._grid.addWidget(consistency, 4, 0, 1, 2)
            self._grid.setColumnStretch(0, 3)
            self._grid.setColumnStretch(1, 2)
        else:
            for row, block in enumerate((header, hero, needs, today, capture, consistency)):
                self._grid.addWidget(block, row, 0)
            self._grid.setColumnStretch(0, 1)
        self._grid.setRowStretch(7, 1)
        for block in self._blocks:
            block.show()

    # ---- blocks ----------------------------------------------------------------------

    def _header(self, now, data):
        box = QWidget()
        layout = QVBoxLayout(box)
        layout.setContentsMargins(0, 0, 0, 4)
        layout.setSpacing(2)
        title = ElidedLabel(greeting(now))
        title.setStyleSheet(f"font-size:30px;font-weight:700;color:{INK};background:transparent;")
        title.setMinimumHeight(40)
        layout.addWidget(title)
        line = now.strftime("%A, %B ") + str(now.day)
        if data["streak"] >= 2:
            line += f"   ·   {data['streak']}-day streak"
        layout.addWidget(_text(line, 14))
        return box

    def _big(self, text):
        label = ElidedLabel(text)
        label.setMinimumHeight(32)
        label.setStyleSheet(f"font-size:24px;font-weight:700;color:{INK};background:transparent;")
        return label

    def _hero(self, data, status):
        card = _card()
        row = QVBoxLayout(card) if self._compact else QHBoxLayout(card)
        row.setContentsMargins(26, 24, 26, 24)
        row.setSpacing(18 if self._compact else 24)
        text = QVBoxLayout()
        text.setSpacing(4)
        size = 116 if self._compact else 132
        actions = QHBoxLayout()
        actions.setSpacing(10)
        if status.get("isActive"):
            label, clock, fraction, can_pause = describe_session(status)
            paused = bool(status.get("isPaused"))
            colour = BREAK_COLOR if status.get("isBreak") else _GRID_BLUE
            ring = Ring(size)
            ring.set_state(colour, fraction or 0.0, clock, "paused" if paused else "", dim=paused)
            self._live = (ring, colour)
            row.addWidget(ring, 0, Qt.AlignLeft if self._compact else Qt.AlignVCenter)
            text.addStretch(1)
            text.addWidget(_heading("Paused" if paused else "Right now"))
            text.addWidget(self._big(label))
            if can_pause:
                toggle = button("Resume" if paused else "Pause", _PRIMARY, height=44)
                toggle.clicked.connect(self._toggle_pause)
                actions.addWidget(toggle)
            open_focus = button("Open Focus", SECONDARY_STYLE, height=44)
            open_focus.clicked.connect(lambda: self._go("focus"))
            actions.addWidget(open_focus)
        elif data["open"]:
            top = data["open"][0]
            ring = Ring(size)
            ring.set_state(
                safe_color(top["task"].get("color")), top["logged"] / top["required"],
                format_minutes(top["left"]), "left")
            row.addWidget(ring, 0, Qt.AlignLeft if self._compact else Qt.AlignVCenter)
            text.addStretch(1)
            text.addWidget(_heading("Start with"))
            text.addWidget(self._big(top["task"].get("name", "Task")))
            text.addWidget(_text(f"{format_minutes(top['logged'])} of {format_minutes(top['required'])} {top['phrase']}"))
            if top["period_left"] is not None:
                days = top["days_left"]
                text.addWidget(_text(
                    f"{format_minutes(top['period_left'])} left {top['period_phrase']}, over {days} day{'s' if days != 1 else ''}",
                    13, FAINT))
            action = button("Start a session", _PRIMARY, height=44)
            action.clicked.connect(lambda: self._go("focus"))
            actions.addWidget(action)
        elif data["due_reviews"]:
            text.addWidget(_heading("Start with"))
            text.addWidget(self._big(f"{data['due_reviews']} reviews are due"))
            text.addWidget(_text("Every task goal for today is met."))
            action = button("Open Review", _PRIMARY, height=44)
            action.clicked.connect(lambda: self._go("review"))
            actions.addWidget(action)
        else:
            text.addWidget(self._big("Nothing is waiting for you"))
            text.addWidget(_text("Today's goals are met and no reviews are due."))
            action = button("Plan something", SECONDARY_STYLE, height=44)
            action.clicked.connect(lambda: self._go("tasks"))
            actions.addWidget(action)
        actions.addStretch(1)
        text.addSpacing(8)
        text.addLayout(actions)
        text.addStretch(1)
        row.addLayout(text, 1)
        return card

    def _toggle_pause(self):
        if session_manager.get_status().get("isPaused"):
            session_manager.resume_session()
        else:
            session_manager.pause_session()
        self.refresh()

    def _today(self, data):
        card = _card()
        layout = QVBoxLayout(card)
        layout.setContentsMargins(22, 18, 22, 18)
        layout.setSpacing(12)
        done = data["done"]
        layout.addWidget(_heading("Today" + (f"  ·  {done} done" if done else "")))
        if data["total_required"] > 0:
            layout.addWidget(_text(
                f"{format_minutes(data['total_logged'])} of {format_minutes(data['total_required'])} goal",
                15, INK, 600))
            layout.addWidget(_Bar(data["total_logged"] / data["total_required"], _GRID_BLUE, height=10))
        if data["done_tasks"]:
            chips = QHBoxLayout()
            chips.setSpacing(6)
            for task in data["done_tasks"][:4]:
                chips.addWidget(_chip_done(task))
            chips.addStretch(1)
            layout.addLayout(chips)
        # The hero already features the top task when nothing is running.
        skip = 0 if self._live is not None else 1
        rest = data["open"][skip:skip + 5]
        if not data["open"]:
            layout.addWidget(_text("All goals met." if data["done"] else "No goals set for today."))
        for item in rest:
            colour = safe_color(item["task"].get("color"))
            line = QHBoxLayout()
            line.setSpacing(12)
            dot = QLabel()
            dot.setFixedSize(12, 12)
            dot.setStyleSheet(f"background:{colour};border-radius:6px;")
            line.addWidget(dot)
            name = ElidedLabel(item["task"].get("name", "Task"))
            name.setStyleSheet(f"color:{INK};font-size:17px;font-weight:600;background:transparent;")
            line.addWidget(name, 1)
            left = QLabel(f"{format_minutes(item['left'])} left")
            left.setStyleSheet(f"color:{MUTED};font-size:14px;background:transparent;")
            line.addWidget(left)
            layout.addLayout(line)
            layout.addWidget(_Bar(item["logged"] / item["required"], colour))
        more = len(data["open"]) - skip - 5
        if more > 0:
            layout.addWidget(_text(f"and {more} more", 12.5, FAINT))
        link = button("All tasks", SECONDARY_STYLE, height=34)
        link.clicked.connect(lambda: self._go("tasks"))
        layout.addWidget(link, 0, Qt.AlignLeft)
        return card

    def _needs_you(self, data, status, now):
        card = _card()
        layout = QVBoxLayout(card)
        layout.setContentsMargins(22, 18, 22, 18)
        layout.setSpacing(12)
        layout.addWidget(_heading("Needs you"))
        rows = 0
        for waiting in waiting_sessions(status):
            if waiting["kind"] == "paused":
                continue            # the hero already has its Resume button
            rows += 1
            layout.addLayout(self._need_row("Resume", waiting["label"], "set aside", lambda: self._go("focus")))
        if data["due_reviews"]:
            rows += 1
            count = data["due_reviews"]
            layout.addLayout(self._need_row(
                "Start review", f"{count} review{'s' if count != 1 else ''} due", "spaced repetition",
                lambda: self._go("review")))
        event = next_event(now)
        if event:
            rows += 1
            start, title = event
            minutes = max(0, int((start - now).total_seconds() // 60))
            when = f"in {format_minutes(minutes)}" if minutes < 180 else start.strftime("%a %I:%M %p").replace(" 0", " ")
            layout.addLayout(self._need_row(None, title, when, None))
        if not rows:
            layout.addWidget(_text("You're all caught up."))
        layout.addStretch(1)
        return card

    def _need_row(self, action, title, caption, on_click):
        line = QHBoxLayout()
        line.setSpacing(10)
        texts = QVBoxLayout()
        texts.setSpacing(0)
        name = ElidedLabel(title)
        name.setStyleSheet(f"color:{INK};font-size:16px;font-weight:600;background:transparent;")
        texts.addWidget(name)
        texts.addWidget(_text(caption, 13))
        line.addLayout(texts, 1)
        if action:
            go = button(action, SECONDARY_STYLE, height=32)
            go.clicked.connect(on_click)
            line.addWidget(go)
        return line

    def _capture_card(self):
        card = _card()
        layout = QVBoxLayout(card)
        layout.setContentsMargins(22, 18, 22, 18)
        layout.setSpacing(10)
        layout.addWidget(_heading("Jot something down"))
        edit = WrapEdit(boxed=False, max_rows=3)
        edit.setPlaceholderText(_NOTE_HINT)
        edit.setStyleSheet(
            f"QPlainTextEdit#WrapEdit{{background:#F5F6F9;color:{INK};border:1px solid #E7EAF0;"
            "border-radius:10px;padding:4px 8px;font-size:14px;}"
            "QPlainTextEdit#WrapEdit:focus{border:1px solid #5B8DEF;}")
        edit.returnPressed.connect(lambda: self._capture_note(edit))
        layout.addWidget(edit)
        board = board_store.list_active_tasks()[:3]
        if board:
            layout.addSpacing(4)
            layout.addWidget(_heading("Top of the board"))
            for item in board:
                row = QPushButton()
                row.setFlat(True)
                row.setCursor(Qt.PointingHandCursor)
                row.setStyleSheet("QPushButton{background:transparent;border:none;text-align:left;padding:2px 0;}")
                inner = QHBoxLayout(row)
                inner.setContentsMargins(0, 0, 0, 0)
                inner.setSpacing(10)
                rank = QLabel(str(item.get("importance", "")))
                rank.setFixedWidth(22)
                rank.setAlignment(Qt.AlignCenter)
                rank.setAttribute(Qt.WA_TransparentForMouseEvents)
                rank.setStyleSheet(f"color:{INK};background:#EEF1F6;border-radius:6px;font-size:12px;font-weight:700;")
                inner.addWidget(rank)
                name = ElidedLabel(item.get("name", "Task"))
                name.setAttribute(Qt.WA_TransparentForMouseEvents)
                name.setStyleSheet(f"color:{INK};font-size:13.5px;background:transparent;")
                inner.addWidget(name, 1)
                row.clicked.connect(lambda _c=False: self._go("board"))
                layout.addWidget(row)
        layout.addStretch(1)
        return card

    def _capture_note(self, edit):
        text = edit.text().strip()
        if not text:
            return
        items, _stamp = quicknote_store.load()
        quicknote_store.save([i for i in items if i.strip()] + [text])
        edit.setText("")
        edit.setPlaceholderText("Added to QuickNote")
        QTimer.singleShot(1800, lambda: edit.setPlaceholderText(_NOTE_HINT))

    def _consistency(self, data):
        card = _card()
        layout = QVBoxLayout(card)
        layout.setContentsMargins(22, 18, 22, 16)
        layout.setSpacing(8)
        streak = data["streak"]
        days = [(d, m) for col in data["grid"] for d, m in col if d is not None]
        active = sum(1 for _d, m in days if m >= 1)
        total = sum(m for _d, m in days)
        head = QHBoxLayout()
        head.addWidget(_text(
            f"{streak}-day streak" if streak else "No streak yet: one session today starts it", 17, INK, 700))
        head.addStretch(1)
        totals = QLabel(f"{active} active days  ·  {format_minutes(total)} this year")
        totals.setStyleSheet(f"color:{MUTED};font-size:13px;background:transparent;")
        head.addWidget(totals)
        layout.addLayout(head)
        grid = _Grid(data["grid"])
        layout.addWidget(grid)
        readout = _text(" ", 13, MUTED)
        readout.setMinimumHeight(20)
        grid.hovered.connect(lambda text: readout.setText(text or " "))
        layout.addWidget(readout)
        return card
