"""Local REST API exposing session control, bound to 127.0.0.1 only.

Endpoints:
    GET  /health
    GET  /status
    POST /session/start
    POST /session/end
    POST /session/pause
    POST /session/resume
    POST /violation
    POST /violation/resolved
    GET  /history
    GET  /apps/running
    GET  /apps/installed
    POST /blocklist/apps
    POST /blocklist/apps/remove
    GET  /whitelist/domains
    POST /whitelist/domains
    POST /whitelist/domains/add
    POST /tasks/<task_id>/domain-whitelist
    GET  /api/focus/rules
    POST /api/focus/rules

This is the shared source of truth for focus session state: both this
desktop app and the separate browser extension read/write the same session
through this API instead of tracking their own state. It's also the
boundary Carmen's main system will call into once this module runs as an
independent process — see README.md for the documented contract.

The blocklist picker and the start-session timer are now a native Tkinter
GUI (picker_gui.py, launched from the tray menu) rather than a served web
page — /apps/installed and /blocklist/apps remain here as the same API
surface for any other caller (e.g. Carmen) to drive the same picks
programmatically.
"""
import functools
import hmac
import math
import re
import threading

from flask import Flask, jsonify, request
from flask_cors import CORS

import config
import installed_apps
import review_store
import session_history
import session_manager
import tasks_store
import window_tracker

app = Flask(__name__)

# Localhost-only API, so permissive CORS is fine — this explicitly allows a
# chrome-extension:// origin (the browser extension) alongside anything else,
# since the server only ever listens on 127.0.0.1 regardless.
#
# /api/focus/rules gets its own, narrower entry (extension origins only)
# per the cross-browser sync design — flask-cors matches the longest/most
# specific resource pattern first, so this takes precedence over the
# blanket "/*" rule below for that one path. Both chrome-extension:// (also
# what Edge uses, being Chromium-based) and moz-extension:// (Firefox) are
# allowed since the same core/rules-client.js polls this route from every
# browser variant.
_EXTENSION_ORIGIN_RE = re.compile(r"^(chrome|moz)-extension://.*$")

CORS(
    app,
    resources={
        r"/api/focus/rules": {"origins": _EXTENSION_ORIGIN_RE},
        r"/*": {"origins": "*"},
    },
)

API_PORT = 5847

# Set by main.py once its on_quit closure exists (tray icon removal, Qt
# event loop teardown, etc.) — lets singleinstance.py ask a still-running
# instance to shut itself down cleanly over loopback HTTP instead of having
# a brand-new instance hard-kill it and leave its tray icon/socket behind.
_quit_callback = None


def register_quit_callback(fn):
    global _quit_callback
    _quit_callback = fn


def _require_token(fn):
    """Guards every state-changing endpoint with a local shared secret (see
    config.get_api_token()) -- this API used to have zero authentication at
    all, so anything that could reach 127.0.0.1:5847 (any process running as
    the same user, not just the browser extension) could end the session,
    kill the app outright (/internal/quit), or unblock any app with a
    one-word "reason". Read-only routes (GET /status, /health, etc.) are
    deliberately left open -- they don't change anything, and the extension
    polls several of them before it's ever paired with a token."""
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        expected = config.get_api_token()
        provided = request.headers.get("X-Carmen-Token", "")
        if not hmac.compare_digest(provided, expected):
            return jsonify({"error": "missing or invalid X-Carmen-Token header"}), 401
        return fn(*args, **kwargs)
    return wrapper


def _is_string_list(value):
    """True if value is a list where every element is a non-empty (after
    stripping whitespace) string. Used to reject process/domain lists whose
    elements aren't strings before they ever reach session_manager -- a bad
    element there raises deep inside is_blocked()/add_domain_to_whitelist()
    (AttributeError from calling .lower() on a non-string). is_blocked() is
    called from window_tracker's polling loop, which wraps each tick in a
    blanket try/except and swallows the error -- so a single non-string
    process/domain entry silently disables ALL enforcement for the rest of
    the session, with no visible error to the user, instead of failing loud
    and clear at the point the bad list was actually submitted."""
    return isinstance(value, list) and all(isinstance(item, str) and item.strip() for item in value)


