"""Asks you, before you stop for the night, to plan tomorrow.

One background loop does two small jobs every half minute: it marks "someone is at the computer" for
shutdown_model (so Carmen learns when you usually stop), and it checks whether one of tonight's two nudges is due
(30 and 15 minutes before the usual stop). A nudge is a notification with a "Plan tomorrow" button. It is sent once
per night per size, only if tomorrow has no plan yet, only when you are around, and not at all if the app was
closed through the nudge's own moment (a stale nudge after a late start is worse than none)."""
import json
import logging
import os
import threading
from datetime import datetime, timedelta

import config
import paths
import plan_store
import shutdown_model

logger = logging.getLogger(__name__)

SENT_PATH = paths.private_path("plan_prompt_sent.json")
CHECK_SECONDS = 30
IDLE_AFTER_SECONDS = 180      # a mark only counts when there was input in the last few minutes
FRESH_MINUTES = 10            # a nudge more than this late is skipped
MESSAGES = {30: "Half an hour before you usually stop. Plan tomorrow?",
            15: "Fifteen minutes to go. Take a minute to plan tomorrow?"}


def _sent():
    try:
        with open(SENT_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _remember(night_key, lead):
    data = {night_key: sorted(set(_sent().get(night_key, []) + [lead]))}      # older nights are dropped
    os.makedirs(os.path.dirname(SENT_PATH), exist_ok=True)
    tmp = SENT_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f)
    os.replace(tmp, SENT_PATH)


def _tomorrow(now):
    """The day a plan made now is for: tonight's plan is for the next calendar day, and one made after
    midnight (still the same night) is for today."""
    return now.date() if now.hour < shutdown_model.WINDOW_END_HOUR else now.date() + timedelta(days=1)


def due_nudge(now, sent, has_plan, enabled=True, around=True):
    """The lead (30 or 15) of a nudge to send right now, else None. `sent` is {night: [leads already sent]}."""
    if not enabled or has_plan or not around:
        return None
    anchor = now.date() if now.hour >= shutdown_model.WINDOW_END_HOUR else now.date() - timedelta(days=1)
    already = sent.get(anchor.isoformat(), [])
    for lead, when in reversed(shutdown_model.nudge_times(now)):         # the closer nudge first
        if lead in already:
            continue
        late = (now - when).total_seconds() / 60
        if 0 <= late <= FRESH_MINUTES:
            return lead
    return None


def tick(now=None, idle_seconds=None, notify=None):
    """One pass of the loop. Returns the lead sent, or None. `notify(lead, day)` shows the nudge."""
    now = now or datetime.now()
    around = idle_seconds is None or idle_seconds < IDLE_AFTER_SECONDS
    if around:
        shutdown_model.note_active(now)
    enabled = bool(config.load_config().get("nightlyPrompt", True))
    day = _tomorrow(now)
    lead = due_nudge(now, _sent(), plan_store.has_plan(day), enabled, around)
    if lead is None:
        return None
    anchor = now.date() if now.hour >= shutdown_model.WINDOW_END_HOUR else now.date() - timedelta(days=1)
    _remember(anchor.isoformat(), lead)
    if notify is not None:
        notify(lead, day)
    return lead


def _show(lead, day):
    import calendar_toast
    import qt_gui_thread

    def open_plan():
        from qt_ui import plan_dialog
        plan_dialog.open_plan(day)

    def acted(_argument):
        qt_gui_thread.run_on_gui_thread(open_plan)

    calendar_toast.show_toast("Plan tomorrow", MESSAGES[lead], buttons=[("plan", "Plan tomorrow")], on_action=acted)


def start(stop_event):
    import activity

    def run():
        while not stop_event.is_set():
            try:
                tick(idle_seconds=activity.idle_seconds(), notify=_show)
            except Exception:
                logger.exception("plan prompt failed")
            stop_event.wait(CHECK_SECONDS)
    thread = threading.Thread(target=run, daemon=True, name="plan-prompt")
    thread.start()
    return thread
