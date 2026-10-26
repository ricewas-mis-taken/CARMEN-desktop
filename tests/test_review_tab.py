"""Widget-level tests for the Review tab (qt_ui/review_tab.py) -- topic tab
strip, due/all filtering, the problems table, and the Add Problem flow.
Uses isolate_review_db (tests/conftest.py) so nothing touches the real
calendar.db or review_photos folder."""
from datetime import date, datetime, timedelta

import review_store
import session_manager
import tasks_store
import qt_ui.review_tab as review_tab


def _make_topic_and_subject(name="Math", subject_name="Quadratics", color="#4A90D9"):
    topic = review_store.create_topic(name)
    subject = review_store.create_subject(topic["id"], subject_name, color)
    return topic, subject


def test_empty_state_shows_only_plus_tab(qtbot, isolate_review_db):
    tab = review_tab.ReviewTab()
    qtbot.addWidget(tab)
    assert tab._tabs.count() == 1
    assert tab._tabs.tabText(0) == "+"


def test_topics_render_as_tabs_with_trailing_plus(qtbot, isolate_review_db):
    review_store.create_topic("Math")
    review_store.create_topic("Physics")

    tab = review_tab.ReviewTab()
    qtbot.addWidget(tab)

    assert tab._tabs.count() == 3
    assert [tab._tabs.tabText(i) for i in range(3)] == ["Math", "Physics", "+"]


def test_ending_a_first_attempt_early_still_reopens_add_problem_dialog(qtbot, isolate_review_db, isolate_state):
    """Regression test: clicking "End" mid first-attempt used to just clear
    the banner's state and vanish -- review_store.abandon_review(token=None)
    is a no-op in first-attempt mode, and nothing ever called
    add_problem_dialog.show() again, so the in-progress problem was silently
    lost with no way back to fill in details and save it (the reported bug:
    "submit the stuff, doesn't give the option to add details and save")."""
    topic, _subject = _make_topic_and_subject()
    view = review_tab._TopicView(topic["id"])
    qtbot.addWidget(view)

    dialog = review_tab._AddProblemDialog(
        topic["id"], on_added=lambda _p: None, on_start_first_attempt=view._start_first_attempt,
    )
    qtbot.addWidget(dialog)

    view._start_first_attempt(dialog)
    assert not dialog.isVisible()

    view._review_banner._end_early()

    assert dialog.isVisible()


def test_topic_view_lists_due_problems_by_default(qtbot, isolate_review_db):
    topic, subject = _make_topic_and_subject()
    review_store.create_problem(
        topic["id"], subject["id"], "Solve it", stars=5,
        description_type="text", description_text="factor",
    )
    tab = review_tab.ReviewTab()
    qtbot.addWidget(tab)

    view = tab._topic_views[topic["id"]]
    # 5-star problems land 1 day out (see review_scheduler), so nothing is
    # due yet under the default "Due" filter.
    assert len(view._rows) == 0

    view._set_due_only(False)
    assert len(view._rows) == 1
    assert view._rows[0].name_label.text() == "Solve it"


def test_topic_view_shows_due_problem_when_next_review_is_today(qtbot, isolate_review_db):
    topic, subject = _make_topic_and_subject()
    problem = review_store.create_problem(
        topic["id"], subject["id"], "Due today", stars=3,
        description_type="text", description_text="x",
    )
    conn = review_store._get_conn()
    conn.execute(
        "UPDATE review_problems SET next_review_date = ? WHERE id = ?",
        (date.today().isoformat(), problem["id"]),
    )
    conn.commit()

    tab = review_tab.ReviewTab()
    qtbot.addWidget(tab)
    view = tab._topic_views[topic["id"]]
    assert len(view._rows) == 1


def test_start_and_finish_review_updates_the_list(qtbot, isolate_review_db):
    topic, subject = _make_topic_and_subject()
    problem = review_store.create_problem(
        topic["id"], subject["id"], "Due today", stars=3,
        description_type="text", description_text="x",
    )
    conn = review_store._get_conn()
    conn.execute(
        "UPDATE review_problems SET next_review_date = ? WHERE id = ?",
        (date.today().isoformat(), problem["id"]),
    )
    conn.commit()

    tab = review_tab.ReviewTab()
    qtbot.addWidget(tab)
    view = tab._topic_views[topic["id"]]
    assert len(view._rows) == 1

    token = review_store.start_review(problem["id"])
    review_store.finish_review(token)
    view.refresh()

    # Rescheduled at least a day out, so it drops off the "Due" view.
    assert len(view._rows) == 0


def test_add_problem_dialog_creates_problem_with_text_description(qtbot, isolate_review_db):
    topic, subject = _make_topic_and_subject()
    tab = review_tab.ReviewTab()
    qtbot.addWidget(tab)
    view = tab._topic_views[topic["id"]]

    dialog = review_tab._AddProblemDialog(topic["id"], on_added=lambda _p: view.refresh())
    qtbot.addWidget(dialog)
    dialog._name_edit.setText("New Problem")
    dialog._star_picker._set_value(4)
    dialog._text_edit.setPlainText("do the thing")
    dialog._submit()

    problems = review_store.list_problems(topic["id"], due_only=False)
    assert len(problems) == 1
    assert problems[0]["name"] == "New Problem"
    assert problems[0]["stars"] == 4
    assert problems[0]["descriptionText"] == "do the thing"


def test_add_problem_dialog_rejects_missing_name(qtbot, isolate_review_db):
    topic, subject = _make_topic_and_subject()
    dialog = review_tab._AddProblemDialog(topic["id"], on_added=lambda _p: None)
    qtbot.addWidget(dialog)
    dialog._text_edit.setPlainText("desc")
    dialog._submit()
    assert "name is required" in dialog._status_label.text().lower()
    assert review_store.list_problems(topic["id"], due_only=False) == []


def test_add_problem_dialog_inline_add_subject_selects_new_subject(qtbot, isolate_review_db):
    topic, _subject = _make_topic_and_subject()
    dialog = review_tab._AddProblemDialog(topic["id"], on_added=lambda _p: None)
    qtbot.addWidget(dialog)

    dialog._new_subject_name.setText("Trig")
    dialog._pick_subject_color("#e53935")
    dialog._save_new_subject()

    assert dialog._subject_combo.currentText() == "Trig"
    assert dialog._subject_combo.currentData() is not None
    assert not dialog._add_subject_form.isVisible()


