"""Motion for the whole interface, in one place so every tab moves the same way.

The rules come from the interaction-design skill (wshobson/agents) and Emil Kowalski's motion
guidance, applied to Qt: motion confirms an action or shows where something came from (never
decoration); hover and press feedback is 100-150 ms, small transitions 200-300 ms, a page
entrance 300-500 ms; things that arrive ease out (a fast start, a soft landing); only short lists
are staggered; nothing loops except real status. It all turns off when Windows "animation effects"
are off, and under pytest, so the rest of the code can call it unconditionally.
"""
import math
import os
import re
import sys

from PySide6.QtCore import QEasingCurve, QEvent, QObject, QPoint, QPointF, QRect, Qt, QVariantAnimation
from PySide6.QtGui import QColor, QPainter
from PySide6.QtCore import QRectF
from PySide6.QtWidgets import QApplication, QFrame, QGraphicsDropShadowEffect, QLabel, QPushButton, QWidget

HOVER_MS = 140        # hover / press feedback
SMALL_MS = 240        # one card arriving, a check being drawn
MEDIUM_MS = 420       # a ring sweeping, a bar growing, the nav marker moving
STAGGER_MS = 70       # between neighbouring cards on a page entrance
REVEAL_MS = 620       # one card rising into place
MAX_STAGGERED = 8     # cards after this arrive together with the last one

_os_allows = None
FORCE = None          # tests set this to turn motion on (pytest otherwise keeps it off)


def enabled():
    """True when UI animation should run."""
    global _os_allows
    if FORCE is not None:
        return FORCE
    if "PYTEST_CURRENT_TEST" in os.environ or os.environ.get("CARMEN_NO_MOTION"):
        return False
    if _os_allows is None:
        _os_allows = True
        if sys.platform == "win32":
            try:
                import ctypes
                flag = ctypes.c_int(1)
                SPI_GETCLIENTAREAANIMATION = 0x1042
                if ctypes.windll.user32.SystemParametersInfoW(SPI_GETCLIENTAREAANIMATION, 0, ctypes.byref(flag), 0):
                    _os_allows = bool(flag.value)
            except Exception:
                _os_allows = True
    return _os_allows


def ease_out():
    """cubic-bezier(0.16, 1, 0.3, 1): arrives fast, settles slowly."""
    curve = QEasingCurve(QEasingCurve.BezierSpline)
    curve.addCubicBezierSegment(QPointF(0.23, 1.0), QPointF(0.32, 1.0), QPointF(1.0, 1.0))
    return curve


def tween(owner, start, end, ms, on_value, on_done=None, curve=None):
    """Runs on_value(v) from start to end over ms. With motion off it jumps straight to the end."""
    if not enabled() or ms <= 0:
        on_value(end)
        if on_done:
            on_done()
        return None
    anim = QVariantAnimation(owner)
    anim.setStartValue(float(start))
    anim.setEndValue(float(end))
    anim.setDuration(int(ms))
    anim.setEasingCurve(curve or ease_out())
    anim.valueChanged.connect(lambda v: on_value(float(v)))
    if on_done:
        anim.finished.connect(on_done)
    anim.finished.connect(anim.deleteLater)
    anim.start()
    return anim


def stop(anim):
    if anim is not None:
        try:
            anim.stop()
        except RuntimeError:
            pass


# --- page entrance: cards arrive one after another ----------------------------------------------

