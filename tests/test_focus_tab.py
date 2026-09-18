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
    text = tab._status_label.text()
    assert "hard" in text.lower()
    assert "remaining" in text.lower()


def test_burnout_session_shows_until_burnout_not_a_countdown(qtbot, isolate_state):
    """Regression test: a burnout session used to be indistinguishable from
    a normal one on the Focus tab -- it showed a countdown from the full
    8-hour internal ceiling instead of "Until burnout" with elapsed time,
    since burnout-ness used to live only in the Tasks tab card's own
    ephemeral widget state, invisible to every other UI surface."""
    session_manager.start_session(25, "hard", ["a.exe"], [], is_burnout=True)
    tab = focus_tab.FocusTab()
    qtbot.addWidget(tab)
    text = tab._status_label.text().lower()
    assert "until burnout" in text
    assert "remaining" not in text


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


def test_pause_and_nuclear_buttons_shown_with_active_session(qtbot, isolate_state):
    session_manager.start_session(25, "soft", [], [])
    tab = focus_tab.FocusTab()
    qtbot.addWidget(tab)
    tab.show()
    tab._refresh_status()
    assert tab._pause_button.isVisible()
    assert tab._nuclear_button.isVisible()
