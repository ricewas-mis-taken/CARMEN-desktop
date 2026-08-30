"""Tests for blocking a single Chrome/Edge profile by its per-window
AppUserModelID (see enforcer.get_window_aumi) instead of blocking the whole
browser via processBlocklist -- needed because every profile of the same
browser shares one OS process (confirmed empirically: two profile windows
launched on the same machine came back with the same PID), so a plain
process-name check can't tell them apart."""
import enforcer
import session_manager
import window_tracker


def test_describe_browser_profile_aumi_uses_the_real_profile_name(monkeypatch):
    """The label should be the name Chrome itself shows in its profile
    switcher (from Local State), not the raw folder name the AUMI encodes --
    "Profile4" on its own tells the user nothing about which profile that
    actually is."""
    monkeypatch.setattr(
        enforcer, "_read_profile_display_names",
        lambda process_name: {"default": "Rice", "profile4": "Lucas (Person 1)"},
    )
    assert enforcer.describe_browser_profile_aumi("chrome.exe", "Chrome") == "chrome.exe — Rice"
    assert (
        enforcer.describe_browser_profile_aumi("chrome.exe", "Chrome.UserData.Profile4")
        == "chrome.exe — Lucas (Person 1)"
    )


def test_describe_browser_profile_aumi_falls_back_when_name_lookup_fails(monkeypatch):
    """Local State might not exist, be unreadable, or simply not have an
    entry for this folder (e.g. read mid-write by the browser itself) --
    the raw folder name/"Default" is still a usable label, just less
    friendly, and must never be replaced with nothing."""
    monkeypatch.setattr(enforcer, "_read_profile_display_names", lambda process_name: {})
    assert enforcer.describe_browser_profile_aumi("chrome.exe", "Chrome") == "chrome.exe — Default"
    assert enforcer.describe_browser_profile_aumi("chrome.exe", "Chrome.UserData.Profile4") == "chrome.exe — Profile4"


def test_describe_browser_profile_aumi_falls_back_for_unrecognized_shape():
    assert enforcer.describe_browser_profile_aumi("chrome.exe", "SomethingElse") == "chrome.exe — SomethingElse"


def test_describe_browser_profile_aumi_no_aumi_returns_process_name():
    assert enforcer.describe_browser_profile_aumi("chrome.exe", None) == "chrome.exe"


def test_read_profile_display_names_parses_local_state(monkeypatch, tmp_path):
    """End-to-end through the real file-reading/JSON-parsing/normalizing
    path, using a Local State shaped like Chrome's actual file (folder names
    with spaces, shortcut_name preferred over the more generic name)."""
    data_dir = tmp_path / "Chrome User Data"
    data_dir.mkdir()
    (data_dir / "Local State").write_text(
        '{"profile": {"info_cache": {'
        '"Default": {"name": "Person 1", "shortcut_name": "Rice"}, '
        '"Profile 2": {"name": "Lucas"}, '
        '"Profile 4": {"name": "Person 1", "shortcut_name": "Lucas (Person 1)"}'
        "}}}",
        encoding="utf-8",
    )
    monkeypatch.setitem(enforcer._PROFILE_DATA_DIRS, "chrome.exe", str(data_dir))

    names = enforcer._read_profile_display_names("chrome.exe")

    assert names == {"default": "Rice", "profile2": "Lucas", "profile4": "Lucas (Person 1)"}


def test_read_profile_display_names_returns_empty_for_missing_file(monkeypatch, tmp_path):
    monkeypatch.setitem(enforcer._PROFILE_DATA_DIRS, "chrome.exe", str(tmp_path / "does-not-exist"))
    assert enforcer._read_profile_display_names("chrome.exe") == {}


def test_read_profile_display_names_returns_empty_for_unknown_process():
    assert enforcer._read_profile_display_names("notepad.exe") == {}


def test_is_blocked_window_true_for_plain_process_blocklist(isolate_state):
    session_manager.start_session(25, "hard", ["discord.exe"], [])
    assert enforcer.is_blocked_window("discord.exe", 555)


