"""Shared look-and-feel for the redesigned tabs: the colours, numerals font, small
formatters and the few widgets (progress ring, one-line elided label, chips, buttons)
that the Tasks, Focus and the other redesigned tabs have in common.

The language: white cards with a hairline border on a soft grey page, ink text, one
accent colour per thing (a task's colour, a subject's colour), and DIN-style numerals
(Bahnschrift) wherever a number is the point of the card.
"""
from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QFont, QFontMetrics, QPainter, QPen
from PySide6.QtWidgets import QLabel, QPushButton, QSizePolicy, QWidget

from qt_ui.day_layout import contrasting_text_color

INK = "#1B2230"
MUTED = "#667085"
FAINT = "#98A2B3"
TEXT_FONT = "Segoe UI"
# DIN-style numerals for the ring and clocks (falls back to Segoe UI).
NUM_FONTS = ["Bahnschrift SemiBold", "Segoe UI Semibold", "Segoe UI"]
# The same face for a QLabel: it has to come from the label's stylesheet, because the
# app stylesheet sets a font on every label and wins over QWidget.setFont().
NUM_CSS = "font-family:'Bahnschrift SemiBold','Segoe UI Semibold','Segoe UI';"

GREEN = ("#E3F6E8", "#157F3B")
AMBER = ("#FEF0C7", "#93370D")
TEAL = ("#D5F5F3", "#0E7C75")
GREY = ("#EEF0F4", "#5B6472")
RED = ("#FEE4E2", "#B42318")
BREAK_COLOR = "#14B8A6"


def format_minutes(total_minutes):
    total_minutes = int(round(total_minutes))
    hours, minutes = divmod(total_minutes, 60)
    if hours:
        return f"{hours}h {minutes}m"
    return f"{minutes}m"


def format_amount(total_minutes):
    """Like format_minutes, but a whole number of hours has no "0m" ("10h", not "10h 0m")."""
    total_minutes = int(round(total_minutes))
    hours, minutes = divmod(total_minutes, 60)
    if hours and not minutes:
        return f"{hours}h"
    return format_minutes(total_minutes)


def format_clock(total_seconds):
    total_seconds = max(0, int(total_seconds))
    minutes, seconds = divmod(total_seconds, 60)
    if minutes >= 100:
        hours, minutes = divmod(minutes, 60)
        return f"{hours}:{minutes:02d}:{seconds:02d}"
    return f"{minutes:02d}:{seconds:02d}"


def safe_color(value):
    color = QColor(value if isinstance(value, str) else "")
    return color.name() if color.isValid() else "#5B8DEF"


def tint(color, alpha):
    c = QColor(color)
    return f"rgba({c.red()},{c.green()},{c.blue()},{alpha})"


def darker(color, factor=0.62):
    c = QColor(color)
    return QColor(int(c.red() * factor), int(c.green() * factor), int(c.blue() * factor)).name()


