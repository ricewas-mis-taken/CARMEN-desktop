"""The sidebar's Session History page, and the text formatting helpers for one completed
session.

The page reads private/session_history.json through session_history.load_all() -- the
file is written by session_manager exactly as before -- and shows it as a summary, a
fortnight strip of focus time, and a timeline of day-by-day session cards that open to
their details (rules, pauses, violations, exceptions).

format_session_html(session) stays a standalone function returning an HTML fragment so
other code can reuse the old plain-text style description of one session.
"""
import html
from datetime import date, datetime, timedelta

from PySide6.QtCore import QRectF, Qt
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

import session_history
import session_manager
import tasks_store
from qt_ui import motion
from qt_ui.ui_kit import (
    AMBER,
    FAINT,
    GREEN,
    GREY,
    INK,
    MUTED,
    NUM_CSS,
    RED,
    SECONDARY_STYLE,
    TEAL,
    ElidedLabel,
    button,
    chip,
    format_minutes,
    safe_color,
)

SEPARATOR = "─" * 72

DAY_WINDOWS = (("7 days", 7), ("30 days", 30), ("All time", None))
KIND_FILTERS = (("Everything", None), ("Tasks", "task"), ("Reviews", "review"), ("Manual", "manual"))
PAGE_SIZE = 40          # sessions drawn at once; "Show more" adds this many
STRIP_DAYS = 14
_TONE_BY_KIND = {"task": None, "review": ("#E4ECFB", "#2F4D9A"), "manual": GREY, "calendar": TEAL}
_PILL_STYLE = (
    f"QPushButton{{background:#FFFFFF;color:{INK};border:1px solid #E1E5EC;border-radius:13px;"
    "font-size:12.5px;font-weight:600;padding:0 14px;}"
    "QPushButton:hover{background:#F6F7FA;}"
    f"QPushButton:checked{{background:{INK};color:#FFFFFF;border:1px solid {INK};}}")


# --- reading the history file (pure: no widgets) -----------------------------------------

def session_kind(session):
    """"task", "review", "calendar" or "manual"."""
    source = session.get("source")
    if source == "review" or session.get("reviewProblemName"):
        return "review"
    if source == "task":
        return "task"
    if source == "calendar-event":
        return "calendar"
    return "manual"


def session_title(session):
    if session_kind(session) == "review":
        if session_manager.is_first_attempt(session):
            return "First attempt"
        return f"Review: {session.get('reviewProblemName') or '?'}"
    return session.get("eventTitle") or "Focus session"


def session_color(session):
    task = tasks_store.get_task(session["eventId"]) if session.get("eventId") else None
    return safe_color(task.get("color")) if task else "#98A2B3"


def violation_count(session):
    log = session.get("violationLog") or []
    return session.get("violationCount", sum(1 for e in log if e.get("kind") in ("process", "domain")))


def worked_seconds(session):
    """Time actually worked: start to end, minus pauses (and so minus pomodoro breaks)."""
    start, end = _parse(session.get("startTime")), _parse(session.get("endTime"))
    if not start or not end:
        return 0
    return tasks_store.worked_seconds(session["startTime"], session["endTime"], session.get("violationLog"))


def summarize(sessions):
    """Totals for the strip at the top: focused seconds, session count, violations, clean sessions."""
    total = sum(worked_seconds(s) for s in sessions)
    violations = sum(violation_count(s) for s in sessions)
    clean = sum(1 for s in sessions if violation_count(s) == 0)
    return {"seconds": total, "sessions": len(sessions), "violations": violations, "clean": clean}


def minutes_by_day(sessions, days=STRIP_DAYS, today=None):
    """[(date, minutes)] for the last `days` days ending today, oldest first."""
    today = today or date.today()
    totals = {today - timedelta(days=i): 0.0 for i in range(days)}
    for s in sessions:
        start = _parse(s.get("startTime"))
        if start and start.date() in totals:
            totals[start.date()] += worked_seconds(s) / 60
    return sorted(totals.items())


