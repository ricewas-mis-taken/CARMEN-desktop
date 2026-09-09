# Carmen Focus — macOS Port Spec

## Mission

Port Carmen Focus to macOS as a **true 1:1 behavioral replica of the Windows app**. Every
feature that works on Windows today must work identically on macOS: same enforcement
strength, same UX flow, same session/state model, same sync behavior. There is no
"lite" or "best-effort" macOS build — where a Windows mechanism has no direct macOS
API equivalent, find the macOS mechanism that achieves the **same practical outcome**
(same or stronger enforcement), not a weaker approximation.

Two things in this spec are flagged as **research spikes** rather than a settled
design, because they depend on macOS/Chrome behavior that needs to be verified
empirically before committing to an implementation. Do the spike, confirm the
mechanism actually works, *then* implement — do not skip straight to a guess.

## Hard constraints

1. **All new code lives under `mac-os/` at the repo root.** No Windows-platform file's
   *logic* changes. The only exception (see "Dispatch shims" below) is a small,
   fixed list of existing files that need a **single conditional import** added at
   the top, so the app picks the right platform implementation at runtime. Nothing
   else in those files changes.
2. **No Windows behavior may change or regress.** Every dispatch shim must be a no-op
   on Windows — same import, same call, same behavior as today.
3. **Feature parity, not visual parity.** The macOS build should look like a native
   macOS app (title bar, window chrome, tray/menu-bar icon behavior) — PySide6
   already handles this automatically. Parity is about *what the app does and
   enforces*, not pixel-matching Windows UI.
4. Branch: work on top of `main`. One branch for the whole port (or sub-branches per
   phase, merged back before the next phase starts) — follow whatever this repo's
   existing convention is for multi-phase work (see recent git history for examples
   of phased feature branches with individual commits per logical change).
5. Commit style: `type: short lowercase description`, one logical change per commit
   (see any recent commit on `main` for the pattern already in use).

---

## Architecture: platform dispatch

Six existing files import Windows-only APIs directly. Each needs the same one-line
treatment: detect the platform, import the macOS implementation from `mac-os/`
instead when running on macOS, otherwise behave exactly as it does today.

Pattern to use in each of the files below (adjust names to match):

```python
import sys

if sys.platform == "darwin":
    from mac_os.enforcer_mac import (
        soft_lock_warning,
        hard_lock_redirect,
        sweep_minimize_blocked_windows,
        is_blocked_window,
        get_window_aumi,
        describe_browser_profile_aumi,
        list_known_profile_aumis,
    )
else:
    # existing Windows implementation below, unchanged
    ...
```

Note: `mac-os` is not a valid Python package name (hyphens aren't allowed in
identifiers), so the actual importable package inside it must be named
`mac_os` (underscore) even though the directory on disk is `mac-os` — i.e. put an
`__init__.py` at `mac-os/mac_os/__init__.py` and the real modules under
`mac-os/mac_os/*.py`, OR simpler: just name the top-level directory's Python package
folder `mac_os` and have `mac-os/` be a container directory that only holds
`mac_os/` plus non-code files (this spec, `requirements-mac.txt`, README). Pick
whichever layout keeps `sys.path`/imports clean — document the choice in
`mac-os/README.md` once decided, since it affects every import statement below.

### Files needing the dispatch shim (and nothing else changed in them)

| File | What to guard |
|---|---|
| `enforcer.py` | Its whole public API (see table below) — used by `window_tracker.py`, `api_server.py` |
| `window_tracker.py` | `list_running_apps`, `list_browser_profile_windows`, `list_known_browser_profiles`, `get_active_window`, `run_polling_loop`'s internal window-lookup calls |
| `installed_apps.py` | `list_installed_apps` |
| `autostart.py` | `ensure_autostart_registered` |
| `calendar_toast.py` | `set_app_id`, `show_toast` (or whatever its public functions are — check current signatures before wiring) |
| `qt_ui/main_window.py` | Just wrap the `nativeEvent`/`win32gui` drag-snap block in `if sys.platform != "darwin":` — this one is cosmetic, not a dispatch to a macOS equivalent (there's nothing to replace it with; macOS doesn't need this fix) |

`tray.py` needs **no shim at all** — it only calls `pystray`'s public API, which
already has a macOS backend. Just make sure `requirements-mac.txt` includes the
pyobjc packages `pystray` needs on macOS (see dependencies section).

---

## Subsystem 1: Window enumeration & active-window detection

**Replaces:** `window_tracker.py`'s Windows-only calls (`win32gui.EnumWindows`,
`win32gui.GetForegroundWindow`, `win32process.GetWindowThreadProcessId`).

**New file:** `mac-os/window_tracker_mac.py`

macOS equivalent APIs (via `pyobjc`):

