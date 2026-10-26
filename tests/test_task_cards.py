"""The redesigned Tasks cards: what each state shows, the Start flow (Until burnout /
Custom time / Pomodoro), the hover-to-use vacation line, reviews listed in the card, and
the grid reflowing with the window."""
from datetime import date

import pytest
from PySide6.QtCore import QEvent, QPointF
from PySide6.QtGui import QEnterEvent
from PySide6.QtWidgets import QLabel, QPushButton

import review_store
import session_history
import session_manager as sm
import tasks_store
import qt_ui.tasks_tab as tasks_tab


@pytest.fixture
def world(tmp_path, monkeypatch, isolate_state, isolate_review_db):
    monkeypatch.setattr(tasks_store, "TASKS_PATH", str(tmp_path / "tasks.json"))


def _task(name="Study", **extra):
    data = {"name": name, "targetMinutes": 30}
    data.update(extra)
    return tasks_store.create_task(data)


def _card(qtbot, task, shown=True):
    card = tasks_tab._TaskCard(task, on_changed=lambda: None)
    qtbot.addWidget(card)
    if shown:
        card.show()
    card.update_dynamic(sm.get_status(), session_history.load_all())
    return card


def _refresh(card, reviews=None):
    card.update_dynamic(sm.get_status(), session_history.load_all(), reviews)


# ---------------------------------------------------------------- idle ----

def test_idle_card_shows_the_goal_the_rules_and_a_start_button(qtbot, world):
    card = _card(qtbot, _task(processBlocklist=["a.exe", "b.exe"], domainWhitelist=["x.com"]))
    assert card._countdown_label.text() == "30 min to go"
    assert card._ring._big == "0m" and card._ring._small == "of 30m today"
    assert card._restrictions_variants[0] == "2 apps blocked · 1 site ok"
    assert card._blocklist_button.text() in card._restrictions_variants
    assert card._start_button.isEnabled() and card._start_button.text().endswith("Start")
    assert not card._running_panel.isVisible() and not card._armed_panel.isVisible()


def test_no_rules_reads_as_no_restrictions(qtbot, world):
    card = _card(qtbot, _task())
    assert card._restrictions_variants[0] == "No restrictions set"
    assert card._blocklist_button.text() in card._restrictions_variants
    assert not card._blocklist_button.isEnabled()


def test_hard_lock_is_marked(qtbot, world):
    card = _card(qtbot, _task(lockMode="hard"))
    assert "Hard lock" in [label.text() for label in card.findChildren(QLabel)]


# --------------------------------------------------------------- start ----

def test_start_opens_the_three_choices_and_cancel_closes_them(qtbot, world):
    card = _card(qtbot, _task())
    card._start_button.click()
    assert card._armed_panel.isVisible() and not card._idle_actions.isVisible()
    assert card._burnout_button.isVisible() and card._custom_button.isVisible()
    assert card._pomodoro_button.isVisible()
    assert not card._custom_row.isVisible()
    card._cancel_button.click()
    assert card._idle_actions.isVisible() and not card._armed_panel.isVisible()


def test_custom_time_shows_a_minutes_box_and_back_returns(qtbot, world):
    card = _card(qtbot, _task())
    card._start_button.click()
    card._custom_button.click()
    assert card._custom_row.isVisible() and not card._burnout_button.isVisible()
    assert card._duration_edit.text() == "30"                      # today's goal is the default
    assert card._cancel_button.text() == "Back"
    card._cancel_button.click()
    assert card._burnout_button.isVisible() and not card._custom_row.isVisible()


def test_custom_time_starts_a_timed_session_for_the_task(qtbot, world):
    task = _task(lockMode="hard", processBlocklist=["bad.exe"])
    card = _card(qtbot, task)
    card._start_button.click()
    card._custom_button.click()
    card._duration_edit.setText("45")
    card._custom_start_button.click()
    status = sm.get_status()
    assert status["isActive"] and status["source"] == "task" and status["eventId"] == task["id"]
    assert status["lockMode"] == "hard" and status["processBlocklist"] == ["bad.exe"]
    assert not status["isBreak"] and 44 * 60 <= status["secondsRemaining"] <= 45 * 60


