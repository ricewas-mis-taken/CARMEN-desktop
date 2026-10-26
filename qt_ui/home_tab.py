"""Home: the page Carmen opens on.

What is on it, and why: the answer to "what should I do now?" comes first (the task that
still asks for the most time, with a Start button), then three plain numbers (focused
today, tasks still open, reviews due) and the last week as bars. The colour themes sit at
the bottom: picked once, rarely touched again.
"""
from datetime import date, datetime

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import (
    QColorDialog, QFrame, QGridLayout, QHBoxLayout, QLabel, QPushButton, QScrollArea, QVBoxLayout, QWidget,
)

import review_store
import session_history
import tasks_store
import theme
from qt_ui.history_viewer import minutes_by_day
from qt_ui.ui_kit import (
    INK, MUTED, FAINT, SECONDARY_STYLE, accent, button, dialog_title, field_label, format_minutes, role_label,
)

_CARD = "QFrame#HomeCard{background:#FFFFFF;border:1px solid #E7EAF0;border-radius:16px;}"
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
    open_tasks = []
    for task in tasks:
        snap = tasks_store.goal_snapshot(task, today, sessions)
        left = snap["required"] - snap["logged"]
        if snap["required"] > 0 and left > 0.5:
            open_tasks.append({"task": task, "left": left, "required": snap["required"], "phrase": snap["phrase"]})
    open_tasks.sort(key=lambda item: -item["left"])
    week = minutes_by_day(sessions, 7, today=today)
    return {
        "focused_today": week[-1][1],
        "open": open_tasks,
        "due_reviews": due_reviews,
        "week": week,
    }


def due_review_count():
    total = 0
    for topic in review_store.list_topics():
        total += len(review_store.list_problems(topic["id"], due_only=True))
    return total


