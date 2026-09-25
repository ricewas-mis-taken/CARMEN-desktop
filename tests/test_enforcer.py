"""Tests for enforcer.py's sweep_minimize_blocked_windows() -- the
hard-lock pass that minimizes every visible blocklisted window each poll
tick, independent of which window happens to be in the foreground."""
import ctypes
import sys

import pytest

import enforcer
import session_manager

# This file monkeypatches enforcer.win32gui/win32process and enforcer's
# Windows-only _hidden_hwnds directly -- none of that exists in enforcer.py's
# darwin branch (see enforcer_mac.py + mac-os/tests/test_enforcer_mac.py for
# the macOS equivalents of this coverage). Per PORT_SPEC.md's Testing
# section, mark it to skip on macOS rather than deleting/weakening it.
pytestmark = pytest.mark.skipif(sys.platform == "darwin", reason="monkeypatches Windows-only enforcer internals")


@pytest.fixture(autouse=True)
def clear_hidden_hwnds():
    """enforcer._hidden_hwnds is a module-level set mutated across this
    whole process's life -- reset it so one test's hidden hwnd doesn't leak
    into the next, same reasoning as test_enforcer_overlay.py's
    clear_open_overlays fixture."""
    enforcer._hidden_hwnds.clear()
    yield
    enforcer._hidden_hwnds.clear()


@pytest.fixture(autouse=True)
def clear_last_violation_notice():
    """enforcer._last_violation_notice is a module-level dict deduping
    repeated violation-notice overlays by process name and real wall-clock
    time (see _show_violation_notice) -- without resetting it, one test's
    "discord.exe" notice can still be inside the other's cooldown window if
    the whole suite runs fast enough, silently suppressing the very overlay
    call the next test is asserting on."""
    enforcer._last_violation_notice.clear()
    yield
    enforcer._last_violation_notice.clear()


class _FakeProcess:
    def __init__(self, name):
        self._name = name

    def name(self):
        return self._name


def _patch_single_window(
    monkeypatch, hwnd, pid, process_name, visible=True, iconic=False, title="Some Window", owner=0
):
    """Makes enforcer.sweep_minimize_blocked_windows() see exactly one
    visible window (hwnd/pid/process_name) via EnumWindows. owner=0 (the
    default) means "no owner" -- a real top-level window, per GW_OWNER
    semantics; pass a non-zero value to simulate an owned tool/popup window
    the sweep should skip."""
    def fake_enum_windows(callback, extra):
        callback(hwnd, extra)

    monkeypatch.setattr(enforcer.win32gui, "EnumWindows", fake_enum_windows)
    monkeypatch.setattr(enforcer.win32gui, "IsWindowVisible", lambda h: visible)
    monkeypatch.setattr(enforcer.win32gui, "IsIconic", lambda h: iconic)
    monkeypatch.setattr(enforcer.win32gui, "GetWindowText", lambda h: title)
    monkeypatch.setattr(enforcer.win32gui, "GetWindow", lambda h, flag: owner)
    monkeypatch.setattr(enforcer.win32process, "GetWindowThreadProcessId", lambda h: (0, pid))
    monkeypatch.setattr(enforcer.psutil, "Process", lambda p: _FakeProcess(process_name))


def test_sweep_minimizes_blocked_background_window(isolate_state, monkeypatch):
    session_manager.start_session(25, "hard", ["discord.exe"], [])
    minimize_calls = []
    _patch_single_window(monkeypatch, hwnd=555, pid=4242, process_name="discord.exe")
    monkeypatch.setattr(enforcer.win32gui, "ShowWindow", lambda h, cmd: minimize_calls.append((h, cmd)))

    swept = enforcer.sweep_minimize_blocked_windows()

    assert minimize_calls == [(555, enforcer.win32con.SW_MINIMIZE)]
    assert swept == [("discord.exe", 555)]


