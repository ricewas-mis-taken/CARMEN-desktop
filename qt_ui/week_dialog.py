"""The week ahead, opened from the Review page. One sheet, one row per day: the date on the left, that
day's reviews as loose tags in the middle, its workload as a short bar on the right. Drag a review onto
another day to move it. Earlier is free, each day later costs 3 points, said while you drag."""
from datetime import date, timedelta

from PySide6.QtCore import QMimeData, QPoint, Qt, QTimer
from PySide6.QtGui import QDrag
from PySide6.QtWidgets import (
    QFrame, QHBoxLayout, QLabel, QScrollArea, QSizePolicy, QSlider, QVBoxLayout, QWidget,
)

import review_store
import week_plan
from qt_ui.ui_kit import FAINT, INK, MUTED, FlowLayout, format_minutes, safe_color, tint

MIME = "application/x-carmen-review"
MAX_WEEKS = 4
AMBER = "#D98A00"
_OPEN = []


def show():
    dialog = WeekDialog()
    _OPEN.append(dialog)
    dialog.show()
    return dialog


def _refresh_review_page():
    """A move changes what is due, so the Review page's lists catch up now rather than on the next click."""
    try:
        from qt_ui import main_window
        page = main_window._win._pages["review"] if main_window._win is not None else None
        page = page.widget() if hasattr(page, "widget") else page
        for view in page._topic_views.values():
            view.refresh()
        page._refresh_strip()
    except Exception:
        pass


def cost_text(cost):
    """What a move costs, said as a cost (the number itself is negative: 0 or less)."""
    return "free" if cost == 0 else f"costs {abs(cost)} points"


def cost_for_day(problem, day):
    """(points, can_move) of putting this review on `day`; the points are 0 or less."""
    due = date.fromisoformat((problem.get("originalDueDate") or problem["nextReviewDate"])[:10])
    return review_store.move_cost(due, day), (day - due).days <= review_store.MAX_MOVE_DAYS


def _weekday(day, today):
    return "Today" if day == today else "Tomorrow" if day == today + timedelta(days=1) else day.strftime("%A")


class _Tag(QFrame):
    """One review, as a small tag with its subject's colour. Press and drag to move it."""

    def __init__(self, problem, day=None):
        super().__init__()
        self.problem = problem
        self._press = None
        colour = safe_color(problem.get("subjectColor"))
        self.setObjectName("WeekTag")
        self.setCursor(Qt.OpenHandCursor)
        self.setStyleSheet(f"QFrame#WeekTag{{background:{tint(colour, 0.18)};border:none;border-radius:8px;}}")
        row = QHBoxLayout(self)
        row.setContentsMargins(8, 4, 10, 4)
        row.setSpacing(7)
        dot = QLabel()
        dot.setFixedSize(8, 8)
        dot.setStyleSheet(f"background:{colour};border-radius:4px;")
        row.addWidget(dot)
        name = QLabel(problem["name"])
        name.setStyleSheet(f"color:{INK};font-size:13px;font-weight:600;background:transparent;")
        row.addWidget(name)
        if day is not None:
            lost = -cost_for_day(problem, day)[0]
            chip = QLabel(f"-{lost} pts" if lost else "free")
            chip.setStyleSheet(
                f"color:{'#D9480F' if lost else '#2F9E44'};background:transparent;font-size:11.5px;font-weight:700;")
            chip.setToolTip("Points this review loses on this day" if lost else "No points lost: on or before its due day")
            row.addWidget(chip)
        self.setToolTip(f"{problem['name']}, {problem.get('subjectName') or ''}. Drag to another day.")

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
        drag.setHotSpot(QPoint(12, 12))
        dialog = self.window()
        if hasattr(dialog, "drag_started"):
            dialog.drag_started(self.problem)           # every day says what it would cost while this is in the air
        drag.exec(Qt.MoveAction)
        if hasattr(dialog, "drag_ended"):
            dialog.drag_ended()

    def mouseReleaseEvent(self, event):
        self._press = None
        super().mouseReleaseEvent(event)


