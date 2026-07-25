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
    POST /whitelist/apps
    POST /whitelist/apps/add
    GET  /whitelist/domains
    POST /whitelist/domains
    POST /whitelist/domains/add

This is the shared source of truth for focus session state: both this
desktop app and the separate browser extension read/write the same session
through this API instead of tracking their own state. It's also the
boundary Carmen's main system will call into once this module runs as an
independent process — see README.md for the documented contract.

The whitelist picker and the start-session timer are now a native Tkinter
GUI (picker_gui.py, launched from the tray menu) rather than a served web
page — /apps/installed and /whitelist/apps remain here as the same API
surface for any other caller (e.g. Carmen) to drive the same picks
programmatically.
"""
import threading

from flask import Flask, jsonify, request
from flask_cors import CORS

import config
import installed_apps
import review_store
import session_history
import session_manager
import window_tracker

app = Flask(__name__)

# Localhost-only API, so permissive CORS is fine — this explicitly allows a
# chrome-extension:// origin (the browser extension) alongside anything else,
# since the server only ever listens on 127.0.0.1 regardless.
CORS(app, resources={r"/*": {"origins": "*"}})

API_PORT = 5847

# Set by main.py once its on_quit closure exists (tray icon removal, Qt
# event loop teardown, etc.) — lets singleinstance.py ask a still-running
# instance to shut itself down cleanly over loopback HTTP instead of having
# a brand-new instance hard-kill it and leave its tray icon/socket behind.
_quit_callback = None


def register_quit_callback(fn):
    global _quit_callback
    _quit_callback = fn


@app.route("/internal/quit", methods=["POST"])
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
def session_start():
    body = request.get_json(force=True, silent=True) or {}

    duration_minutes = body.get("duration_minutes")
    lock_mode = body.get("lock_mode")
    process_whitelist = body.get("process_whitelist")
    domain_whitelist = body.get("domain_whitelist")
    # "manual" (popup/picker_gui, using the saved whitelist) or
    # "calendar-event" (a temporary per-session override tagged with the
    # event it came from) — purely descriptive, surfaced back through
    # GET /status so the extension popup can show why the active whitelist
    # doesn't match the user's manually saved one.
    source = body.get("source", "manual")
    event_id = body.get("event_id")
    event_title = body.get("event_title")

    if not isinstance(duration_minutes, (int, float)) or duration_minutes <= 0:
        return jsonify({"error": "duration_minutes must be a positive number"}), 400
    if lock_mode not in ("soft", "hard"):
        return jsonify({"error": "lock_mode must be 'soft' or 'hard'"}), 400
    if source not in ("manual", "calendar-event"):
        return jsonify({"error": "source must be 'manual' or 'calendar-event'"}), 400
    if source == "calendar-event" and (not isinstance(event_id, str) or not event_id.strip()):
        return jsonify({"error": "event_id is required when source is 'calendar-event'"}), 400

    if process_whitelist is None:
        # Caller didn't send one (e.g. the browser extension, which no
        # longer collects an app whitelist and sends null) — fall back to
        # whatever was last saved on this side via the app picker, instead
        # of rejecting the request or wiping the whitelist out.
        process_whitelist = config.load_config().get("processWhitelist", [])
    elif not isinstance(process_whitelist, list):
        return jsonify({"error": "process_whitelist must be a list, null, or omitted"}), 400

    if not isinstance(domain_whitelist, list):
        return jsonify({"error": "domain_whitelist must be a list of domain/URL substrings"}), 400

    result = session_manager.start_session(
        duration_minutes,
        lock_mode,
        process_whitelist,
        domain_whitelist,
        source=source,
        event_id=event_id,
        event_title=event_title,
    )
    return jsonify(result)


@app.route("/session/end", methods=["POST"])
def session_end():
    result = session_manager.end_session()
    return jsonify(result)


@app.route("/session/pause", methods=["POST"])
def session_pause():
    """Freezes the countdown only — the session stays active and lock
    enforcement (handled entirely by the extension/window_tracker, not this
    endpoint) is untouched. Idempotent: no active session, or a session
    that's already paused, just returns the current status unchanged."""
    return jsonify(session_manager.pause_session())


@app.route("/session/resume", methods=["POST"])
def session_resume():
    """Resumes the countdown from exactly where it was frozen. Idempotent:
    no active session, or a session that isn't paused, just returns the
    current status unchanged."""
    return jsonify(session_manager.resume_session())


@app.route("/violation", methods=["POST"])
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
    """Every completed session — start/end time, lock mode, the whitelists
    used, and every violation with its resolution time/duration if any. Same
    data the tray's "Session History" viewer shows."""
    return jsonify(session_history.load_all())


