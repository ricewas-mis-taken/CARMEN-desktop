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
import math
import os
import random
import time
from datetime import date, datetime, timedelta

from PySide6.QtCore import QElapsedTimer, QPointF, QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QFontMetricsF, QLinearGradient, QPainter, QPainterPath, QPen, QRadialGradient
from PySide6.QtWidgets import (
    QFrame, QGridLayout, QHBoxLayout, QLabel, QPushButton, QScrollArea, QSizePolicy, QVBoxLayout, QWidget,
)

import board_store
import calendar_recurrence
import calendar_store
import quicknote_store
import theme
import review_store
import session_history
import session_manager
import tasks_store
from qt_ui.history_viewer import minutes_by_day
from qt_ui.mini_timer import describe as describe_session
from qt_ui.ui_kit import (
    BREAK_COLOR, ElidedLabel, Ring, button, format_minutes, safe_color, tint,
)
from qt_ui import inkwrite
from qt_ui.wrap_edit import WrapEdit

WIDE_FROM = 900          # page width where the two-column layout starts
COMPACT_BELOW = 640      # under this the big ring sits above its text instead of beside it
GRID_WEEKS = 53          # a whole year of days, like a contribution graph
TIMER_MS = 1000
_GRID_BLUE = "#7B5FD3"
_BOOTED = False          # the opening animation plays once per launch
_NOTE_HINT = "Add to QuickNote, then Enter"
INK = MUTED = FAINT = ACCENT = "#000000"      # set from the theme by _apply_theme()
_SKY = ("#2A2060", "#4C3A8C", "#7A62B4")
_CARD = _PRIMARY = SECONDARY_STYLE = ""


def _apply_theme():
    """The page wears the chosen theme: white glass cards with the theme's ink, accent for the
    highlights, and the theme's sky behind them."""
    global INK, MUTED, FAINT, ACCENT, _SKY, _CARD, _PRIMARY, SECONDARY_STYLE, _GRID_BLUE
    colours = theme.current_colours()
    INK, MUTED, FAINT, ACCENT = colours["ink"], colours["muted"], colours["faint"], colours["accent"]
    _GRID_BLUE = ACCENT
    _SKY = theme.current_sky()
    _CARD = ("QFrame#HomeCard{background:rgba(255,255,255,0.95);border:1px solid rgba(255,255,255,0.95);"
             "border-radius:18px;}")
    _PRIMARY = (
        f"QPushButton{{background:{INK};color:#FFFFFF;border:none;border-radius:12px;"
        "font-size:14px;font-weight:700;padding:0 22px;}"
        f"QPushButton:hover{{background:{tint_hex(INK, 0.85)};}}")
    SECONDARY_STYLE = (
        f"QPushButton{{background:{tint(ACCENT, 0.13)};color:{INK};border:none;"
        "border-radius:12px;font-size:13.5px;font-weight:600;padding:0 16px;}"
        f"QPushButton:hover{{background:{tint(ACCENT, 0.24)};}}")


def tint_hex(colour, keep):
    """`colour` lightened toward white (keep=1 leaves it as is)."""
    c = QColor(colour)
    mix = lambda v: int(v * keep + 255 * (1 - keep))
    return QColor(mix(c.red()), mix(c.green()), mix(c.blue())).name()


_apply_theme_pending = True


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


def grid_cells(sessions, today, weeks=GRID_WEEKS, live_minutes=0.0):
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
            minutes = by_day.get(day, 0.0) + (live_minutes if day == today else 0.0)
            column.append((day, minutes) if day <= today else (None, 0.0))
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


def _with_live(minutes, live_minutes):
    """The running session counts toward today at once, so the streak lights up while you work."""
    if not minutes or live_minutes <= 0:
        return minutes
    day, value = minutes[-1]
    return minutes[:-1] + [(day, value + live_minutes)]


MILESTONES = (3, 7, 14, 30, 60, 100, 200, 365)


def next_milestone(streak):
    return next((m for m in MILESTONES if m > streak), streak + 100)


_WELCOME = {
    "night": ["Still up. Make it count, then rest.", "Quiet hours. One focused block, then sleep."],
    "morning": ["Fresh start. Pick one thing and begin.", "The day is empty. Fill it on purpose.",
                "Early focus is the cheapest focus."],
    "afternoon": ["Halfway there. Protect the next hour.", "Pick up where you left off.",
                  "A good afternoon starts with one session."],
    "evening": ["Wind down strong. One more block?", "Look how far today went. Finish it well.",
                "Evening focus counts the same."],
}