def test_add_problem_dialog_link_validation(qtbot, isolate_review_db):
    topic, _subject = _make_topic_and_subject()
    dialog = review_tab._AddProblemDialog(topic["id"], on_added=lambda _p: None)
    qtbot.addWidget(dialog)
    dialog._name_edit.setText("Link Problem")
    dialog._link_button.setChecked(True)
    dialog._link_edit.setText("not a url")
    dialog._submit()
    assert "valid url" in dialog._status_label.text().lower()

    dialog._link_edit.setText("https://example.com/problem")
    dialog._submit()
    problems = review_store.list_problems(topic["id"], due_only=False)
    assert len(problems) == 1
    assert problems[0]["descriptionLink"] == "https://example.com/problem"


def test_star_text_and_time_formatting_helpers():
    assert review_tab._star_text(3) == "★★★☆☆"
    assert review_tab._format_mmss(None) == "--:--"
    assert review_tab._format_mmss(65) == "01:05"
    assert review_tab._relative_time(None) == "Never"


def test_lighten_produces_valid_hex():
    result = review_tab._lighten("#5B8DEF")
    assert result.startswith("#")
    assert len(result) == 7


def _fake_session(n, self_solved=True, shakiness=3):
    """n=1 is the oldest/first-ever session -- matches how real sessions
    sort (list_sessions returns newest first), so callers build a fake
    newest-first list with _fake_session(5), _fake_session(4), ..., _fake_session(1)."""
    return {
        "id": n,
        "startedAt": f"2026-01-{n:02d}T10:00:00",
        "finishedAt": f"2026-01-{n:02d}T10:05:00",
        "durationSeconds": 300,
        "selfSolved": self_solved,
        "shakiness": shakiness if self_solved else None,
    }


def test_timeline_sessions_shows_everything_when_five_or_fewer():
    sessions = [_fake_session(n) for n in (5, 4, 3, 2, 1)]
    shown, has_gap = review_tab._timeline_sessions(sessions)
    assert [(number, s["id"]) for number, s in shown] == [(5, 5), (4, 4), (3, 3), (2, 2), (1, 1)]
    assert has_gap is False


def test_timeline_sessions_shows_latest_three_and_first_two_ever_when_more_than_five():
    # Newest first, id 7 is the latest session, id 1 the first ever.
    sessions = [_fake_session(n) for n in range(7, 0, -1)]
    shown, has_gap = review_tab._timeline_sessions(sessions)
    assert has_gap is True
    assert [(number, s["id"]) for number, s in shown] == [(7, 7), (6, 6), (5, 5), (1, 1), (2, 2)]


def test_session_summary_fields_shows_number_duration_shakiness_and_a_marker():
    solved = _fake_session(1, self_solved=True, shakiness=2)
    checked = _fake_session(2, self_solved=False)
    solved_fields = review_tab._session_summary_fields(3, solved)
    checked_fields = review_tab._session_summary_fields(4, checked)
    assert solved_fields[0] == "#3"
    assert "2/5" in solved_fields[2]
    assert "(A)" not in solved_fields[2]
    assert "checked the answer" not in "".join(solved_fields).lower()
    assert checked_fields[0] == "#4"
    assert "-/5" in checked_fields[2]
    assert "(A)" in checked_fields[2]


def test_description_popup_shows_review_timeline(qtbot, isolate_review_db):
    topic, subject = _make_topic_and_subject()
    problem = review_store.create_problem(
        topic["id"], subject["id"], "Timeline Problem", stars=3,
        description_type="text", description_text="x",
    )
    token = review_store.start_review(problem["id"])
    review_store.finish_review(token, self_solved=False, shakiness=3)
    problem = review_store.get_problem(problem["id"])

    popup = review_tab._DescriptionPopup(problem)
    qtbot.addWidget(popup)

    labels = [w.text() for w in popup.findChildren(review_tab.QLabel)]
    assert any("Review Timeline" in text for text in labels)
    assert any("(A)" in text for text in labels)
    for header_cell in review_tab._TIMELINE_HEADER_CELLS:
        assert header_cell in labels


def test_timeline_gap_marker_has_three_straight_dots_spanning_all_columns(qtbot, isolate_review_db):
    """Regression test: the "more history" gap marker is 3 independently
    centered dot labels (straight line, no off-axis glyph) spanning every
    column of the timeline grid -- see _timeline_gap_marker."""
    topic, subject = _make_topic_and_subject()
    problem = review_store.create_problem(
        topic["id"], subject["id"], "Gap Marker Problem", stars=3,
        description_type="text", description_text="x",
    )
    for _ in range(7):
        token = review_store.start_review(problem["id"])
        review_store.finish_review(token, self_solved=True, shakiness=2)
    problem = review_store.get_problem(problem["id"])

    popup = review_tab._DescriptionPopup(problem)
    qtbot.addWidget(popup)
    popup.show()

    dot_labels = [w for w in popup.findChildren(review_tab.QLabel) if w.text() == "•"]
    assert len(dot_labels) == 3
    dots_widget = dot_labels[0].parentWidget()
    assert all(dot.parentWidget() is dots_widget for dot in dot_labels)

    grid = popup.findChild(review_tab.QGridLayout)
    assert grid.columnCount() == len(review_tab._TIMELINE_HEADER_CELLS)
    index = grid.indexOf(dots_widget)
    _, _, _, col_span = grid.getItemPosition(index)
    assert col_span == len(review_tab._TIMELINE_HEADER_CELLS)


def test_review_start_dialog_shows_subject_and_task_name(
    qtbot, isolate_review_db, isolate_state, tmp_path, monkeypatch
):
    monkeypatch.setattr(tasks_store, "TASKS_PATH", str(tmp_path / "tasks.json"))
    task = tasks_store.create_task({"name": "Math Session", "lockMode": "soft"})
    topic = review_store.create_topic("Math")
    review_store.update_topic_link(topic["id"], task["id"])
    subject = review_store.create_subject(topic["id"], "Quadratics", "#5B8DEF")
    problem = review_store.create_problem(
        topic["id"], subject["id"], "Solve for x", stars=3,
        description_type="text", description_text="factor",
    )

    dialog = review_tab._ReviewStartDialog(problem, on_start=lambda _p: None)
    qtbot.addWidget(dialog)

    labels = [w.text() for w in dialog.findChildren(review_tab.QLabel)]
    assert any("Quadratics" in text and "Math Session" in text for text in labels)


