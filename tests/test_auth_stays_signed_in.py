"""Staying signed in: the saved sign-in must follow every renewal Supabase makes,
survive being offline, and only be thrown away when Supabase itself refuses it."""
import threading
import time
import types

import pytest

import auth_manager
from test_auth_manager import fake_keyring  # noqa: F401  (shared fixture)

SAVED = ("CarmenDesktop", "supabase_refresh_token")


class _Auth:
    def __init__(self):
        self.refresh_calls = []
        self.outcomes = []
        self.callbacks = []
        self.delay = 0.0

    def on_auth_state_change(self, callback):
        self.callbacks.append(callback)

    def refresh_session(self, token):
        self.refresh_calls.append(token)
        time.sleep(self.delay)
        outcome = self.outcomes.pop(0) if self.outcomes else _ok(f"access-{len(self.refresh_calls)}", f"refresh-{len(self.refresh_calls)}")
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


def _ok(access, refresh):
    return types.SimpleNamespace(
        session=types.SimpleNamespace(access_token=access, refresh_token=refresh),
        user=types.SimpleNamespace(id="user-1", email="u@example.com"),
    )


class _Rejected(Exception):
    status = 400


@pytest.fixture
def auth(monkeypatch, fake_keyring):
    fake = types.SimpleNamespace(auth=_Auth())
    monkeypatch.setattr(auth_manager, "create_client", lambda url, key: fake)
    monkeypatch.setattr(auth_manager, "SUPABASE_URL", "https://x.supabase.co")
    monkeypatch.setattr(auth_manager, "SUPABASE_PUBLISHABLE_KEY", "key")
    monkeypatch.setattr(auth_manager, "_client", None)
    monkeypatch.setattr(auth_manager, "_access_token", None)
    monkeypatch.setattr(auth_manager, "_current_user", None)
    monkeypatch.setattr(auth_manager, "_retry_not_before", 0.0)
    return types.SimpleNamespace(client=fake, auth=fake.auth, keyring=fake_keyring)


def test_a_renewal_the_library_makes_on_its_own_is_saved(auth):
    auth.keyring[SAVED] = "refresh-0"
    assert auth_manager.is_logged_in()                 # startup: used refresh-0, saved refresh-1
    assert auth.keyring[SAVED] == "refresh-1"
    # an hour later the Supabase client renews in the background and tells us
    session = types.SimpleNamespace(access_token="access-new", refresh_token="refresh-new")
    auth.auth.callbacks[0]("TOKEN_REFRESHED", session)
    assert auth.keyring[SAVED] == "refresh-new"        # the next launch will use this one
    assert auth_manager.get_access_token() == "access-new"


def test_a_renewal_after_signing_out_is_not_brought_back(auth):
    auth.keyring[SAVED] = "refresh-0"
    assert auth_manager.is_logged_in()
    auth_manager.logout()
    session = types.SimpleNamespace(access_token="late", refresh_token="late-refresh")
    auth.auth.callbacks[0]("TOKEN_REFRESHED", session)
    assert SAVED not in auth.keyring and auth_manager.get_access_token() is None


def test_other_events_do_not_touch_the_saved_sign_in(auth):
    auth.keyring[SAVED] = "refresh-0"
    assert auth_manager.is_logged_in()
    saved = auth.keyring[SAVED]
    session = types.SimpleNamespace(access_token="x", refresh_token="y")
    for event in ("SIGNED_OUT", "USER_UPDATED", "INITIAL_SESSION"):
        auth.auth.callbacks[0](event, session if event != "SIGNED_OUT" else None)
    assert auth.keyring[SAVED] == saved


