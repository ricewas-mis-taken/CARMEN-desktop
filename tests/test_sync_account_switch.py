import pytest

import auth_manager
import sync_client


@pytest.fixture
def sync_env(tmp_path, monkeypatch):
    monkeypatch.setattr(sync_client, "LAST_SYNC_PATH", str(tmp_path / "last_sync.txt"))
    monkeypatch.setattr(sync_client, "_cached_last_sync", None)
    monkeypatch.setattr(sync_client, "PULL_CURSOR_PATH", str(tmp_path / "pull_cursor.txt"))
    monkeypatch.setattr(sync_client, "SYNC_OWNER_PATH", str(tmp_path / "sync_owner.txt"), raising=False)
    monkeypatch.setattr(auth_manager, "is_logged_in", lambda: True)
    monkeypatch.setattr(auth_manager, "get_access_token", lambda: "tok")
    monkeypatch.setattr(sync_client, "_gather_all", lambda cutoff: [])
    monkeypatch.setattr(sync_client, "_apply_all", lambda records: (0, 0, 0))

    pulls = []

    class FakeResp:
        def __init__(self, payload): self._p = payload
        def raise_for_status(self): pass
        def json(self): return self._p

    class FakeClient:
        def __init__(self, *a, **k): pass
        def __enter__(self): return self
        def __exit__(self, *a): pass
        def post(self, *a, **k): return FakeResp({"accepted": [], "skipped": []})
        def get(self, url, headers=None, params=None):
            pulls.append(dict(params or {}))
            return FakeResp([])

    monkeypatch.setattr(sync_client.httpx, "Client", FakeClient)
    return pulls


def _as(monkeypatch, user_id):
    monkeypatch.setattr(auth_manager, "get_current_user", lambda: {"id": user_id, "email": f"{user_id}@x"})


def test_same_account_keeps_syncing(sync_env, monkeypatch):
    _as(monkeypatch, "user-a")
    assert sync_client.sync_now().success
    assert sync_client.sync_now().success
    assert len(sync_env) == 2


def test_different_account_is_refused_instead_of_inheriting_the_watermark(sync_env, monkeypatch):
    _as(monkeypatch, "user-a")
    assert sync_client.sync_now().success
    _as(monkeypatch, "user-b")
    result = sync_client.sync_now()
    assert result.success is False
    assert "different account" in result.error
    assert len(sync_env) == 1  # nothing was pushed/pulled under user-b's token
