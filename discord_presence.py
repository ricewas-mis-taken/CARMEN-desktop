"""Shows what Carmen is doing in Discord's "Playing" widget (Rich Presence).

Talks to the Discord desktop app over its local connection, so nothing leaves this computer except
through Discord itself. Task and problem names are only shown when discordShowNames is on."""
import logging
import threading
import time

import config
import review_store
import session_manager

logger = logging.getLogger(__name__)

APPLICATION_ID = "1566985492547895337"
POLL_SECONDS = 5


def describe(status, review, show_names=False):
    """(details, state, started_epoch_or_None) for what is happening now, or None when idle."""
    if status.get("isActive"):
        pomo = status.get("pomodoro")
        paused = bool(status.get("isPaused")) and not status.get("isBreak")
        reviewing = status.get("source") == "review"
        if reviewing:
            details = "Reviewing"
            if show_names and status.get("reviewProblemName"):
                details = f"Reviewing: {status['reviewProblemName']}"
        elif pomo:
            details = "On a break" if status.get("isBreak") else "Pomodoro focus"
        elif status.get("isBurnout"):
            details = "Working until burnout"
        else:
            details = "Focus session"
        if show_names and not reviewing and status.get("eventTitle"):
            details = f"{details}: {status['eventTitle']}"
        parts = []
        if pomo:
            parts.append(f"Cycle {pomo['currentCycle']} of {pomo['totalCycles']}")
        if paused:
            parts.append("paused")
        state = " · ".join(parts) or "In a session"
        started = None if paused or status.get("isBreak") else _epoch(status.get("startTime"))
        return details, state, started
    if review:
        details = "Reviewing"
        if show_names and review.get("problemName"):
            details = f"Reviewing: {review['problemName']}"
        return details, "paused" if review.get("isPaused") else "In a review", None
    return None


def _epoch(iso):
    if not iso:
        return None
    try:
        from datetime import datetime
        return int(datetime.fromisoformat(iso).timestamp())
    except ValueError:
        return None


class Presence(threading.Thread):
    def __init__(self, stop_event):
        super().__init__(daemon=True, name="discord-presence")
        self._stop_event = stop_event
        self._client = None
        self._last = "unset"

    def _connect(self):
        from pypresence import Presence as Client
        client = Client(APPLICATION_ID)
        client.connect()
        self._client = client
        self._last = "unset"

    def _drop(self):
        try:
            if self._client is not None:
                self._client.close()
        except Exception:
            pass
        self._client = None

    def run(self):
        while not self._stop_event.is_set():
            try:
                cfg = config.load_config()
                if not cfg.get("discordPresence", True):
                    if self._client is not None:
                        self._drop()
                else:
                    if self._client is None:
                        self._connect()
                    self._update(cfg)
            except Exception:
                # Discord not running (or closed mid-way): try again later, quietly.
                self._drop()
            self._stop_event.wait(POLL_SECONDS)
        self._drop()

    def _update(self, cfg):
        shown = describe(session_manager.get_status(), review_store.get_active_review(),
                         bool(cfg.get("discordShowNames")))
        if shown == self._last:
            return
        self._last = shown
        if shown is None:
            self._client.clear()
            return
        details, state, started = shown
        fields = {"details": details, "state": state, "large_image": "carmen", "large_text": "Carmen Focus"}
        if started:
            fields["start"] = started
        self._client.update(**fields)


def start(stop_event):
    presence = Presence(stop_event)
    presence.start()
    return presence
