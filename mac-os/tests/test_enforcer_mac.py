"""Tests for enforcer_mac.py's hard/soft lock actions, run against stubbed
AppKit/Quartz/ApplicationServices (see conftest.py's mac_world fixture --
no pyobjc on this Windows machine). qt_gui_thread.run_on_gui_thread and
enforcer_overlay.build_overlay are monkeypatched to run synchronously and
record calls, rather than needing a real Qt event loop."""
import importlib
import sys

import psutil
import pytest

import session_manager



class _FakeProcess:
    def __init__(self, name):
        self._name = name

    def name(self):
        return self._name


def _import_fresh(monkeypatch):
    import mac_os.enforcer_mac as enforcer_mac
    importlib.reload(enforcer_mac)

    overlay_calls = []
    monkeypatch.setattr(
        enforcer_mac.qt_gui_thread, "run_on_gui_thread", lambda fn: fn()
    )
    monkeypatch.setattr(
        enforcer_mac.enforcer_overlay,
        "build_overlay",
        lambda message, duration_ms, offending_process_name=None, blackout_rect=None: overlay_calls.append(
            (message, duration_ms, offending_process_name, blackout_rect)
        ),
    )
    return enforcer_mac, overlay_calls


def _patch_processes(monkeypatch, names_by_pid):
    monkeypatch.setattr(psutil, "Process", lambda pid: _FakeProcess(names_by_pid[pid]))


@pytest.fixture(autouse=True)
def clear_hidden_pids(mac_world, monkeypatch):
    enforcer_mac, _ = _import_fresh(monkeypatch)
    enforcer_mac._hidden_pids.clear()
    yield
    enforcer_mac._hidden_pids.clear()


def test_is_blocked_window_matches_plain_process_blocklist(mac_world, monkeypatch, isolate_state):
    enforcer_mac, _ = _import_fresh(monkeypatch)
    session_manager.start_session(25, "hard", ["discord.exe"], [])

    assert enforcer_mac.is_blocked_window("discord.exe", 4242) is True
    assert enforcer_mac.is_blocked_window("textedit", 4242) is False


def _fake_chrome(monkeypatch, tmp_path, titles_by_pid):
    from mac_os import browser_profiles_mac as bp
    (tmp_path / "Local State").write_text(
        '{"profile": {"info_cache": {"Default": {"name": "Rice"}, "Profile 2": {"name": "Lucas"}}}}'
    )
    monkeypatch.setitem(bp.BROWSERS["google chrome"], "data_dir", str(tmp_path))
    monkeypatch.setattr(
        bp, "window_title_for",
        lambda pid, focused_only=True: titles_by_pid[pid] if focused_only else [(f"w{i}", t) for i, t in enumerate(titles_by_pid[pid])],
    )


def test_non_browsers_have_no_aumi(mac_world, monkeypatch):
    enforcer_mac, _ = _import_fresh(monkeypatch)
    _patch_processes(monkeypatch, {123: "TextEdit"})

    assert enforcer_mac.get_window_aumi(123) is None
    assert enforcer_mac.list_known_profile_aumis("TextEdit") == []
    assert enforcer_mac.describe_browser_profile_aumi("TextEdit", "anything") == "TextEdit — anything"


def test_chrome_window_aumi_comes_from_its_title(mac_world, monkeypatch, tmp_path):
    enforcer_mac, _ = _import_fresh(monkeypatch)
    _patch_processes(monkeypatch, {50: "Google Chrome"})
    _fake_chrome(monkeypatch, tmp_path, {50: "Example - Google Chrome - Lucas"})

    assert enforcer_mac.get_window_aumi(50) == "Chrome.UserData.Profile2"
    assert enforcer_mac.list_known_profile_aumis("Google Chrome") == ["Chrome", "Chrome.UserData.Profile2"]
    assert enforcer_mac.describe_browser_profile_aumi("Google Chrome", "Chrome.UserData.Profile2") == "Google Chrome — Lucas"


def test_only_the_blocked_profile_is_blocked(mac_world, monkeypatch, isolate_state, tmp_path):
    enforcer_mac, _ = _import_fresh(monkeypatch)
    _patch_processes(monkeypatch, {50: "Google Chrome", 51: "Google Chrome"})
    _fake_chrome(monkeypatch, tmp_path, {
        50: "Example - Google Chrome - Lucas",
        51: "Example - Google Chrome - Rice",
    })
    session_manager.start_session(25, "hard", [], [], blocked_browser_profiles=["Chrome.UserData.Profile2"])

    assert enforcer_mac.is_blocked_window("Google Chrome", 50) is True
    assert enforcer_mac.is_blocked_window("Google Chrome", 51) is False