```python
from AppKit import NSWorkspace, NSApplicationActivateIgnoringOtherApps
from Quartz import (
    CGWindowListCopyWindowInfo,
    kCGWindowListOptionOnScreenOnly,
    kCGNullWindowID,
)
import psutil  # already a dependency, cross-platform, no change needed


def get_active_window():
    """Equivalent of window_tracker.get_active_window()."""
    active_app = NSWorkspace.sharedWorkspace().frontmostApplication()
    pid = active_app.processIdentifier()
    title = None
    for w in CGWindowListCopyWindowInfo(kCGWindowListOptionOnScreenOnly, kCGNullWindowID):
        if w.get("kCGWindowOwnerPID") == pid and w.get("kCGWindowLayer") == 0:
            title = w.get("kCGWindowName") or title
            break
    try:
        process_name = psutil.Process(pid).name()
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        process_name = None
    # "hwnd" doesn't exist on macOS -- use pid as the window-identity key
    # everywhere enforcer_mac.py/window_tracker_mac.py need a handle. Every
    # call site that currently threads an hwnd through (is_blocked_window,
    # sweep_minimize_blocked_windows, the hard-redirect cooldown dict, etc.)
    # needs to accept a pid (or an AXUIElement ref, see below) instead --
    # trace every hwnd-typed parameter back from window_tracker.py through
    # enforcer.py and give it a mac-appropriate replacement type.
    return {"title": title, "process_name": process_name, "pid": pid, "hwnd": pid}


def list_running_apps():
    """Equivalent of window_tracker.list_running_apps() -- one entry per
    unique process name, for the app picker."""
    apps = {}
    for w in CGWindowListCopyWindowInfo(kCGWindowListOptionOnScreenOnly, kCGNullWindowID):
        title = w.get("kCGWindowName")
        pid = w.get("kCGWindowOwnerPID")
        if not title or pid is None:
            continue
        try:
            process_name = psutil.Process(pid).name()
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
        key = process_name.lower()
        if key not in apps:
            apps[key] = {"process_name": process_name, "window_title": title}
    return list(apps.values())
```

`run_polling_loop`'s actual poll-loop *logic* (the violation/cooldown/dedupe
bookkeeping in `window_tracker.py`) is pure Python and platform-agnostic already —
it just calls `get_active_window()` and `enforcer.is_blocked_window()`. Once those
two are swapped for the mac versions via the dispatch shim, the loop itself needs
no changes. Confirm this by re-reading `run_polling_loop` before touching anything —
if it turns out to reference `win32gui`/`win32process` directly anywhere beyond
what's already listed above, add that to the shim list too.

---

## Subsystem 2: Enforcement — hard lock, sweep-minimize, soft lock

**Replaces:** `enforcer.py`'s Windows-only functions.

**New file:** `mac-os/enforcer_mac.py`

### Permission model (read this first — nothing below works without it)

Every mechanism in this subsystem requires **Accessibility permission**, granted
once by the user in System Settings → Privacy & Security → Accessibility. This is
the macOS analogue of "this app needs to control other windows" — there is no way
around it; Apple gates all cross-application window control behind this toggle.

- Check/request via `pyobjc-framework-ApplicationServices`:
  ```python
  from ApplicationServices import AXIsProcessTrustedWithOptions
  AXIsProcessTrustedWithOptions({"AXTrustedCheckOptionPrompt": True})
  ```
  Passing the prompt option makes macOS pop the system permission dialog itself the
  first time this runs, if not already granted.
- **Build this as an explicit onboarding step**, not a silent background check:
  on first launch (or any launch where the check comes back `False`), show a clear
  in-app message explaining that Carmen Focus needs Accessibility access to enforce
  focus sessions, with a button that opens System Settings directly
  (`open "x-apple.systempreferences:com.apple.preference.security?Privacy_Accessibility"`
  via `subprocess`). Block session-start (or at minimum show a persistent warning)
  until permission is granted — a session that *thinks* it's enforcing but silently
  can't touch any window would be worse than not having the feature at all.
- Re-check trust status on every app launch, not just once ever — the user can
  revoke it later in System Settings, and a stale "trusted" assumption would mean
  hard lock silently does nothing.

### Hard lock redirect (replaces `hard_lock_redirect`)

Windows minimizes the single offending window and restores the last acceptable app.
The macOS replacement should do the same, using the Accessibility API to act on the
specific window rather than the whole app (matching Windows' per-window granularity,
not per-app):

