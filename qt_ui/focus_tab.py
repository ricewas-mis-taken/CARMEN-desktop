"""Focus tab: session start/status/pause/nuclear-end controls, split back
out into its own tab from qt_ui/finished_tab.py (which had absorbed them
during the Tk->Qt migration) so starting a session and reviewing finished
ones are separate tabs again.
"""
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

import history_gui
import picker_gui
import session_manager
import qt_ui.nuclear_dialog as nuclear_dialog
from qt_ui.next_up_widget import NextUpLabel

STATUS_REFRESH_MS = 1000


class FocusTab(QWidget):
    def __init__(self):
        super().__init__()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 22, 24, 20)
        layout.setSpacing(8)

        title = QLabel("Focus")
        title.setStyleSheet("font-size: 20px; font-weight: 700;")
        layout.addWidget(title)

        layout.addWidget(NextUpLabel())

        self._status_label = QLabel()
        self._status_label.setWordWrap(True)
        layout.addWidget(self._status_label)

        layout.addSpacing(6)

        button_row = QHBoxLayout()
        button_row.setSpacing(8)

        start_button = QPushButton("Start Focus Session")
        start_button.setProperty("class", "AccentButton")
        start_button.clicked.connect(picker_gui.open_timer_dialog)
        button_row.addWidget(start_button)

        whitelist_button = QPushButton("Pick Apps to Whitelist")
        whitelist_button.setProperty("class", "SecondaryButton")
        whitelist_button.clicked.connect(picker_gui.open_whitelist_picker)
        button_row.addWidget(whitelist_button)

        # Pause/Resume and Nuclear End only make sense while a session is
        # actually running -- same reasoning as tray.py's pystray menu items
        # (visible=_session_active there); _refresh_status re-evaluates this
        # every tick so these disappear on their own once a session ends.
        self._pause_button = QPushButton("Pause / Resume Session")
        self._pause_button.setProperty("class", "SecondaryButton")
        self._pause_button.clicked.connect(self._pause_resume)
        button_row.addWidget(self._pause_button)

        self._nuclear_button = QPushButton("End Session (Nuclear)")
        self._nuclear_button.setProperty("class", "SecondaryButton")
        self._nuclear_button.clicked.connect(self._open_nuclear_dialog)
        button_row.addWidget(self._nuclear_button)

        history_button = QPushButton("Session History")
        history_button.setProperty("class", "SecondaryButton")
        history_button.clicked.connect(history_gui.open_history_viewer)
        button_row.addWidget(history_button)

        button_row.addStretch(1)
        layout.addLayout(button_row)
        layout.addStretch(1)

        # Tracks isActive across ticks so _refresh_status can notice a
        # session ending (naturally, manually, or via nuclear end) and kick
        # the Finished tab to refresh, since that's the only other view that
        # needs to pick up the newly-appended history entry.
        self._was_active = session_manager.get_status()["isActive"]

        self._status_timer = QTimer(self)
        self._status_timer.timeout.connect(self._refresh_status)
        self._status_timer.start(STATUS_REFRESH_MS)
        self._refresh_status()

    def _pause_resume(self):
        if session_manager.get_status()["isPaused"]:
            session_manager.resume_session()
        else:
            session_manager.pause_session()

    def _open_nuclear_dialog(self):
        # qt_ui/nuclear_dialog.py was written for tray.py's pystray menu
        # item, so it expects an "icon" it can call .notify()/.update_menu()
        # on -- neither is meaningful from an in-window button, so this
        # passes a no-op stand-in rather than reworking the dialog's public
        # signature just for this second caller. Imported lazily: tray.py
        # imports calendar_gui -> qt_ui.main_window -> this module, so a
        # top-level "import tray" here would be circular.
        import tray

        nuclear_dialog.open_nuclear_reason_dialog(_NullIcon(), tray.format_end_summary)

    def _refresh_status(self):
        status = session_manager.get_status()
        active = status["isActive"]
        if self._was_active and not active:
            # Lazy import: qt_ui/main_window.py imports this module to build
            # the "focus" page, so a top-level import here would be circular.
            import qt_ui.main_window as main_window

            main_window.refresh_calendar_views()
        self._was_active = active
        self._pause_button.setVisible(active)
        self._nuclear_button.setVisible(active)
        if not active:
            self._status_label.setText("No active focus session.")
            return
        minutes, seconds = divmod(status["secondsRemaining"], 60)
        paused = " (paused)" if status["isPaused"] else ""
        source_note = ""
        if status.get("source") == "calendar-event" and status.get("eventTitle"):
            source_note = f"\nFrom calendar event: {status['eventTitle']}"
        elif status.get("source") == "task" and status.get("eventTitle"):
            source_note = f"\nTask: {status['eventTitle']}"
        self._status_label.setText(
            f"Active session{paused} — {minutes}m {seconds}s remaining\n"
            f"Lock mode: {status['lockMode']}   Violations: {status['violationCount']}"
            f"{source_note}"
        )


class _NullIcon:
    """Stand-in for the pystray Icon that qt_ui/nuclear_dialog.py expects,
    used when opening it from the in-window Nuclear End button rather than
    the tray menu -- there's no tray notification/menu-refresh to perform
    from here, so both calls are no-ops."""

    def notify(self, *args, **kwargs):
        pass

    def update_menu(self):
        pass
