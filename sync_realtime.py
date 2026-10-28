"""A live connection to Supabase Realtime, so a change made on another computer
reaches this one in well under a second instead of at the next poll.

It does not carry the data. Supabase tells us "a row in sync_records changed";
we answer by running sync_live's ordinary pass right away, which pulls and
applies it exactly as a poll would. One code path, and a lost message costs
nothing: polling is still there as the safety net (every FALLBACK_SECONDS).

The connection is only held when it can help:
  * the account uses more than one computer (multi_device), and
  * a session or review is running, or someone has touched this computer in the
    last IDLE_AFTER_SECONDS.
Otherwise nothing is connected and the slow poll does all the work; the moment
someone touches an idle computer, it connects and catches up at once.

If the live connection cannot be had (a network that blocks it, Realtime not
switched on in Supabase), polling speeds back up to RETRY_SECONDS so nothing
gets slower than before this module existed.
"""
import ctypes
import json
import sys
import threading
import time
from urllib.parse import quote, urlparse

import websockets
from websockets.sync.client import connect as _real_connect

import auth_manager
import device_id
import multi_device
import review_store
import session_manager
import sync_cloud
import sync_live
from calendar_log import logger

IDLE_AFTER_SECONDS = 5 * 60
HEARTBEAT_SECONDS = 20
SILENCE_LIMIT_SECONDS = 60       # nothing heard (not even a heartbeat reply): the line is dead
TOKEN_REFRESH_SECONDS = 25 * 60  # logins last an hour
FALLBACK_SECONDS = 30            # poll interval while live (or while there is no reason to be live)
RETRY_SECONDS = 5                # poll interval while live is wanted but not working
MAX_BACKOFF_SECONDS = 60
SETUP_BACKOFF_SECONDS = 300      # the server refused the subscription: do not hammer it
JOIN_TIMEOUT_SECONDS = 10
CHANNEL_TOPIC = "realtime:carmen-sync"

_connect = _real_connect        # tests swap in a scripted socket
_clock = time.monotonic
_state = {"connected": False}
_enabled = True
_stop = threading.Event()


class _SetupRefused(Exception):
    pass


# --- when is a live connection worth having? ---

def _idle_seconds():
    """Seconds since the last keyboard or mouse input on this computer. Where
    that can't be read, 0 (never idle): staying connected is the safe error."""
    if sys.platform != "win32":
        return 0.0

    class _LastInput(ctypes.Structure):
        _fields_ = [("cbSize", ctypes.c_uint), ("dwTime", ctypes.c_uint)]

    try:
        info = _LastInput()
        info.cbSize = ctypes.sizeof(_LastInput)
        if not ctypes.windll.user32.GetLastInputInfo(ctypes.byref(info)):
            return 0.0
        return ((ctypes.windll.kernel32.GetTickCount() - info.dwTime) & 0xFFFFFFFF) / 1000.0
    except Exception:
        return 0.0


def _something_running():
    try:
        status = session_manager.get_status()
        return bool(status["isActive"] or status.get("parkedSessions") or review_store.get_active_review())
    except Exception:
        return True


def wanted():
    if not _enabled or not auth_manager.is_logged_in() or not multi_device.has_other_device():
        return False
    return _something_running() or _idle_seconds() < IDLE_AFTER_SECONDS


def poll_seconds():
    """How long sync_live waits between its own passes."""
    if _state["connected"]:
        return FALLBACK_SECONDS
    return RETRY_SECONDS if wanted() else FALLBACK_SECONDS


# --- the connection ---

def _url():
    base = urlparse(auth_manager.SUPABASE_URL or "")
    if not base.netloc or not auth_manager.SUPABASE_PUBLISHABLE_KEY:
        raise _SetupRefused("Supabase is not configured")
    return (f"wss://{base.netloc}/realtime/v1/websocket"
            f"?apikey={quote(auth_manager.SUPABASE_PUBLISHABLE_KEY)}&vsn=1.0.0")


def _message(topic, event, payload, ref, join_ref=None):
    return json.dumps({"topic": topic, "event": event, "payload": payload, "ref": ref, "join_ref": join_ref})


def _join_message(user_id, token):
    return _message(CHANNEL_TOPIC, "phx_join", {
        "config": {
            "postgres_changes": [
                {"event": "*", "schema": "public", "table": "sync_records", "filter": f"user_id=eq.{user_id}"}
            ],
            "private": False,
        },
        "access_token": token,
    }, "1", "1")


def _is_news_for_us(message, me):
    """True if a postgres_changes message is a change from some other computer
    to one of the tables sync_live handles."""
    data = (message.get("payload") or {}).get("data") or {}
    record = data.get("record") if isinstance(data, dict) else None
    if not isinstance(record, dict):
        return True                     # cannot tell: look
    table = record.get("table_name")
    if table is not None and table not in sync_cloud.FAST_LANE_TABLES:
        return False
    return record.get("device_id") != me


