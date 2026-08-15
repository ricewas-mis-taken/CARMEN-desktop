"""Qt port of picker_gui.py's three dialogs: the app blocklist picker, its
follow-up mid-session reason dialog, and the start-session timer dialog.

Uses the shared qt_ui/checklist.py component for the blocklist picker's
checkbox list -- the same component the event editor's process/domain
blocklist checklists use (qt_ui/event_editor.py), replacing both this
module's original Stage-1 ad-hoc duplicate and the old Tk
checklist_widget.py.

All three windows are non-modal (.show(), not .exec()) -- same as the
original Tk versions, which never used grab_set().
"""
import os

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QButtonGroup,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QRadioButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

import config
import installed_apps
import session_history
import session_manager
import qt_ui.checklist as checklist

_open_windows = set()


def _track(win):
    _open_windows.add(win)
    win.destroyed.connect(lambda: _open_windows.discard(win))
    return win


def open_blocklist_picker():
    _track(_BlocklistPicker()).show()


def open_timer_dialog():
    _track(_TimerDialog()).show()


class _Checklist:
    """Thin wrapper around qt_ui.checklist's function-based API, giving
    callers in this module the same object-with-methods shape the Stage-1
    ad-hoc _ScrollableChecklist had (add_row / add_separator_label /
    checked_keys / has_key), so _BlocklistPicker's logic below didn't need
    to change shape when the underlying checklist implementation was
    unified in Stage 5."""

    def __init__(self, height=280):
        self.widget, self._checkboxes_by_key, self._add_row = checklist.build_checklist(
            [], set(), height=height
        )

    def add_row(self, key, label, checked):
        self._add_row(key, label, checked)

    def add_separator_label(self, text):
        self._add_row.add_separator_label(text)

    def checked_keys(self):
        return checklist.get_checked(self._checkboxes_by_key)

    def has_key(self, key):
        return key.lower() in self._checkboxes_by_key


