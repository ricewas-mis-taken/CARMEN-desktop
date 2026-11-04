"""Widget-level tests for the Tasks tab's running card (qt_ui/tasks_tab.py) --
in particular that a review timer running against a task's linked topic
(source="review", same eventId as the task) is recognized as *this* task's
own running session, not some other session locking the card out."""
import pytest

import session_history
import session_manager
import tasks_store
import qt_ui.tasks_tab as tasks_tab


@pytest.fixture
def isolate_tasks(tmp_path, monkeypatch):
    monkeypatch.setattr(tasks_store, "TASKS_PATH", str(tmp_path / "tasks.json"))
    yield


def _make_task(**overrides):
    data = {"name": "Study", "targetMinutes": 30}
    data.update(overrides)
    return tasks_store.create_task(data)


def test_review_session_for_linked_task_shows_as_running(qtbot, isolate_tasks, isolate_state):
    task = _make_task()
    card = tasks_tab._TaskCard(task, on_changed=lambda: None)
    qtbot.addWidget(card)
    card.show()

    session_manager.start_session(
        30, "soft", [], [],
        source="review", event_id=task["id"], event_title="Study - Algebra review",
        review_problem_name="Quadratics 1", review_subject_name="Algebra",
    )
    status = session_manager.get_status()
    card.update_dynamic(status, session_history.load_all())

    assert card._running_panel.isVisible()
    assert not card.property("locked")
    text = card._countdown_label.text()
    assert "Quadratics 1" in text
    assert "Algebra" not in text
    assert "elapsed" not in text.lower()      # the ring carries the time; the line does not repeat it
    assert card._ring._small == "elapsed"


def test_burnout_session_shows_as_stopwatch_even_on_a_fresh_card(qtbot, isolate_tasks, isolate_state):
    """Regression test: burnout-ness used to live only in the specific
    _TaskCard widget instance that clicked "Until I burnout"
    (self._active_is_burnout), so a DIFFERENT card observing the same
    already-running burnout session -- e.g. after switching tabs away and
    back rebuilds the card -- had no way to know it was a burnout session
    and showed a countdown from the full 8-hour ceiling instead."""
    task = _make_task()
    starter_card = tasks_tab._TaskCard(task, on_changed=lambda: None)
    qtbot.addWidget(starter_card)
    starter_card._start_burnout()

    fresh_card = tasks_tab._TaskCard(task, on_changed=lambda: None)
    qtbot.addWidget(fresh_card)
    fresh_card.update_dynamic(session_manager.get_status(), session_history.load_all())

    text = fresh_card._countdown_label.text().lower()
    assert text == "until burnout"
    assert fresh_card._ring._small == "elapsed"


def test_review_session_pause_button_works_for_linked_task(qtbot, isolate_tasks, isolate_state):
    task = _make_task()
    card = tasks_tab._TaskCard(task, on_changed=lambda: None)
    qtbot.addWidget(card)

    session_manager.start_session(
        30, "soft", [], [],
        source="review", event_id=task["id"], event_title="Study - Algebra review",
        review_problem_name="Quadratics 1", review_subject_name="Algebra",
    )
    card.update_dynamic(session_manager.get_status(), session_history.load_all())
    assert card._pause_button.text() == "Pause"

    card._pause_resume()
    from qt_ui import pause_reason
    pause_reason._OPEN[-1]._choose("break")
    assert session_manager.get_status()["isPaused"]
    card.update_dynamic(session_manager.get_status(), session_history.load_all())
    assert card._pause_button.text() == "Resume"

    card._pause_resume()
    assert not session_manager.get_status()["isPaused"]


def test_pomodoro_button_starts_a_pomodoro_session_for_this_task(qtbot, isolate_tasks, isolate_state, monkeypatch):
    """Drives the "Pomodoro" button all the way through to
    session_manager.start_pomodoro_session() -- catches an argument-order
    slip that no session_manager-only test could, by asserting on the
    actual resulting session state instead of a mocked call."""
    task = _make_task(lockMode="hard", processBlocklist=["bad.exe"])
    card = tasks_tab._TaskCard(task, on_changed=lambda: None)
    qtbot.addWidget(card)
    card.show()

    monkeypatch.setattr(tasks_tab._PomodoroDialog, "get_settings", staticmethod(lambda parent=None: (25, 5, 4)))

    card._start_pomodoro()

    status = session_manager.get_status()
    assert status["isActive"]
    assert status["lockMode"] == "hard"
    assert status["processBlocklist"] == ["bad.exe"]
    assert status["eventId"] == task["id"]
    assert status["source"] == "task"
    assert status["pomodoro"] == {
        "focusMinutes": 25, "breakMinutes": 5, "totalCycles": 4,
        "currentCycle": 1, "phase": "focus",
    }

    card.update_dynamic(status, session_history.load_all())
    assert card._running_panel.isVisible()
    assert "Focus 1/4" in card._countdown_label.text()


def test_pomodoro_button_does_nothing_when_dialog_is_cancelled(qtbot, isolate_tasks, isolate_state, monkeypatch):
    task = _make_task()
    card = tasks_tab._TaskCard(task, on_changed=lambda: None)
    qtbot.addWidget(card)

    monkeypatch.setattr(tasks_tab._PomodoroDialog, "get_settings", staticmethod(lambda parent=None: None))

    card._start_pomodoro()

    assert not session_manager.get_status()["isActive"]


def test_pomodoro_dialog_uses_the_light_popup_theme(qtbot):
    """Regression test: this dialog was the one popup in the app that forgot
    setObjectName("PopupBg"), so it fell back to Qt's default (dark-mode-
    following) QDialog styling instead of styles.qss's white-background/
    black-text popup look -- the user reported literally not being able to
    read the labels or values."""
    dialog = tasks_tab._PomodoroDialog()
    qtbot.addWidget(dialog)
    assert dialog.objectName() == "PopupBg"


