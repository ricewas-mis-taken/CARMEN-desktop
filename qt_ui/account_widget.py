"""Sidebar account area (Phase 4 Part 2): a "Sign In" button when logged
out, or a small avatar+email row when logged in. Clicking the row opens a
menu with "Sync now" / "Log out".

Threading: auth_manager.login()/signup() and sync_client.sync_now() are
blocking network calls -- never run them on the Qt main thread. Follows
this app's existing pattern (see enforcer.py, sync_scheduler.py): a plain
daemon threading.Thread does the blocking call, then hands the result
back via qt_gui_thread.run_on_gui_thread() so only the Qt main thread
ever touches widgets.

Popup lifetime: _AuthDialog is an unparented top-level QWidget (same
"frameless dialog" pattern as qt_ui/board_tab.py's _AddTaskDialog /
qt_ui/review_tab.py's popups), so it needs the same _register_popup()
strong-reference trick or it would be garbage-collected the instant
_open_auth_dialog() returns.
"""
import threading

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMenu,
    QPushButton,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

import auth_manager
import qt_gui_thread
import sync_client
import sync_scheduler
import sync_trigger

_STATUS_CLEAR_MS = 4000


class _AccountRow(QWidget):
    """Avatar circle + email, the whole row clickable -- opens the
    account menu. Reuses the app's one accent color (#5B8DEF, same as
    qt_ui/board_tab.py's _ImportanceBadge circle) rather than inventing a
    new one, since it sits on the sidebar's dark background where that
    color already reads clearly."""

    def __init__(self, on_click):
        super().__init__()
        self._on_click = on_click
        self.setCursor(Qt.PointingHandCursor)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(14, 6, 14, 6)
        layout.setSpacing(8)

        self._avatar = QLabel("")
        self._avatar.setFixedSize(26, 26)
        self._avatar.setAlignment(Qt.AlignCenter)
        self._avatar.setStyleSheet(
            "background: #5B8DEF; color: white; border-radius: 13px; "
            "font-size: 12px; font-weight: 700;"
        )
        layout.addWidget(self._avatar)

        self._email_label = QLabel("")
        self._email_label.setStyleSheet("color: #E8EAED; font-size: 12px;")
        layout.addWidget(self._email_label, 1)

    def set_email(self, email):
        email = email or ""
        self._avatar.setText(email[0].upper() if email else "?")
        self._email_label.setText(email)
        self._email_label.setToolTip(email)

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self._on_click()
        super().mousePressEvent(event)


class AccountArea(QWidget):
    def __init__(self):
        super().__init__()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 8, 0, 12)
        layout.setSpacing(2)

        self._signin_button = QPushButton("Sign In")
        self._signin_button.setProperty("class", "NavButton")
        self._signin_button.clicked.connect(self._open_auth_dialog)
        layout.addWidget(self._signin_button)

        self._account_row = _AccountRow(on_click=self._open_account_menu)
        layout.addWidget(self._account_row)

        self._status_label = QLabel("")
        self._status_label.setStyleSheet("color: #8A8F98; font-size: 11px; padding: 0px 14px;")
        self._status_label.setVisible(False)
        layout.addWidget(self._status_label)

        self._status_timer = QTimer(self)
        self._status_timer.setSingleShot(True)
        self._status_timer.timeout.connect(lambda: self._status_label.setVisible(False))

        self.refresh()
        if auth_manager.is_logged_in():
            # Startup sync: kicked off here (as the sidebar is being
            # built) rather than blocking window construction on it --
            # sync_now() is a network call and must never run on the Qt
            # main thread. "Fresh on open" just means it's started early,
            # not that the window waits for it to finish.
            self._set_status("Syncing…")
            threading.Thread(target=self._background_sync, daemon=True).start()

    def refresh(self):
        """Re-reads auth_manager.is_logged_in() and switches state. Called
        on construction (app startup) and after any login/logout."""
        logged_in = auth_manager.is_logged_in()
        self._signin_button.setVisible(not logged_in)
        self._account_row.setVisible(logged_in)
        if logged_in:
            user = auth_manager.get_current_user()
            self._account_row.set_email(user.get("email") if user else None)
            sync_trigger.enable()
            sync_scheduler.enable()
        else:
            self._status_label.setVisible(False)
            sync_trigger.disable()
            sync_scheduler.disable()

    # --- sign in / sign up ---

    def _open_auth_dialog(self):
        _register_popup(_AuthDialog(on_success=self._on_login_success))

    def _on_login_success(self):
        self.refresh()
        self._set_status("Syncing…")
        threading.Thread(target=self._background_sync, daemon=True).start()

    # --- logged-in menu ---

    def _open_account_menu(self):
        menu = QMenu(self)
        menu.addAction("Sync now").triggered.connect(self._manual_sync)
        menu.addAction("Log out").triggered.connect(self._logout)
        menu.exec(self._account_row.mapToGlobal(self._account_row.rect().bottomLeft()))

    def _manual_sync(self):
        self._set_status("Syncing…")
        threading.Thread(target=self._background_sync, daemon=True).start()

    def _background_sync(self):
        result = sync_client.sync_now()
        qt_gui_thread.run_on_gui_thread(lambda: self._show_sync_result(result))

    def _show_sync_result(self, result):
        if result.not_logged_in:
            return  # logged out while a sync was in flight -- nothing to show
        if result.success:
            self._set_status("Synced just now", auto_clear=True)
        else:
            self._set_status(f"Sync error: {result.error}", auto_clear=True)

    def _logout(self):
        sync_trigger.disable()
        sync_scheduler.disable()
        auth_manager.logout()
        self.refresh()

    # --- status line ---

    def _set_status(self, text, auto_clear=False):
        self._status_label.setText(text)
        self._status_label.setVisible(True)
        if auto_clear:
            self._status_timer.start(_STATUS_CLEAR_MS)
        else:
            self._status_timer.stop()


