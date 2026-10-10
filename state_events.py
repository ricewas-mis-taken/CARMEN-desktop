"""A change counter the browser extension can wait on, so it learns about a
session starting/ending/pausing (or a review changing) right away instead of
only on its next 7-second poll.

session_manager and review_store call bump() whenever something the
extension shows or enforces changes. GET /events/wait?since=<n> (see
api_server.py) holds the request open until the counter moves past n or a
timeout elapses. The extension keeps polling /status on its own timer as
well -- this is only a wake-up hint, so losing it costs speed, never
correctness.

At most MAX_WAITERS requests wait at once (a couple of browsers can be
paired); extra ones return immediately instead of tying up a server thread.
"""
import threading

MAX_WAITERS = 4

_cond = threading.Condition()
_version = 0
_waiters = 0


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
    `since` is None (the caller just wants the baseline), or if too many
    others are already waiting."""
    global _waiters
    with _cond:
        if since is None:
            return _version, False
        if _version != since:
            return _version, True
        if _waiters >= MAX_WAITERS:
            return _version, False
        _waiters += 1
        try:
            _cond.wait_for(lambda: _version != since, timeout=timeout)
            return _version, _version != since
        finally:
            _waiters -= 1
