"""Starting a review while another is running: the running one is paused and
collapsed into a strip row, the new one starts in the banner, and a row can be
resumed again -- for every kind of running review (linked to a task or timed
on its own, a review or a first attempt) against a new review in a linked or
an unlinked topic."""
import pytest

import review_store
import tasks_store
import qt_ui.review_tab as review_tab


@pytest.fixture
def world(qtbot, isolate_review_db, isolate_state, monkeypatch, tmp_path):
    monkeypatch.setattr(tasks_store, "TASKS_PATH", str(tmp_path / "tasks.json"))
    answers = {"confirm": True, "asked": []}

    def fake_confirm(parent, title, text):
        answers["asked"].append(text)
        return answers["confirm"]

    monkeypatch.setattr(review_tab, "_confirm", fake_confirm)
    # The "how do you want to start" dialog: confirm straight away.
    monkeypatch.setattr(review_tab, "_ReviewStartDialog", lambda problem, on_start: on_start(problem))

    class World:
        pass

    w = World()
    w.answers = answers
    w.qtbot = qtbot

    colors = iter(["#4A90D9", "#E27D60", "#8E44AD", "#2E8B57", "#F5A623", "#C0392B"])

    def make_topic(name, linked):
        topic = review_store.create_topic(name)
        subject = review_store.create_subject(topic["id"], "Sub " + name, next(colors))
        if linked:
            task = tasks_store.create_task({"name": "Task " + name, "targetMinutes": 30})
            review_store.update_topic_link(topic["id"], task["id"])
        problem = review_store.create_problem(
            topic["id"], subject["id"], "P-" + name, stars=5, description_type="text", description_text="x")
        return topic, problem

    w.make_topic = make_topic
    return w


def _tab(w):
    tab = review_tab.ReviewTab()
    w.qtbot.addWidget(tab)
    tab.resize(900, 700)
    tab.show()
    return tab


def _start_current(w, tab, first_attempt, topic, problem):
    view = tab._topic_views[topic["id"]]
    if not first_attempt:
        view._begin_review(problem)
        return None
    dialog = review_tab._AddProblemDialog(
        topic["id"], on_added=lambda _p: None, on_start_first_attempt=view._start_first_attempt)
    w.qtbot.addWidget(dialog)
    view._start_first_attempt(dialog)
    return dialog


def _names(tab):
    return [r["name"] for r in tab._parked_rows()]


def _start_buttons(view):
    return {
        p["name"]: view._table.cellWidget(row, review_tab.COLUMN_START).findChild(review_tab.QPushButton)
        for row, p in enumerate(view._problems)
    }


# (is a first attempt, topic linked to a task): a review driving a task
# session, a review timed on its own, and the same two for a first attempt.
CURRENT_KINDS = [(False, True), (False, False), (True, True), (True, False)]


@pytest.mark.parametrize("new_linked", [True, False])
@pytest.mark.parametrize("first_attempt,current_linked", CURRENT_KINDS)
def test_switching_parks_the_running_review_and_resuming_brings_it_back(
        world, first_attempt, current_linked, new_linked):
    current_topic, current_problem = world.make_topic("A", current_linked)
    new_topic, new_problem = world.make_topic("B", new_linked)
    tab = _tab(world)
    dialog = _start_current(world, tab, first_attempt, current_topic, current_problem)
    banner = tab.banner
    assert banner.is_running()
    old_name = "First attempt" if first_attempt else "P-A"

    # Start the other problem: it asks first, then the old review is collapsed.
    new_view = tab._topic_views[new_topic["id"]]
    new_view._start_problem(new_problem)
    assert len(world.answers["asked"]) == 1 and "P-B" in world.answers["asked"][0]
    assert banner.is_running() and banner.current_problem_id() == new_problem["id"]
    assert _names(tab) == [old_name]
    assert tab._strip.isVisible() and tab._strip.findChild(review_tab.QFrame).isVisible()
    new_view._set_due_only(False)
    assert not _start_buttons(new_view)["P-B"].isEnabled()

    # Resume the collapsed one: the new one takes its place in the strip.
    tab._resume_row(tab._parked_rows()[0])
    assert banner.is_running()
    if first_attempt:
        assert banner._first_attempt_callback is not None
        assert banner.current_problem_id() is None
    else:
        assert banner.current_problem_id() == current_problem["id"]
    assert _names(tab) == ["P-B"]

    if first_attempt:
        # Finishing still hands the result to the Add Problem dialog.
        banner._first_attempt_callback(60, True, 3)
        assert dialog.isVisible()
        dialog.close()


