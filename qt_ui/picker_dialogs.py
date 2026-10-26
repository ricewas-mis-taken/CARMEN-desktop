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
import subprocess
import sys

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QButtonGroup,
    QCheckBox,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QRadioButton,
    QScrollArea,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)
from qt_ui.wrap_edit import WrapEdit

import config
import enforcer
import installed_apps
import session_history
import session_manager
import window_tracker
import qt_ui.checklist as checklist
from qt_ui.ui_kit import accent, danger, dialog_title, field_label, role_label

_open_windows = set()


def _track(win):
    _open_windows.add(win)
    win.destroyed.connect(lambda: _open_windows.discard(win))
    return win


def open_blocklist_picker():
    _track(_BlocklistPicker()).show()


def open_rules_picker(on_saved=None):
    """The Focus tab's "Pick rules": apps, allowed websites and browser profiles for the
    NEXT session -- always, even while another session runs (open_blocklist_picker()
    switches to "unblock what is blocked now" in that case)."""
    picker = _BlocklistPicker(for_next_session=True)
    picker.on_saved = on_saved
    _track(picker).show()
    return picker


def open_timer_dialog():
    _track(_TimerDialog()).show()


def open_edit_session_rules():
    _track(_EditSessionRulesDialog()).show()


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
    def __init__(self, for_next_session=False):
        super().__init__(None, Qt.WindowStaysOnTopHint)
        self.setObjectName("PopupBg")
        self.setWindowTitle("Carmen Focus — Session Rules" if for_next_session else "Carmen Focus — Pick Apps to Blocklist")
        self.resize(460, 760 if for_next_session else 640)
        self._domains_checklist = None
        self.on_saved = None  # the Focus tab refreshes its summary line through this

        # Populated below only in the not-session-active branch -- lets
        # _save() tell a picked browser-profile AUMI apart from a picked
        # process name, since both live as keys in the same checklist.
        self._profile_aumi_keys = set()

        self._session_active = False if for_next_session else session_manager.is_active()
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
        layout.setContentsMargins(24, 22, 24, 20)
        layout.setSpacing(10)

        if self._session_active:
            layout.addWidget(dialog_title(
                "Unblock apps",
                "These are blocked right now. Tick the ones to let through. You will say why for each."))
        elif for_next_session:
            layout.addWidget(dialog_title(
                "Session rules",
                "Choose what the next session blocks: apps and browser profiles. "
                "Then list the websites that stay allowed. Saved picks are already ticked."))
        else:
            layout.addWidget(dialog_title(
                "Apps to block", "Tick the apps to block during a focus session. Saved picks are already ticked."))

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

            self._add_browser_profile_rows()

            layout.addWidget(field_label("Not listed?", "add an app by its .exe name, or browse for it"))

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

            self._manual_status = role_label("", "error")
            layout.addWidget(self._manual_status)

            if for_next_session:
                self._build_websites_section(layout)

        self._status_label = QLabel()
        self._status_label.setStyleSheet("color: #157F3B; font-size: 12.5px;")
        layout.addWidget(self._status_label)

        button_label = "Unblock selected" if self._session_active else "Save rules" if for_next_session else "Save blocklist"
        close_button = QPushButton("Close")
        close_button.clicked.connect(self.close)
        save_button = accent(QPushButton(button_label))
        save_button.clicked.connect(self._save)
        footer = QHBoxLayout()
        footer.addStretch(1)
        footer.addWidget(close_button)
        footer.addWidget(save_button)
        layout.addLayout(footer)

    def _build_websites_section(self, layout):
        """The websites that stay allowed (everything else in a browser is off-limits),
        saved as the default domain list the browser extension also uses."""
        layout.addSpacing(6)
        layout.addWidget(field_label("Allowed websites", "everything else in a browser is off-limits"))
        saved = list(config.load_config().get("domainWhitelist", []))
        self._domains_checklist = _Checklist(height=150)
        if not saved:
            self._domains_checklist.add_separator_label("No websites yet. Add the ones you need.")
        for domain in saved:
            self._domains_checklist.add_row(domain, domain, checked=True)
        layout.addWidget(self._domains_checklist.widget)

        row = QHBoxLayout()
        self._domain_edit = QLineEdit()
        self._domain_edit.setPlaceholderText("e.g. docs.google.com")
        add_button = QPushButton("Add")
        self._domain_edit.returnPressed.connect(self._add_domain)
        add_button.clicked.connect(self._add_domain)
        row.addWidget(self._domain_edit)
        row.addWidget(add_button)
        layout.addLayout(row)

    def _add_domain(self):
        domain = self._domain_edit.text().strip().lower()
        for prefix in ("https://", "http://"):
            if domain.startswith(prefix):
                domain = domain[len(prefix):]
        domain = domain.strip("/ ")
        if not domain or " " in domain:
            return
        self._domains_checklist.add_row(domain, domain, checked=True)
        self._domain_edit.setText("")

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

    def _add_browser_profile_rows(self):
        """Lets a session block just one Chrome/Edge profile instead of the
        whole browser (blocking chrome.exe via processBlocklist above blocks
        every profile equally) -- and without needing the browser extension
        installed in that profile at all. Lists every profile Chrome/Edge
        know about (window_tracker.list_known_browser_profiles(), read from
        each browser's Local State file), not just ones with a window open
        right now -- picking a profile to block is usually a precaution
        against it being opened at all, so a closed profile must still be
        offered rather than silently disappearing from the list."""
        known_profiles = window_tracker.list_known_browser_profiles()
        if not known_profiles:
            return
        saved_profiles = {
            aumi for aumi in config.load_config().get("browserProfileBlocklist", [])
        }
        self._checklist.add_separator_label("Browser profiles (blocks just that profile, not the whole browser):")
        for profile in known_profiles:
            self._profile_aumi_keys.add(profile["aumi"])
            label = profile["label"] if profile["is_running"] else f"{profile['label']} (not open right now)"
            self._checklist.add_row(
                profile["aumi"], label, checked=profile["aumi"] in saved_profiles
            )

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

        # The checklist mixes two kinds of keys (process names and browser-
        # profile AUMIs) in one flat list -- split them back apart by the
        # set collected while building the profile rows, since they save
        # into two different config fields.
        profile_keys = [key for key in selected if key in self._profile_aumi_keys]
        process_keys = [key for key in selected if key not in self._profile_aumi_keys]

        changes = {"processBlocklist": process_keys, "browserProfileBlocklist": profile_keys}
        if self._domains_checklist is not None:
            changes["domainWhitelist"] = self._domains_checklist.checked_keys()
        config.update_config(lambda cfg: cfg.update(changes))
        status = f"Saved {len(process_keys)} app(s)"
        if self._profile_aumi_keys:
            status += f", {len(profile_keys)} browser profile(s)"
        if self._domains_checklist is not None:
            status += f" and {len(changes['domainWhitelist'])} website(s)"
        self._status_label.setText(status + ".")
        if self.on_saved is not None:
            self.on_saved()


