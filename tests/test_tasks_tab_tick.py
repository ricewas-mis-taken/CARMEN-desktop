"""The Tasks tab ticks every second; it should not do the work while hidden, and a
card should only be restyled when its locked state actually changes."""
import pytest
import tasks_store
import qt_ui.tasks_tab as tasks_tab


@pytest.fixture
def isolate_tasks(tmp_path, monkeypatch):
    monkeypatch.setattr(tasks_store, "TASKS_PATH", str(tmp_path / "tasks.json"))
    yield


class _PolishCounter:
    """Stands in for card.style() and counts how often the card is re-styled."""

    def __init__(self, real):
        self._real = real
        self.polishes = 0

    def unpolish(self, widget):
        self._real.unpolish(widget)

    def polish(self, widget):
        self.polishes += 1
        self._real.polish(widget)


def test_timer_does_nothing_while_the_tab_is_hidden(qtbot, isolate_tasks, monkeypatch):
    tasks_store.create_task({"name": "A"})
    tab = tasks_tab.TasksTab()
    qtbot.addWidget(tab)
    ticks = []
    monkeypatch.setattr(tab, "_tick", lambda: ticks.append(1))
    tab._tick_if_visible()
    assert ticks == []
    tab.show()
    assert ticks == [1]            # showing catches up at once
    tab._tick_if_visible()
    assert ticks == [1, 1]


def test_card_is_not_restyled_on_every_tick(qtbot, isolate_tasks):
    tasks_store.create_task({"name": "A"})
    tab = tasks_tab.TasksTab()
    qtbot.addWidget(tab)
    tab.show()
    card = next(iter(tab._cards.values()))
    counter = _PolishCounter(card.style())
    card.style = lambda: counter
    for _ in range(5):
        tab._tick()
    assert counter.polishes == 0
