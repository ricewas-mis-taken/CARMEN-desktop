"""The "how it works" sheet behind the small i on the Home score: what moves the score, and how vacation minutes
are earned, each with a small drawing. The numbers come from score_store and vacation, so this cannot drift from
what the app really does."""
from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import (
    QButtonGroup, QHBoxLayout, QLabel, QPushButton, QStackedWidget, QToolButton, QVBoxLayout, QWidget,
)

import score_store
import vacation
from qt_ui.ui_kit import FAINT, INK, MUTED

_OPEN = []
GREEN, RED = "#2F9E44", "#D9363E"


def _ink(alpha=1.0):
    colour = QColor(INK)
    colour.setAlphaF(alpha)
    return colour


def _font(size, weight=QFont.Normal):
    font = QFont()
    font.setPixelSize(size)
    font.setWeight(weight)
    return font


def _text(painter, rect, text, size=11, weight=QFont.Normal, colour=None, align=Qt.AlignCenter):
    painter.setFont(_font(size, weight))
    painter.setPen(colour or QColor(MUTED))
    painter.drawText(rect, align, text)


def show(page="score"):
    sheet = Explainer(page)
    _OPEN.append(sheet)
    sheet.show()
    sheet.raise_()
    return sheet


class InfoButton(QToolButton):
    """A small round "i". Press it for the sheet."""

    def __init__(self, page="score", tip="How this works"):
        super().__init__()
        self._page = page
        self.setFixedSize(22, 22)
        self.setCursor(Qt.PointingHandCursor)
        self.setToolTip(tip)
        self.clicked.connect(lambda: show(self._page))

    def paintEvent(self, _event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        hover = self.underMouse()
        p.setPen(QPen(_ink(0.55 if hover else 0.28), 1.4))
        p.setBrush(_ink(0.07) if hover else Qt.NoBrush)
        p.drawEllipse(QRectF(2, 2, 18, 18))
        p.setPen(Qt.NoPen)
        p.setBrush(_ink(0.7 if hover else 0.5))
        p.drawEllipse(QPointF(11, 6.6), 1.3, 1.3)
        p.drawRoundedRect(QRectF(9.8, 9.2, 2.4, 6.4), 1.1, 1.1)


# --- the drawings ----------------------------------------------------------------------------------------

class _GradeRail(QWidget):
    """0 to 1000 cut into the five grades, with a marker where the score is now."""

    def __init__(self, value):
        super().__init__()
        self._value = value
        self.setFixedHeight(74)

    def paintEvent(self, _event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        left, right = 4.0, self.width() - 4.0
        span = right - left
        top, height = 30.0, 12.0
        edges = (0, 350, 500, 650, 800, 1000)
        letters = ("F", "D", "C", "B", "A")
        for i, letter in enumerate(letters):
            x0 = left + span * edges[i] / 1000
            x1 = left + span * edges[i + 1] / 1000
            path = QPainterPath()
            path.addRoundedRect(QRectF(x0 + 1.5, top, x1 - x0 - 3, height), 6, 6)
            p.fillPath(path, _ink(0.10 + 0.17 * i))
            _text(p, QRectF(x0, top + height + 4, x1 - x0, 16), letter, 13, QFont.Bold, _ink(0.85))
            if i:
                _text(p, QRectF(x0 - 20, top + height + 20, 40, 14), str(edges[i]), 10, colour=QColor(FAINT))
            else:
                _text(p, QRectF(left, top + height + 20, 40, 14), "0", 10, colour=QColor(FAINT), align=Qt.AlignLeft)
        _text(p, QRectF(right - 44, top + height + 20, 44, 14), "1000", 10, colour=QColor(FAINT), align=Qt.AlignRight)
        x = left + span * max(0, min(1000, self._value)) / 1000
        marker = QPainterPath()
        marker.moveTo(x, top - 3)
        marker.lineTo(x - 5, top - 11)
        marker.lineTo(x + 5, top - 11)
        marker.closeSubpath()
        p.fillPath(marker, _ink())
        _text(p, QRectF(x - 30, 0, 60, 16), f"{self._value}", 13, QFont.Bold, _ink())


class _Ladder(QWidget):
    """Vacation earned per extra minute, step by step, then nothing. Equal widths for readability: the real
    reach of each step sits under it."""

    def __init__(self):
        super().__init__()
        self.setFixedHeight(150)

    def paintEvent(self, _event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        left, right = 6.0, self.width() - 6.0
        base, tall = 108.0, 74.0
        cells = len(vacation.TIERS) + 1
        gap = 6.0
        width = (right - left - gap * (cells - 1)) / cells
        labels = ("first 25%", "next 25%", "next 50%", "next 100%", "past 2x")
        for i, (_span, rate) in enumerate(vacation.TIERS):
            x = left + i * (width + gap)
            h = tall * rate
            path = QPainterPath()
            path.addRoundedRect(QRectF(x, base - h, width, h), 7, 7)
            p.fillPath(path, _ink(0.92 - 0.2 * i))
            ratio = round(1 / rate)
            _text(p, QRectF(x - 4, base - h - 22, width + 8, 18), f"{ratio} for 1" if ratio > 1 else "1 for 1",
                  12, QFont.Bold, _ink())
            _text(p, QRectF(x - 6, base + 8, width + 12, 16), labels[i], 10.5, colour=QColor(MUTED))
        x = left + (cells - 1) * (width + gap)
        p.setPen(QPen(_ink(0.25), 2, Qt.SolidLine, Qt.RoundCap))
        p.drawLine(QPointF(x, base - 1), QPointF(x + width, base - 1))
        _text(p, QRectF(x - 4, base - 40, width + 8, 18), "nothing", 12, QFont.Bold, QColor(FAINT))
        _text(p, QRectF(x - 6, base + 8, width + 12, 16), labels[-1], 10.5, colour=QColor(MUTED))
        p.setPen(QPen(_ink(0.14), 1))
        p.drawLine(QPointF(left, base + 3), QPointF(right, base + 3))
        _text(p, QRectF(left, base + 28, right - left, 16), "time worked past your goal, as a share of the goal",
              10.5, colour=QColor(FAINT))


class _Streak(QWidget):
    """Five days in a row on target, each adding a tenth."""

    def __init__(self):
        super().__init__()
        self.setFixedHeight(50)

    def paintEvent(self, _event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        n = int(round(vacation.STREAK_MAX / vacation.STREAK_STEP))
        left, right = 24.0, self.width() - 24.0
        y = 20.0
        step = (right - left) / (n - 1)
        p.setPen(QPen(_ink(0.16), 2, Qt.SolidLine, Qt.RoundCap))
        p.drawLine(QPointF(left, y), QPointF(right, y))
        for i in range(n):
            x = left + step * i
            p.setPen(Qt.NoPen)
            p.setBrush(_ink(0.35 + 0.65 * (i + 1) / n))
            p.drawEllipse(QPointF(x, y), 9, 9)
            p.setPen(QPen(QColor("#FFFFFF"), 1.8, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
            p.drawPolyline([QPointF(x - 3.6, y + 0.3), QPointF(x - 1, y + 3), QPointF(x + 3.8, y - 2.6)])
            _text(p, QRectF(x - 30, y + 14, 60, 16), f"+{int(round(vacation.STREAK_STEP * 100 * (i + 1)))}%",
                  11, QFont.DemiBold, _ink(0.8))


# --- the pages -------------------------------------------------------------------------------------------

def _row(left, right, tone=None):
    box = QWidget()
    row = QHBoxLayout(box)
    row.setContentsMargins(0, 0, 0, 0)
    row.setSpacing(10)
    a = QLabel(left)
    a.setStyleSheet(f"color:{INK};font-size:13.5px;background:transparent;")
    b = QLabel(right)
    b.setStyleSheet(f"color:{tone or INK};font-size:13.5px;font-weight:700;background:transparent;")
    b.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
    row.addWidget(a, 1)
    row.addWidget(b)
    return box


def _rule():
    line = QWidget()
    line.setFixedHeight(1)
    line.setStyleSheet(f"background:{_ink(0.08).name(QColor.HexArgb)};")
    return line


def _caption(text):
    label = QLabel(text)
    label.setWordWrap(True)
    label.setStyleSheet(f"color:{MUTED};font-size:13px;background:transparent;")
    return label


def _heading(text):
    label = QLabel(text)
    label.setStyleSheet(f"color:{INK};font-size:15px;font-weight:700;background:transparent;")
    return label


def _score_page():
    page = QWidget()
    layout = QVBoxLayout(page)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(10)
    layout.addWidget(_caption("One number from 0 to 1000. It starts at 500 and is never reset."))
    layout.addWidget(_GradeRail(score_store.score()))
    layout.addWidget(_rule())
    layout.addWidget(_heading("What moves it"))
    over = score_store.OVER_BONUS_MAX
    for text, value, tone in (
        ("A day with all its goals met", "+12", GREEN),
        ("Time beyond the goals", f"up to +{over}", GREEN),
        ("A week or month goal, when it ends", "+20", GREEN),
        ("A review done on time", "+6", GREEN),
        ("A day with none of its goals done", "-18", RED),
        ("A week or month goal left untouched", "-30", RED),
        ("A review left overdue", f"-{score_store.OVERDUE_DAY_COST} a day", RED),
        ("Pausing past your budget to look at other things", "-1 per 3 min", RED),
    ):
        layout.addWidget(_row(text, value, tone))
    layout.addWidget(_caption("Breaks, set-aside problems and rest days never cost anything."))
    return page


def _vacation_page():
    page = QWidget()
    layout = QVBoxLayout(page)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(10)
    layout.addWidget(_caption("Work past a day's goal and the extra is banked as vacation. "
                              "You can spend it to cover a goal later. The further past, the less each minute earns."))
    layout.addWidget(_Ladder())
    example = vacation.ladder(30, 60)
    layout.addWidget(_caption(f"A 60 minute goal, 90 minutes worked: {example:g} minutes of vacation. "
                              f"The most a day can bank is {vacation.most_a_day(60):g}."))
    layout.addWidget(_rule())
    layout.addWidget(_heading("Then the day is adjusted"))
    layout.addWidget(_row("Minutes with violations count half", "x0.5 to x1"))
    layout.addWidget(_row("Minutes under a hard lock", f"+{int(vacation.HARD_BONUS * 100)}%"))
    layout.addWidget(_row("Days in a row on target", f"+{int(vacation.STREAK_STEP * 100)}% each, up to +{int(vacation.STREAK_MAX * 100)}%"))
    layout.addWidget(_Streak())
    layout.addWidget(_row("Once a week has banked a day's worth, the rest earns half", "x0.5"))
    return page


class Explainer(QWidget):
    def __init__(self, page="score"):
        super().__init__(None, Qt.WindowStaysOnTopHint)
        self.setObjectName("PopupBg")
        self.setWindowTitle("How it works")
        self.setFixedWidth(470)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(28, 24, 28, 24)
        outer.setSpacing(14)

        head = QHBoxLayout()
        title = QLabel("How it works")
        title.setStyleSheet(f"color:{INK};font-size:22px;font-weight:800;background:transparent;")
        head.addWidget(title)
        head.addStretch(1)
        outer.addLayout(head)

        switch = QHBoxLayout()
        switch.setSpacing(6)
        group = QButtonGroup(self)
        group.setExclusive(True)
        self._stack = QStackedWidget()
        self._stack.addWidget(_score_page())
        self._stack.addWidget(_vacation_page())
        self._tabs = []
        for index, label in enumerate(("Score", "Vacation")):
            tab = QPushButton(label)
            tab.setCheckable(True)
            tab.setCursor(Qt.PointingHandCursor)
            tab.setStyleSheet(
                f"QPushButton{{background:transparent;color:{MUTED};border:none;border-radius:15px;"
                "padding:7px 16px;font-size:13.5px;font-weight:600;}"
                f"QPushButton:checked{{background:{INK};color:#FFFFFF;}}"
                "QPushButton:hover:!checked{background:rgba(0,0,0,0.05);}")
            tab.clicked.connect(lambda _=False, i=index: self._stack.setCurrentIndex(i))
            group.addButton(tab)
            switch.addWidget(tab)
            self._tabs.append(tab)
        switch.addStretch(1)
        outer.addLayout(switch)
        outer.addWidget(self._stack)
        # The window is as tall as the taller page, so switching never squeezes a drawing.
        self._stack.setMinimumHeight(max(self._stack.widget(i).sizeHint().height() for i in range(self._stack.count())))
        start = 1 if page == "vacation" else 0
        self._tabs[start].setChecked(True)
        self._stack.setCurrentIndex(start)

    def closeEvent(self, event):
        if self in _OPEN:
            _OPEN.remove(self)
        super().closeEvent(event)

    def keyPressEvent(self, event):
        if event.key() == Qt.Key_Escape:
            self.close()
            return
        super().keyPressEvent(event)