def group_by_day(sessions):
    """[(date, [sessions newest first])], newest day first. `sessions` is oldest first, as stored."""
    days = {}
    for s in reversed(sessions):
        start = _parse(s.get("startTime"))
        days.setdefault(start.date() if start else date.min, []).append(s)
    return sorted(days.items(), key=lambda kv: kv[0], reverse=True)


def day_label(day, today=None):
    today = today or date.today()
    if day == today:
        return "Today"
    if day == today - timedelta(days=1):
        return "Yesterday"
    if day == date.min:
        return "Unknown date"
    return f"{day.strftime('%A')}, {day.strftime('%b')} {day.day}"


def _duration_text(seconds):
    return format_minutes(seconds / 60) if seconds >= 60 else f"{int(seconds)}s"


def _time_range(session):
    start, end = _parse(session.get("startTime")), _parse(session.get("endTime"))
    if not start:
        return "?"
    text = start.strftime("%H:%M")
    return f"{text} – {end.strftime('%H:%M')}" if end else text


# --- widgets ------------------------------------------------------------------------------

def _soft_card(frame, blur=22, alpha=16):
    shadow = QGraphicsDropShadowEffect(frame)
    shadow.setBlurRadius(blur)
    shadow.setOffset(0, 5)
    shadow.setColor(QColor(16, 24, 40, alpha))
    frame.setGraphicsEffect(shadow)


class _Stat(QFrame):
    def __init__(self, caption):
        super().__init__()
        self.setObjectName("HistStat")
        self.setStyleSheet("QFrame#HistStat{background:#FFFFFF;border:1px solid #E6E9EF;border-radius:16px;}")
        _soft_card(self, 18, 12)
        box = QVBoxLayout(self)
        box.setContentsMargins(18, 14, 18, 14)
        box.setSpacing(0)
        self.value = QLabel("0")
        self.value.setStyleSheet(f"font-size:32px;{NUM_CSS}color:{INK};background:transparent;")
        box.addWidget(self.value)
        self.caption = QLabel(caption)
        self.caption.setStyleSheet(f"font-size:12.5px;color:{MUTED};background:transparent;")
        box.addWidget(self.caption)


class _ActivityStrip(QWidget):
    """The last two weeks as bars, one per day, as tall as that day's focus time."""

    def __init__(self):
        super().__init__()
        self._days = []
        self.setFixedHeight(76)
        self.setMinimumWidth(240)

    def set_days(self, days):
        self._days = days
        peak = max((m for _d, m in days), default=0)
        self.setToolTip("\n".join(f"{d.strftime('%a %b')} {d.day}: {format_minutes(m)}" for d, m in days if m) or
                        "No focus time in the last two weeks")
        self._peak = peak
        self.update()

    def paintEvent(self, _event):
        if not self._days:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(Qt.NoPen)
        count = len(self._days)
        gap = 6
        bar_w = max(6.0, (self.width() - gap * (count - 1)) / count)
        chart_h = self.height() - 18
        peak = max(self._peak, 1)
        today = date.today()
        font = p.font()
        font.setPixelSize(10)
        p.setFont(font)
        for i, (day, minutes) in enumerate(self._days):
            x = i * (bar_w + gap)
            h = max(4.0, chart_h * minutes / peak) if minutes else 4.0
            color = QColor(INK) if day == today else QColor("#9AA6BF" if minutes else "#E4E7EC")
            p.setBrush(color)
            p.drawRoundedRect(QRectF(x, chart_h - h, bar_w, h), 4, 4)
            p.setPen(QColor(FAINT))
            p.drawText(QRectF(x - 4, chart_h + 2, bar_w + 8, 14), Qt.AlignCenter, day.strftime("%a")[0])
            p.setPen(Qt.NoPen)