@app.route("/internal/quit", methods=["POST"])
@_require_token
def internal_quit():
    # Runs the real on_quit() on its own thread rather than inline in this
    # request handler -- on_quit() blocks on Qt/pystray teardown, which
    # would otherwise hold the HTTP response (and this request's worker
    # thread) open until the process is most of the way through exiting.
    if _quit_callback is not None:
        threading.Thread(target=_quit_callback, daemon=True).start()
    return jsonify({"ok": True})


@app.route("/health", methods=["GET"])
def health():
    return jsonify({"ok": True})


@app.route("/status", methods=["GET"])
def status():
    return jsonify(session_manager.get_status())


@app.route("/session/start", methods=["POST"])
@_require_token
def session_start():
    body = request.get_json(force=True, silent=True) or {}

    duration_minutes = body.get("duration_minutes")
    lock_mode = body.get("lock_mode")
    process_blocklist = body.get("process_blocklist")
    domain_whitelist = body.get("domain_whitelist")
    # "manual" (popup/picker_gui, using the saved lists) or "calendar-event"
    # (a temporary per-session override tagged with the event it came from)
    # — purely descriptive, surfaced back through GET /status so the
    # extension popup can show why the active lists don't match the user's
    # manually saved ones.
    source = body.get("source", "manual")
    event_id = body.get("event_id")
    event_title = body.get("event_title")

    # isinstance(duration_minutes, (int, float)) alone lets NaN/Infinity
    # through -- every comparison against NaN (including "<= 0") is False,
    # and "inf <= 0" is also False, so both silently passed this check
    # before math.isfinite() was added here. Downstream, datetime.timedelta
    # can't represent either (ValueError for NaN, OverflowError for
    # Infinity or anything that many minutes converts to), so a session
    # start with duration_minutes: NaN/Infinity/1e18 crashed the request
    # with an unhandled 500 instead of a clean 400. bool is also an int
    # subclass in Python, so duration_minutes: true would otherwise pass as
    # 1 -- excluded explicitly since it's not a meaningful duration.
    if (
        not isinstance(duration_minutes, (int, float))
        or isinstance(duration_minutes, bool)
        or not math.isfinite(duration_minutes)
        or duration_minutes <= 0
        or duration_minutes > 1_000_000
    ):
        return (
            jsonify({"error": "duration_minutes must be a finite positive number (at most 1,000,000 minutes)"}),
            400,
        )
    if lock_mode not in ("soft", "hard"):
        return jsonify({"error": "lock_mode must be 'soft' or 'hard'"}), 400
    if source not in ("manual", "calendar-event"):
        return jsonify({"error": "source must be 'manual' or 'calendar-event'"}), 400
    if source == "calendar-event" and (not isinstance(event_id, str) or not event_id.strip()):
        return jsonify({"error": "event_id is required when source is 'calendar-event'"}), 400

    if process_blocklist is None:
        # Caller didn't send one (e.g. the browser extension, which no
        # longer collects an app blocklist and sends null) — fall back to
        # whatever was last saved on this side via the app picker, instead
        # of rejecting the request or wiping the blocklist out.
        process_blocklist = config.load_config().get("processBlocklist", [])
    elif not _is_string_list(process_blocklist):
        return jsonify({"error": "process_blocklist must be a list of non-empty strings, null, or omitted"}), 400

    if not _is_string_list(domain_whitelist):
        return jsonify({"error": "domain_whitelist must be a list of non-empty domain/URL strings"}), 400

    result = session_manager.start_session(
        duration_minutes,
        lock_mode,
        process_blocklist,
        domain_whitelist,
        source=source,
        event_id=event_id,
        event_title=event_title,
    )
    return jsonify(result)