def test_review_start_dialog_shows_subject_only_when_no_linked_task(qtbot, isolate_review_db):
    topic = review_store.create_topic("Math")
    subject = review_store.create_subject(topic["id"], "Quadratics", "#5B8DEF")
    problem = review_store.create_problem(
        topic["id"], subject["id"], "Solve for x", stars=3,
        description_type="text", description_text="factor",
    )

    dialog = review_tab._ReviewStartDialog(problem, on_start=lambda _p: None)
    qtbot.addWidget(dialog)

    labels = [w.text() for w in dialog.findChildren(review_tab.QLabel)]
    assert "Quadratics" in labels


def test_review_start_dialog_shows_review_timeline(qtbot, isolate_review_db):
    topic, subject = _make_topic_and_subject()
    problem = review_store.create_problem(
        topic["id"], subject["id"], "Timeline Problem", stars=3,
        description_type="text", description_text="x",
    )
    token = review_store.start_review(problem["id"])
    review_store.finish_review(token, self_solved=False, shakiness=3)
    problem = review_store.get_problem(problem["id"])

    dialog = review_tab._ReviewStartDialog(problem, on_start=lambda _p: None)
    qtbot.addWidget(dialog)

    labels = [w.text() for w in dialog.findChildren(review_tab.QLabel)]
    assert any("Review Timeline" in text for text in labels)
    assert any("(A)" in text for text in labels)


def test_begin_review_starts_and_ends_linked_task_session(
    qtbot, isolate_review_db, isolate_state, tmp_path, monkeypatch
):
    monkeypatch.setattr(tasks_store, "TASKS_PATH", str(tmp_path / "tasks.json"))
    task = tasks_store.create_task({"name": "Math Session", "lockMode": "soft"})

    topic = review_store.create_topic("Math")
    review_store.update_topic_link(topic["id"], task["id"])
    subject = review_store.create_subject(topic["id"], "Quadratics", "#5B8DEF")
    problem = review_store.create_problem(
        topic["id"], subject["id"], "Solve for x", stars=3,
        description_type="text", description_text="factor",
    )

    tab = review_tab.ReviewTab()
    qtbot.addWidget(tab)
    view = tab._topic_views[topic["id"]]
    view._set_due_only(False)

    assert not session_manager.is_active()
    assert view._problems, "problem should appear in All view"

    view._begin_review(view._problems[0])

    assert session_manager.is_active(), "linked task session should be active during review"

    view._review_banner._finish()
    # _finish() shows the post-review grading dialog; submit it to complete the flow.
    dlg = next(p for p in review_tab._popup_refs if isinstance(p, review_tab._PostReviewDialog) and not p._submitted)
    dlg._solved_btn.setChecked(True)
    dlg._submit()

    assert not session_manager.is_active(), "session should end when review finishes"


def test_begin_review_does_not_leave_a_duplicate_desynced_tracker(
    qtbot, isolate_review_db, isolate_state, tmp_path, monkeypatch
):
    """Regression test: _begin_review() used to call review_store.start_review()
    unconditionally, even for a review driving its own session_manager
    session -- leaving a second, permanently-desynced elapsed-time tracker
    in review_store._active_sessions that never learned about a pause
    recorded through session_manager (Tasks tab, the extension, Focus tab),
    since that duplicate entry belonged to no code path that ever paused it.
    Pausing through session_manager directly (not this banner's own button)
    must still freeze the banner's own displayed elapsed time."""
    monkeypatch.setattr(tasks_store, "TASKS_PATH", str(tmp_path / "tasks.json"))
    task = tasks_store.create_task({"name": "Math Session", "lockMode": "soft"})

    topic = review_store.create_topic("Math")
    review_store.update_topic_link(topic["id"], task["id"])
    subject = review_store.create_subject(topic["id"], "Quadratics", "#5B8DEF")
    problem = review_store.create_problem(
        topic["id"], subject["id"], "Solve for x", stars=3,
        description_type="text", description_text="factor",
    )

    tab = review_tab.ReviewTab()
    qtbot.addWidget(tab)
    view = tab._topic_views[topic["id"]]
    view._set_due_only(False)

    view._begin_review(view._problems[0])

    assert review_store.get_active_review() is None, (
        "a task-linked review must not also create an independent "
        "review_store tracking entry"
    )
    assert not view._review_banner._is_independent_review()

    # Backdate startTime by 10 real minutes, then pause through
    # session_manager directly -- exactly what the Tasks tab's own Pause
    # button, the extension's pause button, and the Focus tab all do.
    started_at = datetime.now() - timedelta(minutes=10)
    session_manager._state["startTime"] = started_at.isoformat()
    session_manager.pause_session()

    elapsed_at_pause = view._review_banner._elapsed_seconds_now()
    assert 590 <= elapsed_at_pause <= 610  # ~10 minutes, give or take test overhead

    # Time passing while paused must not keep advancing the banner's number.
    elapsed_later = view._review_banner._elapsed_seconds_now()
    assert elapsed_later == elapsed_at_pause
    assert view._review_banner._currently_paused()


def test_review_banner_recovers_when_linked_session_ends_externally(
    qtbot, isolate_review_db, isolate_state, tmp_path, monkeypatch
):
    """Regression test: ending the linked task session from somewhere other
    than the review banner itself (Tasks tab's "End Task", Focus tab's
    Nuclear End, a natural timeout, or a direct API call) used to leave the
    banner ticking forever against a dead session, and can_start_review()
    permanently False -- no review could be started anywhere until the app
    was restarted."""
    monkeypatch.setattr(tasks_store, "TASKS_PATH", str(tmp_path / "tasks.json"))
    task = tasks_store.create_task({"name": "Math Session", "lockMode": "soft"})

    topic = review_store.create_topic("Math")
    review_store.update_topic_link(topic["id"], task["id"])
    subject = review_store.create_subject(topic["id"], "Quadratics", "#5B8DEF")
    problem = review_store.create_problem(
        topic["id"], subject["id"], "Solve for x", stars=3,
        description_type="text", description_text="factor",
    )

    tab = review_tab.ReviewTab()
    qtbot.addWidget(tab)
    view = tab._topic_views[topic["id"]]
    view._set_due_only(False)
    view._begin_review(view._problems[0])

    assert not tab.can_start_review()

    # Simulate ending the session from an entirely different surface, e.g.
    # the Tasks tab's "End Task" button -- not the banner's own Finish/End.
    session_manager.end_session(end_type="manual")

    view._review_banner._tick()

    assert not view._review_banner.isVisible()
    assert tab.can_start_review(), "a stale banner must not lock out starting new reviews"


