"""Live timer in the macOS menu bar, next to Carmen's blue dot.

Reuses the status item pystray already created (tray.py), so the right-click /
click menu is unchanged. While a session runs the item shows its clock
("12:34", counting down for timed sessions and up for burnout/review ones,
"⏸ 12:34" while paused). Hovering it opens a small card with the details and
a Pause / Resume button, like the mini timer.

Everything here runs on the Qt main thread (a QTimer), which is also the
Cocoa main thread, so AppKit calls are safe. Any failure (pystray internals
changed, no status item yet) just leaves the plain dot.
"""
import objc
from AppKit import (
    NSFont,
    NSObject,
    NSScreen,
    NSTrackingActiveAlways,
    NSTrackingArea,
    NSTrackingInVisibleRect,
    NSTrackingMouseEnteredAndExited,
)
from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QCursor
from PySide6.QtWidgets import QLabel, QPushButton, QVBoxLayout, QWidget

import qt_ui.mini_timer as mini_timer
import session_manager

REFRESH_MS = 500
HIDE_GRACE_MS = 350
NSImageLeft = 2


def title_for(status):
    """Text shown in the menu bar: "" when idle, else the clock (with a pause
    marker). Count-up vs count-down comes from mini_timer.describe."""
    if not status.get("isActive"):
        return ""
    _label, clock, _fraction, _can_pause = mini_timer.describe(status)
    return f"⏸ {clock}" if status.get("isPaused") else clock


def detail_lines(status):
    if not status.get("isActive"):
        return ["No active session"]
    lines = []
    if status.get("isPaused"):
        lines.append("Paused")
    elif status.get("isBreak"):
        lines.append("On a break")
    lines.append(f"Lock mode: {status.get('lockMode', '?')}")
    lines.append(f"Violations: {status.get('violationCount', 0)}")
    return lines


class _Hover(NSObject):
    def initWithOwner_(self, owner):
        self = objc.super(_Hover, self).init()
        if self is None:
            return None
        self._owner = owner
        return self

    def mouseEntered_(self, _event):
        self._owner.on_enter()

    def mouseExited_(self, _event):
        pass  # leaving is detected by polling the cursor, so moving onto the card keeps it open


class _Card(QWidget):
    def __init__(self):
        super().__init__(None, Qt.Tool | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint)
        self.setAttribute(Qt.WA_MacAlwaysShowToolWindow)
        self.setStyleSheet(
            "QWidget { background: #000; color: #F2F4F7; }"
            "QPushButton { border: 1px solid #5A5F69; border-radius: 11px; padding: 4px 16px; }"
            "QPushButton:hover { border-color: #2D8CFF; }"
            "QPushButton:disabled { color: #5A5F69; }"
        )
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 12, 16, 12)
        self.label = QLabel()
        self.label.setStyleSheet("color: #8A8F98;")
        self.clock = QLabel()
        self.clock.setStyleSheet("font-size: 30px; font-weight: 600;")
        self.details = QLabel()
        self.details.setStyleSheet("color: #8A8F98;")
        self.button = QPushButton("Pause")
        self.button.setCursor(Qt.PointingHandCursor)
        self.button.clicked.connect(self._toggle)
        for w in (self.label, self.clock, self.details, self.button):
            layout.addWidget(w)
        self.setFixedWidth(230)

    def _toggle(self):
        if session_manager.get_status()["isPaused"]:
            session_manager.resume_session()
        else:
            session_manager.pause_session()
        self.refresh()

    def refresh(self):
        status = session_manager.get_status()
        label, clock, _fraction, can_pause = mini_timer.describe(status)
        self.label.setText(label)
        self.clock.setText(clock)
        self.details.setText("\n".join(detail_lines(status)))
        self.button.setEnabled(can_pause)
        self.button.setText("Resume" if status.get("isPaused") else "Pause")
        self.adjustSize()


class MenubarTimer:
    def __init__(self, icon):
        self._icon = icon
        self._button = None
        self._hover = None
        self._card = _Card()
        self._last_inside = 0
        self._elapsed_outside = 0
        self._timer = QTimer()
        self._timer.timeout.connect(self._tick)
        self._timer.start(REFRESH_MS)
        self._leave_timer = QTimer()
        self._leave_timer.timeout.connect(self._check_leave)

    def _status_button(self):
        item = getattr(self._icon, "_status_item", None)
        return item.button() if item is not None else None

    def _attach(self, button):
        self._button = button
        button.setImagePosition_(NSImageLeft)
        button.setToolTip_(None)
        try:
            button.setFont_(NSFont.monospacedDigitSystemFontOfSize_weight_(13, 0.0))
        except Exception:
            pass
        self._hover = _Hover.alloc().initWithOwner_(self)
        area = NSTrackingArea.alloc().initWithRect_options_owner_userInfo_(
            button.bounds(),
            NSTrackingMouseEnteredAndExited | NSTrackingActiveAlways | NSTrackingInVisibleRect,
            self._hover,
            None,
        )
        button.addTrackingArea_(area)

    def _tick(self):
        try:
            button = self._status_button()
            if button is None:
                return
            if self._button is None:
                self._attach(button)
            status = session_manager.get_status()
            title = title_for(status)
            button.setTitle_(f" {title}" if title else "")
            if self._card.isVisible():
                self._card.refresh()
        except Exception:
            pass

    def on_enter(self):
        try:
            self._card.refresh()
            self._place_card()
            self._card.show()
            self._elapsed_outside = 0
            self._leave_timer.start(100)
        except Exception:
            pass

    def _button_rect(self):
        """(left, top, width, height) of the status item in Qt's top-left screen space."""
        frame = self._button.window().frame()
        screen_h = NSScreen.screens()[0].frame().size.height
        return (
            int(frame.origin.x),
            int(screen_h - frame.origin.y - frame.size.height),
            int(frame.size.width),
            int(frame.size.height),
        )

    def _place_card(self):
        left, top, width, height = self._button_rect()
        x = left + width // 2 - self._card.width() // 2
        self._card.move(max(0, x), top + height + 2)

    def _check_leave(self):
        pos = QCursor.pos()
        left, top, width, height = self._button_rect()
        in_button = left <= pos.x() <= left + width and top <= pos.y() <= top + height + 2
        in_card = self._card.geometry().adjusted(-6, -6, 6, 6).contains(pos)
        if in_button or in_card:
            self._elapsed_outside = 0
            return
        self._elapsed_outside += 100
        if self._elapsed_outside >= HIDE_GRACE_MS:
            self._card.hide()
            self._leave_timer.stop()


def install(icon):
    """Starts the menu-bar timer for pystray `icon`; returns it (keep a reference)."""
    return MenubarTimer(icon)