@app.route("/session/end", methods=["POST"])
@_require_token
def session_end():
    result = session_manager.end_session()
    return jsonify(result)


@app.route("/session/pause", methods=["POST"])
@_require_token
def session_pause():
    """Freezes the countdown only — the session stays active and lock
    enforcement (handled entirely by the extension/window_tracker, not this
    endpoint) is untouched. Idempotent: no active session, or a session
    that's already paused, just returns the current status unchanged."""
    return jsonify(session_manager.pause_session())


@app.route("/session/resume", methods=["POST"])
@_require_token
def session_resume():
    """Resumes the countdown from exactly where it was frozen. Idempotent:
    no active session, or a session that isn't paused, just returns the
    current status unchanged."""
    return jsonify(session_manager.resume_session())


@app.route("/violation", methods=["POST"])
@_require_token
def violation():
    """Called by the browser extension whenever the active tab's domain
    isn't in domain_whitelist during an active session — increments the
    same violation_count/violationLog GET /status returns, alongside this
    app's own process-based violations."""
    body = request.get_json(force=True, silent=True) or {}
    url = body.get("url")

    if not isinstance(url, str) or not url:
        return jsonify({"error": "url must be a non-empty string"}), 400

    violation_count = session_manager.record_domain_violation(url)
    return jsonify({"violationCount": violation_count})


@app.route("/violation/resolved", methods=["POST"])
@_require_token
def violation_resolved():
    """Called by the browser extension when the active tab is back on an
    allowed domain — closes out the open domain violation (if any) so its
    duration ("how long before returning to correct") gets recorded. Body is
    currently just {"type": "domain"} — process-side resolution already
    happens automatically via this app's own window-polling loop, so there's
    nothing else for a caller to resolve today, but the type field keeps the
    door open."""
    body = request.get_json(force=True, silent=True) or {}
    violation_type = body.get("type", "domain")

    if violation_type != "domain":
        return jsonify({"error": "type must be 'domain'"}), 400

    session_manager.resolve_domain_violation()
    return jsonify(session_manager.get_status())


@app.route("/history", methods=["GET"])
def history():
    """Every completed session — start/end time, lock mode, the app block
    list and domain allow list used, and every violation with its
    resolution time/duration if any. Same data the tray's "Session History"
    viewer shows."""
    return jsonify(session_history.load_all())


@app.route("/apps/running", methods=["GET"])
def apps_running():
    return jsonify(window_tracker.list_running_apps())


@app.route("/apps/installed", methods=["GET"])
def apps_installed():
    return jsonify(installed_apps.list_installed_apps())


@app.route("/blocklist/apps", methods=["POST"])
@_require_token
def blocklist_apps():
    body = request.get_json(force=True, silent=True) or {}
    process_blocklist = body.get("process_blocklist")

    if not _is_string_list(process_blocklist):
        return jsonify({"error": "process_blocklist must be a list of non-empty process names"}), 400

    def _mutate(cfg):
        cfg["processBlocklist"] = list(process_blocklist)

    cfg = config.update_config(_mutate)
    return jsonify({"processBlocklist": cfg["processBlocklist"]})


@app.route("/whitelist/domains", methods=["GET"])
def whitelist_domains_get():
    """Returns config.json's global domainWhitelist — the same "manual/
    on-demand default" processBlocklist already is. Meant for the browser
    extension to poll so a domain-allow-list edit made on the desktop side
    (calendar_gui.py's event editor defaults new focus profiles from this
    same field) shows up on the extension side too."""
    cfg = config.load_config()
    return jsonify({"domainWhitelist": cfg.get("domainWhitelist", [])})