class _ReasonDialog(QWidget):
    """Second page shown after picking apps to unblock mid-session -- one
    reason field per selected app, all required, before
    remove_process_from_blocklist() actually applies any of them."""

    def __init__(self, process_names):
        super().__init__(None, Qt.WindowStaysOnTopHint)
        self.setObjectName("PopupBg")
        self.setWindowTitle("Carmen Focus — Explain Unblocks")
        self.resize(460, 500)
        self._process_names = process_names

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 22, 24, 20)
        layout.setSpacing(10)
        layout.addWidget(dialog_title(
            "Why unblock?", "Each app needs a reason. It is saved with the session."))

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.NoFrame)
        scroll.setStyleSheet("background: transparent;")
        content = QWidget()
        content.setObjectName("PopupBg")
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(0, 0, 8, 0)
        content_layout.setSpacing(6)
        self._reason_edits = {}
        for process_name in process_names:
            content_layout.addWidget(field_label(process_name))
            edit = WrapEdit()
            content_layout.addWidget(edit)
            self._reason_edits[process_name] = edit
        content_layout.addStretch(1)
        scroll.setWidget(content)
        layout.addWidget(scroll)

        self._status_label = role_label("", "error")
        layout.addWidget(self._status_label)

        button_row = QHBoxLayout()
        confirm_button = accent(QPushButton("Unblock"))
        cancel_button = QPushButton("Cancel")
        confirm_button.clicked.connect(self._confirm)
        cancel_button.clicked.connect(self.close)
        button_row.addStretch(1)
        button_row.addWidget(cancel_button)
        button_row.addWidget(confirm_button)
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
        self.resize(380, 380)

        cfg = config.load_config()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 22, 24, 20)
        layout.setSpacing(10)
        layout.addWidget(dialog_title("Start a focus session", "Blocks your saved apps until the time is up."))

        layout.addWidget(field_label("Minutes"))
        self._duration_edit = QLineEdit(str(cfg.get("last_duration_minutes", 25)))
        layout.addWidget(self._duration_edit)

        layout.addWidget(field_label("Lock mode"))
        mode_row = QHBoxLayout()
        mode_row.setSpacing(18)
        self._soft_radio = QRadioButton("Soft: warn me, then let me stay")
        self._hard_radio = QRadioButton("Hard: send me straight back")
        mode_group = QButtonGroup(self)
        mode_group.addButton(self._soft_radio)
        mode_group.addButton(self._hard_radio)
        if cfg.get("last_lock_mode", "soft") == "hard":
            self._hard_radio.setChecked(True)
        else:
            self._soft_radio.setChecked(True)
        mode_box = QVBoxLayout()
        mode_box.setSpacing(6)
        mode_box.addWidget(self._soft_radio)
        mode_box.addWidget(self._hard_radio)
        layout.addLayout(mode_box)

        process_count = len(cfg.get("processBlocklist", []))
        profile_count = len(cfg.get("browserProfileBlocklist", []))
        count_text = f"Using your saved blocklist: {process_count} app{'s' if process_count != 1 else ''}"
        if profile_count:
            count_text += f", {profile_count} browser profile{'s' if profile_count != 1 else ''}"
        layout.addWidget(role_label(count_text, "hint"))

        layout.addStretch(1)
        self._status_label = role_label("", "error")
        layout.addWidget(self._status_label)

        start_button = accent(QPushButton("Start session"))
        start_button.clicked.connect(self._start)
        cancel_button = QPushButton("Cancel")
        cancel_button.clicked.connect(self.close)
        footer = QHBoxLayout()
        footer.addStretch(1)
        footer.addWidget(cancel_button)
        footer.addWidget(start_button)
        layout.addLayout(footer)

    def _start(self):
        try:
            duration_minutes = float(self._duration_edit.text())
        except ValueError:
            self._status_label.setText("Enter a valid duration.")
            return
        lock_mode = "hard" if self._hard_radio.isChecked() else "soft"
        error = start_manual_session(duration_minutes, lock_mode, parent=self)
        if error:
            self._status_label.setText(error)
            return
        self.close()