def test_many_threads_asking_at_startup_use_the_saved_token_only_once(auth):
    auth.keyring[SAVED] = "refresh-0"
    auth.auth.delay = 0.2
    results = []
    threads = [threading.Thread(target=lambda: results.append(auth_manager.is_logged_in())) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert results == [True] * 8
    assert auth.auth.refresh_calls == ["refresh-0"]     # a second use would look like theft


def test_being_offline_keeps_the_saved_sign_in_and_does_not_hammer_the_network(auth):
    auth.keyring[SAVED] = "refresh-0"
    auth.auth.outcomes = [ConnectionError("no network")]
    assert not auth_manager.is_logged_in()
    assert auth.keyring[SAVED] == "refresh-0" and auth_manager.has_saved_login()
    for _ in range(5):
        assert not auth_manager.is_logged_in()
    assert len(auth.auth.refresh_calls) == 1            # backing off between tries


def test_it_signs_back_in_by_itself_once_the_network_is_back(auth, monkeypatch):
    auth.keyring[SAVED] = "refresh-0"
    auth.auth.outcomes = [ConnectionError("no network")]
    assert not auth_manager.is_logged_in()
    monkeypatch.setattr(auth_manager, "_retry_not_before", 0.0)   # the wait is over
    assert auth_manager.is_logged_in()
    assert auth.auth.refresh_calls == ["refresh-0", "refresh-0"]  # still the same, never-used token


def test_only_a_refusal_from_supabase_discards_the_saved_sign_in(auth):
    auth.keyring[SAVED] = "refresh-0"
    auth.auth.outcomes = [_Rejected("Invalid Refresh Token: Already Used")]
    assert not auth_manager.is_logged_in()
    assert SAVED not in auth.keyring and not auth_manager.has_saved_login()
    assert not auth_manager.is_logged_in()
    assert len(auth.auth.refresh_calls) == 1            # and it does not keep knocking with a dead token


def test_a_forced_refresh_after_a_401_still_works(auth):
    auth.keyring[SAVED] = "refresh-0"
    assert auth_manager.is_logged_in()
    assert auth_manager.refresh_access_token()
    assert auth.auth.refresh_calls == ["refresh-0", "refresh-1"]
    assert auth.keyring[SAVED] == "refresh-2"


def test_the_client_registers_for_renewals_exactly_once(auth):
    auth_manager._get_client()
    auth_manager._get_client()
    assert len(auth.auth.callbacks) == 1


def test_the_account_area_keeps_trying_when_signed_in_but_offline(qtbot, auth, monkeypatch):
    import qt_gui_thread
    import qt_ui.account_widget as account_widget
    monkeypatch.setattr(qt_gui_thread, "_dispatcher", None)    # restored after the test
    qt_gui_thread.start()
    auth.keyring[SAVED] = "refresh-0"
    auth.auth.outcomes = [ConnectionError("no network")]
    monkeypatch.setattr(account_widget.AccountArea, "_background_sync", lambda self: None)
    area = account_widget.AccountArea()
    qtbot.addWidget(area)
    assert area._reconnect_timer.isActive()
    assert "signing back in" in area._status_label.text().lower()
    monkeypatch.setattr(auth_manager, "_retry_not_before", 0.0)
    area._try_saved_login()
    qtbot.waitUntil(lambda: not area._reconnect_timer.isActive(), timeout=3000)
    assert auth_manager.is_logged_in() and area._account_row.isVisibleTo(area)


def test_a_refused_saved_sign_in_goes_back_to_the_sign_in_button(qtbot, auth, monkeypatch):
    import qt_gui_thread
    import qt_ui.account_widget as account_widget
    monkeypatch.setattr(qt_gui_thread, "_dispatcher", None)    # restored after the test
    qt_gui_thread.start()
    auth.keyring[SAVED] = "refresh-0"
    auth.auth.outcomes = [ConnectionError("no network"), _Rejected("Invalid Refresh Token")]
    area = account_widget.AccountArea()
    qtbot.addWidget(area)
    assert area._reconnect_timer.isActive()
    monkeypatch.setattr(auth_manager, "_retry_not_before", 0.0)
    area._try_saved_login()
    qtbot.waitUntil(lambda: not area._reconnect_timer.isActive(), timeout=3000)
    assert area._signin_button.isVisibleTo(area)
