"""Keeps the app-wide event filters from running inside themselves or inside a garbage collection.

The motion and popup filters see every event of the whole app. Their own work (and the freeing of widgets during a
collection) can send more events, which come straight back into the filters, nested deeper each time, until Python's
recursion limit is hit and the window thread hangs (and then crashes). While a filter is running, or the collector
is, any event that arrives is passed through untouched."""
from contextlib import contextmanager

_state = {"busy": False}


def busy():
    return _state["busy"]


@contextmanager
def quiet():
    """Inside this block every guarded filter does nothing. Returns at once if already quiet."""
    was = _state["busy"]
    _state["busy"] = True
    try:
        yield not was
    finally:
        _state["busy"] = was