def start_manual_session(duration_minutes, lock_mode, parent=None):
    """Starts an a-la-carte focus session with the saved blocklist. Returns None once it
    has started, or the reason it did not (also shown to the user where a dialog helps).

    A session that is already running is paused first, so it waits (frozen, nothing
    enforced for it) and comes back when this one ends -- instead of being replaced
    for good, which is what start_session() does to a running one."""
    try:
        # float() accepts "nan" and "inf", and a huge number is a valid float
        # that session_manager then cannot turn into a time -- the slot raised
        # and the dialog just sat there.
        if not session_manager.is_valid_duration(duration_minutes):
            raise ValueError
    except (TypeError, ValueError):
        return "Enter a valid duration."

    # macOS-only: hard/soft lock can't do anything at all without
    # Accessibility permission (see mac_os/enforcer_mac.py's module
    # docstring) -- gating the interactive "Start Session" button is the
    # stricter of the two options PORT_SPEC.md's Subsystem 2 allows (the
    # weaker one, a persistent warning at app launch, is already handled
    # by main.py._check_accessibility_trust()). Automatic session starts
    # (calendar-triggered, review-triggered) are deliberately NOT gated
    # here -- refusing one of those silently, with no dialog to show an
    # error in, would be worse than letting it start with enforcement
    # degraded, so this only covers the one flow with a UI to refuse in.
    if sys.platform == "darwin" and not enforcer.is_accessibility_trusted():
        box = QMessageBox(parent)
        box.setIcon(QMessageBox.Warning)
        box.setWindowTitle("Accessibility permission needed")
        box.setText(
            "Carmen Focus can't enforce hard/soft lock on macOS without "
            "Accessibility permission. Grant it in System Settings, then "
            "try starting the session again."
        )
        open_settings_button = box.addButton("Open System Settings", QMessageBox.ActionRole)
        box.addButton("Cancel", QMessageBox.RejectRole)
        box.exec()
        if box.clickedButton() is open_settings_button:
            try:
                subprocess.run(
                    ["open", "x-apple.systempreferences:com.apple.preference.security?Privacy_Accessibility"],
                    check=False,
                )
            except Exception:
                pass
        return "Carmen Focus needs Accessibility permission to enforce a session on macOS."

    current_cfg = config.load_config()
    process_blocklist = current_cfg.get("processBlocklist", [])
    domain_whitelist = current_cfg.get("domainWhitelist", [])
    blocked_browser_profiles = current_cfg.get("browserProfileBlocklist", [])

    running = session_manager.get_status()
    if running["isActive"] and not running["isPaused"]:
        session_manager.pause_session()

    # Calls the same function POST /session/start uses, so this session
    # is immediately visible to the browser extension via GET /status.
    session_manager.start_session(
        duration_minutes, lock_mode, process_blocklist, domain_whitelist,
        blocked_browser_profiles=blocked_browser_profiles,
        hide_taskbar_badges=False,
        stop_taskbar_flashing=False,
    )

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
        "last_hide_taskbar_badges": False,
        "last_stop_taskbar_flashing": False,
    }))
    return None


