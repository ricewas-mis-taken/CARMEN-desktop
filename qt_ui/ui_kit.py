"""Shared look-and-feel for the redesigned tabs: the colours, numerals font, small
formatters and the few widgets (progress ring, one-line elided label, chips, buttons)
that the Tasks, Focus and the other redesigned tabs have in common.

The language: white cards with a hairline border on a soft grey page, ink text, one
accent colour per thing (a task's colour, a subject's colour), and DIN-style numerals
(Bahnschrift) wherever a number is the point of the card.
"""
import os
import tempfile

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QFontMetrics, QImage, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import QLabel, QPushButton, QSizePolicy, QWidget

from qt_ui.day_layout import contrasting_text_color

import theme

_THEME = theme.startup_colours()
INK = _THEME["ink"]
MUTED = _THEME["muted"]
FAINT = _THEME["faint"]
ACCENT = _THEME["accent"]
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

    def __init__(self, text="", natural=False):
        """natural=True asks for the text's own width (and only gives it up when the
        row is too tight); the default takes whatever room is left over."""
        super().__init__(text)
        self._full = text
        self._natural = natural
        self.setSizePolicy(QSizePolicy.Preferred if natural else QSizePolicy.Ignored, QSizePolicy.Preferred)
        self.setMinimumWidth(40)

    def sizeHint(self):
        if self._natural:
            hint = super().sizeHint()
            hint.setWidth(QFontMetrics(self.font()).horizontalAdvance(self._full) + 2)
            return hint
        return super().sizeHint()

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


class FitButton(QPushButton):
    """A button that never shrinks below its own text, however tight the layout
    gets (a plain QPushButton's minimum can be smaller than its label under a
    stylesheet's padding, so rows of them used to collapse to blank slivers)."""

    def minimumSizeHint(self):
        return self.sizeHint()


def button(text, style, height=40):
    b = FitButton(text)
    b.setCursor(Qt.PointingHandCursor)
    b.setFixedHeight(height)
    b.setStyleSheet(style)
    return b


def link_button(text):
    b = FitButton(text)
    b.setFlat(True)
    b.setCursor(Qt.PointingHandCursor)
    b.setStyleSheet(LINK_STYLE)
    return b


# --- the checkmark ----------------------------------------------------------------------
#
# Every checked box in the app is green with a white tick. A stylesheet can colour the
# box but needs a picture for the tick, so one is drawn here once (and a 2x copy for
# sharp screens) into the temp folder, and the stylesheets refer to it by path.

CHECK_GREEN = "#2F9E44"
CHECK_GREEN_HOVER = "#278A3B"