def test_is_blocked_window_false_for_unblocked_non_browser(isolate_state):
    session_manager.start_session(25, "hard", ["discord.exe"], [])
    assert not enforcer.is_blocked_window("notepad.exe", 555)


def test_is_blocked_window_true_for_blocked_chrome_profile(isolate_state, monkeypatch):
    session_manager.start_session(
        25, "hard", [], [], blocked_browser_profiles=["Chrome.UserData.Profile4"]
    )
    monkeypatch.setattr(enforcer, "get_window_aumi", lambda hwnd: "Chrome.UserData.Profile4")
    assert enforcer.is_blocked_window("chrome.exe", 999)


def test_is_blocked_window_false_for_a_different_chrome_profile(isolate_state, monkeypatch):
    session_manager.start_session(
        25, "hard", [], [], blocked_browser_profiles=["Chrome.UserData.Profile4"]
    )
    monkeypatch.setattr(enforcer, "get_window_aumi", lambda hwnd: "Chrome")
    assert not enforcer.is_blocked_window("chrome.exe", 999)


def test_is_blocked_window_ignores_aumi_lookup_for_non_browser_process(isolate_state, monkeypatch):
    """A non-browser process must never trigger the (comparatively
    expensive, COM-based) AUMI lookup at all."""
    session_manager.start_session(25, "hard", [], [], blocked_browser_profiles=["Chrome"])

    def _boom(hwnd):
        raise AssertionError("get_window_aumi should not be called for a non-browser process")

    monkeypatch.setattr(enforcer, "get_window_aumi", _boom)
    assert not enforcer.is_blocked_window("notepad.exe", 555)


def test_list_browser_profile_windows_returns_one_entry_per_unique_aumi(monkeypatch):
    windows = [
        (111, 5000, "chrome.exe", "Tab A - Google Chrome", "Chrome"),
        (222, 5000, "chrome.exe", "Tab B - Google Chrome", "Chrome.UserData.Profile4"),
        # Same AUMI as the first window (e.g. two tabs in the same profile
        # window, or a second window of the same profile) -- must not
        # produce a duplicate entry.
        (333, 5000, "chrome.exe", "Tab C - Google Chrome", "Chrome"),
        # A non-browser window must be skipped entirely.
        (444, 6000, "notepad.exe", "note.txt - Notepad", None),
    ]

    class _FakeProcess:
        def __init__(self, name):
            self._name = name

        def name(self):
            return self._name

    def fake_enum_windows(callback, extra):
        for hwnd, pid, name, title, _aumi in windows:
            callback(hwnd, extra)

    def fake_get_thread_process_id(hwnd):
        for h, pid, name, title, _aumi in windows:
            if h == hwnd:
                return (0, pid)
        raise ValueError("unknown hwnd")

    def fake_process(pid):
        for h, p, name, title, _aumi in windows:
            if p == pid:
                return _FakeProcess(name)
        raise ValueError("unknown pid")

    def fake_get_window_text(hwnd):
        for h, pid, name, title, _aumi in windows:
            if h == hwnd:
                return title
        return ""

    def fake_get_window_aumi(hwnd):
        for h, pid, name, title, aumi in windows:
            if h == hwnd:
                return aumi
        return None

    monkeypatch.setattr(window_tracker.win32gui, "EnumWindows", fake_enum_windows)
    monkeypatch.setattr(window_tracker.win32gui, "IsWindowVisible", lambda h: True)
    monkeypatch.setattr(window_tracker.win32gui, "GetWindowText", fake_get_window_text)
    monkeypatch.setattr(window_tracker.win32process, "GetWindowThreadProcessId", fake_get_thread_process_id)
    monkeypatch.setattr(window_tracker.psutil, "Process", fake_process)
    monkeypatch.setattr(window_tracker.enforcer, "get_window_aumi", fake_get_window_aumi)

    result = window_tracker.list_browser_profile_windows()

    assert {r["aumi"] for r in result} == {"Chrome", "Chrome.UserData.Profile4"}
    assert all(r["process_name"] == "chrome.exe" for r in result)