class _BlocklistPicker(QWidget):
    def __init__(self):
        super().__init__(None, Qt.WindowStaysOnTopHint)
        self.setObjectName("PopupBg")
        self.setWindowTitle("Carmen Focus — Pick Apps to Blocklist")
        self.resize(440, 640)

        self._session_active = session_manager.is_active()
        if self._session_active:
            # Mid-session, this picker isn't for adding new restrictions --
            # it lists what's *currently* blocked so the user can pick which
            # of those to lift (see _save()/_ReasonDialog below), the same
            # accountability-gated "explain why" flow as the lock overlay's
            # own per-violation Unblock button, just reachable in general
            # instead of only from an active violation.
            self._current_blocklist = list(session_manager.get_status()["processBlocklist"])
        else:
            self._saved = {name.lower() for name in config.load_config().get("processBlocklist", [])}

        layout = QVBoxLayout(self)

        if self._session_active:
            instructions = (
                "A session is active — these apps are currently blocked.\n"
                "Check any you want to unblock. You'll be asked to explain each\n"
                "one before it's let through."
            )
        else:
            instructions = "Check the apps to block during a focus session.\nPreviously saved picks are pre-checked."
        instructions_label = QLabel(instructions)
        instructions_label.setAlignment(Qt.AlignCenter)
        layout.addWidget(instructions_label)

        self._checklist = _Checklist()
        layout.addWidget(self._checklist.widget)

        if self._session_active:
            if not self._current_blocklist:
                self._checklist.add_separator_label("Nothing is currently blocked.")
            for process_name in self._current_blocklist:
                self._checklist.add_row(process_name, process_name, checked=False)
        else:
            self._add_quick_readd_rows()

            apps = installed_apps.list_installed_apps()
            if not apps:
                self._checklist.add_separator_label("No installed apps found.")
            for app in apps:
                self._checklist.add_row(
                    app["process_name"],
                    f"{app['display_name']}   ({app['process_name']})",
                    checked=app["process_name"].lower() in self._saved,
                )

            manual_label = QLabel("Not listed? Add by name or file:")
            layout.addWidget(manual_label)

            manual_row = QHBoxLayout()
            self._manual_edit = QLineEdit()
            browse_button = QPushButton("Browse...")
            add_button = QPushButton("Add")
            browse_button.clicked.connect(self._browse_for_exe)
            add_button.clicked.connect(lambda: self._add_manual_entry(self._manual_edit.text()))
            self._manual_edit.returnPressed.connect(lambda: self._add_manual_entry(self._manual_edit.text()))
            manual_row.addWidget(self._manual_edit)
            manual_row.addWidget(browse_button)
            manual_row.addWidget(add_button)
            layout.addLayout(manual_row)

            self._manual_status = QLabel()
            self._manual_status.setStyleSheet("color: #c62828;")
            layout.addWidget(self._manual_status)

        self._status_label = QLabel()
        self._status_label.setStyleSheet("color: #2e7d32;")
        layout.addWidget(self._status_label)

        button_label = "Unblock Selected" if self._session_active else "Save Blocklist"
        save_button = QPushButton(button_label)
        save_button.clicked.connect(self._save)
        layout.addWidget(save_button, alignment=Qt.AlignCenter)

    def _add_quick_readd_rows(self):
        # Only offered when picking the blocklist for the *next* session, not
        # mid-session -- a quick way to re-check whatever the previous
        # session actually ended up blocklisting.
        history = session_history.load_all()
        prev_session = history[-1] if history else None
        prev_apps = prev_session.get("processBlocklist", []) if prev_session else []

        if prev_apps:
            self._checklist.add_separator_label("From your last session — quick re-add:")
            for process_name in prev_apps:
                self._checklist.add_row(process_name, process_name, checked=False)

    def _add_manual_entry(self, process_name):
        # Reduce to just the basename even for a typed (not browsed) entry --
        # is_blocked() and enforcement everywhere else compare on
        # process name alone, never a full path.
        process_name = os.path.basename(process_name.strip())
        if not process_name:
            self._manual_status.setText("Enter an exe name or browse for a file.")
            return
        if not process_name.lower().endswith(".exe"):
            self._manual_status.setText("Process name must end in .exe.")
            return
        self._checklist.add_row(process_name, process_name, checked=True)
        self._manual_status.setText("")
        self._manual_edit.setText("")

    def _browse_for_exe(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Pick an executable", "", "Executables (*.exe);;All files (*.*)"
        )
        if path:
            self._add_manual_entry(os.path.basename(path))

    def _save(self):
        selected = self._checklist.checked_keys()

        if self._session_active:
            if not selected:
                self._status_label.setText("Nothing selected — nothing to unblock.")
                return
            self.close()
            _track(_ReasonDialog(selected)).show()
            return

        config.update_config(lambda cfg: cfg.update({"processBlocklist": selected}))
        self._status_label.setText(f"Saved {len(selected)} app(s) to the blocklist.")


class _ReasonDialog(QWidget):
    """Second page shown after picking apps to unblock mid-session -- one
    reason field per selected app, all required, before
    remove_process_from_blocklist() actually applies any of them."""

    def __init__(self, process_names):
        super().__init__(None, Qt.WindowStaysOnTopHint)
        self.setObjectName("PopupBg")
        self.setWindowTitle("Carmen Focus — Explain Unblocks")
        self.resize(440, 480)
        self._process_names = process_names

        layout = QVBoxLayout(self)

        prompt = QLabel("Why does each of these need to be unblocked for this session?")
        prompt.setWordWrap(True)
        prompt.setAlignment(Qt.AlignCenter)
        layout.addWidget(prompt)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.NoFrame)
        scroll.setStyleSheet("background: transparent;")
        content = QWidget()
        content.setObjectName("PopupBg")
        content_layout = QVBoxLayout(content)
        self._reason_edits = {}
        for process_name in process_names:
            name_label = QLabel(process_name)
            name_label.setStyleSheet("font-weight: bold;")
            content_layout.addWidget(name_label)
            edit = QLineEdit()
            content_layout.addWidget(edit)
            self._reason_edits[process_name] = edit
        content_layout.addStretch(1)
        scroll.setWidget(content)
        layout.addWidget(scroll)

        self._status_label = QLabel()
        self._status_label.setStyleSheet("color: #c62828;")
        layout.addWidget(self._status_label)

        button_row = QHBoxLayout()
        confirm_button = QPushButton("Confirm")
        cancel_button = QPushButton("Cancel")
        confirm_button.clicked.connect(self._confirm)
        cancel_button.clicked.connect(self.close)
        button_row.addStretch(1)
        button_row.addWidget(confirm_button)
        button_row.addWidget(cancel_button)
        button_row.addStretch(1)
        layout.addLayout(button_row)

    def _confirm(self):
        reasons = {name: edit.text().strip() for name, edit in self._reason_edits.items()}
        missing = [name for name, reason in reasons.items() if not reason]
        if missing:
            self._status_label.setText(f"Enter a reason for: {', '.join(missing)}")
            return

        import enforcer

        unblocked = 0
        for process_name, reason in reasons.items():
            _, exception_entry = session_manager.remove_process_from_blocklist(process_name, reason)
            if exception_entry is not None:
                unblocked += 1
                # Bring the app's window straight up rather than leaving it
                # minimized for the user to go dig out of the taskbar --
                # see enforcer.restore_window_for_process()'s docstring.
                enforcer.restore_window_for_process(process_name)

        self.close()
        if unblocked < len(reasons):
            # Session ended mid-form (naturally, nuclear, or via the API)
            # before every entry could be applied.
            QMessageBox.warning(
                None, "Carmen Focus",
                f"Session ended before all apps could be unblocked — "
                f"{unblocked} of {len(reasons)} were unblocked.",
            )
        else:
            QMessageBox.information(
                None, "Carmen Focus", f"Unblocked {unblocked} app(s) for the rest of the session."
            )