def test_pomodoro_dialog_is_readable_even_parented_to_a_colored_card(qtbot, isolate_tasks, isolate_state):
    """Regression test: setObjectName("PopupBg") alone relies on the global
    app stylesheet (styles.qss), but the real call site
    (_TaskCard._start_pomodoro -> _PomodoroDialog.get_settings(self)) parents
    this dialog to its _TaskCard -- which sets its OWN instance-level
    stylesheet on itself, including "QFrame.TaskCard QWidget { background:
    transparent; }". That ancestor stylesheet outranks the global one for any
    QWidget descendant, including this dialog, so it rendered with a
    transparent (effectively black) background and the dark #1F2328 label
    text still on top of it -- black-on-black, unreadable, even though the
    dialog-with-no-parent case above looked fine. The dialog needs its own
    inline stylesheet to win back over the ancestor's."""
    task = _make_task(color="#E5484D")
    card = tasks_tab._TaskCard(task, on_changed=lambda: None)
    qtbot.addWidget(card)
    card.show()

    dialog = tasks_tab._PomodoroDialog(parent=card)
    qtbot.addWidget(dialog)
    dialog.resize(260, 220)
    dialog.show()

    from PySide6.QtGui import QColor
    corner_color = QColor(dialog.grab().toImage().pixel(2, 2))
    # #F7F7F8 (the popup background) is light -- every channel above 200.
    # Transparent-over-black (the bug) grabs as solid black, (0, 0, 0).
    assert corner_color.red() > 200
    assert corner_color.green() > 200
    assert corner_color.blue() > 200


def test_pomodoro_and_burnout_buttons_are_distinctly_styled(qtbot, isolate_tasks, isolate_state):
    task = _make_task()
    card = tasks_tab._TaskCard(task, on_changed=lambda: None)
    qtbot.addWidget(card)

    assert card._burnout_button.objectName() == "burnoutButton"
    assert card._pomodoro_button.objectName() == "pomodoroButton"
    assert card._burnout_button.objectName() != card._pomodoro_button.objectName()


def test_a_pomodoro_can_start_while_a_review_of_the_task_is_running(qtbot, isolate_tasks, isolate_state, monkeypatch):
    task = _make_task()
    card = tasks_tab._TaskCard(task, on_changed=lambda: None)
    qtbot.addWidget(card)
    card.show()
    session_manager.start_session(
        30, "soft", [], [],
        source="review", event_id=task["id"], event_title="Study - Algebra review",
        review_problem_name="Quadratics 1", review_subject_name="Algebra",
    )
    card.update_dynamic(session_manager.get_status(), session_history.load_all())
    assert card._review_pomodoro_button.isVisible()

    monkeypatch.setattr(tasks_tab._PomodoroDialog, "get_settings", staticmethod(lambda parent=None: (25, 5, 4)))
    card._review_pomodoro_button.click()

    status = session_manager.get_status()
    assert status["pomodoro"]["focusMinutes"] == 25 and status["source"] == "task" and not status["isPaused"]
    waiting = status.get("parkedSessions") or []
    assert [p.get("reviewProblemName") for p in waiting] == ["Quadratics 1"]    # the review waits, not ended
    card.update_dynamic(status, session_history.load_all())
    assert not card._review_pomodoro_button.isVisible()


def test_burnout_can_start_over_a_paused_review_session(qtbot, isolate_tasks, isolate_state):
    task = _make_task()
    card = tasks_tab._TaskCard(task, on_changed=lambda: None)
    qtbot.addWidget(card)
    card.show()
    session_manager.start_session(
        30, "soft", [], [],
        source="review", event_id=task["id"], event_title="Study - Algebra review",
        review_problem_name="Quadratics 1", review_subject_name="Algebra",
    )
    session_manager.pause_session()
    card.update_dynamic(session_manager.get_status(), session_history.load_all())
    assert card._review_burnout_button.isVisible() and card._review_custom_button.isVisible()

    card._review_burnout_button.click()

    status = session_manager.get_status()
    assert status["source"] == "task" and not status["isPaused"]
    assert [p.get("reviewProblemName") for p in status["parkedSessions"]] == ["Quadratics 1"]


def test_a_session_over_a_review_leaves_it_counting_beside_it(qtbot, isolate_tasks, isolate_state, isolate_review_db):
    import review_store
    from qt_ui import review_tab
    task = _make_task()
    topic = review_store.create_topic("Math")
    subject = review_store.create_subject(topic["id"], "Algebra", "#111111")
    problem = review_store.create_problem(topic["id"], subject["id"], "Quadratics 1", 3, "text", description_text="x")
    session_manager.start_session(
        30, "soft", [], [], source="review", event_id=task["id"], event_title="Study - Algebra review",
        review_problem_name="Quadratics 1", review_subject_name="Algebra", review_problem_id=problem["id"],
    )
    banner = review_tab._ReviewBanner(on_finished=lambda: None)
    qtbot.addWidget(banner)
    banner.start({**problem, "id": problem["id"]}, None, end_session_on_finish=True)
    card = tasks_tab._TaskCard(task, on_changed=lambda: None)
    qtbot.addWidget(card)

    card._start_burnout()

    status = session_manager.get_status()
    assert status["source"] == "task" and not status["isPaused"] and not status.get("parkedSessions")
    active = review_store.get_active_review()
    assert active["problemId"] == problem["id"] and active["isPaused"] is False
    assert banner._end_session_on_finish is False
    session_manager.pause_session()
    assert review_store.get_active_review()["isPaused"] is True
    session_manager.resume_session()
    assert review_store.get_active_review()["isPaused"] is False