def test_topic_view_resumes_banner_for_already_active_linked_session(
    qtbot, isolate_review_db, isolate_state, tmp_path, monkeypatch
):
    """Regression test: if a review-linked task session is already active in
    session_manager (e.g. the app restarted mid-review -- dev_watcher
    restarts on every .py save under --dev) by the time a _TopicView is
    constructed, the banner used to just sit hidden with no way to tell a
    review was running at all, even though the Tasks tab (which reads
    session_manager.get_status() fresh) showed it correctly the whole time."""
    monkeypatch.setattr(tasks_store, "TASKS_PATH", str(tmp_path / "tasks.json"))
    task = tasks_store.create_task({"name": "Math Session", "lockMode": "hard"})

    topic = review_store.create_topic("Math")
    review_store.update_topic_link(topic["id"], task["id"])

    # Simulate the session already being active -- as if a previous process
    # (before a restart) had started it via _begin_review().
    session_manager.start_session(
        25, "hard", [], [], source="review",
        event_id=task["id"], event_title="Math Session - Quadratics review",
        review_problem_name="Solve for x", review_subject_name="Quadratics",
    )

    tab = review_tab.ReviewTab()
    qtbot.addWidget(tab)
    tab.show()
    view = tab._topic_views[topic["id"]]

    assert view._review_banner.isVisible()
    assert "Solve for x" in view._review_banner._problem_label.text()
    assert not tab.can_start_review()


def test_resumed_banner_shows_real_elapsed_time_not_zero_while_paused(
    qtbot, isolate_review_db, isolate_state, tmp_path, monkeypatch
):
    """Regression test: a review resumed after an app restart (see the test
    above) that was PAUSED at the time of the restart used to show a stuck
    "00:00" timer -- start() hardcoded that text and _tick() never updates
    the label while paused, so a freshly-built banner had no real value to
    fall back on until Resume was pressed."""
    monkeypatch.setattr(tasks_store, "TASKS_PATH", str(tmp_path / "tasks.json"))
    task = tasks_store.create_task({"name": "Math Session", "lockMode": "hard"})
    topic = review_store.create_topic("Math")
    review_store.update_topic_link(topic["id"], task["id"])

    session_manager.start_session(
        25, "hard", [], [], source="review",
        event_id=task["id"], event_title="Math Session - Quadratics review",
        review_problem_name="Solve for x", review_subject_name="Quadratics",
        review_problem_id=1,
    )
    # 5 real minutes worked before pausing, matching a session that ran for
    # a while, got paused, and (in the real bug) the PC was shut down.
    started_at = datetime.now() - timedelta(minutes=5)
    session_manager._state["startTime"] = started_at.isoformat()
    session_manager.pause_session()

    tab = review_tab.ReviewTab()
    qtbot.addWidget(tab)
    view = tab._topic_views[topic["id"]]

    assert view._review_banner._timer_label.text() != "00:00"
    assert view._review_banner._pause_btn.text() == "Resume"


def test_finish_after_restart_still_records_a_review_session(
    qtbot, isolate_review_db, isolate_state, tmp_path, monkeypatch
):
    """Regression test: finishing a review recovered after an app restart
    (token=None, see _TopicView._resume_if_active) used to silently record
    nothing at all -- reviewProblemId (persisted in session_manager) now
    lets _ReviewBanner._complete_finish log it directly instead."""
    monkeypatch.setattr(tasks_store, "TASKS_PATH", str(tmp_path / "tasks.json"))
    task = tasks_store.create_task({"name": "Math Session", "lockMode": "hard"})
    topic = review_store.create_topic("Math")
    review_store.update_topic_link(topic["id"], task["id"])
    subject = review_store.create_subject(topic["id"], "Quadratics", "#5B8DEF")
    problem = review_store.create_problem(
        topic["id"], subject["id"], "Solve for x", stars=3,
        description_type="text", description_text="factor",
    )
    assert problem["reviewCount"] == 0

    session_manager.start_session(
        25, "hard", [], [], source="review",
        event_id=task["id"], event_title="Math Session - Quadratics review",
        review_problem_name="Solve for x", review_subject_name="Quadratics",
        review_problem_id=problem["id"],
    )

    tab = review_tab.ReviewTab()
    qtbot.addWidget(tab)
    view = tab._topic_views[topic["id"]]
    assert view._review_banner._session_token is None

    view._review_banner._finish()
    dlg = next(p for p in review_tab._popup_refs if isinstance(p, review_tab._PostReviewDialog) and not p._submitted)
    dlg._solved_btn.setChecked(True)
    dlg._submit()

    updated = review_store.get_problem(problem["id"])
    assert updated["reviewCount"] == 1
    assert len(review_store.list_sessions(problem["id"])) == 1


def test_topic_view_resumes_an_independent_review_after_a_restart(
    qtbot, isolate_review_db, isolate_state, monkeypatch
):
    """Regression test: a review started while a DIFFERENT session (e.g. a
    pomodoro) was already active never touches session_manager at all (see
    _begin_review's `not session_manager.is_active()` guard), so closing or
    restarting the app used to just discard it -- review_store's
    _active_sessions was in-memory only. It's persisted now (see
    review_store.py's ACTIVE_SESSION_PATH), so a fresh _TopicView must
    recover it with the real elapsed time and the real token, not a blank
    banner or one that can't be finished."""
    topic = review_store.create_topic("Math")
    subject = review_store.create_subject(topic["id"], "Quadratics", "#5B8DEF")
    problem = review_store.create_problem(
        topic["id"], subject["id"], "Solve for x", stars=3,
        description_type="text", description_text="factor",
    )
    token = review_store.start_review(problem["id"])

    # Simulate the restart: in-memory state is gone, only the persisted
    # file remains.
    monkeypatch.setattr(review_store, "_active_sessions", {})
    monkeypatch.setattr(review_store, "_active_sessions_loaded", False)

    tab = review_tab.ReviewTab()
    qtbot.addWidget(tab)
    tab.show()
    view = tab._topic_views[topic["id"]]

    assert view._review_banner.isVisible()
    assert "Solve for x" in view._review_banner._problem_label.text()
    assert view._review_banner._session_token == token
    assert not view._review_banner._end_session_on_finish

    view._review_banner._finish()
    dlg = next(p for p in review_tab._popup_refs if isinstance(p, review_tab._PostReviewDialog) and not p._submitted)
    dlg._solved_btn.setChecked(True)
    dlg._submit()

    assert review_store.get_problem(problem["id"])["reviewCount"] == 1
    assert review_store.get_active_review() is None