def test_sweep_still_catches_a_blocked_window_with_a_blank_title(isolate_state, monkeypatch):
    """Regression test for a blocked window with no title text (blanked
    deliberately or just transiently) being invisible to the sweep entirely
    -- it must still be caught as long as it's a real top-level window
    (GW_OWNER == 0), since the sweep no longer gates on title text."""
    session_manager.start_session(25, "hard", ["discord.exe"], [])
    minimize_calls = []
    _patch_single_window(monkeypatch, hwnd=555, pid=4242, process_name="discord.exe", title="")
    monkeypatch.setattr(enforcer.win32gui, "ShowWindow", lambda h, cmd: minimize_calls.append((h, cmd)))

    swept = enforcer.sweep_minimize_blocked_windows()

    assert minimize_calls == [(555, enforcer.win32con.SW_MINIMIZE)]
    assert swept == [("discord.exe", 555)]


def test_sweep_skips_an_owned_tool_window(isolate_state, monkeypatch):
    """A window with an owner (tooltip, dropdown popup, etc.) is not a real
    top-level app window and must still be skipped, same as the old title
    check intended -- just via GW_OWNER instead of title text."""
    session_manager.start_session(25, "hard", ["discord.exe"], [])
    minimize_calls = []
    _patch_single_window(monkeypatch, hwnd=555, pid=4242, process_name="discord.exe", owner=999)
    monkeypatch.setattr(enforcer.win32gui, "ShowWindow", lambda h, cmd: minimize_calls.append((h, cmd)))

    swept = enforcer.sweep_minimize_blocked_windows()

    assert minimize_calls == []
    assert swept == []


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


def test_sweep_skips_reminimizing_already_minimized_window(isolate_state, monkeypatch):
    session_manager.start_session(25, "hard", ["discord.exe"], [])
    minimize_calls = []
    _patch_single_window(monkeypatch, hwnd=555, pid=4242, process_name="discord.exe", iconic=True)
    monkeypatch.setattr(enforcer.win32gui, "ShowWindow", lambda h, cmd: minimize_calls.append((h, cmd)))

    swept = enforcer.sweep_minimize_blocked_windows()

    assert minimize_calls == []
    # Not re-minimized (already iconic) and not reported as newly caught,
    # but see the peek-hiding regression test below -- it must still get
    # that applied.
    assert swept == []


def test_sweep_hides_peek_even_for_an_already_minimized_window(isolate_state, monkeypatch):
    """Regression test: a window that arrives already minimized (started
    minimized, or the user minimized it manually before the sweep ever saw
    it visible) used to never get _hide_taskbar_preview at all -- the old
    early-return bailed on any already-iconic window before reaching that
    call. Only hard_lock_redirect's own independent call (which only fires
    once the window genuinely becomes the foreground window) would ever
    hide it, so hover/Alt+Tab preview stayed live until the user actually
    switched into the window once."""
    session_manager.start_session(25, "hard", ["discord.exe"], [])
    peek_calls = []
    _patch_single_window(monkeypatch, hwnd=555, pid=4242, process_name="discord.exe", iconic=True)
    monkeypatch.setattr(
        enforcer, "_hide_taskbar_preview", lambda hwnd, hide: peek_calls.append((hwnd, hide))
    )

    enforcer.sweep_minimize_blocked_windows()

    assert peek_calls == [(555, True)]


def test_sweep_skips_exempt_process(isolate_state, monkeypatch):
    session_manager.start_session(25, "hard", ["discord.exe"], [])
    minimize_calls = []
    _patch_single_window(monkeypatch, hwnd=555, pid=4242, process_name="explorer.exe")
    monkeypatch.setattr(enforcer.win32gui, "ShowWindow", lambda h, cmd: minimize_calls.append((h, cmd)))

    enforcer.sweep_minimize_blocked_windows()

    assert minimize_calls == []


