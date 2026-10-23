"""The Start Session dialog must refuse durations the session can't be built from."""
import pytest

import session_manager
import qt_ui.picker_dialogs as picker_dialogs


@pytest.mark.parametrize("text", ["nan", "inf", "-5", "0", "abc", "", "1e18", "1000001"])
def test_bad_durations_show_a_message_and_start_nothing(qtbot, isolate_state, text):
    dialog = picker_dialogs._TimerDialog()
    qtbot.addWidget(dialog)
    dialog._duration_edit.setText(text)
    dialog._start()
    assert "valid duration" in dialog._status_label.text()
    assert not session_manager.get_status()["isActive"]


def test_a_normal_duration_still_starts(qtbot, isolate_state):
    dialog = picker_dialogs._TimerDialog()
    qtbot.addWidget(dialog)
    dialog._duration_edit.setText("25")
    dialog._start()
    assert session_manager.get_status()["isActive"]
    session_manager.end_session()
