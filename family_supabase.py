"""The Supabase transport for the family features. NOT IMPLEMENTED -- a placeholder with a map.

Today the desktop reaches the family backend through family_rest.py (the CarmenLinked dev backend). When the
family features move to the real Supabase project, this class replaces it: same FamilyTransport methods, same
return shapes (see family_transport.py), so family_sync.py and the UI do not change.

Nothing here talks to Supabase yet, on purpose: the schema in carmenlinked/supabase/ is a design, and a client
written against an unrun design would only pretend to work. Every method raises NotImplementedError and names
the table or function it will use, taken from carmenlinked/supabase/003_family_links.sql and
005_family_features.sql (and 004_projection_trigger.sql for how raw sync rows become what guides can read).

How the REST calls map to Supabase when this is built:

  sign-in / refresh / logout       supabase auth (auth_manager.py already does this for the sync account; the
                                   family account is the same Supabase user, so no second sign-in is needed)
  GET /me                          tables guide_links (student_id = auth.uid()), profiles, devices_registry
  POST /me/heartbeat               table devices_registry (device_id, kind, last_seen, enforcing, rules_state)
  POST /me/status                  table student_status. Clients cannot write it (003 revokes insert/update): the
                                   projection trigger in 004 fills it from the sync_records row 'live_session' that
                                   sync_live.py already uploads. So post_status becomes a no-op here.
  POST /me/summaries               tables session_summaries + session_details, filled the same way from the
                                   'session_history' rows (no-op here). NOTE the trigger maps endType 'manual' to
                                   'ended_early' and everything else to 'completed'; the desktop files a session a
                                   guide ended as 'natural' so it reads as completed, as it does on REST.
  POST /me/screentime              table screentime_samples (check whether a client write policy exists first)
  GET  /me/commands, ack           table guide_commands (status 'pending' -> 'applied'/'declined'); the guide
                                   side inserts through an RPC, never directly
  GET  /me/rules                   table guide_rules (joined to guide_links where can_set_rules and active)
  GET  /me/schedule, skips         table schedule_blocks (+ the my_schedule() function in 005)
  GET  /me/classes                 tables classes, class_windows, class_sessions (via guide_links.class_id)
  GET  /me/notifications           table notifications
  GET  /me/audit                   table audit_log (append-only, readable by the student only)
  GET  /me/shared-log              table shared_log
  POST /links/:id/revoke           RPC revoke_link(link_id)
  POST /links/redeem               RPC redeem_invite(code, scopes, detail)

Realtime: guide_commands and guide_links are the two tables worth a realtime subscription, so a command or a
revocation reaches the desktop in seconds instead of at the next poll. Polling must keep working without it.
"""
from family_transport import FamilyTransport

SUPABASE_TABLES = (
    "profiles", "guide_links", "devices_registry", "student_status", "session_summaries", "session_details",
    "screentime_samples", "guide_commands", "guide_rules", "schedule_blocks", "classes", "class_windows",
    "class_sessions", "notifications", "audit_log", "shared_log", "requests", "invites",
)


class SupabaseFamilyTransport(FamilyTransport):
    name = "supabase"

    def _todo(self, what):
        raise NotImplementedError(
            f"The Supabase family transport is not written yet ({what}). "
            f"See the table map in family_supabase.py; the tables are {', '.join(SUPABASE_TABLES)}.")

    def configure(self, base):
        self._todo("configure")

    def login(self, email, password):
        self._todo("sign-in: reuse auth_manager's Supabase session")

    def logout(self, everywhere=False):
        self._todo("sign-out")

    def has_saved_login(self):
        self._todo("saved sign-in")

    def is_signed_in(self):
        self._todo("signed-in check")

    def ensure_fresh(self):
        self._todo("token renewal: supabase-py renews on its own")

    def me(self):
        self._todo("tables guide_links, profiles, devices_registry")

    def heartbeat(self, payload):
        self._todo("table devices_registry")

    def post_status(self, payload):
        self._todo("table student_status")

    def post_summaries(self, items):
        self._todo("tables session_summaries, session_details")

    def post_screentime(self, items):
        self._todo("table screentime_samples")

    def commands(self):
        self._todo("table guide_commands")

    def ack_command(self, command_id, applied):
        self._todo("table guide_commands")

    def rules(self):
        self._todo("table guide_rules")

    def schedule(self):
        self._todo("table schedule_blocks / function my_schedule")

    def schedule_conflicts(self):
        self._todo("table schedule_blocks + class_windows")

    def classes(self):
        self._todo("tables classes, class_windows, class_sessions")

    def post_schedule_skip(self, block_id, reason, day):
        self._todo("table schedule_blocks (skips)")

    def notifications(self, limit=50):
        self._todo("table notifications")

    def mark_notifications_read(self):
        self._todo("table notifications")

    def audit(self, limit=200):
        self._todo("table audit_log")

    def shared_log(self, limit=200):
        self._todo("table shared_log")

    def revoke_link(self, link_id):
        self._todo("function revoke_link")

    def redeem_code(self, code):
        self._todo("function redeem_invite")
