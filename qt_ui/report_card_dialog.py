"""The weekly report card popup. It is also the fresh-start moment: last week is shown as history, and one
button goes straight to planning the new week."""
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

import report_card
from qt_ui.ui_kit import FAINT, INK, MUTED, SECONDARY_STYLE, accent, button, format_minutes

_OPEN = []
GREEN, RED = "#2F9E44", "#D9363E"


def _signed(n):
    return f"+{n}" if n > 0 else f"{n}" if n < 0 else "0"


def show(card, on_plan=None):
    popup = ReportCardDialog(card, on_plan)
    _OPEN.append(popup)
    popup.show()
    return popup


class ReportCardDialog(QWidget):
    def __init__(self, card, on_plan=None):
        super().__init__(None, Qt.WindowStaysOnTopHint)
        self.setObjectName("PopupBg")
        self.setWindowTitle("Last week")
        self._on_plan = on_plan
        layout = QVBoxLayout(self)
        layout.setContentsMargins(28, 24, 28, 22)
        layout.setSpacing(12)

        title = QLabel(f"{card['start'].strftime('%b %d').replace(' 0', ' ')} to {card['end'].strftime('%b %d').replace(' 0', ' ')}")
        title.setStyleSheet(f"color:{MUTED};font-size:14px;background:transparent;")
        layout.addWidget(title)

        head = QHBoxLayout()
        grade = QLabel(card["grade"])
        grade.setStyleSheet(f"color:{INK};font-size:64px;font-weight:800;background:transparent;")
        head.addWidget(grade)
        score = QVBoxLayout()
        score.setSpacing(0)
        number = QLabel(f"{card['after']} / 1000")
        number.setStyleSheet(f"color:{INK};font-size:26px;font-weight:700;background:transparent;")
        score.addWidget(number)
        change = QLabel(f"{_signed(card['change'])} this week")
        colour = GREEN if card["change"] > 0 else RED if card["change"] < 0 else MUTED
        change.setStyleSheet(f"color:{colour};font-size:15px;font-weight:600;background:transparent;")
        score.addWidget(change)
        head.addLayout(score)
        head.addStretch(1)
        layout.addLayout(head)

        if card["empty"]:
            layout.addWidget(self._line("Nothing was scored this week. Your score stays where it is."))
        for row in card["rows"]:
            layout.addLayout(self._row(row["label"], row["points"]))

        extras = []
        if card["reviews_done"]:
            late = f", {card['reviews_late']} late" if card["reviews_late"] else ""
            extras.append(f"{card['reviews_done']} review{'s' if card['reviews_done'] != 1 else ''} done{late}")
        pause = card["pause"]
        if pause["free_seconds"] or pause["escape_seconds"]:
            extras.append(f"Pauses: {format_minutes(pause['free_seconds'] / 60)} honest, "
                          f"{format_minutes(pause['escape_seconds'] / 60)} looking elsewhere")
        for text in extras:
            layout.addWidget(self._line(text))

        rule = QFrame()
        rule.setFixedHeight(1)
        rule.setStyleSheet("background:#E6E9EF;border:none;")
        layout.addWidget(rule)
        advice = QLabel(card["suggestion"])
        advice.setWordWrap(True)
        advice.setStyleSheet(f"color:{INK};font-size:14.5px;font-weight:600;background:transparent;")
        layout.addWidget(advice)
        history = self._line("Last week is history. It does not carry over as debt.", FAINT)
        layout.addWidget(history)

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        close = button("Close", SECONDARY_STYLE, height=40)
        close.clicked.connect(self.close)
        buttons.addWidget(close)
        plan = accent(QPushButton("Plan this week"))
        plan.clicked.connect(self._plan)
        buttons.addWidget(plan)
        layout.addLayout(buttons)
        self.resize(480, 520)

    def _plan(self):
        self.close()
        if self._on_plan:
            self._on_plan()

    def _line(self, text, colour=MUTED):
        label = QLabel(text)
        label.setWordWrap(True)
        label.setStyleSheet(f"color:{colour};font-size:13.5px;background:transparent;")
        return label

    def _row(self, label, points):
        row = QHBoxLayout()
        name = QLabel(label)
        name.setStyleSheet(f"color:{INK};font-size:15px;background:transparent;")
        row.addWidget(name, 1)
        value = QLabel(_signed(points))
        colour = GREEN if points > 0 else RED if points < 0 else MUTED
        value.setStyleSheet(f"color:{colour};font-size:15px;font-weight:700;background:transparent;")
        row.addWidget(value)
        return row

    def closeEvent(self, event):
        if self in _OPEN:
            _OPEN.remove(self)
        super().closeEvent(event)