class _Veil(QWidget):
    """Sits over a card in the colour of what is behind it, then fades away: the card seems to
    fade in without needing a graphics effect (cards already use those for their shadows)."""

    def __init__(self, parent, rect, colour, radius):
        super().__init__(parent)
        self.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.setGeometry(rect)
        self._colour = QColor(colour)
        self._radius = radius
        self._alpha = 1.0
        self.show()
        self.raise_()

    def set_alpha(self, alpha):
        self._alpha = alpha
        self.update()

    def paintEvent(self, _event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        colour = QColor(self._colour)
        colour.setAlphaF(max(0.0, min(1.0, self._alpha)))
        p.setPen(Qt.NoPen)
        p.setBrush(colour)
        p.drawRoundedRect(self.rect().adjusted(-1, -1, 1, 1), self._radius, self._radius)


def _radius_of(frame):
    match = re.search(r"border-radius:\s*(\d+)px", frame.styleSheet())
    return int(match.group(1)) if match else 14


def _cards(page):
    """The visible card-like frames on a page, top to bottom: frames with rounded corners that
    are big enough to be a card, skipping any that sit inside another card."""
    found = []
    for frame in page.findChildren(QFrame):
        if not frame.isVisible() or frame.width() < 120 or frame.height() < 36:
            continue
        if "border-radius" not in frame.styleSheet():
            continue
        found.append(frame)
    chosen = []
    for frame in found:
        parent = frame.parentWidget()
        nested = False
        while parent is not None and parent is not page:
            if parent in found:
                nested = True
                break
            parent = parent.parentWidget()
        if not nested:
            chosen.append(frame)
    chosen.sort(key=lambda f: (f.mapTo(page, QPoint(0, 0)).y(), f.mapTo(page, QPoint(0, 0)).x()))
    return chosen


class _Stage(QWidget):
    """Lies over a page for its entrance and paints each card (a picture of it) rising into place:
    it slides up ~30 px, grows from 96% and fades in, one after another. Under each card a patch in
    the colour behind it hides the real card until its turn, so nothing flashes first."""
    RISE = 22

    def __init__(self, page):
        super().__init__(page)
        self.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.setGeometry(page.rect())
        self._items = []         # (pixmap, QRect, patch colour, radius, delay ms, dx, dy)
        self._ms = 0.0
        self._curve = ease_out()
        self.show()
        self.raise_()

    def add(self, pixmap, rect, colour, radius, delay, dx=0, dy=None):
        self._items.append((pixmap, rect, colour, radius, delay, dx, self.RISE if dy is None else dy))

    def set_time(self, ms):
        self._ms = ms
        self.update()

    def paintEvent(self, _event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setRenderHint(QPainter.SmoothPixmapTransform)
        for pixmap, rect, colour, radius, delay, dx, dy in self._items:
            t = max(0.0, min(1.0, (self._ms - delay) / REVEAL_MS))
            if t >= 1.0:
                continue                                  # arrived: the real card shows through
            e = self._curve.valueForProgress(t)
            p.setOpacity(1.0)
            p.setPen(Qt.NoPen)
            p.setBrush(colour)
            p.drawRoundedRect(rect.adjusted(-8, -8, 8, 8), radius + 4, radius + 4)
            if t <= 0.0:
                continue
            # Only a slide and a fade: the card keeps its exact size the whole way, so nothing wobbles.
            x = rect.x() + dx * (1 - e)
            y = rect.y() + dy * (1 - e)
            p.setOpacity(min(1.0, e * 1.4))
            p.drawPixmap(QRectF(x, y, rect.width(), rect.height()), pixmap, QRectF(pixmap.rect()))


def _big_labels(page, cards):
    """Page headings: big text outside any card (the title of the page, a section heading)."""
    found = []
    for label in page.findChildren(QLabel):
        if not label.isVisible() or not label.text().strip() or label.width() < 40 or label.height() < 20:
            continue
        font = label.font()
        size = font.pixelSize() if font.pixelSize() > 0 else int(font.pointSizeF() * 1.33)
        match = re.search(r"font-size:\s*(\d+)px", label.styleSheet())
        if match:
            size = int(match.group(1))
        if size < 24:
            continue
        inside = False
        parent = label.parentWidget()
        while parent is not None and parent is not page:
            if parent in cards:
                inside = True
                break
            parent = parent.parentWidget()
        if not inside:
            found.append(label)
    return found[:3]


class _EndOnResize(QObject):
    def __init__(self, page, finish):
        super().__init__(page)
        self._finish = finish

    def eventFilter(self, obj, event):
        if event.type() == QEvent.Resize and getattr(obj, "_motion_stage", None) is not None:
            self._finish()
        return False


def reveal(page):
    """Page entrance: headings glide in from the left, the cards rise one after another, and rings,
    bars and grids that asked to (an attribute `replay_on_enter`) draw themselves again."""
    if not enabled() or not page.isVisible():
        return
    old = getattr(page, "_motion_stage", None)
    if old is not None:
        try:
            old.deleteLater()
        except RuntimeError:
            pass
    stop(getattr(page, "_motion_anim", None))
    page._motion_veils = []
    page._motion_stage = None
    for widget in page.findChildren(QWidget):
        if getattr(widget, "replay_on_enter", False) and widget.isVisible():
            widget.replay()
    cards = _cards(page)
    heads = _big_labels(page, cards)
    if not cards and not heads:
        return
    window = page.window()
    image = window.grab().toImage()                       # the colour behind each card comes from here
    ratio = image.devicePixelRatio()
    stage = _Stage(page)

    def colour_behind(widget):
        origin = widget.mapTo(window, QPoint(0, 0))
        sx = max(0, min(image.width() - 1, int((origin.x() - 10) * ratio)))
        sy = max(0, min(image.height() - 1, int((origin.y() + widget.height() // 2) * ratio)))
        return image.pixelColor(sx, sy)

    for label in heads:
        rect = QRect(label.mapTo(page, QPoint(0, 0)), label.size())
        stage.add(label.grab(), rect, colour_behind(label), 4, 0, dx=-34, dy=0)
    for index, card in enumerate(cards):
        rect = QRect(card.mapTo(page, QPoint(0, 0)), card.size())
        stage.add(card.grab(), rect, colour_behind(card), _radius_of(card), 90 + min(index, MAX_STAGGERED) * STAGGER_MS)
    page._motion_stage = stage
    page._motion_veils = [stage]
    total = 90 + MAX_STAGGERED * STAGGER_MS + REVEAL_MS

    def done():
        try:
            stage.deleteLater()
        except RuntimeError:
            pass
        page._motion_stage = None
        page._motion_veils = []

    page._motion_anim = tween(stage, 0.0, float(total), total, stage.set_time, done, QEasingCurve(QEasingCurve.Linear))
    # If the window is resized mid-entrance the pictures no longer match the layout: end it at once.
    guard = _EndOnResize(page, lambda: (stop(page._motion_anim), done()))
    page._motion_guard = guard
    page.installEventFilter(guard)


def appear(widget):
    """One widget fading in (a card that was just added), over the colour behind it."""
    if not enabled() or widget is None:
        return

    def go():
        try:
            if not widget.isVisible() or widget.width() < 8:
                return
            window = widget.window()
            image = window.grab().toImage()
            ratio = image.devicePixelRatio()
            origin = widget.mapTo(window, QPoint(0, 0))
            sx = max(0, min(image.width() - 1, int((origin.x() - 5) * ratio)))
            sy = max(0, min(image.height() - 1, int((origin.y() + widget.height() // 2) * ratio)))
            veil = _Veil(widget.parentWidget(), widget.geometry(), image.pixelColor(sx, sy), _radius_of(widget))
            tween(veil, 1.0, 0.0, SMALL_MS, veil.set_alpha, veil.deleteLater)
        except RuntimeError:
            pass
    from PySide6.QtCore import QTimer
    QTimer.singleShot(0, go)


def count_up(label, text, ms=MEDIUM_MS):
    """Sets a label to `text`; if it starts with a number, counts up to it. Anything else (an
    "1h 20m" style value) is set straight away."""
    match = re.match(r"^(\d+)(.*)$", str(text))
    if not enabled() or not match or int(match.group(1)) < 2:
        label.setText(str(text))
        return
    target, rest = int(match.group(1)), match.group(2)
    stop(getattr(label, "_count_anim", None))
    label._count_anim = tween(label, 0, target, ms, lambda v: label.setText(f"{round(v)}{rest}"),
                              lambda: label.setText(str(text)))


# --- buttons: lift on hover, press in --------------------------------------------------------------

class _Ripple(QWidget):
    """A circle that spreads from the click and fades, clipped to the button's shape."""

    def __init__(self, button, point, light):
        super().__init__(button)
        self.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.setGeometry(button.rect())
        self._point = QPointF(point)
        self._light = light
        self._t = 0.0
        far = max(math.hypot(point.x() - x, point.y() - y) for x in (0, button.width()) for y in (0, button.height()))
        self._reach = far
        self._radius = min(button.height() / 2.0, 14.0)
        self.show()
        self.raise_()

    def set_t(self, t):
        self._t = t
        self.update()

    def paintEvent(self, _event):
        from PySide6.QtGui import QPainterPath
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        clip = QPainterPath()
        clip.addRoundedRect(QRectF(self.rect()), self._radius, self._radius)
        p.setClipPath(clip)
        alpha = 0.42 * (1.0 - self._t) ** 1.5
        colour = QColor(255, 255, 255, int(255 * alpha)) if self._light else QColor(70, 60, 140, int(255 * alpha * 0.55))
        p.setPen(Qt.NoPen)
        p.setBrush(colour)
        p.drawEllipse(self._point, self._reach * self._t, self._reach * self._t)


def ripple(button, point):
    if not enabled() or button.width() < 36 or button.height() < 22:
        return
    light = button.property("class") in ("AccentButton", "DangerButton", "NavButton") or button.isChecked()
    wave = _Ripple(button, point, light)
    tween(wave, 0.0, 1.0, 520, wave.set_t, wave.deleteLater)


class CardHover(QObject):
    """Cards lift when the pointer is on them: the shadow deepens and drops a little (180 ms)."""

    def eventFilter(self, obj, event):
        kind = event.type()
        if kind in (QEvent.Enter, QEvent.Leave) and isinstance(obj, QFrame) and enabled():
            effect = obj.graphicsEffect()
            if isinstance(effect, QGraphicsDropShadowEffect) and obj.width() >= 120 and obj.height() >= 40                     and "border-radius" in obj.styleSheet():
                self._lift(obj, effect, kind == QEvent.Enter)
        return False

    def _lift(self, card, effect, up):
        if not hasattr(card, "_shadow_base"):
            card._shadow_base = (effect.blurRadius(), effect.yOffset(), effect.color().alpha())
        blur, offset, alpha = card._shadow_base
        stop(getattr(card, "_hover_anim", None))
        colour = QColor(effect.color())

        def step(v):
            try:
                effect.setBlurRadius(blur + 16 * v)
                effect.setYOffset(offset + 6 * v)
                colour.setAlpha(min(255, int(alpha + 26 * v)))
                effect.setColor(colour)
            except RuntimeError:
                pass
        card._hover_anim = tween(card, getattr(card, "_hover_v", 0.0), 1.0 if up else 0.0, 200 if up else 260,
                                 lambda v: (setattr(card, "_hover_v", v), step(v)))


class ButtonMotion(QObject):
    """A soft shadow that grows under a button on hover, tightens on press and fades on leave."""
    CLASSES = {"AccentButton", "SecondaryButton", "DangerButton"}

    def eventFilter(self, obj, event):
        kind = event.type()
        if kind in (QEvent.Enter, QEvent.Leave, QEvent.MouseButtonPress, QEvent.MouseButtonRelease) \
                and isinstance(obj, QPushButton) and obj.property("class") in self.CLASSES and enabled():
            if kind == QEvent.Enter:
                self._to(obj, 14)
            elif kind == QEvent.Leave:
                self._to(obj, 0)
            elif kind == QEvent.MouseButtonPress:
                self._to(obj, 3)
            else:
                self._to(obj, 14 if obj.underMouse() else 0)
        return False

    def _to(self, button, blur):
        effect = button.graphicsEffect()
        if effect is None:
            effect = QGraphicsDropShadowEffect(button)
            effect.setOffset(0, 2)
            effect.setColor(QColor(20, 20, 40, 70))
            effect.setBlurRadius(0)
            button.setGraphicsEffect(effect)
        elif not isinstance(effect, QGraphicsDropShadowEffect) or not getattr(effect, "_ours", False):
            effect = None
        else:
            pass
        if effect is None:
            return
        effect._ours = True
        stop(getattr(button, "_lift_anim", None))

        def set_blur(value):
            try:
                effect.setBlurRadius(value)
                effect.setOffset(0, 1 + value / 8)
            except RuntimeError:
                pass

        def done():
            if blur == 0:
                try:
                    if button.graphicsEffect() is effect:
                        button.setGraphicsEffect(None)
                except RuntimeError:
                    pass

        button._lift_anim = tween(button, effect.blurRadius(), blur, HOVER_MS, set_blur, done)


_button_motion = None


def install(app=None):
    """Starts the app-wide button feedback. Safe to call once at startup."""
    global _button_motion
    app = app or QApplication.instance()
    if app is None or _button_motion is not None:
        return
    _button_motion = ButtonMotion(app)
    app.installEventFilter(_button_motion)
    app._card_hover = CardHover(app)
    app.installEventFilter(app._card_hover)
