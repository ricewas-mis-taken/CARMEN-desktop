"""The REST transport: talks to the CarmenLinked dev backend (carmenlinked/docs/API.md).

  * Base URL comes from config key "familyApiBase" (default http://localhost:8420).
  * Auth: "Authorization: Bearer <access token>". Access tokens last about an hour and are kept in memory only.
    The refresh token (30 days, spent and replaced on EVERY use) is saved in the OS credential store through
    `keyring`, the same place auth_manager.py keeps the sync account's refresh token, under its own name.
  * Refreshing is single-flight: every refresh spends the token it is given, so two refreshes at once would look
    like theft to the server (refresh_reused) and end the whole sign-in. One lock, and a caller that arrives
    while another is refreshing waits and then uses the new token instead of refreshing again.
  * A 401 on an ordinary call refreshes once and retries once. If the server refuses the refresh token
    (invalid_refresh / refresh_expired / refresh_reused) the sign-in is gone: FamilyError(code="session_lost").
    If the server just cannot be reached, nothing is discarded and the error is "offline".
  * Only plain stdlib (urllib): no new dependency, nothing that behaves differently on Windows.
  * https is required unless the backend is on this computer (localhost): a sign-in must never cross a network
    in the clear.

Known limit: if the computer dies in the instant between the server spending a refresh token and the new one
being saved, that sign-in cannot be resumed and the student signs in again. The same limit applies to every
rotating-refresh-token client, including the browser extension.
"""
import json
import logging
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from email.utils import parsedate_to_datetime

from family_transport import FamilyError, FamilyTransport

logger = logging.getLogger("carmen_family")

DEFAULT_BASE = "http://localhost:8420"
KEYRING_SERVICE = "CarmenDesktop"
KEYRING_USERNAME = "family_refresh_token"
REQUEST_TIMEOUT_SECONDS = 6.0
EARLY_REFRESH_MS = 2 * 60 * 1000          # renew when less than this is left on the access token
_LOOPBACK = {"localhost", "127.0.0.1", "::1", "[::1]"}
_SESSION_ENDED_CODES = ("refresh_reused", "invalid_refresh", "refresh_expired")


# ---- where the refresh token lives -----------------------------------------------------------------------

class KeyringTokenStore:
    """The refresh token in the OS credential store (Windows Credential Manager on the target platform).
    Saving is best effort, like auth_manager: the in-memory sign-in is already valid; the only cost of a failed
    save is signing in again next launch."""

    def get(self):
        try:
            import keyring
            return keyring.get_password(KEYRING_SERVICE, KEYRING_USERNAME)
        except Exception:
            return None

    def set(self, token):
        try:
            import keyring
            keyring.set_password(KEYRING_SERVICE, KEYRING_USERNAME, token)
            return True
        except Exception:
            logger.warning("Could not save the family sign-in to the OS credential store", exc_info=True)
            return False

    def clear(self):
        try:
            import keyring
            keyring.delete_password(KEYRING_SERVICE, KEYRING_USERNAME)
        except Exception:
            pass


class MemoryTokenStore:
    """For tests (and for a computer with no credential store): nothing survives the process."""

    def __init__(self, token=None):
        self.token = token

    def get(self):
        return self.token

    def set(self, token):
        self.token = token
        return True

    def clear(self):
        self.token = None


def normalize_base(base):
    """The backend's base URL as http(s)://host[:port] with no trailing slash, or raises FamilyError. A plain
    http address is only accepted for this computer (localhost / 127.0.0.1)."""
    text = (base or "").strip()
    if not text:
        raise FamilyError("bad_base", "Enter the address of the family server.")
    if "://" not in text:
        text = "https://" + text
    parts = urllib.parse.urlsplit(text)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise FamilyError("bad_base", "The address must start with http:// or https://.")
    if parts.scheme == "http" and parts.hostname.lower() not in _LOOPBACK:
        raise FamilyError("insecure_base", "Use an https:// address: a sign-in must not cross a network unencrypted.")
    if parts.username or parts.password or parts.query or parts.fragment:
        raise FamilyError("bad_base", "The address must be just the server, with no password or query.")
    return f"{parts.scheme}://{parts.netloc}{parts.path.rstrip('/')}"