def welcome_line(now):
    hour = now.hour
    key = "night" if hour < 5 else "morning" if hour < 12 else "afternoon" if hour < 18 else "evening"
    lines = _WELCOME[key]
    return lines[now.timetuple().tm_yday % len(lines)]


def importance_colour(n):
    n = n or 0
    return "#5B8DEF" if n <= 3 else "#14B8A6" if n <= 5 else "#F5A524" if n <= 7 else "#F97316" if n <= 9 else "#E5484D"


def summary(tasks, sessions, due_reviews, today, live_minutes=0.0, status=None):
    """Everything Home shows, as plain data (so it can be tested without a window)."""
    open_tasks, done_tasks = [], []
    total_required = total_logged = 0.0
    for task in tasks:
        snap = tasks_store.goal_snapshot(task, today, sessions, live_status=status)
        if snap["required"] <= 0:
            continue
        required, logged, period_left = snap["required"], snap["logged"], None
        if snap["isPeriod"]:
            # A week / month goal is shown as what today has to bring in to stay on pace:
            # what is still missing at the start of today, spread over the days left.
            logged_today = min(logged, tasks_store.logged_seconds_for_date(task, today, sessions, live_status=status) / 60)
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
        "streak": streak_days(_with_live(minutes_by_day(sessions, 60, today=today), live_minutes)),
        "grid": grid_cells(sessions, today, live_minutes=live_minutes),
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
    """A card's title: centred and large, a rule under it, then the content."""
    box = QWidget()
    column = QVBoxLayout(box)
    column.setContentsMargins(0, 0, 0, 4)
    column.setSpacing(8)
    label = QLabel(text)
    label.setAlignment(Qt.AlignCenter)
    label.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)      # shrinks with the card, never widens the page
    label.setMinimumHeight(28)
    label.setStyleSheet(f"color:{INK};font-size:19px;font-weight:700;background:transparent;")
    column.addWidget(label)
    rule = QFrame()
    rule.setFixedHeight(2)
    rule.setStyleSheet(f"background:{tint(ACCENT, 0.35)};border:none;border-radius:1px;")
    column.addWidget(rule)
    return box


def _text(text, size=13, colour=None, weight=400):
    label = ElidedLabel(text)
    colour = colour or MUTED
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
    MAX_STEP, LEFT, TOP = 26, 30, 20

    def __init__(self, columns):
        super().__init__()
        self._columns = columns
        self._peak = max([m for col in columns for _d, m in col] + [1])
        self._hover = None
        self._wave = 1.0
        self._anim = None
        self.setMouseTracking(True)
        self.setMinimumWidth(0)
        self.setFixedHeight(self.TOP + 7 * 14)

    replay_on_enter = True

    def replay(self):
        """The year fills in as a wave from the left, the way a contribution graph loads."""
        from qt_ui import motion
        motion.stop(self._anim)
        self._anim = motion.tween(self, 0.0, 1.0, 1100, self._set_wave)

    def _set_wave(self, value):
        self._wave = value
        self.update()

    def _step(self):
        return max(4.0, min(float(self.MAX_STEP), (self.width() - self.LEFT) / len(self._columns)))

    def resizeEvent(self, event):
        self.setFixedHeight(int(self.TOP + 7 * self._step()) + 2)
        self.updateGeometry()
        super().resizeEvent(event)

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
        count = max(1, len(self._columns))
        for x, column in enumerate(self._columns):
            p.setOpacity(max(0.0, min(1.0, (self._wave * 1.5 - x / count) * 6)))
            for y, (day, minutes) in enumerate(column):
                if day is None:
                    continue
                colour = QColor(ACCENT) if minutes >= 1 else QColor(ACCENT)
                colour.setAlphaF(0.28 + 0.72 * min(1.0, minutes / self._peak) if minutes >= 1 else 0.10)
                rect = QRectF(self.LEFT + x * step, self.TOP + y * step, side, side)
                p.setPen(Qt.NoPen)
                p.setBrush(colour)
                p.drawRoundedRect(rect, radius, radius)
                if day == today or (x, y) == self._hover:
                    p.setBrush(Qt.NoBrush)
                    p.setPen(QColor(INK))
                    p.drawRoundedRect(rect.adjusted(0.5, 0.5, -0.5, -0.5), radius, radius)


