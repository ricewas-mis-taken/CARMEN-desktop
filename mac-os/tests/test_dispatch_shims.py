"""Regression coverage for the repo-root dispatch shims themselves
(enforcer.py, window_tracker.py, autostart.py, installed_apps.py,
calendar_toast.py) -- NOT the mac_os/*_mac.py implementations, which have
their own test files.

Every *_mac.py module is dead code on this Windows machine unless something
actually forces sys.platform == "darwin" and re-imports the dispatch shim
through that branch. Without a test like this, a wiring bug in the shim
itself -- a name never bound in the darwin branch that's needed later, a
typo in an attribute name, a missing import -- is invisible: the module
still imports fine (Python doesn't check that every code path defines every
name), and the bug only surfaces as silent do-nothing behavior on a real
Mac. This is exactly the failure class that let a NameError in
window_tracker.run_polling_loop (enforcer/session_manager only imported
inside the never-taken Windows branch) slip through 269+35 passing tests
during this port -- caught by review, not by any test, which is why this
file exists now.

Each test monkeypatches sys.platform to "darwin", reloads the shim module (so
its top-level code re-executes through the darwin branch), asserts on the
result, and restores the real platform + reloads back in a finally block --
these are the same long-lived module objects every other test file imports,
so leaving one stuck mid-test in "darwin state" would corrupt every test that
runs after it in the same process.
"""
import importlib
import sys
import types
import urllib.request  # noqa: F401 -- see note below, must be imported before any test fakes sys.platform

import pytest

# CPython's own urllib/request.py does a module-level `if sys.platform ==
# "darwin": from _scproxy import ...` on its *first* import (a real macOS-only
# C extension) -- if that first-ever import happens while a test has faked
# sys.platform to "darwin" (as_darwin below), it crashes with
# ModuleNotFoundError, regardless of anything in this repo's own code.
# singleinstance.py imports urllib.request, so reloading it under a faked
# darwin platform can trigger this. Forcing the import here, at collection
# time, under the real platform, guarantees urllib.request is already fully
# initialized (and cached in sys.modules) before any as_darwin test runs --
# this bit only when this test file ran in isolation; running the full suite
# happened to import urllib.request some other way first, which is exactly
# the kind of order-dependent fragility this import exists to remove.


_DISPATCH_MODULES = [
    "enforcer", "window_tracker", "autostart", "installed_apps", "calendar_toast",
    # Not dispatch shims (import no mac_os module), but both branch on
    # sys.platform at import time (ALWAYS_ALLOWED_PROCESSES's macOS union in
    # session_manager.py, LOCK_DIR's macOS path in singleinstance.py), so
    # both need the same reload-back treatment.
    "session_manager",
    "singleinstance",
]


@pytest.fixture
def as_darwin():
    """Sets sys.platform to "darwin" for the test body, then -- critically --
    restores it and reloads every dispatch shim back to the real platform's
    branch *before* this fixture finishes tearing down, not relying on
    pytest's `monkeypatch` fixture for the restore.

    monkeypatch.setattr(sys, "platform", ...) doesn't work for this: fixture
    teardown order is reverse of setup order, so if this fixture depended on
    `monkeypatch`, monkeypatch would still be undone *after* this fixture's
    own post-yield code runs -- meaning sys.platform would still read
    "darwin" while trying to reload modules back, reloading them into the
    darwin branch *again* instead of restoring them. A plain manual
    save/restore sequences this correctly: restore the real value first,
    then reload, all within this fixture's own teardown."""
    real_platform = sys.platform
    sys.platform = "darwin"
    yield
    sys.platform = real_platform
    for name in _DISPATCH_MODULES:
        if name in sys.modules:
            importlib.reload(sys.modules[name])


def _reload(module_name):
    module = importlib.import_module(module_name)
    return importlib.reload(module)


def test_window_tracker_darwin_branch_resolves_names_run_polling_loop_needs(
    as_darwin, mac_world
):
    """The exact bug this file exists to catch: run_polling_loop references
    enforcer.* and session_manager.* at call time, but is defined once,
    unconditionally, after window_tracker.py's if/else -- those two imports
    must be hoisted above that if/else, not left inside the Windows-only
    branch, or every call raises NameError (silently swallowed by the loop's
    own try/except -- see run_polling_loop's docstring)."""
    try:
        window_tracker = _reload("window_tracker")
        assert hasattr(window_tracker, "enforcer")
        assert hasattr(window_tracker, "session_manager")
        assert callable(window_tracker.get_active_window)
        assert callable(window_tracker.run_polling_loop)
        assert window_tracker.POLL_INTERVAL_SECONDS > 0

        # A real call, not just an attribute check -- proves get_active_window
        # actually runs through mac_os.window_tracker_mac without raising.
        window = window_tracker.get_active_window()
        assert set(window.keys()) == {"title", "process_name", "pid", "hwnd"}
    finally:
        _reload("window_tracker")


def test_session_manager_exempts_macos_shell_processes_under_darwin(as_darwin):
    session_manager = _reload("session_manager")
    try:
        assert session_manager.is_exempt("Finder")
        assert session_manager.is_exempt("Dock")
        assert session_manager.is_exempt("WindowServer")
        assert not session_manager.is_exempt("discord")
    finally:
        _reload("session_manager")


