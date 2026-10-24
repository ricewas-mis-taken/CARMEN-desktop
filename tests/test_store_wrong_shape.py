"""tasks.json / board.json holding valid JSON of the wrong shape: a display read shows
nothing instead of crashing, and a read-modify-write refuses rather than replacing the
file's real contents with just one new item."""
import json

import pytest

import board_store
import tasks_store

BAD = ['{"tasks": 5}', '{"tasks": "x"}', "[1, 2]", "null", "123"]


@pytest.fixture
def paths(tmp_path, monkeypatch):
    monkeypatch.setattr(tasks_store, "TASKS_PATH", str(tmp_path / "tasks.json"))
    monkeypatch.setattr(board_store, "BOARD_PATH", str(tmp_path / "board.json"))
    return tmp_path / "tasks.json", tmp_path / "board.json"


@pytest.mark.parametrize("content", BAD)
def test_display_reads_do_not_crash(paths, content):
    for path in paths:
        path.write_text(content, encoding="utf-8")
    assert tasks_store.load_tasks() == []
    assert board_store.load_board() == []


@pytest.mark.parametrize("content", BAD)
def test_mutators_refuse_and_leave_the_file_alone(paths, content):
    tasks_path, board_path = paths
    tasks_path.write_text(content, encoding="utf-8")
    board_path.write_text(content, encoding="utf-8")
    with pytest.raises(tasks_store.TasksLoadError):
        tasks_store.create_task({"name": "x"})
    with pytest.raises(board_store.BoardLoadError):
        board_store.create_task("x", importance=5)
    assert tasks_path.read_text(encoding="utf-8") == content
    assert board_path.read_text(encoding="utf-8") == content


def test_empty_object_still_means_no_tasks(paths):
    for path in paths:
        path.write_text("{}", encoding="utf-8")
    assert tasks_store.load_tasks(include_deleted=True) == []
    assert tasks_store.create_task({"name": "first"})["name"] == "first"
    assert board_store.create_task("first", importance=3)["name"] == "first"