class _SessionCard(QFrame):
    """One finished session. Click it (or the arrow) to open the details."""

    def __init__(self, session):
        super().__init__()
        self.session = session
        self._details = None
        self.setObjectName("HistCard")
        self.setStyleSheet(
            "QFrame#HistCard{background:#FFFFFF;border:1px solid #E6E9EF;border-radius:14px;}"
            "QFrame#HistCard:hover{border:1px solid #C9CED8;}")
        _soft_card(self, 16, 12)
        self.setCursor(Qt.PointingHandCursor)

        kind = session_kind(session)
        end_type = session.get("endType", "manual")
        self._outer = QVBoxLayout(self)
        self._outer.setContentsMargins(16, 12, 16, 12)
        self._outer.setSpacing(8)

        row = QHBoxLayout()
        row.setSpacing(14)
        dot = QFrame()
        dot.setFixedSize(10, 10)
        dot.setStyleSheet(f"background:{session_color(session)};border-radius:5px;border:none;")
        row.addWidget(dot, 0, Qt.AlignVCenter)

        text = QVBoxLayout()
        text.setSpacing(2)
        self.title_label = ElidedLabel(session_title(session))
        self.title_label.setStyleSheet(f"font-size:14.5px;font-weight:700;color:{INK};background:transparent;")
        text.addWidget(self.title_label)
        self.time_label = QLabel(_time_range(session))
        self.time_label.setStyleSheet(f"font-size:12.5px;color:{MUTED};background:transparent;")
        text.addWidget(self.time_label)
        row.addLayout(text, 1)

        chips = QHBoxLayout()
        chips.setSpacing(6)
        if kind == "review":
            chips.addWidget(chip("Review", tone=_TONE_BY_KIND["review"]))
        elif kind == "task":
            chips.addWidget(chip("Task", session_color(session)))
        elif kind == "calendar":
            chips.addWidget(chip("Calendar", tone=TEAL))
        pomodoro = session.get("pomodoro")
        if pomodoro:
            chips.addWidget(chip(f"Pomodoro {pomodoro.get('currentCycle')}/{pomodoro.get('totalCycles')}",
                                 "#6C3FA8"))
        chips.addWidget(chip("Hard lock" if session.get("lockMode") == "hard" else "Soft lock"))
        if end_type == "nuclear":
            chips.addWidget(chip("Ended early", tone=RED))
        count = violation_count(session)
        self.violation_chip = chip("Clean" if count == 0 else f"{count} violation{'s' if count != 1 else ''}",
                                   tone=GREEN if count == 0 else AMBER if count < 4 else RED)
        chips.addWidget(self.violation_chip)
        row.addLayout(chips)

        self.duration_label = QLabel(_duration_text(worked_seconds(session)))
        self.duration_label.setMinimumWidth(78)
        self.duration_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self.duration_label.setStyleSheet(f"font-size:20px;{NUM_CSS}color:{INK};background:transparent;")
        row.addWidget(self.duration_label)
        self.arrow = QLabel("▸")
        self.arrow.setStyleSheet(f"font-size:12px;color:{FAINT};background:transparent;")
        row.addWidget(self.arrow)
        self._outer.addLayout(row)

    @property
    def expanded(self):
        return self._details is not None

    def toggle(self):
        if self._details is not None:
            self._details.deleteLater()
            self._details = None
            self.arrow.setText("▸")
            return
        self._details = self._build_details()
        self._outer.addWidget(self._details)
        self.arrow.setText("▾")

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.toggle()
        super().mousePressEvent(event)

    def _build_details(self):
        s = self.session
        panel = QFrame()
        panel.setStyleSheet("QFrame{background:#F7F8FA;border-radius:10px;}")
        box = QVBoxLayout(panel)
        box.setContentsMargins(14, 10, 14, 10)
        box.setSpacing(6)

        def line(text_html):
            label = QLabel(text_html)
            label.setWordWrap(True)
            label.setTextFormat(Qt.RichText)
            label.setStyleSheet(f"font-size:12.5px;color:{MUTED};background:transparent;")
            box.addWidget(label)
            return label

        label = _format_source_label(s)
        if label:
            line(f'<b style="color:{INK};">{_esc(label)}</b>')
        pomodoro = _format_pomodoro_label(s)
        if pomodoro:
            line(_esc(pomodoro))
        if s.get("endType") == "nuclear":
            line(f'<span style="color:#B42318;">Ended early. Reason: {_esc(s.get("reason") or "none given")}</span>')

        apps = s.get("processBlocklist") or []
        sites = s.get("domainWhitelist") or []
        line(f'<b style="color:{INK};">Blocked apps</b> ({len(apps)}): {_esc(", ".join(apps) or "none")}')
        line(f'<b style="color:{INK};">Allowed sites</b> ({len(sites)}): {_esc(", ".join(sites) or "none")}')

        log = s.get("violationLog") or []
        pauses = sum(1 for e in log if e.get("kind") == "pause")
        if pauses:
            line(f'Paused {pauses} time{"s" if pauses != 1 else ""}')
        problems = [e for e in log if e.get("kind") not in ("pause", "resume")]
        if problems:
            line(f'<b style="color:{INK};">Violations</b>')
        for entry in problems:
            text, color = _format_violation(entry)
            line(f'<span style="color:{color};">&nbsp;&nbsp;{_esc(text)}</span>')

        exceptions = s.get("processBlocklistExceptions") or []
        additions = s.get("domainWhitelistAdditions") or []
        if exceptions or additions:
            line(f'<b style="color:{INK};">Let through mid-session</b>')
            for entry in exceptions:
                line(f'&nbsp;&nbsp;{_esc(_format_exception("app unblocked", entry, "process"))}')
            for entry in additions:
                line(f'&nbsp;&nbsp;{_esc(_format_exception("site allowed", entry, "domain"))}')
        return panel


