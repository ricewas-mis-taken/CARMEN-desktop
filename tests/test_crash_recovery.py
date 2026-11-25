"""The Qt crash on a monitor change: the stack recorder, the screen guard, and coming back after a crash."""
import ctypes
import subprocess
import sys

import pytest
from PySide6.QtWidgets import QApplication

import native_crash
import qt_ui.main_window as mw
import qt_ui.mini_timer as mini_timer
import screen_guard
import supervisor
import window_memory

windows_only = pytest.mark.skipif(sys.platform != "win32", reason="Windows only")


@windows_only
def test_a_crash_leaves_its_native_stack_in_the_log(tmp_path):
    log = tmp_path / "native.log"
    code = (
        "import sys, ctypes\n"
        "sys.path.insert(0, %r)\n"
        "import native_crash\n"
        "assert native_crash.install(%r)\n"
        "ctypes.string_at(0)\n"
    ) % (str(__import__("pathlib").Path(native_crash.__file__).parent), str(log))
    result = subprocess.run([sys.executable, "-c", code], capture_output=True, timeout=60)
    assert result.returncode != 0                                   # it still crashes: the recorder only looks
    text = log.read_text(encoding="utf-8")
    assert "access violation" in text and "reading" in text and "at 0x0" in text
    assert "#0 " in text and "+0x" in text                          # frames as module+offset


@windows_only
def test_describe_names_the_module_and_offset_of_a_code_address():
    address = ctypes.cast(ctypes.windll.kernel32.GetTickCount, ctypes.c_void_p).value
    text = native_crash.describe(address)
    assert "+0x" in text and text.lower().split("+")[0].endswith(".dll")
    assert native_crash.describe(0).startswith("0x")                # no module owns it: the bare address


def test_the_screens_count_as_settling_for_a_few_seconds(monkeypatch):
    now = {"t": 100.0}
    monkeypatch.setattr(screen_guard, "_clock", lambda: now["t"])
    monkeypatch.setitem(screen_guard._state, "last", None)
    assert not screen_guard.settling()
    screen_guard.note_change("a screen was removed")
    assert screen_guard.settling()
    now["t"] += screen_guard.SETTLE_SECONDS + 0.1
    assert not screen_guard.settling()


def test_a_screen_added_or_removed_is_noticed(qtbot, monkeypatch):
    app = QApplication.instance()
    monkeypatch.setattr(screen_guard, "_snapshot", lambda: None)
    monkeypatch.setitem(screen_guard._state, "last", None)
    before = screen_guard.changes()
    screen_guard.install(app)
    app.screenRemoved.emit(app.primaryScreen())
    assert screen_guard.changes() == before + 1 and screen_guard.settling()
    app.screenAdded.emit(app.primaryScreen())
    assert screen_guard.changes() == before + 2


def test_the_mini_timer_leaves_its_window_alone_while_monitors_change(qtbot, isolate_state, monkeypatch):
    win = mini_timer.MiniTimer()
    qtbot.addWidget(win)
    win.show()
    qtbot.waitExposed(win)
    win.hide()
    monkeypatch.setattr(screen_guard, "settling", lambda: True)
    win._keep_in_view()
    assert not win.isVisible()                                      # not touched during the change
    monkeypatch.setattr(screen_guard, "settling", lambda: False)
    win._keep_in_view()
    assert win.isVisible()                                          # and brought back once it calms down


def test_window_state_is_saved_only_when_it_changes(tmp_path, monkeypatch):
    monkeypatch.setattr(window_memory, "PATH", str(tmp_path / "window_state.json"))
    monkeypatch.setitem(window_memory._last, "state", None)
    state = {"main": {"open": True, "page": "review"}, "mini": True}
    assert window_memory.save_if_changed(state) is True
    assert window_memory.save_if_changed(dict(state)) is False
    assert window_memory.load() == state
    assert window_memory.save_if_changed({"main": {"open": False, "page": "home"}, "mini": False}) is True


def test_an_unreadable_window_state_is_just_empty(tmp_path, monkeypatch):
    path = tmp_path / "window_state.json"
    monkeypatch.setattr(window_memory, "PATH", str(path))
    assert window_memory.load() == {}
    path.write_text("{not json", encoding="utf-8")
    assert window_memory.load() == {}
    path.write_text("[1, 2]", encoding="utf-8")
    assert window_memory.load() == {}


def test_after_a_crash_the_open_windows_come_back(monkeypatch):
    opened = []
    monkeypatch.setattr(mw, "open_main_window", lambda: opened.append("main"))
    monkeypatch.setattr(mini_timer, "open_mini_timer", lambda: opened.append("mini"))
    mw.reopen_after_crash({"main": {"open": True, "page": "review"}, "mini": True})
    assert opened == ["main", "mini"]
    opened.clear()
    mw.reopen_after_crash({"main": {"open": False, "page": "home"}, "mini": False})
    mw.reopen_after_crash({})
    assert opened == []                                             # what was closed stays closed


def test_the_supervisor_restarts_after_a_crash_with_the_windows_flag(monkeypatch):
    launches = []
    codes = iter([3221226525, 75, 0])

    class FakeProcess:
        def __init__(self, command, cwd=None, env=None):
            launches.append(dict(env))

        def wait(self):
            return next(codes)

    monkeypatch.setattr(supervisor.subprocess, "Popen", FakeProcess)
    monkeypatch.setattr(supervisor.time, "sleep", lambda s: None)
    monkeypatch.setattr(supervisor, "_note", lambda code: None)
    assert supervisor.run() == 0
    assert "CARMEN_RESTARTED" not in launches[0]
    assert launches[1]["CARMEN_RESTARTED"] == "1" and launches[1]["CARMEN_CRASHED"] == "1"    # after the crash
    assert launches[2]["CARMEN_RESTARTED"] == "1" and "CARMEN_CRASHED" not in launches[2]     # after a relaunch