def test_declining_the_question_changes_nothing(world):
    topic_a, problem_a = world.make_topic("A", False)
    topic_b, problem_b = world.make_topic("B", False)
    tab = _tab(world)
    tab._topic_views[topic_a["id"]]._begin_review(problem_a)
    world.answers["confirm"] = False
    tab._topic_views[topic_b["id"]]._start_problem(problem_b)
    assert tab.banner.current_problem_id() == problem_a["id"]
    assert _names(tab) == []
    assert review_store.get_active_review()["problemId"] == problem_a["id"]


def test_cancelling_the_start_dialog_leaves_the_running_review_alone(world, monkeypatch):
    topic_a, problem_a = world.make_topic("A", False)
    topic_b, problem_b = world.make_topic("B", False)
    tab = _tab(world)
    tab._topic_views[topic_a["id"]]._begin_review(problem_a)
    monkeypatch.setattr(review_tab, "_ReviewStartDialog", lambda problem, on_start: None)  # user cancels
    tab._topic_views[topic_b["id"]]._start_problem(problem_b)
    assert tab.banner.current_problem_id() == problem_a["id"] and _names(tab) == []
    assert review_store.list_parked_reviews() == []


def test_two_reviews_in_one_linked_topic(world):
    topic, first = world.make_topic("A", True)
    second = review_store.create_problem(
        topic["id"], first["subjectId"], "P-second", stars=5, description_type="text", description_text="y")
    tab = _tab(world)
    view = tab._topic_views[topic["id"]]
    view._begin_review(first)
    view._start_problem(second)
    assert tab.banner.current_problem_id() == second["id"]
    assert _names(tab) == ["P-A"]
    # Start on the collapsed one resumes it instead of starting a second review of it.
    view._start_problem(first)
    assert tab.banner.current_problem_id() == first["id"]
    assert _names(tab) == ["P-second"]


def test_finishing_the_running_review_with_one_collapsed_shows_each_once(world):
    topic_a, problem_a = world.make_topic("A", True)
    topic_b, problem_b = world.make_topic("B", True)
    tab = _tab(world)
    tab._topic_views[topic_a["id"]]._begin_review(problem_a)
    tab._topic_views[topic_b["id"]]._start_problem(problem_b)
    assert _names(tab) == ["P-A"]
    banner = tab.banner
    problem = banner._problem
    # Finish the running one (Submit on the how-did-it-go dialog), as _finish() does.
    banner._tick_timer.stop()
    banner._finishing = True
    banner._session_token, banner._end_session_on_finish = None, False
    banner.hide()
    banner._complete_finish(None, True, True, 3, 120, None, problem, None)
    # Ending its session brought the collapsed one back as the running one.
    assert tab.banner.is_running() and tab.banner.current_problem_id() == problem_a["id"]
    assert _names(tab) == []


def test_ending_a_collapsed_first_attempt_reopens_its_dialog(world):
    topic_a, problem_a = world.make_topic("A", False)
    topic_b, problem_b = world.make_topic("B", False)
    tab = _tab(world)
    dialog = _start_current(world, tab, True, topic_a, problem_a)
    tab._topic_views[topic_b["id"]]._start_problem(problem_b)
    assert not dialog.isVisible() and _names(tab) == ["First attempt"]
    tab._end_row(tab._parked_rows()[0])
    assert dialog.isVisible() and _names(tab) == []
    dialog.close()


def test_resuming_an_independent_row_beats_a_paused_linked_session(world):
    topic_a, problem_a = world.make_topic("A", True)
    topic_b, problem_b = world.make_topic("B", False)
    topic_c, problem_c = world.make_topic("C", False)
    tab = _tab(world)
    tab._topic_views[topic_a["id"]]._begin_review(problem_a)   # linked, later paused-active
    tab._topic_views[topic_b["id"]]._start_problem(problem_b)  # independent
    tab._topic_views[topic_c["id"]]._start_problem(problem_c)  # B parked in the review store
    row = next(r for r in tab._parked_rows() if r["name"] == "P-B")
    tab._resume_row(row)
    assert tab.banner.current_problem_id() == problem_b["id"]
    assert review_store.get_active_review()["problemId"] == problem_b["id"]
    assert sorted(_names(tab)) == ["P-A", "P-C"]


def test_start_is_ignored_while_the_finish_dialog_is_open(world):
    topic_a, problem_a = world.make_topic("A", False)
    topic_b, problem_b = world.make_topic("B", False)
    tab = _tab(world)
    tab._topic_views[topic_a["id"]]._begin_review(problem_a)
    tab.banner._finishing = True
    tab._topic_views[topic_b["id"]]._start_problem(problem_b)
    assert tab.banner.current_problem_id() == problem_a["id"] and world.answers["asked"] == []