def test_sweep_disallows_peek_on_minimized_blocked_window(isolate_state, monkeypatch):
    """Regression test: minimizing a blocked window alone doesn't stop it
    from being seen -- hovering its taskbar icon still shows a live
    thumbnail, and hovering that (Aero Peek) reveals the real window without
    ever un-minimizing it. Every window sweep_minimize_blocked_windows()
    minimizes must also get peek disallowed."""
    session_manager.start_session(25, "hard", ["discord.exe"], [])
    peek_calls = []
    _patch_single_window(monkeypatch, hwnd=555, pid=4242, process_name="discord.exe")
    monkeypatch.setattr(enforcer.win32gui, "ShowWindow", lambda h, cmd: None)
    monkeypatch.setattr(
        enforcer, "_hide_taskbar_preview", lambda hwnd, disallow: peek_calls.append((hwnd, disallow))
    )

    enforcer.sweep_minimize_blocked_windows()

    assert peek_calls == [(555, True)]


def test_hard_lock_redirect_disallows_peek_on_minimized_offending_window(isolate_state, monkeypatch):
    session_manager.start_session(25, "hard", ["discord.exe"], [])
    peek_calls = []
    monkeypatch.setattr(enforcer.win32gui, "GetForegroundWindow", lambda: 555)
    monkeypatch.setattr(enforcer.win32process, "GetWindowThreadProcessId", lambda h: (0, 4242))
    monkeypatch.setattr(enforcer.psutil, "Process", lambda p: _FakeProcess("discord.exe"))
    monkeypatch.setattr(enforcer.win32gui, "ShowWindow", lambda h, cmd: None)
    monkeypatch.setattr(enforcer.win32gui, "SetForegroundWindow", lambda h: None)
    monkeypatch.setattr(enforcer, "_find_window_by_process_name", lambda name: None)
    monkeypatch.setattr(enforcer, "_show_lock_overlay", lambda *a, **kw: None)
    monkeypatch.setattr(
        enforcer, "_hide_taskbar_preview", lambda hwnd, disallow: peek_calls.append((hwnd, disallow))
    )

    enforcer.hard_lock_redirect(offending_process_name="discord.exe")

    assert peek_calls == [(555, True)]


def test_restore_window_for_process_re_allows_peek(isolate_state, monkeypatch):
    peek_calls = []
    monkeypatch.setattr(enforcer, "_find_window_by_process_name", lambda name: 555)
    monkeypatch.setattr(enforcer.win32gui, "IsIconic", lambda h: True)
    monkeypatch.setattr(enforcer.win32gui, "ShowWindow", lambda h, cmd: None)
    monkeypatch.setattr(enforcer.win32gui, "SetForegroundWindow", lambda h: None)
    monkeypatch.setattr(
        enforcer, "_hide_taskbar_preview", lambda hwnd, disallow: peek_calls.append((hwnd, disallow))
    )

    enforcer.restore_window_for_process("discord.exe")

    assert peek_calls == [(555, False)]


def test_hide_taskbar_preview_sets_both_dwm_attributes(monkeypatch):
    """Regression test: DWMWA_DISALLOW_PEEK alone only suppresses the full
    Peek reveal (hovering the enlarged thumbnail) -- the small live
    thumbnail shown the instant you hover the taskbar icon itself is a
    separate mechanism (DWMWA_FORCE_ICONIC_REPRESENTATION) that must also
    be set, or that thumbnail still shows live content."""
    calls = []
    monkeypatch.setattr(
        enforcer.ctypes.windll.dwmapi, "DwmSetWindowAttribute",
        lambda hwnd, attr, value_ptr, size: calls.append((hwnd, attr)),
    )

    enforcer._hide_taskbar_preview(555, True)

    assert set(calls) == {
        (555, enforcer._DWMWA_FORCE_ICONIC_REPRESENTATION),
        (555, enforcer._DWMWA_DISALLOW_PEEK),
    }


def test_dwm_peek_attribute_constants_are_the_real_values():
    """Regression test: an earlier version used 12 (DWMWA_EXCLUDED_FROM_PEEK,
    an unrelated attribute) instead of 11 (the real DWMWA_DISALLOW_PEEK) --
    the call silently "succeeded" without erroring, but did nothing to
    actually stop the taskbar hover-peek cheese."""
    assert enforcer._DWMWA_DISALLOW_PEEK == 11
    assert enforcer._DWMWA_FORCE_ICONIC_REPRESENTATION == 7


