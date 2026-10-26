"""Does this account use more than one computer?

Only then is a live connection worth its cost: a single computer has nobody to
hear from. Any record that arrived from another device's id is proof, and the
answer is remembered (as the day it was last seen), so a one-computer user
never opens a connection and a two-computer user does from the first sync that
shows the other one. Not seen for MAX_AGE_DAYS counts as one computer again.
"""
import os
import time

import device_id
import paths

SEEN_PATH = paths.private_path("other_device_seen.txt")
MAX_AGE_DAYS = 30


def note_records(records):
    """Call with records pulled from the cloud; remembers if one came from
    another computer."""
    me = device_id.get_device_id()
    for record in records or []:
        other = record.get("device_id")
        if other and other != me:
            _write_seen()
            return True
    return False


def has_other_device():
    try:
        with open(SEEN_PATH, "r", encoding="utf-8") as f:
            seen_at = float(f.read().strip())
    except (OSError, ValueError):
        return False
    return time.time() - seen_at < MAX_AGE_DAYS * 24 * 3600


def _write_seen():
    try:
        os.makedirs(os.path.dirname(SEEN_PATH), exist_ok=True)
        with open(SEEN_PATH, "w", encoding="utf-8") as f:
            f.write(str(time.time()))
    except OSError:
        pass
