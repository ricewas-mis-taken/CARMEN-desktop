"""An edit made on top of a version must beat that version, whatever the two clocks say."""
from datetime import datetime, timedelta

import server_clock
import tasks_store


def test_local_stamp_is_later_than_a_stamp_from_a_fast_clock():
    future = (datetime.now() + timedelta(minutes=5)).isoformat()
    assert datetime.fromisoformat(server_clock.local_stamp(future)) > datetime.fromisoformat(future)


def test_local_stamp_follows_the_cloud_clock(monkeypatch):
    monkeypatch.setattr(server_clock, "_offset_seconds", 120.0)
    stamp = datetime.fromisoformat(server_clock.local_stamp())
    assert stamp > datetime.now() + timedelta(seconds=100)


def test_editing_a_task_whose_stamp_is_ahead_still_wins(tmp_path, monkeypatch):
    monkeypatch.setattr(tasks_store, "TASKS_PATH", str(tmp_path / "tasks.json"))
    task = tasks_store.create_task({"name": "x", "targetMinutes": 30})
    ahead = (datetime.now() + timedelta(minutes=3)).isoformat()
    tasks = tasks_store.load_tasks(include_deleted=True)
    tasks[0]["updatedAt"] = ahead
    tasks_store.save_tasks(tasks)
    tasks_store.update_task(task["id"], {"targetMinutes": 45})
    stored = tasks_store.get_task(task["id"])
    assert stored["targetMinutes"] == 45
    assert datetime.fromisoformat(stored["updatedAt"]) > datetime.fromisoformat(ahead)
