"""QuickNote: the bullet list's keys, saving, the desktop right-click entry, the
mini timer click, and the note travelling between computers."""
import json
import types

import pytest
from PySide6.QtCore import Qt, QPointF
from PySide6.QtGui import QMouseEvent
from PySide6.QtTest import QTest

import desktop_menu
import quicknote_launcher
import quicknote_store
import qt_ui.mini_timer as mini_timer
import qt_ui.quicknote as quicknote
import sync_live

from test_sync_client import fake_logged_in  # noqa: F401  (shared fixture)
from test_sync_live_session import device, server  # noqa: F401  (shared fixtures)


@pytest.fixture
def note(qtbot, isolate_state):
    window = quicknote.QuickNoteWindow()
    qtbot.addWidget(window)
    window.show()
    return window


def _type(edit, text):
    QTest.keyClicks(edit, text)


def _press(edit, key):
    QTest.keyClick(edit, key)


# --- the list ---

def test_a_new_note_is_one_empty_bullet(note):
    assert note._list.items() == [""]


def test_enter_adds_a_bullet_below_and_moves_to_it(note):
    first = note._list._edits[0]
    _type(first, "buy milk")
    _press(first, Qt.Key_Return)
    assert note._list.items() == ["buy milk", ""]
    assert note._list._edits[1].hasFocus()
    _type(note._list._edits[1], "call mom")
    assert note._list.items() == ["buy milk", "call mom"]


def test_enter_in_the_middle_splits_the_bullet_at_the_cursor(note):
    first = note._list._edits[0]
    _type(first, "buy milk and eggs")
    first.setCursorPosition(8)
    _press(first, Qt.Key_Return)
    assert note._list.items() == ["buy milk", " and eggs"]
    assert note._list._edits[1].cursorPosition() == 0


def test_backspace_on_an_empty_bullet_removes_it_and_goes_back_up(note):
    first = note._list._edits[0]
    _type(first, "one")
    _press(first, Qt.Key_Return)
    _press(note._list._edits[1], Qt.Key_Backspace)
    assert note._list.items() == ["one"]
    assert note._list._edits[0].hasFocus()
    assert note._list._edits[0].cursorPosition() == 3


def test_backspace_on_a_bullet_with_text_only_deletes_a_letter(note):
    first = note._list._edits[0]
    _type(first, "abc")
    _press(first, Qt.Key_Return)
    second = note._list._edits[1]
    _type(second, "xy")
    _press(second, Qt.Key_Backspace)
    assert note._list.items() == ["abc", "x"]


def test_the_last_bullet_is_never_removed(note):
    only = note._list._edits[0]
    _press(only, Qt.Key_Backspace)
    _press(only, Qt.Key_Backspace)
    assert note._list.items() == [""]


def test_removing_the_first_of_several_empty_bullets(note):
    first = note._list._edits[0]
    _press(first, Qt.Key_Return)
    _press(note._list._edits[1], Qt.Key_Return)
    assert note._list.items() == ["", "", ""]
    _press(note._list._edits[0], Qt.Key_Backspace)
    assert note._list.items() == ["", ""]


def test_arrows_move_between_bullets(note):
    first = note._list._edits[0]
    _type(first, "a")
    _press(first, Qt.Key_Return)
    _type(note._list._edits[1], "b")
    _press(note._list._edits[1], Qt.Key_Up)
    assert note._list._edits[0].hasFocus()
    _press(note._list._edits[0], Qt.Key_Down)
    assert note._list._edits[1].hasFocus()


# --- saving ---

def test_typing_is_saved_without_pressing_anything(note, qtbot):
    _type(note._list._edits[0], "remember this")
    qtbot.waitUntil(lambda: quicknote_store.load()[0] == ["remember this"], timeout=2000)


def test_closing_saves_at_once_and_reopening_shows_the_same_bullets(note, qtbot, isolate_state):
    first = note._list._edits[0]
    _type(first, "one")
    _press(first, Qt.Key_Return)
    _type(note._list._edits[1], "two")
    note.close()
    assert quicknote_store.load()[0] == ["one", "two"]
    again = quicknote.QuickNoteWindow()
    qtbot.addWidget(again)
    assert again._list.items() == ["one", "two"]


