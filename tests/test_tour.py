import pytest
from PySide6.QtWidgets import QPushButton, QWidget

import config
import tasks_store
from qt_ui import tour, tour_content, tour_runner


class FakeWindow(QWidget):
    """Just enough of the main window for the tour: pages, nav buttons, a sidebar and _show_tab."""

    def __init__(self):
        super().__init__()
        self.resize(900, 600)
        self._sidebar = QWidget(self)
        self._sidebar.setGeometry(0, 0, 200, 600)
        self._nav_buttons, self._pages, self.shown = {}, {}, []
        for i, key in enumerate(("home", "focus", "tasks", "board", "review", "scribe", "screentime", "history", "openings")):
            button = QPushButton(key, self._sidebar)
            button.setGeometry(10, 10 + i * 40, 150, 30)
            self._nav_buttons[key] = button
            self._pages[key] = QWidget(self)
            self._pages[key].setGeometry(200, 0, 700, 600)
        self._pages["tasks"]._add_button = QPushButton("Add", self._pages["tasks"])
        self._pages["tasks"]._add_button.setGeometry(500, 10, 120, 30)
        self._pages["review"]._week_button = QPushButton("Week", self._pages["review"])
        self._account_area = QWidget(self)
        self._account_area._options_button = QPushButton("Options", self._account_area)

    def _show_tab(self, key):
        self.shown.append(key)


@pytest.fixture(autouse=True)
def _no_note_left_on_screen():
    yield
    from PySide6.QtWidgets import QApplication
    for w in QApplication.topLevelWidgets():
        if w.objectName() == "TourCard":
            w.hide()
    tour_runner._tour = None


@pytest.fixture
def window(qtbot, isolate_config):
    w = FakeWindow()
    qtbot.addWidget(w)
    w.show()
    qtbot.waitExposed(w)
    return w


def _tour(window, ids=None):
    chapters = tour_content.chapters(window)
    if ids:
        chapters = [c for c in chapters if c.id in ids]
    done = []
    t = tour.Tour(window, chapters, on_finished=lambda ok: done.append(ok))
    t.done = done
    return t


def test_next_and_back_walk_every_step_in_order(window):
    t = _tour(window)
    t.start()
    seen = []
    while t.active:
        seen.append((t.chapter, t.index))
        t.next()
    total = sum(len(c.steps) for c in t.chapters)
    assert len(seen) == total and seen[0] == (0, 0) and t.done == [True]
    t.start()
    t.next()
    t.back()
    assert (t.chapter, t.index) == (0, 0)


def test_skip_this_part_jumps_to_the_next_chapter_and_skip_tour_leaves(window):
    t = _tour(window)
    t.start()
    t.skip_chapter()
    assert (t.chapter, t.index) == (1, 0)
    t.finish(completed=False)
    assert not t.active and t.done == [False] and not t.dim.isVisible() and not t.card.isVisible()


def test_skipping_the_last_chapter_finishes(window):
    t = _tour(window)
    t.start()
    t.chapter, t.index = len(t.chapters) - 1, 0
    t.skip_chapter()
    assert not t.active and t.done == [True]


def test_a_step_that_opens_a_page_shows_it_and_the_bright_part_follows_its_target(window):
    t = _tour(window, ["around"])
    t.start()
    t.next()                                                  # Home
    t.next()                                                  # Focus
    t.next()                                                  # Tasks
    assert "tasks" in window.shown
    rect = t._target_rect()
    button = window._nav_buttons["tasks"]
    assert rect is not None and rect.contains(button.geometry().center() + window._sidebar.pos())


def test_the_bright_part_is_click_through_and_the_rest_is_not(window):
    t = _tour(window, ["around"])
    t.start()
    t.next(); t.next(); t.next()
    t._place()
    hole = t._target_rect()
    assert not t.dim.mask().contains(hole.center())           # clicks in the hole reach the button
    assert t.dim.mask().contains(window.rect().bottomRight() - hole.center().__class__(5, 5))


def test_the_first_task_chapter_moves_on_by_itself_when_the_task_is_saved(window, tmp_path, monkeypatch):
    monkeypatch.setattr(tasks_store, "TASKS_PATH", str(tmp_path / "tasks.json"))
    t = _tour(window, ["task"])
    t.start()
    t.next()                                                  # now waiting for the task to be saved
    assert t.step.free and t.step.wait_for is not None
    assert not t.step.wait_for()
    tasks_store.create_task({"name": "Reading", "color": "#222222"})
    assert t.step.wait_for()
    t._check_wait()
    assert t.index == 2


def test_the_note_stays_inside_the_screen_and_says_where_you_are(window):
    t = _tour(window)
    t.start()
    t.chapter, t.index = 2, 0
    t._show_step()
    screen = t.card.screen().availableGeometry()
    assert screen.contains(t.card.geometry())
    assert "of" in t.card._count.text() and t.card._skip_part.isVisible() is not None


def test_finishing_or_skipping_marks_the_tour_done_so_it_does_not_come_back(window):
    tour_runner.register(window)
    assert config.load_config().get("tourDone") is False
    assert tour_runner.start_if_new() is not None
    tour_runner._tour.finish(completed=False)
    assert config.load_config()["tourDone"] is True
    assert tour_runner.start_if_new() is None


def test_help_lists_every_chapter_and_can_play_just_one(window):
    tour_runner.register(window)
    ids = [c[0] for c in tour_runner.chapter_list()]
    assert ids[0] == "welcome" and "task" in ids and "extension" in ids
    t = tour_runner.start(["review"])
    assert [c.id for c in t.chapters] == ["review"]
    t.finish(completed=True)


def test_the_options_dialog_has_a_help_section_that_starts_the_tour(qtbot, window, monkeypatch):
    from qt_ui.options_dialog import OptionsDialog
    tour_runner.register(window)
    started = []
    monkeypatch.setattr(tour_runner, "start", lambda ids=None: started.append(ids))
    dialog = OptionsDialog()
    qtbot.addWidget(dialog)
    chips = [b.text() for b in dialog.findChildren(QPushButton)]
    assert "Play the whole tour" in chips and "Your first task" in chips
    next(b for b in dialog.findChildren(QPushButton) if b.text() == "Your first task").click()
    qtbot.waitUntil(lambda: started == [["task"]], timeout=2000)