class HistoryTab(QWidget):
    """The sidebar's Session History page: a summary, a fortnight of focus time, and every
    completed session as a card, newest first. Re-read each time the tab is shown (and
    whenever sessions sync in)."""

    def __init__(self):
        super().__init__()
        self._window_days = 7
        self._kind = None
        self._shown = PAGE_SIZE
        self._cards = []
        self._day_label_widgets = []

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setStyleSheet("QScrollArea{background:transparent;}")
        outer.addWidget(scroll)
        self._scroll = scroll

        page = QWidget()
        page.setObjectName("HistPage")
        page.setStyleSheet("QWidget#HistPage{background:transparent;}")
        scroll.setWidget(page)
        layout = QVBoxLayout(page)
        layout.setContentsMargins(28, 24, 28, 28)
        layout.setSpacing(14)

        head = QHBoxLayout()
        title = QLabel("Session History")
        title.setStyleSheet(f"font-size:26px;font-weight:700;color:{INK};background:transparent;")
        head.addWidget(title)
        head.addStretch(1)
        layout.addLayout(head)

        # filters
        filters = QHBoxLayout()
        filters.setSpacing(6)
        self._window_buttons = {}
        window_group = QButtonGroup(self)
        window_group.setExclusive(True)
        for label, days in DAY_WINDOWS:
            pill = QPushButton(label)
            pill.setCheckable(True)
            pill.setCursor(Qt.PointingHandCursor)
            pill.setFixedHeight(28)
            pill.setStyleSheet(_PILL_STYLE)
            pill.clicked.connect(lambda _c=False, d=days: self._set_window(d))
            window_group.addButton(pill)
            self._window_buttons[days] = pill
            filters.addWidget(pill)
        self._window_buttons[7].setChecked(True)
        filters.addSpacing(14)
        self._kind_buttons = {}
        kind_group = QButtonGroup(self)
        kind_group.setExclusive(True)
        for label, kind in KIND_FILTERS:
            pill = QPushButton(label)
            pill.setCheckable(True)
            pill.setCursor(Qt.PointingHandCursor)
            pill.setFixedHeight(28)
            pill.setStyleSheet(_PILL_STYLE)
            pill.clicked.connect(lambda _c=False, k=kind: self._set_kind(k))
            kind_group.addButton(pill)
            self._kind_buttons[kind] = pill
            filters.addWidget(pill)
        self._kind_buttons[None].setChecked(True)
        filters.addStretch(1)
        layout.addLayout(filters)

        # summary + strip
        top = QHBoxLayout()
        top.setSpacing(12)
        self.stat_time = _Stat("focused")
        self.stat_sessions = _Stat("sessions")
        self.stat_violations = _Stat("violations")
        self.stat_clean = _Stat("clean sessions")
        for stat in (self.stat_time, self.stat_sessions, self.stat_violations, self.stat_clean):
            top.addWidget(stat, 1)
        layout.addLayout(top)

        strip_card = QFrame()
        strip_card.setObjectName("HistStrip")
        strip_card.setStyleSheet("QFrame#HistStrip{background:#FFFFFF;border:1px solid #E6E9EF;border-radius:16px;}")
        _soft_card(strip_card, 18, 12)
        strip_box = QVBoxLayout(strip_card)
        strip_box.setContentsMargins(18, 12, 18, 10)
        strip_box.setSpacing(6)
        strip_title = QLabel("Last two weeks")
        strip_title.setStyleSheet(f"font-size:12.5px;font-weight:700;color:{MUTED};background:transparent;")
        strip_box.addWidget(strip_title)
        self.strip = _ActivityStrip()
        strip_box.addWidget(self.strip)
        layout.addWidget(strip_card)

        # timeline
        self._timeline = QVBoxLayout()
        self._timeline.setSpacing(8)
        layout.addLayout(self._timeline)
        self._more_button = button("Show more", SECONDARY_STYLE, height=36)
        self._more_button.clicked.connect(self._show_more)
        layout.addWidget(self._more_button, 0, Qt.AlignHCenter)
        layout.addStretch(1)

        self.refresh()

    def showEvent(self, event):
        self.refresh()
        super().showEvent(event)

    def _set_window(self, days):
        self._window_days = days
        self._shown = PAGE_SIZE
        self.refresh()

    def _set_kind(self, kind):
        self._kind = kind
        self._shown = PAGE_SIZE
        self.refresh()

    def _show_more(self):
        self._shown += PAGE_SIZE
        self.refresh()

    def _filtered(self, everything):
        sessions = everything
        if self._window_days is not None:
            cutoff = date.today() - timedelta(days=self._window_days - 1)
            sessions = [s for s in sessions if (_parse(s.get("startTime")) or datetime.min).date() >= cutoff]
        if self._kind is not None:
            sessions = [s for s in sessions if session_kind(s) == self._kind or
                        (self._kind == "manual" and session_kind(s) == "calendar")]
        return sessions

    def refresh(self):
        everything = session_history.load_all()
        # The tab re-reads the log every time it is shown; laying the page out again is the
        # costly part, so only do it when a session was added or the view itself changed.
        last = everything[-1] if everything else {}
        signature = (len(everything), last.get("sessionId"), last.get("startTime"), last.get("endTime"),
                     self._window_days, self._kind, self._shown, date.today())
        if signature == getattr(self, "_signature", None):
            return
        self._signature = signature
        sessions = self._filtered(everything)
        scroll = self._scroll.verticalScrollBar().value()

        stats = summarize(sessions)
        self.stat_time.value.setText(_stat_time(stats["seconds"]))
        motion.count_up(self.stat_sessions.value, str(stats["sessions"]))
        motion.count_up(self.stat_violations.value, str(stats["violations"]))
        self.stat_clean.value.setText(
            f"{stats['clean']}" if not stats["sessions"] else f"{round(100 * stats['clean'] / stats['sessions'])}%")
        self.strip.set_days(minutes_by_day(everything))

        while self._timeline.count():
            widget = self._timeline.takeAt(0).widget()
            if widget is not None:
                widget.setParent(None)
                widget.deleteLater()
        self._cards = []
        self._day_label_widgets = []

        if not sessions:
            empty = QLabel("No completed sessions yet. This fills in once a session ends."
                           if not everything else "No sessions match these filters.")
            empty.setAlignment(Qt.AlignCenter)
            empty.setWordWrap(True)
            empty.setStyleSheet(
                f"color:{MUTED};font-size:14px;background:#FFFFFF;border:1px dashed #D0D5DD;"
                "border-radius:16px;padding:32px 24px;")
            self._timeline.addWidget(empty)
            self._more_button.hide()
            return

        drawn = 0
        for day, items in group_by_day(sessions):
            if drawn >= self._shown:
                break
            heading = QWidget()
            hrow = QHBoxLayout(heading)
            hrow.setContentsMargins(2, 8, 2, 0)
            name = QLabel(day_label(day))
            name.setStyleSheet(f"font-size:13px;font-weight:700;color:{INK};background:transparent;")
            hrow.addWidget(name)
            total = QLabel(f"{_duration_text(sum(worked_seconds(s) for s in items))} · "
                           f"{len(items)} session{'s' if len(items) != 1 else ''}")
            total.setStyleSheet(f"font-size:12.5px;color:{MUTED};background:transparent;")
            hrow.addWidget(total)
            hrow.addStretch(1)
            self._timeline.addWidget(heading)
            self._day_label_widgets.append(name)
            for session in items:
                if drawn >= self._shown:
                    break
                card = _SessionCard(session)
                self._cards.append(card)
                self._timeline.addWidget(card)
                drawn += 1
        self._more_button.setVisible(drawn < len(sessions))
        self._more_button.setText(f"Show more ({len(sessions) - drawn} older)")
        self._scroll.verticalScrollBar().setValue(scroll)