def test_sweep_minimizes_only_the_blocked_profiles_windows(mac_world, monkeypatch, isolate_state, tmp_path):
    enforcer_mac, _ = _import_fresh(monkeypatch)
    _patch_processes(monkeypatch, {50: "Google Chrome"})
    _fake_chrome(monkeypatch, tmp_path, {50: ["T - Google Chrome - Lucas", "T - Google Chrome - Rice"]})
    session_manager.start_session(25, "hard", [], [], blocked_browser_profiles=["Chrome.UserData.Profile2"])
    mac_world.add_app(50)
    minimized = []
    monkeypatch.setattr(
        enforcer_mac, "AXUIElementSetAttributeValue", lambda w, attr, v: minimized.append(w) or 0
    )
    monkeypatch.setattr(enforcer_mac, "AXUIElementCopyAttributeValue", lambda *a: (0, False))

    assert enforcer_mac._minimize_blocked_profile_windows(50, "Google Chrome") == 1
    assert minimized == ["w0"]


def test_soft_lock_warning_covers_the_focused_windows_rect(mac_world, monkeypatch, isolate_state):
    enforcer_mac, overlay_calls = _import_fresh(monkeypatch)
    session_manager.start_session(25, "soft", ["discord.exe"], [])
    session_manager.record_acceptable("code.exe")
    mac_world.add_app(111, focused_window_rect=(10, 20, 300, 400))

    enforcer_mac.soft_lock_warning("discord.exe", 111)

    assert len(overlay_calls) == 1
    message, duration_ms, offending_process_name, blackout_rect = overlay_calls[0]
    assert offending_process_name == "discord.exe"
    assert blackout_rect == (10, 20, 300, 400)


def test_soft_lock_warning_has_no_cover_without_a_hwnd(mac_world, monkeypatch, isolate_state):
    """No hwnd (pid) means no window to look up a rect for -- must not fall
    back to a full-screen cover, matching hard_lock_redirect's own "no
    blackout at all" behavior."""
    enforcer_mac, overlay_calls = _import_fresh(monkeypatch)
    session_manager.start_session(25, "soft", ["discord.exe"], [])

    enforcer_mac.soft_lock_warning("discord.exe", None)

    assert overlay_calls[0][3] is None


def test_soft_lock_warning_has_no_cover_when_rect_lookup_fails(mac_world, monkeypatch, isolate_state):
    """A pid with no focused-window rect available (permission denied, app
    already gone, etc.) must degrade to no cover, not raise."""
    enforcer_mac, overlay_calls = _import_fresh(monkeypatch)
    session_manager.start_session(25, "soft", ["discord.exe"], [])
    mac_world.add_app(222)  # no focused_window_rect given

    enforcer_mac.soft_lock_warning("discord.exe", 222)

    assert overlay_calls[0][3] is None


def test_hard_lock_redirect_hides_blocked_frontmost_app(
    mac_world, monkeypatch, isolate_state
):
    enforcer_mac, overlay_calls = _import_fresh(monkeypatch)
    session_manager.start_session(25, "hard", ["discord.exe"], [])
    session_manager.record_acceptable("code.exe")

    discord = mac_world.add_app(111, minimized_windows=[False])
    code_app = mac_world.add_app(222, minimized_windows=[True])
    mac_world.frontmost_pid = 111
    _patch_processes(monkeypatch, {111: "discord.exe", 222: "code.exe"})
    monkeypatch.setattr(psutil, "process_iter", lambda attrs=None: [
        type("P", (), {"info": {"pid": 222, "name": "code.exe"}})()
    ])

    enforcer_mac.hard_lock_redirect("discord.exe")

    assert mac_world.windows[111][0].minimized is False
    assert discord.hide_calls == 1
    assert 111 in enforcer_mac._hidden_pids
    assert code_app.activate_calls == 1
    assert overlay_calls, "expected a lock overlay to be shown"
    assert "discord.exe" in overlay_calls[0][2] or "discord.exe" in overlay_calls[0][0]


def test_hard_lock_redirect_does_nothing_when_frontmost_is_exempt(
    mac_world, monkeypatch, isolate_state
):
    enforcer_mac, _ = _import_fresh(monkeypatch)
    session_manager.start_session(25, "hard", ["discord.exe"], [])

    own_app = mac_world.add_app(555, minimized_windows=[False])
    mac_world.frontmost_pid = 555
    monkeypatch.setattr(
        session_manager,
        "is_exempt",
        lambda process_name, pid=None: True,
    )
    _patch_processes(monkeypatch, {555: "some.exe"})

    enforcer_mac.hard_lock_redirect()

    assert mac_world.windows[555][0].minimized is False
    assert own_app.hide_calls == 0


