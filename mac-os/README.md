# mac-os/

macOS platform implementation for Carmen Focus. See [PORT_SPEC.md](PORT_SPEC.md)
for the full port spec, phase order, and (once run) the Subsystem 3 research
spike findings.

## Package layout

`mac-os` (hyphen) is the container directory at the repo root, as required by
the port spec's hard constraint 1. Hyphens aren't legal in a Python package
name, so the actual importable package lives one level down, at
`mac-os/mac_os/` (underscore) -- this is option A from the spec's two
suggested layouts. Real modules live at `mac-os/mac_os/*_mac.py`; tests live
at `mac-os/tests/`.

For `from mac_os import enforcer_mac` (etc.) to resolve, the `mac-os/`
directory itself must be on `sys.path`. Rather than relying on `main.py` to
add it once at process startup (which would leave a bare `pytest` collection
run or any standalone import broken), **every dispatch shim in the repo root
inserts it itself**, on first import:

```python
if sys.platform == "darwin":
    import os as _os
    _mac_os_dir = _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "mac-os")
    if _mac_os_dir not in sys.path:
        sys.path.insert(0, _mac_os_dir)
    from mac_os import enforcer_mac as _mac
    ...
```

This is self-contained and idempotent (the `not in sys.path` guard), so it
works regardless of entry point.

## Deviation from the spec's dispatch-shim pattern

The spec's suggested shim was a top-level `if sys.platform == "darwin": ...
else: <existing Windows implementation, unchanged>`. That doesn't actually
work: `enforcer.py`, `window_tracker.py`, `autostart.py`, `installed_apps.py`,
and `calendar_toast.py` all have **module-level** Windows-only calls --
`import win32gui`, `import winreg`, `ctypes.windll.ole32`, `SetWinEventHook`
proto setup, etc. -- that run at *import time*, before any `if` inside a
function body could help. A plain top-level `else:` after those imports
would still crash macOS at the `import enforcer` statement itself.

The actual fix applied to all five files: guard the **entire Windows-specific
body** (imports, module-level ctypes/COM setup, and every function/class
definition) inside one `if sys.platform == "darwin": ... else: <original
body, reindented>` block, with the macOS functions bound to the same names in
the `if` branch. This is a bigger textual diff than the spec's one-liner (the
whole Windows body gets reindented one level), but it is a **zero logic
change** on Windows -- confirmed by running the full existing test suite
before and after (269 passed, unchanged) -- and it preserves something the
naive pattern would have silently broken: several existing tests
monkeypatch module-level Windows globals directly (e.g.
`monkeypatch.setattr(window_tracker, "POLL_INTERVAL_SECONDS", ...)`,
`monkeypatch.setattr(enforcer.win32gui, "EnumWindows", ...)`). That only
works if the functions being tested are still physically defined inside that
same module object -- moving the Windows implementation out to a separate
`enforcer_win.py` file (a cleaner-looking split considered and rejected)
would have broken every one of those tests, since a function's module
globals are fixed at *def* time to whichever module it's textually defined
in, not wherever its name is later re-exported to.

`window_tracker.py`'s `run_polling_loop` is the one exception: per the spec,
its poll-loop logic is genuinely platform-agnostic (it only calls
`get_active_window()` and `enforcer.*`, both already dispatched), so it's
defined once, unconditionally, after the `if/else` -- not duplicated in
`window_tracker_mac.py`.

`qt_ui/main_window.py` and `main.py` needed smaller, targeted guards instead
of the full-body wrap, since only a small part of each file is Windows-only
(see those files directly).

## What's NOT verified

No physical Mac was available while writing this port. Every `*_mac.py`
module and its tests were written against the exact contracts read from the
current Windows source (signatures, return dict keys, etc.) and, where
possible, exercised on Windows against **stubbed** `AppKit`/`Quartz`/
`ApplicationServices`/`UserNotifications` modules (see `mac-os/tests/`) --
that catches import errors, wrong symbol names, and wrong control flow, but
it cannot confirm the real pyobjc APIs behave as documented, or that any of
this actually works against real macOS window-manager state. Treat every
`*_mac.py` module as needing a real first-run pass on a Mac before shipping.
The Subsystem 3 Chrome/Edge-profile spike and the Subsystem 8 (packaging)
Gatekeeper/notarization checks are Mac-only research that hasn't been run at
all -- see PORT_SPEC.md's appended findings section.

## Dependencies

`requirements-mac.txt` is `requirements.txt` with `pywin32`/`winsdk` removed
and the `pyobjc-*` packages added. Keep the two files in sync by hand -- no
shared base file, this project isn't big enough to need one.