def _stat_time(seconds):
    minutes = int(round(seconds / 60))
    hours, rest = divmod(minutes, 60)
    return f"{hours}h {rest:02d}m" if hours else f"{rest}m"


def _wrap_html(body):
    return f'<div style="font-family: Consolas, monospace; font-size: 10pt;">{body}</div>'


def format_session_html(session):
    """Returns an HTML fragment (a <div>...</div>) describing one completed
    session -- header line, blocked apps / allowed sites, violation
    timeline, and any mid-session exceptions. Same content/order as the
    original Tk _write_session(), just emitted as HTML instead of
    Text-widget tag inserts."""
    start = _parse(session.get("startTime"))
    end = _parse(session.get("endTime"))

    end_type = session.get("endType", "manual")
    end_label = "NUCLEAR" if end_type == "nuclear" else end_type
    header_color = "#c62828" if end_type == "nuclear" else None

    if start and end:
        duration = _format_duration(int((end - start).total_seconds()))
        header = (
            f"{_format_dt(start)}  →  {_format_dt(end)}   "
            f"({duration}, {_esc(session.get('lockMode', '?'))} lock, {end_label} end)"
        )
    else:
        header = f"(unknown start/end time, {_esc(session.get('lockMode', '?'))} lock, {end_label} end)"

    parts = []
    if header_color:
        parts.append(f'<b style="color:{header_color};">{_esc(header)}</b><br>')
    else:
        parts.append(f"<b>{_esc(header)}</b><br>")

    source_label = _format_source_label(session)
    if source_label:
        parts.append(f'<span style="color:#3f8f86;">{_esc(source_label)}</span><br>')

    pomodoro_label = _format_pomodoro_label(session)
    if pomodoro_label:
        parts.append(f'<span style="color:#6C3FA8;">{_esc(pomodoro_label)}</span><br>')

    parts.append(f'<span style="color:#888888;">{"─" * len(header)}</span><br>')

    reason = session.get("reason")
    if end_type == "nuclear":
        parts.append(
            f'<span style="color:#c62828;">Nuclear end reason: '
            f'{_esc(reason or "(none given)")}</span><br>'
        )

    process_blocklist = session.get("processBlocklist") or []
    domain_whitelist = session.get("domainWhitelist") or []
    parts.append(f"Blocked apps:&nbsp; {_esc(', '.join(process_blocklist) or '(none)')}<br>")
    parts.append(f"Allowed sites: {_esc(', '.join(domain_whitelist) or '(none)')}<br>")

    violation_log = session.get("violationLog") or []
    violation_count = session.get(
        "violationCount", sum(1 for e in violation_log if e.get("kind") in ("process", "domain"))
    )
    parts.append(f'<span style="color:#888888;">Violations: {violation_count}</span><br>')

    for entry in violation_log:
        kind = entry.get("kind", "process")
        if kind in ("pause", "resume"):
            parts.append(f'<span style="color:#888888;">&nbsp;&nbsp;{_esc(_format_pause_event(entry))}</span><br>')
        else:
            line, color = _format_violation(entry)
            parts.append(f'<span style="color:{color};">&nbsp;&nbsp;{_esc(line)}</span><br>')

    process_exceptions = session.get("processBlocklistExceptions") or []
    domain_additions = session.get("domainWhitelistAdditions") or []
    if process_exceptions or domain_additions:
        parts.append('<span style="color:#888888;">Mid-session exceptions:</span><br>')
        for entry in process_exceptions:
            parts.append(f"&nbsp;&nbsp;{_esc(_format_exception('app unblocked', entry, 'process'))}<br>")
        for entry in domain_additions:
            parts.append(f"&nbsp;&nbsp;{_esc(_format_exception('site allowed', entry, 'domain'))}<br>")

    parts.append(f'<br><span style="color:#888888;">{SEPARATOR}</span><br><br>')
    return "".join(parts)


