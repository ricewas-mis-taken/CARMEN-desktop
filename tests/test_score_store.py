from datetime import date, timedelta

import score_store
import tasks_store


def _task(**kw):
    return {"id": "t1", "name": "Maths", "recurrence": "daily", "targetMinutes": 60, "color": "#5B8DEF", **kw}


def _entry(task_id, day, minutes, sid="s"):
    start = f"{day.isoformat()}T08:00:00"
    end = f"{day.isoformat()}T{8 + minutes // 60:02d}:{minutes % 60:02d}:00"
    return {"sessionId": f"{sid}-{day}", "startTime": start, "endTime": end, "lockMode": "soft",
            "source": "task", "eventId": task_id, "violations": []}


def test_a_new_score_starts_at_500_and_is_clamped():
    assert score_store.score() == 500
    score_store.add_event("x", date.today(), "t", 900)
    assert score_store.score() == 1000
    score_store.add_event("y", date.today(), "t", -5000)
    assert score_store.score() == 0


def test_the_same_event_key_is_only_counted_once():
    assert score_store.add_event("k", date.today(), "t", 10) is True
    assert score_store.add_event("k", date.today(), "t", 10) is False
    assert score_store.score() == 510


def test_the_first_settle_judges_nothing_before_it_began(monkeypatch):
    assert score_store.settle(today=date.today(), tasks=[_task()], sessions=[]) == 0
    assert score_store.score() == 500


def test_missed_days_are_caught_up_once_and_only_once():
    today = date.today()
    score_store.settle(today=today - timedelta(days=5), tasks=[], sessions=[])      # the score begins
    tasks = [_task()]
    added = score_store.settle(today=today, tasks=tasks, sessions=[])
    assert added == 5                                          # the five days after it began, up to yesterday
    low = score_store.score()
    assert low < 500
    assert score_store.settle(today=today, tasks=tasks, sessions=[]) == 0
    assert score_store.score() == low


def test_a_met_day_adds_points_and_the_score_is_never_reset():
    today = date.today()
    score_store.settle(today=today - timedelta(days=1), tasks=[], sessions=[])
    y = today - timedelta(days=1)
    tasks = [_task()]
    score_store.settle(today=today, tasks=tasks, sessions=[_entry("t1", y, 60)])
    assert score_store.score() == 512
    # a new week or month does not touch it
    score_store.settle(today=today + timedelta(days=40), tasks=[], sessions=[])
    assert score_store.score() == 512


def test_a_period_goal_only_counts_when_its_period_ends():
    today = date(2026, 11, 4)                                   # a Wednesday
    task = _task(recurrence="weekly_total", targetMinutes=300)
    score_store.settle(today=today - timedelta(days=1), tasks=[], sessions=[])
    mid_week = score_store.settle(today=today + timedelta(days=2), tasks=[task], sessions=[])
    assert mid_week == 0                                        # Wed..Fri: the week is not over
    score_store.settle(today=date(2026, 11, 9), tasks=[task], sessions=[])   # Monday: Sunday the 8th ended it
    events = [e for e in score_store.load()["events"] if e["kind"] == "period"]
    assert len(events) == 1 and events[0]["points"] == -30 and events[0]["date"] == "2026-11-08"


def test_a_review_on_time_earns_and_a_late_one_costs():
    d = date(2026, 11, 3)
    score_store.record_review("a", "2026-11-03", d, "P")
    assert score_store.score() == 506
    score_store.record_review("b", "2026-10-31", d, "Q")        # 3 days late
    assert score_store.score() == 506 + (6 - 9)


def test_overdue_reviews_cost_a_little_each_day_but_stop_after_ten():
    today = date(2026, 11, 20)
    due = date(2026, 11, 1)
    score_store.settle(today=today - timedelta(days=20), tasks=[], sessions=[])
    score_store.settle(today=today, tasks=[], sessions=[], overdue=lambda day: [(7, due)] if due < day else [])
    overdue = [e for e in score_store.load()["events"] if e["kind"] == "overdue"]
    assert len(overdue) == score_store.OVERDUE_MAX_DAYS
    assert score_store.score() == 500 - 3 * score_store.OVERDUE_MAX_DAYS


def test_grade_bands():
    assert [score_store.grade(v) for v in (850, 700, 520, 400, 100)] == ["A", "B", "C", "D", "F"]