class _WeekBars(QWidget):
    def __init__(self, week):
        super().__init__()
        self._week = week
        self.setFixedHeight(86)

    def paintEvent(self, _event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        peak = max([m for _d, m in self._week] + [1])
        slot = self.width() / len(self._week)
        for i, (day, minutes) in enumerate(self._week):
            height = max(4.0, (self.height() - 22) * minutes / peak) if minutes else 4.0
            x = i * slot + slot * 0.2
            colour = QColor(INK) if i == len(self._week) - 1 else QColor("#CBD2DE")
            p.setPen(Qt.NoPen)
            p.setBrush(colour)
            p.drawRoundedRect(QRectF(x, self.height() - 20 - height, slot * 0.6, height), 4, 4)
            p.setPen(QColor(FAINT))
            p.drawText(QRectF(i * slot, self.height() - 16, slot, 16), Qt.AlignCenter, day.strftime("%a")[:2])


def _card():
    frame = QFrame()
    frame.setObjectName("HomeCard")
    frame.setStyleSheet(_CARD)
    return frame


def _stat(value, caption):
    card = _card()
    layout = QVBoxLayout(card)
    layout.setContentsMargins(20, 16, 20, 16)
    layout.setSpacing(2)
    number = QLabel(value)
    number.setStyleSheet(
        f"color:{INK};font-size:30px;font-weight:600;background:transparent;"
        "font-family:'Bahnschrift SemiBold','Segoe UI Semibold','Segoe UI';")
    layout.addWidget(number)
    label = QLabel(caption)
    label.setStyleSheet(f"color:{MUTED};font-size:12.5px;background:transparent;")
    layout.addWidget(label)
    return card


class HomeTab(QWidget):
    def __init__(self, on_navigate=None):
        super().__init__()
        self._on_navigate = on_navigate or (lambda key: None)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setObjectName("HomeScroll")
        outer.addWidget(scroll)
        self._body = QWidget()
        self._body.setObjectName("HomePage")
        scroll.setWidget(self._body)
        self._layout = QVBoxLayout(self._body)
        self._layout.setContentsMargins(32, 28, 32, 32)
        self._layout.setSpacing(16)
        self.refresh()

    def showEvent(self, event):
        self.refresh()
        super().showEvent(event)

    def _go(self, key):
        self._on_navigate(key)

    def refresh(self):
        while self._layout.count():
            item = self._layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        now = datetime.now()
        data = summary(tasks_store.load_tasks(), session_history.load_all(), due_review_count(), date.today())

        title = QLabel(greeting(now))
        title.setStyleSheet(f"font-size:28px;font-weight:700;color:{INK};background:transparent;")
        self._layout.addWidget(title)
        self._layout.addWidget(role_label(now.strftime("%A, %B ") + str(now.day), "subtitle"))

        self._layout.addWidget(self._next_card(data))

        stats = QHBoxLayout()
        stats.setSpacing(14)
        stats.addWidget(_stat(format_minutes(data["focused_today"]), "focused today"))
        stats.addWidget(_stat(str(len(data["open"])), "tasks still open"))
        stats.addWidget(_stat(str(data["due_reviews"]), "reviews due"))
        self._layout.addLayout(stats)

        week = _card()
        week_layout = QVBoxLayout(week)
        week_layout.setContentsMargins(20, 16, 20, 14)
        week_layout.addWidget(field_label("Last 7 days"))
        week_layout.addWidget(_WeekBars(data["week"]))
        self._layout.addWidget(week)

        self._layout.addWidget(self._theme_card())
        self._layout.addStretch(1)

    def _next_card(self, data):
        card = _card()
        row = QHBoxLayout(card)
        row.setContentsMargins(24, 20, 24, 20)
        text = QVBoxLayout()
        text.setSpacing(4)
        if data["open"]:
            top = data["open"][0]
            text.addWidget(field_label("Start with"))
            name = QLabel(top["task"].get("name", "Task"))
            name.setStyleSheet(f"font-size:22px;font-weight:700;color:{INK};background:transparent;")
            text.addWidget(name)
            text.addWidget(role_label(f"{format_minutes(top['left'])} left {top['phrase']}", "subtitle"))
            action = button("Start a session", _PRIMARY, height=44)
            action.clicked.connect(lambda: self._go("focus"))
        elif data["due_reviews"]:
            text.addWidget(field_label("Start with"))
            text.addWidget(role_label(f"{data['due_reviews']} reviews are due", "title"))
            action = button("Open Review", _PRIMARY, height=44)
            action.clicked.connect(lambda: self._go("review"))
        else:
            text.addWidget(role_label("Nothing is waiting for you.", "title"))
            text.addWidget(role_label("Every goal for today is met and no reviews are due.", "subtitle"))
            action = button("Plan something", SECONDARY_STYLE, height=44)
            action.clicked.connect(lambda: self._go("tasks"))
        row.addLayout(text, 1)
        row.addWidget(action, 0, Qt.AlignVCenter)
        return card

    def _theme_card(self):
        card = _card()
        layout = QVBoxLayout(card)
        layout.setContentsMargins(20, 16, 20, 18)
        layout.setSpacing(10)
        layout.addWidget(field_label("Colours"))
        grid = QGridLayout()
        grid.setSpacing(10)
        setting = theme.load_setting()
        current = setting.get("name") if isinstance(setting, dict) else theme.DEFAULT_THEME
        for i, (name, t) in enumerate(theme.THEMES.items()):
            tile = QPushButton()
            tile.setCursor(Qt.PointingHandCursor)
            tile.setToolTip(t["why"])
            tile.setFixedHeight(52)
            border = f"2px solid {t['accent']}" if name == current else "1px solid #E7EAF0"
            tile.setStyleSheet(
                f"QPushButton{{background:{t['page']};border:{border};border-radius:12px;"
                f"color:{t['ink']};font-size:13px;font-weight:600;text-align:left;padding:0 14px;}}")
            tile.setText(name)
            tile.clicked.connect(lambda _c=False, n=name: self._choose(n))
            grid.addWidget(tile, i // 3, i % 3)
        custom = QPushButton("Make your own")
        custom.setCursor(Qt.PointingHandCursor)
        custom.setFixedHeight(52)
        custom.setStyleSheet(SECONDARY_STYLE)
        custom.clicked.connect(self._open_custom)
        grid.addWidget(custom, len(theme.THEMES) // 3, len(theme.THEMES) % 3)
        layout.addLayout(grid)
        self._theme_note = role_label(self._theme_why(current), "hint", wrap=True)
        layout.addWidget(self._theme_note)
        return card

    @staticmethod
    def _theme_why(name):
        entry = theme.THEMES.get(name)
        return entry["why"] if entry else "Your own colours."

    def _choose(self, name):
        theme.save_setting({"name": name})
        self._reapply()
        self.refresh()

    def _reapply(self):
        """Repaint the stylesheet now. Colours written straight into a widget's own
        style (the ink buttons, the numerals) update the next time the app starts."""
        import os
        from PySide6.QtWidgets import QApplication
        from qt_ui import ui_kit
        path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "styles.qss")
        try:
            with open(path, encoding="utf-8") as f:
                QApplication.instance().setStyleSheet(
                    ui_kit.with_check_icon(theme.apply_to_stylesheet(f.read(), theme.current_colours())))
        except OSError:
            pass

    def _open_custom(self):
        self._dialog = _CustomThemeDialog(on_saved=lambda: (self._reapply(), self.refresh()))


class _CustomThemeDialog(QWidget):
    LABELS = {
        "ink": "Text and buttons", "muted": "Secondary text", "faint": "Hints",
        "accent": "Accent", "page": "Page background", "sidebar": "Sidebar",
    }

    def __init__(self, on_saved):
        super().__init__(None, Qt.WindowStaysOnTopHint)
        self.setObjectName("PopupBg")
        self.setWindowTitle("Your colours")
        self.resize(420, 520)
        self._on_saved = on_saved
        self._colours = dict(theme.current_colours())

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 22, 24, 20)
        layout.setSpacing(8)
        layout.addWidget(dialog_title("Your colours", "Cards stay white. The rest is yours."))
        self._swatches = {}
        for key in theme.TOKENS:
            row = QHBoxLayout()
            row.addWidget(QLabel(self.LABELS[key]), 1)
            swatch = QPushButton()
            swatch.setFixedSize(76, 30)
            swatch.setCursor(Qt.PointingHandCursor)
            swatch.clicked.connect(lambda _c=False, k=key: self._pick(k))
            self._swatches[key] = swatch
            row.addWidget(swatch)
            layout.addLayout(row)
            self._paint(key)
        layout.addStretch(1)
        buttons = QHBoxLayout()
        buttons.addStretch(1)
        cancel = QPushButton("Cancel")
        cancel.clicked.connect(self.close)
        buttons.addWidget(cancel)
        save = accent(QPushButton("Use these"))
        save.clicked.connect(self._save)
        buttons.addWidget(save)
        layout.addLayout(buttons)
        self.show()

    def _paint(self, key):
        self._swatches[key].setStyleSheet(
            f"background:{self._colours[key]};border:1px solid #0003;border-radius:8px;")

    def _pick(self, key):
        chosen = QColorDialog.getColor(QColor(self._colours[key]), self, self.LABELS[key])
        if chosen.isValid():
            self._colours[key] = chosen.name().upper()
            self._paint(key)

    def _save(self):
        theme.save_setting({"name": "Custom", "custom": dict(self._colours)})
        self.close()
        self._on_saved()
