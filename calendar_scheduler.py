"""Background scheduler thread — the runtime engine behind reminders, the
"focus session starting soon" heads-up, and auto-starting a focus session at
a tracked event's start time.

Runs a simple poll loop (not one-shot timers per event) so it naturally
survives events being added/edited/deleted mid-session and recurring events
being expanded fresh each tick, at the cost of only sub-tick precision —
fine for a reminder system. Everything is wrapped in try/except and logged
to calendar_errors.log, same as calendar_store.py, so a bad event (a
malformed RRULE, a store hiccup) can never take the whole tray app down with
it.
"""
import threading
import time
from datetime import datetime, timedelta

import calendar_recurrence as recurrence
import calendar_store as store
import calendar_toast as toast
import session_manager
from calendar_log import logger

POLL_INTERVAL_SECONDS = 20

# How far past a trigger's exact moment we'll still fire it — covers a
# trigger that fell in the gap between two poll ticks (e.g. the process was
# briefly frozen, or the poll interval itself). Firing something up to this
# late is still useful; anything older is treated as missed rather than
# firing a stale "reminder" long after the fact.
CATCH_UP_WINDOW_SECONDS = POLL_INTERVAL_SECONDS * 3

# Expand recurring events this far into the future each tick — comfortably
# covers any reminder offset a user would set (hours, not days) plus the
# poll/catch-up windows above.
LOOKAHEAD_HOURS = 6

_stop_event = None
_fired = set()  # {(event_id, occ_start_iso, trigger_kind)} already fired
_snoozes = []  # [{"event_id", "occ_start_iso", "title", "fire_at": datetime}]
_snooze_lock = threading.Lock()


def start(stop_event):
    """Starts the scheduler loop in its own daemon thread. Call once from
    main.py, same pattern as window_tracker.run_polling_loop."""
    global _stop_event
    _stop_event = stop_event
    thread = threading.Thread(target=_run, args=(stop_event,), daemon=True)
    thread.start()
    return thread


def _run(stop_event):
    while not stop_event.is_set():
        try:
            _tick()
        except Exception:
            logger.exception("scheduler tick failed")
        stop_event.wait(POLL_INTERVAL_SECONDS)


def _tick():
    now = datetime.now()
    store.purge_expired_soft_deletes()

    events = store.list_events()
    range_end = now + timedelta(hours=LOOKAHEAD_HOURS)

    for event in events:
        try:
            _process_event(event, now, range_end)
        except Exception:
            logger.exception("scheduler failed processing event %s", event.get("id"))

    _process_snoozes(now)
    _prune_fired(now)


def _prune_fired(now):
    """_fired grows for as long as the tray app stays running — drop entries
    for occurrences well outside the current lookahead window so long
    uptimes (this is a persistent background app) don't leak memory over
    weeks."""
    cutoff = now - timedelta(hours=LOOKAHEAD_HOURS, days=1)
    stale = set()
    for key in _fired:
        occ_key = key[2]
        try:
            if datetime.fromisoformat(occ_key) < cutoff:
                stale.add(key)
        except ValueError:
            continue
    _fired.difference_update(stale)


def _process_event(event, now, range_end):
    occurrences = recurrence.expand_occurrences(event, now - timedelta(hours=LOOKAHEAD_HOURS), range_end)
    focus = event.get("focusProfile")

    for occ_start, occ_end in occurrences:
        occ_key = occ_start.isoformat()

        for offset_minutes in event.get("reminderOffsets", []) or []:
            trigger_at = occ_start - timedelta(minutes=offset_minutes)
            _maybe_fire(
                ("reminder", event["id"], occ_key, offset_minutes),
                trigger_at, now,
                lambda ev=event, off=offset_minutes: _fire_reminder(ev, off),
            )

        if focus and focus.get("enabled") and focus.get("warningMinutes") is not None:
            warn_at = occ_start - timedelta(minutes=focus["warningMinutes"])
            _maybe_fire(
                ("focus_warning", event["id"], occ_key),
                warn_at, now,
                lambda ev=event: _fire_focus_warning(ev),
            )

        _maybe_fire(
            ("start", event["id"], occ_key),
            occ_start, now,
            lambda ev=event, oe=occ_end: _fire_event_start(ev, oe),
        )


def _maybe_fire(fired_key, trigger_at, now, action):
    if fired_key in _fired:
        return
    if trigger_at > now:
        return
    if trigger_at < now - timedelta(seconds=CATCH_UP_WINDOW_SECONDS):
        # Missed by more than the catch-up window (app was asleep/closed) —
        # mark as fired without actually firing, rather than showing a
        # reminder for something long past.
        _fired.add(fired_key)
        return
    _fired.add(fired_key)
    action()


def _fire_reminder(event, offset_minutes):
    if offset_minutes <= 0:
        body = f"{event['title']} starts now."
    else:
        body = f"{event['title']} in {offset_minutes} minute(s)."
    toast.show_toast(
        "Reminder",
        body,
        buttons=[
            ("snooze10", "Snooze 10 min"),
            ("snooze1h", "Snooze 1 hour"),
        ],
        on_action=lambda arg: _handle_reminder_action(arg, event),
    )


def _handle_reminder_action(argument, event):
    minutes = {"snooze10": 10, "snooze1h": 60}.get(argument)
    if minutes is None:
        return
    with _snooze_lock:
        _snoozes.append(
            {
                "event_id": event["id"],
                "title": event["title"],
                "fire_at": datetime.now() + timedelta(minutes=minutes),
            }
        )


def _process_snoozes(now):
    with _snooze_lock:
        due = [s for s in _snoozes if s["fire_at"] <= now]
        _snoozes[:] = [s for s in _snoozes if s["fire_at"] > now]
    for snooze in due:
        toast.show_toast(
            "Reminder (snoozed)",
            f"{snooze['title']}",
            buttons=[("snooze10", "Snooze 10 min")],
            on_action=lambda arg, ev=snooze: _handle_reminder_action(
                arg, {"id": ev["event_id"], "title": ev["title"]}
            ),
        )


def _fire_focus_warning(event):
    minutes = event["focusProfile"]["warningMinutes"]
    toast.show_toast(
        "Focus session starting soon",
        f"\"{event['title']}\" starts in {minutes} minute(s).",
    )


def _fire_event_start(event, occ_end):
    focus = event.get("focusProfile")

    if focus and focus.get("enabled"):
        toast.show_toast(
            event["title"],
            f"{event['title']} is starting now. Focus session starting.",
        )
        _start_focus_session(event, occ_end)
    else:
        toast.show_toast(event["title"], f"{event['title']} is starting now.")


def _start_focus_session(event, occ_end):
    focus = event["focusProfile"]

    if session_manager.is_active():
        # Deliberately simple per spec: don't try to stack/queue lock types
        # or reconcile whitelists — the new event's profile wins, same as
        # calling start_session() again from the tray's timer dialog would.
        # start_session() itself finalizes the session being replaced to
        # history first, so this is a visible handoff, not silent data loss.
        logger.warning(
            "Event '%s' focus session starting while another session is already active — "
            "overwriting with this event's profile (last-one-wins).",
            event["title"],
        )

    duration_minutes = max(1, (occ_end - datetime.now()).total_seconds() / 60)
    try:
        session_manager.start_session(
            duration_minutes,
            focus.get("lockMode", "soft"),
            list(focus.get("processWhitelist", [])),
            list(focus.get("domainWhitelist", [])),
            source="calendar-event",
            event_id=event.get("id"),
            event_title=event.get("title"),
        )
    except Exception:
        logger.exception("failed to start focus session for event %s", event.get("id"))
