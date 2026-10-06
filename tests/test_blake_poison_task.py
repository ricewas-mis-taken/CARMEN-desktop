import json
import pytest
import sync_client, tasks_store, board_store
import qt_ui.tasks_tab as tasks_tab
import qt_ui.board_tab as board_tab


def _rec(table, data):
    return {"table_name": table, "sync_id": "poison1", "data": data, "device_id": "dev-x",
            "updated_at": "2030-01-01T00:00:00+00:00", "is_deleted": False}


@pytest.mark.parametrize("data", [{}, {"name": "x"}, {"name": "x", "targetMinutes": "lots"}, {"name": None, "targetMinutes": None}])
def test_poisoned_synced_task_does_not_break_tasks_tab(qtbot, isolate_state, tmp_path, monkeypatch, data):
    monkeypatch.setattr(tasks_store, "TASKS_PATH", str(tmp_path / "tasks.json"))
    applied, skipped, failed = sync_client._apply_tasks([_rec("tasks", data)])
    print("applied", applied, "failed", failed, "stored", tasks_store.load_tasks())
    tab = tasks_tab.TasksTab()
    qtbot.addWidget(tab)


@pytest.mark.parametrize("data", [{}, {"name": "x"}, {"name": "x", "importance": "high"}])
def test_poisoned_synced_board_task_does_not_break_board_tab(qtbot, tmp_path, monkeypatch, data):
    monkeypatch.setattr(board_store, "BOARD_PATH", str(tmp_path / "board.json"))
    applied, skipped, failed = sync_client._apply_board([_rec("board", data)])
    print("applied", applied, "failed", failed, "stored", board_store.load_board())
    tab = board_tab.BoardTab()
    qtbot.addWidget(tab)
