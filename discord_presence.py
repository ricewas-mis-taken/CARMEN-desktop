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
POLL_SECONDS = 2
SETTLE_POLLS = 2      # a card must read the same this many polls in a row before it is shown
MIN_GAP_SECONDS = 15  # Discord throttles presence updates


def describe(status, review, show_names=False):
    """The card to show now, as Rich Presence fields, or None when idle.

    A countdown (pomodoro focus or rest, a custom length) shows Discord's own "12:30 left" through `end`;
    a stopwatch (until burnout, a review) shows elapsed time through `start`; a pause shows neither."""
    if status.get("isActive"):
        pomo = status.get("pomodoro")
        on_break = bool(status.get("isBreak"))
        if status.get("isPaused") and not on_break:
            return None   # a pause is not "doing something": no card, so a parked session never reads as live
        reviewing = status.get("source") == "review"
        if reviewing:
            details, state = "Reviewing", "Problem review"
            if show_names and status.get("reviewProblemName"):
                details = f"Reviewing: {status['reviewProblemName']}"
        elif pomo:
            details = "Taking a break" if on_break else "Pomodoro focus"
            state = f"{_minutes(pomo.get('breakMinutes'))} break" if on_break else f"{_minutes(pomo.get('focusMinutes'))} focus"
        elif status.get("isBurnout"):
            details, state = "Working until burnout", "No time limit"
        else:
            details, state = "Focus session", "Locked in"
            length = _span_minutes(status.get("startTime"), status.get("endTime"))
            if length:
                state = f"{_minutes(length)} session"
        if show_names and not reviewing and status.get("eventTitle"):
            details = f"{details}: {status['eventTitle']}"
        fields = {"details": details, "state": state}
        if pomo and not on_break:
            fields["party_size"] = [int(pomo["currentCycle"]), int(pomo["totalCycles"])]   # "Cycle (2 of 4)"
        countdown = bool(pomo) or not (reviewing or status.get("isBurnout"))
        if countdown and _epoch(status.get("endTime")):
            fields["end"] = _epoch(status["endTime"])
        elif _epoch(status.get("startTime")):
            fields["start"] = _epoch(status["startTime"])
        return fields
    if review:
        details = "Reviewing"
        if show_names and review.get("problemName"):
            details = f"Reviewing: {review['problemName']}"
        if review.get("isPaused"):
            return None
        return {"details": details, "state": "Problem review"}
    return None


def _minutes(value):
    try:
        n = int(round(float(value)))
    except (TypeError, ValueError):
        return ""
    if n >= 60 and n % 60 == 0:
        return f"{n // 60} h"
    return f"{n} min"


def _span_minutes(start_iso, end_iso):
    a, b = _epoch(start_iso), _epoch(end_iso)
    return round((b - a) / 60) if a and b and b > a else 0


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
        self._candidate = "unset"
        self._seen = 0
        self._sent_at = 0.0

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
        if shown != self._candidate:
            self._candidate, self._seen = shown, 1
        else:
            self._seen += 1
        if shown == self._last or self._seen < SETTLE_POLLS:
            return   # quick back-and-forth switching never reaches Discord
        if time.monotonic() - self._sent_at < MIN_GAP_SECONDS and self._last != "unset":
            return
        self._sent_at = time.monotonic()
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
