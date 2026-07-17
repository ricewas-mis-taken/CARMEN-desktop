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
from flask import Flask, jsonify, request
from flask_cors import CORS

import config
import installed_apps
import session_history
import session_manager
import window_tracker

app = Flask(__name__)

# Localhost-only API, so permissive CORS is fine — this explicitly allows a
# chrome-extension:// origin (the browser extension) alongside anything else,
# since the server only ever listens on 127.0.0.1 regardless.
CORS(app, resources={r"/*": {"origins": "*"}})


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

    if not isinstance(duration_minutes, (int, float)) or duration_minutes <= 0:
        return jsonify({"error": "duration_minutes must be a positive number"}), 400
    if lock_mode not in ("soft", "hard"):
        return jsonify({"error": "lock_mode must be 'soft' or 'hard'"}), 400

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
        duration_minutes, lock_mode, process_whitelist, domain_whitelist
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


@app.route("/whitelist/domains/add", methods=["POST"])
def whitelist_domains_add():
    """Adds a single domain to the active session's domainWhitelist, with a
    required reason logged for the audit trail (session_manager's
    domainWhitelistAdditions) — for unblocking a site mid-session without
    ending it. Only makes sense while a session is actually running."""
    if not session_manager.is_active():
        return jsonify({"error": "no active session"}), 400

    body = request.get_json(force=True, silent=True) or {}
    domain = body.get("domain")
    reason = body.get("reason")

    if not isinstance(domain, str) or not domain.strip():
        return jsonify({"error": "domain must be a non-empty string"}), 400
    if not isinstance(reason, str) or not reason.strip():
        return jsonify({"error": "reason must be a non-empty string"}), 400

    domain_whitelist, addition = session_manager.add_domain_to_whitelist(
        domain.strip(), reason.strip()
    )
    return jsonify({"domainWhitelist": domain_whitelist, "addition": addition})


def run_server():
    app.run(host="127.0.0.1", port=5847, debug=False, use_reloader=False)