def _format_source_label(session):
    """Task/review context for a completed session's header -- a plain
    manual session (the common case, no linked task/event) shows nothing
    extra. Mirrors the same source-based labeling the browser extension's
    popup already shows for a *live* session (see chrome/popup/popup.js's
    renderActiveSession), applied here to completed history entries."""
    source = session.get("source")
    event_title = session.get("eventTitle")
    review_problem_name = session.get("reviewProblemName")
    review_subject_name = session.get("reviewSubjectName")

    if source == "review":
        label = "First attempt" if session_manager.is_first_attempt(session) else f"Review: {review_problem_name or '?'}"
        if review_subject_name:
            label += f"  •  Subject: {review_subject_name}"
        if event_title:
            label += f"  •  Task: {event_title}"
        return label
    if source == "task":
        return f"Task: {event_title}" if event_title else None
    if source == "calendar-event":
        return f"Calendar event: {event_title}" if event_title else None
    return None


def _format_pomodoro_label(session):
    """Pomodoro sessions (session_manager.start_pomodoro_session) run
    multiple focus/break cycles but only ever produce one history entry, at
    the end -- without this, that entry looked exactly like a plain session
    someone happened to pause and resume a few times (the pause/resume
    violationLog markers _advance_pomodoro_locked adds to keep break time
    out of the worked-time tally). completedCycles is the pomodoro dict's
    currentCycle at the moment it finished/was cut short, which is the last
    fully-started cycle, not necessarily the last fully-finished one."""
    pomodoro = session.get("pomodoro")
    if not pomodoro:
        return None
    focus = pomodoro.get("focusMinutes")
    brk = pomodoro.get("breakMinutes")
    completed = pomodoro.get("currentCycle")
    total = pomodoro.get("totalCycles")
    return f"Pomodoro: {focus}+{brk} min, cycle {completed}/{total}"