def _draw_check(size):
    image = QImage(size, size, QImage.Format_ARGB32)
    image.fill(Qt.transparent)
    p = QPainter(image)
    p.setRenderHint(QPainter.Antialiasing)
    scale = size / 16.0
    tick = QPainterPath(QPointF(3.6 * scale, 8.4 * scale))
    tick.lineTo(6.7 * scale, 11.5 * scale)
    tick.lineTo(12.4 * scale, 4.9 * scale)
    p.setPen(QPen(QColor("#FFFFFF"), 2.0 * scale, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
    p.drawPath(tick)
    p.end()
    return image


def check_icon_path():
    """Path (forward slashes) of the white tick picture, drawn on first use."""
    folder = os.path.join(tempfile.gettempdir(), "carmen-focus-ui")
    path = os.path.join(folder, "check-white.png")
    try:
        os.makedirs(folder, exist_ok=True)
        for size, name in ((16, "check-white.png"), (32, "check-white@2x.png")):
            target = os.path.join(folder, name)
            if not os.path.exists(target):
                _draw_check(size).save(target)
    except OSError:
        pass
    return path.replace("\\", "/")


def with_check_icon(stylesheet):
    """The stylesheet with its @CHECK@ placeholder pointing at the tick picture."""
    return stylesheet.replace("@CHECK@", check_icon_path())


# --- popups -----------------------------------------------------------------------------
#
# Every popup shares one look (styles.qss, "QWidget#PopupBg"). Labels pick their style from a
# "role" property, set here so a dialog never has to carry its own colours.

def role_label(text, role, wrap=False):
    label = QLabel(text)
    label.setProperty("role", role)
    label.setWordWrap(wrap)
    return label


def dialog_title(title, subtitle=None):
    """The heading block at the top of a popup: a title, and a quieter line saying what it is for."""
    from PySide6.QtWidgets import QVBoxLayout
    holder = QWidget()
    holder.setObjectName("DialogHeader")
    box = QVBoxLayout(holder)
    box.setContentsMargins(0, 0, 0, 4)
    box.setSpacing(3)
    box.addWidget(role_label(title, "title"))
    if subtitle:
        box.addWidget(role_label(subtitle, "subtitle", wrap=True))
    return holder


def field_label(text, hint=None):
    """The small grey label above a field (and, if given, a quieter hint after it)."""
    return role_label(text if not hint else f"{text}  ·  {hint}", "field")


def accent(button_):
    """Marks a button as the popup's main action (ink fill)."""
    button_.setProperty("class", "AccentButton")
    return button_


def danger(button_):
    button_.setProperty("class", "DangerButton")
    return button_


# --- sidebar icons ------------------------------------------------------------------------
#
# Line icons for the sidebar, drawn here (no image files): one 24px picture per page, in a quiet
# grey for the page you are not on and white for the one you are on.

def _nav_pixmap(key, color):
    ratio = 2
    image = QImage(24 * ratio, 24 * ratio, QImage.Format_ARGB32)
    image.fill(Qt.transparent)
    image.setDevicePixelRatio(ratio)
    p = QPainter(image)
    p.setRenderHint(QPainter.Antialiasing)
    pen = QPen(QColor(color), 1.7, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin)
    p.setPen(pen)
    p.setBrush(Qt.NoBrush)
    if key == "home":        # a house
        roof = QPainterPath(QPointF(4.5, 11.5))
        roof.lineTo(12, 5)
        roof.lineTo(19.5, 11.5)
        p.drawPath(roof)
        walls = QPainterPath(QPointF(6.5, 10.2))
        walls.lineTo(6.5, 19)
        walls.lineTo(17.5, 19)
        walls.lineTo(17.5, 10.2)
        p.drawPath(walls)
        p.drawLine(QPointF(10.2, 19), QPointF(10.2, 14.5))
        p.drawLine(QPointF(10.2, 14.5), QPointF(13.8, 14.5))
        p.drawLine(QPointF(13.8, 14.5), QPointF(13.8, 19))
    elif key == "focus":       # a target: ring, ring, dot
        p.drawEllipse(QPointF(12, 12), 8, 8)
        p.drawEllipse(QPointF(12, 12), 3.6, 3.6)
    elif key == "tasks":     # a tick in a ring
        p.drawEllipse(QPointF(12, 12), 8.2, 8.2)
        tick = QPainterPath(QPointF(8.2, 12.3))
        tick.lineTo(10.9, 15)
        tick.lineTo(15.8, 9.2)
        p.drawPath(tick)
    elif key == "board":     # three columns
        for x, h in ((4.5, 14), (10, 9), (15.5, 12)):
            p.drawRoundedRect(QRectF(x, 5, 4.5, h), 1.6, 1.6)
    elif key == "review":    # two arrows chasing each other
        p.drawArc(QRectF(5, 5, 14, 14), 30 * 16, 130 * 16)
        p.drawArc(QRectF(5, 5, 14, 14), 210 * 16, 130 * 16)
        p.drawLine(QPointF(17.7, 4.8), QPointF(18.9, 8.8))
        p.drawLine(QPointF(18.9, 8.8), QPointF(14.9, 9.4))
        p.drawLine(QPointF(6.3, 19.2), QPointF(5.1, 15.2))
        p.drawLine(QPointF(5.1, 15.2), QPointF(9.1, 14.6))
    elif key == "scribe":    # a pencil
        body = QPainterPath(QPointF(5, 19))
        body.lineTo(6, 14.6)
        body.lineTo(15.4, 5.2)
        body.lineTo(18.8, 8.6)
        body.lineTo(9.4, 18)
        body.closeSubpath()
        p.drawPath(body)
        p.drawLine(QPointF(13.6, 7), QPointF(17, 10.4))
    elif key == "screentime":  # a clock
        p.drawEllipse(QPointF(12, 12), 8, 8)
        p.drawLine(QPointF(12, 7.5), QPointF(12, 12))
        p.drawLine(QPointF(12, 12), QPointF(15.2, 13.8))
    elif key == "history":   # a list, newest first
        for y in (7, 12, 17):
            p.drawLine(QPointF(9.5, y), QPointF(19, y))
            p.setBrush(QColor(color))
            p.drawEllipse(QPointF(5.6, y), 1.1, 1.1)
            p.setBrush(Qt.NoBrush)
    p.end()
    return QPixmap.fromImage(image)


def nav_icon(key):
    from PySide6.QtGui import QIcon
    icon = QIcon()
    icon.addPixmap(_nav_pixmap(key, "#9AA3B5"), QIcon.Normal, QIcon.Off)
    icon.addPixmap(_nav_pixmap(key, "#FFFFFF"), QIcon.Normal, QIcon.On)
    return icon


class LogoMark(QWidget):
    """The Carmen ring: a ring with a gap in it (the same mark as the browser extension's)."""

    def __init__(self, size=22, color="#8EA0FF"):
        super().__init__()
        self._color = QColor(color)
        self.setFixedSize(size, size)

    def paintEvent(self, _event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        width = self.width() * 0.2
        rect = QRectF(width / 2, width / 2, self.width() - width, self.height() - width)
        p.setPen(QPen(self._color, width, Qt.SolidLine, Qt.RoundCap))
        p.drawArc(rect, 10 * 16, 292 * 16)
