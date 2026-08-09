"""Tests for auth_manager.py's local logic: error classification, graceful
failure when Supabase isn't configured, and state/keyring cleanup on
logout. Deliberately does NOT hit a real Supabase project -- that's what
scripts/manual_auth_check.py is for, run by hand against a throwaway
account. Every test here fakes out keyring so real Windows Credential
Manager entries are never touched."""
import keyring
import pytest

import auth_manager


@pytest.fixture
def fake_keyring(monkeypatch):
    """In-memory stand-in for the OS credential store, isolating tests
    from the real Windows Credential Manager entry auth_manager would
    otherwise read/write."""
    store = {}

    def fake_set(service, username, password):
        store[(service, username)] = password

    def fake_get(service, username):
        return store.get((service, username))

    def fake_delete(service, username):
        if (service, username) not in store:
            raise keyring.errors.PasswordDeleteError("not found")
        del store[(service, username)]

    monkeypatch.setattr(keyring, "set_password", fake_set)
    monkeypatch.setattr(keyring, "get_password", fake_get)
    monkeypatch.setattr(keyring, "delete_password", fake_delete)
    return store


@pytest.fixture(autouse=True)
def reset_module_state(monkeypatch):
    monkeypatch.setattr(auth_manager, "_client", None)
    monkeypatch.setattr(auth_manager, "_access_token", None)
    monkeypatch.setattr(auth_manager, "_current_user", None)
    yield


def test_login_fails_gracefully_when_not_configured(fake_keyring, monkeypatch):
    monkeypatch.setattr(auth_manager, "SUPABASE_URL", None)
    monkeypatch.setattr(auth_manager, "SUPABASE_PUBLISHABLE_KEY", None)

    success, error = auth_manager.login("someone@example.com", "hunter2")

    assert success is False
    assert "isn't set up" in error.lower()


def test_signup_fails_gracefully_when_not_configured(fake_keyring, monkeypatch):
    monkeypatch.setattr(auth_manager, "SUPABASE_URL", None)
    monkeypatch.setattr(auth_manager, "SUPABASE_PUBLISHABLE_KEY", None)

    success, error = auth_manager.signup("someone@example.com", "hunter2")

    assert success is False
    assert error


@pytest.mark.parametrize(
    "raw_message,expected_snippet",
    [
        ("Invalid login credentials", "incorrect email or password"),
        ("User already registered", "already exists"),
        ("Email not confirmed", "confirm your email"),
        ("Network connection lost", "reach the sign-in server"),
        ("Something totally unexpected happened", "sign-in failed"),
    ],
)
def test_classify_auth_error(raw_message, expected_snippet):
    result = auth_manager._classify_auth_error(Exception(raw_message))
    assert expected_snippet in result.lower()


def test_is_logged_in_false_with_no_stored_refresh_token(fake_keyring, monkeypatch):
    monkeypatch.setattr(auth_manager, "SUPABASE_URL", "https://example.supabase.co")
    monkeypatch.setattr(auth_manager, "SUPABASE_PUBLISHABLE_KEY", "dummy-key")

    assert auth_manager.is_logged_in() is False
    assert auth_manager.get_current_user() is None
    assert auth_manager.get_access_token() is None


def test_logout_clears_in_memory_state_and_keyring_entry(fake_keyring):
    auth_manager._access_token = "fake-access-token"
    auth_manager._current_user = {"id": "u1", "email": "someone@example.com"}
    keyring.set_password(auth_manager.KEYRING_SERVICE, auth_manager.KEYRING_USERNAME, "fake-refresh-token")

    auth_manager.logout()

    assert auth_manager._access_token is None
    assert auth_manager._current_user is None
    assert keyring.get_password(auth_manager.KEYRING_SERVICE, auth_manager.KEYRING_USERNAME) is None


def test_logout_does_not_raise_when_nothing_was_stored(fake_keyring):
    auth_manager.logout()  # should be a no-op, not an exception