_PURPLE_TOP, _PURPLE_BOTTOM = "#35296B", "#7F66AE"
_BANNER_FONT = ("Bahnschrift Light", "Bahnschrift", "Segoe UI Light", "Segoe UI")


def _banner_font(pixels, spacing, weight=QFont.Light):
    font = QFont()
    font.setFamilies(list(_BANNER_FONT))
    font.setPixelSize(pixels)
    font.setWeight(weight)
    font.setLetterSpacing(QFont.AbsoluteSpacing, spacing)
    return font


_MOTES = [(random.Random(11 + i).random(), random.Random(97 + i).random(), 0.012 + 0.03 * random.Random(5 + i).random(),
           1.2 + 3.0 * random.Random(41 + i).random(), random.Random(73 + i).random() * 6.28) for i in range(46)]


def _paint_sky(p, rect, drift=None):
    """Dusk purple with slow light and rising motes of dust, so the page is never still."""
    t = time.monotonic() if drift is None else drift
    gradient = QLinearGradient(0, rect.top(), 0, rect.bottom())
    gradient.setColorAt(0, QColor(_SKY[0]))
    gradient.setColorAt(0.55, QColor(_SKY[1]))
    gradient.setColorAt(1, QColor(_SKY[2]))
    p.fillRect(rect, gradient)
    w, h = rect.width(), rect.height()
    reach = max(w, h)
    for k, (bx, by, radius, alpha, colour) in enumerate((
            (0.2, 0.18, 0.62, 80, (200, 170, 255)), (0.85, 0.7, 0.7, 70, (150, 120, 255)),
            (0.5, 1.05, 0.55, 60, (255, 170, 220)))):
        cx = rect.left() + w * (bx + 0.08 * math.sin(t * 0.11 + k * 2.1))
        cy = rect.top() + h * (by + 0.07 * math.cos(t * 0.09 + k * 1.3))
        glow = QRadialGradient(QPointF(cx, cy), reach * radius)
        glow.setColorAt(0, QColor(colour[0], colour[1], colour[2], alpha))
        glow.setColorAt(1, QColor(colour[0], colour[1], colour[2], 0))
        p.fillRect(rect, glow)
    p.setPen(Qt.NoPen)
    for bx, by, speed, size, phase in _MOTES:
        x = rect.left() + (bx + 0.02 * math.sin(t * 0.3 + phase)) * w
        y = rect.top() + ((by - t * speed) % 1.0) * h
        twinkle = 0.35 + 0.35 * math.sin(t * 1.3 + phase)
        p.setBrush(QColor(255, 255, 255, int(255 * twinkle * 0.55)))
        p.drawEllipse(QPointF(x, y), size * 0.5, size * 0.5)


