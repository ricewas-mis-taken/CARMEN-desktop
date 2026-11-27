"""Knows when the monitors are changing, so Carmen can stay out of Qt's way until they settle.

Crash reports from this machine (Qt6Gui.dll, QScreen::geometry and QScreen::virtualSiblings) point at a
monitor going away or coming back while Qt is still using it: two monitors at different zoom levels, one of
which drops off and returns. Qt owns that handling; what Carmen can do is not add to it. While the screens
are changing, its own timers skip window moves and raw Win32 calls, and nothing here reads the screen list
(reading it mid-change is itself risky), so this only records that something changed, and what the screens
look like once it has calmed down."""
import time

from calendar_log import logger

SETTLE_SECONDS = 3.0
_clock = time.monotonic
_state = {"last": None, "changes": 0}
_connected = set()


def note_change(reason):
    """A screen was added or removed, or one changed size or zoom."""
    _state["last"] = _clock()
    _state["changes"] += 1
    logger.info("screens changing: %s (change %d)", reason, _state["changes"])


def settling():
    """True for a few seconds after the screens last changed."""
    return _state["last"] is not None and _clock() - _state["last"] < SETTLE_SECONDS


def changes():
    return _state["changes"]


def _snapshot():
    """What the screens look like now that things have calmed down (logged for the next crash report)."""
    from PySide6.QtGui import QGuiApplication
    try:
        parts = ["%s %dx%d@%.2f" % (s.name(), s.geometry().width(), s.geometry().height(), s.devicePixelRatio())
                 for s in QGuiApplication.screens()]
        logger.info("screens now: %s", "; ".join(parts) or "none")
        for screen in QGuiApplication.screens():
            _watch(screen)
    except Exception:
        logger.exception("screen_guard: could not read the screens")


def _watch(screen):
    key = screen.name()
    if key in _connected:
        return
    _connected.add(key)
    screen.geometryChanged.connect(lambda *_: note_change("a screen's size changed"))
    screen.logicalDotsPerInchChanged.connect(lambda *_: note_change("a screen's zoom changed"))


def install(app):
    """Call once on the Qt main thread after QApplication exists."""
    from PySide6.QtCore import QTimer

    def changed(reason):
        note_change(reason)
        QTimer.singleShot(int((SETTLE_SECONDS + 0.5) * 1000), _snapshot)      # look only after it has calmed down

    app.screenAdded.connect(lambda *_: changed("a screen was added"))
    app.screenRemoved.connect(lambda *_: changed("a screen was removed"))
    app.primaryScreenChanged.connect(lambda *_: changed("the main screen changed"))
    _snapshot()
