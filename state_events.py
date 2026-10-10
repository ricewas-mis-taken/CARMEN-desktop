"""A change counter the browser extension can wait on, so it learns about a
session starting/ending/pausing (or a review changing) right away instead of
only on its next 7-second poll.

session_manager and review_store call bump() whenever something the
extension shows or enforces changes. GET /events/wait?since=<n> (see
api_server.py) holds the request open until the counter moves past n or a
timeout elapses. The extension keeps polling /status on its own timer as
well -- this is only a wake-up hint, so losing it costs speed, never
correctness.

At most MAX_WAITERS requests wait at once (a few browsers can be paired).
When one more arrives, the OLDEST waiter is released (it gets "no change" at
once and the extension backs off) so the newcomer takes its slot. That keeps
requests from clients that already vanished -- an extension background script
that restarted leaves its old request open until the timeout -- from locking
live clients out.
"""
import threading

MAX_WAITERS = 8

_cond = threading.Condition()
_version = 0
_waiters = []   # one token per parked request, oldest first


def bump():
    global _version
    with _cond:
        _version += 1
        _cond.notify_all()


def current():
    with _cond:
        return _version


def wait_for_change(since, timeout):
    """Returns (version, changed). Returns immediately if the counter already
    differs from `since` (including after a desktop restart reset it), or if
    `since` is None (the caller just wants the baseline). Otherwise parks until
    the counter moves, the timeout passes, or a newer waiter evicts this one."""
    with _cond:
        if since is None:
            return _version, False
        if _version != since:
            return _version, True
        token = {"evicted": False}
        active = [t for t in _waiters if not t["evicted"]]
        if len(active) >= MAX_WAITERS:
            active[0]["evicted"] = True
            _cond.notify_all()
        _waiters.append(token)
        try:
            _cond.wait_for(lambda: _version != since or token["evicted"], timeout=timeout)
            return _version, _version != since
        finally:
            _waiters.remove(token)