def test_window_rect_uses_dwm_extended_frame_bounds(monkeypatch):
    """Regression test: GetWindowRect includes several pixels of invisible
    resize-border padding DWM adds around a window's real visible frame, so
    a soft-lock blackout sized from it covers a rect larger than, and offset
    from, what's actually on screen -- an edge or corner of the real window
    peeks out from under it. DWMWA_EXTENDED_FRAME_BOUNDS gives the bounds DWM
    actually composites, matching what's really drawn."""
    def fake_dwm_get(hwnd, attribute, rect_ptr, size):
        assert attribute == enforcer._DWMWA_EXTENDED_FRAME_BOUNDS
        rect = ctypes.cast(rect_ptr, ctypes.POINTER(enforcer.wintypes.RECT)).contents
        rect.left, rect.top, rect.right, rect.bottom = 50, 60, 250, 360
        return 0

    monkeypatch.setattr(enforcer.ctypes.windll.dwmapi, "DwmGetWindowAttribute", fake_dwm_get)
    assert enforcer._window_rect(999) == (50, 60, 200, 300)


def test_window_rect_falls_back_to_getwindowrect_when_dwm_fails(monkeypatch):
    monkeypatch.setattr(
        enforcer.ctypes.windll.dwmapi, "DwmGetWindowAttribute", lambda hwnd, attribute, rect_ptr, size: 0x80070057
    )
    monkeypatch.setattr(enforcer.win32gui, "GetWindowRect", lambda h: (10, 20, 210, 320))
    assert enforcer._window_rect(999) == (10, 20, 200, 300)


def test_soft_lock_warning_covers_just_the_offending_window(isolate_state, monkeypatch):
    session_manager.start_session(25, "soft", ["discord.exe"], [])
    calls = []
    monkeypatch.setattr(enforcer.win32gui, "GetWindowRect", lambda h: (10, 20, 210, 320))
    monkeypatch.setattr(enforcer, "_show_lock_overlay", lambda *a, **kw: calls.append(kw))

    enforcer.soft_lock_warning(offending_process_name="discord.exe", hwnd=555)

    assert len(calls) == 1
    provider = calls[0].pop("blackout_rect_provider")
    assert calls[0] == {
        "duration_ms": 5000, "offending_process_name": "discord.exe",
        "blackout_rect": (10, 20, 200, 300),
    }
    # The provider re-queries GetWindowRect live rather than reusing the
    # snapshot taken above -- confirms it's wired to the same hwnd, not just
    # echoing the already-captured rect.
    assert provider() == (10, 20, 200, 300)


def test_soft_lock_warning_without_hwnd_has_no_blackout(isolate_state, monkeypatch):
    session_manager.start_session(25, "soft", ["discord.exe"], [])
    calls = []
    monkeypatch.setattr(enforcer, "_show_lock_overlay", lambda *a, **kw: calls.append(kw))

    enforcer.soft_lock_warning(offending_process_name="discord.exe")

    assert calls[0]["blackout_rect"] is None
    assert calls[0]["blackout_rect_provider"] is None


def test_hard_lock_redirect_has_no_blackout(isolate_state, monkeypatch):
    """Regression test: hard lock already minimizes the offending window and
    hides its taskbar preview -- a full redirect notification shouldn't also
    take over the screen the way soft lock's own warning does."""
    session_manager.start_session(25, "hard", ["discord.exe"], [])
    calls = []
    monkeypatch.setattr(enforcer.win32gui, "GetForegroundWindow", lambda: 555)
    monkeypatch.setattr(enforcer.win32process, "GetWindowThreadProcessId", lambda h: (0, 4242))
    monkeypatch.setattr(enforcer.psutil, "Process", lambda p: _FakeProcess("discord.exe"))
    monkeypatch.setattr(enforcer.win32gui, "ShowWindow", lambda h, cmd: None)
    monkeypatch.setattr(enforcer.win32gui, "SetForegroundWindow", lambda h: None)
    monkeypatch.setattr(enforcer, "_find_window_by_process_name", lambda name: None)
    monkeypatch.setattr(enforcer, "_hide_taskbar_preview", lambda hwnd, disallow: None)
    monkeypatch.setattr(enforcer, "_show_lock_overlay", lambda *a, **kw: calls.append(kw))

    enforcer.hard_lock_redirect(offending_process_name="discord.exe")

    assert calls[0].get("blackout_rect") is None