class FamilyRest(FamilyTransport):
    name = "rest"

    def __init__(self, base=DEFAULT_BASE, token_store=None, timeout=REQUEST_TIMEOUT_SECONDS):
        self._base = DEFAULT_BASE
        self._timeout = timeout
        self._tokens = token_store if token_store is not None else KeyringTokenStore()
        self._lock = threading.RLock()            # guards the token fields below
        self._refresh_lock = threading.Lock()     # one refresh at a time (see module docstring)
        self._access = None
        self._access_expires_ms = None
        self._user = None
        self._etags = {}                          # path -> (etag, parsed body), per sign-in
        self._offset_ms = 0
        self.early_refresh_ms = EARLY_REFRESH_MS
        self.refresh_count = 0                    # how many refreshes actually went to the server (tests read this)
        self.session_lost = None                  # the code, once the server has ended the sign-in
        try:
            self._base = normalize_base(base)
        except FamilyError:
            self._base = DEFAULT_BASE

    # ---- configuration and state ----
    def configure(self, base):
        new = normalize_base(base)
        with self._lock:
            if new != self._base:
                self._base = new
                self._etags.clear()
        return new

    @property
    def base(self):
        return self._base

    def server_clock_offset_ms(self):
        return self._offset_ms

    def has_saved_login(self):
        return bool(self._access or self._tokens.get())

    def is_signed_in(self):
        with self._lock:
            if self._access:
                return True
        return bool(self._tokens.get()) and self.session_lost is None

    def user(self):
        with self._lock:
            return dict(self._user) if self._user else None

    # ---- sign-in ----
    def login(self, email, password):
        if not isinstance(email, str) or not email.strip() or not isinstance(password, str) or not password:
            raise FamilyError("validation", "Email and password are required.", 400)
        reply = self._request("POST", "/auth/login", body={"email": email.strip(), "password": password}, auth=False)
        self._take_session(reply)
        self.session_lost = None
        return self.user()

    def logout(self, everywhere=False):
        try:
            if self._access:
                self._request("POST", "/auth/logout", body={"all": True} if everywhere else {}, timeout=3.0, retry=False)
        except FamilyError:
            pass                                     # a sign-out has to work offline
        self.forget()

    def forget(self):
        """Drop the sign-in on this computer only (no network)."""
        with self._lock:
            self._access = None
            self._access_expires_ms = None
            self._user = None
            self._etags.clear()
        self._tokens.clear()

    def _take_session(self, reply):
        """Keep a login/refresh reply: access token in memory, new refresh token saved at once."""
        if not isinstance(reply, dict) or not isinstance(reply.get("token"), str) or not reply["token"]:
            raise FamilyError("malformed", "The server's sign-in reply was not understood.", offline=True)
        from family_rules import parse_iso_ms
        refresh = reply.get("refreshToken")
        with self._lock:
            self._access = reply["token"]
            self._access_expires_ms = parse_iso_ms(reply.get("expiresAt"))
            user = reply.get("user")
            if isinstance(user, dict) and isinstance(user.get("id"), str):
                self._user = {"id": user["id"], "email": str(user.get("email") or ""),
                              "displayName": str(user.get("displayName") or "")}
        if isinstance(refresh, str) and refresh:
            self._tokens.set(refresh)

    # ---- refreshing ----
    def ensure_fresh(self):
        with self._lock:
            token, expires = self._access, self._access_expires_ms
        if not token:
            if self._tokens.get() and self.session_lost is None:
                return self.refresh(None)
            return False
        if expires is None or expires - self._now_ms() > self.early_refresh_ms:
            return False
        return self.refresh(token)

    def refresh(self, stale):
        """Swap the refresh token for a new pair. `stale` is the access token the caller found expired or refused:
        if a different one is already in memory, another caller refreshed first and there is nothing to do.
        Returns True. Raises FamilyError (code "session_lost", or offline)."""
        with self._refresh_lock:
            with self._lock:
                if stale is not None and self._access is not None and self._access != stale:
                    return True
            refresh_token = self._tokens.get()
            if not refresh_token:
                with self._lock:
                    self._access = None
                raise FamilyError("session_lost", "Not signed in.", 401)
            try:
                reply = self._request("POST", "/auth/refresh", body={"refreshToken": refresh_token}, auth=False, retry=False)
            except FamilyError as err:
                if err.status == 401 and err.code in _SESSION_ENDED_CODES:
                    self.session_lost = err.code
                    self.forget()
                    raise FamilyError("session_lost", err.code, 401) from err
                raise                                # offline or a server hiccup: keep every token, try again later
            self.refresh_count += 1
            self._take_session(reply)
            return True

    # ---- the one HTTP call ----
    @staticmethod
    def _now_ms():
        return int(time.time() * 1000)

    def _request(self, method, path, body=None, auth=True, timeout=None, retry=True, etag=False):
        headers = {"Accept": "application/json"}
        data = None
        if body is not None:
            data = json.dumps(body).encode("utf-8")
            headers["Content-Type"] = "application/json"
        used_token = None
        if auth:
            with self._lock:
                used_token = self._access
            if not used_token:
                if self._tokens.get() and self.session_lost is None:
                    self.refresh(None)
                    with self._lock:
                        used_token = self._access
                if not used_token:
                    raise FamilyError("signed_out", "Not signed in.", 401)
            headers["Authorization"] = f"Bearer {used_token}"
        cached = self._etags.get(path) if (etag and method == "GET") else None
        if cached:
            headers["If-None-Match"] = cached[0]
        request = urllib.request.Request(self._base + path, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(request, timeout=timeout or self._timeout) as response:
                status, raw, resp_headers = response.status, response.read(), response.headers
        except urllib.error.HTTPError as err:
            status, resp_headers = err.code, err.headers
            try:
                raw = err.read()
            except Exception:
                raw = b""
        except (urllib.error.URLError, OSError, ValueError, TimeoutError) as err:
            # (a refused connection, DNS failure, timeout, reset, or a base URL urllib cannot open)
            raise FamilyError("offline", str(getattr(err, "reason", err)), 0, offline=True) from err
        except Exception as err:                       # http.client.HTTPException and friends
            raise FamilyError("offline", str(err), 0, offline=True) from err
        self._learn_clock(resp_headers)

        if status == 304 and cached:
            return cached[1]
        parsed = None
        if raw:
            try:
                parsed = json.loads(raw.decode("utf-8"))
            except (ValueError, UnicodeDecodeError):
                parsed = None
        if status == 401 and auth and retry and path != "/auth/logout":
            self.refresh(used_token)                  # raises session_lost / offline
            return self._request(method, path, body=body, auth=auth, timeout=timeout, retry=False, etag=etag)
        if status >= 400:
            code = (parsed.get("error") if isinstance(parsed, dict) else None) or f"http_{status}"
            message = (parsed.get("message") if isinstance(parsed, dict) else None) or f"HTTP {status}"
            retry_after = parsed.get("retryAfterSec") if isinstance(parsed, dict) else None
            # a server that is down behind a proxy answers 5xx: that is "could not reach", not "refused"
            raise FamilyError(str(code), str(message), status, offline=status >= 500, retry_after=retry_after, body=parsed)
        if parsed is None and raw:
            raise FamilyError("malformed", "The server's reply was not JSON.", status, offline=True)
        if etag and method == "GET" and resp_headers.get("ETag") and parsed is not None:
            self._etags[path] = (resp_headers.get("ETag"), parsed)
        return parsed

    def _learn_clock(self, headers):
        try:
            when = parsedate_to_datetime(headers.get("Date"))
            self._offset_ms = int(when.timestamp() * 1000 + 500) - self._now_ms()   # the header has whole seconds
        except Exception:
            pass

    def _get(self, path):
        return self._request("GET", path, etag=True)

    # ---- the calls (see FamilyTransport) ----
    def me(self):
        return self._get("/me")

    def heartbeat(self, payload):
        return self._request("POST", "/me/heartbeat", body=payload)

    def post_status(self, payload):
        return self._request("POST", "/me/status", body=payload)

    def post_summaries(self, items):
        return self._request("POST", "/me/summaries", body=items)

    def post_screentime(self, items):
        return self._request("POST", "/me/screentime", body=items)

    def commands(self):
        return self._request("GET", "/me/commands")

    def ack_command(self, command_id, applied):
        return self._request("POST", f"/me/commands/{urllib.parse.quote(str(command_id), safe='')}/ack",
                             body={"applied": bool(applied)})

    def rules(self):
        return self._get("/me/rules")

    def schedule(self):
        return self._get("/me/schedule")

    def schedule_conflicts(self):
        return self._get("/me/schedule-conflicts")

    def classes(self):
        return self._get("/me/classes")

    def post_schedule_skip(self, block_id, reason, day):
        return self._request("POST", "/me/schedule/skips", body={"blockId": block_id, "reason": reason, "date": day})

    def notifications(self, limit=50):
        return self._request("GET", f"/me/notifications?limit={int(limit)}")

    def mark_notifications_read(self):
        return self._request("POST", "/me/notifications/read-all", body={})

    def audit(self, limit=200):
        return self._request("GET", f"/me/audit?limit={int(limit)}")

    def shared_log(self, limit=200):
        return self._request("GET", f"/me/shared-log?limit={int(limit)}")

    def revoke_link(self, link_id):
        return self._request("POST", f"/links/{urllib.parse.quote(str(link_id), safe='')}/revoke", body={})

    def redeem_code(self, code):
        return self._request("POST", "/links/redeem", body={"code": code})

    def health(self):
        return self._request("GET", "/health", auth=False, timeout=4.0, retry=False)