```python
from ApplicationServices import (
    AXUIElementCreateApplication,
    AXUIElementCopyAttributeValue,
    AXUIElementSetAttributeValue,
    kAXWindowsAttribute,
    kAXMinimizedAttribute,
)
from AppKit import NSRunningApplication, NSApplicationActivateIgnoringOtherApps


def _minimize_all_windows(pid):
    app_ref = AXUIElementCreateApplication(pid)
    err, windows = AXUIElementCopyAttributeValue(app_ref, kAXWindowsAttribute, None)
    if err:
        return False
    for w in windows or []:
        AXUIElementSetAttributeValue(w, kAXMinimizedAttribute, True)
    return True


def _activate(pid):
    app = NSRunningApplication.runningApplicationWithProcessIdentifier_(pid)
    if app:
        app.activateWithOptions_(NSApplicationActivateIgnoringOtherApps)
```

Port `hard_lock_redirect`'s actual control flow (re-check against the blocklist
right before acting, since the foreground window can change between detection and
this call running — see the existing Windows function's own comment on this race)
unchanged; only the "minimize this" and "activate that" primitives change.

### Sweep-minimize (replaces `sweep_minimize_blocked_windows`)

Same idea as Windows: walk every visible window (via `CGWindowListCopyWindowInfo`,
same call as Subsystem 1), and for any blocked one not already minimized
(`kAXMinimizedAttribute` reads `True`), minimize it. Port the existing cooldown/dedupe
logic (`HARD_REDIRECT_COOLDOWN_SECONDS`, the hwnd-keyed dicts in
`window_tracker.py`) as-is, just keyed by `pid` (or a stable per-window AX
reference — verify whether `AXUIElement` references stay stable across polls before
committing to which key to use; if they don't, pid is the fallback).

### Closest-parity replacement for DWM "disallow peek" (taskbar live thumbnail hiding)

There is no macOS equivalent to Windows' taskbar hover-preview — macOS's Dock
doesn't show a live thumbnail on hover the way Windows' taskbar does, so the literal
attack surface this Windows code defends against (see `enforcer.py`'s
`_set_disallow_peek` comment) doesn't exist by default. But macOS has its own
version of "the blocked window is still visible without being focused": Mission
Control (F3) and Cmd+Tab both show live previews of open windows/apps, which is a
comparable "cheese" risk to what the Windows peek-disallow code defends against.

**Full-parity mechanism:** rather than minimizing just the one window, use
`NSRunningApplication.hide()` on the blocked app's PID as part of the hard-lock
action:

```python
def _hide_app(pid):
    app = NSRunningApplication.runningApplicationWithProcessIdentifier_(pid)
    if app:
        app.hide()  # equivalent to Cmd+H -- removes every window of this app
                     # from the screen, Mission Control, and Cmd+Tab's window
                     # previews (the app itself still appears in Cmd+Tab's app
                     # list, same as a minimized app would, but with no visible
                     # window content to preview)
```

This is strictly stronger than Windows' single-window peek-disallow (it hides
every window of the app, not just the one that was blocked), which satisfies the
"no differences — or better" bar in the mission statement above. Use `hide()`
in addition to (not instead of) the per-window minimize from the hard-lock
function above, so behavior degrades gracefully if `hide()` ever fails for some
app.

### Restoring focus to the last acceptable app (replaces the `SetForegroundWindow` call)

`_activate` above, called with the last-acceptable process's pid (found the same
way `enforcer.py`'s `_find_window_by_process_name` does today, just via
`psutil`/`NSWorkspace` instead of `win32gui`).

---

## Subsystem 3: Per-Chrome/Edge-profile blocking — RESEARCH SPIKE REQUIRED

**Do not implement this until the spike below is done and its answer is documented
in this file.** This is the one area where the Windows mechanism (`AppUserModelID`
read via `SHGetPropertyStoreForWindow`) genuinely has no macOS analogue — AUMI is a
Windows-only shell concept.

### What needs verifying first

1. **Does macOS Chrome/Edge run every profile in one shared process, or one process
   per profile?** The Windows implementation's entire design (`enforcer.py`'s
   `_PROFILE_DATA_DIRS` comment, `session_manager.MULTI_PROFILE_BROWSER_PROCESSES`)
   exists *because* Windows Chrome/Edge share one process across profiles. If macOS
   Chrome/Edge instead spawn a separate process per open profile, this whole problem
   collapses to the *already-solved* "block by process" case (`is_blocked`/
   `processBlocklist`) — no per-window profile-matching logic would be needed at
   all on macOS.

   Test: open two different Chrome profiles as separate windows on a real Mac, then
   run `ps aux | grep -i "Google Chrome"` and compare PIDs of the two window-owning
   processes. Also check `Activity Monitor` grouped by process, since Chrome's
   subprocess architecture (renderer/GPU helpers) can make raw `ps` output noisy —
   look specifically at the main "Google Chrome" (not "Google Chrome Helper")
   process for each window.

