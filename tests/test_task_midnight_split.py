from datetime import date
import tasks_store

def test_session_across_midnight_is_credited_to_the_start_day_only():
    task = {"id": "t1"}
    sessions = [{"source": "task", "eventId": "t1", "startTime": "2026-10-03T23:00:00",
                 "endTime": "2026-10-04T03:00:00", "violationLog": []}]
    d3 = tasks_store.logged_seconds_for_date(task, date(2026, 10, 3), sessions) / 3600
    d4 = tasks_store.logged_seconds_for_date(task, date(2026, 10, 4), sessions) / 3600
    print("Oct 3 credited hours:", d3, "| Oct 4 credited hours:", d4, "(truth: 1.0 and 3.0)")
    assert (d3, d4) == (1.0, 3.0)


def test_pause_at_midnight_is_respected():
    task = {"id": "t1"}
    log = [{"kind": "pause", "timestamp": "2026-10-03T23:30:00"}, {"kind": "resume", "timestamp": "2026-10-04T01:00:00"}]
    sessions = [{"source": "task", "eventId": "t1", "startTime": "2026-10-03T23:00:00",
                 "endTime": "2026-10-04T03:00:00", "violationLog": log}]
    assert tasks_store.logged_seconds_for_date(task, date(2026, 10, 3), sessions) == 30 * 60
    assert tasks_store.logged_seconds_for_date(task, date(2026, 10, 4), sessions) == 2 * 3600