def _is_slow_lane_news(message, me):
    """True if a postgres_changes message is another computer's change to a
    table the slower sync_client lane handles (review, tasks, board, ...)."""
    data = (message.get("payload") or {}).get("data") or {}
    record = data.get("record") if isinstance(data, dict) else None
    if not isinstance(record, dict):
        return False
    table = record.get("table_name")
    if table is None or table in sync_cloud.FAST_LANE_TABLES:
        return False
    return record.get("device_id") != me


def _session():
    """One connection, from connecting until it ends. Returns why it ended:
    "idle" (no longer wanted), "dropped" (the line went away or went silent)."""
    user = auth_manager.get_current_user()
    token = auth_manager.get_access_token()
    if not user or not token:
        return "dropped"
    me = device_id.get_device_id()
    try:
        return _run_connection(user, token, me)
    finally:
        _state["connected"] = False


def _run_connection(user, token, me):
    with _connect(_url(), open_timeout=10, close_timeout=2, max_size=2 ** 20) as ws:
        ws.send(_join_message(user["id"], token))
        joined = False
        started = last_heard = last_heartbeat = last_token = _clock()
        ref = 1
        while not _stop.is_set():
            now = _clock()
            if joined and not wanted():
                return "idle"
            if not joined and now - started > JOIN_TIMEOUT_SECONDS:
                return "dropped"
            if joined and now - last_heard > SILENCE_LIMIT_SECONDS:
                return "dropped"
            if joined and now - last_heartbeat >= HEARTBEAT_SECONDS:
                ref += 1
                ws.send(_message("phoenix", "heartbeat", {}, str(ref)))
                last_heartbeat = now
            if joined and now - last_token >= TOKEN_REFRESH_SECONDS:
                auth_manager.refresh_access_token()
                fresh = auth_manager.get_access_token()
                if fresh:
                    ref += 1
                    ws.send(_message(CHANNEL_TOPIC, "access_token", {"access_token": fresh}, str(ref), "1"))
                last_token = now
            try:
                raw = ws.recv(timeout=1.0)
            except TimeoutError:
                continue
            except (OSError, websockets.exceptions.WebSocketException):
                return "dropped"
            last_heard = _clock()
            try:
                message = json.loads(raw)
            except (TypeError, ValueError):
                continue
            if not isinstance(message, dict):
                continue
            event = message.get("event")
            if event == "phx_reply" and message.get("ref") == "1":
                payload = message.get("payload") or {}
                if payload.get("status") != "ok":
                    reason = (payload.get("response") or {}).get("reason", "the subscription was refused")
                    raise _SetupRefused(str(reason))
                joined = True
                _state["connected"] = True
                sync_live.poke()          # catch up on anything missed while not connected
            elif event == "postgres_changes" and _is_news_for_us(message, me):
                sync_live.poke()
            elif event == "postgres_changes" and _is_slow_lane_news(message, me):
                # Review, tasks, board and events ride the slower lane; without
                # this they only arrived on its 5-minute timer.
                import sync_trigger
                sync_trigger.note_change()
            elif event in ("phx_close", "phx_error") or (
                event == "system" and (message.get("payload") or {}).get("status") == "error"
            ):
                return "dropped"
    return "idle"


# --- the loop ---

def enable():
    global _enabled
    _enabled = True


def disable():
    global _enabled
    _enabled = False


def start(stop_event):
    """Starts the listener thread and tells sync_live to ask us how long to wait."""
    global _stop
    _stop = stop_event
    sync_live.set_interval_provider(poll_seconds)
    thread = threading.Thread(target=_run, daemon=True)
    thread.start()
    return thread


def _run():
    failures = 0
    warned = False
    while not _stop.is_set():
        if not wanted():
            _state["connected"] = False
            _stop.wait(2.0)
            continue
        try:
            reason = _session()
            failures = 0 if reason == "idle" else failures + 1
            warned = False
            delay = 1.0 if reason == "idle" else min(MAX_BACKOFF_SECONDS, 2.0 ** min(failures, 6))
        except _SetupRefused as exc:
            failures += 1
            if not warned:
                logger.warning(
                    "sync_realtime: Realtime is not available (%s). If you have not yet, run "
                    "sync_server/migrations/002_realtime.sql in the Supabase SQL editor. "
                    "Polling every %s s meanwhile.", exc, RETRY_SECONDS)
                warned = True
            delay = SETUP_BACKOFF_SECONDS
        except Exception as exc:
            failures += 1
            delay = min(MAX_BACKOFF_SECONDS, 2.0 ** min(failures, 6))
            logger.debug("sync_realtime: connection failed: %s", exc)
        finally:
            _state["connected"] = False
        _stop.wait(delay)