@app.route("/apps/running", methods=["GET"])
def apps_running():
    return jsonify(window_tracker.list_running_apps())


@app.route("/apps/installed", methods=["GET"])
def apps_installed():
    return jsonify(installed_apps.list_installed_apps())


@app.route("/whitelist/apps", methods=["POST"])
def whitelist_apps():
    body = request.get_json(force=True, silent=True) or {}
    process_whitelist = body.get("process_whitelist")

    if not isinstance(process_whitelist, list):
        return jsonify({"error": "process_whitelist must be a list of process names"}), 400

    cfg = config.load_config()
    cfg["processWhitelist"] = list(process_whitelist)
    config.save_config(cfg)
    return jsonify({"processWhitelist": cfg["processWhitelist"]})


@app.route("/whitelist/domains", methods=["GET"])
def whitelist_domains_get():
    """Returns config.json's global domainWhitelist — the same "manual/
    on-demand default" processWhitelist already is. Meant for the browser
    extension to poll so a domain-whitelist edit made on the desktop side
    (calendar_gui.py's event editor defaults new focus profiles from this
    same field) shows up on the extension side too."""
    cfg = config.load_config()
    return jsonify({"domainWhitelist": cfg.get("domainWhitelist", [])})


@app.route("/whitelist/domains", methods=["POST"])
def whitelist_domains_set():
    """Overwrites config.json's global domainWhitelist — the domain
    counterpart to POST /whitelist/apps. Meant to be called by the browser
    extension whenever its own domain whitelist changes, so that edit is
    reflected back into the desktop app (and from there, into any calendar
    event's default domain picks) instead of the two sides silently
    diverging. Deliberately separate from POST /whitelist/domains/add below,
    which only ever touches the *active session's* domainWhitelist and
    requires a reason — this endpoint is the same "just replace the saved
    default" shape as /whitelist/apps, with no session or reason involved."""
    body = request.get_json(force=True, silent=True) or {}
    domain_whitelist = body.get("domain_whitelist")

    if not isinstance(domain_whitelist, list) or not all(isinstance(d, str) for d in domain_whitelist):
        return jsonify({"error": "domain_whitelist must be a list of strings"}), 400

    cfg = config.load_config()
    cfg["domainWhitelist"] = list(domain_whitelist)
    config.save_config(cfg)
    return jsonify({"domainWhitelist": cfg["domainWhitelist"]})


@app.route("/whitelist/apps/add", methods=["POST"])
def whitelist_apps_add():
    """Adds a single process to the active session's processWhitelist, with a
    required reason logged for the audit trail (session_manager's
    processWhitelistAdditions) — the API-level counterpart to the lock
    overlay's own "Whitelist" button (enforcer.py), for any other caller
    (e.g. Carmen) that wants to drive the same mid-session unblock."""
    body = request.get_json(force=True, silent=True) or {}
    process_name = body.get("process_name")
    reason = body.get("reason")

    if not isinstance(process_name, str) or not process_name.strip():
        return jsonify({"error": "process_name must be a non-empty string"}), 400
    if not isinstance(reason, str) or not reason.strip():
        return jsonify({"error": "reason must be a non-empty string"}), 400

    # is_active() is checked atomically inside add_process_to_whitelist,
    # under the same lock as the write itself, rather than as a separate
    # check-then-act step here — a session can end in the gap between a
    # pre-check and the write actually happening.
    process_whitelist, addition = session_manager.add_process_to_whitelist(
        process_name.strip(), reason.strip()
    )
    if addition is None:
        return jsonify({"error": "no active session"}), 409
    return jsonify({"processWhitelist": process_whitelist, "addition": addition})


@app.route("/whitelist/domains/add", methods=["POST"])
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

    # See whitelist_apps_add() above for why is_active() is checked
    # atomically inside add_domain_to_whitelist rather than as a separate
    # pre-check here.
    domain_whitelist, addition = session_manager.add_domain_to_whitelist(
        domain.strip(), reason.strip()
    )
    if addition is None:
        return jsonify({"error": "no active session"}), 409
    return jsonify({"domainWhitelist": domain_whitelist, "addition": addition})


@app.route("/review/topics", methods=["GET"])
def review_topics_list():
    return jsonify(review_store.list_topics())


@app.route("/review/topics", methods=["POST"])
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
def review_problem_start(problem_id):
    token = review_store.start_review(problem_id)
    if token is None:
        return jsonify({"error": "problem not found"}), 404
    return jsonify({"sessionToken": token})


@app.route("/review/problems/<int:problem_id>/finish", methods=["POST"])
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
