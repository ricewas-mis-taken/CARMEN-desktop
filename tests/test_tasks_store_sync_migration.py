"""Tests for the multi-device sync scaffolding (updatedAt/deviceId/
isDeleted) backfilled onto tasks.json rows saved before those fields
existed -- non-destructiveness matters here since this runs automatically
against the user's real tasks.json the next time the app starts."""
import json

import pytest

import tasks_store


@pytest.fixture
def isolate_tasks(tmp_path, monkeypatch):
    monkeypatch.setattr(tasks_store, "TASKS_PATH", str(tmp_path / "tasks.json"))
    yield


def _write_old_format_tasks(path, tasks):
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"tasks": tasks}, f)


def test_old_format_tasks_get_backfilled_without_data_loss(isolate_tasks):
    old_task = {
        "id": "abc123", "name": "Reading", "color": "#5B8DEF", "targetMinutes": 30,
        "recurrence": "daily", "weekdays": [], "lockMode": "soft",
        "processBlocklist": [], "domainWhitelist": [], "cashedInDates": {},
        "archived": False, "createdAt": "2020-01-01T00:00:00",
    }
    _write_old_format_tasks(tasks_store.TASKS_PATH, [old_task])

    tasks = tasks_store.load_tasks()

    assert len(tasks) == 1
    assert tasks[0]["name"] == "Reading"  # original data intact
    assert tasks[0]["targetMinutes"] == 30
    assert tasks[0]["updatedAt"] == "2020-01-01T00:00:00"  # backfilled from createdAt
    assert tasks[0]["deviceId"] is None
    assert tasks[0]["isDeleted"] is False


def test_backfill_persists_to_disk(isolate_tasks):
    """The migration must actually rewrite tasks.json, not just patch the
    in-memory dict on every load -- otherwise a task nobody edits again
    would silently stay old-format forever."""
    old_task = {"id": "abc123", "name": "Reading", "createdAt": "2020-01-01T00:00:00"}
    _write_old_format_tasks(tasks_store.TASKS_PATH, [old_task])

    tasks_store.load_tasks()

    with open(tasks_store.TASKS_PATH, "r", encoding="utf-8") as f:
        on_disk = json.load(f)
    assert on_disk["tasks"][0]["updatedAt"] == "2020-01-01T00:00:00"


def test_backfill_applies_to_every_old_task_not_just_the_first(isolate_tasks):
    """Regression guard: an any(...) over a generator would short-circuit on
    the first task that needed backfilling and skip every task after it."""
    old_tasks = [
        {"id": f"t{i}", "name": f"Task {i}", "createdAt": "2020-01-01T00:00:00"}
        for i in range(5)
    ]
    _write_old_format_tasks(tasks_store.TASKS_PATH, old_tasks)

    tasks = tasks_store.load_tasks()

    assert len(tasks) == 5
    assert all(t["updatedAt"] == "2020-01-01T00:00:00" for t in tasks)
    assert all(t["deviceId"] is None for t in tasks)
    assert all(t["isDeleted"] is False for t in tasks)


def test_new_tasks_already_have_sync_fields(isolate_tasks):
    task = tasks_store.create_task({"name": "Writing"})
    assert task["updatedAt"] == task["createdAt"]
    assert task["deviceId"] is None
    assert task["isDeleted"] is False


def test_update_task_bumps_updated_at(isolate_tasks):
    task = tasks_store.create_task({"name": "Writing"})
    original_updated_at = task["updatedAt"]

    updated = tasks_store.update_task(task["id"], {"targetMinutes": 60})

    assert updated["updatedAt"] >= original_updated_at
