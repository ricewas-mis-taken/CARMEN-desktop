"""The always-on-top mini timer: what it shows and its one Pause button."""
import qt_ui.mini_timer as mini_timer
import session_manager as sm


def test_idle_shows_no_session_and_cannot_pause(qtbot, isolate_state):
    win = mini_timer.MiniTimer()
    qtbot.addWidget(win)
    assert (win._label, win._clock, win._fraction) == ("No session", "--:--", None)
    assert not win._button.isEnabled()


def test_timed_session_counts_down_inside_a_ring(qtbot, isolate_state):
    sm.start_session(25, "soft", [], [], source="task", event_id="a", event_title="Essay")
    win = mini_timer.MiniTimer()
    qtbot.addWidget(win)
    assert win._label == "Essay"
    assert win._clock in ("25:00", "24:59")
    assert 0 <= win._fraction < 0.01


def test_burnout_is_a_stopwatch_without_a_ring(qtbot, isolate_state):
    sm.start_session(480, "soft", [], [], is_burnout=True, event_title="Deep work")
    win = mini_timer.MiniTimer()
    qtbot.addWidget(win)
    assert win._fraction is None
    assert win._clock == "00:00"


def test_pause_button_pauses_and_resumes(qtbot, isolate_state):
    sm.start_session(25, "soft", [], [])
    win = mini_timer.MiniTimer()
    qtbot.addWidget(win)
    assert win._button.text() == "Pause"
    win._button.click()
    assert sm.get_status()["isPaused"] and win._button.text() == "Resume"
    assert win._label.endswith("(paused)")
    win._button.click()
    assert not sm.get_status()["isPaused"] and win._button.text() == "Pause"


def test_pomodoro_ring_uses_the_phase_length(qtbot, isolate_state):
    sm.start_pomodoro_session(25, 5, 4, "soft", [], [])
    win = mini_timer.MiniTimer()
    qtbot.addWidget(win)
    assert win._label.startswith("Focus 1/4")
    assert 0 <= win._fraction < 0.01


def test_clock_formatting():
    assert mini_timer.format_clock(65) == "01:05"
    assert mini_timer.format_clock(3725) == "1:02:05"
    assert mini_timer.format_clock(-3) == "00:00"


def test_window_is_frameless_on_top_and_resizable(qtbot, isolate_state):
    from PySide6.QtCore import Qt
    win = mini_timer.MiniTimer()
    qtbot.addWidget(win)
    flags = win.windowFlags()
    assert flags & Qt.WindowStaysOnTopHint and flags & Qt.FramelessWindowHint
    win.resize(300, 340)
    assert (win.width(), win.height()) == (300, 340)