def test_until_burnout_button_starts_a_burnout_session(qtbot, world):
    card = _card(qtbot, _task())
    card._start_button.click()
    card._burnout_button.click()
    assert sm.get_status()["isActive"] and sm.get_status()["isBurnout"]


def test_pomodoro_bar_opens_the_window_then_starts(qtbot, world, monkeypatch):
    card = _card(qtbot, _task())
    card._start_button.click()
    monkeypatch.setattr(tasks_tab._PomodoroDialog, "get_settings", staticmethod(lambda parent=None: (25, 5, 4)))
    card._pomodoro_button.click()
    assert sm.get_status()["pomodoro"]["totalCycles"] == 4


def test_a_busy_card_cannot_be_started(qtbot, world):
    a, b = _task("Alpha"), _task("Beta")
    sm.start_session(30, "soft", [], [], source="task", event_id=a["id"], event_title="Alpha")
    card = _card(qtbot, b)
    assert not card._start_button.isEnabled()
    assert card._busy_note.text() == "Alpha is running"
    card._arm()                                                    # a stray call must not arm it
    assert not card._armed_panel.isVisible()


# ------------------------------------------------------------- running ----

def test_running_card_shows_the_session_ring_and_a_running_chip(qtbot, world):
    task = _task()
    sm.start_session(30, "soft", [], [], source="task", event_id=task["id"], event_title="Study")
    card = _card(qtbot, task)
    assert card._running_panel.isVisible() and not card._idle_actions.isVisible()
    assert card._state_chip.text() == "Running"
    assert card._ring._small == "remaining"
    assert card._ring._big in ("30:00", "29:59")
    assert card._pause_button.text() == "Pause" and "remaining" in card._countdown_label.text()


def test_paused_card_offers_resume(qtbot, world):
    task = _task()
    sm.start_session(30, "soft", [], [], source="task", event_id=task["id"], event_title="Study")
    sm.pause_session()
    card = _card(qtbot, task)
    assert card._state_chip.text() == "Paused" and card._ring._small == "paused"
    assert card._pause_button.text() == "Resume"


def test_pomodoro_break_is_labelled_and_says_nothing_is_blocked(qtbot, world):
    task = _task()
    sm.start_pomodoro_session(1, 1, 2, "soft", [], [], source="task", event_id=task["id"], event_title="Study")
    sm._state["endTime"] = (sm.datetime.now() - sm.timedelta(seconds=2)).isoformat()
    sm.get_status()                                                # the focus block ends -> break
    card = _card(qtbot, task)
    assert card._state_chip.text() == "Break" and card._ring._small == "break"
    assert "Nothing is blocked" in card._detail_label.text()


def test_waiting_card_has_a_dashed_frame_and_switches_back(qtbot, world):
    a, b = _task("Alpha"), _task("Beta")
    sm.start_session(30, "soft", [], [], source="task", event_id=a["id"], event_title="Alpha")
    sm.pause_session()
    sm.start_session(30, "soft", [], [], source="task", event_id=b["id"], event_title="Beta")
    card = _card(qtbot, a)
    assert card._state_chip.text() == "Waiting" and "dashed" in card.styleSheet()
    assert card._pause_button.text() == "Switch to this"
    card._pause_button.click()
    assert sm.get_status()["eventId"] == a["id"] and not sm.get_status()["isPaused"]


def test_the_card_is_only_restyled_when_its_look_changes(qtbot, world):
    task = _task()
    card = _card(qtbot, task)
    first = card.styleSheet()
    for _ in range(3):
        _refresh(card)
    assert card.styleSheet() == first
    sm.start_session(30, "soft", [], [], source="task", event_id=task["id"], event_title="Study")
    _refresh(card)
    assert card.styleSheet() != first


