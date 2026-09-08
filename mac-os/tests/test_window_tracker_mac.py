"""Tests for window_tracker_mac.py's window-enumeration primitives, run
against stubbed AppKit/Quartz (see conftest.py's mac_world fixture -- no
pyobjc on this Windows machine)."""
import importlib

import psutil


class _FakeProcess:
    def __init__(self, name):
        self._name = name

    def name(self):
        return self._name


def _import_fresh(monkeypatch):
    import mac_os.window_tracker_mac as window_tracker_mac
    importlib.reload(window_tracker_mac)
    return window_tracker_mac


def test_get_active_window_returns_frontmost_app_details(mac_world, monkeypatch):
    window_tracker_mac = _import_fresh(monkeypatch)
    mac_world.frontmost_pid = 4242
    mac_world.on_screen_windows = [
        {"kCGWindowOwnerPID": 4242, "kCGWindowName": "My Doc", "kCGWindowLayer": 0},
    ]
    monkeypatch.setattr(psutil, "Process", lambda pid: _FakeProcess("TextEdit"))

    window = window_tracker_mac.get_active_window()

    assert window == {"title": "My Doc", "process_name": "TextEdit", "pid": 4242, "hwnd": 4242}


def test_get_active_window_with_no_frontmost_app(mac_world, monkeypatch):
    window_tracker_mac = _import_fresh(monkeypatch)
    mac_world.frontmost_pid = None

    window = window_tracker_mac.get_active_window()

    assert window == {"title": None, "process_name": None, "pid": None, "hwnd": None}


def test_get_active_window_ignores_windows_at_other_layers(mac_world, monkeypatch):
    """kCGWindowLayer == 0 is the real on-screen top-level window layer --
    a status-bar/overlay item (nonzero layer) for the same pid must not be
    picked up as the window title."""
    window_tracker_mac = _import_fresh(monkeypatch)
    mac_world.frontmost_pid = 99
    mac_world.on_screen_windows = [
        {"kCGWindowOwnerPID": 99, "kCGWindowName": "Menu Extra", "kCGWindowLayer": 25},
        {"kCGWindowOwnerPID": 99, "kCGWindowName": "Real Window", "kCGWindowLayer": 0},
    ]
    monkeypatch.setattr(psutil, "Process", lambda pid: _FakeProcess("Finder"))

    window = window_tracker_mac.get_active_window()

    assert window["title"] == "Real Window"


def test_list_running_apps_dedupes_by_process_name(mac_world, monkeypatch):
    window_tracker_mac = _import_fresh(monkeypatch)
    mac_world.on_screen_windows = [
        {"kCGWindowOwnerPID": 1, "kCGWindowName": "Window A", "kCGWindowLayer": 0},
        {"kCGWindowOwnerPID": 2, "kCGWindowName": "Window B1", "kCGWindowLayer": 0},
        {"kCGWindowOwnerPID": 3, "kCGWindowName": "Window B2", "kCGWindowLayer": 0},
    ]
    names = {1: "AppA", 2: "AppB", 3: "AppB"}
    monkeypatch.setattr(psutil, "Process", lambda pid: _FakeProcess(names[pid]))

    apps = window_tracker_mac.list_running_apps()

    keys = sorted(a["process_name"] for a in apps)
    assert keys == ["AppA", "AppB"]


def test_list_running_apps_skips_windows_with_no_title(mac_world, monkeypatch):
    window_tracker_mac = _import_fresh(monkeypatch)
    mac_world.on_screen_windows = [
        {"kCGWindowOwnerPID": 1, "kCGWindowName": None, "kCGWindowLayer": 0},
        {"kCGWindowOwnerPID": 1, "kCGWindowName": "", "kCGWindowLayer": 0},
    ]
    monkeypatch.setattr(psutil, "Process", lambda pid: _FakeProcess("AppA"))

    apps = window_tracker_mac.list_running_apps()

    assert apps == []


def test_browser_profile_functions_are_windows_only_stub(mac_world, monkeypatch):
    """Subsystem 3 (per-Chrome/Edge-profile blocking) needs a real Mac to
    spike -- both functions must return [] rather than raising or guessing,
    per PORT_SPEC.md's documented fallback."""
    window_tracker_mac = _import_fresh(monkeypatch)

    assert window_tracker_mac.list_browser_profile_windows() == []
    assert window_tracker_mac.list_known_browser_profiles() == []
