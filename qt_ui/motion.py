"""Motion for the whole interface, in one place so every tab moves the same way.

The rules come from the interaction-design skill (wshobson/agents) and Emil Kowalski's motion
guidance, applied to Qt: motion confirms an action or shows where something came from (never
decoration); hover and press feedback is 100-150 ms, small transitions 200-300 ms, a page
entrance 300-500 ms; things that arrive ease out (a fast start, a soft landing); only short lists
are staggered; nothing loops except real status. It all turns off when Windows "animation effects"
are off, and under pytest, so the rest of the code can call it unconditionally.
"""
import os
import re
import sys

from PySide6.QtCore import QEasingCurve, QEvent, QObject, QPoint, QPointF, QRect, Qt, QVariantAnimation
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import QApplication, QFrame, QGraphicsDropShadowEffect, QPushButton, QWidget

HOVER_MS = 140        # hover / press feedback
SMALL_MS = 240        # one card arriving, a check being drawn
MEDIUM_MS = 420       # a ring sweeping, a bar growing, the nav marker moving
STAGGER_MS = 55       # between neighbouring cards on a page entrance
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
    curve.addCubicBezierSegment(QPointF(0.16, 1.0), QPointF(0.3, 1.0), QPointF(1.0, 1.0))
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


def reveal(page):
    """Page entrance: the cards fade in top to bottom, and rings, bars and grids that asked to
    (an attribute `replay_on_enter`) draw themselves again."""
    if not enabled() or not page.isVisible():
        return
    for old in getattr(page, "_motion_veils", []):
        try:
            old.deleteLater()
        except RuntimeError:
            pass
    stop(getattr(page, "_motion_anim", None))
    page._motion_veils = []
    for widget in page.findChildren(QWidget):
        if getattr(widget, "replay_on_enter", False) and widget.isVisible():
            widget.replay()
    cards = _cards(page)
    if not cards:
        return
    # Sample the colour behind each card from the whole window (the page alone has no background of
    # its own), just left of the card's edge.
    window = page.window()
    image = window.grab().toImage()
    ratio = image.devicePixelRatio()
    veils = []
    for index, card in enumerate(cards):
        origin = card.mapTo(window, QPoint(0, 0))
        sx = max(0, min(image.width() - 1, int((origin.x() - 5) * ratio)))
        sy = max(0, min(image.height() - 1, int((origin.y() + card.height() // 2) * ratio)))
        veils.append((_Veil(card.parentWidget(), card.geometry(), image.pixelColor(sx, sy), _radius_of(card)),
                      min(index, MAX_STAGGERED) * STAGGER_MS))
    page._motion_veils = [v for v, _d in veils]
    total = SMALL_MS + MAX_STAGGERED * STAGGER_MS
    curve = ease_out()

    def step(ms):
        ms = ms * total
        for veil, delay in veils:
            try:
                veil.set_alpha(1.0 - curve.valueForProgress(max(0.0, min(1.0, (ms - delay) / SMALL_MS))))
            except RuntimeError:
                pass

    def done():
        for veil, _d in veils:
            try:
                veil.deleteLater()
            except RuntimeError:
                pass
        page._motion_veils = []

    page._motion_anim = tween(page, 0.0, 1.0, total, step, done, QEasingCurve(QEasingCurve.Linear))


# --- buttons: lift on hover, press in --------------------------------------------------------------

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
