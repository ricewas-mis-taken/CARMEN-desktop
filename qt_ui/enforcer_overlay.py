"""Qt port of enforcer.py's lock-overlay popup and its follow-up whitelist-
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
from PySide6.QtWidgets import (
    QApplication,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

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


def build_overlay(message, duration_ms, offending_process_name=None):
    win = _LockOverlay(message, duration_ms, offending_process_name)
    _open_windows.add(win)
    win.destroyed.connect(lambda: _open_windows.discard(win))
    win.show()
    return win


def build_whitelist_reason_dialog(process_name):
    win = _WhitelistReasonDialog(process_name)
    _open_windows.add(win)
    win.destroyed.connect(lambda: _open_windows.discard(win))
    win.show()
    return win


class _LockOverlay(QWidget):
    def __init__(self, message, duration_ms, offending_process_name=None):
        super().__init__(
            None,
            Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool,
        )
        self._closed = False
        self._duration_ms = duration_ms
        self._start_time = time.time()

        width, height = 380, 150
        if offending_process_name:
            height += 40
        self.resize(width, height)
        self._position_window(width, height)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 18, 20, 16)

        message_label = QLabel(message)
        message_label.setWordWrap(True)
        message_label.setAlignment(Qt.AlignCenter)
        layout.addWidget(message_label)

        self._time_label = QLabel()
        self._time_label.setAlignment(Qt.AlignCenter)
        layout.addWidget(self._time_label)

        self._progress = QProgressBar()
        self._progress.setRange(0, 1000)
        self._progress.setValue(0)
        self._progress.setTextVisible(False)
        self._progress.setFixedHeight(8)
        layout.addWidget(self._progress)

        if offending_process_name:
            whitelist_button = QPushButton("Whitelist")
            whitelist_button.clicked.connect(
                lambda: self._on_whitelist_click(offending_process_name)
            )
            layout.addWidget(whitelist_button, alignment=Qt.AlignCenter)

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

    def _on_whitelist_click(self, process_name):
        # Close this overlay first, not just hide it — otherwise its own
        # raise_()/activateWindow() tick would keep stealing focus back
        # from the reason dialog below every 50ms.
        self.close()
        build_whitelist_reason_dialog(process_name)

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
        # close() only hides the widget -- it doesn't delete the C++ object,
        # so `destroyed` (which build_overlay() relies on to prune
        # _open_windows) never fires. Without this, every closed overlay
        # stays counted in _position_window()'s cascade index, so the very
        # next soft-lock warning (and every one after) lands off-center
        # instead of back at dead-center once nothing is actually open.
        _open_windows.discard(self)
        return super().close()


class _WhitelistReasonDialog(QWidget):
    def __init__(self, process_name):
        super().__init__(None, Qt.WindowStaysOnTopHint)
        self.setObjectName("PopupBg")
        self.setWindowTitle("Carmen Focus — Whitelist App")
        self.resize(360, 180)
        self._process_name = process_name

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 16, 20, 16)

        prompt = QLabel(f"Whitelist {process_name} for the rest of this session — why?")
        prompt.setWordWrap(True)
        prompt.setAlignment(Qt.AlignCenter)
        layout.addWidget(prompt)

        self._reason_edit = QLineEdit()
        layout.addWidget(self._reason_edit)

        self._status_label = QLabel()
        self._status_label.setStyleSheet("color: #c62828;")
        self._status_label.setAlignment(Qt.AlignCenter)
        layout.addWidget(self._status_label)

        button_row = QHBoxLayout()
        whitelist_button = QPushButton("Whitelist")
        cancel_button = QPushButton("Cancel")
        whitelist_button.clicked.connect(self._confirm)
        cancel_button.clicked.connect(self.close)
        button_row.addStretch(1)
        button_row.addWidget(whitelist_button)
        button_row.addWidget(cancel_button)
        button_row.addStretch(1)
        layout.addLayout(button_row)

        self._reason_edit.returnPressed.connect(self._confirm)
        self._reason_edit.setFocus()

    def _confirm(self):
        reason = self._reason_edit.text().strip()
        if not reason:
            self._status_label.setText("Enter a reason before whitelisting.")
            return
        _, addition = session_manager.add_process_to_whitelist(self._process_name, reason)
        if addition is None:
            # Session ended (naturally, nuclear, or via the API) between this
            # popup opening and the user confirming — nothing to whitelist
            # anymore, and applying it anyway would silently bleed into
            # whatever session starts next.
            self._status_label.setText("Session already ended — nothing to whitelist.")
            return
        self.close()