class _LoadBar(QWidget):
    """A day's workload against the heaviest day of the week."""

    def __init__(self, fraction, heavy):
        super().__init__()
        self._fraction, self._heavy = max(0.0, min(1.0, fraction)), heavy
        self.setFixedSize(96, 6)

    def paintEvent(self, _event):
        from PySide6.QtCore import QRectF
        from PySide6.QtGui import QColor, QPainter
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(Qt.NoPen)
        rail = QColor(INK)
        rail.setAlphaF(0.10)
        p.setBrush(rail)
        p.drawRoundedRect(QRectF(0, 0, self.width(), 6), 3, 3)
        if self._fraction > 0:
            p.setBrush(QColor(AMBER if self._heavy else INK))
            p.drawRoundedRect(QRectF(0, 0, max(6.0, self.width() * self._fraction), 6), 3, 3)


class _DayRow(QFrame):
    """One day. A review dropped anywhere on the row moves to it."""

    def __init__(self, entry, today, peak, on_drop, on_hint):
        super().__init__()
        self.day, self._today, self._on_drop, self._on_hint = entry["date"], today, on_drop, on_hint
        self.setAcceptDrops(True)
        self._reviews = entry["reviews"]
        self.setObjectName("WeekRow")
        self._rest()
        row = QHBoxLayout(self)
        row.setContentsMargins(4, 12, 4, 12)
        row.setSpacing(16)

        gutter = QVBoxLayout()
        gutter.setSpacing(0)
        is_today = self.day == today
        number = QLabel(str(self.day.day))
        number.setStyleSheet(
            f"color:{INK if is_today else MUTED};font-size:30px;font-weight:{800 if is_today else 600};background:transparent;")
        gutter.addWidget(number)
        name = QLabel(_weekday(self.day, today))
        name.setStyleSheet(f"color:{INK if is_today else FAINT};font-size:12.5px;font-weight:600;background:transparent;")
        gutter.addWidget(name)
        holder = QWidget()
        holder.setLayout(gutter)
        holder.setFixedWidth(86)
        row.addWidget(holder, 0, Qt.AlignTop)

        tags = FlowLayout(spacing=6)
        for problem in entry["reviews"]:
            tags.addWidget(_Tag(problem, self.day))
        middle = QWidget()
        middle.setLayout(tags)
        middle.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        if not entry["reviews"]:
            empty = QLabel("Nothing to review")
            empty.setStyleSheet(f"color:{FAINT};font-size:13px;background:transparent;")
            row.addWidget(empty, 1, Qt.AlignVCenter)
        else:
            row.addWidget(middle, 1)

        self._cost = QLabel("")
        self._cost.setFixedWidth(104)
        self._cost.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        row.addWidget(self._cost, 0, Qt.AlignVCenter)

        load = QVBoxLayout()
        load.setSpacing(4)
        load.addWidget(_LoadBar(entry["load"] / peak if peak else 0, entry["heavy"]), 0, Qt.AlignRight)
        text = format_minutes(entry["load"]) if entry["load"] else "free"
        if entry["heavy"]:
            text += ", busy"
        caption = QLabel(text)
        caption.setAlignment(Qt.AlignRight)
        caption.setStyleSheet(
            f"color:{AMBER if entry['heavy'] else MUTED};font-size:12px;font-weight:{700 if entry['heavy'] else 500};"
            "background:transparent;")
        load.addWidget(caption)
        holder = QWidget()
        holder.setLayout(load)
        holder.setFixedWidth(110)
        row.addWidget(holder, 0, Qt.AlignTop)

    def show_cost(self, problem):
        """While a review is being dragged: what it would cost to put it on this day (nothing when it is
        already here)."""
        if problem is None or any(p["id"] == problem["id"] for p in self._reviews):
            self._cost.setText("")
            return
        points, can = cost_for_day(problem, self.day)
        text = "too late" if not can else "free" if points == 0 else f"-{abs(points)} points"
        colour = FAINT if not can else "#2F9E44" if points == 0 else "#D9480F"
        self._cost.setText(text)
        self._cost.setStyleSheet(f"color:{colour};font-size:13px;font-weight:700;background:transparent;")

    def _rest(self):
        self.setStyleSheet("QFrame#WeekRow{background:transparent;border:none;border-bottom:1px solid #E6E9EF;}")

    def _lit(self):
        self.setStyleSheet(f"QFrame#WeekRow{{background:{tint('#8B7BE8', 0.14)};border:none;border-bottom:1px solid #E6E9EF;}}")

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