def test_pause_button_visible_for_standalone_review(qtbot, isolate_review_db, isolate_state):
    """A review not linked to any task session (no underlying session_manager
    session to pause) must still show a working Pause button on its own
    timer -- previously it was hidden whenever end_session_on_finish was
    False, which is every review except ones tied to a linked task."""
    topic, subject = _make_topic_and_subject()
    problem = review_store.create_problem(
        topic["id"], subject["id"], "Solve for x", stars=3,
        description_type="text", description_text="factor",
    )

    tab = review_tab.ReviewTab()
    qtbot.addWidget(tab)
    tab.show()
    view = tab._topic_views[topic["id"]]

    assert not session_manager.is_active()
    view._begin_review(problem)
    assert not session_manager.is_active(), "standalone review must not start a task session"

    banner = view._review_banner
    assert banner._pause_btn.isVisible()
    assert banner._pause_btn.text() == "Pause"

    banner._pause_resume()
    assert banner._pause_btn.text() == "Resume"
    banner._pause_resume()
    assert banner._pause_btn.text() == "Pause"


def test_first_solved_reads_never_for_a_problem_never_reviewed(qtbot, isolate_review_db):
    topic, subject = _make_topic_and_subject()
    review_store.create_problem(
        topic["id"], subject["id"], "Fresh", stars=3,
        description_type="text", description_text="x",
    )
    tab = review_tab.ReviewTab()
    qtbot.addWidget(tab)
    view = tab._topic_views[topic["id"]]
    view._set_due_only(False)

    assert view._rows[0].first_solved_text == "Never"


def test_first_solved_shows_date_once_a_problem_has_been_reviewed():
    problem = {"lastReviewedAt": "2026-10-01T10:00:00", "dateAdded": "2026-09-20"}
    assert review_tab._first_solved_display(problem) == "Sep 20, 2026"


def test_first_attempt_banner_publishes_to_status_and_clears_on_end(qtbot, isolate_review_db, isolate_state):
    """A first attempt has no review_store entry, so the extension could only
    see it if the banner publishes a snapshot -- and must retract it on End."""
    topic, _subject = _make_topic_and_subject()
    view = review_tab._TopicView(topic["id"])
    qtbot.addWidget(view)
    dialog = review_tab._AddProblemDialog(
        topic["id"], on_added=lambda _p: None, on_start_first_attempt=view._start_first_attempt,
    )
    qtbot.addWidget(dialog)

    view._start_first_attempt(dialog)
    published = review_store.get_first_attempt()
    assert published is not None and published["topicId"] == topic["id"]

    view._review_banner._end_early()
    assert review_store.get_first_attempt() is None


def _first_attempt_banner(qtbot, monkeypatch, break_flag):
    topic, _subject = _make_topic_and_subject()
    view = review_tab._TopicView(topic["id"])
    qtbot.addWidget(view)
    dialog = review_tab._AddProblemDialog(
        topic["id"], on_added=lambda _p: None, on_start_first_attempt=view._start_first_attempt,
    )
    qtbot.addWidget(dialog)
    real_get_status = session_manager.get_status
    monkeypatch.setattr(
        session_manager, "get_status", lambda: {**real_get_status(), "isBreak": break_flag["on"]}
    )
    view._start_first_attempt(dialog)
    return view._review_banner


def test_first_attempt_auto_pauses_on_pomodoro_break_and_resumes_after(qtbot, isolate_review_db, isolate_state, monkeypatch):
    flag = {"on": False}
    banner = _first_attempt_banner(qtbot, monkeypatch, flag)
    banner._tick()
    assert not banner._is_paused

    flag["on"] = True
    banner._tick()
    assert banner._is_paused and banner._auto_paused
    assert review_store.get_first_attempt()["autoPaused"] is True
    assert banner._pause_btn.text() == "Resume (on break)"

    flag["on"] = False
    banner._tick()
    assert not banner._is_paused and not banner._auto_paused
    banner._end_early()


def test_first_attempt_manual_resume_during_break_sticks(qtbot, isolate_review_db, isolate_state, monkeypatch):
    flag = {"on": False}
    banner = _first_attempt_banner(qtbot, monkeypatch, flag)
    banner._tick()
    flag["on"] = True
    banner._tick()
    assert banner._is_paused

    banner._pause_resume()
    banner._tick()
    assert not banner._is_paused
    banner._end_early()


def test_first_attempt_manual_pause_is_not_auto_resumed_after_break(qtbot, isolate_review_db, isolate_state, monkeypatch):
    flag = {"on": False}
    banner = _first_attempt_banner(qtbot, monkeypatch, flag)
    banner._tick()
    banner._pause_resume()
    assert banner._is_paused and not banner._auto_paused

    flag["on"] = True
    banner._tick()
    flag["on"] = False
    banner._tick()
    assert banner._is_paused
    banner._end_early()


def _parked_first_attempt(qtbot, isolate_review_db, isolate_state, monkeypatch, tmp_path):
    """A first attempt on a linked task that was paused and then pushed back
    by a different session -- returns (view, banner, dialog)."""
    monkeypatch.setattr(tasks_store, "TASKS_PATH", str(tmp_path / "tasks.json"))
    task = tasks_store.create_task({"name": "Study"})
    topic, _subject = _make_topic_and_subject()
    review_store.update_topic_link(topic["id"], task["id"])
    view = review_tab._TopicView(topic["id"])
    qtbot.addWidget(view)
    dialog = review_tab._AddProblemDialog(
        topic["id"], on_added=lambda _p: None, on_start_first_attempt=view._start_first_attempt,
    )
    qtbot.addWidget(dialog)
    view._start_first_attempt(dialog)
    banner = view._review_banner
    banner._pause_resume()
    session_manager.start_session(
        duration_minutes=30, lock_mode="soft", process_blocklist=[], domain_whitelist=[],
    )
    return view, banner, dialog


