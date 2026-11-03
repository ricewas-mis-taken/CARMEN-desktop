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
    """The card to show now, as Rich Presence fields, or None when idle.

    A countdown (pomodoro focus or rest, a custom length) shows Discord's own "12:30 left" through `end`;
    a stopwatch (until burnout, a review) shows elapsed time through `start`; a pause shows neither."""
    if status.get("isActive"):
        pomo = status.get("pomodoro")
        on_break = bool(status.get("isBreak"))
        paused = bool(status.get("isPaused")) and not on_break
        reviewing = status.get("source") == "review"
        if reviewing:
            details, state = "Reviewing", "Problem review"
            if show_names and status.get("reviewProblemName"):
                details = f"Reviewing: {status['reviewProblemName']}"
        elif pomo:
            details, state = ("Taking a break", "Rest") if on_break else ("Pomodoro focus", "Cycle")
        elif status.get("isBurnout"):
            details, state = "Working until burnout", "No time limit"
        else:
            details, state = "Focus session", "Locked in"
        if show_names and not reviewing and status.get("eventTitle"):
            details = f"{details}: {status['eventTitle']}"
        fields = {"details": details, "state": "Paused" if paused else state}
        if pomo and not on_break:
            fields["party_size"] = [int(pomo["currentCycle"]), int(pomo["totalCycles"])]   # "Cycle (2 of 4)"
        countdown = bool(pomo) or not (reviewing or status.get("isBurnout"))
        if not paused:
            if countdown and _epoch(status.get("endTime")):
                fields["end"] = _epoch(status["endTime"])
            elif _epoch(status.get("startTime")):
                fields["start"] = _epoch(status["startTime"])
        return fields
    if review:
        details = "Reviewing"
        if show_names and review.get("problemName"):
            details = f"Reviewing: {review['problemName']}"
        return {"details": details, "state": "Paused" if review.get("isPaused") else "Problem review"}
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
        fields = {**shown, "large_image": "carmen", "large_text": "Carmen Focus"}
        self._client.update(**fields)


def start(stop_event):
    presence = Presence(stop_event)
    presence.start()
    return presence