class WeekDialog(QWidget):
    def __init__(self):
        super().__init__(None, Qt.WindowStaysOnTopHint)
        self.setObjectName("PopupBg")
        self.setWindowTitle("The week ahead")
        self._today = date.today()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(28, 24, 28, 18)
        layout.setSpacing(6)
        title = QLabel("The week ahead")
        title.setStyleSheet(f"color:{INK};font-size:24px;font-weight:800;background:transparent;")
        layout.addWidget(title)
        self._note = QLabel("")
        self._note.setWordWrap(True)
        self._note.setStyleSheet(f"color:{MUTED};font-size:14px;background:transparent;")
        layout.addWidget(self._note)
        self._hint = QLabel("")
        self._hint.setMinimumHeight(22)
        self._hint.setStyleSheet(f"color:{INK};font-size:14px;font-weight:700;background:transparent;")
        layout.addWidget(self._hint)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setStyleSheet("QScrollArea{background:transparent;}")
        scroll.viewport().setStyleSheet("background:transparent;")
        self._holder = QWidget()
        self._holder.setStyleSheet("background:transparent;")
        self._rows = QVBoxLayout(self._holder)
        self._rows.setContentsMargins(0, 0, 4, 0)
        self._rows.setSpacing(0)
        scroll.setWidget(self._holder)
        layout.addWidget(scroll, 1)
        bar = QHBoxLayout()
        self._span_label = QLabel("")
        self._span_label.setStyleSheet(f"color:{MUTED};font-size:13px;font-weight:600;background:transparent;")
        self._span_label.setFixedWidth(84)
        self._span = QSlider(Qt.Horizontal)
        self._span.setRange(1, MAX_WEEKS)
        self._span.setValue(1)
        self._span.valueChanged.connect(self.refresh)
        bar.addWidget(self._span_label)
        bar.addWidget(self._span, 1)
        layout.addLayout(bar)
        self.resize(720, 640)
        self.refresh()

    def closeEvent(self, event):
        if self in _OPEN:
            _OPEN.remove(self)
        super().closeEvent(event)

    def refresh(self):
        self._today = date.today()
        weeks = self._span.value()
        self._span_label.setText("1 week" if weeks == 1 else f"{weeks} weeks")
        plan = week_plan.build(self._today, days=7 * weeks)
        while self._rows.count():
            item = self._rows.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.hide()
                widget.setParent(None)
                widget.deleteLater()
        peak = max((d["load"] for d in plan), default=0)
        for entry in plan:
            self._rows.addWidget(_DayRow(entry, self._today, peak, self._moved, self._show_hint))
        self._rows.addStretch(1)
        self._note.setText(self._summary(plan, weeks))

    def _summary(self, plan, weeks=1):
        total = sum(len(d["reviews"]) for d in plan)
        span = "this week" if weeks == 1 else f"in the next {weeks} weeks"
        if not total:
            return f"Nothing to review {span}. Reviews show up here as they come due."
        text = f"{total} review{'s' if total != 1 else ''} {span}."
        heavy = [d for d in plan if d["heavy"]]
        if heavy:
            names = ", ".join(_weekday(d["date"], self._today) for d in heavy)
            text += f" Busiest: {names}. {_weekday(week_plan.lightest_day(plan), self._today)} is the lightest."
        return text + " Drag a review to another day: earlier is free, each day later costs 3 points."

    def drag_started(self, problem):
        for i in range(self._rows.count()):
            widget = self._rows.itemAt(i).widget()
            if isinstance(widget, _DayRow):
                widget.show_cost(problem)

    def drag_ended(self):
        self.drag_started(None)

    def _show_hint(self, day, cost, problem):
        self._hint.setText("" if day is None else f"{problem['name']} to {_weekday(day, self._today)}: {cost_text(cost)}")

    def _moved(self, problem, day):
        self._hint.setText("")
        current = date.fromisoformat(problem["nextReviewDate"][:10])
        if max(current, self._today) == day:
            return
        if review_store.move_review(problem["id"], day) is None:
            self._hint.setText("A review can't go more than a week past its due date.")
        QTimer.singleShot(0, self.refresh)
        QTimer.singleShot(0, _refresh_review_page)
