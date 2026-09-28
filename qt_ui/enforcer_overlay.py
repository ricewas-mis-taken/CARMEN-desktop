"""Qt port of enforcer.py's lock-overlay popup and its follow-up unblock-
reason dialog. Stage 1 of the Tkinter->PySide6 migration: functional port
only, default Qt look, no QSS styling yet (that lands in Stage 5's final
style pass alongside the other Stage-1 dialogs).

Business logic (session_manager reads/writes, win32 foreground-window
handling) lives in enforcer.py, unchanged — this module only builds the
widgets enforcer.py's _show_lock_overlay() hands off to via
qt_gui_thread.run_on_gui_thread().

Deliberately non-modal (no exec(), no modal flag) — same reasoning as the
Tk version: a modal input grab would freeze every other running app, not
just the offending one. Instead this self-enforces "stay on top" via a
repeating raise_()/activateWindow() tick, same as the Tk version's
lift()/focus_force() loop.
"""
import time

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QApplication,
    QFrame,
    QGraphicsDropShadowEffect,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

# _LockOverlay's own dark card styling -- deliberately not styles.qss (see
# that file's #PopupBg comment: this overlay intentionally keeps its own look
# regardless of the app's light theme, since it has to stay legible floating
# over an arbitrary, unpredictable app underneath it). Frameless + translucent
# background is what lets the QSS border-radius below actually round the
# window's real corners instead of being clipped square by the OS.
_OVERLAY_STYLESHEET = """
    QWidget#LockOverlayCard {
        background: #23262E;
        border-radius: 14px;
    }
    QLabel#LockOverlayMessage {
        color: #F2F3F5;
        font-size: 14px;
    }
    QLabel#LockOverlayTime {
        color: #9AA1AC;
        font-size: 11px;
    }
    QProgressBar {
        background: #33373F;
        border: none;
        border-radius: 3px;
    }
    QProgressBar::chunk {
        background: #5B8DEF;
        border-radius: 3px;
    }
    QPushButton {
        background: #33373F;
        color: #F2F3F5;
        border: none;
        border-radius: 8px;
        padding: 6px 16px;
        font-weight: 600;
    }
    QPushButton:hover {
        background: #3F444D;
    }
    QLineEdit {
        background: #2C2F37;
        color: #F2F3F5;
        border: 1px solid #3F444D;
        border-radius: 8px;
        padding: 6px 10px;
    }
    QLineEdit:focus {
        border: 1px solid #5B8DEF;
    }
"""

import session_manager

# Qt widgets with no parent are only kept alive at the C++ level while
# shown; without a Python-side reference here, the wrapper object can be
# garbage-collected out from under a still-visible window. Entries are
# removed on close().
_open_windows = set()

# If a second violation fires before the first overlay finishes (e.g. two
# different offending apps in quick succession, or a soft-lock warning's 5s
# window outlasting the poll interval), stacking every overlay dead-center
# made them indistinguishable and turned into the same "flashing" look as
# window_tracker.py's redirect-storm bug. Each additional overlay open at
# construction time cascades further from center instead; the very first one
# (no others open yet) always still lands exactly centered.
_CASCADE_OFFSET_PX = 46
_CASCADE_MAX_STEPS = 6


def _to_logical_rect(rect):
    """Win32/DWM report window rects in physical pixels; Qt widget geometry
    is in logical (device-independent) pixels. On any display scaled above
    100% (e.g. Windows' common 125%/150% presets) that mismatch alone makes
    the blackout land far off from the real window -- not a positioning bug,
    a unit mismatch. Assumes a single scale factor (primary screen's), which
    covers one monitor or several matched-DPI ones; a genuinely mixed-DPI
    multi-monitor setup would need per-monitor DPI lookup instead."""
    screen = QApplication.primaryScreen()
    dpr = screen.devicePixelRatio() if screen else 1
    if dpr == 1:
        return rect
    left, top, width, height = rect
    return (int(left / dpr), int(top / dpr), int(width / dpr), int(height / dpr))


def build_overlay(
    message, duration_ms, offending_process_name=None, blackout_rect=None, blackout_rect_provider=None
):
    win = _LockOverlay(
        message,
        duration_ms,
        offending_process_name,
        blackout_rect=blackout_rect,
        blackout_rect_provider=blackout_rect_provider,
    )
    _open_windows.add(win)
    win.destroyed.connect(lambda: _open_windows.discard(win))
    win.show()
    return win


