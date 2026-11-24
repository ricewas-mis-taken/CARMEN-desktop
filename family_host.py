"""The thin adapter between family_sync.py and the rest of the desktop app.

family_sync decides; this is the only place it touches session_manager, config, session_history, screentime_store
and the toast. Everything it does to a session goes through the same public functions the tray and the local API
use (start_session, update_blocklist, end_session), so a session a guide set is enforced by exactly the same
code as one the student started: the same window tracker, the same lock overlay, the same history.

Nothing here runs unless the family features are switched on (family_sync only calls it from a pass, and a pass
does nothing when "familyEnabled" is off).
"""
import logging
from datetime import datetime

logger = logging.getLogger("carmen_family")

APP_VERSION = "desktop"


class DesktopHost:
    def session_view(self):
        """The running session, in the keys family_rules.decide and family_report read."""
        import session_manager
        s = session_manager.get_status()
        return {
            "isActive": bool(s.get("isActive")), "isPaused": bool(s.get("isPaused")), "isBreak": bool(s.get("isBreak")),
            "sessionId": s.get("sessionId"), "lockMode": s.get("lockMode"),
            "domainWhitelist": list(s.get("domainWhitelist") or []), "processBlocklist": list(s.get("processBlocklist") or []),
            "guide": s.get("guide"), "source": s.get("source"), "startTime": s.get("startTime"),
            "secondsRemaining": s.get("secondsRemaining"), "violationCount": s.get("violationCount"),
            "violationLog": s.get("violationLog") or [], "eventTitle": s.get("eventTitle"),
            "reviewProblemName": s.get("reviewProblemName"),
        }

    # ---- acting on a session ----
    def start_session(self, step):
        """Starts the session a guide asked for. The lock mode and the allowed sites come from the guide; the apps that
        are blocked are the student's own saved list (a guide sets sites, not apps). -> the new session's id."""
        import config
        import session_manager
        cfg = config.load_config()
        guide = step["guide"]
        title = guide.get("className") or ("Study block" if guide.get("kind") == "block" else f"Session from {guide.get('name')}")
        status = session_manager.start_session(
            step["minutes"], step["lockMode"], list(cfg.get("processBlocklist") or []), list(step["allow"]),
            source="guide", event_title=title,
            blocked_browser_profiles=list(cfg.get("browserProfileBlocklist") or []),
            hide_taskbar_badges=bool(cfg.get("last_hide_taskbar_badges")),
            stop_taskbar_flashing=bool(cfg.get("last_stop_taskbar_flashing")),
            guide=guide)
        return status.get("sessionId")

    def update_session(self, step):
        """Brings the running guide session in step with the guide's current rules (sites and lock), keeping it running."""
        import session_manager
        status = session_manager.get_status()
        if not status.get("isActive") or not status.get("guide"):
            return
        session_manager.update_blocklist(status.get("processBlocklist") or [], list(step["allow"]), lock_mode=step.get("lockMode"))
        if step.get("guide"):
            session_manager.update_guide(step["guide"])

    def end_session(self, step):
        import session_manager
        session_manager.end_session(end_type=step.get("endType") or "manual", reason=step.get("reason"))

    # ---- facts ----
    def enforcing(self, session):
        """Whether this computer is enforcing right now: not while a session is paused or on a break."""
        return not (session.get("isActive") and (session.get("isPaused") or session.get("isBreak")))

    def device_id(self):
        import device_id
        return device_id.get_device_id()

    def app_version(self):
        return APP_VERSION

    def now_local_iso(self):
        return datetime.now().isoformat()

    def history_after(self, watermark):
        import session_history
        try:
            return session_history.entries_ended_after(watermark)
        except Exception:
            logger.warning("could not read the session history", exc_info=True)
            return []

    def domain_seconds_today(self):
        """(local day, {website: seconds}) for this computer. Website names only: app names are never read here."""
        import screentime_store
        day = datetime.now().strftime("%Y-%m-%d")
        try:
            return day, dict(screentime_store.get_day(day).get("domains") or {})
        except Exception:
            return day, {}

    def notify(self, notice):
        """A desktop notice, so nothing a guide does is silent. Best effort: the note is stored either way."""
        if notice.get("silent"):
            return
        try:
            import calendar_toast
            title = f"Carmen - {notice['by']}" if notice.get("by") else "Carmen"
            calendar_toast.show_toast(title, str(notice.get("text", ""))[:240])
        except Exception:
            pass
