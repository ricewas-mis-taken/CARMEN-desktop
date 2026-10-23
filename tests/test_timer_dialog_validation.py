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


# --- the same limits everywhere a duration is typed or passed in ---

@pytest.mark.parametrize("minutes", [float("nan"), float("inf"), 0, -1, 1e18, 1_000_001, True, "25"])
def test_is_valid_duration_rejects_unusable_values(minutes):
    assert not session_manager.is_valid_duration(minutes)


def test_is_valid_duration_accepts_normal_values():
    assert session_manager.is_valid_duration(25) and session_manager.is_valid_duration(0.5)
    assert session_manager.is_valid_duration(session_manager.MAX_DURATION_MINUTES)


def test_a_pomodoro_break_that_would_overflow_is_refused_up_front(isolate_state):
    """Regression: an absurd break length was accepted, then overflowed later,
    inside a status poll, when the focus block ended."""
    with pytest.raises(ValueError):
        session_manager.start_pomodoro_session(25, 1e18, 2, "soft", [], [])
    assert not session_manager.get_status()["isActive"]
    with pytest.raises(ValueError):
        session_manager.start_session(1e18, "soft", [], [])


def test_pomodoro_dialog_refuses_nan_and_huge_numbers(qtbot):
    import qt_ui.tasks_tab as tasks_tab
    for focus, brk in (("nan", "5"), ("25", "inf"), ("25", "1e18")):
        dialog = tasks_tab._PomodoroDialog()
        qtbot.addWidget(dialog)
        dialog._focus_edit.setText(focus)
        dialog._break_edit.setText(brk)
        dialog._on_start()
        assert dialog._result is None and "valid" in dialog._status_label.text()


def test_task_card_ignores_unusable_durations(qtbot, isolate_state, tmp_path, monkeypatch):
    import qt_ui.tasks_tab as tasks_tab
    import tasks_store
    monkeypatch.setattr(tasks_store, "TASKS_PATH", str(tmp_path / "tasks.json"))
    task = tasks_store.create_task({"name": "Study", "targetMinutes": 30})
    card = tasks_tab._TaskCard(task, on_changed=lambda: None)
    qtbot.addWidget(card)
    for text in ("nan", "inf", "1e18", "0", "x"):
        card._duration_edit.setText(text)
        card._start_task()  # must neither raise nor start anything
        assert not session_manager.get_status()["isActive"]