class _EditSessionRulesDialog(QWidget):
    """Lets an already-running session's process blocklist, domain
    whitelist, and lock mode be edited in place, via
    session_manager.update_blocklist() -- previously the only way to change
    any of these was to end the session and start a new one, and lock mode
    couldn't be changed mid-session at all, update_blocklist() itself having
    no caller anywhere in the app. Reachable from FocusTab's "Edit Session
    Rules" button, which only shows while a session is active."""

    def __init__(self):
        super().__init__(None, Qt.WindowStaysOnTopHint)
        self.setObjectName("PopupBg")
        self.setWindowTitle("Carmen Focus — Edit Session Rules")
        self.resize(460, 520)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 22, 24, 20)
        layout.setSpacing(10)

        status = session_manager.get_status()
        layout.addWidget(dialog_title(
            "Edit this session's rules", "Changes apply right away, to the session that is running."))
        if not status["isActive"]:
            layout.addWidget(role_label("No active session to edit.", "hint"))
            return

        layout.addWidget(field_label("Blocked apps", "one per line, or separated by commas"))
        self._process_edit = QTextEdit()
        self._process_edit.setPlainText(", ".join(status["processBlocklist"]))
        layout.addWidget(self._process_edit)

        layout.addWidget(field_label("Allowed websites", "one per line, or separated by commas"))
        self._domain_edit = QTextEdit()
        self._domain_edit.setPlainText(", ".join(status["domainWhitelist"]))
        layout.addWidget(self._domain_edit)

        layout.addWidget(field_label("Lock mode"))
        mode_row = QHBoxLayout()
        mode_row.setSpacing(18)
        self._soft_radio = QRadioButton("Soft")
        self._hard_radio = QRadioButton("Hard")
        mode_group = QButtonGroup(self)
        mode_group.addButton(self._soft_radio)
        mode_group.addButton(self._hard_radio)
        if status["lockMode"] == "hard":
            self._hard_radio.setChecked(True)
        else:
            self._soft_radio.setChecked(True)
        mode_row.addWidget(self._soft_radio)
        mode_row.addWidget(self._hard_radio)
        mode_row.addStretch(1)
        layout.addLayout(mode_row)

        self._status_label = role_label("", "error")
        layout.addWidget(self._status_label)

        save_button = accent(QPushButton("Save changes"))
        save_button.clicked.connect(self._save)
        close_button = QPushButton("Close")
        close_button.clicked.connect(self.close)
        footer = QHBoxLayout()
        footer.addStretch(1)
        footer.addWidget(close_button)
        footer.addWidget(save_button)
        layout.addLayout(footer)

    @staticmethod
    def _parse_list(text):
        # Accepts either one entry per line or a comma-separated line (or a
        # mix of both) -- splitting on commas first, then newlines within
        # each piece, covers both without needing the user to pick one
        # format.
        entries = []
        for line in text.replace(",", "\n").splitlines():
            entry = line.strip()
            if entry:
                entries.append(entry)
        return entries

    def _save(self):
        if not session_manager.get_status()["isActive"]:
            self._status_label.setText("Session already ended — nothing to update.")
            return
        process_blocklist = self._parse_list(self._process_edit.toPlainText())
        domain_whitelist = self._parse_list(self._domain_edit.toPlainText())
        lock_mode = "hard" if self._hard_radio.isChecked() else "soft"
        session_manager.update_blocklist(process_blocklist, domain_whitelist, lock_mode=lock_mode)
        self._status_label.setStyleSheet("color: #157F3B; font-size: 12.5px;")
        self._status_label.setText("Session rules updated.")