def test_parked_first_attempt_banner_follows_its_own_session(
        qtbot, isolate_review_db, isolate_state, monkeypatch, tmp_path):
    """Regression test: with the first attempt paused and a different session
    started over it, the banner (a first attempt has no problem id) read the
    OTHER session -- so when that one ended the banner abandoned itself, lost
    its Add Problem callback, and a later Finish > Submit saved nothing."""
    _view, banner, dialog = _parked_first_attempt(qtbot, isolate_review_db, isolate_state, monkeypatch, tmp_path)
    status, parked = banner._linked_view()
    assert parked and session_manager.is_first_attempt(status)

    banner._tick()
    assert banner.is_running() and banner._first_attempt_callback is not None

    session_manager.end_session()  # the other session ends; the first attempt comes back
    banner._tick()
    assert banner.is_running() and banner._first_attempt_callback is not None
    assert not banner._linked_view()[1]

    banner._complete_finish(None, True, True, 3, 120, banner._first_attempt_callback, banner._problem, None)
    assert dialog.isVisible()


def test_rebuilt_view_gives_a_resumed_first_attempt_its_add_problem_dialog(
        qtbot, isolate_review_db, isolate_state, monkeypatch, tmp_path):
    """The banner can be rebuilt around a still-running first attempt (app
    restart); finishing it must still reach an Add Problem dialog."""
    view, _banner, _dialog = _parked_first_attempt(qtbot, isolate_review_db, isolate_state, monkeypatch, tmp_path)
    session_manager.end_session()  # first attempt restored as the live session
    fresh = review_tab._TopicView(view._topic_id)
    qtbot.addWidget(fresh)
    banner = fresh._review_banner
    assert banner.is_running() and banner._first_attempt_callback is not None

    done = banner._first_attempt_callback
    done(90, True, 3)
    popups = [p for p in review_tab._popup_refs if isinstance(p, review_tab._AddProblemDialog) and p.isVisible()]
    assert popups
    for popup in popups:
        popup.close()


def _start_buttons(view):
    return [row.start_button for row in view._rows]


def test_review_banner_sits_above_the_topic_tabs_and_is_shared(qtbot, isolate_review_db):
    topic, subject = _make_topic_and_subject()
    other = review_store.create_topic("Physics")
    tab = review_tab.ReviewTab()
    qtbot.addWidget(tab)
    tab.resize(900, 700)
    tab.show()
    problem = review_store.create_problem(
        topic["id"], subject["id"], "Solve it", stars=5, description_type="text", description_text="x")
    tab._topic_views[topic["id"]]._begin_review(problem)
    assert tab.banner.isVisible()
    assert tab.banner.mapTo(tab, tab.banner.rect().topLeft()).y() < tab._tabs.y()
    assert all(v._review_banner is tab.banner for v in tab._topic_views.values())
    # Switching to another topic's tab, or rebuilding the tabs, keeps the review.
    tab._select_topic(other["id"])
    tab.refresh()
    assert tab.banner.isVisible() and tab.banner.is_running()
    assert tab.banner.current_problem_id() == problem["id"]


def test_start_button_of_the_review_in_progress_is_greyed_out(qtbot, isolate_review_db):
    topic, subject = _make_topic_and_subject()
    first = review_store.create_problem(
        topic["id"], subject["id"], "First", stars=5, description_type="text", description_text="x")
    review_store.create_problem(
        topic["id"], subject["id"], "Second", stars=5, description_type="text", description_text="y")
    tab = review_tab.ReviewTab()
    qtbot.addWidget(tab)
    view = tab._topic_views[topic["id"]]
    view._set_due_only(False)
    assert all(b.isEnabled() for b in _start_buttons(view))

    view._begin_review(first)
    names = [p["name"] for p in view._problems]
    enabled = {name: b.isEnabled() for name, b in zip(names, _start_buttons(view))}
    assert enabled == {"First": False, "Second": True}

    tab.banner._end_early()
    assert all(b.isEnabled() for b in _start_buttons(view))


# ------------------------------------------------------ redesigned rows ----

def _problem_dict(**over):
    problem = {"reviewCount": 0, "lastReviewedAt": None, "nextReviewDate": None, "fastestTimeSeconds": None,
               "fastestTimeIsSolved": False, "firstSolvedAt": None, "firstSolved": None}
    problem.update(over)
    return problem


def test_the_due_pill_reads_overdue_today_or_later():
    from datetime import date, timedelta
    iso = lambda days: (date.today() + timedelta(days=days)).isoformat()
    assert review_tab._next_review_pill(_problem_dict(nextReviewDate=iso(-3)))[0] == "Overdue · 3 days ago"
    assert review_tab._next_review_pill(_problem_dict(nextReviewDate=iso(0)))[0] == "Due today"
    assert review_tab._next_review_pill(_problem_dict(nextReviewDate=iso(1)))[0] == "Next · Tomorrow"
    assert review_tab._next_review_pill(_problem_dict(nextReviewDate=iso(5)))[0] == "Next · in 5 days"
    assert review_tab._next_review_pill(_problem_dict())[0] == "Not scheduled"
    assert review_tab._next_review_pill(_problem_dict(nextReviewDate=iso(-3)))[1] == review_tab.RED


def test_a_new_problems_history_line_says_what_has_not_happened(qtbot, isolate_review_db):
    topic, subject = _make_topic_and_subject()
    review_store.create_problem(topic["id"], subject["id"], "Solve it", stars=3, description_type="text",
                                description_text="x")
    view = review_tab._TopicView(topic["id"])
    qtbot.addWidget(view)
    view._set_due_only(False)
    meta = view._rows[0].meta_label.text()
    assert "0 reviews" in meta and "never reviewed" in meta and "not solved yet" in meta


def test_each_row_carries_its_subject_stars_and_start_button(qtbot, isolate_review_db):
    topic, subject = _make_topic_and_subject("Math", "Quadratics", "#4A90D9")
    review_store.create_problem(topic["id"], subject["id"], "Solve it", stars=4, description_type="text",
                                description_text="x")
    view = review_tab._TopicView(topic["id"])
    qtbot.addWidget(view)
    view._set_due_only(False)
    row = view._rows[0]
    assert row.name_label.text() == "Solve it" and row.stars_label.text() == "★★★★☆"
    assert "Quadratics" in [l.text() for l in row.findChildren(review_tab.QLabel)]
    assert row.start_button.objectName() == "reviewStartButton" and row.start_button.isEnabled()