@app.route("/whitelist/domains", methods=["POST"])
@_require_token
def whitelist_domains_set():
    """Overwrites config.json's global domainWhitelist — the domain
    counterpart to POST /blocklist/apps. Meant to be called by the browser
    extension whenever its own domain allow list changes, so that edit is
    reflected back into the desktop app (and from there, into any calendar
    event's default domain picks) instead of the two sides silently
    diverging. Deliberately separate from POST /whitelist/domains/add below,
    which only ever touches the *active session's* domainWhitelist and
    requires a reason — this endpoint is the same "just replace the saved
    default" shape as /blocklist/apps, with no session or reason involved."""
    body = request.get_json(force=True, silent=True) or {}
    domain_whitelist = body.get("domain_whitelist")

    if not _is_string_list(domain_whitelist):
        return jsonify({"error": "domain_whitelist must be a list of non-empty strings"}), 400

    def _mutate(cfg):
        cfg["domainWhitelist"] = list(domain_whitelist)

    cfg = config.update_config(_mutate)
    return jsonify({"domainWhitelist": cfg["domainWhitelist"]})


@app.route("/blocklist/apps/remove", methods=["POST"])
@_require_token
def blocklist_apps_remove():
    """Removes a single process from the active session's processBlocklist,
    with a required reason logged for the audit trail (session_manager's
    processBlocklistExceptions) — the API-level counterpart to the lock
    overlay's own "Unblock" button (enforcer.py), for any other caller
    (e.g. Carmen) that wants to drive the same mid-session unblock."""
    body = request.get_json(force=True, silent=True) or {}
    process_name = body.get("process_name")
    reason = body.get("reason")

    if not isinstance(process_name, str) or not process_name.strip():
        return jsonify({"error": "process_name must be a non-empty string"}), 400
    if not isinstance(reason, str) or not reason.strip():
        return jsonify({"error": "reason must be a non-empty string"}), 400

    # is_active() is checked atomically inside remove_process_from_blocklist,
    # under the same lock as the write itself, rather than as a separate
    # check-then-act step here — a session can end in the gap between a
    # pre-check and the write actually happening.
    process_blocklist, exception_entry = session_manager.remove_process_from_blocklist(
        process_name.strip(), reason.strip()
    )
    if exception_entry is None:
        return jsonify({"error": "no active session"}), 409
    return jsonify({"processBlocklist": process_blocklist, "exception": exception_entry})


@app.route("/whitelist/domains/add", methods=["POST"])
@_require_token
def whitelist_domains_add():
    """Adds a single domain to the active session's domainWhitelist, with a
    required reason logged for the audit trail (session_manager's
    domainWhitelistAdditions) — for unblocking a site mid-session without
    ending it. Only makes sense while a session is actually running."""
    body = request.get_json(force=True, silent=True) or {}
    domain = body.get("domain")
    reason = body.get("reason")

    if not isinstance(domain, str) or not domain.strip():
        return jsonify({"error": "domain must be a non-empty string"}), 400
    if not isinstance(reason, str) or not reason.strip():
        return jsonify({"error": "reason must be a non-empty string"}), 400

    # See blocklist_apps_remove() above for why is_active() is checked
    # atomically inside add_domain_to_whitelist rather than as a separate
    # pre-check here.
    domain_whitelist, addition = session_manager.add_domain_to_whitelist(
        domain.strip(), reason.strip()
    )
    if addition is None:
        return jsonify({"error": "no active session"}), 409
    return jsonify({"domainWhitelist": domain_whitelist, "addition": addition})