class Ring(QWidget):
    """The progress ring: a faint full track, a rounded-end arc, big numerals inside."""

    def __init__(self, size=124):
        super().__init__()
        self._color = QColor("#5B8DEF")
        self._fraction = 0.0
        self._big = ""
        self._small = ""
        self._dim = False
        self.setFixedSize(size, size)

    def set_state(self, color, fraction, big, small, dim=False):
        fraction = round(max(0.0, fraction), 4)
        if (QColor(color).name(), fraction, big, small, dim) == (
                self._color.name(), self._fraction, self._big, self._small, self._dim):
            return
        self._color, self._fraction, self._big, self._small, self._dim = QColor(color), fraction, big, small, dim
        self.update()

    def paintEvent(self, _event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        width = 11
        rect = QRectF(width / 2 + 2, width / 2 + 2, self.width() - width - 4, self.height() - width - 4)
        track = QColor(self._color)
        track.setAlphaF(0.15)
        p.setPen(QPen(track, width, Qt.SolidLine, Qt.RoundCap))
        p.drawEllipse(rect)
        if self._fraction > 0:
            arc = QColor(self._color)
            if self._dim:
                arc.setAlphaF(0.4)
            p.setPen(QPen(arc, width, Qt.SolidLine, Qt.RoundCap))
            span = min(self._fraction, 1.0) * 360
            if span >= 359.5:
                p.drawEllipse(rect)
            else:
                p.drawArc(rect, 90 * 16, int(-span * 16))
        big = QFont()
        big.setFamilies(NUM_FONTS)
        big.setPixelSize(26 if len(self._big) < 6 else 22)
        p.setFont(big)
        p.setPen(QColor(FAINT if self._dim else INK))
        p.drawText(QRectF(0, self.height() / 2 - 22, self.width(), 30), Qt.AlignCenter, self._big)
        small = QFont(TEXT_FONT)
        small.setPixelSize(11)
        p.setFont(small)
        p.setPen(QColor(FAINT if self._dim else MUTED))
        p.drawText(QRectF(0, self.height() / 2 + 6, self.width(), 18), Qt.AlignCenter, self._small)


class ElidedLabel(QLabel):
    """One line that ends in an ellipsis instead of pushing the card wider."""

    def __init__(self, text=""):
        super().__init__(text)
        self._full = text
        self.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        self.setMinimumWidth(40)

    def setText(self, text):
        self._full = text or ""
        self.setToolTip(self._full)
        super().setText(self._full)
        self.update()

    def paintEvent(self, _event):
        p = QPainter(self)
        p.setFont(self.font())
        p.setPen(self.palette().color(self.foregroundRole()))
        shown = QFontMetrics(self.font()).elidedText(self._full, Qt.ElideRight, self.width())
        p.drawText(self.rect(), Qt.AlignLeft | Qt.AlignVCenter, shown)


def chip(text, color=None, solid=False, tone=None):
    label = QLabel(text)
    if solid:
        css = f"background:{INK};color:#FFFFFF;"
    elif tone:
        css = f"background:{tone[0]};color:{tone[1]};"
    elif color:
        css = f"background:{tint(color, 0.14)};color:{darker(color, .55)};"
    else:
        css = f"background:#F2F4F7;color:{MUTED};"
    label.setStyleSheet(css + "border-radius:10px;padding:2px 10px;font-size:11.5px;font-weight:600;")
    return label


def note(text="", color=MUTED, size=12.5):
    label = QLabel(text)
    label.setWordWrap(True)
    label.setStyleSheet(f"font-size:{size}px;color:{color};background:transparent;")
    return label


def primary_style(color):
    return (
        f"QPushButton{{background:{color};color:{contrasting_text_color(color)};border:none;"
        f"border-radius:12px;font-size:14px;font-weight:600;padding:0 18px;}}"
        f"QPushButton:hover{{background:{darker(color, .9)};}}"
        "QPushButton:disabled{background:#EEF0F4;color:#A3ABB8;}")


SECONDARY_STYLE = (
    f"QPushButton{{background:#F2F4F7;color:{INK};border:none;border-radius:12px;"
    "font-size:13.5px;font-weight:600;padding:0 16px;}"
    "QPushButton:hover{background:#E7EAF0;}")


def accent_style(color):
    return (
        f"QPushButton{{background:{tint(color, .12)};color:{darker(color, .55)};border:none;"
        "border-radius:12px;font-size:13.5px;font-weight:600;padding:0 16px;}"
        f"QPushButton:hover{{background:{tint(color, .2)};}}")


DANGER_STYLE = (
    "QPushButton{background:#FEF3F2;color:#B42318;border:none;border-radius:12px;"
    "font-size:13.5px;font-weight:600;padding:0 20px;}"
    "QPushButton:hover{background:#FEE4E2;}")

LINK_STYLE = (
    f"QPushButton{{border:none;background:transparent;color:{MUTED};font-size:12.5px;padding:0;text-align:left;}}"
    f"QPushButton:hover:enabled{{color:{INK};text-decoration:underline;}}"
    f"QPushButton:disabled{{color:{FAINT};}}")


def button(text, style, height=40):
    b = QPushButton(text)
    b.setCursor(Qt.PointingHandCursor)
    b.setFixedHeight(height)
    b.setStyleSheet(style)
    return b


def link_button(text):
    b = QPushButton(text)
    b.setFlat(True)
    b.setCursor(Qt.PointingHandCursor)
    b.setStyleSheet(LINK_STYLE)
    return b
