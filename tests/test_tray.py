"""Tests for tray.py's _format_status_text() -- the tray icon tooltip/menu
text, in particular that a burnout session shows elapsed time instead of a
countdown from the internal 8-hour ceiling."""
import session_manager
import tray


def test_no_active_session(isolate_state):
    assert "no active session" in tray._format_status_text().lower()


def test_regular_session_shows_remaining(isolate_state):
    session_manager.start_session(25, "soft", [], [])
    text = tray._format_status_text().lower()
    assert "remaining" in text
    assert "until burnout" not in text


def test_burnout_session_shows_elapsed_not_remaining(isolate_state):
    session_manager.start_session(25, "hard", [], [], is_burnout=True)
    text = tray._format_status_text().lower()
    assert "until burnout" in text
    assert "elapsed" in text
    assert "remaining" not in text