def _format_violation(entry):
    kind = entry.get("kind", "process")
    name = entry.get("process") if kind == "process" else entry.get("url")
    ts = _parse(entry.get("timestamp"))
    time_text = ts.strftime("%H:%M:%S") if ts else "?"

    duration_seconds = entry.get("durationSeconds")
    if duration_seconds is not None:
        resolution = f"back on track after {_format_duration(duration_seconds)}"
        color = "#2e7d32"
    else:
        resolution = "never corrected before session ended"
        color = "#c62828"

    return f"[{kind}] {name}  —  {time_text}  —  {resolution}", color


def _format_pause_event(entry):
    label = "Paused" if entry.get("kind") == "pause" else "Resumed"
    ts = _parse(entry.get("timestamp"))
    time_text = ts.strftime("%H:%M:%S") if ts else "?"
    return f"{label} at {time_text}"


def _format_exception(kind_label, entry, key):
    name = entry.get(key, "?")
    reason = entry.get("reason") or "(no reason given)"
    ts = _parse(entry.get("timestamp"))
    time_text = ts.strftime("%H:%M:%S") if ts else "?"
    return f"[{kind_label}] {name}  —  {time_text}  —  {reason}"


def _format_dt(dt):
    return dt.strftime("%Y-%m-%d %H:%M:%S")


def _format_duration(total_seconds):
    minutes, seconds = divmod(max(0, total_seconds), 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours}h {minutes}m {seconds}s"
    if minutes:
        return f"{minutes}m {seconds}s"
    return f"{seconds}s"


def _parse(iso_string):
    if not iso_string:
        return None
    try:
        return datetime.fromisoformat(iso_string)
    except ValueError:
        return None


def _esc(text):
    return html.escape(str(text))
