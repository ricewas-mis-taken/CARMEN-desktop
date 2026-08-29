"""Soft/hard lock enforcement actions."""
import ctypes
import json
import os
import threading
from ctypes import wintypes

import psutil
import win32con
import win32gui
import win32process

import qt_gui_thread
import qt_ui.enforcer_overlay as enforcer_overlay
import session_manager

# Two separate, undocumented-in-win32con DWM window attributes (dwmapi.h),
# set via raw ctypes -- minimizing a blocked window alone doesn't stop it
# from being seen. Windows offers two different hover-preview surfaces for
# a taskbar button, and both need suppressing:
#
#   - DWMWA_DISALLOW_PEEK (11) stops the FULL "Peek" reveal -- hovering the
#     enlarged thumbnail (in the taskbar strip or Alt+Tab) making the real
#     window fully visible on the desktop without un-minimizing/focusing it.
#     (An earlier version used 12 here -- DWMWA_EXCLUDED_FROM_PEEK, an
#     unrelated attribute -- which silently did nothing useful instead of
#     erroring, so this half of the cheese kept working.)
#   - DWMWA_FORCE_ICONIC_REPRESENTATION (7) stops the SMALL live thumbnail
#     shown the instant you hover the taskbar icon itself, before Peek is
#     even invoked -- DISALLOW_PEEK alone does not touch this; it's a
#     separate mechanism. The blocked app is never told to supply a custom
#     thumbnail bitmap (that would need code running inside its own
#     process), so DWM just falls back to a plain static icon instead.
#
# Together, a minimized blocked window is only ever seen as a plain static
# taskbar icon -- no live content at any hover stage -- the same "cheese"
# risk the browser extension's own click-and-hold block guards against on
# the extension side.
_DWMWA_FORCE_ICONIC_REPRESENTATION = 7
_DWMWA_DISALLOW_PEEK = 11

# Every hwnd currently hidden via _hide_taskbar_preview(hwnd, True) --
# restore_all_taskbar_previews() (called whenever a session ends, see
# session_manager.py) walks this to undo every one of them, not just
# whichever single window restore_window_for_process's mid-session
# "Unblock" flow happens to target. Without this, a window hard lock hid
# from Alt+Tab/taskbar preview during the session stayed that way forever
# once the session ended normally -- restore_window_for_process only ever
# ran for an explicit Unblock click, never for a plain session end.
_hidden_hwnds = set()
# Guards _hidden_hwnds -- it's mutated from both window_tracker's polling
# thread (sweep_minimize_blocked_windows, hard_lock_redirect) and the Qt main
# thread (restore_window_for_process, on an "Unblock" click).
_hidden_hwnds_lock = threading.Lock()


def _hide_taskbar_preview(hwnd, hide):
    with _hidden_hwnds_lock:
        if hide:
            _hidden_hwnds.add(hwnd)
        else:
            _hidden_hwnds.discard(hwnd)
    for attribute in (_DWMWA_FORCE_ICONIC_REPRESENTATION, _DWMWA_DISALLOW_PEEK):
        try:
            value = ctypes.c_int(1 if hide else 0)
            ctypes.windll.dwmapi.DwmSetWindowAttribute(
                hwnd, attribute, ctypes.byref(value), ctypes.sizeof(value)
            )
        except Exception:
            pass


def restore_all_taskbar_previews():
    """Reverts _hide_taskbar_preview for every window hidden this session --
    called on every session end (see session_manager.py's end_session() and
    pop_pending_natural_end()), independent of whether any of them were
    individually unblocked first. Safe to call with stale/closed hwnds
    still in _hidden_hwnds -- _hide_taskbar_preview's own try/except already
    swallows a DWM call against a hwnd that no longer exists."""
    with _hidden_hwnds_lock:
        hwnds = list(_hidden_hwnds)
    for hwnd in hwnds:
        _hide_taskbar_preview(hwnd, False)


