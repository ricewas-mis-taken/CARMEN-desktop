"""Photos for synced records (board attachments and review problem photos).

A record's `descriptionPhotoPath` is an absolute path on one machine, which is
meaningless on another. On the wire a record carries only the photo's file
name (`descriptionPhotoFile`); each device resolves that name against its own
photo folder. The bytes travel through the private Supabase Storage bucket
(see sync_cloud.py): uploaded before the record that references them is
pushed, fetched after pulled records are applied.

Names that arrive from the cloud are untrusted: they must match a strict
pattern (no path separators, allowed image extension) before they are ever
joined onto a local folder or used in a storage URL.
"""
import json
import os
import re
import time

import board_store
import review_store
import sync_cloud
from calendar_log import logger

KINDS = ("board", "review")

# Photos known to be in the cloud bucket (remembered across restarts so a
# photo is uploaded once, however old its record is), and photos the server
# said it doesn't have (not retried for a while instead of on every sync).
UPLOADED_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "private", "uploaded_photos.json")
_uploaded = None
_missing_until = {}
MISSING_RETRY_SECONDS = 3600


def _photos_dir(kind):
    return board_store.PHOTOS_DIR if kind == "board" else review_store.PHOTOS_DIR


def file_name_from_path(path):
    """The safe file name inside a stored path (from any OS), or None."""
    if not path or not isinstance(path, str):
        return None
    name = re.split(r"[\\/]", path)[-1]
    return name if sync_cloud.is_safe_photo_name(name) else None


def local_path(kind, name):
    if not sync_cloud.is_safe_photo_name(name):
        return None
    return os.path.join(_photos_dir(kind), name)


def to_wire(data):
    """Returns a copy of a record's data with the absolute path swapped for the
    portable file name. Records without photo fields are returned unchanged."""
    if "descriptionPhotoPath" not in data:
        return data
    wire = dict(data)
    wire["descriptionPhotoFile"] = file_name_from_path(wire.pop("descriptionPhotoPath"))
    return wire


def resolve_incoming(kind, data):
    """Mutates a pulled record's data: replaces the portable file name (or a
    legacy absolute path from another machine) with this device's own path.
    A name that fails validation becomes no photo rather than a path."""
    if "descriptionPhotoFile" not in data and "descriptionPhotoPath" not in data:
        return data
    name = data.pop("descriptionPhotoFile", None) or file_name_from_path(data.get("descriptionPhotoPath"))
    data["descriptionPhotoPath"] = local_path(kind, name) if name else None
    return data


def _uploaded_names():
    global _uploaded
    if _uploaded is None:
        try:
            with open(UPLOADED_PATH, "r", encoding="utf-8") as f:
                _uploaded = {n for n in json.load(f) if isinstance(n, str)}
        except (OSError, ValueError):
            _uploaded = set()
    return _uploaded


def _mark_uploaded(name):
    names = _uploaded_names()
    if name in names:
        return
    names.add(name)
    try:
        os.makedirs(os.path.dirname(UPLOADED_PATH), exist_ok=True)
        tmp = UPLOADED_PATH + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(sorted(names), f)
        os.replace(tmp, UPLOADED_PATH)
    except OSError:
        logger.warning("sync_photos: couldn't save the uploaded-photos list", exc_info=True)


def upload_missing(client, token, user_id):
    """Uploads every local photo a record points at that the cloud doesn't
    have yet. Goes through all local records, not just the ones changing in
    this sync: a photo whose record was last edited before cloud sync existed
    (or whose earlier upload failed) must still get there."""
    for kind, name in _referenced_photos():
        if name in _uploaded_names():
            continue
        path = local_path(kind, name)
        if not path or not os.path.isfile(path):
            continue
        try:
            with open(path, "rb") as f:
                data = f.read()
            if sync_cloud.upload_photo(client, token, user_id, kind, name, data):
                _mark_uploaded(name)
        except Exception:
            logger.warning("sync_photos: couldn't upload %s", name, exc_info=True)


def _referenced_photos():
    found = []
    try:
        for item in board_store.load_board():
            name = file_name_from_path(item.get("descriptionPhotoPath"))
            if name:
                found.append(("board", name))
    except Exception:
        logger.warning("sync_photos: couldn't list board photos", exc_info=True)
    try:
        for row in review_store.list_photo_paths():
            name = file_name_from_path(row)
            if name:
                found.append(("review", name))
    except Exception:
        logger.warning("sync_photos: couldn't list review photos", exc_info=True)
    return found


def download_missing(client, token, user_id):
    """Fetches every photo a local record points at but that isn't on disk."""
    now = time.monotonic()
    for kind, name in _referenced_photos():
        path = local_path(kind, name)
        if not path or os.path.isfile(path) or _missing_until.get(name, 0) > now:
            continue
        try:
            data = sync_cloud.download_photo(client, token, user_id, kind, name)
            if data is None:
                _missing_until[name] = now + MISSING_RETRY_SECONDS
                continue
            os.makedirs(os.path.dirname(path), exist_ok=True)
            tmp = path + ".tmp"
            with open(tmp, "wb") as f:
                f.write(data)
            os.replace(tmp, path)
            _mark_uploaded(name)
        except Exception:
            logger.warning("sync_photos: couldn't download %s", name, exc_info=True)