def test_show_blocked_notice_has_no_blackout(isolate_state, monkeypatch):
    """show_blocked_notice fires for a window caught in the background --
    the user may be actively using a different, allowed app right now, so
    covering anything there would hide legitimate work that was never the
    violation."""
    session_manager.start_session(25, "hard", ["discord.exe"], [])
    calls = []
    monkeypatch.setattr(enforcer, "_show_lock_overlay", lambda *a, **kw: calls.append(kw))

    enforcer.show_blocked_notice("discord.exe")

    assert calls == [{"duration_ms": 3000, "offending_process_name": "discord.exe"}]


def test_violation_notice_names_the_app_violation_number_and_a_motivation(isolate_state, monkeypatch):
    session_manager.start_session(25, "hard", ["discord.exe"], [])
    session_manager.record_violation("discord.exe")
    session_manager.record_violation("discord.exe")
    calls = []
    monkeypatch.setattr(enforcer, "_show_lock_overlay", lambda message, **kw: calls.append(message))

    enforcer.show_blocked_notice("discord.exe")

    message = calls[0]
    assert "discord.exe" in message
    assert "violation #2" in message
    # A motivational line follows the title on its own line -- not asserting
    # which one (it's chosen at random from _VIOLATION_MOTIVATIONS), just
    # that something non-empty is actually there.
    assert len(message.splitlines()) >= 2
    assert message.splitlines()[1].strip()


def test_violation_notice_is_deduped_across_hard_redirect_and_sweep_notice(isolate_state, monkeypatch):
    """Regression test: an app with several top-level windows (a floating
    call overlay, a notification popup, an updater dialog) used to trigger
    one overlay per window -- hard_lock_redirect's own notice for whichever
    window was in the foreground, plus a separate show_blocked_notice for
    every other window of the same process caught by the background sweep --
    stacking multiple popups (cascaded to different corners, racing each
    other's focus-stealing ticks) for what the user experienced as a single
    violation."""
    session_manager.start_session(25, "hard", ["discord.exe"], [])
    calls = []
    monkeypatch.setattr(enforcer, "_show_lock_overlay", lambda *a, **kw: calls.append(kw))
    monkeypatch.setattr(enforcer.win32gui, "GetForegroundWindow", lambda: 555)
    monkeypatch.setattr(enforcer.win32process, "GetWindowThreadProcessId", lambda h: (0, 4242))
    monkeypatch.setattr(enforcer.psutil, "Process", lambda p: _FakeProcess("discord.exe"))
    monkeypatch.setattr(enforcer.win32gui, "ShowWindow", lambda h, cmd: None)
    monkeypatch.setattr(enforcer.win32gui, "SetForegroundWindow", lambda h: None)
    monkeypatch.setattr(enforcer, "_find_window_by_process_name", lambda name: None)
    monkeypatch.setattr(enforcer, "_hide_taskbar_preview", lambda hwnd, disallow: None)

    enforcer.hard_lock_redirect(offending_process_name="discord.exe")
    # Two more top-level windows of the same process, caught moments later by
    # sweep_minimize_blocked_windows in the same or the very next poll tick.
    enforcer.show_blocked_notice("discord.exe")
    enforcer.show_blocked_notice("discord.exe")

    assert len(calls) == 1

    # A different process is never suppressed by another process's cooldown.
    enforcer.show_blocked_notice("roblox.exe")
    assert len(calls) == 2