def build_unblock_reason_dialog(process_name):
    win = _UnblockReasonDialog(process_name)
    _open_windows.add(win)
    win.destroyed.connect(lambda: _open_windows.discard(win))
    win.show()
    return win


class _BlackoutOverlay(QWidget):
    """A solid black, click-capturing window covering exactly one rectangle
    (the offending window's own, not the whole screen) -- shown alongside
    _LockOverlay for soft_lock_warning specifically, so the warning can't
    just be read straight through the small popup for its duration. Mirrors
    the browser extension's own press-and-hold black-box block on the
    extension side.

    Deliberately not used for every lock-overlay call site -- see
    enforcer.py's hard_lock_redirect() (already minimizes the window and
    hides its taskbar preview -- a screen-covering overlay on top of a
    plain redirect isn't needed) and show_blocked_notice() (catches windows
    minimized in the background while the user is on a different, allowed
    app; covering anything there would hide unrelated, legitimate work)."""

    def __init__(self, duration_ms, rect, rect_provider=None):
        super().__init__(
            None,
            Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool,
        )
        self._closed = False
        self._rect_provider = rect_provider
        self._consecutive_misses = 0
        self.setStyleSheet("background-color: black;")
        left, top, width, height = _to_logical_rect(rect)
        self.setGeometry(left, top, width, height)

        QTimer.singleShot(duration_ms + 1000, self.close)

        if rect_provider is not None:
            # Re-queries the offending window's live rect rather than trusting
            # the one captured at construction time -- without this, dragging
            # or resizing that window during the overlay's lifetime leaves the
            # blackout sitting over the window's old position/size, looking
            # like a small black box that doesn't cover the app at all.
            self._track_timer = QTimer(self)
            self._track_timer.timeout.connect(self._track)
            self._track_timer.start(150)

    # A single missed rect lookup (rect_provider returning None) could just be
    # a transient DWM/win32 hiccup, not the window actually closing -- closing
    # the blackout on the very first miss risked it vanishing mid-overlay for
    # a reason having nothing to do with the real window going away. Requiring
    # a few consecutive misses (at 150ms/tick, ~450ms) before giving up still
    # reacts promptly to a genuinely closed window without being trigger-happy
    # about one flaky query.
    _CONSECUTIVE_MISSES_BEFORE_CLOSE = 3

    def _track(self):
        if self._closed:
            return
        rect = self._rect_provider()
        if rect is None:
            self._consecutive_misses += 1
            if self._consecutive_misses >= self._CONSECUTIVE_MISSES_BEFORE_CLOSE:
                # The window closed/minimized mid-overlay -- nothing left to
                # cover, so get out of the way instead of leaving a stray
                # black box floating over whatever's now underneath it.
                self.close()
            return
        self._consecutive_misses = 0
        left, top, width, height = _to_logical_rect(rect)
        if (left, top, width, height) != (self.x(), self.y(), self.width(), self.height()):
            self.setGeometry(left, top, width, height)

    def close(self):
        if self._closed:
            return True
        self._closed = True
        if self._rect_provider is not None:
            self._track_timer.stop()
        return super().close()


