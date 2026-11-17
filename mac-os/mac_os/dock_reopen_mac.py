"""Clicking Carmen's Dock icon (or opening the app again) while it is running opens the main window.

macOS sends the running app a "reopen" request for that. Qt's application delegate does not turn it into
anything the app can react to, so this adds the delegate method itself."""
import objc
from AppKit import NSApp

_SELECTOR = b"applicationShouldHandleReopen:hasVisibleWindows:"


def install(open_window):
    """open_window() is called (on the main thread) for every Dock click. Returns True if hooked."""
    delegate = NSApp().delegate()
    if delegate is None:
        return False

    def handle(self, sender, has_visible_windows):
        try:
            open_window()
        except Exception:
            import logging
            logging.getLogger(__name__).exception("dock reopen failed")
        return True

    method = objc.selector(handle, selector=_SELECTOR, signature=b"B@:@B")
    cls = delegate.__class__
    # Qt's delegate may already have a method of this name; adding ours then replaces it (checked by
    # calling the delegate afterwards), so the return value is not a failure signal.
    objc.classAddMethod(cls, _SELECTOR, method)
    return True
