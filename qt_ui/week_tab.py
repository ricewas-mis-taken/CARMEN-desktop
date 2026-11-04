"""The week ahead: what has to be reviewed on each day, which days are heavy, and a way to move reviews
around by dragging them onto another day. Moving earlier is free; each day later costs 3 points, shown
while you drag, so nothing is lost by surprise."""
from datetime import date, timedelta

from PySide6.QtCore import QMimeData, QPoint, Qt, QTimer
from PySide6.QtGui import QDrag
from PySide6.QtWidgets import (
    QFrame, QHBoxLayout, QLabel, QScrollArea, QSizePolicy, QVBoxLayout, QWidget,
)

import review_store
import score_store
import report_card
import session_history
import week_plan
from qt_ui import report_card_dialog
from qt_ui.ui_kit import FAINT, INK, MUTED, SECONDARY_STYLE, button, format_minutes, safe_color, tint

MIME = "application/x-carmen-review"
AMBER = "#F5A524"
_COLUMN_MIN = 150


def _day_name(day, today):
    if day == today:
        return "Today"
    if day == today + timedelta(days=1):
        return "Tomorrow"
    return day.strftime("%A")


def cost_text(cost):
    return "free" if cost == 0 else f"{cost} points"


class _Chip(QFrame):
    """One review. Press and drag to move it to another day."""

    def __init__(self, problem, on_open):
        super().__init__()
        self.problem = problem
        self._press = None
        colour = safe_color(problem.get("subjectColor"))
        self.setObjectName("WeekChip")
        self.setCursor(Qt.OpenHandCursor)
        self.setStyleSheet(
            f"QFrame#WeekChip{{background:#FFFFFF;border:1px solid {tint(colour, 0.5)};border-left:4px solid {colour};"
            "border-radius:9px;}")
        row = QVBoxLayout(self)
        row.setContentsMargins(9, 6, 9, 6)
        row.setSpacing(1)
        name = QLabel(problem["name"])
        name.setWordWrap(True)
        name.setStyleSheet(f"color:{INK};font-size:13px;font-weight:600;background:transparent;border:none;")
        row.addWidget(name)
        sub = QLabel(problem.get("subjectName") or "")
        sub.setStyleSheet(f"color:{MUTED};font-size:11.5px;background:transparent;border:none;")
        row.addWidget(sub)
        self.setToolTip("Drag to another day to move this review")

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self._press = event.position().toPoint()
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._press is None or not (event.buttons() & Qt.LeftButton):
            return
        if (event.position().toPoint() - self._press).manhattanLength() < 8:
            return
        self._press = None
        mime = QMimeData()
        mime.setData(MIME, str(self.problem["id"]).encode())
        drag = QDrag(self)
        drag.setMimeData(mime)
        drag.setPixmap(self.grab())
        drag.setHotSpot(QPoint(16, 12))
        self.setCursor(Qt.ClosedHandCursor)
        drag.exec(Qt.MoveAction)
        self.setCursor(Qt.OpenHandCursor)

    def mouseReleaseEvent(self, event):
        self._press = None
        super().mouseReleaseEvent(event)


class _DayColumn(QFrame):
    """One day. Accepts a dragged review and snaps it in (the page redraws with it there)."""

    def __init__(self, day, today, entry, on_drop, on_hint, on_open):
        super().__init__()
        self.day, self._today, self._on_drop, self._on_hint = day, today, on_drop, on_hint
        self.setAcceptDrops(True)
        self.setObjectName("WeekDay")
        self._heavy = entry["heavy"]
        self._rest()
        self.setMinimumWidth(_COLUMN_MIN)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(6)
        title = QLabel(_day_name(day, today))
        title.setStyleSheet(f"color:{INK};font-size:15px;font-weight:700;background:transparent;border:none;")
        layout.addWidget(title)
        count = len(entry["reviews"])
        parts = [f"{count} review{'s' if count != 1 else ''}"]
        if entry["goal_minutes"]:
            parts.append(f"{format_minutes(entry['goal_minutes'])} of goals")
        meta = QLabel(", ".join(parts))
        meta.setStyleSheet(f"color:{MUTED};font-size:12px;background:transparent;border:none;")
        layout.addWidget(meta)
        if entry["heavy"]:
            tag = QLabel("Heavy day")
            tag.setStyleSheet(
                f"color:#8A5A00;background:{tint(AMBER, 0.3)};border:none;border-radius:8px;"
                "padding:2px 8px;font-size:11.5px;font-weight:700;")
            tag.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Fixed)
            layout.addWidget(tag, 0, Qt.AlignLeft)
        for problem in entry["reviews"]:
            layout.addWidget(_Chip(problem, on_open))
        layout.addStretch(1)

    def _rest(self):
        border = f"1.5px solid {AMBER}" if self._heavy else "1px solid #E6E9EF"
        self.setStyleSheet(f"QFrame#WeekDay{{background:#FFFFFF;border:{border};border-radius:14px;}}")

    def _lit(self):
        self.setStyleSheet(
            "QFrame#WeekDay{background:#F1EEFF;border:2px solid #8B7BE8;border-radius:14px;}")

    def _cost_for(self, event):
        try:
            problem_id = int(bytes(event.mimeData().data(MIME)).decode())
        except ValueError:
            return None, None
        problem = review_store.get_problem(problem_id)
        if not problem:
            return None, None
        due = date.fromisoformat((problem.get("originalDueDate") or problem["nextReviewDate"])[:10])
        return problem, review_store.move_cost(due, self.day)

    def dragEnterEvent(self, event):
        if not event.mimeData().hasFormat(MIME):
            return
        problem, cost = self._cost_for(event)
        if problem is None:
            return
        event.acceptProposedAction()
        self._lit()
        self._on_hint(self.day, cost, problem)

    def dragMoveEvent(self, event):
        if event.mimeData().hasFormat(MIME):
            event.acceptProposedAction()

    def dragLeaveEvent(self, event):
        self._rest()
        self._on_hint(None, None, None)

    def dropEvent(self, event):
        self._rest()
        problem, _cost = self._cost_for(event)
        if problem is None:
            return
        event.acceptProposedAction()
        self._on_drop(problem, self.day)


