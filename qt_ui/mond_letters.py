"""Mond: a small geometric stroke alphabet, drawn with the painter so it can write itself on.

Wide, even strokes with flat ends and sharp joins, no curves except the round letters
(O, D, C, U). Only the letters Carmen needs (weekday names and its own name). Every glyph is
a list of polylines in a box that is `width` wide and 1.0 tall (y grows downward), so any
size, any letter spacing and a draw-on animation come from the same data.
"""
import math

from PySide6.QtCore import QPointF, Qt
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen

STROKE = 0.115          # stroke width as a fraction of the letter height


def _arc(cx, cy, rx, ry, start, end, steps=28):
    return [(cx + rx * math.cos(math.radians(a)), cy + ry * math.sin(math.radians(a)))
            for a in (start + (end - start) * i / steps for i in range(steps + 1))]


GLYPHS = {
    " ": (0.45, []),
    "M": (0.85, [[(0, 1), (0, 0), (0.425, 0.62), (0.85, 0), (0.85, 1)]]),
    "O": (0.82, [_arc(0.41, 0.5, 0.41, 0.5, -90, 270, 44)]),
    "N": (0.72, [[(0, 1), (0, 0), (0.72, 1), (0.72, 0)]]),
    "D": (0.78, [[(0, 0), (0, 1), (0.28, 1)] + _arc(0.28, 0.5, 0.5, 0.5, 90, -90, 24) + [(0, 0)]]),
    "A": (0.8, [[(0, 1), (0.4, 0), (0.8, 1)], [(0.16, 0.7), (0.64, 0.7)]]),
    "Y": (0.76, [[(0, 0), (0.38, 0.52), (0.76, 0)], [(0.38, 0.52), (0.38, 1)]]),
    "T": (0.76, [[(0, 0), (0.76, 0)], [(0.38, 0), (0.38, 1)]]),
    "U": (0.72, [[(0, 0), (0, 0.64)] + _arc(0.36, 0.64, 0.36, 0.36, 180, 0, 20) + [(0.72, 0)]]),
    "E": (0.66, [[(0.66, 0), (0, 0), (0, 1), (0.66, 1)], [(0, 0.5), (0.5, 0.5)]]),
    "F": (0.66, [[(0.66, 0), (0, 0), (0, 1)], [(0, 0.5), (0.5, 0.5)]]),
    "S": (0.68, [[(0.68, 0.14), (0.54, 0), (0.14, 0), (0, 0.14), (0, 0.36), (0.14, 0.5),
                  (0.54, 0.5), (0.68, 0.64), (0.68, 0.86), (0.54, 1), (0.14, 1), (0, 0.86)]]),
    "W": (1.1, [[(0, 0), (0.22, 1), (0.55, 0.34), (0.88, 1), (1.1, 0)]]),
    "H": (0.72, [[(0, 0), (0, 1)], [(0.72, 0), (0.72, 1)], [(0, 0.5), (0.72, 0.5)]]),
    "R": (0.7, [[(0, 1), (0, 0), (0.56, 0), (0.7, 0.14), (0.7, 0.36), (0.56, 0.5), (0, 0.5)],
                [(0.36, 0.5), (0.7, 1)]]),
    "I": (0.12, [[(0.06, 0), (0.06, 1)]]),
    "C": (0.8, [_arc(0.42, 0.5, 0.42, 0.5, -42, -318, 40)]),
}
_GAP = 0.34            # space between letters, as a fraction of the height


def _length(points):
    return sum(math.dist(a, b) for a, b in zip(points, points[1:]))


def text_width(text, height, tracking=1.0):
    """Width of `text` when drawn `height` tall; `tracking` scales the space between letters."""
    total = 0.0
    for ch in text.upper():
        total += GLYPHS.get(ch, GLYPHS[" "])[0] + _GAP * tracking
    return max(0.0, (total - _GAP * tracking) * height)


def _polyline_to(painter, points, x, y, h, fraction):
    """Draws the first `fraction` (0..1) of a polyline as one stroke (so joins and curves stay clean)."""
    if fraction <= 0 or len(points) < 2:
        return
    total = _length(points) * fraction
    run = 0.0
    path = QPainterPath(QPointF(x + points[0][0] * h, y + points[0][1] * h))
    for a, b in zip(points, points[1:]):
        seg = math.dist(a, b)
        if seg == 0:
            continue
        take = min(seg, total - run)
        if take <= 0:
            break
        t = take / seg
        path.lineTo(QPointF(x + (a[0] + (b[0] - a[0]) * t) * h, y + (a[1] + (b[1] - a[1]) * t) * h))
        run += take
    painter.drawPath(path)


def _ease(t):
    t = max(0.0, min(1.0, t))
    return 1 - (1 - t) ** 3


def draw_text(painter, text, x, y, height, colour, tracking=1.0, progress=1.0, stagger=0.55):
    """Draws `text` with its top-left at (x, y). `progress` (0..1) writes the letters on one after
    another: each starts a little after the one before and runs until `progress` reaches 1."""
    pen = QPen(QColor(colour), max(1.0, STROKE * height), Qt.SolidLine, Qt.FlatCap, Qt.MiterJoin)
    pen.setMiterLimit(1.3)         # sharp corners are bevelled instead of spiking out
    painter.setPen(pen)
    painter.setBrush(Qt.NoBrush)
    letters = text.upper()
    count = max(1, len(letters))
    span = 1.0 / (1.0 + stagger * (count - 1))          # how much of the timeline one letter takes
    cursor = x
    for i, ch in enumerate(letters):
        width, strokes = GLYPHS.get(ch, GLYPHS[" "])
        local = _ease((progress - i * stagger * span) / span) if progress < 1 else 1.0
        n = max(1, len(strokes))
        for k, points in enumerate(strokes):
            part = max(0.0, min(1.0, local * n - k))
            _polyline_to(painter, points, cursor, y, height, part)
        cursor += (width + _GAP * tracking) * height
