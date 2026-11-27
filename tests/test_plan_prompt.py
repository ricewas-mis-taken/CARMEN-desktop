from datetime import date, datetime

import pytest

import config
import plan_prompt
import plan_store
import shutdown_model


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(shutdown_model, "PATH", str(tmp_path / "shutdown.json"))
    monkeypatch.setattr(plan_prompt, "SENT_PATH", str(tmp_path / "sent.json"))
    monkeypatch.setattr(plan_store, "PATH", str(tmp_path / "plans.json"))
    monkeypatch.setattr(config, "load_config", lambda: {})


# No history: the default stop is 22:30, so the nudges are due at 22:00 and 22:15.
def _at(hour, minute, day=24):
    return datetime(2026, 11, day, hour, minute)


def test_nothing_before_the_first_nudge_time():
    assert plan_prompt.due_nudge(_at(21, 59), {}, False) is None


def test_the_half_hour_nudge_then_the_quarter_hour_one():
    assert plan_prompt.due_nudge(_at(22, 1), {}, False) == 30
    assert plan_prompt.due_nudge(_at(22, 16), {"2026-11-24": [30]}, False) == 15


def test_each_nudge_is_sent_once():
    assert plan_prompt.due_nudge(_at(22, 3), {"2026-11-24": [30]}, False) is None


def test_no_nudge_when_tomorrow_is_already_planned_or_nobody_is_there_or_it_is_off():
    assert plan_prompt.due_nudge(_at(22, 1), {}, True) is None
    assert plan_prompt.due_nudge(_at(22, 1), {}, False, around=False) is None
    assert plan_prompt.due_nudge(_at(22, 1), {}, False, enabled=False) is None


def test_a_stale_nudge_after_a_late_start_is_skipped():
    assert plan_prompt.due_nudge(_at(22, 40), {}, False) is None


def test_tick_marks_the_night_and_sends_one_nudge():
    sent = []
    assert plan_prompt.tick(_at(22, 2), idle_seconds=5, notify=lambda lead, day: sent.append((lead, day))) == 30
    assert sent == [(30, date(2026, 11, 25))]
    assert plan_prompt.tick(_at(22, 3), idle_seconds=5, notify=lambda *a: sent.append(a)) is None
    assert shutdown_model._read()["2026-11-24"] == 243            # the last mark, 22:03, is 243 minutes after 18:00


def test_an_idle_computer_neither_counts_as_a_stop_time_nor_gets_a_nudge():
    sent = []
    assert plan_prompt.tick(_at(22, 2), idle_seconds=9999, notify=lambda *a: sent.append(a)) is None
    assert not sent and shutdown_model._read() == {}


def test_after_midnight_the_plan_is_for_the_same_calendar_day():
    assert plan_prompt._tomorrow(datetime(2026, 11, 25, 0, 30)) == date(2026, 11, 25)
    assert plan_prompt._tomorrow(datetime(2026, 11, 24, 22, 0)) == date(2026, 11, 25)
