"""A task's own history: every session you spent on it, with the totals that matter.

Opened from the clock button on a task card. It reads the same session log as the History tab, but
only the sessions filed under this task, so you can see how long it has taken in all, how often you
sat down to it, how many sittings ran to the end and how many were cut short, and open any one of
them for its details.
"""
from datetime import date

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QFrame, QGridLayout, QHBoxLayout, QLabel, QScrollArea, QVBoxLayout, QWidget

import session_history
from qt_ui.history_viewer import (
    _ActivityStrip, _SessionCard, _Stat, _duration_text, _parse, day_label, group_by_day, minutes_by_day,
    session_kind, summarize, worked_seconds,
)
from qt_ui.ui_kit import FAINT, INK, MUTED, dialog_title, role_label, safe_color

_open = set()        # keeps an open window alive


def task_sessions(task_id, sessions):
    """The finished sessions filed under one task, oldest first (as the log stores them)."""
    return [s for s in sessions if task_id and s.get("eventId") == task_id]


def task_summary(sessions):
    """Everything the top of the window shows, as plain data (so it can be tested without a window)."""
    base = summarize(sessions)
    durations = [worked_seconds(s) for s in sessions]
    starts = [d for d in (_parse(s.get("startTime")) for s in sessions) if d]
    ended_early = sum(1 for s in sessions if s.get("endType") == "nuclear")
    base.update(
        longest=max(durations, default=0),
        average=(sum(durations) / len(durations)) if durations else 0,
        ended_early=ended_early,
        ran_to_the_end=len(sessions) - ended_early,
        reviews=sum(1 for s in sessions if session_kind(s) == "review"),
        active_days=len({d.date() for d in starts}),
        first=min(starts).date() if starts else None,
        last=max(starts).date() if starts else None,
    )
    return base


def _date_text(day):
    return f"{day.strftime('%b')} {day.day}, {day.year}" if day else "—"


class TaskHistoryDialog(QWidget):
    def __init__(self, task):
        super().__init__(None, Qt.WindowStaysOnTopHint)
        self.setObjectName("PopupBg")
        self.setWindowTitle(f"{task.get('name') or 'Task'} — History")
        self.resize(780, 780)
        self._task = task
        self.sessions = task_sessions(task.get("id"), session_history.load_all())
        self.summary = task_summary(self.sessions)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(24, 22, 24, 20)
        outer.setSpacing(14)

        head = QHBoxLayout()
        head.setSpacing(12)
        dot = QFrame()
        dot.setFixedSize(14, 14)
        dot.setStyleSheet(f"background:{safe_color(task.get('color'))};border-radius:7px;border:none;")
        head.addWidget(dot, 0, Qt.AlignVCenter)
        head.addWidget(dialog_title(task.get("name") or "Task"), 1)
        outer.addLayout(head)

        stats = self.summary
        tiles = QGridLayout()
        tiles.setHorizontalSpacing(12)
        tiles.setVerticalSpacing(12)
        facts = [
            (_duration_text(stats["seconds"]), "worked in all"),
            (str(stats["sessions"]), "sessions"),
            (_duration_text(stats["longest"]), "longest sitting"),
            (_duration_text(stats["average"]), "average sitting"),
            (str(stats["active_days"]), "days worked"),
            (f"{stats['ran_to_the_end']} / {stats['ended_early']}", "ran to the end / cut short"),
            (str(stats["violations"]), "violations"),
            (f"{round(100 * stats['clean'] / stats['sessions'])}%" if stats["sessions"] else "—", "clean sessions"),
        ]
        self.tiles = []
        for index, (value, caption) in enumerate(facts):
            tile = _Stat(caption)
            tile.value.setText(value)
            tile.value.setStyleSheet(tile.value.styleSheet().replace("32px", "24px"))
            self.tiles.append(tile)
            tiles.addWidget(tile, index // 4, index % 4)
        outer.addLayout(tiles)

        span = QLabel(f"First session {_date_text(stats['first'])}  ·  Last {_date_text(stats['last'])}"
                      if stats["sessions"] else "")
        span.setStyleSheet(f"font-size:12.5px;color:{MUTED};background:transparent;")
        span.setVisible(bool(stats["sessions"]))
        outer.addWidget(span)

        if stats["sessions"]:
            strip = _ActivityStrip()
            strip.set_days(minutes_by_day(self.sessions))
            strip.setFixedHeight(96)
            outer.addWidget(strip)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setStyleSheet("QScrollArea{background:transparent;}")
        body = QWidget()
        body.setStyleSheet("background:transparent;")
        column = QVBoxLayout(body)
        column.setContentsMargins(2, 2, 6, 8)
        column.setSpacing(8)
        if not self.sessions:
            empty = QLabel("No sessions on this task yet.")
            empty.setAlignment(Qt.AlignCenter)
            empty.setStyleSheet(f"font-size:14px;color:{FAINT};padding:36px;background:transparent;")
            column.addWidget(empty)
        today = date.today()
        for day, day_sessions in group_by_day(self.sessions):
            column.addWidget(role_label(day_label(day, today), "field"))
            for session in day_sessions:
                column.addWidget(_SessionCard(session))
        column.addStretch(1)
        scroll.setWidget(body)
        outer.addWidget(scroll, 1)
        self.show()


def open_task_history(task):
    dialog = TaskHistoryDialog(task)
    _open.add(dialog)
    dialog.destroyed.connect(lambda: _open.discard(dialog))
    return dialog
