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


def test_background_is_solid_black():
    assert mini_timer.BACKGROUND.alpha() == 255 and mini_timer.BACKGROUND.name() == "#000000"


def test_has_a_close_x_and_no_resize_grip(qtbot, isolate_state):
    from PySide6.QtWidgets import QSizeGrip
    win = mini_timer.MiniTimer()
    qtbot.addWidget(win)
    win.show()
    assert win.findChildren(QSizeGrip) == []
    assert win._close.x() > win.width() - 40 and win._close.y() < 20
    assert not win._button.geometry().intersects(win._close.geometry())
    win._close.click()
    assert not win.isVisible()


def test_edges_and_corners_are_resize_handles(qtbot, isolate_state):
    from PySide6.QtCore import QPointF, Qt
    win = mini_timer.MiniTimer()
    qtbot.addWidget(win)
    win.resize(200, 240)
    assert win._edges_at(QPointF(100, 120)) == Qt.Edge(0)
    assert win._edges_at(QPointF(199, 239)) == Qt.RightEdge | Qt.BottomEdge
    assert win._edges_at(QPointF(2, 120)) == Qt.LeftEdge
    assert win._edges_at(QPointF(100, 1)) == Qt.TopEdge


def test_position_and_size_come_back_next_launch(qtbot, isolate_state):
    first = mini_timer.MiniTimer()
    qtbot.addWidget(first)
    first.setGeometry(120, 130, 260, 300)
    first.close()
    second = mini_timer.MiniTimer()
    qtbot.addWidget(second)
    g = second.geometry()
    assert (g.x(), g.y(), g.width(), g.height()) == (120, 130, 260, 300)


def test_a_saved_spot_on_a_missing_monitor_is_ignored():
    from PySide6.QtCore import QRect
    screens = [QRect(0, 0, 1920, 1080)]
    assert mini_timer.visible_geometry([100, 100, 200, 230], screens) == [100, 100, 200, 230]
    assert mini_timer.visible_geometry([5000, 100, 200, 230], screens) is None
    assert mini_timer.visible_geometry([100, 100, 10, 10], screens) is None
    for junk in (None, "x", [1, 2, 3], [1, 2, "a", 4], [True, 1, 200, 200]):
        assert mini_timer.visible_geometry(junk, screens) is None
