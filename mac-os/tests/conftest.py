"""Shared fixtures for mac_os module tests.

No pyobjc is installed on this (Windows) machine, so AppKit/Quartz/
ApplicationServices are stubbed into sys.modules before the module under
test is imported. This can only catch import errors, wrong symbol names,
and wrong control flow against the *documented* shape of these APIs -- it
cannot confirm the real frameworks behave this way. Every *_mac.py module
still needs a real first-run pass on an actual Mac before shipping (see
mac-os/README.md).
"""
import copy
import os
import sys
import types

import pytest

# So `import enforcer_mac` (etc, unqualified) and `from mac_os import
# enforcer_mac` both resolve, and so enforcer_mac.py's own repo-root imports
# (qt_gui_thread, qt_ui.enforcer_overlay, session_manager) resolve too.
_MAC_OS_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_REPO_ROOT = os.path.dirname(_MAC_OS_DIR)
for _p in (_MAC_OS_DIR, _REPO_ROOT):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import config
import session_history
import session_manager


@pytest.fixture
def isolate_config(tmp_path, monkeypatch):
    """Duplicated from tests/conftest.py's fixture of the same name --
    mac-os/tests/ is a separate pytest rootdir-relative directory, so
    conftest.py fixtures don't cross over from tests/ to here. Keep these
    two in sync if session_manager/config's on-disk paths ever change."""
    monkeypatch.setattr(config, "CONFIG_PATH", str(tmp_path / "config.json"))
    yield


@pytest.fixture
def isolate_state(isolate_config, tmp_path, monkeypatch):
    """Duplicated from tests/conftest.py's fixture of the same name -- see
    isolate_config's docstring above for why."""
    monkeypatch.setattr(session_manager, "STATE_PATH", str(tmp_path / "session_state.json"))
    monkeypatch.setattr(session_history, "HISTORY_PATH", str(tmp_path / "session_history.json"))

    fresh_state = {
        "isActive": False,
        "startTime": None,
        "endTime": None,
        "lockMode": "soft",
        "processBlocklist": [],
        "domainWhitelist": [],
        "violationCount": 0,
        "violationLog": [],
        "lastAcceptableProcess": None,
        "domainWhitelistAdditions": [],
        "processBlocklistExceptions": [],
        "isPaused": False,
        "pausedAt": None,
        "frozenSecondsRemaining": None,
        "source": "manual",
        "eventId": None,
        "eventTitle": None,
        "reviewProblemName": None,
        "reviewSubjectName": None,
        "reviewProblemId": None,
        "isBurnout": False,
        "blockedBrowserProfiles": [],
    }
    monkeypatch.setattr(session_manager, "_state", copy.deepcopy(fresh_state))
    monkeypatch.setattr(session_manager, "_open_violation_index", {"process": None, "domain": None})
    monkeypatch.setattr(session_manager, "_pending_natural_end", {"value": None})

    yield


class FakeAXRef:
    """Stands in for an AXUIElementRef -- either an application-level ref
    (created via AXUIElementCreateApplication) or a single window ref (one
    entry in kAXWindowsAttribute's returned list)."""

    def __init__(self, pid=None, minimized=False):
        self.pid = pid
        self.minimized = minimized


class FakeRunningApp:
    def __init__(self, pid):
        self.pid = pid
        self.hidden = False
        self.hide_calls = 0
        self.unhide_calls = 0
        self.activate_calls = 0

    def processIdentifier(self):
        return self.pid

    def hide(self):
        self.hidden = True
        self.hide_calls += 1

    def unhide(self):
        self.hidden = False
        self.unhide_calls += 1

    def activateWithOptions_(self, options):
        self.activate_calls += 1


