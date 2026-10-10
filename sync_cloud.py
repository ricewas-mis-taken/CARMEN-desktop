"""Talks straight to Supabase for multi-device sync -- no middle server.

The desktop sends the signed-in user's own access token with the publishable
key, so Supabase's row-level-security policies (sync_server/schema.sql) are
what keep one account's rows away from another's. Conflict handling lives in
the database too (sync_server/migrations/001_direct_sync.sql): a trigger keeps
only the newer write and stamps every accepted row with the server's own
clock in `server_updated_at`, which is what pulls use as their cursor so a
device with a wrong clock can no longer hide rows from other devices.

Functions take an open httpx.Client and never read global auth state, so
sync_client owns retries (401 -> refresh) and tests can swap the transport.
"""
import re
from datetime import datetime, timedelta

import auth_manager

TABLE_PATH = "/rest/v1/sync_records"
PHOTO_BUCKET = "carmen-photos"
PUSH_BATCH_SIZE = 200
# Same cap PostgREST applies by default (max-rows 1000); page until a short page.
PULL_PAGE_SIZE = 1000
# `now()`-style timestamps are taken when a transaction starts, so a row can
# become visible slightly after a later-stamped one. Re-reading this much of
# the tail on every pull is harmless (applying a record twice is a no-op).
CURSOR_OVERLAP = timedelta(seconds=30)
MAX_PHOTO_BYTES = 10 * 1024 * 1024

RECORD_FIELDS = ("table_name", "sync_id", "data", "device_id", "updated_at", "is_deleted")


class NotConfigured(Exception):
    pass


def _base_url():
    url = auth_manager.SUPABASE_URL
    key = auth_manager.SUPABASE_PUBLISHABLE_KEY
    if not url or not key:
        raise NotConfigured("Supabase isn't configured on this install.")
    return url.rstrip("/"), key


def _headers(token, extra=None):
    _, key = _base_url()
    headers = {"apikey": key, "Authorization": f"Bearer {token}"}
    if extra:
        headers.update(extra)
    return headers


def _parse_ts(value):
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def push(client, token, user_id, records):
    """Upserts records. Returns (accepted, skipped): rows the database kept
    versus rows it ignored because it already holds a newer version."""
    if not records:
        return 0, 0
    base, _ = _base_url()
    accepted = 0
    for start in range(0, len(records), PUSH_BATCH_SIZE):
        batch = records[start:start + PUSH_BATCH_SIZE]
        payload = [
            {"user_id": user_id, **{field: record[field] for field in RECORD_FIELDS}}
            for record in batch
        ]
        resp = client.post(
            f"{base}{TABLE_PATH}",
            headers=_headers(token, {
                "Content-Type": "application/json",
                "Prefer": "resolution=merge-duplicates,return=representation",
            }),
            params={"on_conflict": "user_id,table_name,sync_id", "select": "sync_id"},
            json=payload,
        )
        resp.raise_for_status()
        # The database's guard trigger drops a row that is not newer than the
        # stored one, so it simply isn't echoed back.
        accepted += len(resp.json())
    return accepted, len(records) - accepted


def pull(client, token, cursor):
    """Returns (records, new_cursor). `cursor` is the largest server_updated_at
    seen by the previous pull (or None for everything)."""
    base, _ = _base_url()
    params = {
        "select": ",".join(RECORD_FIELDS + ("server_updated_at",)),
        "order": "server_updated_at.asc,sync_id.asc",
        "limit": str(PULL_PAGE_SIZE),
    }
    if cursor:
        params["server_updated_at"] = f"gt.{(_parse_ts(cursor) - CURSOR_OVERLAP).isoformat()}"
    records = []
    offset = 0
    while True:
        resp = client.get(
            f"{base}{TABLE_PATH}", headers=_headers(token), params={**params, "offset": str(offset)}
        )
        resp.raise_for_status()
        page = resp.json()
        records.extend(page)
        if len(page) < PULL_PAGE_SIZE:
            break
        offset += len(page)
    newest = cursor
    for record in records:
        stamp = record.get("server_updated_at")
        if stamp and (newest is None or _parse_ts(stamp) > _parse_ts(newest)):
            newest = stamp
    return records, newest


# --- photos (Supabase Storage, private bucket, one folder per user) ---

_SAFE_PHOTO_NAME = re.compile(r"^[A-Za-z0-9_-]{1,64}\.(png|jpe?g|gif|bmp)$")


def is_safe_photo_name(name):
    return isinstance(name, str) and bool(_SAFE_PHOTO_NAME.match(name))


def _photo_url(user_id, kind, name):
    base, _ = _base_url()
    return f"{base}/storage/v1/object/{PHOTO_BUCKET}/{user_id}/{kind}/{name}"


def upload_photo(client, token, user_id, kind, name, data):
    """Stores one photo. An already-uploaded name is not an error: photo names
    are random per upload, so the same name always means the same bytes."""
    if not is_safe_photo_name(name) or len(data) > MAX_PHOTO_BYTES:
        return False
    resp = client.post(
        _photo_url(user_id, kind, name),
        headers=_headers(token, {"Content-Type": "application/octet-stream", "x-upsert": "false"}),
        content=data,
    )
    if resp.status_code in (400, 409) and "exist" in resp.text.lower():
        return True
    resp.raise_for_status()
    return True


def download_photo(client, token, user_id, kind, name):
    """Returns the photo's bytes, or None if it isn't there / isn't acceptable."""
    if not is_safe_photo_name(name):
        return None
    base, _ = _base_url()
    resp = client.get(
        f"{base}/storage/v1/object/authenticated/{PHOTO_BUCKET}/{user_id}/{kind}/{name}",
        headers=_headers(token),
    )
    if resp.status_code in (400, 404):
        return None
    resp.raise_for_status()
    if len(resp.content) > MAX_PHOTO_BYTES:
        return None
    return resp.content
