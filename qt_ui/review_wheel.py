"""The review wheel window: a big, lit-up wheel of problem names you can flick or spin.

The slice sizes come from review_wheel.weight. The winner is drawn by weight first and the
wheel is then turned so the pointer stops inside that slice, so a bigger slice really is
likelier, and the flick only decides how long and how dramatic the spin looks.

Everything on the wheel except the names is decoration that moves: a stage with twinkling
stars, a metal rim with chasing lights, a pointer on a spring that flicks as each peg goes
past, a motion ghost at speed, and a winner that pops out with a glow and a burst of confetti."""
import math
import random
import time

from PySide6.QtCore import QEasingCurve, QPointF, QPropertyAnimation, QRectF, Qt, QTimer, QVariantAnimation
from PySide6.QtGui import (
    QBrush, QColor, QConicalGradient, QFont, QFontMetrics, QLinearGradient, QPainter, QPainterPath, QPen,
    QPolygonF, QRadialGradient,
)
from PySide6.QtWidgets import QGraphicsOpacityEffect, QHBoxLayout, QPushButton, QVBoxLayout, QWidget

import review_wheel
from qt_ui.ui_kit import accent, dialog_title, role_label

COLOURS = ["#F5C242", "#4F8EF7", "#E8576B", "#43B581", "#9B6BF2", "#F2894B",
           "#2DB7C9", "#D9589B", "#8BC34A", "#5C6BC0", "#E0A526", "#26A69A"]
BULBS = 28
FRAME_MS = 16
_OPEN = []


def _frame_time():
    return time.monotonic()


