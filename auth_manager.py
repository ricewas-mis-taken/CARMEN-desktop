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

import keyring
import keyring.errors
from dotenv import load_dotenv
from supabase import create_client

logger = logging.getLogger("carmen_auth")

ENV_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "private", ".env")
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
_client = None
_access_token = None
_current_user = None  # {"id": ..., "email": ...} while logged in, else None


def _get_client():
    global _client
    if _client is None:
        _client = create_client(SUPABASE_URL, SUPABASE_PUBLISHABLE_KEY)
    return _client


def _store_refresh_token(token):
    keyring.set_password(KEYRING_SERVICE, KEYRING_USERNAME, token)


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
    global _access_token, _current_user
    with _lock:
        if _client is not None:
            try:
                _client.auth.sign_out()
            except Exception:
                pass  # best-effort -- still clear local state below even if this fails
        _access_token = None
        _current_user = None
        _clear_refresh_token()


def _refresh_session():
    """Exchanges the stored refresh token for a fresh access token.
    Returns True on success. Used both at startup (no in-memory session
    yet) and whenever a request gets a 401 (the sync module's job, once
    it exists) -- calling this again is how that recovers."""
    global _access_token, _current_user
    if not SUPABASE_URL or not SUPABASE_PUBLISHABLE_KEY:
        return False
    refresh_token = _load_refresh_token()
    if not refresh_token:
        return False
    try:
        result = _get_client().auth.refresh_session(refresh_token)
    except Exception:
        return False

    session = result.session
    if not session:
        return False
    with _lock:
        _access_token = session.access_token
        _current_user = {"id": result.user.id, "email": result.user.email}
        _store_refresh_token(session.refresh_token)
    return True


def is_logged_in():
    if _access_token and _current_user:
        return True
    return _refresh_session()


def get_current_user():
    """Returns {"id": ..., "email": ...} if logged in, else None."""
    if not is_logged_in():
        return None
    return dict(_current_user)


def get_access_token():
    """For the future sync module to attach as a Bearer token against
    sync_server. Refreshes first if the in-memory token is gone."""
    if not is_logged_in():
        return None
    return _access_token