class _AuthDialog(QWidget):
    def __init__(self, on_success):
        super().__init__(None, Qt.WindowStaysOnTopHint)
        self.setObjectName("PopupBg")
        self.setWindowTitle("Carmen Focus — Sign In")
        self.resize(360, 260)
        self._on_success = on_success

        layout = QVBoxLayout(self)

        self._tabs = QTabWidget()
        self._login_tab = _AuthTab(
            submit_label="Log In",
            on_submit=self._login,
        )
        self._signup_tab = _AuthTab(
            submit_label="Sign Up",
            on_submit=self._signup,
        )
        self._tabs.addTab(self._login_tab, "Login")
        self._tabs.addTab(self._signup_tab, "Sign Up")
        layout.addWidget(self._tabs)

        self.show()

    def _login(self, email, password):
        tab = self._login_tab
        tab.set_busy(True)

        def work():
            success, error = auth_manager.login(email, password)
            qt_gui_thread.run_on_gui_thread(lambda: self._on_login_result(success, error))

        threading.Thread(target=work, daemon=True).start()

    def _on_login_result(self, success, error):
        self._login_tab.set_busy(False)
        if success:
            self.close()
            self._on_success()
            return
        self._login_tab.show_error(_classify_login_error(error))

    def _signup(self, email, password):
        tab = self._signup_tab
        tab.set_busy(True)

        def work():
            success, error = auth_manager.signup(email, password)
            qt_gui_thread.run_on_gui_thread(lambda: self._on_signup_result(success, error))

        threading.Thread(target=work, daemon=True).start()

    def _on_signup_result(self, success, error):
        self._signup_tab.set_busy(False)
        if not success:
            self._signup_tab.show_error(_classify_signup_error(error))
            return
        # Deliberately does NOT switch to logged-in state -- login will
        # keep failing with "confirm your email" until the account is
        # confirmed, per auth_manager.login()'s own _classify_auth_error.
        self._signup_tab.show_info("Account created. Check your email to confirm it before signing in.")


def _classify_login_error(error):
    if not error:
        return "Sign-in failed. Please try again."
    lowered = error.lower()
    if "incorrect email or password" in lowered:
        return "Invalid email or password."
    if "reach the sign-in server" in lowered or "connection" in lowered:
        return "Couldn't reach the server -- check your connection."
    return error


def _classify_signup_error(error):
    if not error:
        return "Sign-up failed. Please try again."
    lowered = error.lower()
    if "already exists" in lowered:
        return "An account with that email already exists."
    if "reach the sign-in server" in lowered or "connection" in lowered:
        return "Couldn't reach the server -- check your connection."
    return error


class _AuthTab(QWidget):
    """One Login/Sign Up tab's form: email + masked password + submit +
    an inline error/info label hidden until there's something to show."""

    def __init__(self, submit_label, on_submit):
        super().__init__()
        self._on_submit = on_submit

        layout = QVBoxLayout(self)

        layout.addWidget(QLabel("Email"))
        self._email_edit = QLineEdit()
        layout.addWidget(self._email_edit)

        layout.addWidget(QLabel("Password"))
        self._password_edit = QLineEdit()
        self._password_edit.setEchoMode(QLineEdit.Password)
        layout.addWidget(self._password_edit)

        self._message_label = QLabel("")
        self._message_label.setWordWrap(True)
        self._message_label.setVisible(False)
        layout.addWidget(self._message_label)

        layout.addStretch(1)

        self._submit_button = QPushButton(submit_label)
        self._submit_button.setProperty("class", "AccentButton")
        self._submit_button.clicked.connect(self._submit)
        layout.addWidget(self._submit_button)

    def _submit(self):
        email = self._email_edit.text().strip()
        password = self._password_edit.text()
        if not email or not password:
            self.show_error("Email and password are required.")
            return
        self._message_label.setVisible(False)
        self._on_submit(email, password)

    def set_busy(self, busy):
        self._submit_button.setEnabled(not busy)

    def show_error(self, text):
        self._message_label.setStyleSheet("color: #c62828; font-size: 12px;")
        self._message_label.setText(text)
        self._message_label.setVisible(True)

    def show_info(self, text):
        self._message_label.setStyleSheet("color: #1F2328; font-size: 12px;")
        self._message_label.setText(text)
        self._message_label.setVisible(True)


_popup_refs = set()


def _register_popup(popup):
    _popup_refs.add(popup)
    popup.destroyed.connect(lambda: _popup_refs.discard(popup))
