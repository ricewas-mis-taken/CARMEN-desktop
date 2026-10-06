import json
from datetime import datetime, timedelta, timezone

import httpx
from fastapi import FastAPI
from fastapi.testclient import TestClient

from sync_server import sync as sync_mod
from sync_server.auth import AuthContext, require_auth


def _client(monkeypatch):
    store = {}

    def handler(request):
        q = request.url.params
        if request.method == "GET":
            rows = [r for k, r in store.items()
                    if k == (q["table_name"].removeprefix("eq."), q["sync_id"].removeprefix("eq."))]
            return httpx.Response(200, json=[{"updated_at": r["updated_at"]} for r in rows])
        body = json.loads(request.content)
        store[(body["table_name"], body["sync_id"])] = body
        return httpx.Response(201, json=[])

    real = httpx.AsyncClient
    monkeypatch.setattr(sync_mod, "REST_URL", "http://fake/rest/v1/sync_records")
    monkeypatch.setattr(sync_mod, "SUPABASE_PUBLISHABLE_KEY", "k")
    monkeypatch.setattr(sync_mod.httpx, "AsyncClient", lambda **kw: real(transport=httpx.MockTransport(handler)))
    app = FastAPI()
    app.include_router(sync_mod.router)
    app.dependency_overrides[require_auth] = lambda: AuthContext(user_id="u", access_token="t")
    return TestClient(app), store


def _rec(updated_at, name):
    return {"table_name": "tasks", "sync_id": "t1", "data": {"name": name}, "device_id": "d",
            "updated_at": updated_at, "is_deleted": False}


def test_one_clock_skewed_push_does_not_freeze_the_record_forever(monkeypatch):
    client, store = _client(monkeypatch)
    now = datetime.now(timezone.utc)
    # a laptop whose clock is a year+ ahead edits the task
    skewed = (now + timedelta(days=400)).isoformat()
    r1 = client.post("/sync/push", json=[_rec(skewed, "edited on skewed clock")]).json()
    # a correctly-clocked device edits it a minute later
    r2 = client.post("/sync/push", json=[_rec((now + timedelta(minutes=1)).isoformat(), "real later edit")]).json()
    print("skewed push:", r1)
    print("later real edit:", r2)
    print("stored name:", store[("tasks", "t1")]["data"]["name"])
    assert r2["accepted"], "a legitimate later edit was silently dropped"
    assert store[("tasks", "t1")]["data"]["name"] == "real later edit"
