"""Live timer and control panel on the macOS menu bar dot.

Reuses the status item pystray already created (tray.py). While a session runs
the item shows its clock ("12:34", counting down for timed sessions and up for
burnout/review ones, "⏸ 12:34" while paused).

Hover: a compact card with the session, a big clock, progress, and Pause/Resume.
Click: the same card pinned open with the tray's actions underneath (Open, Mini
Timer, QuickNote, and the rarer ones), closing on a click elsewhere or Esc.
Right-click still opens the plain tray menu. Layout follows Apple's popover
guidance: the thing you came for (the time and Pause) first, actions below, no
chrome that isn't a control.

Everything runs on the Qt main thread (a QTimer), which is also the Cocoa main
thread, so AppKit calls are safe. Any failure (pystray internals changed, no
status item yet) just leaves the plain dot and its menu.
"""
import objc
from AppKit import (
    NSApp,
    NSFont,
    NSMakePoint,
    NSObject,
    NSScreen,
    NSTrackingActiveAlways,
    NSTrackingArea,
    NSTrackingInVisibleRect,
    NSTrackingMouseEnteredAndExited,
)
from PySide6.QtCore import QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QCursor, QPainter
from PySide6.QtWidgets import (
    QFrame,
    QGraphicsDropShadowEffect,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

import qt_ui.mini_timer as mini_timer
import session_manager
from qt_ui.ui_kit import FAINT, INK, MUTED, NUM_CSS, ElidedLabel

REFRESH_MS = 500
HIDE_GRACE_MS = 350
NSImageLeft = 2
NSEventMaskLeftMouseUp = 1 << 2
NSEventMaskRightMouseUp = 1 << 4
NSEventTypeRightMouseUp = 4

BREAK_COLOR = "#2FB67C"
ACCENT = "#2D8CFF"

_PRIMARY = (
    f"QPushButton{{background:{INK};color:#FFFFFF;border:none;border-radius:12px;"
    "font-size:14px;font-weight:600;}"
    "QPushButton:hover{background:#2B3445;}"
    "QPushButton:disabled{background:#D5D9E0;color:#FFFFFF;}")
_QUIET = (
    f"QPushButton{{background:#F2F4F7;color:{INK};border:none;border-radius:10px;"
    "font-size:12.5px;font-weight:600;padding:0 10px;}"
    "QPushButton:hover{background:#E7EAF0;}")
_LINK = (
    f"QPushButton{{background:transparent;color:{MUTED};border:none;font-size:12px;padding:0;}}"
    f"QPushButton:hover{{color:{INK};text-decoration:underline;}}")


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


def summary_line(status):
    """One quiet line under the clock."""
    if not status.get("isActive"):
        return "Start a focus session from Carmen."
    count = status.get("violationCount", 0)
    plural = "violation" if count == 1 else "violations"
    return f"{str(status.get('lockMode', '')).capitalize()} lock  ·  {count} {plural}"


def state_pill(status):
    """(text, colour) for the status pill, or None when idle."""
    if not status.get("isActive"):
        return None
    if status.get("isPaused"):
        return "Paused", FAINT
    if status.get("isBreak"):
        return "Break", BREAK_COLOR
    return "Live", "#2FB67C"


class _Bar(QWidget):
    """A thin progress bar."""

    def __init__(self):
        super().__init__()
        self._fraction = 0.0
        self.setFixedHeight(6)

    def set_fraction(self, fraction):
        fraction = max(0.0, min(1.0, fraction or 0.0))
        if fraction != self._fraction:
            self._fraction = fraction
            self.update()

    def paintEvent(self, _event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(0, 0, 0, 24))
        p.drawRoundedRect(QRectF(self.rect()), 3, 3)
        if self._fraction > 0:
            p.setBrush(QColor(ACCENT))
            p.drawRoundedRect(QRectF(0, 0, self.width() * self._fraction, self.height()), 3, 3)


class _Pill(QWidget):
    """A small rounded status tag, painted directly so the corners are always round."""

    def __init__(self):
        super().__init__()
        self._text = ""
        self._color = QColor(FAINT)
        self.setFixedHeight(20)

    def set_state(self, text, color):
        self._text, self._color = text, QColor(color)
        font = self.font()
        font.setPixelSize(11)
        font.setBold(True)
        self.setFont(font)
        self.setFixedWidth(self.fontMetrics().horizontalAdvance(text) + 20)
        self.update()

    def paintEvent(self, _event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(Qt.NoPen)
        p.setBrush(self._color)
        p.drawRoundedRect(QRectF(self.rect()), 10, 10)
        p.setPen(QColor("#FFFFFF"))
        p.setFont(self.font())
        p.drawText(self.rect(), Qt.AlignCenter, self._text)


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

    def statusClicked_(self, _sender):
        self._owner.on_click()


class _Panel(QWidget):
    WIDTH = 296

    def __init__(self, on_action):
        super().__init__(None)
        self._on_action = on_action
        self._pinned = False
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_MacAlwaysShowToolWindow)
        self._apply_flags(False)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(14, 8, 14, 20)    # room for the shadow
        self._card = QFrame()
        self._card.setObjectName("PanelCard")
        self._card.setStyleSheet(
            "QFrame#PanelCard { background: #FFFFFF; border: 1px solid #E3E6EC; border-radius: 16px; }")
        shadow = QGraphicsDropShadowEffect(self._card)
        shadow.setBlurRadius(28)
        shadow.setOffset(0, 8)
        shadow.setColor(QColor(16, 24, 40, 60))
        self._card.setGraphicsEffect(shadow)
        outer.addWidget(self._card)

        box = QVBoxLayout(self._card)
        box.setContentsMargins(18, 16, 18, 16)
        box.setSpacing(10)

        top = QHBoxLayout()
        top.setSpacing(8)
        self._label = ElidedLabel("")
        self._label.setStyleSheet(f"font-size:12.5px;font-weight:600;color:{MUTED};")
        top.addWidget(self._label, 1)
        self._pill = _Pill()
        top.addWidget(self._pill, 0, Qt.AlignVCenter)
        box.addLayout(top)

        self._clock = QLabel("--:--")
        self._clock.setStyleSheet(f"{NUM_CSS}font-size:42px;color:{INK};")
        box.addWidget(self._clock)

        self._bar = _Bar()
        box.addWidget(self._bar)

        self._summary = QLabel("")
        self._summary.setStyleSheet(f"font-size:12.5px;color:{MUTED};")
        box.addWidget(self._summary)

        self._primary = QPushButton("Pause")
        self._primary.setCursor(Qt.PointingHandCursor)
        self._primary.setFixedHeight(40)
        self._primary.setStyleSheet(_PRIMARY)
        self._primary.clicked.connect(self._primary_clicked)
        box.addWidget(self._primary)

        # Pinned-only: the tray's actions.
        self._footer = QWidget()
        foot = QVBoxLayout(self._footer)
        foot.setContentsMargins(0, 4, 0, 0)
        foot.setSpacing(10)
        rule = QFrame()
        rule.setFixedHeight(1)
        rule.setStyleSheet("background:#EEF0F4;border:none;")
        foot.addWidget(rule)
        row = QHBoxLayout()
        row.setSpacing(8)
        for text, prefix in (("Open", "Open"), ("Mini timer", "Mini Timer"), ("QuickNote", "QuickNote")):
            b = QPushButton(text)
            b.setCursor(Qt.PointingHandCursor)
            b.setFixedHeight(34)
            b.setStyleSheet(_QUIET)
            b.clicked.connect(lambda _=False, p=prefix: self._act(p))
            row.addWidget(b, 1)
        foot.addLayout(row)
        links = QHBoxLayout()
        links.setSpacing(14)
        self._link_buttons = {}
        for text, prefix in (("Copy API token", "Copy API Token"), ("Sign in", "Sign in")):
            b = QPushButton(text)
            b.setCursor(Qt.PointingHandCursor)
            b.setStyleSheet(_LINK)
            b.clicked.connect(lambda _=False, p=prefix: self._act(p))
            links.addWidget(b)
            self._link_buttons[prefix] = b
        links.addStretch(1)
        quit_button = QPushButton("Quit Carmen")
        quit_button.setCursor(Qt.PointingHandCursor)
        quit_button.setStyleSheet(_LINK)
        quit_button.clicked.connect(lambda: self._act("Quit"))
        links.addWidget(quit_button)
        foot.addLayout(links)
        box.addWidget(self._footer)
        self._footer.hide()

        self.setFixedWidth(self.WIDTH + 28)

    def _apply_flags(self, pinned):
        base = Qt.FramelessWindowHint | Qt.NoDropShadowWindowHint
        self.setWindowFlags(base | (Qt.Popup if pinned else (Qt.Tool | Qt.WindowStaysOnTopHint)))

    def _act(self, prefix):
        self.hide()
        self._on_action(prefix)

    def _primary_clicked(self):
        status = session_manager.get_status()
        if not status.get("isActive"):
            self._act("Open")
            return
        if status["isPaused"]:
            session_manager.resume_session()
        else:
            session_manager.pause_session()
        self.refresh()

    def keyPressEvent(self, event):
        if event.key() == Qt.Key_Escape:
            self.hide()
            return
        super().keyPressEvent(event)

    def present(self, pinned):
        """(Re)shows the card in hover or pinned form; the caller positions it."""
        self.hide()
        self._pinned = pinned
        self._apply_flags(pinned)
        self._footer.setVisible(pinned)
        self.refresh()
        self.adjustSize()

    @property
    def pinned(self):
        return self._pinned

    def refresh(self):
        status = session_manager.get_status()
        label, clock, fraction, can_pause = mini_timer.describe(status)
        self._label.setText(label)
        self._clock.setText(clock)
        self._bar.setVisible(status.get("isActive") and fraction is not None)
        self._bar.set_fraction(fraction)
        self._summary.setText(summary_line(status))
        pill = state_pill(status)
        if pill is None:
            self._pill.hide()
        else:
            text, color = pill
            self._pill.set_state(text, color)
            self._pill.show()
        if not status.get("isActive"):
            self._primary.setText("Start a session")
            self._primary.setEnabled(True)
        else:
            self._primary.setText("Resume" if status.get("isPaused") else "Pause")
            self._primary.setEnabled(can_pause)

    def set_available(self, titles):
        """Hides footer links the tray menu does not currently offer."""
        for prefix, b in self._link_buttons.items():
            b.setVisible(any(t.lower().startswith(prefix.lower()) for t in titles))


class MenubarTimer:
    def __init__(self, icon):
        self._icon = icon
        self._button = None
        self._hover = None
        self._menu = None
        self._panel = _Panel(self._menu_action)
        self._elapsed_outside = 0
        self._timer = QTimer()
        self._timer.timeout.connect(self._tick)
        self._timer.start(REFRESH_MS)
        self._leave_timer = QTimer()
        self._leave_timer.timeout.connect(self._check_leave)

    def _status_item(self):
        return getattr(self._icon, "_status_item", None)

    def _attach(self, item):
        button = item.button()
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
        button.setTarget_(self._hover)
        button.setAction_(b"statusClicked:")
        button.sendActionOn_(NSEventMaskLeftMouseUp | NSEventMaskRightMouseUp)

    def _steal_menu(self, item):
        """pystray attaches its menu to the item (and again on every update), which
        would make a click open it. Keep it for right-click and for the panel's
        buttons, and leave the item menu-less so a click reaches us."""
        menu = item.menu()
        if menu is not None:
            self._menu = menu
            item.setMenu_(None)
            self._panel.set_available(
                [str(menu.itemAtIndex_(i).title()) for i in range(menu.numberOfItems())])

    def _tick(self):
        try:
            item = self._status_item()
            if item is None or item.button() is None:
                return
            if self._button is None:
                self._attach(item)
            self._steal_menu(item)
            status = session_manager.get_status()
            title = title_for(status)
            self._button.setTitle_(f" {title}" if title else "")
            if self._panel.isVisible():
                self._panel.refresh()
        except Exception:
            pass

    def _menu_action(self, prefix):
        """Runs the tray menu item whose title starts with `prefix` (so the panel's
        buttons do exactly what the menu does, Quit included)."""
        menu = self._menu
        if menu is None:
            return
        for i in range(menu.numberOfItems()):
            if str(menu.itemAtIndex_(i).title()).lower().startswith(prefix.lower()):
                menu.performActionForItemAtIndex_(i)
                return

    def on_click(self):
        try:
            event = NSApp().currentEvent()
            if event is not None and event.type() == NSEventTypeRightMouseUp and self._menu is not None:
                self._panel.hide()
                self._leave_timer.stop()
                self._menu.popUpMenuPositioningItem_atLocation_inView_(
                    None, NSMakePoint(0, self._button.bounds().size.height + 6), self._button)
                return
            if self._panel.isVisible() and self._panel.pinned:
                self._panel.hide()
                return
            self._leave_timer.stop()
            self._panel.present(pinned=True)
            self._place()
            self._panel.show()
            self._panel.raise_()
            self._panel.activateWindow()
        except Exception:
            pass

    def on_enter(self):
        try:
            if self._panel.isVisible():
                return
            self._panel.present(pinned=False)
            self._place()
            self._panel.show()
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

    def _place(self):
        left, top, width, height = self._button_rect()
        x = left + width // 2 - self._panel.width() // 2
        screen = NSScreen.screens()[0].frame().size.width
        x = max(4, min(x, int(screen) - self._panel.width() - 4))
        self._panel.move(x, top + height - 6)

    def _check_leave(self):
        if self._panel.pinned:
            self._leave_timer.stop()
            return
        pos = QCursor.pos()
        left, top, width, height = self._button_rect()
        in_button = left <= pos.x() <= left + width and top <= pos.y() <= top + height + 2
        in_card = self._panel.geometry().adjusted(-6, -6, 6, 6).contains(pos)
        if in_button or in_card:
            self._elapsed_outside = 0
            return
        self._elapsed_outside += 100
        if self._elapsed_outside >= HIDE_GRACE_MS:
            self._panel.hide()
            self._leave_timer.stop()


def install(icon):
    """Starts the menu-bar timer for pystray `icon`; returns it (keep a reference)."""
    return MenubarTimer(icon)
