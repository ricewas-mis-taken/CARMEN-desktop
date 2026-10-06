from datetime import datetime, timedelta

import calendar_scheduler as sched


def _event(start, offsets, rrule=None):
    return {
        "id": "ev1", "title": "Exam", "start": start.isoformat(),
        "end": (start + timedelta(hours=1)).isoformat(),
        "rrule": rrule, "reminderOffsets": offsets, "focusProfile": None,
    }


def _simulate_ticks(event, start_at, end_at, step=timedelta(seconds=20)):
    """Runs the scheduler's real per-event logic on each 20s tick, the way
    _tick() does (range_end = now + LOOKAHEAD_HOURS)."""
    now = start_at
    while now <= end_at:
        sched._process_event(event, now, now + timedelta(hours=sched.LOOKAHEAD_HOURS))
        now += step


def test_one_day_before_reminder_fires(monkeypatch):
    fired = []
    monkeypatch.setattr(sched, "_fire_reminder", lambda ev, off: fired.append(off))
    monkeypatch.setattr(sched, "_fired", set())
    start = datetime(2030, 6, 10, 9, 0)
    ev = _event(start, [1440])  # the "1 day before" preset in the event editor
    # app has been running the whole time; simulate ticks from 25h before to the event start
    _simulate_ticks(ev, start - timedelta(hours=25), start)
    print("fired offsets:", fired)
    assert fired == [1440]


def test_hour_before_reminder_still_fires(monkeypatch):
    fired = []
    monkeypatch.setattr(sched, "_fire_reminder", lambda ev, off: fired.append(off))
    monkeypatch.setattr(sched, "_fired", set())
    start = datetime(2030, 6, 10, 9, 0)
    _simulate_ticks(_event(start, [60]), start - timedelta(hours=2), start)
    assert fired == [60]
