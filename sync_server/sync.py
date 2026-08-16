"""Push/pull routes backing multi-device sync. Every request forwards the
caller's own verified Supabase access token to Supabase's PostgREST API
(never the service_role key), so Supabase's RLS policies enforce that a
user can only ever read/write their own rows -- this module's own use of
the verified user_id (from require_auth) is a first layer on top of that,
not a substitute for it.
"""
import logging
import os
from datetime import datetime, timezone
from typing import Optional

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query

from .auth import AuthContext, require_auth

logger = logging.getLogger("sync_server.sync")

router = APIRouter()

SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_PUBLISHABLE_KEY = os.environ.get("SUPABASE_PUBLISHABLE_KEY")
REST_URL = f"{SUPABASE_URL.rstrip('/')}/rest/v1/sync_records" if SUPABASE_URL else None

RECORD_FIELDS = ("table_name", "sync_id", "data", "device_id", "updated_at", "is_deleted")


def _require_configured():
    if not REST_URL or not SUPABASE_PUBLISHABLE_KEY:
        raise HTTPException(
            status_code=500,
            detail="sync_server is not configured (missing SUPABASE_URL/SUPABASE_PUBLISHABLE_KEY)",
        )


def _headers(access_token, extra=None):
    headers = {
        "apikey": SUPABASE_PUBLISHABLE_KEY,
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json",
    }
    if extra:
        headers.update(extra)
    return headers


def _parse_ts(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


async def _postgrest_request(client, method, **kwargs):
    try:
        resp = await client.request(method, REST_URL, **kwargs)
        resp.raise_for_status()
        return resp
    except httpx.HTTPStatusError as exc:
        logger.error("PostgREST request failed: %s", exc.response.text)
        raise HTTPException(status_code=502, detail="Sync storage request failed") from exc
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail="Could not reach sync storage") from exc


@router.post("/sync/push")
async def push(records: list[dict], auth: AuthContext = Depends(require_auth)):
    _require_configured()
    accepted = []
    skipped = []

    async with httpx.AsyncClient(timeout=15.0) as client:
        for record in records:
            missing = [f for f in RECORD_FIELDS if f not in record]
            if missing:
                raise HTTPException(status_code=422, detail=f"Record missing fields: {missing}")

            table_name = record["table_name"]
            sync_id = record["sync_id"]
            updated_at = record["updated_at"]

            existing_resp = await _postgrest_request(
                client,
                "GET",
                headers=_headers(auth.access_token),
                params={
                    "table_name": f"eq.{table_name}",
                    "sync_id": f"eq.{sync_id}",
                    "select": "updated_at",
                },
            )
            existing_rows = existing_resp.json()

            if existing_rows and _parse_ts(existing_rows[0]["updated_at"]) >= _parse_ts(updated_at):
                skipped.append({"table_name": table_name, "sync_id": sync_id})
                continue

            payload = {
                "user_id": auth.user_id,
                "table_name": table_name,
                "sync_id": sync_id,
                "data": record["data"],
                "device_id": record["device_id"],
                "updated_at": updated_at,
                "is_deleted": record["is_deleted"],
            }
            await _postgrest_request(
                client,
                "POST",
                headers=_headers(auth.access_token, {"Prefer": "resolution=merge-duplicates"}),
                params={"on_conflict": "user_id,table_name,sync_id"},
                json=payload,
            )
            accepted.append({"table_name": table_name, "sync_id": sync_id})

    return {"accepted": accepted, "skipped": skipped}


@router.get("/sync/pull")
async def pull(since: Optional[str] = Query(default=None), auth: AuthContext = Depends(require_auth)):
    _require_configured()
    params = {"select": ",".join(RECORD_FIELDS)}
    if since:
        params["updated_at"] = f"gt.{since}"

    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await _postgrest_request(client, "GET", headers=_headers(auth.access_token), params=params)

    return resp.json()
