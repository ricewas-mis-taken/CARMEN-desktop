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
    assert swept == [("discord.exe", 555)]


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


def test_soft_lock_warning_covers_just_the_offending_window(isolate_state, monkeypatch):
    session_manager.start_session(25, "soft", ["discord.exe"], [])
    calls = []
    monkeypatch.setattr(enforcer.win32gui, "GetWindowRect", lambda h: (10, 20, 210, 320))
    monkeypatch.setattr(enforcer, "_show_lock_overlay", lambda *a, **kw: calls.append(kw))

    enforcer.soft_lock_warning(offending_process_name="discord.exe", hwnd=555)

    assert calls == [{
        "duration_ms": 5000, "offending_process_name": "discord.exe",
        "blackout_rect": (10, 20, 200, 300),
    }]


def test_soft_lock_warning_without_hwnd_has_no_blackout(isolate_state, monkeypatch):
    session_manager.start_session(25, "soft", ["discord.exe"], [])
    calls = []
    monkeypatch.setattr(enforcer, "_show_lock_overlay", lambda *a, **kw: calls.append(kw))

    enforcer.soft_lock_warning(offending_process_name="discord.exe")

    assert calls[0]["blackout_rect"] is None


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

    assert calls == [{"duration_ms": 5000, "offending_process_name": "discord.exe"}]


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