class _SkyBody(QWidget):
    """The page itself: the animated dusk behind every card."""

    def paintEvent(self, _event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        _paint_sky(p, QRectF(self.rect()))


def _paint_spaced(p, x, baseline, text, font, colour, reveal=1.0, rise=14):
    """Draws `text` one letter at a time; `reveal` (0..1) fades each letter in left to right."""
    metrics = QFontMetricsF(font)
    spacing = font.letterSpacing()
    count = max(1, len(text))
    p.setFont(font)
    for i, ch in enumerate(text):
        amount = max(0.0, min(1.0, reveal * (count + 3) - i - 1)) if reveal < 1 else 1.0
        if amount > 0:
            c = QColor(colour)
            c.setAlphaF(c.alphaF() * amount)
            p.setPen(c)
            p.drawText(QPointF(x, baseline + (1 - amount) * rise), ch)
        x += metrics.horizontalAdvance(ch) + spacing
    return x


def _spaced_width(text, font):
    metrics = QFontMetricsF(font)
    return sum(metrics.horizontalAdvance(ch) + font.letterSpacing() for ch in text)


class _Banner(QWidget):
    """The welcome, straight on the page: the weekday written large and centred, the date and
    time under it, and a line to start the day with."""

    def __init__(self, now, reveal=1.0):
        super().__init__()
        self._now = now
        self.reveal = reveal
        self.setFixedHeight(300)
        self.setMinimumWidth(0)
        self._tick = QTimer(self)
        self._tick.setInterval(20000)
        self._tick.timeout.connect(self._clock)
        self._tick.start()

    def _clock(self):
        self._now = datetime.now()
        self.update()

    def paintEvent(self, _event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        white = QColor("#FFFFFF")
        now = self._now
        size = 96
        text = now.strftime("%A").upper()
        lettering = inkwrite.Lettering(text, inkwrite.make_font(size, size * 0.34))
        while lettering.width > self.width() - 60 and size > 28:
            size -= 4
            lettering = inkwrite.Lettering(text, inkwrite.make_font(size, size * 0.34))
        top = 30
        inkwrite.draw(p, lettering, (self.width() - lettering.width) / 2, top + lettering.ascent, white,
                      self.reveal, stroke=max(1.4, size / 40))

        def centred(line, baseline, font, amount):
            width = _spaced_width(line, font)
            _paint_spaced(p, (self.width() - width) / 2, baseline, line, font, white, amount, rise=8)

        late = max(0.0, min(1.0, (self.reveal - 0.5) / 0.5)) if self.reveal < 1 else 1.0
        base = top + lettering.height + 8
        centred(f"{now.day}  {now.strftime('%B').upper()},  {now.year}.", base + 34,
                _banner_font(28, 5, QFont.DemiBold), late)
        centred(f"- {now.strftime('%I:%M %p').lstrip('0')} -", base + 82,
                _banner_font(26, 4, QFont.DemiBold), late)
        soft = QColor("#E6DCFF")
        soft.setAlphaF(0.92 * max(0.0, min(1.0, (self.reveal - 0.75) / 0.25)) if self.reveal < 1 else 0.92)
        p.setPen(soft)
        line_font = QFont()
        line_font.setPixelSize(20)
        line_font.setWeight(QFont.Medium)
        p.setFont(line_font)
        p.drawText(QRectF(20, self.height() - 48, self.width() - 40, 34), Qt.AlignCenter, welcome_line(now))


class _Boot(QWidget):
    """The opening, about ten seconds: the dusk fades up, CARMEN is traced by a glowing pen and
    then filled, a rule opens beneath, FOCUS and a promise arrive, the cover lifts and the weekday
    writes itself on the page. A click or a key skips to the lift."""
    LIFT_AT, DURATION = 9.2, 10.4

    def __init__(self, parent, on_progress, on_done):
        super().__init__(parent)
        self._on_progress, self._on_done = on_progress, on_done
        self._skipped_to = 0.0
        self._clock = QElapsedTimer()
        self._clock.start()
        self._timer = QTimer(self)
        self._timer.setInterval(16)
        self._timer.timeout.connect(self._step)
        self._timer.start()
        self._cache = None
        self.raise_()

    def _t(self):
        return self._clock.elapsed() / 1000.0 + self._skipped_to

    def mousePressEvent(self, event):
        self._skipped_to = max(self._skipped_to, self.LIFT_AT - self._clock.elapsed() / 1000.0)

    def _step(self):
        t = self._t()
        self._on_progress(max(0.0, min(1.0, (t - self.LIFT_AT) / 1.1)))
        if t >= self.DURATION:
            self._timer.stop()
            self._on_done()
            self.deleteLater()
            return
        self.update()

    @staticmethod
    def _ramp(t, start, end):
        return max(0.0, min(1.0, (t - start) / (end - start)))

    def paintEvent(self, _event):
        t = self._t()
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        lift = self._ramp(t, self.LIFT_AT, self.DURATION)
        p.setOpacity(1.0 - lift ** 2)
        p.translate(0, -lift * 70)
        _paint_sky(p, QRectF(0, -70, self.width(), self.height() + 70))
        p.setOpacity(min(1.0, t / 0.8) * (1.0 - lift ** 2))
        white = QColor("#FFFFFF")
        size = max(60, min(190, self.width() // 6))
        if self._cache is None or self._cache[0] != size:
            self._cache = (size, inkwrite.Lettering("Carmen", inkwrite.make_font(size, 0, light=False, script=True)))
        lettering = self._cache[1]
        left = (self.width() - lettering.width) / 2
        baseline = self.height() / 2 + lettering.ascent * 0.3
        inkwrite.draw_signature(p, lettering, left, baseline, white, self._ramp(t, 0.9, 6.6),
                                stroke=max(1.6, size / 50))
        width = lettering.width
        rule = self._ramp(t, 6.4, 7.4)
        if rule > 0:
            ease = 1 - (1 - rule) ** 3
            p.setPen(QPen(QColor(255, 255, 255, 150), 1.5))
            half = width * 0.5 * ease
            y = baseline + size * 0.34
            p.drawLine(QPointF(self.width() / 2 - half, y), QPointF(self.width() / 2 + half, y))
            focus = "F O C U S"
            font = _banner_font(max(14, size // 5), 7, QFont.DemiBold)
            fw = _spaced_width(focus, font)
            _paint_spaced(p, (self.width() - fw) / 2, y + size * 0.42, focus, font, white,
                          self._ramp(t, 7.0, 8.0), rise=10)
            tag = QColor("#E6DCFF")
            tag.setAlphaF(0.92 * self._ramp(t, 7.8, 8.6))
            p.setPen(tag)
            small = QFont()
            small.setPixelSize(15)
            p.setFont(small)
            p.drawText(QRectF(0, y + size * 0.62, self.width(), 30), Qt.AlignCenter, "Focus on what matters.")


class _Flame(QWidget):
    """A small painted fire: lit when there is a streak, grey when there is not."""

    def __init__(self, lit, size=46):
        super().__init__()
        self._lit = lit
        self.setFixedSize(size, size)

    def paintEvent(self, _event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        w, h = self.width(), self.height()
        outer = QPainterPath()
        outer.moveTo(w * 0.5, h * 0.04)
        outer.cubicTo(w * 0.62, h * 0.30, w * 0.92, h * 0.40, w * 0.86, h * 0.68)
        outer.cubicTo(w * 0.82, h * 0.92, w * 0.62, h * 0.98, w * 0.5, h * 0.98)
        outer.cubicTo(w * 0.34, h * 0.98, w * 0.14, h * 0.90, w * 0.14, h * 0.66)
        outer.cubicTo(w * 0.14, h * 0.46, w * 0.30, h * 0.40, w * 0.36, h * 0.24)
        outer.cubicTo(w * 0.42, h * 0.14, w * 0.46, h * 0.08, w * 0.5, h * 0.04)
        gradient = QLinearGradient(0, 0, 0, h)
        gradient.setColorAt(0, QColor("#FFB020" if self._lit else "#D5D9E2"))
        gradient.setColorAt(1, QColor("#F2542D" if self._lit else "#B8BECB"))
        p.setPen(Qt.NoPen)
        p.setBrush(gradient)
        p.drawPath(outer)
        inner = QPainterPath()
        inner.moveTo(w * 0.5, h * 0.50)
        inner.cubicTo(w * 0.62, h * 0.64, w * 0.70, h * 0.72, w * 0.62, h * 0.86)
        inner.cubicTo(w * 0.58, h * 0.94, w * 0.42, h * 0.94, w * 0.38, h * 0.84)
        inner.cubicTo(w * 0.32, h * 0.70, w * 0.44, h * 0.62, w * 0.5, h * 0.50)
        p.setBrush(QColor("#FFE08A" if self._lit else "#EEF0F5"))
        p.drawPath(inner)


class HomeTab(QWidget):
    def __init__(self, on_navigate=None):
        super().__init__()
        self._on_navigate = on_navigate or (lambda key: None)
        self._wide = None
        self._compact = None
        self._signature = None
        self._live = None            # (ring, colour) while a live session is on show
        self._reveal = 1.0           # how much of the banner lettering has appeared (the boot animation)
        self._boot = None
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setObjectName("HomeScroll")
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        outer.addWidget(scroll)
        self._body = _SkyBody()
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
        self._motion = QTimer(self)
        self._motion.setInterval(50)
        self._motion.timeout.connect(self._body.update)
        self.refresh()

    # a page that can shrink with its window
    def minimumSizeHint(self):
        return QSize(0, 0)

    def showEvent(self, event):
        global _BOOTED
        if not _BOOTED and "PYTEST_CURRENT_TEST" not in os.environ:
            _BOOTED = True
            self._reveal = 0.0
            self._boot = _Boot(self, self._set_reveal, self._boot_done)
            self._boot.setGeometry(self.rect())
            self._boot.show()
        self.refresh()
        self._timer.start()
        self._motion.start()
        super().showEvent(event)

    def _set_reveal(self, value):
        self._reveal = value
        if self._blocks and isinstance(self._blocks[0], _Banner):
            self._blocks[0].reveal = value
            self._blocks[0].update()

    def _boot_done(self):
        self._boot = None
        self._set_reveal(1.0)

    def hideEvent(self, event):
        self._timer.stop()
        self._motion.stop()
        super().hideEvent(event)

    def resizeEvent(self, event):
        if self._boot is not None:
            self._boot.setGeometry(self.rect())
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
        self._ticks = getattr(self, "_ticks", 0) + 1
        if self._signature_of(status) != self._signature or (status.get("isActive") and self._ticks % 10 == 0 and not self._typing()):
            self.refresh()
            return
        if self._live is not None and status.get("isActive"):
            ring, colour = self._live
            _label, clock, fraction, _can_pause = describe_session(status)
            paused = bool(status.get("isPaused"))
            ring.set_state(colour, fraction or 0.0, clock, "paused" if paused else "", dim=paused)

    def _typing(self):
        widget = self.focusWidget()
        return isinstance(widget, WrapEdit) and widget.hasFocus()

    def refresh(self, width=None):
        _apply_theme()
        width = self.width() if width is None else width
        self._compact = width < COMPACT_BELOW
        for block in self._blocks:
            self._grid.removeWidget(block)
            block.hide()          # gone from view at once; deleteLater only runs on the next loop turn
            block.deleteLater()
        now = datetime.now()
        status = session_manager.get_status()
        self._signature = self._signature_of(status)
        self._live = None
        live = 0.0
        if status.get("isActive") and status.get("startTime") and not status.get("isBreak"):
            live = tasks_store.worked_seconds(status["startTime"], None, status.get("violationLog")) / 60
        data = summary(tasks_store.load_tasks(), session_history.load_all(), due_review_count(), date.today(), live, status)
        self._blocks = [
            _Banner(now, self._reveal),
            self._hero(data, status),
            self._today(data),
            self._needs_you(data, status, now),
            self._consistency(data),
            self._streak_card(data),
            self._board_card(),
        ]
        self._place(width >= WIDE_FROM)

    def _place(self, wide):
        """Wide: banner on top; the live card beside the streak and notes box; today and needs-you
        under the live card, the board down the right; the year underneath. Narrow: one column."""
        self._wide = wide
        banner, hero, today, needs, consistency, streak, board = self._blocks
        for block in self._blocks:
            self._grid.removeWidget(block)
        for column in range(2):
            self._grid.setColumnStretch(column, 0)
        if wide:
            self._grid.addWidget(banner, 0, 0, 1, 2)
            self._grid.addWidget(hero, 1, 0)
            self._grid.addWidget(streak, 1, 1)
            self._grid.addWidget(today, 2, 0)
            self._grid.addWidget(board, 2, 1, 2, 1)
            self._grid.addWidget(needs, 3, 0)
            self._grid.addWidget(consistency, 4, 0, 1, 2)
            self._grid.setColumnStretch(0, 3)
            self._grid.setColumnStretch(1, 2)
        else:
            for row, block in enumerate((banner, hero, streak, today, needs, board, consistency)):
                self._grid.addWidget(block, row, 0)
            self._grid.setColumnStretch(0, 1)
        self._grid.setRowStretch(8, 1)
        for block in self._blocks:
            block.show()

    # ---- blocks ----------------------------------------------------------------------

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
            ring.set_text_colors(INK, MUTED, FAINT)
            ring.set_state(colour, fraction or 0.0, clock, "paused" if paused else "", dim=paused)
            ring.set_live(not paused)
            self._live = (ring, colour)
            row.addWidget(ring, 0, Qt.AlignLeft if self._compact else Qt.AlignVCenter)
            text.addStretch(1)
            title = QHBoxLayout()
            title.setSpacing(10)
            title.addWidget(self._big(label), 1)
            pill = QLabel("Paused" if paused else "Live")
            pill.setStyleSheet(
                f"color:{'#FFFFFF'};background:{FAINT if paused else '#2FB67C'};border-radius:11px;"
                "padding:3px 12px;font-size:12.5px;font-weight:700;")
            title.addWidget(pill, 0, Qt.AlignVCenter)
            text.addLayout(title)
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
            ring.set_text_colors(INK, MUTED, FAINT)
            ring.set_state(
                safe_color(top["task"].get("color")), top["logged"] / top["required"],
                format_minutes(top["left"]), "left")
            row.addWidget(ring, 0, Qt.AlignLeft if self._compact else Qt.AlignVCenter)
            text.addStretch(1)
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

    def _board_card(self):
        card = _card()
        layout = QVBoxLayout(card)
        layout.setContentsMargins(22, 18, 22, 18)
        layout.setSpacing(8)
        layout.addWidget(_heading("Top of the board"))
        board = board_store.list_active_tasks()[:4]
        if not board:
            layout.addWidget(_text("Nothing on the board yet.", 15))
        for item in board:
            colour = importance_colour(item.get("importance"))
            row = QPushButton()
            row.setCursor(Qt.PointingHandCursor)
            row.setFixedHeight(52)
            row.setStyleSheet(
                f"QPushButton{{background:{tint(colour, 0.13)};border:none;border-left:6px solid {colour};"
                f"border-radius:12px;text-align:left;}}QPushButton:hover{{background:{tint(colour, 0.24)};}}")
            inner = QHBoxLayout(row)
            inner.setContentsMargins(16, 0, 14, 0)
            inner.setSpacing(10)
            name = ElidedLabel(item.get("name", "Task"))
            name.setAttribute(Qt.WA_TransparentForMouseEvents)
            name.setStyleSheet(f"color:{INK};font-size:17px;font-weight:600;background:transparent;")
            inner.addWidget(name, 1)
            rank = QLabel(str(item.get("importance", "")))
            rank.setFixedSize(32, 26)
            rank.setAlignment(Qt.AlignCenter)
            rank.setAttribute(Qt.WA_TransparentForMouseEvents)
            rank.setStyleSheet(f"color:#FFFFFF;background:{colour};border-radius:13px;font-size:13px;font-weight:700;")
            inner.addWidget(rank)
            row.clicked.connect(lambda _c=False: self._go("board"))
            layout.addWidget(row)
        layout.addStretch(1)
        return card

    def _streak_card(self, data):
        """The streak, and under it the quick-capture line, in one box."""
        card = _card()
        outer = QVBoxLayout(card)
        outer.setContentsMargins(22, 18, 22, 18)
        outer.setSpacing(12)
        row = QHBoxLayout()
        row.setSpacing(16)
        streak = data["streak"]
        row.addWidget(_Flame(streak > 0, 60), 0, Qt.AlignVCenter)
        col = QVBoxLayout()
        col.setSpacing(2)
        big = QLabel(str(streak))
        big.setStyleSheet(f"color:{INK};font-size:44px;font-weight:700;background:transparent;")
        col.addWidget(big)
        col.addWidget(_text("day streak", 17, MUTED, 600))
        goal = next_milestone(streak)
        col.addSpacing(4)
        col.addWidget(_Bar(streak / goal, "#F2842D", height=10))
        col.addWidget(_text(f"{goal - streak} more to reach {goal}" if streak else "Focus today to light it", 14, FAINT))
        row.addLayout(col, 1)
        outer.addLayout(row)
        outer.addWidget(_heading("Jot something down"))
        edit = WrapEdit(boxed=False, max_rows=3)
        edit.setPlaceholderText(_NOTE_HINT)
        edit.setStyleSheet(
            f"QPlainTextEdit#WrapEdit{{background:{tint(ACCENT, 0.07)};color:{INK};border:1px solid {tint(ACCENT, 0.25)};"
            "border-radius:10px;padding:4px 8px;font-size:16px;}"
            f"QPlainTextEdit#WrapEdit:focus{{border:1px solid {ACCENT};}}")
        edit.returnPressed.connect(lambda: self._capture_note(edit))
        outer.addWidget(edit)
        return card

    def _capture_note(self, edit):
        text = edit.text().strip()
        if not text:
            return
        items, _stamp = quicknote_store.load()
        quicknote_store.save([i for i in items if i.strip()] + [text])
        edit.setText("")
        self.refresh()          # the QuickNote list on the board card shows it at once

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
        for text, size, colour, weight, align in (
                (f"{streak}-day streak" if streak else "No streak yet: one session today starts it", 17, INK, 700, Qt.AlignLeft),
                (f"{active} active days  ·  {format_minutes(total)} this year", 13, MUTED, 400, Qt.AlignRight)):
            label = QLabel(text)
            label.setAlignment(align | Qt.AlignVCenter)
            label.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)     # shrinks with the window
            label.setStyleSheet(f"color:{colour};font-size:{size}px;font-weight:{weight};background:transparent;")
            head.addWidget(label, 1)
        layout.addLayout(head)
        grid = _Grid(data["grid"])
        layout.addWidget(grid)
        readout = _text(" ", 13, MUTED)
        readout.setMinimumHeight(20)
        grid.hovered.connect(lambda text: readout.setText(text or " "))
        layout.addWidget(readout)
        return card


_apply_theme()
