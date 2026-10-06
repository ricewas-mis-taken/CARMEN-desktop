"""If the OS credential store refuses to save the refresh token (no backend,
locked vault, ...), login()/refresh must not raise: login() runs on a
background thread whose result callback is what un-busies the login form."""
import types

import keyring
import keyring.errors
import pytest

import auth_manager


class _FakeAuth:
    def _result(self):
        session = types.SimpleNamespace(access_token="at", refresh_token="rt")
        user = types.SimpleNamespace(id="u1", email="a@b.c")
        return types.SimpleNamespace(session=session, user=user)

    def sign_in_with_password(self, creds):
        return self._result()

    def refresh_session(self, token):
        return self._result()


@pytest.fixture
def broken_keyring(monkeypatch):
    def boom(*a, **kw):
        raise keyring.errors.PasswordSetError("credential store unavailable")

    monkeypatch.setattr(keyring, "set_password", boom)
    monkeypatch.setattr(keyring, "get_password", lambda *a: "old-refresh-token")
    monkeypatch.setattr(auth_manager, "SUPABASE_URL", "http://x")
    monkeypatch.setattr(auth_manager, "SUPABASE_PUBLISHABLE_KEY", "k")
    monkeypatch.setattr(auth_manager, "_client", types.SimpleNamespace(auth=_FakeAuth()))
    monkeypatch.setattr(auth_manager, "_access_token", None)
    monkeypatch.setattr(auth_manager, "_current_user", None)


def test_login_survives_keyring_failure(broken_keyring):
    success, error = auth_manager.login("a@b.c", "pw")  # must not raise
    assert (success, error) == (True, None)
    assert auth_manager.get_current_user() == {"id": "u1", "email": "a@b.c"}


def test_refresh_survives_keyring_failure(broken_keyring):
    assert auth_manager.is_logged_in() is True  # must not raise