def test_a_change_from_another_computer_shows_up_unless_typing_is_unsaved(note):
    quicknote_store.apply_remote(["from the other computer"], "2099-01-01T00:00:00+00:00")
    note._pick_up_remote_change()
    assert note._list.items() == ["from the other computer"]
    _type(note._list._edits[0], "!")
    quicknote_store.apply_remote(["newer still"], "2099-06-01T00:00:00+00:00")
    note._pick_up_remote_change()                       # "!" has not been saved yet: keep it
    assert note._list.items() == ["from the other computer!"]


# --- the store ---

def test_the_store_limits_and_refuses_junk(isolate_state):
    assert quicknote_store.save(["a"] * 600) is True
    assert len(quicknote_store.load()[0]) == quicknote_store.MAX_ITEMS
    assert quicknote_store.save("not a list") is False and quicknote_store.save([1, 2]) is False
    quicknote_store.save(["x" * 5000])
    assert len(quicknote_store.load()[0][0]) == quicknote_store.MAX_ITEM_CHARS


def test_an_unchanged_note_is_not_re_saved(isolate_state):
    quicknote_store.save(["same"])
    stamp = quicknote_store.export()["updatedAt"]
    assert quicknote_store.save(["same"]) is False
    assert quicknote_store.export()["updatedAt"] == stamp
    assert quicknote_store.save([""]) is True and quicknote_store.save([]) is False   # empty == one empty bullet


def test_an_older_remote_note_does_not_replace_ours(isolate_state):
    quicknote_store.save(["ours"])
    assert quicknote_store.apply_remote(["old"], "2000-01-01T00:00:00+00:00") is False
    assert quicknote_store.load()[0] == ["ours"]


# --- the desktop right-click entry ---

class _FakeRegistry:
    HKEY_CURRENT_USER = "HKCU"
    KEY_READ, KEY_SET_VALUE, REG_SZ = 1, 2, 1

    def __init__(self):
        self.values = {}

    class _Key:
        def __init__(self, registry, path):
            self.registry, self.path = registry, path

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    def OpenKey(self, root, path, reserved=0, access=0):
        if not any(k[0] == path for k in self.values):
            raise FileNotFoundError(path)
        return self._Key(self, path)

    def CreateKeyEx(self, root, path, reserved=0, access=0):
        return self._Key(self, path)

    def QueryValueEx(self, key, name):
        if (key.path, name) not in self.values:
            raise FileNotFoundError(name)
        return self.values[(key.path, name)], self.REG_SZ

    def SetValueEx(self, key, name, reserved, kind, value):
        self.values[(key.path, name)] = value

    def DeleteKey(self, root, path):
        for k in [k for k in self.values if k[0] == path]:
            del self.values[k]


def test_the_desktop_menu_entry_is_created_once_and_repaired_if_stale():
    registry = _FakeRegistry()
    assert desktop_menu.ensure_registered(registry) is True
    label, icon, command = desktop_menu.desired_entry()
    assert registry.values[(desktop_menu.MENU_KEY, "")] == label == "QuickNote (Carmen Focus)"
    assert registry.values[(desktop_menu.MENU_KEY + r"\command", "")] == command
    assert "quicknote_launcher.py" in command
    assert desktop_menu.ensure_registered(registry) is False          # already right: untouched
    registry.values[(desktop_menu.MENU_KEY + r"\command", "")] = r'"C:\old\place\pythonw.exe" "C:\old\x.py"'
    assert desktop_menu.ensure_registered(registry) is True           # the repo moved: fixed
    assert registry.values[(desktop_menu.MENU_KEY + r"\command", "")] == command
    desktop_menu.remove(registry)
    assert not registry.values


def test_the_desktop_menu_never_raises_when_the_registry_refuses():
    class Broken(_FakeRegistry):
        def CreateKeyEx(self, *a, **k):
            raise PermissionError("no")
    assert desktop_menu.ensure_registered(Broken()) is False


def test_the_launcher_asks_the_running_app_and_only_starts_one_if_none_answers(monkeypatch):
    calls = []

    class Reply:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    monkeypatch.setattr(quicknote_launcher.urllib.request, "urlopen", lambda req, timeout: (calls.append(req), Reply())[1])
    started = []
    monkeypatch.setattr(quicknote_launcher, "start_app_with_note", lambda: started.append(1))
    quicknote_launcher.main()
    assert started == [] and calls[0].full_url.endswith("/quicknote/open") and "X-carmen-token" in calls[0].headers

    def refuse(req, timeout):
        raise quicknote_launcher.urllib.error.URLError("refused")
    monkeypatch.setattr(quicknote_launcher.urllib.request, "urlopen", refuse)
    quicknote_launcher.main()
    assert started == [1]


