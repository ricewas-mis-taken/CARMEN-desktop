"""Tests for the multi-device sync scaffolding (updatedAt/deviceId/
isDeleted) backfilled onto board.json rows saved before those fields
existed -- non-destructiveness matters here since this runs automatically
against the user's real board.json the next time the app starts."""
import json

import pytest

import board_store


@pytest.fixture
def isolate_board(tmp_path, monkeypatch):
    monkeypatch.setattr(board_store, "BOARD_PATH", str(tmp_path / "board.json"))
    yield


def _write_old_format_board(path, tasks):
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"tasks": tasks}, f)


def test_old_format_board_tasks_get_backfilled_without_data_loss(isolate_board):
    old_task = {
        "id": "abc123", "name": "Clean garage", "importance": 7,
        "recurringDays": [], "recurrencePattern": "none",
        "descriptionText": "", "descriptionPhotoPath": None, "descriptionLink": "",
        "firstOpenedAt": None, "finished": False, "finishedAt": None,
        "nextDueDate": None, "createdAt": "2020-01-01T00:00:00",
    }
    _write_old_format_board(board_store.BOARD_PATH, [old_task])

    tasks = board_store.load_board()

    assert len(tasks) == 1
    assert tasks[0]["name"] == "Clean garage"
    assert tasks[0]["importance"] == 7
    assert tasks[0]["updatedAt"] == "2020-01-01T00:00:00"
    assert tasks[0]["deviceId"] is None
    assert tasks[0]["isDeleted"] is False


def test_backfill_persists_to_disk(isolate_board):
    old_task = {"id": "abc123", "name": "Clean garage", "createdAt": "2020-01-01T00:00:00"}
    _write_old_format_board(board_store.BOARD_PATH, [old_task])

    board_store.load_board()

    with open(board_store.BOARD_PATH, "r", encoding="utf-8") as f:
        on_disk = json.load(f)
    assert on_disk["tasks"][0]["updatedAt"] == "2020-01-01T00:00:00"


def test_backfill_applies_to_every_old_task_not_just_the_first(isolate_board):
    old_tasks = [
        {"id": f"t{i}", "name": f"Task {i}", "createdAt": "2020-01-01T00:00:00"}
        for i in range(5)
    ]
    _write_old_format_board(board_store.BOARD_PATH, old_tasks)

    tasks = board_store.load_board()

    assert len(tasks) == 5
    assert all(t["updatedAt"] == "2020-01-01T00:00:00" for t in tasks)


def test_new_board_task_already_has_sync_fields(isolate_board):
    task = board_store.create_task("Clean garage", importance=5)
    assert task["updatedAt"] == task["createdAt"]
    assert task["deviceId"] is None
    assert task["isDeleted"] is False


def test_mutators_bump_updated_at(isolate_board):
    task = board_store.create_task("Clean garage", importance=5)
    created_updated_at = task["updatedAt"]

    after_importance = board_store.update_importance(task["id"], 8)
    assert after_importance["updatedAt"] >= created_updated_at

    after_finish = board_store.finish_task(task["id"])
    assert after_finish["updatedAt"] >= after_importance["updatedAt"]
