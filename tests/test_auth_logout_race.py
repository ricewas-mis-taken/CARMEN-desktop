"""A token refresh already in flight when logout() runs used to finish
afterwards, re-populate the in-memory session and write a fresh refresh
token back into the keyring -- the user is silently signed back in."""
import threading
import types

import keyring
import pytest

import auth_manager


@pytest.fixture
def fake_keyring(monkeypatch):
    store = {}
    monkeypatch.setattr(keyring, "set_password", lambda s, u, p: store.__setitem__((s, u), p))
    monkeypatch.setattr(keyring, "get_password", lambda s, u: store.get((s, u)))
    monkeypatch.setattr(keyring, "delete_password", lambda s, u: store.pop((s, u), None))
    return store


def test_logout_during_inflight_refresh_stays_logged_out(fake_keyring, monkeypatch):
    monkeypatch.setattr(auth_manager, "SUPABASE_URL", "http://x")
    monkeypatch.setattr(auth_manager, "SUPABASE_PUBLISHABLE_KEY", "k")
    monkeypatch.setattr(auth_manager, "_access_token", None)
    monkeypatch.setattr(auth_manager, "_current_user", None)
    fake_keyring[(auth_manager.KEYRING_SERVICE, auth_manager.KEYRING_USERNAME)] = "old-refresh"

    in_network, release = threading.Event(), threading.Event()

    def slow_refresh(_token):
        in_network.set()
        release.wait(5)
        return types.SimpleNamespace(
            session=types.SimpleNamespace(access_token="new-access", refresh_token="new-refresh"),
            user=types.SimpleNamespace(id="u1", email="a@b.c"),
        )

    client = types.SimpleNamespace(auth=types.SimpleNamespace(refresh_session=slow_refresh, sign_out=lambda: None))
    monkeypatch.setattr(auth_manager, "_client", client)

    t = threading.Thread(target=auth_manager._refresh_session)
    t.start()
    assert in_network.wait(5)
    auth_manager.logout()
    release.set()
    t.join(5)

    assert auth_manager._access_token is None
    assert auth_manager._current_user is None
    assert fake_keyring.get((auth_manager.KEYRING_SERVICE, auth_manager.KEYRING_USERNAME)) is None
