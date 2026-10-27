"""Lettering that writes itself: a real font's outlines are traced by a glowing pen, then filled.

Spacing and kerning come from the font, so the letters are even; the animation is the same
for every string. `progress` runs 0..1: letters start one after another (staggered), each
outline is drawn with a bright pen tip, and the fill fades in as the last letters finish.
"""
import math

from PySide6.QtCore import QPointF, Qt
from PySide6.QtGui import QBrush, QColor, QFont, QFontMetricsF, QLinearGradient, QPainterPath, QPen, QRadialGradient

FAMILIES = ["Bahnschrift", "Segoe UI Variable Display", "Segoe UI"]
SCRIPT_FAMILIES = ["Segoe Script", "Ink Free", "Segoe Print", "Segoe UI"]


def make_font(pixels, tracking=0.0, light=True, script=False):
    font = QFont()
    font.setFamilies(SCRIPT_FAMILIES if script else FAMILIES)
    font.setPixelSize(int(pixels))
    font.setWeight(QFont.Light if light else QFont.Normal)
    font.setLetterSpacing(QFont.AbsoluteSpacing, tracking)
    return font


class Lettering:
    """`text` laid out with `font`: one outline per letter, measured once."""

    def __init__(self, text, font):
        metrics = QFontMetricsF(font)
        self.letters = []                  # [(QPainterPath, [polygon points], length)]
        x = 0.0
        for ch in text:
            path = QPainterPath()
            if ch.strip():
                path.addText(QPointF(x, 0), font, ch)
            polys = [[(pt.x(), pt.y()) for pt in poly] for poly in path.toSubpathPolygons()]
            length = sum(_plen(poly) for poly in polys)
            self.letters.append((path, polys, length))
            x += metrics.horizontalAdvance(ch) + font.letterSpacing()
        self.width = max(0.0, x - font.letterSpacing())
        self.ascent = metrics.ascent()
        self.height = metrics.ascent() + metrics.descent()


def _plen(poly):
    return sum(math.dist(a, b) for a, b in zip(poly, poly[1:]))


def _ease(t):
    t = max(0.0, min(1.0, t))
    return t * t * (3 - 2 * t)


def draw(painter, lettering, x, baseline, colour, progress=1.0, stroke=2.0, stagger=0.6):
    """Draws `lettering` with its baseline-left at (x, baseline)."""
    count = max(1, len(lettering.letters))
    span = 1.0 / (1.0 + stagger * (count - 1))
    done_fill = 1.0 if progress >= 1 else _ease((progress - 0.62) / 0.38)
    base = QColor(colour)
    painter.save()
    painter.translate(x, baseline)
    pen = QPen(base, stroke, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin)
    for i, (path, polys, length) in enumerate(lettering.letters):
        if not polys:
            continue
        local = 1.0 if progress >= 1 else _ease((progress - i * stagger * span) / span)
        if local < 1.0 and local > 0.0:
            _trace(painter, polys, length, local, pen, base)
        if done_fill > 0:
            fill = QColor(base)
            fill.setAlphaF(base.alphaF() * done_fill * (1.0 if local >= 1.0 else 0.0))
            painter.setPen(Qt.NoPen)
            painter.setBrush(fill)
            painter.drawPath(path)
        elif local >= 1.0:
            painter.setPen(pen)
            painter.setBrush(Qt.NoBrush)
            painter.drawPath(path)
        if local >= 1.0 and done_fill < 1.0:
            outline = QColor(base)
            outline.setAlphaF(base.alphaF() * (1.0 - done_fill))
            painter.setPen(QPen(outline, stroke, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
            painter.setBrush(Qt.NoBrush)
            painter.drawPath(path)
    painter.restore()


def _trace(painter, polys, total, fraction, pen, colour):
    """Part-way through one letter: the outline so far, and a glowing tip where the pen is."""
    painter.setPen(pen)
    painter.setBrush(Qt.NoBrush)
    target = total * fraction
    run = 0.0
    tip = None
    for poly in polys:
        length = _plen(poly)
        if length <= 0:
            continue
        take = min(length, target - run)
        if take <= 0:
            break
        path = QPainterPath(QPointF(*poly[0]))
        used = 0.0
        for a, b in zip(poly, poly[1:]):
            seg = math.dist(a, b)
            if seg == 0:
                continue
            step = min(seg, take - used)
            if step <= 0:
                break
            t = step / seg
            tip = (a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t)
            path.lineTo(QPointF(*tip))
            used += step
        painter.drawPath(path)
        run += take
    if tip is not None:
        glow = QRadialGradient(QPointF(*tip), 16)
        glow.setColorAt(0, QColor(255, 255, 255, 230))
        glow.setColorAt(0.35, QColor(214, 190, 255, 110))
        glow.setColorAt(1, QColor(190, 160, 255, 0))
        painter.setPen(Qt.NoPen)
        painter.setBrush(glow)
        painter.drawEllipse(QPointF(*tip), 16, 16)


def draw_signature(painter, lettering, x, baseline, colour, progress=1.0, stroke=2.0):
    """Handwriting, the way a pen would: the ink is revealed left to right through a soft edge,
    a thin outline runs a little ahead of the fill, and a glowing nib rides the leading edge."""
    width = max(1.0, lettering.width)
    full = QPainterPath()
    for path, _polys, _length in lettering.letters:
        full.addPath(path)
    base = QColor(colour)
    painter.save()
    painter.translate(x, baseline)
    if progress >= 1:
        painter.setPen(Qt.NoPen)
        painter.setBrush(base)
        painter.drawPath(full)
        painter.restore()
        return
    fade = lettering.height * 0.55
    edge = _ease(progress) * (width + fade)           # where the nib is, along the word

    def ramp(lead, softness):
        grad = QLinearGradient(0, 0, width, 0)
        points = [(0.0, 1.0), (max(0.0, min(1.0, (lead - softness) / width)), 1.0),
                  (max(0.0, min(1.0, lead / width)), 0.0), (1.0, 0.0)]
        last = -1.0
        for pos, alpha in points:
            pos = max(pos, last)
            c = QColor(base)
            c.setAlphaF(base.alphaF() * alpha)
            grad.setColorAt(pos, c)
            last = pos
        return grad

    painter.setPen(Qt.NoPen)
    painter.setBrush(QBrush(ramp(edge, fade)))
    painter.drawPath(full)
    painter.setBrush(Qt.NoBrush)
    painter.setPen(QPen(QBrush(ramp(edge + fade * 0.9, fade * 0.9)), max(1.0, stroke * 0.6), Qt.SolidLine,
                        Qt.RoundCap, Qt.RoundJoin))
    painter.drawPath(full)
    if 0 < progress < 1:
        nib = QPointF(min(width, edge - fade * 0.25), -lettering.ascent * 0.38)
        glow = QRadialGradient(nib, 22)
        glow.setColorAt(0, QColor(255, 255, 255, 235))
        glow.setColorAt(0.3, QColor(225, 205, 255, 120))
        glow.setColorAt(1, QColor(190, 160, 255, 0))
        painter.setPen(Qt.NoPen)
        painter.setBrush(glow)
        painter.drawEllipse(nib, 22, 22)
    painter.restore()