def test_sweep_hides_every_blocked_background_app(mac_world, monkeypatch, isolate_state):
    enforcer_mac, _ = _import_fresh(monkeypatch)
    session_manager.start_session(25, "hard", ["discord.exe"], [])

    discord = mac_world.add_app(111, minimized_windows=[False])
    allowed = mac_world.add_app(222, minimized_windows=[False])
    mac_world.on_screen_windows = [
        {"kCGWindowOwnerPID": 111, "kCGWindowName": "Discord", "kCGWindowLayer": 0},
        {"kCGWindowOwnerPID": 222, "kCGWindowName": "Editor", "kCGWindowLayer": 0},
    ]
    _patch_processes(monkeypatch, {111: "discord.exe", 222: "code.exe"})

    swept = enforcer_mac.sweep_minimize_blocked_windows()

    assert swept == [("discord.exe", 111)]
    assert mac_world.windows[111][0].minimized is False
    assert discord.hide_calls == 1
    assert mac_world.windows[222][0].minimized is False
    assert allowed.hide_calls == 0


def test_sweep_does_not_recount_an_already_hidden_blocked_app(
    mac_world, monkeypatch, isolate_state
):
    """Mirrors enforcer.py's Windows was_iconic check: a blocked window
    that's already fully minimized must not be re-added to the returned
    list on every tick (window_tracker.py would otherwise spam a fresh
    notice for it every poll)."""
    enforcer_mac, _ = _import_fresh(monkeypatch)
    session_manager.start_session(25, "hard", ["discord.exe"], [])

    mac_world.add_app(111).hidden = True
    _patch_processes(monkeypatch, {111: "discord.exe"})

    swept = enforcer_mac.sweep_minimize_blocked_windows()

    assert swept == []


def test_restore_all_taskbar_previews_unhides_every_hidden_pid(mac_world, monkeypatch):
    enforcer_mac, _ = _import_fresh(monkeypatch)
    app_a = mac_world.add_app(1)
    app_b = mac_world.add_app(2)
    enforcer_mac._hide_app(1, True)
    enforcer_mac._hide_app(2, True)

    enforcer_mac.restore_all_taskbar_previews()

    assert app_a.unhide_calls == 1
    assert app_b.unhide_calls == 1
    assert enforcer_mac._hidden_pids == set()


def test_restore_window_for_process_activates_and_unhides(mac_world, monkeypatch):
    enforcer_mac, _ = _import_fresh(monkeypatch)
    app = mac_world.add_app(77)
    enforcer_mac._hide_app(77, True)

    monkeypatch.setattr(psutil, "process_iter", lambda attrs=None: [
        type("P", (), {"info": {"pid": 77, "name": "discord.exe"}})()
    ])

    enforcer_mac.restore_window_for_process("discord.exe")

    assert app.activate_calls == 1
    assert app.unhide_calls == 1
    assert 77 not in enforcer_mac._hidden_pids


def test_show_blocked_notice_shows_overlay_without_redirecting_focus(
    mac_world, monkeypatch, isolate_state
):
    enforcer_mac, overlay_calls = _import_fresh(monkeypatch)
    session_manager.start_session(25, "hard", ["discord.exe"], [])

    enforcer_mac.show_blocked_notice("discord.exe")

    assert len(overlay_calls) == 1
    assert "discord.exe" in overlay_calls[0][0]
    assert overlay_calls[0][2] == "discord.exe"


def test_accessibility_trust_check_reflects_world_state(mac_world, monkeypatch):
    enforcer_mac, _ = _import_fresh(monkeypatch)
    mac_world.ax_trusted = False

    assert enforcer_mac.is_accessibility_trusted() is False

    mac_world.ax_trusted = True
    assert enforcer_mac.is_accessibility_trusted() is True


def test_sweep_finds_blocked_app_that_is_not_in_the_on_screen_list(mac_world, monkeypatch, isolate_state):
    """A blocked app on another Space or full-screen is absent from
    CGWindowList's on-screen list; the sweep must still take it down."""
    enforcer_mac, _ = _import_fresh(monkeypatch)
    session_manager.start_session(25, "hard", ["discord.exe"], [])
    app = mac_world.add_app(111, minimized_windows=[False])
    mac_world.on_screen_windows = []
    _patch_processes(monkeypatch, {111: "Discord"})

    assert enforcer_mac.sweep_minimize_blocked_windows() == [("Discord", 111)]
    assert app.hidden is True


def test_block_matches_the_apps_dock_name_not_just_its_executable(mac_world, monkeypatch, isolate_state):
    enforcer_mac, _ = _import_fresh(monkeypatch)
    session_manager.start_session(25, "hard", ["Steam.exe"], [])
    app = mac_world.add_app(111)
    app.localizedName = lambda: "Steam"
    _patch_processes(monkeypatch, {111: "steam_osx"})

    assert enforcer_mac.is_blocked_window("steam_osx", 111) is True