def test_the_summary_counts_due_and_all(qtbot, isolate_review_db):
    topic, subject = _make_topic_and_subject()
    view = review_tab._TopicView(topic["id"])
    qtbot.addWidget(view)
    assert view._summary_label.text() == "Nothing due · 0 problems"
    review_store.create_problem(topic["id"], subject["id"], "A", stars=3, description_type="text", description_text="x")
    view.refresh()
    assert view._summary_label.text().endswith("· 1 problem")


def test_the_empty_list_explains_itself(qtbot, isolate_review_db):
    topic, _subject = _make_topic_and_subject()
    view = review_tab._TopicView(topic["id"])
    qtbot.addWidget(view)
    assert view._rows == []
    texts = [l.text() for l in view.findChildren(review_tab.QLabel)]
    assert any("Nothing to review today" in t for t in texts)
    view._set_due_only(False)
    texts = [l.text() for l in view.findChildren(review_tab.QLabel)]
    assert any("No problems yet" in t for t in texts)


def test_the_extension_can_pause_and_resume_a_first_attempt(qtbot, isolate_review_db, isolate_state, monkeypatch):
    """The first attempt is timed only by this banner, so a pause asked for through the
    API (review_store.request_first_attempt) is applied here on the next tick."""
    banner = _first_attempt_banner(qtbot, monkeypatch, {"on": False})
    assert not banner._currently_paused()
    review_store.request_first_attempt("pause")
    banner._tick()
    assert banner._currently_paused() and banner._pause_btn.text() == "Resume"
    assert review_store.get_first_attempt()["isPaused"]
    review_store.request_first_attempt("resume")
    banner._tick()
    assert not banner._currently_paused() and banner._pause_btn.text() == "Pause"
    assert not review_store.get_first_attempt()["isPaused"]


def test_a_request_for_the_state_it_is_already_in_changes_nothing(qtbot, isolate_review_db, isolate_state, monkeypatch):
    banner = _first_attempt_banner(qtbot, monkeypatch, {"on": False})
    review_store.request_first_attempt("resume")   # already running
    banner._tick()
    assert not banner._currently_paused()


# ---------------------------------------------- find / category / sort sidebar ----

def _view_with_problems(qtbot):
    topic = review_store.create_topic("Calculus")
    integrals = review_store.create_subject(topic["id"], "Integrals", "#4A90D9")
    limits = review_store.create_subject(topic["id"], "Limits", "#e53935")
    for name, subject, stars in (("Parts drill", integrals, 4), ("Trig sub", integrals, 2), ("Epsilon proof", limits, 5)):
        review_store.create_problem(topic["id"], subject["id"], name, stars=stars, description_type="text",
                                    description_text=f"notes about {name.lower()} and a secret word zebra" if name == "Trig sub" else "x")
    view = review_tab._TopicView(topic["id"])
    qtbot.addWidget(view)
    view._set_due_only(False)
    return view, integrals, limits


def _names(view):
    return [row.name_label.text() for row in view._rows]


def test_the_sidebar_lists_every_category_with_its_count(qtbot, isolate_review_db):
    view, integrals, limits = _view_with_problems(qtbot)
    buttons = view._category_buttons
    assert list(buttons) == [None, integrals["id"], limits["id"]] or set(buttons) == {None, integrals["id"], limits["id"]}
    assert buttons[None]._count == 3 and buttons[integrals["id"]]._count == 2 and buttons[limits["id"]]._count == 1
    assert buttons[None].isChecked()


def test_picking_a_category_shows_only_its_problems_and_all_brings_the_rest_back(qtbot, isolate_review_db):
    view, integrals, limits = _view_with_problems(qtbot)
    view._category_buttons[limits["id"]].click()
    assert _names(view) == ["Epsilon proof"] and view._category_buttons[limits["id"]].isChecked()
    assert "showing 1" in view._summary_label.text()
    view._category_buttons[None].click()
    assert len(view._rows) == 3


def test_search_matches_names_subjects_and_notes(qtbot, isolate_review_db):
    view, _integrals, _limits = _view_with_problems(qtbot)
    view._search_edit.setText("parts")
    assert _names(view) == ["Parts drill"]
    view._search_edit.setText("limits")        # a subject name
    assert _names(view) == ["Epsilon proof"]
    view._search_edit.setText("zebra")         # only in the notes
    assert _names(view) == ["Trig sub"]
    view._search_edit.setText("")
    assert len(view._rows) == 3


def test_search_and_category_work_together_and_an_empty_result_explains_itself(qtbot, isolate_review_db):
    view, integrals, limits = _view_with_problems(qtbot)
    view._category_buttons[integrals["id"]].click()
    view._search_edit.setText("epsilon")
    assert view._rows == []
    assert any("Nothing matches" in label.text() for label in view.findChildren(review_tab.QLabel))


def test_sorting(qtbot, isolate_review_db):
    view, _i, _l = _view_with_problems(qtbot)
    for key, expected in (("hardest", ["Epsilon proof", "Parts drill", "Trig sub"]),
                          ("name", ["Epsilon proof", "Parts drill", "Trig sub"])):
        view._sort_combo.setCurrentIndex(view._sort_combo.findData(key))
        assert _names(view) == expected
    view._sort_combo.setCurrentIndex(view._sort_combo.findData("newest"))
    assert len(_names(view)) == 3


def test_counts_follow_the_due_or_all_view(qtbot, isolate_review_db):
    view, _i, _l = _view_with_problems(qtbot)
    view._set_due_only(True)
    assert view._category_buttons[None]._count == len(view._problems)


def test_miss_dialog_files_it_under_the_chosen_category_with_the_chosen_stars(qtbot, isolate_review_db):
    topic = review_store.create_topic("Math")["id"]
    subject = review_store.create_subject(topic, "Algebra", "#112233")
    said = []
    dialog = review_tab._MissDialog(topic, on_done=said.append)
    qtbot.addWidget(dialog)
    dialog._edit.setText("Ch7 #14")
    dialog._category.setCurrentIndex(dialog._category.findData(subject["id"]))
    dialog._stars._value = 4
    dialog._save()
    problem = review_store.list_problems(topic, due_only=False)[0]
    assert (problem["subjectName"], problem["stars"]) == ("Algebra", 4)
    assert 'Added "Ch7 #14"' in said[0] and problem["nextReviewDate"] in said[0]


