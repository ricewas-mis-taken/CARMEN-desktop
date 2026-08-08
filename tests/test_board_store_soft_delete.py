"""Tests for board_store.delete_task()'s soft-delete behavior (Phase 3 Part
A of the sync project): a deleted task must stay on disk as a tombstone
(isDeleted=True) rather than being physically removed, so a future sync
push can tell other devices about the deletion."""
import json

import pytest

import board_store
import device_id


@pytest.fixture
def isolate_board(tmp_path, monkeypatch):
    monkeypatch.setattr(board_store, "BOARD_PATH", str(tmp_path / "board.json"))
    monkeypatch.setattr(device_id, "DEVICE_ID_PATH", str(tmp_path / "device_id.txt"))
    monkeypatch.setattr(device_id, "_cached_id", None)
    yield


def test_delete_task_tombstones_instead_of_removing(isolate_board):
    task = board_store.create_task("Clean garage", importance=5)

    assert board_store.delete_task(task["id"]) is True

    with open(board_store.BOARD_PATH, "r", encoding="utf-8") as f:
        on_disk = json.load(f)
    assert len(on_disk["tasks"]) == 1
    row = on_disk["tasks"][0]
    assert row["isDeleted"] is True
    assert row["deviceId"] == device_id.get_device_id()
    assert row["updatedAt"] >= task["updatedAt"]


def test_deleted_task_is_hidden_from_reads(isolate_board):
    task = board_store.create_task("Clean garage", importance=5)
    board_store.delete_task(task["id"])

    assert board_store.get_task(task["id"]) is None
    assert task["id"] not in [t["id"] for t in board_store.list_active_tasks()]
    assert task["id"] not in [t["id"] for t in board_store.list_finished_tasks()]
    assert task["id"] not in [t["id"] for t in board_store.list_upcoming_tasks()]


def test_deleting_already_deleted_task_returns_false(isolate_board):
    task = board_store.create_task("Clean garage", importance=5)
    assert board_store.delete_task(task["id"]) is True
    assert board_store.delete_task(task["id"]) is False


def test_deleting_unknown_task_returns_false(isolate_board):
    assert board_store.delete_task("does-not-exist") is False


def test_creating_a_new_task_does_not_wipe_an_existing_tombstone(isolate_board):
    old_task = board_store.create_task("Old task", importance=3)
    board_store.delete_task(old_task["id"])

    board_store.create_task("New task", importance=8)

    with open(board_store.BOARD_PATH, "r", encoding="utf-8") as f:
        on_disk = json.load(f)
    assert len(on_disk["tasks"]) == 2
    tombstoned = next(t for t in on_disk["tasks"] if t["id"] == old_task["id"])
    assert tombstoned["isDeleted"] is True
