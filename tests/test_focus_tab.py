"""Widget-level tests for the Focus tab (qt_ui/focus_tab.py) -- session
start/status, pause/resume, nuclear end -- split back out of
tests/test_finished_tab.py alongside the tab itself."""
import pytest

import qt_ui.focus_tab as focus_tab
import session_manager

# The Focus tab also reads the review engine, so every test here runs on a throwaway review
# database, never the real one.
pytestmark = pytest.mark.usefixtures("isolate_review_db")


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


def test_start_a_session_of_the_minutes_you_type(qtbot, isolate_state):
    tab = _tab(qtbot)
    tab._custom_edit.setText("45")
    tab._hard_button.click()
    tab._start_button.click()
    status = session_manager.get_status()
    assert status["isActive"] and status["lockMode"] == "hard"
    assert 44 * 60 <= status["secondsRemaining"] <= 45 * 60


def test_there_is_only_a_custom_length_no_presets(qtbot, isolate_state):
    tab = _tab(qtbot)
    assert not hasattr(tab, "_preset_buttons")
    labels = [b.text() for b in tab.findChildren(focus_tab.QPushButton)]
    assert not any(t in ("15 min", "25 min", "45 min", "60 min") for t in labels)


def test_the_length_box_starts_at_the_last_length_used(qtbot, isolate_state):
    import config
    config.update_config(lambda cfg: cfg.update({"last_duration_minutes": 40}))
    assert _tab(qtbot)._custom_edit.text() == "40"


def test_starting_while_one_runs_pauses_it_instead_of_replacing_it(qtbot, isolate_state):
    session_manager.start_session(30, "soft", [], [], source="task", event_id="a", event_title="Alpha")
    tab = _tab(qtbot)
    tab._custom_edit.setText("20")
    tab._start_button.click()
    status = session_manager.get_status()
    assert status["isActive"] and status["source"] == "manual" and not status["isPaused"]
    waiting = status["parkedSessions"]
    assert [p["eventTitle"] for p in waiting] == ["Alpha"]
    assert waiting[0]["secondsRemaining"] > 29 * 60  # its time was frozen, not lost


def test_a_bad_length_shows_an_error_and_starts_nothing(qtbot, isolate_state):
    tab = _tab(qtbot)
    tab._custom_edit.setText("soon")
    tab._start_button.click()
    assert not tab._start_error.isHidden() and not session_manager.get_status()["isActive"]
    tab._custom_edit.setText("0")
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


# ---------------------------------------------- a running review is the big time ----

import review_store  # noqa: E402


def _running_first_attempt(elapsed=278):
    review_store.publish_first_attempt(1, "2026-10-25T12:00:00", elapsed, False)


def test_a_running_review_is_the_big_time_and_the_session_sits_behind_it(qtbot, isolate_state, isolate_review_db):
    session_manager.start_session(25, "hard", [], [], source="task", event_id="t1", event_title="Chinese")
    session_manager.pause_session()
    _running_first_attempt()
    tab = _tab(qtbot)
    tab.show()
    assert tab._now_name.text() == "First attempt" and tab._now_chip.text() == "Reviewing"
    assert tab._clock_label.text() in ("04:38", "04:39", "04:40") and tab._clock_caption.text() == "elapsed"
    assert "Chinese" in tab._status_label.text() and "hard" in tab._status_label.text()
    assert tab._behind_row is not None and tab._behind_row.name_label.text() == "Chinese"
    assert tab._summary_label.text() == "1 reviewing · 1 waiting"


def test_pause_pauses_the_review_not_the_session(qtbot, isolate_state, isolate_review_db):
    session_manager.start_session(25, "soft", [], [], source="task", event_id="t1", event_title="Chinese")
    _running_first_attempt()
    tab = _tab(qtbot)
    tab._pause_button.click()
    assert review_store.get_first_attempt()["isPaused"]
    assert not session_manager.get_status()["isPaused"]


def test_resume_on_the_session_row_sets_the_review_aside_and_resumes_the_session(qtbot, isolate_state, isolate_review_db):
    session_manager.start_session(25, "soft", [], [], source="task", event_id="t1", event_title="Chinese")
    session_manager.pause_session()
    _running_first_attempt()
    tab = _tab(qtbot)
    tab.show()
    tab._behind_row.switch_button.click()
    assert not session_manager.get_status()["isPaused"]
    assert review_store.get_first_attempt()["isPaused"]
    tab._refresh_status()
    assert tab._now_name.text() == "Chinese"      # the session is the big time again
    assert tab._behind_row is None


def test_a_paused_review_is_not_the_big_time(qtbot, isolate_state, isolate_review_db):
    session_manager.start_session(25, "soft", [], [], source="task", event_id="t1", event_title="Chinese")
    review_store.publish_first_attempt(1, "2026-10-25T12:00:00", 90, True)
    tab = _tab(qtbot)
    assert tab._now_name.text() == "Chinese" and tab._behind_row is None


def test_set_aside_reviews_are_listed_with_the_waiting_sessions(qtbot, isolate_state, isolate_review_db, monkeypatch):
    monkeypatch.setattr(review_store, "list_parked_reviews", lambda: [
        {"token": "a", "problemName": "Tones drill", "elapsedSeconds": 125},
        {"token": "b", "problemName": "Radicals", "elapsedSeconds": 40}])
    tab = _tab(qtbot)
    assert sorted(r.name_label.text() for r in tab._review_rows.values()) == ["Review: Radicals", "Review: Tones drill"]
    assert tab._summary_label.text() == "2 waiting"


def test_the_rules_summary_counts_apps_websites_and_profiles(qtbot, isolate_state):
    import config
    config.update_config(lambda cfg: cfg.update({
        "processBlocklist": ["a.exe", "b.exe"], "domainWhitelist": ["x.com"], "browserProfileBlocklist": ["p1"]}))
    text = _tab(qtbot)._rules_summary.text()
    assert "2 apps blocked" in text and "1 website allowed" in text and "1 browser profile blocked" in text
