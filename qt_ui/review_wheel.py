"""The review wheel window: a big coloured wheel of problem names you can flick or spin.

The slice sizes come from review_wheel.weight. The winner is drawn by weight first and the
wheel is then turned so the pointer stops inside that slice, so a bigger slice really is
likelier, and the flick only decides how long and how dramatic the spin looks."""
import math
import random

from PySide6.QtCore import QEasingCurve, QPointF, QRectF, Qt, QVariantAnimation
from PySide6.QtGui import QColor, QFont, QFontMetrics, QPainter, QPen, QPolygonF
from PySide6.QtWidgets import QHBoxLayout, QPushButton, QVBoxLayout, QWidget

import review_wheel
from qt_ui.ui_kit import accent, dialog_title, role_label

COLOURS = ["#F5C242", "#4F8EF7", "#E8576B", "#43B581", "#9B6BF2", "#F2894B",
           "#2DB7C9", "#D9589B", "#8BC34A", "#5C6BC0", "#E0A526", "#26A69A"]
_OPEN = []


class WheelWidget(QWidget):
    """The wheel itself. Names only, one colour per slice, a pointer at the top."""

    def __init__(self, names, weights, on_landed=None):
        super().__init__()
        self.names, self.weights = list(names), [float(w) for w in weights]
        self._on_landed = on_landed
        self._angle = 0.0                 # degrees the wheel has turned clockwise
        self._winner = None
        self._anim = None
        self._drag = None                 # (grab angle, wheel angle at grab, [(time, angle)])
        self.setMinimumSize(380, 380)
        self.setCursor(Qt.OpenHandCursor)

    # ---- geometry ----
    def _bounds(self):
        side = min(self.width(), self.height()) - 36
        return QRectF((self.width() - side) / 2, (self.height() - side) / 2 + 12, side, side)

    def slices(self):
        """[(start, sweep)] in degrees clockwise from the top, before the wheel is turned."""
        total = sum(self.weights) or 1.0
        out, at = [], 0.0
        for w in self.weights:
            out.append((at, 360.0 * w / total))
            at += 360.0 * w / total
        return out

    def slice_under_pointer(self):
        at = (-self._angle) % 360.0
        for i, (start, sweep) in enumerate(self.slices()):
            if start <= at < start + sweep:
                return i
        return len(self.names) - 1

    # ---- painting ----
    def paintEvent(self, _event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        box = self._bounds()
        centre, radius = box.center(), box.width() / 2
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(0, 0, 0, 28))
        p.drawEllipse(box.translated(0, 5))
        slices = self.slices()
        for i, (start, sweep) in enumerate(slices):
            colour = QColor(COLOURS[i % len(COLOURS)])
            if self._winner is not None and i != self._winner:
                colour.setAlpha(90)
            p.setPen(QPen(QColor("#FFFFFF"), 2))
            p.setBrush(colour)
            p.drawPie(box, int((90 - (start + self._angle)) * 16), int(-sweep * 16))
        for i, (start, sweep) in enumerate(slices):
            self._draw_name(p, i, start, sweep, centre, radius)
        p.setPen(QPen(QColor("#FFFFFF"), 5))
        p.setBrush(Qt.NoBrush)
        p.drawEllipse(box)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor("#FFFFFF"))
        p.drawEllipse(centre, radius * 0.13, radius * 0.13)
        p.setBrush(QColor("#1F2430"))
        p.drawEllipse(centre, radius * 0.06, radius * 0.06)
        # the pointer flicks aside a little as each slice edge goes past it
        kick = 0.0
        if self.spinning():
            edge = min(((s + self._angle) % 360.0) for s, _ in slices)
            kick = max(0.0, 9.0 - edge * 2.5) if edge < 3.6 else 0.0
        p.save()
        p.translate(centre.x(), box.top() - 6)
        p.rotate(-kick)
        p.setPen(QPen(QColor("#FFFFFF"), 2))
        p.setBrush(QColor("#1F2430"))
        p.drawPolygon(QPolygonF([QPointF(-13, -10), QPointF(13, -10), QPointF(0, 24)]))
        p.restore()

    def _draw_name(self, p, index, start, sweep, centre, radius):
        if sweep < 4.5:
            return
        mid = start + sweep / 2 + self._angle
        chord = 2 * radius * 0.6 * math.sin(math.radians(min(sweep, 170)) / 2)
        font = QFont(self.font())
        font.setBold(True)
        font.setPixelSize(int(max(9, min(16, chord * 0.7))))
        p.save()
        p.translate(centre)
        p.rotate(mid - 90)
        p.setFont(font)
        p.setPen(QColor(255, 255, 255, 255 if self._winner in (None, index) else 120))
        left, right = radius * 0.2, radius * 0.93
        text = QFontMetrics(font).elidedText(self.names[index], Qt.ElideRight, int(right - left))
        if 180.0 < mid % 360.0 < 360.0:        # on the left half: turn the name the right way up
            p.rotate(180)
            p.drawText(QRectF(-right, -radius, right - left, radius * 2), Qt.AlignVCenter | Qt.AlignLeft, text)
        else:
            p.drawText(QRectF(left, -radius, right - left, radius * 2), Qt.AlignVCenter | Qt.AlignRight, text)
        p.restore()

    # ---- spinning ----
    def spinning(self):
        return self._anim is not None and self._anim.state() == QVariantAnimation.Running

    def spin(self, speed=None, direction=1, rng=random):
        """Turn the wheel. `speed` (degrees a second, from a flick) only lengthens or shortens the spin."""
        if self.spinning() or not self.names:
            return
        index, position = review_wheel.spin(self.weights, rng)
        start, sweep = self.slices()[index]
        target = (-(start + sweep * position)) % 360.0
        turns = rng.uniform(4, 6) if speed is None else min(9, 3 + speed / 500)
        step = direction or 1
        end = self._angle + step * 360.0 * turns
        end += ((target - end) % 360.0) if step > 0 else -((end - target) % 360.0)
        self._winner = None
        self._anim = QVariantAnimation(self)
        self._anim.setStartValue(float(self._angle))
        self._anim.setEndValue(float(end))
        self._anim.setDuration(int(3200 + 450 * turns))
        self._anim.setEasingCurve(QEasingCurve.OutQuart)
        self._anim.valueChanged.connect(self._turned)
        self._anim.finished.connect(lambda i=index: self._landed(i))
        self._anim.start()
        self.setCursor(Qt.ArrowCursor)

    def _turned(self, value):
        self._angle = float(value)
        self.update()

    def _landed(self, index):
        self._angle %= 360.0
        self._winner = index
        self.setCursor(Qt.OpenHandCursor)
        self.update()
        if self._on_landed:
            self._on_landed(index)

    # ---- flicking by hand ----
    def _grab_angle(self, pos):
        c = self._bounds().center()
        return math.degrees(math.atan2(pos.x() - c.x(), -(pos.y() - c.y())))

    def mousePressEvent(self, event):
        if event.button() != Qt.LeftButton or self.spinning() or self._winner is not None:
            return
        self._drag = (self._grab_angle(event.position()), self._angle, [(event.timestamp(), self._angle)])
        self.setCursor(Qt.ClosedHandCursor)

    def mouseMoveEvent(self, event):
        if self._drag is None:
            return
        grab, base, trail = self._drag
        self._angle = base + (self._grab_angle(event.position()) - grab)
        trail.append((event.timestamp(), self._angle))
        del trail[:-6]
        self.update()

    def mouseReleaseEvent(self, event):
        if self._drag is None:
            return
        trail = self._drag[2]
        self._drag = None
        self.setCursor(Qt.OpenHandCursor)
        (t0, a0), (t1, a1) = trail[0], trail[-1]
        speed = abs(a1 - a0) / max(0.001, (t1 - t0) / 1000.0)
        if speed > 240:
            self.spin(speed=speed, direction=1 if a1 >= a0 else -1)