# --- Per-window AppUserModelID (for blocking one Chrome/Edge profile) ---
#
# Chrome/Edge run every open profile as ONE OS process (a second
# `chrome.exe --profile-directory=X` launch hands off to the existing
# process over an IPC pipe and exits immediately) -- confirmed empirically
# on this machine: two profile windows, same PID. That means
# session_manager.is_blocked()'s plain process-name check can never tell one
# profile's windows apart from another's. What Windows itself uses to let
# the user pin separate taskbar icons per profile is each top-level browser
# window's own AppUserModelID (AUMI), set individually via IPropertyStore --
# NOT the process-wide SetCurrentProcessExplicitAppUserModelID, which
# couldn't differ per window on a shared process anyway. This section reads
# that per-window AUMI via SHGetPropertyStoreForWindow, the same mechanism
# Explorer itself uses for taskbar grouping.
_ole32 = ctypes.windll.ole32
_shell32 = ctypes.windll.shell32


class _GUID(ctypes.Structure):
    _fields_ = [
        ("Data1", ctypes.c_uint32),
        ("Data2", ctypes.c_uint16),
        ("Data3", ctypes.c_uint16),
        ("Data4", ctypes.c_uint8 * 8),
    ]


def _guid(text):
    guid = _GUID()
    ctypes.windll.ole32.CLSIDFromString(ctypes.c_wchar_p(text), ctypes.byref(guid))
    return guid


class _PROPERTYKEY(ctypes.Structure):
    _fields_ = [("fmtid", _GUID), ("pid", ctypes.c_uint32)]


class _PROPVARIANT(ctypes.Structure):
    # Only ever read as VT_LPWSTR here -- 16 bytes of generic union storage
    # is plenty for that one pointer-sized arm, without modeling every real
    # PROPVARIANT arm (CY, FILETIME, CLIPDATA, ...) this code never touches.
    _fields_ = [
        ("vt", ctypes.c_uint16),
        ("wReserved1", ctypes.c_uint16),
        ("wReserved2", ctypes.c_uint16),
        ("wReserved3", ctypes.c_uint16),
        ("data", ctypes.c_uint64 * 2),
    ]


_PKEY_AppUserModel_ID = _PROPERTYKEY(_guid("{9F4C2855-9F79-4B39-A8D0-E1D42DE1D5F3}"), 5)
_IID_IPropertyStore = _guid("{886D8EEB-8CF2-4446-8D02-CDBA1DBDCF99}")
_VT_LPWSTR = 31

_shell32.SHGetPropertyStoreForWindow.argtypes = [
    wintypes.HWND, ctypes.POINTER(_GUID), ctypes.POINTER(ctypes.c_void_p)
]
_shell32.SHGetPropertyStoreForWindow.restype = wintypes.LONG
_ole32.PropVariantClear.argtypes = [ctypes.c_void_p]
_ole32.PropVariantClear.restype = wintypes.LONG

_GetValueProto = ctypes.WINFUNCTYPE(
    wintypes.LONG, ctypes.c_void_p, ctypes.POINTER(_PROPERTYKEY), ctypes.POINTER(_PROPVARIANT)
)
_ReleaseProto = ctypes.WINFUNCTYPE(ctypes.c_ulong, ctypes.c_void_p)


def get_window_aumi(hwnd):
    """The AppUserModelID hwnd's owner set on this specific window, or None
    if it doesn't have one / the lookup fails for any reason. This is raw
    COM vtable interop (SHGetPropertyStoreForWindow -> IPropertyStore ->
    GetValue(PKEY_AppUserModel_ID)) -- more failure-prone than anything else
    in this file, so every step is guarded and a failure here must never be
    allowed to propagate into the enforcement loop that calls it every
    tick."""
    pps = ctypes.c_void_p()
    try:
        # SHGetPropertyStoreForWindow silently fails (a COM "not
        # initialized" HRESULT, not a raised exception -- caught below as
        # hr != 0 either way) unless COM is initialized on the calling
        # thread. This is called from window_tracker's polling thread, Qt's
        # main thread, and Flask worker threads, none of which are
        # guaranteed to have done that themselves. CoInitialize is a no-op
        # (returns S_FALSE, not an error) if this thread already has COM
        # initialized, so it's safe to call unconditionally on every lookup.
        _ole32.CoInitialize(None)
        hr = _shell32.SHGetPropertyStoreForWindow(hwnd, ctypes.byref(_IID_IPropertyStore), ctypes.byref(pps))
        if hr != 0 or not pps.value:
            return None
        vtable_ptr = ctypes.cast(pps, ctypes.POINTER(ctypes.c_void_p)).contents.value
        vtable = ctypes.cast(vtable_ptr, ctypes.POINTER(ctypes.c_void_p * 8)).contents
        release = _ReleaseProto(vtable[2])
        try:
            get_value = _GetValueProto(vtable[5])
            pv = _PROPVARIANT()
            hr = get_value(pps, ctypes.byref(_PKEY_AppUserModel_ID), ctypes.byref(pv))
            result = None
            if hr == 0 and pv.vt == _VT_LPWSTR and pv.data[0]:
                result = ctypes.wstring_at(pv.data[0])
            _ole32.PropVariantClear(ctypes.byref(pv))
            return result
        finally:
            release(pps)
    except Exception:
        return None


