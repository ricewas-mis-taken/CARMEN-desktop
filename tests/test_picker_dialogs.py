"""Widget-level tests for the ported picker_gui dialogs
(qt_ui/picker_dialogs.py). The timer dialog test is the most important one
here: it protects the documented requirement that starting a session from
this dialog calls the exact same session_manager.start_session() the
Flask API's POST /session/start uses, so the desktop UI, tray, and browser
extension always agree on session state."""
from unittest.mock import MagicMock

import qt_ui.picker_dialogs as picker_dialogs
import session_manager


def test_timer_dialog_start_calls_start_session_with_expected_args(qtbot, isolate_state, monkeypatch):
    spy = MagicMock(wraps=session_manager.start_session)
    monkeypatch.setattr(session_manager, "start_session", spy)

    win = picker_dialogs._TimerDialog()
    qtbot.addWidget(win)

    win._duration_edit.setText("42")
    win._hard_radio.setChecked(True)
    win._start()

    spy.assert_called_once()
    args = spy.call_args.args
    assert args[0] == 42.0
    assert args[1] == "hard"


def test_timer_dialog_rejects_invalid_duration(qtbot, isolate_state):
    win = picker_dialogs._TimerDialog()
    qtbot.addWidget(win)

    win._duration_edit.setText("not a number")
    win._start()

    assert "valid duration" in win._status_label.text().lower()
    assert not session_manager.is_active()


def test_timer_dialog_rejects_zero_duration(qtbot, isolate_state):
    win = picker_dialogs._TimerDialog()
    qtbot.addWidget(win)

    win._duration_edit.setText("0")
    win._start()

    assert not session_manager.is_active()


def test_blocklist_picker_not_active_saves_to_config(qtbot, isolate_state):
    import config

    win = picker_dialogs._BlocklistPicker()
    qtbot.addWidget(win)

    win._checklist.add_row("manualapp.exe", "manualapp.exe", checked=True)
    win._save()

    saved = config.load_config()
    assert "manualapp.exe" in saved["processBlocklist"]


def test_manual_entry_requires_exe_suffix(qtbot, isolate_state):
    win = picker_dialogs._BlocklistPicker()
    qtbot.addWidget(win)

    win._add_manual_entry("notanexe")
    assert "must end in .exe" in win._manual_status.text().lower()
    assert not win._checklist.has_key("notanexe")


def test_blocklist_picker_active_session_lists_current_blocklist_for_unblocking(qtbot, isolate_state):
    # Regression coverage: mid-session "Save" opens _ReasonDialog, which
    # previously raised NameError at construction time (QScrollArea was
    # used but not imported after the checklist unification edit) --
    # nothing in this test file exercised _ReasonDialog before, so that
    # bug shipped past every other test here undetected.
    session_manager.start_session(25, "soft", ["already.exe"], [])

    win = picker_dialogs._BlocklistPicker()
    qtbot.addWidget(win)
    # Mid-session, the picker lists what's currently blocked (not the full
    # installed-apps list) -- "already.exe" should already be a row.
    assert win._checklist.has_key("already.exe")
    win._checklist._checkboxes_by_key["already.exe"][0].setChecked(True)

    win._save()  # must not raise

    assert len(picker_dialogs._open_windows) >= 1
    reason_dialog = next(w for w in picker_dialogs._open_windows if isinstance(w, picker_dialogs._ReasonDialog))
    qtbot.addWidget(reason_dialog)
    assert "already.exe" in reason_dialog._reason_edits


def test_reason_dialog_confirm_requires_all_reasons(qtbot, isolate_state, monkeypatch):
    from PySide6.QtWidgets import QMessageBox
    monkeypatch.setattr(QMessageBox, "information", staticmethod(lambda *a, **k: None))

    session_manager.start_session(25, "soft", ["a.exe", "b.exe"], [])
    win = picker_dialogs._ReasonDialog(["a.exe", "b.exe"])
    qtbot.addWidget(win)
    win.show()

    win._reason_edits["a.exe"].setText("needed")
    win._confirm()
    assert "b.exe" in win._status_label.text()
    assert win.isVisible()

    win._reason_edits["b.exe"].setText("also needed")
    win._confirm()
    status = session_manager.get_status()
    assert "a.exe" not in status["processBlocklist"]
    assert "b.exe" not in status["processBlocklist"]