# ------------------------------------------------------------- vacation ----

def _enter():
    return QEnterEvent(QPointF(2, 2), QPointF(2, 2), QPointF(2, 2))


def _bank_vacation(monkeypatch, minutes):
    monkeypatch.setattr(tasks_store, "vacation_balance_minutes", lambda task, sessions, today=None: float(minutes))


def test_vacation_line_is_plain_text_with_nothing_banked(qtbot, world):
    card = _card(qtbot, _task())
    assert card._vacation_label.text() == "No vacation banked" and not card._vacation_label.is_usable()
    card._vacation_label.enterEvent(_enter())
    assert not card._vacation_label.tag_visible()


def test_hovering_banked_vacation_shows_use_and_leaving_hides_it(qtbot, world, monkeypatch):
    _bank_vacation(monkeypatch, 65)
    card = _card(qtbot, _task())
    line = card._vacation_label
    assert line.text() == "1h 5m vacation" and line.is_usable()
    assert not line.tag_visible()
    line.enterEvent(_enter())
    assert line.tag_visible()
    line.leaveEvent(QEvent(QEvent.Leave))
    assert not line.tag_visible()


def test_clicking_vacation_opens_the_window_and_spends_the_minutes(qtbot, world, monkeypatch):
    _bank_vacation(monkeypatch, 65)
    task = _task()
    changed = []
    card = tasks_tab._TaskCard(task, on_changed=lambda: changed.append(1))
    qtbot.addWidget(card)
    card.update_dynamic(sm.get_status(), [])
    seen = {}

    def fake(name, color, balance, usable, parent=None):
        seen.update(name=name, balance=balance, usable=usable)
        return 10

    monkeypatch.setattr(tasks_tab._VacationDialog, "get_minutes", staticmethod(fake))
    card._vacation_label.clicked.emit()
    assert seen == {"name": "Study", "balance": 65, "usable": 30}
    assert tasks_store.get_task(task["id"])["cashedInDates"][date.today().isoformat()] == 10
    assert changed == [1]


def test_cancelling_the_vacation_window_spends_nothing(qtbot, world, monkeypatch):
    _bank_vacation(monkeypatch, 65)
    task = _task()
    card = _card(qtbot, task)
    monkeypatch.setattr(tasks_tab._VacationDialog, "get_minutes", staticmethod(lambda *a, **k: None))
    card._vacation_label.clicked.emit()
    assert not tasks_store.get_task(task["id"]).get("cashedInDates")


def test_vacation_window_validates_the_minutes(qtbot):
    dialog = tasks_tab._VacationDialog("Study", "#5B8DEF", 65, 30)
    qtbot.addWidget(dialog)
    for bad in ("0", "31", "x", ""):
        dialog._minutes_edit.setText(bad)
        dialog._confirm()
        assert dialog._result is None and dialog._status_label.text()
    dialog._minutes_edit.setText("20")
    dialog._confirm()
    assert dialog._result == 20


def test_vacation_window_chips_fill_the_box(qtbot):
    dialog = tasks_tab._VacationDialog("Study", "#5B8DEF", 65, 30)
    qtbot.addWidget(dialog)
    chips = {b.text(): b for b in dialog.findChildren(QPushButton)}
    chips["All 30"].click()
    assert dialog._minutes_edit.text() == "30"
    chips["10"].click()
    assert dialog._minutes_edit.text() == "10" and dialog._use_button.text() == "Use 10 minutes"


# -------------------------------------------------------------- reviews ----

def _linked_topic(task, *problem_names):
    topic = review_store.create_topic("Topic " + task["name"])
    review_store.update_topic_link(topic["id"], task["id"])
    subject = review_store.create_subject(topic["id"], "Sub", "#8E44AD")
    return [review_store.create_problem(topic["id"], subject["id"], n, 3, "text", description_text="x")
            for n in problem_names]


