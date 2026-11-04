from datetime import date, timedelta

import goal_raise
import tasks_store

TASK = {"id": "t", "name": "Read", "recurrence": "daily", "targetMinutes": 60, "createdAt": "2020-01-01T00:00:00"}
TODAY = date(2024, 3, 10)


def _sessions(minutes_by_back):
    out = []
    for back, minutes in minutes_by_back.items():
        d = TODAY - timedelta(days=back)
        out.append({"eventId": "t", "source": "task", "startTime": f"{d}T09:00:00",
                    "endTime": f"{d}T{9 + minutes // 60:02d}:{minutes % 60:02d}:00"})
    return out


def test_goal_beaten_most_days_is_raised_to_a_specific_amount():
    s = _sessions({i: 90 for i in range(1, 8)})
    assert goal_raise.suggested_target(TASK, TODAY, s, tasks_store) == 90


def test_a_goal_met_normally_is_left_alone():
    assert goal_raise.suggested_target(TASK, TODAY, _sessions({i: 62 for i in range(1, 8)}), tasks_store) is None


def test_one_big_day_is_not_a_pattern():
    s = _sessions({1: 150, 2: 60, 3: 60, 4: 60, 5: 60, 6: 60, 7: 60})
    assert goal_raise.suggested_target(TASK, TODAY, s, tasks_store) is None


def test_the_raise_is_capped_at_double():
    s = _sessions({i: 200 for i in range(1, 8)})
    assert goal_raise.suggested_target(TASK, TODAY, s, tasks_store) == 120
