"""Tests for mac_os.calendar_toast_mac, run on Windows against fake stand-ins
for UserNotifications/objc (neither exists on this machine -- no pyobjc, no
Mac). These tests only prove the module's own control flow is correct against
the *documented* pyobjc/UserNotifications call shapes from PORT_SPEC.md --
they cannot confirm real pyobjc behaves as documented. See
mac-os/README.md's "What's NOT verified" section.
"""
import sys
import types
import importlib

import pytest


MODULE_NAME = "mac_os.calendar_toast_mac"


class FakeUNMutableNotificationContent:
    def __init__(self):
        self.title = None
        self.body = None
        self.categoryIdentifier = None

    @classmethod
    def alloc(cls):
        return cls()

    def init(self):
        return self

    def setTitle_(self, title):
        self.title = title

    def setBody_(self, body):
        self.body = body

    def setCategoryIdentifier_(self, category_id):
        self.categoryIdentifier = category_id


class FakeUNNotificationRequest:
    def __init__(self, identifier, content, trigger):
        self.identifier = identifier
        self.content = content
        self.trigger = trigger

    @classmethod
    def requestWithIdentifier_content_trigger_(cls, identifier, content, trigger):
        return cls(identifier, content, trigger)


class FakeUNNotificationAction:
    def __init__(self, identifier, title, options):
        self.identifier = identifier
        self.title = title
        self.options = options

    @classmethod
    def actionWithIdentifier_title_options_(cls, identifier, title, options):
        return cls(identifier, title, options)


class FakeUNNotificationCategory:
    def __init__(self, identifier, actions, intent_identifiers, options):
        self.identifier = identifier
        self.actions = actions
        self.intent_identifiers = intent_identifiers
        self.options = options

    @classmethod
    def categoryWithIdentifier_actions_intentIdentifiers_options_(
        cls, identifier, actions, intent_identifiers, options
    ):
        return cls(identifier, actions, intent_identifiers, options)


class FakeNotification:
    def __init__(self, request):
        self._request = request

    def request(self):
        return self._request


class FakeRequestRef:
    def __init__(self, identifier):
        self._identifier = identifier

    def identifier(self):
        return self._identifier


class FakeResponse:
    def __init__(self, request_id, action_identifier):
        self._notification = FakeNotification(FakeRequestRef(request_id))
        self._action_identifier = action_identifier

    def notification(self):
        return self._notification

    def actionIdentifier(self):
        return self._action_identifier


class FakeUNUserNotificationCenter:
    """Singleton fake mirroring UNUserNotificationCenter.currentNotificationCenter()."""

    _instance = None

    def __init__(self):
        self.delegate = None
        self.authorization_calls = []
        self.categories_registered = []
        self.added_requests = []
        self.raise_on_add = False

    @classmethod
    def currentNotificationCenter(cls):
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def setDelegate_(self, delegate):
        self.delegate = delegate

    def requestAuthorizationWithOptions_completionHandler_(self, options, handler):
        self.authorization_calls.append(options)
        handler(True, None)

    def setNotificationCategories_(self, categories):
        self.categories_registered.append(categories)

    def addNotificationRequest_withCompletionHandler_(self, request, handler):
        if self.raise_on_add:
            raise RuntimeError("boom")
        self.added_requests.append(request)
        handler(None)


class FakeNSObject:
    """Stand-in base class for objc.lookUpClass("NSObject")."""

    @classmethod
    def alloc(cls):
        return cls()

    def init(self):
        return self


def _python_method(fn):
    # Real objc.python_method just marks the function; identity is fine here.
    return fn


@pytest.fixture
def fresh_module(monkeypatch):
    """Stubs UserNotifications + objc in sys.modules, then imports (or
    reloads) mac_os.calendar_toast_mac fresh so each test gets an isolated
    module-level state (_pending_actions, _delegate)."""
    fake_center_class = FakeUNUserNotificationCenter
    fake_center_class._instance = None  # reset singleton per test

    un_module = types.ModuleType("UserNotifications")
    un_module.UNAuthorizationOptionAlert = 1 << 2
    un_module.UNAuthorizationOptionSound = 1 << 1
    un_module.UNMutableNotificationContent = FakeUNMutableNotificationContent
    un_module.UNNotificationAction = FakeUNNotificationAction
    un_module.UNNotificationCategory = FakeUNNotificationCategory
    un_module.UNNotificationRequest = FakeUNNotificationRequest
    un_module.UNUserNotificationCenter = fake_center_class

    objc_module = types.ModuleType("objc")
    objc_module.python_method = _python_method
    objc_module.lookUpClass = lambda name: FakeNSObject

    monkeypatch.setitem(sys.modules, "UserNotifications", un_module)
    monkeypatch.setitem(sys.modules, "objc", objc_module)

    # calendar_toast_mac inserts the repo root onto sys.path itself and
    # imports calendar_log from there; that import works unmodified on
    # Windows (calendar_log.py has no Windows deps), so no extra stubbing
    # needed for it.
    sys.modules.pop(MODULE_NAME, None)
    module = importlib.import_module(MODULE_NAME)
    yield module
    sys.modules.pop(MODULE_NAME, None)


