"""A long sync error must stay readable, and the sign-in password can be revealed."""
import qt_ui.account_widget as account_widget


def test_password_is_hidden_until_show_is_ticked(qtbot):
    from PySide6.QtWidgets import QLineEdit
    tab = account_widget._AuthTab("Log in", on_submit=lambda e, p: None)
    qtbot.addWidget(tab)
    assert tab._password_edit.echoMode() == QLineEdit.Password
    tab._show_password.setChecked(True)
    assert tab._password_edit.echoMode() == QLineEdit.Normal
    tab._show_password.setChecked(False)
    assert tab._password_edit.echoMode() == QLineEdit.Password


def test_a_long_sync_error_wraps_and_stays_up_long_enough_to_read(qtbot, monkeypatch):
    import auth_manager
    monkeypatch.setattr(auth_manager, "is_logged_in", lambda: False)
    area = account_widget.AccountArea()
    qtbot.addWidget(area)
    message = "The cloud database needs its one-time setup (run schema.sql, then 001_direct_sync.sql)."
    area._set_status(f"Sync error: {message}", auto_clear=True, duration_ms=account_widget._ERROR_CLEAR_MS)
    assert area._status_label.wordWrap()
    assert message in area._status_label.toolTip()
    assert area._status_timer.interval() == account_widget._ERROR_CLEAR_MS > account_widget._STATUS_CLEAR_MS