def test_violation_notice_fires_again_once_its_own_cooldown_elapses(isolate_state, monkeypatch):
    calls = []
    monkeypatch.setattr(enforcer, "_show_lock_overlay", lambda *a, **kw: calls.append(kw))
    session_manager.start_session(25, "hard", ["discord.exe"], [])

    fake_now = [1000.0]
    monkeypatch.setattr(enforcer.time, "time", lambda: fake_now[0])

    enforcer.show_blocked_notice("discord.exe")
    fake_now[0] += 1
    enforcer.show_blocked_notice("discord.exe")
    assert len(calls) == 1, "still within the cooldown, must not stack a second popup"

    fake_now[0] += enforcer._VIOLATION_NOTICE_COOLDOWN_SECONDS
    enforcer.show_blocked_notice("discord.exe")
    assert len(calls) == 2, "cooldown elapsed -- a genuinely new violation must still get its own notice"


def test_window_rect_returns_left_top_width_height(monkeypatch):
    import enforcer as enforcer_module
    monkeypatch.setattr(enforcer_module.win32gui, "GetWindowRect", lambda h: (10, 20, 210, 320))
    assert enforcer_module._window_rect(555) == (10, 20, 200, 300)


def test_window_rect_returns_none_on_failure(monkeypatch):
    import enforcer as enforcer_module

    def raise_error(h):
        raise Exception("window gone")

    monkeypatch.setattr(enforcer_module.win32gui, "GetWindowRect", raise_error)
    assert enforcer_module._window_rect(555) is None


def test_hide_taskbar_preview_tracks_hidden_hwnds(monkeypatch):
    monkeypatch.setattr(
        enforcer.ctypes.windll.dwmapi, "DwmSetWindowAttribute",
        lambda hwnd, attr, value_ptr, size: None,
    )

    enforcer._hide_taskbar_preview(555, True)
    assert 555 in enforcer._hidden_hwnds

    enforcer._hide_taskbar_preview(555, False)
    assert 555 not in enforcer._hidden_hwnds


def test_restore_all_taskbar_previews_reverts_every_hidden_window(monkeypatch):
    """Regression test: a window hard lock hid from Alt+Tab/taskbar preview
    used to stay that way forever once the session ended normally --
    restore_window_for_process only ever ran for an explicit mid-session
    Unblock click, never for a plain session end."""
    calls = []
    monkeypatch.setattr(
        enforcer.ctypes.windll.dwmapi, "DwmSetWindowAttribute",
        lambda hwnd, attr, value_ptr, size: calls.append((hwnd, attr)),
    )

    enforcer._hide_taskbar_preview(111, True)
    enforcer._hide_taskbar_preview(222, True)
    calls.clear()

    enforcer.restore_all_taskbar_previews()

    assert set(calls) == {
        (111, enforcer._DWMWA_FORCE_ICONIC_REPRESENTATION),
        (111, enforcer._DWMWA_DISALLOW_PEEK),
        (222, enforcer._DWMWA_FORCE_ICONIC_REPRESENTATION),
        (222, enforcer._DWMWA_DISALLOW_PEEK),
    }
    assert enforcer._hidden_hwnds == set()


def test_restore_all_taskbar_previews_tolerates_a_closed_window(monkeypatch):
    monkeypatch.setattr(
        enforcer.ctypes.windll.dwmapi, "DwmSetWindowAttribute",
        lambda hwnd, attr, value_ptr, size: (_ for _ in ()).throw(Exception("window gone")),
    )
    enforcer._hide_taskbar_preview(555, True)

    enforcer.restore_all_taskbar_previews()  # must not raise

    assert enforcer._hidden_hwnds == set()


def test_session_end_restores_all_taskbar_previews(isolate_state, monkeypatch):
    restore_calls = []
    monkeypatch.setattr(enforcer, "restore_all_taskbar_previews", lambda: restore_calls.append(1))

    session_manager.start_session(25, "hard", ["discord.exe"], [])
    session_manager.end_session()

    assert restore_calls == [1]


def test_natural_session_end_restores_all_taskbar_previews(isolate_state, monkeypatch):
    restore_calls = []
    monkeypatch.setattr(enforcer, "restore_all_taskbar_previews", lambda: restore_calls.append(1))
    monkeypatch.setattr(session_manager, "_pending_natural_end", {"value": {"endType": "natural"}})

    summary = session_manager.pop_pending_natural_end()

    assert summary == {"endType": "natural"}
    assert restore_calls == [1]