def test_set_app_id_requests_authorization(fresh_module):
    fresh_module.set_app_id()

    center = fresh_module.UNUserNotificationCenter.currentNotificationCenter()
    assert len(center.authorization_calls) == 1
    options = center.authorization_calls[0]
    assert options == (
        fresh_module.UNAuthorizationOptionAlert | fresh_module.UNAuthorizationOptionSound
    )


def test_set_app_id_never_raises(fresh_module, monkeypatch):
    def _boom(*args, **kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(
        fresh_module.UNUserNotificationCenter.currentNotificationCenter(),
        "requestAuthorizationWithOptions_completionHandler_",
        _boom,
    )

    fresh_module.set_app_id()  # must not raise


def test_show_toast_basic_builds_request(fresh_module):
    fresh_module.show_toast("t", "b")

    center = fresh_module.UNUserNotificationCenter.currentNotificationCenter()
    assert len(center.added_requests) == 1
    request = center.added_requests[0]
    assert request.content.title == "t"
    assert request.content.body == "b"
    assert request.content.categoryIdentifier is None
    assert request.trigger is None
    assert isinstance(request.identifier, str) and request.identifier


def test_show_toast_with_buttons_registers_category_and_fires_callback(fresh_module):
    received = []

    def cb(argument):
        received.append(argument)

    fresh_module.show_toast(
        "t", "b", buttons=[("snooze10", "Snooze 10 min")], on_action=cb
    )

    center = fresh_module.UNUserNotificationCenter.currentNotificationCenter()
    assert len(center.categories_registered) == 1
    request = center.added_requests[0]
    assert request.content.categoryIdentifier is not None
    assert center.delegate is not None

    # Simulate the user tapping the "snooze10" action button: manually
    # invoke the fake delegate's response handler.
    response = FakeResponse(request.identifier, "snooze10")
    completion_calls = []
    center.delegate.userNotificationCenter_didReceiveNotificationResponse_withCompletionHandler_(
        center, response, lambda: completion_calls.append(True)
    )

    assert received == ["snooze10"]
    assert completion_calls == [True]
    # callback popped after firing -- no leak.
    assert request.identifier not in fresh_module._pending_actions


def test_show_toast_body_tap_maps_to_empty_string(fresh_module):
    received = []
    fresh_module.show_toast("t", "b", buttons=[("a", "A")], on_action=received.append)

    center = fresh_module.UNUserNotificationCenter.currentNotificationCenter()
    request = center.added_requests[0]

    response = FakeResponse(request.identifier, fresh_module._DEFAULT_ACTION_IDENTIFIER)
    center.delegate.userNotificationCenter_didReceiveNotificationResponse_withCompletionHandler_(
        center, response, lambda: None
    )

    assert received == [""]


def test_show_toast_swallows_exceptions(fresh_module):
    center = fresh_module.UNUserNotificationCenter.currentNotificationCenter()
    center.raise_on_add = True

    fresh_module.show_toast("t", "b")  # must not raise

    assert center.added_requests == []


def test_show_toast_multiple_pending_callbacks_demultiplexed_correctly(fresh_module):
    received_a = []
    received_b = []

    fresh_module.show_toast("t1", "b1", buttons=[("x", "X")], on_action=received_a.append)
    center = fresh_module.UNUserNotificationCenter.currentNotificationCenter()
    request_a = center.added_requests[-1]
    delegate_after_first = center.delegate

    fresh_module.show_toast("t2", "b2", buttons=[("y", "Y")], on_action=received_b.append)
    request_b = center.added_requests[-1]

    # Exactly one shared delegate for the whole process.
    assert center.delegate is delegate_after_first

    # Both categories must still be registered -- registering the second
    # toast's category must not silently drop the first toast's Snooze
    # button (macOS resolves categoryIdentifier -> category at display
    # time, not delivery time, so an overlapping still-visible earlier
    # toast would otherwise lose its button).
    last_categories = center.categories_registered[-1]
    assert {c.identifier for c in last_categories} == {
        request_a.content.categoryIdentifier,
        request_b.content.categoryIdentifier,
    }

    # Firing B's response must only invoke cb2, not cb1.
    center.delegate.userNotificationCenter_didReceiveNotificationResponse_withCompletionHandler_(
        center, FakeResponse(request_b.identifier, "y"), lambda: None
    )
    assert received_b == ["y"]
    assert received_a == []

    # Firing A's response afterward must only invoke cb1.
    center.delegate.userNotificationCenter_didReceiveNotificationResponse_withCompletionHandler_(
        center, FakeResponse(request_a.identifier, "x"), lambda: None
    )
    assert received_a == ["x"]
    assert received_b == ["y"]


def test_show_toast_exception_does_not_leak_pending_callback(fresh_module):
    center = fresh_module.UNUserNotificationCenter.currentNotificationCenter()
    center.raise_on_add = True

    fresh_module.show_toast("t", "b", buttons=[("a", "A")], on_action=lambda arg: None)

    assert fresh_module._pending_actions == {}
