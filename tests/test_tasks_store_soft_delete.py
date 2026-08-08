"""Tests for tasks_store.delete_task()'s soft-delete behavior (Phase 3 Part
A of the sync project): mirrors board_store's soft-delete tombstone
pattern -- see test_board_store_soft_delete.py."""
import json

import pytest

import device_id
import tasks_store


@pytest.fixture
def isolate_tasks(tmp_path, monkeypatch):
    monkeypatch.setattr(tasks_store, "TASKS_PATH", str(tmp_path / "tasks.json"))
    monkeypatch.setattr(device_id, "DEVICE_ID_PATH", str(tmp_path / "device_id.txt"))
    monkeypatch.setattr(device_id, "_cached_id", None)
    yield


def test_delete_task_tombstones_instead_of_removing(isolate_tasks):
    task = tasks_store.create_task({"name": "Writing", "targetMinutes": 45})

    assert tasks_store.delete_task(task["id"]) is True

    with open(tasks_store.TASKS_PATH, "r", encoding="utf-8") as f:
        on_disk = json.load(f)
    assert len(on_disk["tasks"]) == 1
    row = on_disk["tasks"][0]
    assert row["isDeleted"] is True
    assert row["deviceId"] == device_id.get_device_id()


def test_deleted_task_is_hidden_from_get_task_and_load_tasks(isolate_tasks):
    task = tasks_store.create_task({"name": "Writing", "targetMinutes": 45})
    tasks_store.delete_task(task["id"])

    assert tasks_store.get_task(task["id"]) is None
    assert task["id"] not in [t["id"] for t in tasks_store.load_tasks()]


def test_deleting_already_deleted_task_returns_false(isolate_tasks):
    task = tasks_store.create_task({"name": "Writing", "targetMinutes": 45})
    assert tasks_store.delete_task(task["id"]) is True
    assert tasks_store.delete_task(task["id"]) is False


def test_creating_a_new_task_does_not_wipe_an_existing_tombstone(isolate_tasks):
    old_task = tasks_store.create_task({"name": "Old", "targetMinutes": 10})
    tasks_store.delete_task(old_task["id"])

    tasks_store.create_task({"name": "New", "targetMinutes": 20})

    with open(tasks_store.TASKS_PATH, "r", encoding="utf-8") as f:
        on_disk = json.load(f)
    assert len(on_disk["tasks"]) == 2
    tombstoned = next(t for t in on_disk["tasks"] if t["id"] == old_task["id"])
    assert tombstoned["isDeleted"] is True