class _TimerDialog(QWidget):
    def __init__(self):
        super().__init__(None, Qt.WindowStaysOnTopHint)
        self.setObjectName("PopupBg")
        self.setWindowTitle("Carmen Focus — Start Session")
        self.resize(300, 260)

        cfg = config.load_config()
        layout = QVBoxLayout(self)

        layout.addWidget(QLabel("Duration (minutes)"))
        self._duration_edit = QLineEdit(str(cfg.get("last_duration_minutes", 25)))
        self._duration_edit.setAlignment(Qt.AlignCenter)
        layout.addWidget(self._duration_edit)

        layout.addWidget(QLabel("Lock mode"))
        mode_row = QHBoxLayout()
        self._soft_radio = QRadioButton("Soft")
        self._hard_radio = QRadioButton("Hard")
        mode_group = QButtonGroup(self)
        mode_group.addButton(self._soft_radio)
        mode_group.addButton(self._hard_radio)
        if cfg.get("last_lock_mode", "soft") == "hard":
            self._hard_radio.setChecked(True)
        else:
            self._soft_radio.setChecked(True)
        mode_row.addWidget(self._soft_radio)
        mode_row.addWidget(self._hard_radio)
        layout.addLayout(mode_row)

        process_count = len(cfg.get("processBlocklist", []))
        count_label = QLabel(f"Using saved blocklist: {process_count} app(s)")
        count_label.setStyleSheet("color: #888;")
        layout.addWidget(count_label)

        self._status_label = QLabel()
        self._status_label.setStyleSheet("color: #c62828;")
        layout.addWidget(self._status_label)

        start_button = QPushButton("Start Session")
        start_button.clicked.connect(self._start)
        layout.addWidget(start_button, alignment=Qt.AlignCenter)

    def _start(self):
        try:
            duration_minutes = float(self._duration_edit.text())
            if duration_minutes <= 0:
                raise ValueError
        except ValueError:
            self._status_label.setText("Enter a valid duration.")
            return

        lock_mode = "hard" if self._hard_radio.isChecked() else "soft"
        current_cfg = config.load_config()
        process_blocklist = current_cfg.get("processBlocklist", [])
        domain_whitelist = current_cfg.get("domainWhitelist", [])

        # Calls the same function POST /session/start uses, so this session
        # is immediately visible to the browser extension via GET /status.
        session_manager.start_session(duration_minutes, lock_mode, process_blocklist, domain_whitelist)

        # Mutates a freshly-loaded config inside update_config()'s lock
        # rather than saving the current_cfg read above wholesale -- that
        # would silently clobber any processBlocklist/domainWhitelist
        # change made concurrently (e.g. a whitelist push from the browser
        # extension) during the window between reading current_cfg and
        # this save, since it was the entire stale dict being written
        # back, not just these two fields.
        config.update_config(lambda cfg: cfg.update({
            "last_duration_minutes": duration_minutes,
            "last_lock_mode": lock_mode,
        }))

        self.close()