class _LockOverlay(QWidget):
    def __init__(
        self,
        message,
        duration_ms,
        offending_process_name=None,
        blackout_rect=None,
        blackout_rect_provider=None,
    ):
        super().__init__(
            None,
            Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool,
        )
        # Lets the QSS border-radius below actually round the window's real
        # corners (a plain top-level widget has an opaque rectangular
        # backing surface regardless of QSS) rather than clipping them square.
        # A plain QWidget (unlike QFrame) doesn't paint its own QSS
        # background/border unless this is set -- without it the
        # #LockOverlayCard rule below would be silently ignored and the
        # window would render as whatever's behind it (just floating text).
        # WA_TranslucentBackground (for true rounded corners on a top-level
        # window) was tried and dropped -- confirmed via an isolated repro
        # that it renders the whole window fully blank on this Qt/Windows
        # combo, worse than the plain square corners this falls back to.
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setObjectName("LockOverlayCard")
        self.setStyleSheet(_OVERLAY_STYLESHEET)

        self._closed = False
        self._duration_ms = duration_ms
        self._start_time = time.time()
        self._blackout_win = None
        if blackout_rect is not None:
            self._blackout_win = _BlackoutOverlay(duration_ms, blackout_rect, rect_provider=blackout_rect_provider)
            _open_windows.add(self._blackout_win)
            self._blackout_win.destroyed.connect(lambda: _open_windows.discard(self._blackout_win))
            self._blackout_win.show()

        width, height = 380, 150
        if offending_process_name:
            height += 40
        self.resize(width, height)
        self._position_window(width, height)

        shadow = QGraphicsDropShadowEffect(self)
        shadow.setBlurRadius(32)
        shadow.setOffset(0, 6)
        shadow.setColor(QColor(0, 0, 0, 140))
        self.setGraphicsEffect(shadow)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(22, 20, 22, 18)
        layout.setSpacing(10)

        message_label = QLabel(message)
        message_label.setObjectName("LockOverlayMessage")
        message_label.setWordWrap(True)
        message_label.setAlignment(Qt.AlignCenter)
        layout.addWidget(message_label)

        self._time_label = QLabel()
        self._time_label.setObjectName("LockOverlayTime")
        self._time_label.setAlignment(Qt.AlignCenter)
        layout.addWidget(self._time_label)

        self._progress = QProgressBar()
        self._progress.setRange(0, 1000)
        self._progress.setValue(0)
        self._progress.setTextVisible(False)
        self._progress.setFixedHeight(6)
        layout.addWidget(self._progress)

        if offending_process_name:
            unblock_button = QPushButton("Unblock")
            unblock_button.clicked.connect(
                lambda: self._on_unblock_click(offending_process_name)
            )
            layout.addWidget(unblock_button, alignment=Qt.AlignCenter)

        # Backup auto-close, independent of the tick loop below — guarantees
        # the popup closes even if something in _tick() raises.
        QTimer.singleShot(duration_ms + 1000, self.close)

        self._tick_timer = QTimer(self)
        self._tick_timer.timeout.connect(self._tick)
        self._tick_timer.start(50)
        self._tick()

    def _position_window(self, width, height):
        screen = QApplication.primaryScreen().availableGeometry()
        center_x = screen.x() + (screen.width() - width) // 2
        center_y = screen.y() + (screen.height() - height) // 2

        # Counts overlays already open at construction time -- build_overlay()
        # only adds `self` to _open_windows *after* this runs, so index 0
        # here always means "no other overlay is currently up", i.e. this one
        # is centered exactly like before.
        index = sum(1 for w in _open_windows if isinstance(w, _LockOverlay))
        if index == 0:
            self.move(center_x, center_y)
            return

        step = index % _CASCADE_MAX_STEPS
        offset = _CASCADE_OFFSET_PX * (step + 1)
        x = center_x + offset
        y = center_y + offset

        max_x = screen.x() + screen.width() - width
        max_y = screen.y() + screen.height() - height
        x = max(screen.x(), min(x, max_x))
        y = max(screen.y(), min(y, max_y))
        self.move(x, y)

    def _on_unblock_click(self, process_name):
        # Close this overlay first, not just hide it — otherwise its own
        # raise_()/activateWindow() tick would keep stealing focus back
        # from the reason dialog below every 50ms.
        self.close()
        build_unblock_reason_dialog(process_name)

    def _tick(self):
        if self._closed:
            return
        elapsed_ms = (time.time() - self._start_time) * 1000
        fraction = min(1.0, elapsed_ms / self._duration_ms)
        self._progress.setValue(int(fraction * 1000))

        status = session_manager.get_status()
        minutes, seconds = divmod(status["secondsRemaining"], 60)
        self._time_label.setText(f"Time remaining: {minutes}m {seconds}s")

        try:
            self.raise_()
            self.activateWindow()
        except Exception:
            pass

        if fraction >= 1.0:
            self.close()

    def close(self):
        if self._closed:
            return True
        self._closed = True
        self._tick_timer.stop()
        if self._blackout_win is not None:
            self._blackout_win.close()
        # close() only hides the widget -- it doesn't delete the C++ object,
        # so `destroyed` (which build_overlay() relies on to prune
        # _open_windows) never fires. Without this, every closed overlay
        # stays counted in _position_window()'s cascade index, so the very
        # next soft-lock warning (and every one after) lands off-center
        # instead of back at dead-center once nothing is actually open.
        _open_windows.discard(self)
        return super().close()