@app.route("/tasks/<task_id>/domain-whitelist", methods=["POST"])
@_require_token
def task_domain_whitelist_add(task_id):
    """Merges `domains` into task_id's own saved domainWhitelist (tasks_store,
    not session_manager's active-session list above) -- called by the browser
    extension's post-session "save these sites to this task?" prompt, so a
    site allowed on the fly (via /whitelist/domains/add) during one session
    on this task is already allowed the next time this task starts one.
    Case-insensitive dedupe against what's already saved; existing entries
    and their original casing are left untouched."""
    body = request.get_json(force=True, silent=True) or {}
    domains = body.get("domains")

    if not _is_string_list(domains):
        return jsonify({"error": "domains must be a list of non-empty strings"}), 400

    task = tasks_store.get_task(task_id)
    if task is None:
        return jsonify({"error": "task not found"}), 404

    existing = list(task.get("domainWhitelist") or [])
    existing_lower = {d.lower() for d in existing}
    for domain in domains:
        domain = domain.strip()
        if domain.lower() not in existing_lower:
            existing.append(domain)
            existing_lower.add(domain.lower())

    updated = tasks_store.update_task(task_id, {"domainWhitelist": existing})
    return jsonify({"domainWhitelist": updated["domainWhitelist"]})


@app.route("/api/focus/rules", methods=["GET"])
def focus_rules_get():
    """Returns the synced domain-whitelist ruleset for the browser extension
    (every Chrome profile, Edge, and Firefox instance on this machine poll
    this same route — see carmen-extension/core/rules-client.js) alongside
    version/updatedAt so clients can cheaply tell whether it changed since
    their last fetch instead of re-diffing the whole list every time."""
    cfg = config.load_config()
    return jsonify(
        {
            "domainWhitelist": cfg.get("domainWhitelist", []),
            "version": cfg.get("focusRulesVersion", 0),
            "updatedAt": cfg.get("focusRulesUpdatedAt"),
        }
    )


@app.route("/api/focus/rules", methods=["POST"])
@_require_token
def focus_rules_set():
    """Replaces the synced domain-whitelist ruleset and bumps its version, so
    every other browser instance's next poll picks up the change. Called by
    the extension whenever the user edits their saved whitelist in the popup
    (see chrome/popup/popup.js and firefox/popup/popup.js).

    baseVersion (optional) is the version the caller's edit was based on. If
    it doesn't match the server's current version — another instance pushed
    a change this caller never polled — the edit is merged (union + dedupe)
    with the current list instead of overwriting it outright, so a stale
    push can't silently erase someone else's addition. See
    config.set_focus_rules() for why a merge can only ever add domains, not
    remove them."""
    body = request.get_json(force=True, silent=True) or {}
    domain_whitelist = body.get("domainWhitelist")
    base_version = body.get("baseVersion")

    if not isinstance(domain_whitelist, list) or not all(
        isinstance(d, str) for d in domain_whitelist
    ):
        return jsonify({"error": "domainWhitelist must be a list of strings"}), 400
    if base_version is not None and not isinstance(base_version, int):
        return jsonify({"error": "baseVersion must be an integer or omitted"}), 400

    cfg, conflict = config.set_focus_rules(domain_whitelist, base_version=base_version)
    return jsonify(
        {
            "domainWhitelist": cfg["domainWhitelist"],
            "version": cfg["focusRulesVersion"],
            "updatedAt": cfg["focusRulesUpdatedAt"],
            "merged": conflict,
        }
    )


@app.route("/review/topics", methods=["GET"])
def review_topics_list():
    return jsonify(review_store.list_topics())


@app.route("/review/topics", methods=["POST"])
@_require_token
def review_topics_create():
    body = request.get_json(force=True, silent=True) or {}
    name = body.get("name")
    if not isinstance(name, str) or not name.strip():
        return jsonify({"error": "name must be a non-empty string"}), 400
    topic = review_store.create_topic(name.strip())
    if topic is None:
        return jsonify({"error": "failed to create topic"}), 500
    return jsonify(topic), 201


@app.route("/review/topics/<int:topic_id>/subjects", methods=["GET"])
def review_subjects_list(topic_id):
    return jsonify(review_store.list_subjects(topic_id))


