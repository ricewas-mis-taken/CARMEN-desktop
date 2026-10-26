"""Supabase-backed sign-in (Phase 3 Part B of the multi-device sync work).

No UI lives here (that's Phase 4) and no push/pull sync happens here
either (that depends on sync_server, a later phase) -- just
login/signup/logout and current-session state.

Access tokens are kept in memory only, for the life of the process.
Only the refresh token is persisted, and it goes into the OS credential
store via `keyring` (Windows Credential Manager on this app's target
platform) rather than a plain file, since a refresh token alone is enough
to keep signing back in.

SUPABASE_URL/SUPABASE_PUBLISHABLE_KEY come from private/.env (see
.env.example at the repo root for the expected keys) -- never hardcoded.
If they're missing, every function below fails gracefully with a message
instead of raising, so a fresh clone without a configured .env still
starts up fine; sign-in just won't work until it's set up.
"""
import logging
import os
import threading
import time

import keyring
import keyring.errors
from dotenv import load_dotenv
from supabase import create_client
import paths

logger = logging.getLogger("carmen_auth")

ENV_PATH = paths.private_path(".env")
load_dotenv(ENV_PATH)

SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_PUBLISHABLE_KEY = os.environ.get("SUPABASE_PUBLISHABLE_KEY")

if not SUPABASE_URL or not SUPABASE_PUBLISHABLE_KEY:
    logger.warning(
        "SUPABASE_URL and/or SUPABASE_PUBLISHABLE_KEY are not set (checked %s and the "
        "process environment) -- sign-in/sync will be unavailable until they're configured.",
        ENV_PATH,
    )

KEYRING_SERVICE = "CarmenDesktop"
KEYRING_USERNAME = "supabase_refresh_token"

_lock = threading.Lock()
# One refresh at a time. Every refresh uses up the refresh token it was given and
# returns a new one, so two refreshes running together with the same token make
# Supabase think it was stolen and end the whole sign-in.
_refresh_lock = threading.Lock()
# After a refresh that failed for lack of a connection, wait before trying again
# (callers ask "am I signed in?" every few seconds).
_OFFLINE_RETRY_SECONDS = 15
_retry_not_before = 0.0
_client = None
_access_token = None
_auth_generation = 0  # bumped by logout() so an in-flight refresh can tell it was superseded
_current_user = None  # {"id": ..., "email": ...} while logged in, else None


def _on_auth_event(event, session):
    """The Supabase client renews its own login in the background and each renewal
    replaces the refresh token. Whatever we saved at sign-in is then already used up
    -- next launch it is refused, and that was the "I keep having to sign in" bug.
    So every renewal is saved the moment it happens."""
    global _access_token
    if event != "TOKEN_REFRESHED" or session is None or _current_user is None:
        return                      # (signed out, or not signed in by us: never bring it back)
    _access_token = session.access_token
    _store_refresh_token(session.refresh_token)


def _get_client():
    global _client
    if _client is None:
        _client = create_client(SUPABASE_URL, SUPABASE_PUBLISHABLE_KEY)
        _client.auth.on_auth_state_change(_on_auth_event)
    return _client


def _store_refresh_token(token):
    # Persisting is best-effort: the in-memory session is already valid, and
    # an exception escaping here would kill login()'s background thread (the
    # login form then stays busy forever) or crash is_logged_in() callers.
    # The only cost of a failed save is signing in again next launch.
    try:
        keyring.set_password(KEYRING_SERVICE, KEYRING_USERNAME, token)
    except Exception:
        logger.warning("Could not save the refresh token to the OS credential store", exc_info=True)


def _load_refresh_token():
    try:
        return keyring.get_password(KEYRING_SERVICE, KEYRING_USERNAME)
    except keyring.errors.KeyringError:
        return None


def _clear_refresh_token():
    try:
        keyring.delete_password(KEYRING_SERVICE, KEYRING_USERNAME)
    except keyring.errors.KeyringError:
        pass  # already gone, or no backend available -- either way, nothing to clean up


def _not_configured_error():
    return f"Sign-in isn't set up on this install (missing Supabase config in {ENV_PATH})."


def _classify_auth_error(exc):
    """Turns a raised sign-in/sign-up exception into a short, UI-safe
    message. Supabase deliberately returns the same "invalid login
    credentials" error for both a wrong password and an unknown email
    (to avoid leaking which emails have accounts), so those two cases
    can't actually be told apart here -- only "bad credentials" vs.
    "couldn't reach the server" vs. "email already registered" are
    distinguishable."""
    message = str(exc).lower()
    if "invalid login credentials" in message or "invalid_credentials" in message:
        return "Incorrect email or password."
    if "already registered" in message or "user already exists" in message:
        return "An account with that email already exists."
    if "email not confirmed" in message:
        return "Please confirm your email before signing in."
    if any(term in message for term in ("network", "connection", "timed out", "timeout", "resolve")):
        return "Couldn't reach the sign-in server. Check your internet connection."
    return "Sign-in failed. Please try again."


