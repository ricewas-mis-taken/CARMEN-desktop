import httpx
from fastapi import FastAPI
from fastapi.testclient import TestClient

from sync_server import sync as sync_mod
from sync_server.auth import AuthContext, require_auth

MAX_ROWS = 1000


def test_pull_returns_more_than_postgrest_max_rows(monkeypatch):
    rows = [
        {"table_name": "t", "sync_id": f"{i:05d}", "data": {}, "device_id": "d",
         "updated_at": "2026-01-01T00:00:00+00:00", "is_deleted": False}
        for i in range(2500)
    ]

    def handler(request):
        q = request.url.params
        off = int(q.get("offset", 0))
        lim = min(int(q.get("limit", MAX_ROWS)), MAX_ROWS)  # PostgREST max-rows cap
        return httpx.Response(200, json=rows[off:off + lim])

    real = httpx.AsyncClient
    monkeypatch.setattr(sync_mod, "REST_URL", "http://fake/rest/v1/sync_records")
    monkeypatch.setattr(sync_mod, "SUPABASE_PUBLISHABLE_KEY", "k")
    monkeypatch.setattr(sync_mod.httpx, "AsyncClient", lambda **kw: real(transport=httpx.MockTransport(handler)))

    app = FastAPI()
    app.include_router(sync_mod.router)
    app.dependency_overrides[require_auth] = lambda: AuthContext(user_id="u", access_token="t")
    resp = TestClient(app).get("/sync/pull")
    assert resp.status_code == 200
    assert len(resp.json()) == 2500
