"""Tests for enforcer.py's sweep_minimize_blocked_windows() -- the
hard-lock pass that minimizes every visible blocklisted window each poll
tick, independent of which window happens to be in the foreground."""
import enforcer
import session_manager


class _FakeProcess:
    def __init__(self, name):
        self._name = name

    def name(self):
        return self._name


def _patch_single_window(monkeypatch, hwnd, pid, process_name, visible=True, iconic=False, title="Some Window"):
    """Makes enforcer.sweep_minimize_blocked_windows() see exactly one
    visible window (hwnd/pid/process_name) via EnumWindows."""
    def fake_enum_windows(callback, extra):
        callback(hwnd, extra)

    monkeypatch.setattr(enforcer.win32gui, "EnumWindows", fake_enum_windows)
    monkeypatch.setattr(enforcer.win32gui, "IsWindowVisible", lambda h: visible)
    monkeypatch.setattr(enforcer.win32gui, "IsIconic", lambda h: iconic)
    monkeypatch.setattr(enforcer.win32gui, "GetWindowText", lambda h: title)
    monkeypatch.setattr(enforcer.win32process, "GetWindowThreadProcessId", lambda h: (0, pid))
    monkeypatch.setattr(enforcer.psutil, "Process", lambda p: _FakeProcess(process_name))


def test_sweep_minimizes_blocked_background_window(isolate_state, monkeypatch):
    session_manager.start_session(25, "hard", ["discord.exe"], [])
    minimize_calls = []
    _patch_single_window(monkeypatch, hwnd=555, pid=4242, process_name="discord.exe")
    monkeypatch.setattr(enforcer.win32gui, "ShowWindow", lambda h, cmd: minimize_calls.append((h, cmd)))

    swept = enforcer.sweep_minimize_blocked_windows()

    assert minimize_calls == [(555, enforcer.win32con.SW_MINIMIZE)]
    assert swept == ["discord.exe"]


def test_sweep_leaves_unblocked_app_alone_after_unblock(isolate_state, monkeypatch):
    """Regression test for "unblocking via the popup doesn't actually let
    the app through": once remove_process_from_blocklist() takes an app off
    processBlocklist, the sweep must stop touching its window on every
    subsequent tick -- not just skip it once."""
    session_manager.start_session(25, "hard", ["discord.exe"], [])
    minimize_calls = []
    _patch_single_window(monkeypatch, hwnd=555, pid=4242, process_name="discord.exe")
    monkeypatch.setattr(enforcer.win32gui, "ShowWindow", lambda h, cmd: minimize_calls.append((h, cmd)))

    enforcer.sweep_minimize_blocked_windows()
    assert len(minimize_calls) == 1

    _, exception_entry = session_manager.remove_process_from_blocklist("discord.exe", "need it for reference")
    assert exception_entry is not None

    enforcer.sweep_minimize_blocked_windows()
    enforcer.sweep_minimize_blocked_windows()
    assert len(minimize_calls) == 1


def test_sweep_skips_already_minimized_window(isolate_state, monkeypatch):
    session_manager.start_session(25, "hard", ["discord.exe"], [])
    minimize_calls = []
    _patch_single_window(monkeypatch, hwnd=555, pid=4242, process_name="discord.exe", iconic=True)
    monkeypatch.setattr(enforcer.win32gui, "ShowWindow", lambda h, cmd: minimize_calls.append((h, cmd)))

    enforcer.sweep_minimize_blocked_windows()

    assert minimize_calls == []


def test_sweep_skips_exempt_process(isolate_state, monkeypatch):
    session_manager.start_session(25, "hard", ["discord.exe"], [])
    minimize_calls = []
    _patch_single_window(monkeypatch, hwnd=555, pid=4242, process_name="explorer.exe")
    monkeypatch.setattr(enforcer.win32gui, "ShowWindow", lambda h, cmd: minimize_calls.append((h, cmd)))

    enforcer.sweep_minimize_blocked_windows()

    assert minimize_calls == []