def login(email, password):
    """Returns (success, error_message). error_message is None on success."""
    global _access_token, _current_user
    if not SUPABASE_URL or not SUPABASE_PUBLISHABLE_KEY:
        return False, _not_configured_error()
    try:
        result = _get_client().auth.sign_in_with_password({"email": email, "password": password})
    except Exception as exc:
        return False, _classify_auth_error(exc)

    session = result.session
    if not session:
        return False, "Sign-in failed. Please try again."

    with _lock:
        _access_token = session.access_token
        _current_user = {"id": result.user.id, "email": result.user.email}
        _store_refresh_token(session.refresh_token)
    return True, None


def signup(email, password):
    """Returns (success, error_message). A successful return doesn't
    necessarily mean the account can log in immediately -- Supabase
    projects can require email confirmation first, in which case a
    follow-up login() call fails with the "confirm your email" message
    until that's done."""
    if not SUPABASE_URL or not SUPABASE_PUBLISHABLE_KEY:
        return False, _not_configured_error()
    try:
        _get_client().auth.sign_up({"email": email, "password": password})
    except Exception as exc:
        return False, _classify_auth_error(exc)
    return True, None


def logout():
    global _access_token, _current_user, _auth_generation
    with _lock:
        _auth_generation += 1
        if _client is not None:
            try:
                _client.auth.sign_out()
            except Exception:
                pass  # best-effort -- still clear local state below even if this fails
        _access_token = None
        _current_user = None
        _clear_refresh_token()


def _was_rejected(exc):
    """True if Supabase itself refused the refresh token (revoked, already used,
    expired) -- as opposed to us simply not being able to reach it."""
    status = getattr(exc, "status", None)
    text = str(exc).lower()
    return status in (400, 401, 403) or any(
        term in text for term in ("invalid refresh token", "refresh_token_not_found", "already used", "session_not_found")
    )


def has_saved_login():
    """True if a refresh token is saved -- i.e. the user signed in on this computer
    and has not signed out, even if we cannot reach Supabase right now."""
    return bool(_load_refresh_token())


def _refresh_session(force=True):
    """Exchanges the stored refresh token for a fresh access token.
    Returns True on success. Used both at startup (no in-memory session
    yet) and whenever a request gets a 401 (the sync module's job, once
    it exists) -- calling this again is how that recovers.

    force=False returns at once if another thread has just done it. A refresh
    that failed for lack of a connection keeps the saved token (the user is still
    signed in, we just cannot prove it yet) and is not retried for a few seconds;
    only a refusal from Supabase itself discards it."""
    global _access_token, _current_user, _retry_not_before
    if not SUPABASE_URL or not SUPABASE_PUBLISHABLE_KEY:
        return False
    with _refresh_lock:
        if not force and _access_token and _current_user:
            return True
        if time.monotonic() < _retry_not_before:
            return False
        generation = _auth_generation  # captured before reading the token so a logout() in between is noticed
        refresh_token = _load_refresh_token()
        if not refresh_token:
            return False
        try:
            result = _get_client().auth.refresh_session(refresh_token)
        except Exception as exc:
            if _was_rejected(exc):
                logger.warning("The saved sign-in was refused by Supabase; signing out: %s", exc)
                _clear_refresh_token()
            else:
                _retry_not_before = time.monotonic() + _OFFLINE_RETRY_SECONDS
            return False

        session = result.session
        if not session:
            return False
        _retry_not_before = 0.0
        with _lock:
            if generation != _auth_generation:
                return False  # logout() ran while this refresh was in flight
            _access_token = session.access_token
            _current_user = {"id": result.user.id, "email": result.user.email}
            _store_refresh_token(session.refresh_token)
        return True


def refresh_access_token():
    """Forces a refresh now, even though an in-memory access token exists --
    for a caller that just got a 401 (Supabase access tokens expire, 1h by
    default, and is_logged_in()/get_access_token() alone never notice that).
    Returns True if a fresh token is now held."""
    return _refresh_session()


def is_logged_in():
    if _access_token and _current_user:
        return True
    return _refresh_session(force=False)


def may_be_logged_in():
    """True if signed in, or a saved sign-in could be resumed. Local checks only:
    is_logged_in() may exchange the saved token over the network, which is not
    something to do on the window thread just to decide whether a save is worth
    a background sync."""
    return bool((_access_token and _current_user) or has_saved_login())


def get_current_user():
    """Returns {"id": ..., "email": ...} if logged in, else None."""
    if not is_logged_in():
        return None
    user = _current_user  # a logout() on another thread may clear it right after the check above
    return dict(user) if user else None


def get_access_token():
    """For the future sync module to attach as a Bearer token against
    sync_server. Refreshes first if the in-memory token is gone."""
    if not is_logged_in():
        return None
    return _access_token
