"""The mini timer's Finish button for reviews, and the clock stopping once it is clicked."""
import qt_ui.mini_timer as mini_timer
import qt_ui.review_tab as review_tab
import session_manager as sm


def _review_session():
    sm.start_session(480, "soft", [], [], source="review", event_id="t", event_title="Calc - Integrals review",
                     review_problem_name="Parts drill", review_subject_name="Integrals", review_problem_id=7,
                     is_burnout=True)


def test_a_plain_task_has_no_finish_button(qtbot, isolate_state):
    sm.start_session(25, "soft", [], [], source="task", event_id="a", event_title="Essay")
    win = mini_timer.MiniTimer()
    qtbot.addWidget(win)
    win.show()
    assert not win._finish.isVisible()


def test_a_review_has_finish_beside_pause_and_clicking_it_files_the_review(qtbot, isolate_state, monkeypatch):
    _review_session()
    filed = []
    monkeypatch.setattr(review_tab, "finish_review_for_status", lambda status: filed.append(status) or True)
    win = mini_timer.MiniTimer()
    qtbot.addWidget(win)
    win.resize(*mini_timer.MIN_SIZE)
    win.show()
    assert win._finish.isVisible() and win._finish.text() == "Finish"
    assert not win._finish.geometry().intersects(win._button.geometry())
    assert win.rect().contains(win._finish.geometry()) and win.rect().contains(win._button.geometry())
    win._finish.click()
    assert len(filed) == 1 and filed[0]["reviewProblemId"] == 7
    assert sm.get_status()["isActive"]          # the review step ends the session, not this click


def test_the_clock_stops_when_finish_is_clicked(qtbot, isolate_state, monkeypatch):
    _review_session()
    seconds = {"now": 65}
    monkeypatch.setattr(mini_timer.tasks_store, "worked_seconds", lambda *args, **kw: seconds["now"])
    monkeypatch.setattr(review_tab, "finish_review_for_status", lambda status: True)
    win = mini_timer.MiniTimer()
    qtbot.addWidget(win)
    win.refresh()
    assert win._clock == "01:05"
    win._finish.click()
    seconds["now"] = 400
    win.refresh()
    assert win._clock == "01:05"                                   # not counting up any more
    assert not win._button.isEnabled() and not win._finish.isEnabled() and not win._end.isEnabled()
    sm.end_session()                                               # the review was filed: its timer is gone
    win.refresh()
    assert win._frozen == {}


def test_a_review_timed_on_its_own_finishes_through_its_token(qtbot, isolate_state, monkeypatch):
    view = {"token": "tok", "problemName": "Parts drill", "elapsedSeconds": 90, "active": True, "isPaused": False}
    monkeypatch.setattr(mini_timer, "timers", lambda: [{"key": ("review", "tok"), "kind": "review", "view": view}])
    filed = []
    monkeypatch.setattr(review_tab, "finish_review_from_card", lambda token, name, secs: filed.append((token, name, secs)) or True)
    win = mini_timer.MiniTimer()
    qtbot.addWidget(win)
    win.show()
    assert win._finish.isVisible()
    win._finish.click()
    assert filed == [("tok", "Parts drill", 90)]
    view["elapsedSeconds"] = 500
    win.refresh()
    assert win._clock == "01:30"


def test_a_finish_that_could_not_start_does_not_freeze_the_clock(qtbot, isolate_state, monkeypatch):
    _review_session()
    monkeypatch.setattr(review_tab, "finish_review_for_status", lambda status: False)
    win = mini_timer.MiniTimer()
    qtbot.addWidget(win)
    win._finish.click()
    assert win._frozen == {} and win._button.isEnabled()
