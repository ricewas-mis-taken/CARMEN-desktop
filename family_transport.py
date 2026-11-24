"""How the desktop talks to the family backend, as an interface.

The family features (a linked parent or instructor setting rules, starting sessions, seeing what the student
chose to share) need a server. Today that server is the CarmenLinked dev backend (REST, Bearer tokens; see
family_rest.py). Later it will be Supabase (see family_supabase.py, a documented stub). Everything above this
layer -- family_sync.py, the UI, the tests -- talks to a FamilyTransport and never to a URL, so swapping the
backend means writing one class and changing one line in family_sync.make_transport().

Every method either returns plain JSON data (dicts and lists, exactly the shape the backend documents in
carmenlinked/docs/API.md) or raises FamilyError. The caller decides what an error means; the transport only
reports it truthfully:

  FamilyError.offline  True  -> could not reach the server, or got something that is not a valid reply. The
                               caller keeps whatever it already has (never fail open, never fail closed).
                       False -> the server answered and refused (status/code say why).
  code == "session_lost"     -> the saved sign-in was ended on the server (password changed, another device used
                               the refresh token twice, 30 days passed). The caller signs out locally.
"""


class FamilyError(Exception):
    """A failed call. `offline` is True when the server could not be reached or its reply was not usable."""

    def __init__(self, code="error", message="", status=0, offline=False, retry_after=None, body=None):
        super().__init__(message or code)
        self.code = code
        self.message = message or code
        self.status = status
        self.offline = offline
        self.retry_after = retry_after
        self.body = body

    def __repr__(self):
        return f"FamilyError(code={self.code!r}, status={self.status}, offline={self.offline})"


class FamilyTransport:
    """The calls the desktop makes. Subclasses implement all of them. Methods that read return JSON data."""

    name = "abstract"

    # ---- connection and sign-in ----
    def configure(self, base):
        """Point at a backend (a URL for REST; ignored by Supabase, which has its own project settings)."""
        raise NotImplementedError

    def login(self, email, password):
        """Sign in. Returns {"id", "email", "displayName"}. Saves the refresh token to the OS credential store."""
        raise NotImplementedError

    def logout(self, everywhere=False):
        """End this sign-in on the server (best effort; it must work offline) and forget it locally."""
        raise NotImplementedError

    def has_saved_login(self):
        """True if a sign-in is saved on this computer, even if the server cannot be reached right now."""
        raise NotImplementedError

    def is_signed_in(self):
        """True if there is a usable sign-in right now (an access token in memory, or one that can be renewed)."""
        raise NotImplementedError

    def ensure_fresh(self):
        """Renew the access token shortly before it runs out. Single-flight: concurrent callers share one renewal.
        Raises FamilyError(code="session_lost") when the server ended the sign-in."""
        raise NotImplementedError

    def server_clock_offset_ms(self):
        """Server time minus this computer's time, in milliseconds (0 until a reply taught us)."""
        return 0

    # ---- the student's own state ----
    def me(self):
        """GET /me -> {"user", "asStudent": {"links": [...], "devices": [...]}, "asGuide": {...}}"""
        raise NotImplementedError

    def heartbeat(self, payload):
        """POST /me/heartbeat {deviceId, kind, version, enforcing, rules, sessionActive}"""
        raise NotImplementedError

    def post_status(self, payload):
        """POST /me/status {state, taskName?, lockMode?, startedAt?, endsAt?, pausedMin?, violations?}"""
        raise NotImplementedError

    def post_summaries(self, items):
        """POST /me/summaries [ {...finished session...} ] (upsert by id)"""
        raise NotImplementedError

    def post_screentime(self, items):
        """POST /me/screentime [ {day, hostname, seconds, at} ]  (hostnames only)"""
        raise NotImplementedError

    # ---- what guides send ----
    def commands(self):
        """GET /me/commands -> pending, unexpired commands from guides who still have an active link."""
        raise NotImplementedError

    def ack_command(self, command_id, applied):
        """POST /me/commands/:id/ack {applied}"""
        raise NotImplementedError

    def rules(self):
        """GET /me/rules -> {"allow", "deny", "byGuide": [...]}"""
        raise NotImplementedError

    def schedule(self):
        """GET /me/schedule -> {"blocks": [...]}"""
        raise NotImplementedError

    def schedule_conflicts(self):
        """GET /me/schedule-conflicts -> {"conflicts": [...]}"""
        raise NotImplementedError

    def classes(self):
        """GET /me/classes -> [ {classId, name, windows, tzOffsetMin, guide, activeSession} ]"""
        raise NotImplementedError

    def post_schedule_skip(self, block_id, reason, day):
        """POST /me/schedule/skips {blockId, reason, date}"""
        raise NotImplementedError

    # ---- the student's right to see and to leave ----
    def notifications(self, limit=50):
        """GET /me/notifications -> {"notifications": [...], "unread": n, ...}"""
        raise NotImplementedError

    def mark_notifications_read(self):
        """POST /me/notifications/read-all"""
        raise NotImplementedError

    def audit(self, limit=200):
        """GET /me/audit -> [{at, actorName, action, detail}]  (every guide action, append-only)"""
        raise NotImplementedError

    def shared_log(self, limit=200):
        """GET /me/shared-log -> [{at, kind, count, fields, deviceId}]  (what this student's devices uploaded)"""
        raise NotImplementedError

    def revoke_link(self, link_id):
        """POST /links/:id/revoke -- the student ends a link at once. The guide loses access immediately."""
        raise NotImplementedError

    def redeem_code(self, code):
        """POST /links/redeem {code} -- link to a guide who issued a code."""
        raise NotImplementedError