def test_session_manager_macos_set_does_not_leak_into_windows_branch():
    """Regression guard: the macOS-only exemptions must not be visible when
    sys.platform is the real (Windows) value -- they're unioned in only
    inside the `if sys.platform == "darwin":` branch."""
    session_manager = _reload("session_manager")
    assert not session_manager.is_exempt("Finder")
    assert not session_manager.is_exempt("Dock")


def test_singleinstance_lock_dir_is_idiomatic_on_macos(as_darwin):
    singleinstance = _reload("singleinstance")
    try:
        assert "Library" in singleinstance.LOCK_DIR
        assert "Application Support" in singleinstance.LOCK_DIR
        assert "CARMEN" in singleinstance.LOCK_DIR
    finally:
        _reload("singleinstance")


def test_enforcer_darwin_branch_resolves_public_api(as_darwin, mac_world):
    try:
        enforcer = _reload("enforcer")
        for name in (
            "is_accessibility_trusted",
            "is_blocked_window",
            "soft_lock_warning",
            "hard_lock_redirect",
            "sweep_minimize_blocked_windows",
            "show_blocked_notice",
            "restore_window_for_process",
            "restore_all_taskbar_previews",
            "get_window_aumi",
            "list_known_profile_aumis",
            "describe_browser_profile_aumi",
        ):
            assert callable(getattr(enforcer, name)), f"enforcer.{name} not callable under darwin"

        # A real call each -- proves these aren't just present but wired to
        # something that runs without raising.
        assert enforcer.get_window_aumi(1234) is None
        assert enforcer.list_known_profile_aumis("chrome.exe") == []
        assert enforcer.describe_browser_profile_aumi("chrome.exe", None) == "chrome.exe"
    finally:
        _reload("enforcer")


def test_autostart_darwin_branch_resolves_and_runs(tmp_path, as_darwin, monkeypatch):
    """No pyobjc dependency at all for this one -- autostart_mac.py is pure
    os/plistlib/subprocess -- so this exercises a real call, not just a
    presence check.

    tmp_path is listed *before* as_darwin deliberately -- pytest sets up
    same-scope independent fixtures in parameter order, and pytest's own
    tmp_path fixture calls os.getuid() internally on a non-Windows
    sys.platform (for a POSIX permission check); if as_darwin ran first and
    faked sys.platform to "darwin" before tmp_path's setup, tmp_path would
    call a getuid() that doesn't exist on the real (Windows) os module."""
    try:
        autostart = _reload("autostart")
        assert callable(autostart.ensure_autostart_registered)

        import mac_os.autostart_mac as autostart_mac
        monkeypatch.setattr(autostart_mac, "PLIST_PATH", str(tmp_path / "com.carmenfocus.app.plist"))
        recorded = []
        monkeypatch.setattr(
            autostart_mac.subprocess, "run", lambda *a, **k: recorded.append(a)
        )

        autostart.ensure_autostart_registered()

        assert (tmp_path / "com.carmenfocus.app.plist").exists()
        assert recorded, "expected launchctl load to be invoked"
    finally:
        _reload("autostart")


def test_installed_apps_darwin_branch_resolves_and_runs(tmp_path, as_darwin, monkeypatch):
    # tmp_path listed before as_darwin -- see test_autostart's comment above.
    try:
        installed_apps = _reload("installed_apps")
        assert callable(installed_apps.list_installed_apps)

        import mac_os.installed_apps_mac as installed_apps_mac
        empty_dir = tmp_path / "Applications"
        empty_dir.mkdir()
        monkeypatch.setattr(installed_apps_mac, "APP_DIRS", [str(empty_dir)])

        assert installed_apps.list_installed_apps() == []
    finally:
        _reload("installed_apps")


def test_calendar_toast_darwin_branch_resolves_and_runs(as_darwin, monkeypatch):
    """Minimal UserNotifications/objc stubs -- just enough that the darwin
    branch imports and set_app_id() can run one full call without raising.
    mac-os/tests/test_calendar_toast_mac.py covers show_toast's actual
    behavior in depth; this only proves the *shim* wiring, not the
    implementation."""
    un_module = types.ModuleType("UserNotifications")

    class FakeCenter:
        def requestAuthorizationWithOptions_completionHandler_(self, options, handler):
            handler(True, None)

        def setDelegate_(self, delegate):
            pass

        def setNotificationCategories_(self, categories):
            pass

        def addNotificationRequest_withCompletionHandler_(self, request, handler):
            handler(None)

    un_module.UNUserNotificationCenter = types.SimpleNamespace(
        currentNotificationCenter=staticmethod(lambda: FakeCenter())
    )
    un_module.UNAuthorizationOptionAlert = 1
    un_module.UNAuthorizationOptionSound = 2
    un_module.UNMutableNotificationContent = None
    un_module.UNNotificationAction = None
    un_module.UNNotificationCategory = None
    un_module.UNNotificationRequest = None

    objc_module = types.ModuleType("objc")
    objc_module.python_method = lambda f: f

    monkeypatch.setitem(sys.modules, "UserNotifications", un_module)
    monkeypatch.setitem(sys.modules, "objc", objc_module)

    try:
        calendar_toast = _reload("calendar_toast")
        assert callable(calendar_toast.set_app_id)
        assert callable(calendar_toast.show_toast)

        calendar_toast.set_app_id()  # must not raise
    finally:
        sys.modules.pop("mac_os.calendar_toast_mac", None)
        _reload("calendar_toast")
