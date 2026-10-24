"""The QuickNote: one running list of bullet points, saved to private/quicknote.json.

It is a single shared note (not one per computer): the whole list travels as one
cloud row, newest change wins (see sync_live.py), so a note jotted down on one
computer is there on the other. The list is small by design -- a scratchpad, not
a document -- so replacing it whole is simpler and safer than merging lines.
"""
import json
import os
import threading

import server_clock
import paths

PATH = paths.private_path("quicknote.json")
MAX_ITEMS = 500
MAX_ITEM_CHARS = 2000

_lock = threading.Lock()
_change_listeners = []


def add_change_listener(listener):
    _change_listeners.append(listener)


def _clean(items):
    """A list of at most MAX_ITEMS strings, each cut to MAX_ITEM_CHARS, or None
    if `items` is not a list of strings."""
    if not isinstance(items, list) or not all(isinstance(i, str) for i in items):
        return None
    return [i[:MAX_ITEM_CHARS] for i in items[:MAX_ITEMS]]


def _read():
    try:
        with open(PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        return {"items": [], "updatedAt": None}
    items = _clean(data.get("items")) if isinstance(data, dict) else None
    stamp = data.get("updatedAt") if isinstance(data, dict) else None
    return {"items": items if items is not None else [], "updatedAt": stamp if isinstance(stamp, str) else None}


def _write(items, stamp):
    os.makedirs(os.path.dirname(PATH), exist_ok=True)
    tmp = PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump({"items": items, "updatedAt": stamp}, f)
    os.replace(tmp, PATH)


def load():
    """(items, stamp): the bullets in order (always at least one, maybe empty)
    and when they last changed."""
    with _lock:
        data = _read()
    return (data["items"] or [""]), data["updatedAt"]


def save(items):
    """Saves the bullets as edited here. Returns True if anything changed."""
    cleaned = _clean(items)
    if cleaned is None:
        return False
    with _lock:
        current = _read()
        # An empty note and a note of one empty bullet are the same thing.
        if _normalized(cleaned) == _normalized(current["items"]):
            return False
        _write(cleaned, server_clock.next_stamp())
    for listener in list(_change_listeners):
        try:
            listener()
        except Exception:
            pass
    return True


def _normalized(items):
    return [] if items == [""] else list(items)


def export():
    with _lock:
        return _read()


def apply_remote(items, stamp):
    """Takes the note another computer shared, if it is newer. Returns True if
    it replaced ours."""
    cleaned = _clean(items)
    if cleaned is None or server_clock.parse(stamp) is None:
        return False
    with _lock:
        if not server_clock.is_newer(stamp, _read()["updatedAt"]):
            return False
        _write(cleaned, stamp)
    return True