class WheelDialog(QWidget):
    """Opens on the wheel. When it stops, the winner is fated and can be started from here."""

    def __init__(self, items, on_start):
        super().__init__(None, Qt.WindowStaysOnTopHint)
        self.setObjectName("PopupBg")
        self.setWindowTitle("Spin the wheel")
        self.resize(480, 640)
        self._problems = [problem for problem, _ in items]
        self._on_start = on_start
        self._winner = None
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 22, 24, 20)
        layout.setSpacing(8)
        layout.addWidget(dialog_title("Spin the wheel"))
        self.wheel = WheelWidget([p["name"] for p in self._problems], [w for _, w in items], self._landed)
        layout.addWidget(self.wheel, 1)
        self._result = role_label("", "subtitle", wrap=True)
        self._result.setAlignment(Qt.AlignCenter)
        layout.addWidget(self._result)
        row = QHBoxLayout()
        row.addStretch(1)
        close = QPushButton("Close")
        close.clicked.connect(self.close)
        row.addWidget(close)
        self.spin_button = accent(QPushButton("Spin"))
        self.spin_button.clicked.connect(lambda: self.wheel.spin())
        row.addWidget(self.spin_button)
        self.start_button = accent(QPushButton("Start it"))
        self.start_button.clicked.connect(self._start)
        self.start_button.hide()
        row.addWidget(self.start_button)
        layout.addLayout(row)
        _OPEN.append(self)
        self.show()

    def _landed(self, index):
        self._winner = self._problems[index]
        review_wheel.set_fate(self._winner["id"])
        self._result.setText(f"{self._winner['name']}\nThat's the one.")
        self.spin_button.hide()
        self.start_button.show()

    def _start(self):
        problem = self._winner
        self.close()
        self._on_start(problem)

    def closeEvent(self, event):
        if self in _OPEN:
            _OPEN.remove(self)
        super().closeEvent(event)