def _pause_a_pomodoro(task_name="Pomodoro task"):
    import session_manager
    session_manager.start_pomodoro_session(25, 5, 4, "hard", ["game.exe"], ["ok.com"],
                                           source="task", event_id="other-task", event_title=task_name)
    session_manager.pause_session()


@pytest.mark.parametrize("first_attempt", [False, True])
def test_review_started_while_a_pomodoro_is_paused_is_blocking(world, first_attempt):
    """Regression: with a task's pomodoro paused, a review in a task-linked topic only
    ran its own timer -- nothing was blocked. It now starts a blocking session and the
    paused pomodoro waits behind it."""
    import session_manager
    topic, problem = world.make_topic("Linked", linked=True)
    tab = _tab(world)
    _pause_a_pomodoro()

    _start_current(world, tab, first_attempt, topic, problem)

    status = session_manager.get_status()
    assert status["isActive"] and not status["isPaused"]
    assert status["source"] == "review"
    assert status["lockMode"] == "soft"                    # the linked task's rules
    assert len(status["parkedSessions"]) == 1
    assert status["parkedSessions"][0]["pomodoro"] is not None   # the pomodoro, set aside


def test_finishing_that_review_brings_the_paused_pomodoro_back(world):
    import session_manager
    topic, problem = world.make_topic("Linked", linked=True)
    tab = _tab(world)
    _pause_a_pomodoro()
    _start_current(world, tab, False, topic, problem)

    session_manager.end_session()

    status = session_manager.get_status()
    assert status["isActive"] and status["isPaused"] and status["pomodoro"] is not None
    assert status["parkedSessions"] == []


def _linked_task_id(topic):
    return review_store.get_topic(topic["id"])["linkedTaskId"]


def test_review_pauses_a_running_session_of_a_different_task(world):
    """A task's pomodoro running, then a review of ANOTHER task starts: the pomodoro
    pauses (set aside) and the review's own task rules take over."""
    import session_manager
    topic, problem = world.make_topic("Linked", linked=True)
    tab = _tab(world)
    session_manager.start_pomodoro_session(25, 5, 4, "hard", ["game.exe"], [],
                                           source="task", event_id="some-other-task", event_title="Other")

    _start_current(world, tab, False, topic, problem)

    status = session_manager.get_status()
    assert status["source"] == "review" and status["eventId"] == _linked_task_id(topic)
    assert not status["isPaused"]
    waiting = status["parkedSessions"]
    assert len(waiting) == 1 and waiting[0]["eventId"] == "some-other-task" and waiting[0]["pomodoro"] is not None


def test_review_of_the_same_task_runs_beside_its_pomodoro_and_pauses_on_the_break(world):
    """Same task: no new session -- the pomodoro keeps its rules and the review is
    paused by the break and resumed when the break is over."""
    import session_manager
    topic, problem = world.make_topic("Linked", linked=True)
    tab = _tab(world)
    task_id = _linked_task_id(topic)
    session_manager.start_pomodoro_session(25, 5, 4, "hard", ["game.exe"], [],
                                           source="task", event_id=task_id, event_title="Same")

    _start_current(world, tab, False, topic, problem)

    status = session_manager.get_status()
    assert status["source"] == "task" and status["parkedSessions"] == []      # beside, not over
    assert review_store.get_active_review()["isPaused"] is False

    review_store.auto_pause_for_break()          # what the pomodoro does when its break starts
    assert review_store.get_active_review()["isPaused"] is True
    review_store.auto_resume_from_break()        # ...and when the break ends
    assert review_store.get_active_review()["isPaused"] is False


def test_a_running_manual_session_is_left_alone(world):
    import session_manager
    topic, problem = world.make_topic("Linked", linked=True)
    tab = _tab(world)
    session_manager.start_session(30, "hard", ["game.exe"], [])

    _start_current(world, tab, False, topic, problem)

    status = session_manager.get_status()
    assert status["source"] == "manual" and status["lockMode"] == "hard"
    assert status["parkedSessions"] == [] and not status["isPaused"]


def test_first_attempt_also_pauses_a_running_session_of_a_different_task(world):
    import session_manager
    topic, problem = world.make_topic("Linked", linked=True)
    tab = _tab(world)
    session_manager.start_pomodoro_session(25, 5, 4, "hard", ["game.exe"], [],
                                           source="task", event_id="some-other-task", event_title="Other")

    _start_current(world, tab, True, topic, problem)

    status = session_manager.get_status()
    assert status["source"] == "review" and not status["isPaused"]
    assert [p["eventId"] for p in status["parkedSessions"]] == ["some-other-task"]