class WeekTab(QWidget):
    def __init__(self):
        super().__init__()
        self._today = date.today()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(28, 24, 28, 16)
        layout.setSpacing(10)
        head = QHBoxLayout()
        title = QLabel("Week")
        title.setStyleSheet(f"font-size:26px;font-weight:700;color:{INK};background:transparent;")
        head.addWidget(title)
        head.addStretch(1)
        last = button("Last week", SECONDARY_STYLE, height=34)
        last.clicked.connect(self._show_last_week)
        head.addWidget(last)
        self._score = QLabel("")
        self._score.setStyleSheet(f"color:{INK};font-size:15px;font-weight:700;background:transparent;")
        head.addWidget(self._score)
        layout.addLayout(head)
        self._note = QLabel("")
        self._note.setWordWrap(True)
        self._note.setStyleSheet(f"color:{MUTED};font-size:13.5px;background:transparent;")
        layout.addWidget(self._note)
        self._hint = QLabel("")
        self._hint.setStyleSheet(f"color:{INK};font-size:13.5px;font-weight:600;background:transparent;")
        self._hint.setMinimumHeight(20)
        layout.addWidget(self._hint)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setStyleSheet("QScrollArea{background:transparent;}")
        scroll.viewport().setStyleSheet("background:transparent;")
        self._holder = QWidget()
        self._holder.setStyleSheet("background:transparent;")
        self._row = QHBoxLayout(self._holder)
        self._row.setContentsMargins(0, 0, 0, 0)
        self._row.setSpacing(10)
        scroll.setWidget(self._holder)
        layout.addWidget(scroll, 1)
        self.refresh()

    def minimumSizeHint(self):
        from PySide6.QtCore import QSize
        return QSize(560, 420)

    def refresh(self):
        self._today = date.today()
        plan = week_plan.build(self._today)
        while self._row.count():
            item = self._row.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.hide()
                widget.setParent(None)
                widget.deleteLater()
        for entry in plan:
            self._row.addWidget(_DayColumn(entry["date"], self._today, entry, self._moved, self._show_hint, None), 1)
        value = score_store.score()
        self._score.setText(f"Score {value}  ({score_store.grade(value)})")
        self._note.setText(self._summary(plan))

    def _summary(self, plan):
        total = sum(len(d["reviews"]) for d in plan)
        if not total:
            return "Nothing to review this week. Reviews appear here as they come due."
        heavy = [d for d in plan if d["heavy"]]
        light = week_plan.lightest_day(plan)
        text = f"{total} review{'s' if total != 1 else ''} this week."
        if heavy:
            names = ", ".join(_day_name(d["date"], self._today) for d in heavy)
            text += f" Heaviest: {names}. {_day_name(light, self._today)} is the lightest."
        text += " Drag a review to another day. Earlier is free, each day later costs 3 points."
        return text

    def _show_last_week(self):
        card = report_card.build(report_card.last_week(), session_history.load_all())
        report_card_dialog.show(card, on_plan=self.refresh)

    def _show_hint(self, day, cost, problem):
        if day is None:
            self._hint.setText("")
            return
        self._hint.setText(f"{problem['name']} to {_day_name(day, self._today)}: {cost_text(cost)}")

    def _moved(self, problem, day):
        self._hint.setText("")
        current = date.fromisoformat(problem["nextReviewDate"][:10])
        if max(current, self._today) == day:
            return
        moved = review_store.move_review(problem["id"], day)
        if moved is None:
            self._hint.setText("That review cannot go later than a week past its due date.")
        QTimer.singleShot(0, self.refresh)
