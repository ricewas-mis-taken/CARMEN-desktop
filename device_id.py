"""Generates and persists a stable per-install device identifier.

Used to stamp the deviceId/device_id column on every row a soft-delete or
sync write touches, so a future sync module (and a human debugging a
conflict) can tell which machine made which change. Not a hardware ID --
just a random UUID minted once per install and cached to disk.
"""
import os
import uuid

DEVICE_ID_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "private", "device_id.txt")

_cached_id = None


def get_device_id():
    global _cached_id
    if _cached_id:
        return _cached_id

    if os.path.exists(DEVICE_ID_PATH):
        try:
            with open(DEVICE_ID_PATH, "r", encoding="utf-8") as f:
                existing = f.read().strip()
            if existing:
                _cached_id = existing
                return _cached_id
        except OSError:
            pass

    new_id = uuid.uuid4().hex
    os.makedirs(os.path.dirname(DEVICE_ID_PATH), exist_ok=True)
    tmp_path = DEVICE_ID_PATH + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        f.write(new_id)
    os.replace(tmp_path, DEVICE_ID_PATH)
    _cached_id = new_id
    return _cached_id