class _UnblockReasonDialog(QWidget):
    """Lets the offending app through for the rest of the session by
    removing it from processBlocklist (see
    session_manager.remove_process_from_blocklist) -- reachable straight
    from a violation's lock overlay, same destination as the "Pick Apps to
    Blocklist" picker's own removal flow.

    Styled to match _LockOverlay's dark card (same _OVERLAY_STYLESHEET) --
    it used to fall back to styles.qss's light #PopupBg theme, so clicking
    "Unblock" on the dark overlay dropped straight into a plain white popup
    that looked like a completely different, older app."""

    def __init__(self, process_name):
        super().__init__(
            None,
            Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool,
        )
        # Same reasoning as _LockOverlay's identical block: WA_StyledBackground
        # is what makes the QSS border-radius below round the window's real
        # corners instead of being ignored.
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setObjectName("LockOverlayCard")
        self.setStyleSheet(_OVERLAY_STYLESHEET)
        self.resize(360, 220)
        self._process_name = process_name

        shadow = QGraphicsDropShadowEffect(self)
        shadow.setBlurRadius(32)
        shadow.setOffset(0, 6)
        shadow.setColor(QColor(0, 0, 0, 140))
        self.setGraphicsEffect(shadow)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(22, 18, 22, 18)
        layout.setSpacing(10)

        prompt = QLabel(f"Unblock {process_name} for the rest of this session — why?")
        prompt.setObjectName("LockOverlayMessage")
        prompt.setWordWrap(True)
        prompt.setAlignment(Qt.AlignCenter)
        layout.addWidget(prompt)

        self._reason_edit = QLineEdit()
        layout.addWidget(self._reason_edit)

        self._status_label = QLabel()
        self._status_label.setObjectName("LockOverlayTime")
        self._status_label.setWordWrap(True)
        self._status_label.setAlignment(Qt.AlignCenter)
        layout.addWidget(self._status_label)

        button_row = QHBoxLayout()
        unblock_button = QPushButton("Unblock")
        cancel_button = QPushButton("Cancel")
        unblock_button.clicked.connect(self._confirm)
        cancel_button.clicked.connect(self.close)
        button_row.addStretch(1)
        button_row.addWidget(unblock_button)
        button_row.addWidget(cancel_button)
        button_row.addStretch(1)
        layout.addLayout(button_row)

        self._reason_edit.returnPressed.connect(self._confirm)
        self._reason_edit.setFocus()

    def _confirm(self):
        reason = self._reason_edit.text().strip()
        if not reason:
            self._status_label.setStyleSheet("color: #E06C75;")
            self._status_label.setText("Enter a reason before unblocking.")
            return
        _, exception_entry = session_manager.remove_process_from_blocklist(self._process_name, reason)
        if exception_entry is None:
            # Session ended (naturally, nuclear, or via the API) between this
            # popup opening and the user confirming — nothing to unblock
            # anymore, and applying it anyway would silently bleed into
            # whatever session starts next.
            self._status_label.setStyleSheet("color: #E06C75;")
            self._status_label.setText("Session already ended — nothing to unblock.")
            return
        # Bring the app's own (already-minimized, from the violation that
        # triggered this dialog) window straight up, rather than leaving the
        # user to go find it in the taskbar and hope hard lock doesn't just
        # minimize it right back -- is_blocked() is False from this point on
        # so nothing will.
        import enforcer
        enforcer.restore_window_for_process(self._process_name)
        # Confirm success as text in this still-focused, already-on-top
        # dialog instead of popping a brand new window -- hard lock's own
        # SetForegroundWindow calls (for any other still-open violation) can
        # bury a fresh QMessageBox behind other windows before the user
        # sees it, making a successful unblock look like it silently failed.
        self._status_label.setStyleSheet("color: #7FD88F;")
        self._status_label.setText(f"{self._process_name} unblocked for the rest of this session.")
        self._reason_edit.setEnabled(False)
        QTimer.singleShot(1500, self.close)