def test_a_review_beside_the_tasks_session_is_listed_in_its_card(qtbot, world):
    task = _task()
    (problem,) = _linked_topic(task, "Epsilon delta")
    sm.start_pomodoro_session(25, 5, 4, "soft", [], [], source="task", event_id=task["id"], event_title="Study")
    review_store.start_review(problem["id"])
    card = _card(qtbot, task)
    _refresh(card, tasks_tab._reviews_by_task()[task["id"]])
    assert card._review_box.isVisible()
    assert len(card._review_box.pause_buttons) == 1


def test_the_review_line_pauses_and_resumes_that_review(qtbot, world):
    task = _task()
    (problem,) = _linked_topic(task, "Epsilon delta")
    review_store.start_review(problem["id"])
    card = _card(qtbot, task)
    row = tasks_tab._reviews_by_task()[task["id"]][0]
    assert row["state"] == "running"
    card._toggle_review(row)
    assert review_store.get_active_review()["isPaused"]
    card._toggle_review(tasks_tab._reviews_by_task()[task["id"]][0])
    assert not review_store.get_active_review()["isPaused"]


def test_several_reviews_read_as_a_list_with_the_running_one_first(qtbot, world):
    task = _task()
    first, second, third = _linked_topic(task, "A", "B", "C")
    review_store.start_review(first["id"])
    review_store.park_active_review()
    review_store.start_review(second["id"])
    review_store.park_active_review()
    review_store.start_review(third["id"])
    rows = tasks_tab._reviews_by_task()[task["id"]]
    assert [r["name"] for r in rows] == ["C", "A", "B"]
    assert [r["state"] for r in rows] == ["running", "paused", "paused"]


def test_reviews_of_another_tasks_topic_stay_out_of_the_card(qtbot, world):
    mine, other = _task("Mine"), _task("Other")
    (problem,) = _linked_topic(other, "Elsewhere")
    review_store.start_review(problem["id"])
    reviews = tasks_tab._reviews_by_task()
    assert mine["id"] not in reviews and other["id"] in reviews


# ---------------------------------------------------------------- grid ----

def _tab(qtbot, count=4):
    for i in range(count):
        _task(f"Task {i}")
    tab = tasks_tab.TasksTab()
    qtbot.addWidget(tab)
    return tab


def _columns_used(tab):
    return len({tab._grid.getItemPosition(tab._grid.indexOf(c))[1] for c in tab._card_order})


@pytest.mark.parametrize("width,columns", [(420, 1), (760, 2), (1150, 3), (1600, 4)])
def test_cards_flow_into_as_many_columns_as_fit(qtbot, world, width, columns):
    tab = _tab(qtbot)
    tab.resize(width, 800)
    tab.show()
    qtbot.wait(50)
    assert _columns_used(tab) == columns
    assert tab._scroll.horizontalScrollBar().maximum() == 0


def test_cards_move_when_the_window_is_resized(qtbot, world):
    tab = _tab(qtbot)
    tab.resize(1600, 800)
    tab.show()
    qtbot.wait(50)
    assert _columns_used(tab) == 4
    tab.resize(700, 800)
    qtbot.wait(50)
    assert _columns_used(tab) == 1
    tab.resize(1200, 800)
    qtbot.wait(50)
    assert _columns_used(tab) == 3


def test_timer_does_nothing_while_the_tab_is_hidden(qtbot, world, monkeypatch):
    tab = _tab(qtbot, 1)
    ticks = []
    monkeypatch.setattr(tab, "_tick", lambda: ticks.append(1))
    tab._tick_if_visible()
    assert ticks == []
    tab.show()
    assert ticks == [1]                                            # showing catches up at once