class MacWorld:
    """Everything the fake AppKit/Quartz/ApplicationServices modules read
    from and record into, exposed to tests for setup + assertions.

    apps: pid -> FakeRunningApp
    windows: pid -> [FakeAXRef, ...] (this pid's windows, for the
        Accessibility-API minimize/is-minimized calls)
    on_screen_windows: [{"kCGWindowOwnerPID": pid, "kCGWindowName": str,
        "kCGWindowLayer": int}, ...] -- CGWindowListCopyWindowInfo's return
    frontmost_pid: the pid NSWorkspace should report as frontmost, or None
    ax_trusted: bool AXIsProcessTrustedWithOptions should return
    """

    def __init__(self):
        self.apps = {}
        self.windows = {}
        self.on_screen_windows = []
        self.frontmost_pid = None
        self.ax_trusted = True

    def add_app(self, pid, minimized_windows=None):
        app = FakeRunningApp(pid)
        self.apps[pid] = app
        self.windows[pid] = [FakeAXRef(pid, m) for m in (minimized_windows or [False])]
        return app


@pytest.fixture
def mac_world(monkeypatch):
    world = MacWorld()

    # --- ApplicationServices ---
    def ax_is_process_trusted_with_options(options):
        return world.ax_trusted

    def ax_ui_element_create_application(pid):
        return FakeAXRef(pid=pid)

    def ax_ui_element_copy_attribute_value(ref, attribute, _placeholder):
        if attribute == "kAXWindowsAttribute":
            windows = world.windows.get(ref.pid, [])
            return (0, list(windows))
        if attribute == "kAXMinimizedAttribute":
            return (0, bool(ref.minimized))
        return (1, None)

    def ax_ui_element_set_attribute_value(ref, attribute, value):
        if attribute == "kAXMinimizedAttribute":
            ref.minimized = bool(value)
        return 0

    application_services = types.ModuleType("ApplicationServices")
    application_services.AXIsProcessTrustedWithOptions = ax_is_process_trusted_with_options
    application_services.AXUIElementCreateApplication = ax_ui_element_create_application
    application_services.AXUIElementCopyAttributeValue = ax_ui_element_copy_attribute_value
    application_services.AXUIElementSetAttributeValue = ax_ui_element_set_attribute_value
    application_services.kAXWindowsAttribute = "kAXWindowsAttribute"
    application_services.kAXMinimizedAttribute = "kAXMinimizedAttribute"

    # --- AppKit ---
    class FakeNSRunningApplication:
        @staticmethod
        def runningApplicationWithProcessIdentifier_(pid):
            return world.apps.get(pid)

    class FakeFrontmostApp:
        def __init__(self, pid):
            self._pid = pid

        def processIdentifier(self):
            return self._pid

    class FakeNSWorkspace:
        @staticmethod
        def sharedWorkspace():
            return FakeNSWorkspace()

        def frontmostApplication(self):
            if world.frontmost_pid is None:
                return None
            return FakeFrontmostApp(world.frontmost_pid)

    appkit = types.ModuleType("AppKit")
    appkit.NSRunningApplication = FakeNSRunningApplication
    appkit.NSWorkspace = FakeNSWorkspace
    appkit.NSApplicationActivateIgnoringOtherApps = 1

    # --- Quartz ---
    def cg_window_list_copy_window_info(options, window_id):
        return list(world.on_screen_windows)

    quartz = types.ModuleType("Quartz")
    quartz.CGWindowListCopyWindowInfo = cg_window_list_copy_window_info
    quartz.kCGWindowListOptionOnScreenOnly = 1
    quartz.kCGNullWindowID = 0

    monkeypatch.setitem(sys.modules, "ApplicationServices", application_services)
    monkeypatch.setitem(sys.modules, "AppKit", appkit)
    monkeypatch.setitem(sys.modules, "Quartz", quartz)

    # Every *_mac module must be imported (or re-imported) only after the
    # stubs above are installed -- see each test file's own import dance.
    for name in ("enforcer_mac", "window_tracker_mac"):
        sys.modules.pop(name, None)
        sys.modules.pop(f"mac_os.{name}", None)

    yield world