def test_the_open_route_needs_the_token_and_opens_the_note(isolate_state, monkeypatch):
    import api_server
    import config
    import qt_gui_thread
    opened = []
    monkeypatch.setattr(qt_gui_thread, "run_on_gui_thread", lambda fn: opened.append(fn))
    api_server.app.config["TESTING"] = True
    client = api_server.app.test_client()
    assert client.post("/quicknote/open").status_code == 401 and opened == []
    client.environ_base["HTTP_X_CARMEN_TOKEN"] = config.get_api_token()
    assert client.post("/quicknote/open").status_code == 200
    assert opened == [quicknote.open_quicknote]


# --- the mini timer ---

def _mouse(widget, kind, pos, buttons=Qt.LeftButton):
    local = QPointF(pos)
    event = QMouseEvent(kind, local, widget.mapToGlobal(local), Qt.LeftButton, buttons, Qt.NoModifier)
    return event


@pytest.fixture
def timer_window(qtbot, isolate_state, monkeypatch):
    opened = []
    monkeypatch.setattr(quicknote, "open_quicknote", lambda: opened.append(1))
    window = mini_timer.MiniTimer()
    qtbot.addWidget(window)
    window.resize(240, 270)
    window.show()
    window.opened = opened
    return window


def _click(window, point):
    window.mousePressEvent(_mouse(window, QMouseEvent.Type.MouseButtonPress, point))
    window.mouseReleaseEvent(_mouse(window, QMouseEvent.Type.MouseButtonRelease, point, Qt.NoButton))


def test_clicking_the_time_or_the_ring_opens_quicknote(timer_window):
    centre = timer_window._clock_rect().center()
    _click(timer_window, centre)
    ring_edge = timer_window._clock_rect().topLeft() + QPointF(8, 8)
    _click(timer_window, ring_edge)
    assert timer_window.opened == [1, 1]


def test_clicking_elsewhere_does_not(timer_window):
    _click(timer_window, QPointF(120, 14))        # the label strip
    assert timer_window.opened == []


def test_dragging_the_window_by_the_clock_does_not_open_quicknote(timer_window):
    start = timer_window._clock_rect().center()
    timer_window.mousePressEvent(_mouse(timer_window, QMouseEvent.Type.MouseButtonPress, start))
    timer_window.mouseMoveEvent(_mouse(timer_window, QMouseEvent.Type.MouseMove, start + QPointF(40, 30)))
    timer_window.mouseReleaseEvent(_mouse(timer_window, QMouseEvent.Type.MouseButtonRelease, start + QPointF(40, 30), Qt.NoButton))
    assert timer_window.opened == []


def test_the_pause_button_still_works_and_does_not_open_quicknote(timer_window, qtbot):
    import session_manager as sm
    sm.start_session(25, "soft", [], [])
    timer_window.refresh()
    qtbot.mouseClick(timer_window._button, Qt.LeftButton)
    assert sm.get_status()["isPaused"] and timer_window.opened == []


# --- between computers ---

def test_a_note_written_on_one_computer_appears_on_the_other(device, fake_logged_in, server):
    device("a")
    quicknote_store.save(["milk", "eggs"])
    sync_live.tick()
    device("b")
    sync_live.tick()
    assert quicknote_store.load()[0] == ["milk", "eggs"]
    quicknote_store.save(["milk", "eggs", "bread"])
    sync_live.tick()
    device("a")
    sync_live.tick()
    assert quicknote_store.load()[0] == ["milk", "eggs", "bread"]


def test_a_damaged_shared_note_changes_nothing(device, fake_logged_in, server):
    from datetime import datetime
    server[("quicknote", "note")] = {
        "table_name": "quicknote", "sync_id": "note", "device_id": "x", "is_deleted": False,
        "updated_at": datetime.now().astimezone().isoformat(), "data": {"items": [1, {"a": 2}]},
    }
    device("a")
    quicknote_store.save(["mine"])
    assert sync_live.tick() is None
    assert quicknote_store.load()[0] == ["mine"]


def test_reloading_the_note_leaves_no_stale_rows_behind(note, qtbot):
    note._list.set_items(["a", "b", "c"])
    note._list.set_items(["a", "b", "c"])
    qtbot.wait(50)
    assert note._list._layout.count() == 4            # three rows + the stretch
    assert note._list.items() == ["a", "b", "c"]