2. **If they DO share one process** (matching Windows' behavior), find what
   macOS exposes per-window to identify the profile. Candidates to check, in order
   of likely reliability:
   - `AXUIElement`'s `kAXDocumentAttribute` on the window (may expose the active
     tab's URL/file path, not the profile — verify, don't assume)
   - The window's title via `CGWindowListCopyWindowInfo`'s `kCGWindowName` — on
     some Chrome versions the profile name appears in the title bar; verify whether
     this is reliable across profile-name changes/multiple same-named profiles, or
     just cosmetic
   - Chrome's AppleScript dictionary (`tell application "Google Chrome" to ...`) —
     check if it exposes a profile identifier per window via `osascript -e
     'tell application "Google Chrome" to get id of every window'` or similar;
     document exactly what properties are available
   - As a last resort: matching against `~/Library/Application Support/Google/
     Chrome/Local State`'s `profile.info_cache` (same file/JSON shape as Windows,
     confirmed to exist on macOS too) to at least enumerate known profiles by name,
     even if live per-window matching turns out to be unreliable

3. **Document the finding** (which mechanism works, with a concrete verified
   example) directly in this file before writing `mac-os/browser_profiles_mac.py`,
   so the actual implementation phase isn't guessing.

### Fallback if no reliable per-window profile signal exists

If step 2 turns up nothing reliable, the parity-preserving fallback is: keep
Chrome/Edge profile-level blocking as a **Windows-only feature**, but make sure the
*base* case (blocking the whole browser via `processBlocklist`) works identically on
both platforms — that part has no macOS-specific risk at all, since it's just
`is_blocked(process_name)`, already platform-agnostic. Flag this explicitly back for
a decision rather than silently shipping a broken/flaky profile-matching heuristic.

---

## Subsystem 4: Notifications (toast → `UNUserNotificationCenter`)

**Replaces:** `calendar_toast.py`'s `winsdk`-based toasts, including the
interactive snooze-button requirement called out in that file's own docstring
(`winsdk` was chosen specifically because reminder snoozing needs an activation
callback — the macOS replacement must preserve this, not regress to a plain
message-only notification).

**New file:** `mac-os/calendar_toast_mac.py`

```python
from UserNotifications import (
    UNUserNotificationCenter,
    UNMutableNotificationContent,
    UNNotificationRequest,
    UNNotificationAction,
    UNNotificationCategory,
    UNAuthorizationOptionAlert,
    UNAuthorizationOptionSound,
)
```

Key implementation notes, not a full solution:

- **This will not work from a bare `python main.py` process.** `UNUserNotificationCenter`
  requires the calling process to be a properly bundled `.app` with a valid bundle
  identifier — test this against the PyInstaller-built `.app` (see Packaging
  section), not a raw interpreter invocation, or you'll chase a phantom bug that's
  actually just "not bundled yet."
- Request authorization once at startup (`requestAuthorizationWithOptions:completionHandler:`),
  same spirit as `calendar_toast.py`'s `set_app_id()` being a required once-per-process
  call.
- Interactive snooze buttons: define a `UNNotificationCategory` with one or more
  `UNNotificationAction`s (e.g. "Snooze 10 min"), attach it to the notification
  content's `categoryIdentifier`, and implement a delegate
  (`userNotificationCenter:didReceiveNotificationResponse:withCompletionHandler:`)
  to receive the button tap. Implementing an Objective-C delegate from Python via
  `pyobjc` means subclassing `NSObject` with `objc.python_method`-decorated methods
  — this is the least Windows-like part of this whole port; budget real time for it,
  don't treat it as a drop-in.

---

## Subsystem 5: Autostart (registry → LaunchAgent)

**Replaces:** `autostart.py`'s `winreg`-based Run-key registration.

**New file:** `mac-os/autostart_mac.py`

macOS equivalent is a per-user LaunchAgent `.plist`:

```python
import os
import subprocess

PLIST_PATH = os.path.expanduser(
    "~/Library/LaunchAgents/com.carmenfocus.app.plist"
)
PLIST_TEMPLATE = """<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.carmenfocus.app</string>
    <key>ProgramArguments</key>
    <array>
        <string>{executable_path}</string>
    </array>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <false/>
</dict>
</plist>
"""


def ensure_autostart_registered():
    """Mirrors autostart.ensure_autostart_registered()'s idempotent
    re-registration behavior -- only rewrite + reload if the plist is
    missing or points somewhere stale."""
    ...  # write PLIST_PATH if missing/stale, then:
    subprocess.run(["launchctl", "load", PLIST_PATH], check=False)
```

`RunAtLoad=True` + `KeepAlive=False` matches the Windows Run-key's actual behavior
(launches once at login; does not respawn if the user quits it manually) — don't
default to `KeepAlive=True`, that would be a behavior *change*, not parity.