def test_miss_dialog_with_no_category_makes_mistakes_and_needs_a_name(qtbot, isolate_review_db):
    topic = review_store.create_topic("Math")["id"]
    dialog = review_tab._MissDialog(topic, on_done=lambda m: None)
    qtbot.addWidget(dialog)
    dialog._save()
    assert "first" in dialog._status.text()
    dialog._edit.setText("x")
    dialog._save()
    assert review_store.list_problems(topic, due_only=False)[0]["subjectName"] == "Mistakes"


def test_a_quick_add_switches_to_all_so_the_new_problem_is_visible(qtbot, isolate_review_db):
    topic, subject = _make_topic_and_subject()
    tab = review_tab.ReviewTab()
    qtbot.addWidget(tab)
    view = tab._topic_views[topic["id"]]
    assert view._due_only is True
    review_store.create_problem(topic["id"], subject["id"], "fresh", 5, "text", description_text="")
    view._after_quick_add("Added fresh.")
    assert view._due_only is False and len(view._rows) == 1
    assert view._summary_label.text() == "Added fresh."


def test_batch_dialog_takes_new_lines_previews_then_adds(qtbot, isolate_review_db):
    topic = review_store.create_topic("Math")["id"]
    review_store.create_subject(topic, "Algebra", "#112233")
    said = []
    dialog = review_tab._BatchDialog(topic, on_done=said.append)
    qtbot.addWidget(dialog)
    dialog.show()
    dialog._text.setFocus()
    qtbot.keyClicks(dialog._text, "Fence")
    qtbot.keyClick(dialog._text, review_tab.Qt.Key_Return)
    qtbot.keyClicks(dialog._text, "Cows")
    assert dialog._text.toPlainText() == "Fence\nCows"
    assert "2 to add" in dialog._summary.text()
    dialog._save()
    assert len(review_store.list_problems(topic, due_only=False)) == 2
    assert said == ["Added 2 problems to Algebra."]


def test_batch_dialog_without_a_category_says_so_and_can_make_one(qtbot, isolate_review_db):
    topic = review_store.create_topic("Math")["id"]
    dialog = review_tab._BatchDialog(topic, on_done=lambda m: None)
    qtbot.addWidget(dialog)
    dialog._text.setPlainText("a")
    assert not dialog._add.isEnabled() and "category" in dialog._summary.text()
    subject = review_store.create_subject(topic, "Sorting", "#445566")
    dialog._category_made(subject)
    assert dialog._add.isEnabled() and dialog._subject.currentText() == "Sorting"


def test_new_category_dialog_creates_one_and_rejects_a_repeat(qtbot, isolate_review_db):
    topic = review_store.create_topic("Math")["id"]
    made = []
    dialog = review_tab._NewCategoryDialog(topic, on_done=made.append)
    qtbot.addWidget(dialog)
    dialog._save()
    assert "name" in dialog._status.text() and made == []
    dialog._name.setText("Sorting")
    dialog._save()
    assert made[0]["name"] == "Sorting"
    again = review_tab._NewCategoryDialog(topic, on_done=made.append)
    qtbot.addWidget(again)
    again._name.setText("sorting")
    again._save()
    assert "already" in again._status.text() and len(made) == 1


def test_the_sidebar_can_make_and_delete_a_category(qtbot, isolate_review_db, monkeypatch):
    topic, subject = _make_topic_and_subject()
    review_store.create_problem(topic["id"], subject["id"], "p", 3, "text", description_text="")
    tab = review_tab.ReviewTab()
    qtbot.addWidget(tab)
    view = tab._topic_views[topic["id"]]
    monkeypatch.setattr(review_tab.confirm_dialog, "ask", lambda *a, **k: True)
    view._delete_category(subject["id"], subject["name"], 1)
    assert review_store.list_subjects(topic["id"]) == []
    assert review_store.list_problems(topic["id"], due_only=False) == []


def test_a_problem_can_be_deleted_after_confirming(qtbot, isolate_review_db, monkeypatch):
    topic, subject = _make_topic_and_subject()
    problem = review_store.create_problem(topic["id"], subject["id"], "p", 3, "text", description_text="")
    tab = review_tab.ReviewTab()
    qtbot.addWidget(tab)
    view = tab._topic_views[topic["id"]]
    monkeypatch.setattr(review_tab.confirm_dialog, "ask", lambda *a, **k: False)
    view._delete_problem(problem)
    assert review_store.get_problem(problem["id"]) is not None          # said no: still there
    monkeypatch.setattr(review_tab.confirm_dialog, "ask", lambda *a, **k: True)
    view._delete_problem(problem)
    assert review_store.get_problem(problem["id"]) is None


def test_the_confirm_popup_is_our_own_and_answers_yes_or_no(qtbot):
    from qt_ui import confirm_dialog
    box = confirm_dialog.ConfirmDialog(None, "Delete problem", "Sure?", yes="Delete", risky=True)
    qtbot.addWidget(box)
    assert box.objectName() == "PopupBg" and box.yes_button.text() == "Delete"
    box.yes_button.click()
    assert box.result() == confirm_dialog.QDialog.Accepted
    other = confirm_dialog.ConfirmDialog(None, "t", "x")
    qtbot.addWidget(other)
    other.no_button.click()
    assert other.result() == confirm_dialog.QDialog.Rejected


def test_score_dialog_rejects_bad_numbers_and_schedules(qtbot, isolate_review_db):
    topic = review_store.create_topic("Math")["id"]
    subject = review_store.create_subject(topic, "Sets", "#334455")
    problem = review_store.create_problem(topic, subject["id"], "Set 1", 3, "text", description_text="")
    dialog = review_tab._ScoreDialog(problem, on_done=lambda: None)
    qtbot.addWidget(dialog)
    dialog._correct.setText("12")
    dialog._total.setText("10")
    dialog._save()
    assert review_store.get_problem(problem["id"])["reviewCount"] == 0
    dialog._correct.setText("10")
    dialog._save()
    assert review_store.get_problem(problem["id"])["reviewCount"] == 1
