"""In-memory stand-in for the Supabase REST + Storage endpoints sync_cloud
talks to, including the database trigger from
sync_server/migrations/001_direct_sync.sql (newer-wins, future clamp, server
stamp). Used through httpx.MockTransport -- no network, no real account."""
import json
from datetime import datetime, timedelta, timezone

import httpx

USER = "user-1"


class FakeSupabase:
    def __init__(self, user_id=USER, token="fake-token"):
        self.user_id = user_id
        self.token = token
        self.rows = {}          # (table_name, sync_id) -> row dict
        self.photos = {}        # storage object path -> bytes
        self.requests = []      # (method, path) log
        self.clock = datetime.now(timezone.utc)  # the server's clock; tests may move it
        self.pull_page_size = None  # simulate a smaller max-rows cap when set
        # False behaves like a real project before migration 001: writes are
        # accepted with no newer-wins check, and anything naming the new column fails.
        self.migrated = True
        self.writes = 0

    def tick(self):
        self.clock += timedelta(milliseconds=1)
        return self.clock

    def __setitem__(self, key, record):
        """Plant a row as if another device had pushed it: server[(table, id)] = record."""
        self._upsert({"user_id": self.user_id, **record})

    # --- the guard trigger ---
    def _upsert(self, payload):
        key = (payload["table_name"], payload["sync_id"])
        row = dict(payload)
        updated = datetime.fromisoformat(row["updated_at"].replace("Z", "+00:00"))
        if updated > self.clock + timedelta(minutes=10):
            updated = self.clock
        row["updated_at"] = updated.isoformat()
        existing = self.rows.get(key)
        if existing and updated <= datetime.fromisoformat(existing["updated_at"]):
            return None
        row["server_updated_at"] = self.tick().isoformat()
        self.rows[key] = row
        return row

    def handler(self, request):
        self.requests.append((request.method, request.url.path))
        if request.headers.get("authorization") != f"Bearer {self.token}":
            return httpx.Response(401, json={"message": "JWT expired"})
        path = request.url.path
        if path == "/rest/v1/sync_records":
            self._check_params(request)
            if not self.migrated and "server_updated_at" in str(request.url.params):
                return httpx.Response(400, json={
                    "code": "42703", "message": "column sync_records.server_updated_at does not exist"})
            if request.method == "POST":
                if not self.migrated:
                    for p in json.loads(request.content):
                        self.writes += 1
                        self.rows[(p["table_name"], p["sync_id"])] = dict(p)  # blind overwrite
                    return httpx.Response(200, json=[{"sync_id": p["sync_id"]} for p in json.loads(request.content)])
                kept = [self._upsert(p) for p in json.loads(request.content)]
                return httpx.Response(200, json=[{"sync_id": r["sync_id"]} for r in kept if r])
            if request.method == "GET":
                return self._select(request.url.params)
        if path.startswith("/storage/v1/object/"):
            return self._storage(request, path)
        raise AssertionError(f"unexpected request: {request.method} {request.url}")

    ALLOWED_PARAMS = {
        "GET": {"select", "order", "limit", "offset", "server_updated_at"},
        "POST": {"on_conflict", "select"},
    }

    def _check_params(self, request):
        """A real server would happily ignore (or choke on) a parameter the
        client invented; the fake refuses so a typo or a revert to the old
        client-clock filter can't pass unnoticed."""
        unknown = set(request.url.params.keys()) - self.ALLOWED_PARAMS[request.method]
        assert not unknown, f"unexpected query parameters {sorted(unknown)}"
        if request.method == "GET":
            assert request.url.params.get("order", "server_updated_at.asc").startswith("server_updated_at"), request.url

    def _select(self, params):
        rows = sorted(self.rows.values(), key=lambda r: (r["server_updated_at"], r["sync_id"]))
        flt = params.get("server_updated_at")
        if flt:
            assert flt.startswith("gt."), flt
            cutoff = datetime.fromisoformat(flt[3:].replace("Z", "+00:00"))
            rows = [r for r in rows if datetime.fromisoformat(r["server_updated_at"]) > cutoff]
        offset = int(params.get("offset", 0))
        limit = int(params.get("limit", 1000))
        if self.pull_page_size:
            limit = min(limit, self.pull_page_size)
        fields = params["select"].split(",")
        return httpx.Response(200, json=[{f: r[f] for f in fields} for r in rows[offset:offset + limit]])

    def _storage(self, request, path):
        if request.method == "POST":
            key = path.split("/storage/v1/object/", 1)[1]
            if key in self.photos:
                return httpx.Response(400, json={"error": "Duplicate", "message": "The resource already exists"})
            self.photos[key] = request.content
            return httpx.Response(200, json={"Key": key})
        key = path.split("/storage/v1/object/authenticated/", 1)[1]
        if key not in self.photos:
            return httpx.Response(400, json={"message": "Object not found"})
        return httpx.Response(200, content=self.photos[key])

    def install(self, monkeypatch, sync_client):
        real = httpx.Client
        monkeypatch.setattr(
            sync_client.httpx, "Client",
            lambda **kwargs: real(transport=httpx.MockTransport(self.handler)),
        )
