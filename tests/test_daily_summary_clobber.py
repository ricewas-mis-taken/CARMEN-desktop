"""daily_summary_store.flush_through_yesterday() treats an unreadable
daily_summaries.json as {} and then saves only the days it just computed --
erasing the long-term per-day totals that exist precisely so history survives
if session_history.json is lost."""
import builtins
import json
from datetime import date, datetime, timedelta

import daily_summary_store
import session_history
import tasks_store


def test_unreadable_summary_file_is_not_overwritten(isolate_state, tmp_path, monkeypatch):
    monkeypatch.setattr(daily_summary_store, "SUMMARY_PATH", str(tmp_path / "daily_summaries.json"))
    monkeypatch.setattr(tasks_store, "TASKS_PATH", str(tmp_path / "tasks.json"))
    task = tasks_store.create_task({"name": "usaco"})
    old_day = "2026-01-01"
    daily_summary_store._save({old_day: {task["id"]: {"taskName": "usaco", "secondsWorked": 3600}}})

    yday = date.today() - timedelta(days=1)
    start = datetime.combine(yday, datetime.min.time()).replace(hour=10)
    session_history.append_entry({
        "startTime": start.isoformat(), "endTime": (start + timedelta(hours=1)).isoformat(),
        "endType": "manual", "source": "task", "eventId": task["id"], "violationLog": [],
    })

    path = daily_summary_store.SUMMARY_PATH
    real_open = builtins.open
    state = {"failed": False}

    def flaky_open(file, mode="r", *a, **kw):
        if str(file) == path and "r" in mode and not state["failed"]:
            state["failed"] = True
            raise PermissionError("locked")
        return real_open(file, mode, *a, **kw)

    with monkeypatch.context() as m:
        m.setattr(builtins, "open", flaky_open)
        daily_summary_store.flush_through_yesterday()

    with open(path, encoding="utf-8") as f:
        assert old_day in json.load(f), "earlier daily summaries were erased"