def test_summary_counts_goals_reached(qtbot, world, monkeypatch):
    tab = _tab(qtbot, 2)
    tab.show()
    assert tab._summary_label.text() == "Today · 0 of 2 goals reached"
    first = next(iter(tab._cards.values()))
    monkeypatch.setattr(tasks_store, "logged_seconds_for_date", lambda *a, **k: 31 * 60)
    tab._tick()
    assert tab._summary_label.text() == "Today · 2 of 2 goals reached"
    assert first.goal_state == "reached"


# ------------------------------------------------------- edit pencil / fit ----

def test_pencil_appears_in_the_corner_on_hover_and_opens_the_editor(qtbot, world, monkeypatch):
    card = _card(qtbot, _task())
    card.resize(360, 300)
    assert card._edit_button.isHidden()
    card.enterEvent(_enter())
    assert not card._edit_button.isHidden()
    button = card._edit_button
    assert button.x() + button.width() <= card.width() and button.x() > card.width() // 2
    assert button.y() < 24
    opened = []
    monkeypatch.setattr(tasks_tab, "open_task_editor", lambda task, on_saved: opened.append(task["id"]))
    button.click()
    assert opened == [card._task["id"]]
    card.leaveEvent(QEvent(QEvent.Leave))
    assert card._edit_button.isHidden()


def test_pencil_is_still_there_while_the_task_runs(qtbot, world):
    task = _task()
    sm.start_session(30, "soft", [], [], source="task", event_id=task["id"], event_title=task["name"])
    card = _card(qtbot, task)
    assert card._state_chip.isVisible()  # "Running" chip in the header
    card.enterEvent(_enter())
    assert not card._edit_button.isHidden()
    assert not card._edit_button.geometry().intersects(card._state_chip.geometry().translated(card._state_chip.parent().pos()))


@pytest.mark.parametrize("width", [tasks_tab.CARD_MIN, 400, tasks_tab.CARD_MAX])
def test_restrictions_line_is_never_cut_off(qtbot, world, width):
    card = _card(qtbot, _task(processBlocklist=[f"a{i}.exe" for i in range(12)], domainWhitelist=[f"s{i}.com" for i in range(14)]))
    card.setFixedWidth(width)
    card.show()
    qtbot.wait(30)
    button = card._blocklist_button
    fm = button.fontMetrics()
    assert fm.horizontalAdvance(button.text()) <= button.width()
    assert button.text() in card._restrictions_variants or button.text().endswith("…")
    assert "12 apps blocked · 14 sites ok" in button.toolTip() or button.text() == "12 apps blocked · 14 sites ok"


# ------------------------------------------------------------ period goals ----

def _weekly_task(minutes=600, **extra):
    return _task("Read", recurrence="weekly_total", targetMinutes=minutes, **extra)


def test_weekly_goal_card_shows_the_week_not_today(qtbot, world):
    card = _card(qtbot, _weekly_task())
    assert card._ring._small == "of 10h this week"
    assert card._countdown_label.text() == "10h to go"
    detail = card._detail_label.text()
    assert detail == "Due today" or ("left" in detail and "a day" in detail)  # Sunday is the last day
    assert card.goal_state == "open"


def test_weekly_goal_is_reached_by_work_on_any_day_of_the_week(qtbot, world, monkeypatch):
    task = _weekly_task(minutes=60)
    day = date.today()
    monkeypatch.setattr(session_history, "load_all", lambda: [{
        "source": "task", "eventId": task["id"], "violationLog": [],
        "startTime": f"{day.isoformat()}T09:00:00", "endTime": f"{day.isoformat()}T10:05:00",
    }])
    card = _card(qtbot, task)
    assert card.goal_state == "reached"
    assert card._countdown_label.text() in ("Goal reached", "+5 min over")


def test_weekly_goal_suggests_a_sitting_not_the_whole_week(qtbot, world):
    card = _card(qtbot, _weekly_task(minutes=600))
    card._arm()
    assert card._duration_edit.text() == "60"
