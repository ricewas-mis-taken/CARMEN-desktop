"""sync_client._apply_json_store reads tasks.json / board.json, merges pulled
records, and writes the whole file back. A local edit (tasks_store.update_task
/ board_store.update_task, which hold the store's own lock) that lands between
that read and write must not be silently overwritten."""
import threading
import time

import pytest

import board_store
import sync_client
import tasks_store


@pytest.fixture
def isolate_tasks(tmp_path, monkeypatch):
    monkeypatch.setattr(tasks_store, "TASKS_PATH", str(tmp_path / "tasks.json"))
    monkeypatch.setattr(board_store, "BOARD_PATH", str(tmp_path / "board.json"))
    yield


def _record(table, sync_id, name):
    return {
        "table_name": table, "sync_id": sync_id, "updated_at": "2030-01-01T00:00:00+00:00",
        "device_id": "other", "is_deleted": False,
        "data": {"name": name, "targetMinutes": 10},
    }


def test_local_task_edit_during_sync_apply_is_not_lost(isolate_tasks):
    a = tasks_store.create_task({"name": "A", "targetMinutes": 10})
    b = tasks_store.create_task({"name": "B", "targetMinutes": 10})

    real_load = tasks_store.load_tasks
    entered = threading.Event()

    def slow_load(include_deleted=False):
        result = real_load(include_deleted=include_deleted)
        if threading.current_thread().name == "syncer":
            entered.set()
            time.sleep(0.5)  # the gap between sync's read and its write
        return result

    # Patch the attribute _apply_tasks hands to _apply_json_store.
    import unittest.mock as mock
    with mock.patch.object(tasks_store, "load_tasks", slow_load):
        t = threading.Thread(
            target=lambda: sync_client._apply_tasks([_record("tasks", "newone", "Remote")]),
            name="syncer")
        t.start()
        assert entered.wait(2)
        tasks_store.update_task(a["id"], {"name": "A-edited-locally"})
        t.join()

    names = {x["name"] for x in real_load()}
    assert "A-edited-locally" in names, names
    assert "Remote" in names


def test_local_board_edit_during_sync_apply_is_not_lost(isolate_tasks):
    a = board_store.create_task("A", 3)
    board_store.create_task("B", 3)

    real_load = board_store.load_board
    entered = threading.Event()

    def slow_load(include_deleted=False):
        result = real_load(include_deleted=include_deleted)
        if threading.current_thread().name == "syncer":
            entered.set()
            time.sleep(0.5)
        return result

    import unittest.mock as mock
    with mock.patch.object(board_store, "load_board", slow_load):
        t = threading.Thread(
            target=lambda: sync_client._apply_board([_record("board", "newone", "Remote")]),
            name="syncer")
        t.start()
        assert entered.wait(2)
        board_store.update_task(a["id"], "A-edited-locally")
        t.join()

    names = {x["name"] for x in real_load()}
    assert "A-edited-locally" in names, names
    assert "Remote" in names
