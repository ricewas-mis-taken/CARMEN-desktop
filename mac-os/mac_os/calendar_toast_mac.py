"""macOS notifications via UNUserNotificationCenter -- replaces
calendar_toast.py's winsdk-based toasts (see that file's module docstring for
why winsdk/UNUserNotificationCenter were chosen over a fire-and-forget
message API: reminder snoozing needs an activation callback).

Known limitation, called out here since it's the least Windows-like part of
this port (see PORT_SPEC.md Subsystem 4): UNUserNotificationCenter supports
exactly ONE delegate for the entire process, not one per notification. To
still let each show_toast() call register its own on_action callback, this
module sets the delegate lazily (once) on the first call that needs one, and
keeps a module-level dict mapping the notification request's unique
identifier -> that call's on_action callback. The single shared delegate
looks up the right callback by the identifier on the incoming response and
pops it out once fired, so the dict doesn't grow unbounded. This is a
reasonable-but-not-idiomatic shortcut; a cleaner implementation might route
through some other app-level dispatch instead of a bespoke lookup table, but
this preserves the exact per-call-callback contract calendar_toast.py's
callers expect.

Likewise, notification categories (which carry the button definitions) are
normally meant to be registered once, upfront, with a fixed/known set of
categories for the whole app. Because this app's buttons vary per call
(different (argument, label) pairs each time), show_toast() instead builds a
*fresh* category with a unique identifier on every call that has buttons.
setNotificationCategories_ always replaces the ENTIRE registered category
set (there's no "add one" API), so this module keeps every category it has
ever built in a module-level dict and re-passes the whole accumulated set on
each call -- dropping an earlier call's category here would silently strip
the Snooze button off any still-undelivered/still-visible earlier toast
(macOS resolves categoryIdentifier -> category at *display* time, not
delivery time, and overlapping calendar reminders are the normal case for
this app). The tradeoff: this set only ever grows for the life of the
process, since a category is never known to be safe to drop. That's a
pragmatic shortcut, not the idiomatic static-registration pattern, and is
worth revisiting on a real device test pass.

This module cannot be exercised against a real UNUserNotificationCenter from
this Windows dev machine (no Mac available, no pyobjc installed) -- see
mac-os/tests/test_calendar_toast_mac.py, which stubs UserNotifications/objc
in sys.modules to exercise the control flow only. Every symbol name and
behavior here is taken directly from PORT_SPEC.md's Subsystem 4 and must be
re-verified against a real bundled .app before shipping (see that file's own
warning: UNUserNotificationCenter requires a properly bundled .app with a
valid bundle identifier -- a bare `python main.py` invocation will not work).
"""
import sys
import uuid

from UserNotifications import (
    UNAuthorizationOptionAlert,
    UNAuthorizationOptionSound,
    UNMutableNotificationContent,
    UNNotificationAction,
    UNNotificationCategory,
    UNNotificationRequest,
    UNUserNotificationCenter,
)

import objc