class WheelWidget(QWidget):
    """The wheel itself. Names on the slices, one colour per slice, a pointer at the top."""

    def __init__(self, names, weights, on_landed=None):
        super().__init__()
        self.names, self.weights = list(names), [float(w) for w in weights]
        self._on_landed = on_landed
        self._angle = 0.0                 # degrees the wheel has turned clockwise
        self._winner = None
        self._anim = None
        self._drag = None                 # (grab angle, wheel angle at grab, [(time, angle)])
        self._phase = 0.0                 # seconds, drives the lights and the twinkle
        self._speed = 0.0                 # degrees a second, smoothed over the last frames
        self._last_angle = 0.0
        self._last_slice = None
        self._pointer = 0.0               # how far the pointer is flicked aside, in degrees
        self._pointer_v = 0.0
        self._pop = 0.0                   # 0 to 1: the winning slice coming out of the wheel
        self._confetti = []
        stars = random.Random(11)
        self._stars = [(stars.random(), stars.random(), stars.uniform(0, 6.28), stars.uniform(0.6, 1.8))
                       for _ in range(54)]
        self._last_time = _frame_time()
        self._frame = QTimer(self)
        self._frame.setInterval(FRAME_MS)
        self._frame.timeout.connect(self._step)
        self.setMinimumSize(380, 380)
        self.setCursor(Qt.OpenHandCursor)

    # ---- geometry ----
    def _outer(self):
        """Radius of the whole thing, lights included."""
        return max(40.0, min(self.width(), self.height()) / 2 - 14)

    def _centre(self):
        return QPointF(self.width() / 2, self.height() / 2 + 12)

    def _rim_width(self):
        return self._outer() * 0.09

    def _radius(self):
        """Radius of the slices."""
        return self._outer() - 12 - self._rim_width()

    def _bounds(self):
        c, r = self._centre(), self._radius()
        return QRectF(c.x() - r, c.y() - r, r * 2, r * 2)

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

    # ---- the clock behind every moving part ----
    def showEvent(self, event):
        super().showEvent(event)
        self._last_time = _frame_time()
        self._frame.start()

    def hideEvent(self, event):
        self._frame.stop()
        super().hideEvent(event)

    def _step(self):
        now = _frame_time()
        dt = max(0.001, min(0.05, now - self._last_time))
        self._last_time = now
        self._phase += dt
        self._speed = self._speed * 0.6 + ((self._angle - self._last_angle) / dt) * 0.4
        self._last_angle = self._angle
        # the pointer is on a spring; each peg that goes past knocks it the way the wheel is turning
        index = self.slice_under_pointer()
        if self._last_slice is not None and index != self._last_slice and abs(self._speed) > 20:
            kick = min(320.0, 60.0 + abs(self._speed) * 0.16)
            self._pointer_v += kick if self._speed > 0 else -kick
        self._last_slice = index
        self._pointer_v += (-150.0 * self._pointer - 11.0 * self._pointer_v) * dt
        self._pointer = max(-38.0, min(38.0, self._pointer + self._pointer_v * dt))
        if self._winner is not None:
            self._pop = min(1.0, self._pop + dt * 2.6)
        for bit in self._confetti:
            bit["vy"] += 900.0 * dt
            bit["vx"] *= 0.992
            bit["x"] += bit["vx"] * dt
            bit["y"] += bit["vy"] * dt
            bit["a"] += bit["va"] * dt
            bit["life"] -= dt
        self._confetti = [b for b in self._confetti if b["life"] > 0 and b["y"] < self.height() + 30]
        self.update()

    def _burst(self):
        centre = self._centre()
        top = QPointF(centre.x(), centre.y() - self._outer() * 0.9)
        rng = random.Random()
        for _ in range(110):
            angle = math.radians(rng.uniform(-80, 80))
            power = rng.uniform(280, 760)
            self._confetti.append({
                "x": top.x(), "y": top.y(), "vx": math.sin(angle) * power, "vy": -math.cos(angle) * power,
                "a": rng.uniform(0, 360), "va": rng.uniform(-540, 540), "w": rng.uniform(5, 11),
                "h": rng.uniform(3, 6), "life": rng.uniform(1.6, 3.2),
                "colour": QColor(rng.choice(COLOURS + ["#FFFFFF", "#FFD86B"])),
            })

    # ---- painting ----
    @staticmethod
    def _tone(colour, lighter=0):
        c = QColor(colour)
        return c.lighter(100 + lighter) if lighter > 0 else c.darker(100 - lighter)

    def paintEvent(self, _event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setRenderHint(QPainter.TextAntialiasing)
        centre, outer = self._centre(), self._outer()
        self._paint_stage(p, centre, outer)
        self._paint_shadow(p, centre, outer)
        self._paint_rim(p, centre, outer)
        if abs(self._speed) > 480:                       # a faint ghost behind the wheel while it is fast
            ghost = max(-9.0, min(9.0, -self._speed * 0.011))
            p.setOpacity(0.24)
            self._paint_slices(p, ghost, names=False)
            p.setOpacity(1.0)
        self._paint_slices(p, 0.0, names=True)
        self._paint_gloss(p, centre)
        self._paint_pegs(p, centre)
        self._paint_hub(p, centre)
        self._paint_bulbs(p, centre, outer)
        self._paint_pointer(p, centre, outer)
        self._paint_confetti(p)

    def _paint_stage(self, p, centre, outer):
        p.setPen(Qt.NoPen)
        back = QRadialGradient(centre, max(self.width(), self.height()) * 0.78)
        back.setColorAt(0.0, QColor("#3A3F7A"))
        back.setColorAt(0.55, QColor("#1D2048"))
        back.setColorAt(1.0, QColor("#0E1026"))
        p.setBrush(QBrush(back))
        p.drawRoundedRect(QRectF(self.rect()).adjusted(2, 2, -2, -2), 22, 22)
        for x, y, offset, rate in self._stars:
            glint = 0.5 + 0.5 * math.sin(self._phase * rate * 2.2 + offset)
            p.setBrush(QColor(255, 255, 255, int(25 + 150 * glint)))
            size = 0.9 + 1.5 * glint
            p.drawEllipse(QPointF(8 + x * (self.width() - 16), 8 + y * (self.height() - 16)), size, size)
        if self._winner is not None:                      # the winner's colour glows behind the wheel
            pulse = 0.5 + 0.5 * math.sin(self._phase * 4.0)
            glow = QRadialGradient(centre, outer * 1.35)
            tint = QColor(COLOURS[self._winner % len(COLOURS)])
            tint.setAlpha(int((70 + 60 * pulse) * self._pop))
            glow.setColorAt(0.55, tint)
            tint.setAlpha(0)
            glow.setColorAt(1.0, tint)
            p.setBrush(QBrush(glow))
            p.drawEllipse(centre, outer * 1.35, outer * 1.35)

    def _paint_shadow(self, p, centre, outer):
        p.setPen(Qt.NoPen)
        shade = QRadialGradient(QPointF(centre.x(), centre.y() + 10), outer * 1.08)
        shade.setColorAt(0.86, QColor(0, 0, 0, 120))
        shade.setColorAt(1.0, QColor(0, 0, 0, 0))
        p.setBrush(QBrush(shade))
        p.drawEllipse(QPointF(centre.x(), centre.y() + 10), outer * 1.08, outer * 1.08)

    def _paint_rim(self, p, centre, outer):
        width = self._rim_width()
        rim_outer = outer - 12
        metal = QConicalGradient(centre, 110)
        for at, tone in ((0.0, "#FFE9A6"), (0.18, "#C9972B"), (0.36, "#FFF3C4"), (0.55, "#B07A12"),
                         (0.74, "#F6D46A"), (0.9, "#B8861F"), (1.0, "#FFE9A6")):
            metal.setColorAt(at, QColor(tone))
        p.setBrush(Qt.NoBrush)
        p.setPen(QPen(QBrush(metal), width, Qt.SolidLine, Qt.FlatCap))
        p.drawEllipse(centre, rim_outer - width / 2, rim_outer - width / 2)
        p.setPen(QPen(QColor(60, 40, 0, 150), 1.5))
        p.drawEllipse(centre, rim_outer, rim_outer)
        p.drawEllipse(centre, rim_outer - width, rim_outer - width)

    def _paint_slices(self, p, extra_angle, names):
        centre, radius = self._centre(), self._radius()
        box = self._bounds()
        slices = self.slices()
        turned = self._angle + extra_angle
        for i, (start, sweep) in enumerate(slices):
            base = QColor(COLOURS[i % len(COLOURS)])
            dimmed = self._winner is not None and i != self._winner
            lift = self._pop * 0.05 if i == self._winner else 0.0
            shape = box if not lift else QRectF(centre.x() - radius * (1 + lift), centre.y() - radius * (1 + lift),
                                                radius * 2 * (1 + lift), radius * 2 * (1 + lift))
            if dimmed:
                dull = QColor(base)
                dull.setAlpha(90)
                fill = QRadialGradient(centre, radius)
                fill.setColorAt(0.0, self._tone(dull, -28))
                fill.setColorAt(1.0, dull)
            else:
                fill = QRadialGradient(centre, radius * (1 + lift))
                fill.setColorAt(0.0, self._tone(base, -28))
                fill.setColorAt(0.62, base)
                fill.setColorAt(1.0, self._tone(base, 22))
            p.setPen(QPen(QColor(255, 255, 255, 235 if not dimmed else 110), 2))
            p.setBrush(QBrush(fill))
            p.drawPie(shape, int((90 - (start + turned)) * 16), int(-sweep * 16))
        if names:
            for i, (start, sweep) in enumerate(slices):
                self._draw_name(p, i, start, sweep, centre, radius)
            if self._winner is not None and self._pop > 0:        # a pulsing outline round the winner
                start, sweep = slices[self._winner]
                pulse = 0.5 + 0.5 * math.sin(self._phase * 6.0)
                grow = 1 + self._pop * 0.05
                shape = QRectF(centre.x() - radius * grow, centre.y() - radius * grow, radius * 2 * grow, radius * 2 * grow)
                p.setBrush(Qt.NoBrush)
                p.setPen(QPen(QColor(255, 255, 255, int(150 + 105 * pulse)), 3.5))
                p.drawPie(shape, int((90 - (start + self._angle)) * 16), int(-sweep * 16))

    def _draw_name(self, p, index, start, sweep, centre, radius):
        if sweep < 4.5:
            return
        mid = start + sweep / 2 + self._angle
        chord = 2 * radius * 0.6 * math.sin(math.radians(min(sweep, 170)) / 2)
        font = QFont(self.font())
        font.setBold(True)
        font.setPixelSize(int(max(9, min(17, chord * 0.7))))
        p.save()
        p.translate(centre)
        p.rotate(mid - 90)
        p.setFont(font)
        dimmed = self._winner not in (None, index)
        left, right = radius * 0.2, radius * 0.93
        text = QFontMetrics(font).elidedText(self.names[index], Qt.ElideRight, int(right - left))
        flipped = 180.0 < mid % 360.0 < 360.0           # on the left half: turn the name the right way up
        if flipped:
            p.rotate(180)
            box, align = QRectF(-right, -radius, right - left, radius * 2), Qt.AlignVCenter | Qt.AlignLeft
        else:
            box, align = QRectF(left, -radius, right - left, radius * 2), Qt.AlignVCenter | Qt.AlignRight
        p.setPen(QColor(0, 0, 0, 40 if dimmed else 110))
        p.drawText(box.translated(1.2, 1.4), align, text)
        p.setPen(QColor(255, 255, 255, 120 if dimmed else 255))
        p.drawText(box, align, text)
        p.restore()

    def _paint_gloss(self, p, centre):
        radius = self._radius()
        shine = QPainterPath()
        shine.addEllipse(centre, radius - 5, radius - 5)
        cut = QPainterPath()
        cut.addEllipse(QPointF(centre.x() + radius * 0.1, centre.y() + radius * 0.2), radius - 5, radius - 5)
        shine = shine.subtracted(cut)
        light = QLinearGradient(centre.x() - radius, centre.y() - radius, centre.x() + radius * 0.2, centre.y())
        light.setColorAt(0.0, QColor(255, 255, 255, 95))
        light.setColorAt(1.0, QColor(255, 255, 255, 0))
        p.setPen(Qt.NoPen)
        p.setBrush(QBrush(light))
        p.drawPath(shine)

    def _paint_pegs(self, p, centre):
        radius = self._radius()
        p.setPen(Qt.NoPen)
        for start, _sweep in self.slices():
            angle = math.radians(start + self._angle)
            spot = QPointF(centre.x() + math.sin(angle) * (radius - 3), centre.y() - math.cos(angle) * (radius - 3))
            p.setBrush(QColor(0, 0, 0, 90))
            p.drawEllipse(spot + QPointF(0.8, 1.2), 4.2, 4.2)
            peg = QRadialGradient(spot - QPointF(1, 1), 5)
            peg.setColorAt(0.0, QColor("#FFFFFF"))
            peg.setColorAt(1.0, QColor("#C9CED8"))
            p.setBrush(QBrush(peg))
            p.drawEllipse(spot, 3.8, 3.8)

    def _paint_hub(self, p, centre):
        radius = self._radius()
        p.setPen(QPen(QColor(0, 0, 0, 70), 1.5))
        metal = QRadialGradient(centre - QPointF(radius * 0.03, radius * 0.04), radius * 0.16)
        metal.setColorAt(0.0, QColor("#FFFFFF"))
        metal.setColorAt(0.6, QColor("#C9CED8"))
        metal.setColorAt(1.0, QColor("#8E95A5"))
        p.setBrush(QBrush(metal))
        p.drawEllipse(centre, radius * 0.14, radius * 0.14)
        core = QColor(COLOURS[self._winner % len(COLOURS)]) if self._winner is not None else QColor("#1F2430")
        p.setPen(Qt.NoPen)
        p.setBrush(core)
        p.drawEllipse(centre, radius * 0.065, radius * 0.065)
        p.setBrush(QColor(255, 255, 255, 140))
        p.drawEllipse(centre - QPointF(radius * 0.02, radius * 0.025), radius * 0.02, radius * 0.015)

    def _paint_bulbs(self, p, centre, outer):
        ring = outer - 6
        p.setPen(Qt.NoPen)
        for i in range(BULBS):
            angle = 2 * math.pi * i / BULBS
            spot = QPointF(centre.x() + math.sin(angle) * ring, centre.y() - math.cos(angle) * ring)
            if self._winner is not None:
                lit = math.sin(self._phase * 8.0 + (i % 2) * math.pi) > -0.2          # a flashing win
            elif self.spinning() or self._drag is not None:
                lit = (i + int(self._phase * 22)) % 2 == 0                            # a fast shimmer
            else:
                lit = (i + int(self._phase * 6)) % 4 < 2                              # a slow chase
            if lit:
                glow = QRadialGradient(spot, 11)
                glow.setColorAt(0.0, QColor(255, 246, 200, 255))
                glow.setColorAt(0.35, QColor(255, 214, 90, 200))
                glow.setColorAt(1.0, QColor(255, 190, 40, 0))
                p.setBrush(QBrush(glow))
                p.drawEllipse(spot, 11, 11)
                p.setBrush(QColor("#FFFDF0"))
                p.drawEllipse(spot, 3.4, 3.4)
            else:
                p.setBrush(QColor("#7A5A1C"))
                p.drawEllipse(spot, 3.2, 3.2)

    def _paint_pointer(self, p, centre, outer):
        pivot = QPointF(centre.x(), centre.y() - outer - 4)
        p.save()
        p.translate(pivot)
        p.rotate(self._pointer)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(0, 0, 0, 90))
        p.drawPolygon(QPolygonF([QPointF(-13, 1), QPointF(15, 1), QPointF(1, 49)]))
        body = QLinearGradient(-14, 0, 14, 0)
        body.setColorAt(0.0, QColor("#E8576B"))
        body.setColorAt(0.5, QColor("#FF8A9B"))
        body.setColorAt(1.0, QColor("#C23049"))
        p.setPen(QPen(QColor("#FFFFFF"), 2))
        p.setBrush(QBrush(body))
        shape = QPainterPath()
        shape.moveTo(-14, -2)
        shape.quadTo(-14, 22, 0, 46)
        shape.quadTo(14, 22, 14, -2)
        shape.closeSubpath()
        p.drawPath(shape)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor("#FFFFFF"))
        p.drawEllipse(QPointF(0, 6), 4.5, 4.5)
        p.restore()

    def _paint_confetti(self, p):
        p.setPen(Qt.NoPen)
        for bit in self._confetti:
            colour = QColor(bit["colour"])
            colour.setAlpha(int(255 * min(1.0, bit["life"] * 1.4)))
            p.save()
            p.translate(bit["x"], bit["y"])
            p.rotate(bit["a"])
            p.setBrush(colour)
            p.drawRect(QRectF(-bit["w"] / 2, -bit["h"] / 2, bit["w"], bit["h"]))
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
        self._pop = 0.0
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
        self._last_angle = self._angle
        self._speed = 0.0
        self._winner = index
        self._pop = 0.0
        self._burst()
        self.setCursor(Qt.OpenHandCursor)
        self.update()
        if self._on_landed:
            self._on_landed(index)

    # ---- flicking by hand ----
    def _grab_angle(self, pos):
        c = self._centre()
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
        self.resize(540, 720)
        self._problems = [problem for problem, _ in items]
        self._on_start = on_start
        self._winner = None
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 22, 24, 20)
        layout.setSpacing(10)
        layout.addWidget(dialog_title("Spin the wheel"))
        self.wheel = WheelWidget([p["name"] for p in self._problems], [w for _, w in items], self._landed)
        layout.addWidget(self.wheel, 1)
        self._result = role_label("", "subtitle", wrap=True)
        self._result.setAlignment(Qt.AlignCenter)
        self._result.setStyleSheet("font-size: 22px; font-weight: 800; background: transparent;")
        self._fade = QGraphicsOpacityEffect(self._result)
        self._fade.setOpacity(0.0)
        self._result.setGraphicsEffect(self._fade)
        layout.addWidget(self._result)
        row = QHBoxLayout()
        row.addStretch(1)
        close = QPushButton("Close")
        close.clicked.connect(self.close)
        row.addWidget(close)
        self.spin_button = accent(QPushButton("Spin"))
        self.spin_button.setMinimumWidth(120)
        self.spin_button.clicked.connect(lambda: self.wheel.spin())
        row.addWidget(self.spin_button)
        self.start_button = accent(QPushButton("Start it"))
        self.start_button.setMinimumWidth(120)
        self.start_button.clicked.connect(self._start)
        self.start_button.hide()
        row.addWidget(self.start_button)
        layout.addLayout(row)
        _OPEN.append(self)
        self.show()

    def _landed(self, index):
        self._winner = self._problems[index]
        review_wheel.set_fate(self._winner["id"])
        self._result.setText(self._winner["name"])
        fade = QPropertyAnimation(self._fade, b"opacity", self)
        fade.setDuration(450)
        fade.setStartValue(0.0)
        fade.setEndValue(1.0)
        fade.setEasingCurve(QEasingCurve.OutCubic)
        fade.start(QPropertyAnimation.DeleteWhenStopped)
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
