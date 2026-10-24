"""The Tasks grid fits as many fixed-width cards as the window allows."""
import pytest

import tasks_store
import qt_ui.tasks_tab as tasks_tab


@pytest.fixture
def isolate_tasks(tmp_path, monkeypatch):
    monkeypatch.setattr(tasks_store, "TASKS_PATH", str(tmp_path / "tasks.json"))
    yield


def _make(n):
    for i in range(n):
        tasks_store.create_task({"name": f"Task {i}"})


def _row_cols(tab):
    return sorted({tab._grid.getItemPosition(tab._grid.indexOf(c))[1] for c in tab._card_order})


def test_narrow_window_shows_one_column_without_sideways_scroll(qtbot, isolate_tasks):
    _make(3)
    tab = tasks_tab.TasksTab()
    qtbot.addWidget(tab)
    tab.resize(620, 700)
    tab.show()
    qtbot.wait(50)
    assert _row_cols(tab) == [0]
    assert tab._scroll.horizontalScrollBar().maximum() == 0


def test_wide_window_shows_three_columns(qtbot, isolate_tasks):
    _make(3)
    tab = tasks_tab.TasksTab()
    qtbot.addWidget(tab)
    tab.resize(1700, 700)
    tab.show()
    qtbot.wait(50)
    assert _row_cols(tab) == [0, 1, 2]