import os as _os
_repo_root = _os.path.dirname(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
if _repo_root not in sys.path:
    sys.path.insert(0, _repo_root)

from calendar_log import logger

# UNNotificationActionOptionForeground -- documented Apple constant, brings
# the app to the foreground when this action is tapped. Not in the spec's
# given import list, so hardcoded as the well-known integer value (1) rather
# than guessing at an extra import name.
_ACTION_OPTION_FOREGROUND = 1
_CATEGORY_OPTIONS_NONE = 0

# UNNotificationDefaultActionIdentifier -- documented Apple constant string
# fired when the user taps the notification body itself rather than an
# action button. Hardcoded for the same reason as above: it's not in the
# spec's given import list, and this string is a stable public API constant,
# not a guess at undocumented behavior.
_DEFAULT_ACTION_IDENTIFIER = "com.apple.UNNotificationDefaultActionIdentifier"

# notification request identifier -> that call's on_action callback.
# Populated by show_toast(), consumed (and popped) by the shared delegate.
# See module docstring for why this indirection exists. NOTE: an entry that
# the user never acts on (dismissed, or just never tapped) is never popped
# and leaks for the process lifetime -- acceptable parity with the Windows
# version (whose WinRT toast object holds its own callback the same way),
# but not literally bounded.
_pending_actions = {}

# category identifier -> UNNotificationCategory. Every category ever built
# by show_toast() is kept here and the whole set is re-registered on each
# buttoned call (see module docstring's "known limitation" note) -- dropping
# an old entry would strip buttons off any earlier toast still visible to
# the user.
_registered_categories = {}

# The single process-wide delegate instance. Built and assigned lazily on
# first use, since UNUserNotificationCenter only ever wants one -- see
# module docstring.
_delegate = None


def _make_delegate_class():
    """Builds the NSObject subclass lazily (not at import time), since
    subclassing needs objc.lookUpClass("NSObject") to resolve against a real
    (or, in tests, stubbed) ObjC runtime.

    objc.python_method marks _handle_response as a plain Python helper so
    pyobjc doesn't try to expose it as an ObjC selector -- only
    userNotificationCenter_didReceiveNotificationResponse_withCompletionHandler_
    needs to be callable from the ObjC runtime side.
    """
    NSObject = objc.lookUpClass("NSObject")

    class CarmenNotificationDelegate(NSObject):
        def userNotificationCenter_didReceiveNotificationResponse_withCompletionHandler_(
            self, center, response, completion_handler
        ):
            try:
                self._handle_response(response)
            except Exception:
                logger.exception("calendar_toast_mac delegate failed to handle response")
            finally:
                try:
                    completion_handler()
                except Exception:
                    logger.exception("calendar_toast_mac completion_handler failed")

        @objc.python_method
        def _handle_response(self, response):
            try:
                request_id = response.notification().request().identifier()
            except Exception:
                logger.exception("calendar_toast_mac failed to read request identifier")
                return

            callback = _pending_actions.pop(request_id, None)
            if callback is None:
                return

            try:
                action_id = response.actionIdentifier()
            except Exception:
                action_id = None

            # Body tap (the default action) maps to "" per calendar_toast.py's
            # exact Windows-side convention.
            argument = "" if action_id in (None, _DEFAULT_ACTION_IDENTIFIER) else action_id

            try:
                callback(argument)
            except Exception:
                logger.exception("toast on_action callback failed")

    return CarmenNotificationDelegate


def _get_delegate():
    # NOTE: if _make_delegate_class()/alloc().init() ever raises here, this
    # function raises too (show_toast's own try/except catches it), and
    # _delegate stays None -- the next show_toast call will retry building
    # the class from scratch. Whether pyobjc tolerates redefining an ObjC
    # class of the same name a second time in the same process is a real-Mac
    # unknown; not guarded further since this path is only reachable after
    # an unexpected failure.
    global _delegate
    if _delegate is None:
        delegate_class = _make_delegate_class()
        _delegate = delegate_class.alloc().init()
        UNUserNotificationCenter.currentNotificationCenter().setDelegate_(_delegate)
    return _delegate


def set_app_id():
    """Must be called once, early in the process (main.py) -- the macOS
    analogue of the Windows version's AppUserModelID registration. There is
    no AppUserModelID concept on macOS; the once-per-process setup step that
    plays the same structural role is requesting UNUserNotificationCenter
    authorization for alert + sound, which the system otherwise won't grant
    silently."""
    try:
        options = UNAuthorizationOptionAlert | UNAuthorizationOptionSound

        def _on_authorized(granted, error):
            try:
                if error is not None:
                    logger.warning("calendar_toast_mac authorization error: %s", error)
                elif not granted:
                    logger.warning("calendar_toast_mac notification authorization denied")
            except Exception:
                logger.exception("calendar_toast_mac authorization completion handler failed")

        center = UNUserNotificationCenter.currentNotificationCenter()
        center.requestAuthorizationWithOptions_completionHandler_(options, _on_authorized)
    except Exception:
        logger.exception("set_app_id failed")


def show_toast(title, body, buttons=None, on_action=None):
    """buttons: optional list of (argument, label) pairs rendered as
    UNNotificationAction buttons. on_action(argument), if given, is called
    when the user taps a button or the notification body itself (argument is
    "" for a body tap, matching calendar_toast.py's exact convention) --
    invoked from whatever thread UNUserNotificationCenter's delegate callback
    runs on, NOT necessarily Qt's main thread, so on_action itself must not
    touch Qt directly (the caller's responsibility, same as the Windows
    version).

    Every failure here is logged and swallowed rather than raised -- this is
    called from the background scheduler loop, which must never die from a
    notification failure."""
    request_id = None
    try:
        content = UNMutableNotificationContent.alloc().init()
        content.setTitle_(title or "")
        content.setBody_(body or "")

        request_id = str(uuid.uuid4())

        if buttons:
            actions = [
                UNNotificationAction.actionWithIdentifier_title_options_(
                    argument, label, _ACTION_OPTION_FOREGROUND
                )
                for argument, label in buttons
            ]

            category_id = f"carmen_focus_{uuid.uuid4().hex}"
            category = UNNotificationCategory.categoryWithIdentifier_actions_intentIdentifiers_options_(
                category_id, actions, [], _CATEGORY_OPTIONS_NONE
            )
            # setNotificationCategories_ replaces the ENTIRE registered set,
            # so re-pass every category ever built, not just this one --
            # see module docstring's "known limitation" note.
            _registered_categories[category_id] = category
            UNUserNotificationCenter.currentNotificationCenter().setNotificationCategories_(
                set(_registered_categories.values())
            )
            content.setCategoryIdentifier_(category_id)

        if on_action is not None:
            _pending_actions[request_id] = on_action
            _get_delegate()

        request = UNNotificationRequest.requestWithIdentifier_content_trigger_(
            request_id, content, None
        )

        def _on_added(error):
            try:
                if error is not None:
                    logger.warning("calendar_toast_mac addNotificationRequest error: %s", error)
            except Exception:
                logger.exception("calendar_toast_mac add-request completion handler failed")

        UNUserNotificationCenter.currentNotificationCenter().addNotificationRequest_withCompletionHandler_(
            request, _on_added
        )
    except Exception:
        logger.exception("show_toast failed: %s / %s", title, body)
        # Don't leak a registered callback for a toast that never actually
        # got scheduled.
        if request_id is not None:
            _pending_actions.pop(request_id, None)
