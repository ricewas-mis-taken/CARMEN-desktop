"""Widget-level tests for the Focus tab (qt_ui/focus_tab.py) -- session
start/status, pause/resume, nuclear end -- split back out of
tests/test_finished_tab.py alongside the tab itself."""
import qt_ui.focus_tab as focus_tab
import session_manager


def test_no_active_session_shows_inactive_message(qtbot, isolate_state):
    tab = focus_tab.FocusTab()
    qtbot.addWidget(tab)
    assert "no active" in tab._status_label.text().lower()


def test_active_session_shows_status_details(qtbot, isolate_state):
    session_manager.start_session(25, "hard", ["a.exe"], [])
    tab = focus_tab.FocusTab()
    qtbot.addWidget(tab)
    assert "hard" in tab._status_label.text().lower()
    assert tab._clock_caption.text() == "remaining"
    assert tab._clock_label.text() in ("25:00", "24:59")
    assert tab._now_chip.text() == "Running"


def test_burnout_session_shows_until_burnout_not_a_countdown(qtbot, isolate_state):
    """Regression test: a burnout session used to be indistinguishable from
    a normal one on the Focus tab -- it showed a countdown from the full
    8-hour internal ceiling instead of elapsed time, since burnout-ness used
    to live only in the Tasks tab card's own ephemeral widget state."""
    session_manager.start_session(25, "hard", ["a.exe"], [], is_burnout=True)
    tab = focus_tab.FocusTab()
    qtbot.addWidget(tab)
    assert tab._clock_caption.text() == "elapsed"
    assert tab._clock_label.text() == "00:00"


def test_pause_resume_button_toggles_session_state(qtbot, isolate_state):
    session_manager.start_session(25, "soft", [], [])
    tab = focus_tab.FocusTab()
    qtbot.addWidget(tab)

    assert not session_manager.get_status()["isPaused"]
    tab._pause_resume()
    assert session_manager.get_status()["isPaused"]
    tab._pause_resume()
    assert not session_manager.get_status()["isPaused"]


def test_pause_and_nuclear_buttons_hidden_without_active_session(qtbot, isolate_state):
    tab = focus_tab.FocusTab()
    qtbot.addWidget(tab)
    tab.show()
    tab._refresh_status()
    assert not tab._pause_button.isVisible()
    assert not tab._nuclear_button.isVisible()
    assert not tab._edit_rules_button.isVisible()


def test_pause_and_nuclear_buttons_shown_with_active_session(qtbot, isolate_state):
    session_manager.start_session(25, "soft", [], [])
    tab = focus_tab.FocusTab()
    qtbot.addWidget(tab)
    tab.show()
    tab._refresh_status()
    assert tab._pause_button.isVisible()
    assert tab._nuclear_button.isVisible()
    assert tab._edit_rules_button.isVisible()


# ------------------------------------------------- start a session from here ----

def _tab(qtbot):
    tab = focus_tab.FocusTab()
    qtbot.addWidget(tab)
    return tab


def test_start_a_preset_session_with_nothing_running(qtbot, isolate_state):
    tab = _tab(qtbot)
    tab._preset_buttons[45].click()
    tab._hard_button.click()
    tab._start_button.click()
    status = session_manager.get_status()
    assert status["isActive"] and status["lockMode"] == "hard"
    assert 44 * 60 <= status["secondsRemaining"] <= 45 * 60


def test_starting_while_one_runs_pauses_it_instead_of_replacing_it(qtbot, isolate_state):
    session_manager.start_session(30, "soft", [], [], source="task", event_id="a", event_title="Alpha")
    tab = _tab(qtbot)
    tab._custom_edit.setText("20")
    tab._custom_edit.textEdited.emit("20")
    tab._start_button.click()
    status = session_manager.get_status()
    assert status["isActive"] and status["source"] == "manual" and not status["isPaused"]
    waiting = status["parkedSessions"]
    assert [p["eventTitle"] for p in waiting] == ["Alpha"]
    assert waiting[0]["secondsRemaining"] > 29 * 60  # its time was frozen, not lost


def test_a_custom_length_beats_the_preset(qtbot, isolate_state):
    tab = _tab(qtbot)
    tab._preset_buttons[15].click()
    tab._custom_edit.setText("7")
    tab._custom_edit.textEdited.emit("7")
    assert tab._chosen_minutes() == 7
    assert not any(p.isChecked() for p in tab._preset_buttons.values())


def test_a_bad_length_shows_an_error_and_starts_nothing(qtbot, isolate_state):
    tab = _tab(qtbot)
    tab._custom_edit.setText("soon")
    tab._custom_edit.textEdited.emit("soon")
    tab._start_button.click()
    assert not tab._start_error.isHidden() and not session_manager.get_status()["isActive"]
    tab._custom_edit.setText("0")
    tab._custom_edit.textEdited.emit("0")
    tab._start_button.click()
    assert not session_manager.get_status()["isActive"]


def test_the_timer_dialog_pauses_a_running_session_too(qtbot, isolate_state):
    import qt_ui.picker_dialogs as picker_dialogs

    session_manager.start_session(30, "soft", [], [], source="task", event_id="a", event_title="Alpha")
    dialog = picker_dialogs._TimerDialog()
    qtbot.addWidget(dialog)
    dialog._duration_edit.setText("10")
    dialog._start()
    status = session_manager.get_status()
    assert status["source"] == "manual" and [p["eventTitle"] for p in status["parkedSessions"]] == ["Alpha"]