`{executable_path}` should point at the PyInstaller-built `.app`'s actual binary
once packaging is done (see below), not a raw `python main.py` invocation — mirror
`autostart.py`'s own reasoning for using `pythonw.exe` (avoid a visible console/dock
icon flash at login).

---

## Subsystem 6: Installed-app enumeration

**Replaces:** `installed_apps.py`'s Start Menu `.lnk` + MSIX scanning.

**New file:** `mac-os/installed_apps_mac.py`

Simpler on macOS — no shortcut/package-manager indirection needed, just scan the
two standard install locations for `.app` bundles and read their bundle identifier:

```python
import os
import plistlib

APP_DIRS = ["/Applications", os.path.expanduser("~/Applications")]


def list_installed_apps():
    apps = []
    for base in APP_DIRS:
        if not os.path.isdir(base):
            continue
        for entry in os.listdir(base):
            if not entry.endswith(".app"):
                continue
            info_plist = os.path.join(base, entry, "Contents", "Info.plist")
            try:
                with open(info_plist, "rb") as f:
                    info = plistlib.load(f)
            except (FileNotFoundError, plistlib.InvalidFileException):
                continue
            executable = info.get("CFBundleExecutable")
            if not executable:
                continue
            apps.append({
                "process_name": executable,
                "display_name": info.get("CFBundleName") or entry.removesuffix(".app"),
            })
    return apps
```

