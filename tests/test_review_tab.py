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
    assert view._table.rowCount() == 0

    view._set_due_only(False)
    assert view._table.rowCount() == 1
    assert view._table.item(0, review_tab.COLUMN_NAME).text() == "Solve it"


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
    assert view._table.rowCount() == 1


def test_start_and_finish_review_updates_table(qtbot, isolate_review_db):
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
    assert view._table.rowCount() == 1

    token = review_store.start_review(problem["id"])
    review_store.finish_review(token)
    view.refresh()

    # Rescheduled at least a day out, so it drops off the "Due" view.
    assert view._table.rowCount() == 0


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