@app.route("/review/topics/<int:topic_id>/subjects", methods=["POST"])
@_require_token
def review_subjects_create(topic_id):
    body = request.get_json(force=True, silent=True) or {}
    name = body.get("name")
    color = body.get("color")
    if not isinstance(name, str) or not name.strip():
        return jsonify({"error": "name must be a non-empty string"}), 400
    if not isinstance(color, str) or not color.strip():
        return jsonify({"error": "color must be a non-empty hex string"}), 400
    subject = review_store.create_subject(topic_id, name.strip(), color.strip())
    if subject is None:
        return jsonify({"error": "failed to create subject"}), 500
    return jsonify(subject), 201


@app.route("/review/topics/<int:topic_id>/problems", methods=["GET"])
def review_problems_list(topic_id):
    due_only = request.args.get("due_only", "true").lower() != "false"
    return jsonify(review_store.list_problems(topic_id, due_only=due_only))


@app.route("/review/topics/<int:topic_id>/problems", methods=["POST"])
@_require_token
def review_problems_create(topic_id):
    name = (request.form.get("name") or "").strip()
    subject_id = request.form.get("subject_id")
    stars = request.form.get("stars")
    description_type = request.form.get("description_type")

    if not name:
        return jsonify({"error": "name must be a non-empty string"}), 400
    try:
        subject_id = int(subject_id)
    except (TypeError, ValueError):
        return jsonify({"error": "subject_id must be an integer"}), 400
    try:
        stars = int(stars)
        if not (1 <= stars <= 5):
            raise ValueError
    except (TypeError, ValueError):
        return jsonify({"error": "stars must be an integer between 1 and 5"}), 400
    if description_type not in ("text", "photo", "link"):
        return jsonify({"error": "description_type must be 'text', 'photo', or 'link'"}), 400

    description_text = None
    description_link = None
    photo_bytes = None
    photo_filename = None

    if description_type == "text":
        description_text = (request.form.get("description_text") or "").strip()
        if not description_text:
            return jsonify({"error": "description_text is required when description_type is 'text'"}), 400
    elif description_type == "link":
        description_link = (request.form.get("description_link") or "").strip()
        if not description_link:
            return jsonify({"error": "description_link is required when description_type is 'link'"}), 400
    else:  # photo
        photo_file = request.files.get("description_photo")
        if photo_file is None or not photo_file.filename:
            return jsonify({"error": "description_photo file is required when description_type is 'photo'"}), 400
        photo_bytes = photo_file.read()
        photo_filename = photo_file.filename

    problem = review_store.create_problem(
        topic_id, subject_id, name, stars, description_type,
        description_text=description_text, description_link=description_link,
        photo_bytes=photo_bytes, photo_filename=photo_filename,
    )
    if problem is None:
        return jsonify({"error": "failed to create problem"}), 500
    return jsonify(problem), 201


@app.route("/review/problems/<int:problem_id>", methods=["GET"])
def review_problem_detail(problem_id):
    problem = review_store.get_problem(problem_id)
    if problem is None:
        return jsonify({"error": "problem not found"}), 404
    return jsonify(problem)


@app.route("/review/problems/<int:problem_id>/start", methods=["POST"])
@_require_token
def review_problem_start(problem_id):
    token = review_store.start_review(problem_id)
    if token is None:
        return jsonify({"error": "problem not found"}), 404
    return jsonify({"sessionToken": token})


@app.route("/review/problems/<int:problem_id>/finish", methods=["POST"])
@_require_token
def review_problem_finish(problem_id):
    body = request.get_json(force=True, silent=True) or {}
    session_token = body.get("session_token")
    if not isinstance(session_token, str) or not session_token:
        return jsonify({"error": "session_token must be a non-empty string"}), 400

    problem = review_store.finish_review(session_token)
    if problem is None:
        return jsonify({"error": "invalid or already-used session_token"}), 409
    if problem["id"] != problem_id:
        return jsonify({"error": "session_token does not belong to this problem"}), 409
    return jsonify(problem)


def run_server():
    app.run(host="127.0.0.1", port=API_PORT, debug=False, use_reloader=False)