Match this function's return shape exactly against whatever `installed_apps.
list_installed_apps()` returns today (check the current Windows implementation's
exact dict keys before finalizing this — don't assume `process_name`/`display_name`
are the real key names used elsewhere, like the picker dialogs).

---

## Subsystem 7: Tray / menu-bar icon

**No new file needed.** `tray.py` already only uses `pystray`'s public,
platform-abstracted API. Confirm on a real Mac that every `pystray.MenuItem`/
`pystray.Menu` feature this app actually uses (checkboxes, `visible` callables,
submenu nesting — re-read `tray.py` for the exact feature set in use) is supported
by `pystray`'s macOS (Cocoa) backend before assuming this is truly zero-work; if
anything is missing, that's the one place a small platform-specific patch to
`tray.py` itself (not a full `mac-os/` file) would be justified — flag it back
rather than silently guessing at a workaround.

---

## Dependencies

**New file:** `mac-os/requirements-mac.txt`

```
psutil
flask
flask-cors
pystray
pillow
python-dateutil
PySide6
watchdog
supabase
httpx
keyring
python-dotenv
pytest
pytest-qt
pyobjc-core
pyobjc-framework-Cocoa
pyobjc-framework-Quartz
pyobjc-framework-ApplicationServices
pyobjc-framework-UserNotifications
```

This is `requirements.txt` with `pywin32` and `winsdk` removed and the five
`pyobjc-*` packages added. Keep the two files in sync manually going forward (no
shared base file needed for a project this size) — note this explicitly in
`mac-os/README.md` so it doesn't quietly drift.

---

## Packaging

- Build with **PyInstaller**, `--windowed` flag, producing a proper `.app` bundle.
  This is not optional polish — `UNUserNotificationCenter` (Subsystem 4) and
  correct Dock/menu-bar behavior both require a real bundle, not a bare script.
- Set `LSUIElement` to `1` (`True`) in the bundle's `Info.plist` so the app runs as
  an accessory app — no Dock icon, no Cmd+Tab entry, matching how the Windows build
  lives in the system tray without a taskbar button.
- **Gatekeeper**: an unsigned/ad-hoc-signed `.app` will show "cannot be opened,
  unidentified developer" on first launch. Right-click → Open bypasses this once,
  no Apple Developer account required. Proper code-signing + notarization is a
  separate, optional later step (needs a paid Apple Developer account) — out of
  scope for this port unless explicitly requested.
- The **Accessibility permission grant is tied to the exact binary path** — if the
  `.app` gets rebuilt/moved, macOS may treat it as a "new" app and require the
  permission to be re-granted. Worth a test pass: rebuild the app, confirm whether
  the previous grant survives, and document the answer here so users aren't
  surprised by a re-prompt after an update.

---

## Testing

- Existing Windows-only tests (anything that imports `win32gui`/`pywin32`/`winreg`/
  `winsdk` directly, or monkeypatches those symbols) must be marked to skip on
  macOS — use `@pytest.mark.skipif(sys.platform == "darwin", reason="...")`, don't
  delete or weaken them.
- Every new `mac-os/*_mac.py` module needs its own test file (e.g.
  `mac-os/tests/test_enforcer_mac.py`), following the same mocking style already
  established in `tests/test_enforcer.py`/`tests/test_window_tracker.py` (monkeypatch
  the pyobjc/Quartz/AppKit calls the same way those tests monkeypatch `win32gui`) —
  read those existing test files for the pattern before writing new ones, don't
  reinvent the mocking approach.
- CI (if this repo has any) will need a macOS runner to actually execute the new
  test files — check whether one exists before assuming the mac tests will run
  anywhere besides a real Mac.

---

## Suggested phase order

1. **Research spike**: Subsystem 3's two open questions (process-per-profile vs
   shared, and what per-window signal is available if shared). Document the
   findings in this file. No implementation code yet.
2. **Core enforcement**: Subsystems 1 + 2 (window tracking + hard lock/sweep/hide),
   plus the Accessibility-permission onboarding flow. This is the part that makes
   the app actually *do* anything on macOS — highest priority.
3. **Supporting subsystems**: 5 (autostart) + 6 (installed-app enumeration) — both
   fully specified above, no open questions, straightforward once 1+2 exist.
4. **Notifications**: Subsystem 4 — depends on Subsystem 8 (packaging) already
   working, since it can't be tested outside a bundled `.app`.
5. **Packaging**: build the `.app`, verify Gatekeeper/first-launch behavior,
   re-verify Subsystem 4 against the real bundle.
6. **Chrome/Edge profile blocking**: only after the Subsystem 3 spike has a real
   answer — implement or explicitly punt per that section's fallback.
7. **Full pass**: re-run the entire existing test suite (both platform-agnostic and
   the newly-mac-specific tests) on a real Mac, end to end, before calling this
   done.

Stop and report back after each phase, same convention as the rest of this repo's
phased feature work — don't silently barrel through all seven and present it as one
giant diff.

---

## Implementation status (as of the `mac-os-port` branch)

No physical Mac was available while implementing any of this — every finding
below marked "needs a real Mac" is genuinely unverified, not just untested for
form's sake.

### Done

- **Architecture: platform dispatch.** Implemented, but with one deviation from
  this doc's original shim snippet -- see `mac-os/README.md`'s "Deviation from
  the spec's dispatch-shim pattern" section for why the naive top-level
  `if/else` doesn't work (module-level Windows-only imports/ctypes setup crash
  at import time on macOS) and what was done instead (the whole Windows body
  guarded and reindented under one `if sys.platform == "darwin": ... else:`,
  keeping every function physically defined in the same module object so
  existing tests that monkeypatch module globals keep working unchanged).
  Package layout: option A (`mac-os/mac_os/`) was chosen; see the README.
- **Subsystem 1 (window enumeration)**: `mac-os/mac_os/window_tracker_mac.py`.
  `get_active_window`/`list_running_apps` implemented against
  `NSWorkspace`/`CGWindowListCopyWindowInfo`. Window identity: there's no
  macOS "hwnd" -- every function that threads one through (is_blocked_window,
  sweep_minimize, the hard-redirect cooldown dict) now uses a **pid** instead,
  since none of the current call sites need true per-window identity (the one
  that would -- Chrome/Edge per-profile AUMI matching -- is the Subsystem 3
  gap below, deferred). `run_polling_loop` itself was NOT duplicated; it stays
  defined once in `window_tracker.py`, unconditionally, per this doc's own
  note that its loop body is already platform-agnostic.
- **Subsystem 2 (enforcement)**: `mac-os/mac_os/enforcer_mac.py`. Hard lock
  (`AXUIElementSetAttributeValue(..., kAXMinimizedAttribute, True)` on every
  window of the frontmost blocked app + `NSRunningApplication.hide()`),
  sweep-minimize (same primitives, applied across every on-screen window's
  owning pid), restore/unhide, and `is_accessibility_trusted()` (wraps
  `AXIsProcessTrustedWithOptions`) are all implemented. Wired into
  `main.py._check_accessibility_trust()`, called once at startup on darwin: it
  requests Accessibility trust (prompting the system dialog if not yet
  granted) and, if still not trusted, shows a `QMessageBox` explaining why and
  offering to open System Settings directly. **Session-start is also now
  gated on trust status**: `qt_ui/picker_dialogs.py`'s `_TimerDialog._start()`
  (the interactive "Start Session" button) refuses to call
  `session_manager.start_session()` on darwin when
  `enforcer.is_accessibility_trusted()` is false, shows an explanatory dialog
  with an "Open System Settings" button, and returns without starting --
  closing the gap this doc's Subsystem 2 section called the stricter of its
  two allowed options. Deliberately NOT applied to automatic session starts
  (calendar-triggered, review-triggered) -- those have no dialog to show an
  error in, and refusing one silently would be worse than starting with
  enforcement degraded. `soft_lock_warning`'s blackout-rect (covering just the
  offending window in black) is also now implemented, via
  `kAXFocusedWindowAttribute` + `kAXPositionAttribute`/`kAXSizeAttribute`
  decoded through `AXValueGetValue` -- unverified against real pyobjc (see
  "What's NOT verified" below), but no longer a known functional gap in the
  code itself.
- **Subsystem 4 (notifications)**: `mac-os/mac_os/calendar_toast_mac.py`, via
  `UNUserNotificationCenter`. Interactive snooze buttons implemented via a
  lazily-built `NSObject` delegate subclass (real subclassing, as this doc
  warned would be needed) that demultiplexes callbacks by notification-request
  identifier, since `UNUserNotificationCenter` supports only one delegate for
  the whole process. See the module's own docstring for two documented
  tradeoffs found during implementation (the single shared delegate, and
  `setNotificationCategories_` replacing the entire category set on every
  buttoned call rather than adding one).
- **Subsystem 5 (autostart)**: `mac-os/mac_os/autostart_mac.py`, a per-user
  LaunchAgent plist (`RunAtLoad=True`, `KeepAlive=False`, matching the Windows
  Run-key's actual launch-once-don't-respawn behavior). `ProgramArguments`
  currently points at `[sys.executable, .../main.py]` as a stand-in until
  Subsystem 8 packaging exists, per this doc's own note that it should
  eventually point at the bundled `.app`'s binary.
- **Subsystem 6 (installed-app enumeration)**: `mac-os/mac_os/installed_apps_mac.py`,
  scanning `/Applications` + `~/Applications` for `.app` bundles and reading
  `Contents/Info.plist`. Matches `installed_apps.list_installed_apps()`'s
  exact return shape. Known gap: only scans one level deep (matching this
  doc's own snippet) -- a nested bundle (e.g. `/Applications/Utilities/*.app`)
  would be missed; not fixed, flagged here for a decision.
- **Subsystem 7 (tray/menu-bar)**: confirmed by reading `tray.py` that it only
  uses `pystray`'s public, platform-abstracted API (`default=True`,
  `visible=<callable>`) -- both are documented cross-platform pystray
  features, so no shim or patch was made. **Not verified on a real Mac** that
  pystray's Cocoa backend actually renders these the same way its win32
  backend does -- this doc's own Subsystem 7 section asked for that
  confirmation explicitly, and it still needs to happen on real hardware.
- Every `mac-os/mac_os/*_mac.py` module has a matching test file under
  `mac-os/tests/`, exercised on this Windows machine against **stubbed**
  `AppKit`/`Quartz`/`ApplicationServices`/`UserNotifications`/`objc` modules
  (see `mac-os/tests/conftest.py`'s `mac_world` fixture). This catches import
  errors, wrong symbol names, and wrong control flow -- it cannot confirm the
  real frameworks behave as documented. The full existing Windows suite (269
  tests) plus the new mac-os suite (35 tests) both pass together
  unchanged/passing on this branch.

### Subsystem 3 (Chrome/Edge per-profile blocking) -- spike NOT run

The research spike this doc calls for (does macOS Chrome/Edge share one
process across profiles or spawn one per profile; if shared, what per-window
signal identifies the profile) requires a real Mac with Chrome/Edge installed
and multiple profiles open, to inspect via `ps`/Activity Monitor/AppleScript.
**None of that was possible from this Windows dev machine** -- the spike has
not been run at all, not even partially.

Per this doc's own documented fallback for exactly this situation,
per-Chrome/Edge-profile blocking stays a **Windows-only feature for now**:

- `window_tracker_mac.list_browser_profile_windows()` and
  `list_known_browser_profiles()` both always return `[]` (the app picker's
  "block just one browser profile" section will simply show no candidates on
  macOS, which is honest rather than broken).
- `enforcer_mac.get_window_aumi()` always returns `None`,
  `list_known_profile_aumis()` always returns `[]`, and
  `describe_browser_profile_aumi()` falls back to the bare process name.
- `enforcer_mac.is_blocked_window()` collapses to the plain
  `session_manager.is_blocked(process_name)` check -- the base case ("block
  the whole browser") already works identically on both platforms with zero
  changes needed, exactly as this doc predicted.

Whoever picks this up next with access to a real Mac should run the two
verification steps in this doc's Subsystem 3 section, record the findings
directly in this file (replacing this section), and only then implement
`mac-os/mac_os/browser_profiles_mac.py` for real.

### Subsystem 8 (packaging) -- not started

No PyInstaller build has been attempted. Everything downstream of packaging
that this doc calls out as needing a real bundled `.app` to test --
`UNUserNotificationCenter` actually delivering a notification (Subsystem 4),
Gatekeeper's first-launch "unidentified developer" prompt, whether an
Accessibility grant survives a rebuild/move of the `.app` -- is entirely
unverified. `mac-os/requirements-mac.txt` exists and lists the expected
dependency set (`requirements.txt` minus `pywin32`/`winsdk`, plus the five
`pyobjc-*` packages) but has never actually been installed or resolved
against real PyPI on macOS.

### Gaps found on review -- now fixed in code (still unverified on real hardware)

These were flagged as open gaps in an earlier pass of this doc and have
since been closed at the code level. Each still needs real-Mac verification
(nothing here has run outside stubbed pyobjc), but they're no longer *missing
functionality* -- they're implemented and covered by stub-based tests.

- **`session_manager.ALWAYS_ALLOWED_PROCESSES` now includes core macOS shell
  processes.** A separate `_ALWAYS_ALLOWED_PROCESSES_MACOS` set (Finder,
  Dock, SystemUIServer, WindowServer, ControlCenter, NotificationCenter,
  Spotlight, CoreServicesUIAgent, loginwindow, UniversalControl,
  ScreenSaverEngine) is unioned into `ALWAYS_ALLOWED_PROCESSES` only when
  `sys.platform == "darwin"`, so it's a no-op on Windows. Covered by
  `mac-os/tests/test_dispatch_shims.py`.
- **`singleinstance.py`'s lock file now uses an idiomatic macOS path.**
  `LOCK_DIR` is `~/Library/Application Support/CARMEN` on darwin instead of
  falling back to bare `~/CARMEN`. Covered by
  `mac-os/tests/test_dispatch_shims.py`.
- **Session-start is now gated on Accessibility trust, not just a warning.**
  See the Subsystem 2 entry above -- `qt_ui/picker_dialogs.py`'s interactive
  "Start Session" button refuses to start (with an explanatory dialog) when
  untrusted on darwin. Covered by `tests/test_picker_dialogs.py`.
- **Soft lock's blackout-rect is now implemented on macOS.** See the
  Subsystem 2 entry above. Covered by `mac-os/tests/test_enforcer_mac.py`.

### Remaining gaps that genuinely need a real Mac (nothing further to do without one)

- **The `process_name` contract between the picker and enforcement is
  unverified, and it's the one that decides whether blocking works at all.**
  `installed_apps_mac.list_installed_apps()` sources `process_name` from
  `Info.plist`'s `CFBundleExecutable`; that string is what ends up in
  `processBlocklist`. Enforcement then matches it against
  `psutil.Process(pid).name()` read live off the process table. On Windows
  these are the same string by construction (both are the `.exe` basename).
  On macOS they come from two independent sources and are only *probably*
  equal -- an app whose bundle executable name differs from its actual
  running process name (some Electron apps do this) would be silently
  unblockable: the picker shows it, the user selects it, and nothing ever
  fires, with no error anywhere to notice by. This is a pure data-matching
  question about real app bundles, not something a code change or a stub can
  resolve -- it needs verifying on a real Mac before anything else in the
  punch list below: pick a few installed apps (including at least one
  Electron app) and confirm `list_installed_apps()`'s `process_name` for each
  actually equals `psutil.Process(<that running app's pid>).name()`.

### Full punch list for whoever has a real Mac next

1. Verify the `process_name` contract above -- do this first, since every
   other enforcement test downstream assumes it holds.
2. Run the Subsystem 3 spike for real; implement per-profile blocking or
   confirm the Windows-only fallback stays permanent.
3. Install `mac-os/requirements-mac.txt` on a real Mac and confirm it
   resolves (pyobjc version pins may need adjusting).
4. Run `mac-os/tests/` against the *real* pyobjc frameworks, not the stubs in
   this branch -- expect real API-shape mismatches; the stubs only prove the
   Python-side control flow, not the actual ObjC method names/signatures.
   `enforcer_mac._window_rect`'s `AXValueGetValue`/`kAXValueCGPointType`/
   `kAXValueCGSizeType` usage is the least-tested-by-analogy part of this
   port and deserves particular attention.
5. Grant Accessibility permission and confirm `hard_lock_redirect`/
   `sweep_minimize_blocked_windows` actually minimize + hide windows as
   intended, including the "hide() is stronger than Windows' peek-disallow"
   claim (verify Mission Control/Cmd+Tab really show no preview).
6. Build the PyInstaller `.app`, verify Gatekeeper's first-launch flow, and
   re-verify Subsystem 4's notifications only work once bundled (per this
   doc's own warning).
7. Rebuild/move the `.app` once and confirm whether the Accessibility grant
   survives or needs re-granting (document the answer in this file, per the
   Packaging section's own ask).
8. Confirm pystray's Cocoa backend renders `default=True`/`visible=<callable>`
   the same way the win32 backend does (Subsystem 7).
