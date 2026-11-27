from datetime import date, datetime, timedelta

import pytest

import shutdown_model


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(shutdown_model, "PATH", str(tmp_path / "shutdown_log.json"))


def _night(anchor, hour, minute=0):
    """The moment on the night that began on `anchor` (hours under 6 fall on the next calendar day)."""
    day = anchor + timedelta(days=1) if hour < 6 else anchor
    return datetime(day.year, day.month, day.day, hour, minute)


def test_a_night_is_measured_from_six_pm_across_midnight():
    assert shutdown_model.night_of(datetime(2026, 11, 20, 23, 50)) == (date(2026, 11, 20), 350)
    assert shutdown_model.night_of(datetime(2026, 11, 21, 0, 20)) == (date(2026, 11, 20), 380)
    assert shutdown_model.night_of(datetime(2026, 11, 21, 14, 0)) is None          # daytime is not a night


def test_the_last_mark_of_the_night_is_the_stop_time():
    for hour, minute in ((22, 0), (23, 10), (22, 30)):
        shutdown_model.note_active(_night(date(2026, 11, 16), hour, minute))
    assert shutdown_model._read() == {"2026-11-16": 310}


def _log(anchor_first, hours):
    for i, (hour, minute) in enumerate(hours):
        shutdown_model.note_active(_night(anchor_first + timedelta(days=i), hour, minute))


def test_it_uses_the_default_until_it_has_a_few_nights():
    _log(date(2026, 11, 9), [(23, 0), (23, 0)])
    stop, learned = shutdown_model.expected_stop(datetime(2026, 11, 11, 20, 0))
    assert not learned and (stop.hour, stop.minute) == shutdown_model.DEFAULT_STOP


def test_it_learns_a_usual_stop_even_when_it_passes_midnight():
    _log(date(2026, 11, 2), [(23, 50), (0, 20), (0, 0), (23, 40), (0, 10), (23, 55)])
    stop, learned = shutdown_model.expected_stop(datetime(2026, 11, 9, 20, 0))
    assert learned
    assert abs((stop - datetime(2026, 11, 10, 0, 0)).total_seconds()) <= 25 * 60


def test_an_all_nighter_does_not_drag_the_guess():
    _log(date(2026, 11, 2), [(23, 0), (23, 10), (5, 30), (23, 5), (22, 55), (23, 0)])
    stop, _ = shutdown_model.expected_stop(datetime(2026, 11, 9, 20, 0))
    assert stop.hour == 23 and stop.minute <= 15


def test_friday_nights_are_judged_by_other_late_nights():
    # Mon-Thu around 22:30, Fri/Sat around 01:00, all in the three weeks before Friday 2026-11-20
    day = date(2026, 10, 31)
    for _ in range(20):
        late = day.weekday() in (4, 5)
        shutdown_model.note_active(_night(day, 1 if late else 22, 0 if late else 30))
        day += timedelta(days=1)
    friday, _ = shutdown_model.expected_stop(datetime(2026, 11, 20, 20, 0))
    thursday, _ = shutdown_model.expected_stop(datetime(2026, 11, 19, 20, 0))
    assert friday.hour == 1 and thursday.hour == 22


def test_the_two_nudges_come_thirty_and_fifteen_minutes_before():
    nudges = shutdown_model.nudge_times(datetime(2026, 11, 20, 20, 0))
    assert [lead for lead, _t in nudges] == [30, 15]
    assert nudges[1][1] - nudges[0][1] == timedelta(minutes=15)


def test_the_usual_stop_as_minutes_after_midnight():
    assert shutdown_model.usual_stop_minutes(date(2026, 11, 9)) == 22 * 60 + 30         # nothing learned: the default
    _log(date(2026, 11, 2), [(0, 30)] * 6)
    assert shutdown_model.usual_stop_minutes(date(2026, 11, 9)) == 24 * 60 + 30
