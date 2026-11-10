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
    from qt_ui import pause_reason
    pause_reason._OPEN[-1]._choose("break")
    assert sm.get_status()["isPaused"] and win._button.text() == "Resume"
    assert "paused" not in win._label
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


def test_the_window_wears_the_chosen_theme(qtbot, isolate_state, monkeypatch):
    import theme
    win = mini_timer.MiniTimer()
    qtbot.addWidget(win)
    win.show()
    for name in ("Dusk", "Ember"):
        monkeypatch.setattr(theme, "current_colours", lambda n=name: theme.resolve({"name": n}))
        image = win.grab().toImage()
        corner = image.pixelColor(win.width() // 2, 3)
        assert corner.name().upper() == theme.THEMES[name]["sidebar"].upper()


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
    win.resize(240, 240)
    assert win._edges_at(QPointF(100, 120)) == Qt.Edge(0)
    assert win._edges_at(QPointF(239, 239)) == Qt.RightEdge | Qt.BottomEdge
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


def test_it_comes_back_if_pushed_out_of_view_but_not_after_the_x(qtbot, isolate_state):
    win = mini_timer.MiniTimer()
    qtbot.addWidget(win)
    win.show()
    win.hide()                                  # what a notification can do to it
    win._keep_in_view()
    assert win.isVisible()
    win._close.click()                          # the x: the user wants it gone
    win._keep_in_view()
    assert not win.isVisible()


def test_a_close_that_was_not_the_x_does_not_stick(qtbot, isolate_state):
    win = mini_timer.MiniTimer()
    qtbot.addWidget(win)
    win.show()
    win.close()                                 # e.g. closed by the system, not by the x
    win._keep_in_view()
    assert win.isVisible()


def test_it_is_pulled_back_when_left_off_every_screen(qtbot, isolate_state):
    win = mini_timer.MiniTimer()
    qtbot.addWidget(win)
    win.show()
    win.move(-9000, -9000)
    win._keep_in_view()
    assert win._on_a_screen()


def test_dragging_the_middle_moves_and_never_resizes(qtbot, isolate_state):
    from PySide6.QtCore import QPointF, Qt
    win = mini_timer.MiniTimer()
    qtbot.addWidget(win)
    win.resize(240, 240)
    win.move(100, 100)
    win._drag_offset = QPointF(50, 50)
    assert win._edges_at(QPointF(100, 120)) == Qt.Edge(0)
    assert win._resize_start is None
    assert (win.width(), win.height()) == (240, 240)


def test_edge_resize_is_clamped_and_cannot_get_huge(qtbot, isolate_state):
    from PySide6.QtCore import QPointF, QRect, Qt
    win = mini_timer.MiniTimer()
    qtbot.addWidget(win)
    win.setGeometry(100, 100, 240, 240)
    win._resize_edges = Qt.RightEdge | Qt.BottomEdge
    win._resize_start = (QPointF(340, 340), QRect(win.geometry()))
    win._resize_to(QPointF(5000, 5000))
    assert (win.width(), win.height()) == mini_timer.MAX_SIZE
    win._resize_to(QPointF(-5000, -5000))
    assert (win.width(), win.height()) == mini_timer.MIN_SIZE


def _two_sessions():
    sm.start_session(30, "soft", [], [], source="task", event_id="a", event_title="Alpha")
    sm.pause_session("break")
    sm.start_session(30, "soft", [], [], source="task", event_id="b", event_title="Beta")


def test_arrows_switch_between_the_running_session_and_the_ones_waiting(qtbot, isolate_state):
    _two_sessions()
    win = mini_timer.MiniTimer()
    qtbot.addWidget(win)
    win.show()
    assert win._prev.isVisible() and win._next.isVisible()
    assert "Beta" in win._label and win._button.text() == "Pause"
    win._next.click()
    assert "Alpha" in win._label and win._button.text() == "Switch to this"
    win._button.click()                                    # the waiting one takes over
    assert sm.get_status()["eventTitle"] == "Alpha"
    win._prev.click() if len(win._items) > 1 else None
    assert "Beta" in win._label or "Alpha" in win._label


def test_end_on_a_waiting_session_ends_only_that_one(qtbot, isolate_state):
    _two_sessions()
    win = mini_timer.MiniTimer()
    qtbot.addWidget(win)
    win.show()
    win._next.click()
    win._end.doubled.emit()
    status = sm.get_status()
    assert status["eventTitle"] == "Beta" and not status.get("parkedSessions")
    assert not win._prev.isVisible()                       # one timer left: no arrows


def test_a_single_timer_shows_a_long_pause_and_a_small_end_and_no_arrows(qtbot, isolate_state):
    sm.start_session(30, "soft", [], [], source="task", event_id="a", event_title="Alpha")
    win = mini_timer.MiniTimer()
    qtbot.addWidget(win)
    win.show()
    assert not win._prev.isVisible() and not win._next.isVisible()
    assert win._end.text() == "End" and not win._end.geometry().intersects(win._button.geometry())
    centre = win._button.geometry().center().x()
    assert abs(centre - win.width() / 2) <= 1                       # Pause sits in the middle
    win._end.click()                                                # one click only says how to end
    assert win._hint and sm.get_status()["isActive"]
    win._end.doubled.emit()
    assert not sm.get_status()["isActive"]


def test_the_pause_budget_is_not_on_the_mini_timer(qtbot, isolate_state):
    sm.start_session(30, "soft", [], [], source="task", event_id="a", event_title="Alpha")
    win = mini_timer.MiniTimer()
    qtbot.addWidget(win)
    win.show()
    assert not hasattr(win, "_budget") and win.toolTip() == ""