# Where each browser keeps its own Local State file -- the same JSON file
# that backs the in-browser profile switcher, keyed by literal profile
# folder name ("Default", "Profile 2", ...). Confirmed empirically that the
# AUMI encodes that folder name with spaces stripped (folder "Profile 4" ->
# AUMI suffix "Profile4"), so lookups below normalize both sides the same
# way rather than assuming an exact match.
_PROFILE_DATA_DIRS = {
    "chrome.exe": os.path.join(os.environ.get("LOCALAPPDATA", ""), "Google", "Chrome", "User Data"),
    "msedge.exe": os.path.join(os.environ.get("LOCALAPPDATA", ""), "Microsoft", "Edge", "User Data"),
}


def _normalize_profile_key(text):
    return text.replace(" ", "").lower()


def _read_profile_display_names(process_name):
    """{normalized profile folder name -> the name Chrome/Edge itself shows
    for it} straight from Local State -- e.g. folder "Profile 4" might be
    signed into an account also used by another profile, in which case
    Chrome shows "Lucas (Person 1)" (its own disambiguated shortcut_name)
    rather than the generic "name" field alone; shortcut_name is preferred
    when both are present, since it's the more specific, recognizable one.
    Returns {} on any failure (file missing, malformed, mid-write by the
    browser itself) -- a friendlier label is a nice-to-have, never worth
    crashing the picker over."""
    data_dir = _PROFILE_DATA_DIRS.get((process_name or "").lower())
    if not data_dir:
        return {}
    local_state_path = os.path.join(data_dir, "Local State")
    try:
        with open(local_state_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        info_cache = data.get("profile", {}).get("info_cache", {})
        names = {}
        for folder, info in info_cache.items():
            display = (info.get("shortcut_name") or info.get("name") or "").strip()
            if display:
                names[_normalize_profile_key(folder)] = display
        return names
    except Exception:
        return {}


def describe_browser_profile_aumi(process_name, aumi):
    """A friendlier label for the picker UI than the raw AUMI -- Chrome/Edge
    encode the profile directory name after ".UserData." (e.g.
    "Chrome.UserData.Profile4"), which is just the folder name (also shown
    in chrome://version), not the display name the user actually sees in
    the profile switcher. Looks that real name up from the browser's own
    Local State file (_read_profile_display_names) and falls back to the
    raw folder name / "Default" / the bare AUMI, in that order, if the
    lookup can't find or read it."""
    if not aumi:
        return process_name
    marker = ".UserData."
    if marker in aumi:
        folder_suffix = aumi.split(marker, 1)[1]
    elif aumi.lower() in ("chrome", "msedge"):
        folder_suffix = "Default"
    else:
        return f"{process_name} — {aumi}"

    display = _read_profile_display_names(process_name).get(_normalize_profile_key(folder_suffix))
    return f"{process_name} — {display or folder_suffix}"


def is_blocked_window(process_name, hwnd):
    """Whether hwnd should be treated as blocked: session_manager.is_blocked()'s
    ordinary process-name check, OR -- for a browser where every profile
    shares one process (session_manager.MULTI_PROFILE_BROWSER_PROCESSES) --
    this specific window belonging to a blocked profile. Lets a session
    block just one Chrome/Edge profile without blocking the browser (and
    every other open profile) outright, and without needing the browser
    extension installed in every profile."""
    if session_manager.is_blocked(process_name):
        return True
    if process_name and process_name.lower() in session_manager.MULTI_PROFILE_BROWSER_PROCESSES:
        aumi = get_window_aumi(hwnd)
        if aumi and session_manager.is_blocked_browser_profile(aumi):
            return True
    return False


def soft_lock_warning(offending_process_name=None, hwnd=None):
    status = session_manager.get_status()
    if status.get("source") == "review":
        message = f"Finish {status.get('reviewProblemName') or 'this review'} first"
    else:
        last_ok = status["lastAcceptableProcess"] or "your focus app"
        message = f"You're off track — back to {last_ok}?"

    # Covers just the offending window's own rectangle, not the whole
    # screen -- soft lock's point is a warning the user can't just keep
    # reading THAT page underneath for the warning's duration, not a
    # full-screen takeover. No hwnd (or a window that's gone by the time
    # GetWindowRect runs) just means no cover at all, not a full-screen
    # fallback -- see hard_lock_redirect for the "no blackout at all" case
    # this deliberately isn't.
    blackout_rect = _window_rect(hwnd) if hwnd else None
    _show_lock_overlay(
        message,
        duration_ms=5000,
        offending_process_name=offending_process_name,
        blackout_rect=blackout_rect,
    )


def hard_lock_redirect(offending_process_name=None):
    """Minimizes the offending foreground window (unless it's exempt/our own
    process), then brings the last acceptable (non-blocklisted) app's window
    back to the foreground without disturbing its size or snap position.

    Note: a previous version of this deliberately skipped minimizing the
    offending window at all, after it turned out to close a lightweight
    background WPM-tracker app outright instead of minimizing it (some
    fragile/minimal apps mishandle a forced SW_MINIMIZE that way). Minimizing
    is back by explicit request — actually enforcing hard lock means the
    offending window shouldn't still be sitting there — with the understanding
    that this same class of fragile app could in principle hit the same issue
    again. It's wrapped in try/except and skipped entirely for exempt/system
    processes, which is the extent of the safety net here."""
    hwnd = win32gui.GetForegroundWindow()
    hwnd_process = None
    hwnd_pid = None
    try:
        _, hwnd_pid = win32process.GetWindowThreadProcessId(hwnd)
        hwnd_process = psutil.Process(hwnd_pid).name()
    except Exception:
        pass

    # Re-check against the blocklist too, not just is_exempt() — the
    # foreground window can change between the polling tick that detected
    # this violation and this call actually running (e.g. the user already
    # switched to an allowed app in that gap). Minimizing whatever happens
    # to be foreground right now without this check could minimize a
    # window that's no longer the violation at all.
    if (
        hwnd
        and not session_manager.is_exempt(hwnd_process, hwnd_pid)
        and hwnd_process
        and is_blocked_window(hwnd_process, hwnd)
    ):
        try:
            win32gui.ShowWindow(hwnd, win32con.SW_MINIMIZE)
            _hide_taskbar_preview(hwnd, True)
        except Exception:
            pass

    last_acceptable = session_manager.get_last_acceptable_process()
    target_hwnd = _find_window_by_process_name(last_acceptable) if last_acceptable else None
    if target_hwnd:
        try:
            # Only restore if it's actually minimized — calling SW_RESTORE on
            # a window that's already visible (e.g. snapped to half the
            # screen) can reset it back to its pre-snap size, which is the
            # "other window shrinks" bug this guards against.
            if win32gui.IsIconic(target_hwnd):
                win32gui.ShowWindow(target_hwnd, win32con.SW_RESTORE)
            win32gui.SetForegroundWindow(target_hwnd)
        except Exception:
            pass

    label = offending_process_name or hwnd_process or "that app"
    status = session_manager.get_status()
    if status.get("source") == "review":
        message = f"Finish {status.get('reviewProblemName') or 'this review'} first"
    else:
        back_to = last_acceptable or "your focus app"
        message = f"Redirected from {label} — back to {back_to}."
    _show_lock_overlay(
        message,
        duration_ms=3000,
        offending_process_name=label if label != "that app" else None,
        # No blackout -- hard lock already minimizes the offending window
        # and (via _hide_taskbar_preview) hides its taskbar hover preview;
        # a screen-covering overlay on top of a plain redirect is the
        # taskbar-hover-cheese's fix, not something a normal click into the
        # window needs too.
    )


def sweep_minimize_blocked_windows():
    """Minimizes every visible, non-iconic window belonging to a blocklisted
    process -- not just whichever one happens to be in the foreground on a
    given poll tick. hard_lock_redirect() alone only ever reacts to the
    current foreground window, so a blocklisted app that's open in the
    background (already running before the session started, or reopened in
    the gap between two polls without ever becoming the foreground window,
    or simply not fully focused yet by the time this tick's foreground
    check ran) would otherwise just sit there, unminimized and usable,
    until the user happened to switch to it directly.

    Returns a list of (process_name, hwnd) actually minimized this call
    (i.e. the ones that weren't already iconic) -- window_tracker.py's
    polling loop uses this to raise the same violation/overlay treatment as
    the foreground-focused path, since a window caught here never otherwise
    passes through that code and would silently vanish with no way to
    unblock it. hwnd is included (not just the name) so the caller can key
    its own notice cooldown on the exact window, the same reasoning as
    HARD_REDIRECT_COOLDOWN_SECONDS's hwnd-keyed cooldown: a process-name-only
    cooldown can't tell "this exact window keeps coming right back" (needs
    throttling, or a stuck popup spams a notice every tick) apart from "the
    user restored/reopened a window they haven't been told about yet"
    (deserves an immediate notice)."""
    minimized = []

    def callback(hwnd, _):
        if not win32gui.IsWindowVisible(hwnd):
            return
        # Not a title check -- a blocked app that clears its own window title
        # (deliberately or just transiently, e.g. mid-navigation) used to be
        # invisible to this sweep entirely, letting it sit open and usable in
        # the background for the rest of the session. GW_OWNER == 0 is the
        # standard "is this a real top-level window" test instead -- it still
        # skips the small owned/tool windows (tooltips, dropdown popups) this
        # check was originally guarding against, just not on title alone.
        if win32gui.GetWindow(hwnd, win32con.GW_OWNER) != 0:
            return
        try:
            _, pid = win32process.GetWindowThreadProcessId(hwnd)
            name = psutil.Process(pid).name()
        except (psutil.NoSuchProcess, psutil.AccessDenied, Exception):
            return
        if session_manager.is_exempt(name, pid):
            return
        if not is_blocked_window(name, hwnd):
            return

        # Not skipped just because it's already iconic -- a window that
        # arrives here already minimized (started minimized, or the user
        # minimized it manually before this sweep ever saw it visible)
        # used to never get this call at all, since the old early-return
        # above bailed on any already-iconic window before reaching it.
        # hard_lock_redirect's own independent minimize+hide call only
        # ever fires when the window is genuinely in the foreground, so a
        # window that never became the foreground window would stay
        # peek-visible on hover/Alt+Tab indefinitely -- "only blocked once
        # you've actually switched into it" was exactly this gap.
        # _hide_taskbar_preview is idempotent (safe to call again on an
        # hwnd it's already hidden), so reapplying every tick is harmless.
        was_iconic = win32gui.IsIconic(hwnd)
        try:
            if not was_iconic:
                win32gui.ShowWindow(hwnd, win32con.SW_MINIMIZE)
                minimized.append((name, hwnd))
            _hide_taskbar_preview(hwnd, True)
        except Exception:
            pass

    win32gui.EnumWindows(callback, None)
    return minimized


def show_blocked_notice(process_name):
    """Shows the lock overlay (with its "Unblock" button) for a window that
    sweep_minimize_blocked_windows() just minimized, without touching
    whatever's actually in the foreground right now -- unlike
    hard_lock_redirect(), which assumes the blocked app IS the foreground
    window and redirects focus away from it. A sweep-caught window usually
    isn't the foreground window (that's exactly why the foreground check
    missed it), so redirecting focus here would yank it away from whatever
    the user is legitimately doing instead."""
    status = session_manager.get_status()
    if status.get("source") == "review":
        message = f"Finish {status.get('reviewProblemName') or 'this review'} first"
    else:
        message = f"{process_name} is blocked and was minimized."
    _show_lock_overlay(message, duration_ms=5000, offending_process_name=process_name)


def restore_window_for_process(process_name):
    """Un-minimizes and foregrounds process_name's window, if it has one --
    called right after a mid-session unblock so "let this through" is
    immediately visible. Without this, a window that hard lock already
    minimized before the unblock stays minimized: the user has to go dig
    it out of the taskbar themselves, and in the meantime it looks
    identical to "the unblock didn't work" even though processBlocklist
    was updated correctly."""
    hwnd = _find_window_by_process_name(process_name)
    if not hwnd:
        return
    try:
        if win32gui.IsIconic(hwnd):
            win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
        win32gui.SetForegroundWindow(hwnd)
        # Undo _hide_taskbar_preview from whichever minimize caught this
        # window -- it's no longer blocked, so its taskbar preview/Peek
        # should behave normally again like any other allowed app.
        _hide_taskbar_preview(hwnd, False)
    except Exception:
        pass


def _find_window_by_process_name(process_name):
    found = {"hwnd": None}

    def callback(hwnd, _):
        if found["hwnd"] is not None:
            return
        if not win32gui.IsWindowVisible(hwnd):
            return
        if not win32gui.GetWindowText(hwnd):
            return
        try:
            _, pid = win32process.GetWindowThreadProcessId(hwnd)
            name = psutil.Process(pid).name()
        except (psutil.NoSuchProcess, psutil.AccessDenied, Exception):
            return
        if name.lower() == process_name.lower():
            found["hwnd"] = hwnd

    win32gui.EnumWindows(callback, None)
    return found["hwnd"]


def _window_rect(hwnd):
    """(left, top, width, height) for hwnd, or None if it's gone/invalid by
    the time this runs -- soft_lock_warning's own hwnd->rect lookup, kept
    here (not in qt_ui/enforcer_overlay.py) since that module has no win32
    dependency of its own."""
    try:
        left, top, right, bottom = win32gui.GetWindowRect(hwnd)
        if right <= left or bottom <= top:
            return None
        return (left, top, right - left, bottom - top)
    except Exception:
        return None


def _show_lock_overlay(message, duration_ms, offending_process_name=None, blackout_rect=None):
    """Shows a small always-on-top, borderless popup for duration_ms while a
    progress bar fills, then closes automatically. It repeatedly raises and
    refocuses itself so it's hard to ignore, but deliberately does not take
    a system-wide input grab -- that would freeze every other running app
    (any background exe's window, tray flyouts, etc.), not just the
    offending one.

    Built on the Qt main thread via qt_gui_thread.run_on_gui_thread() rather
    than constructed here directly -- Qt widgets, like the Tk widgets this
    replaced, may only be touched from the thread that owns the
    QApplication (this app's main thread; see main.py), and this function
    runs on window_tracker's polling thread instead.

    Guarded two ways against ever getting stuck open: the overlay's own
    tick-driven close, and a backup timer -- see qt_ui/enforcer_overlay.py.

    offending_process_name, when known, adds an "Unblock" button -- lets
    the user let that exe through for the rest of the session without
    ending hard/soft lock enforcement entirely, same as the "Pick Apps to
    Blocklist" picker's own removal flow, just reachable from the moment of
    redirect itself.

    blackout_rect, when given, covers exactly that (left, top, width,
    height) in solid black (qt_ui/enforcer_overlay.py's _BlackoutOverlay)
    alongside the small notification popup, for the same duration -- only
    soft_lock_warning passes one, sized to the offending window itself (not
    the whole screen), so its warning can't just be read straight through
    for the duration it's up. hard_lock_redirect and show_blocked_notice
    never pass one: hard lock already minimizes the window and hides its
    taskbar preview, and show_blocked_notice's window is usually in the
    background, where covering anything would hide unrelated, allowed work.
    """
    qt_gui_thread.run_on_gui_thread(
        lambda: enforcer_overlay.build_overlay(
            message, duration_ms, offending_process_name, blackout_rect=blackout_rect
        )
    )
