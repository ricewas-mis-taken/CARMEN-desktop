"""Loads and saves config.json (app blocklist + domain allow list defaults,
last-used settings)."""
import copy
import json
import os
import threading
from datetime import datetime, timezone

CONFIG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "private", "config.json")

DEFAULT_CONFIG = {
    "processBlocklist": [],
    "domainWhitelist": [],
    "last_duration_minutes": 25,
    "last_lock_mode": "soft",
    # Bumped only by set_focus_rules() (the browser-extension cross-profile
    # sync path, api_server.py's /api/focus/rules). Existing callers that
    # write domainWhitelist directly (POST /whitelist/domains, calendar_gui's
    # defaults) don't touch these, so they're specifically "when did the
    # synced ruleset last change" rather than "when did domainWhitelist last
    # change" in general.
    "focusRulesVersion": 0,
    "focusRulesUpdatedAt": None,
}


def load_config():
    if not os.path.exists(CONFIG_PATH):
        # Deep copy — DEFAULT_CONFIG's list values must never be handed out
        # by reference, or an in-place mutation on a caller's "loaded"
        # config (e.g. .append()) would silently corrupt the module-level
        # default for the rest of the process's life.
        defaults = copy.deepcopy(DEFAULT_CONFIG)
        save_config(defaults)
        return defaults

    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError):
        # A corrupt/truncated config.json (e.g. from a crash mid-write)
        # must not crash the whole app on startup — fall back to defaults
        # instead, same as session_manager's state file handling.
        return copy.deepcopy(DEFAULT_CONFIG)

    merged = copy.deepcopy(DEFAULT_CONFIG)
    merged.update(data)
    return merged


def save_config(config):
    # Atomic write — config.json is written from both the Flask thread and
    # the Tkinter GUI thread; a plain in-place write killed mid-save would
    # leave a truncated file that crashes the next load_config() call.
    # private/ (gitignored, holds every real data file) won't exist yet on
    # a fresh clone.
    os.makedirs(os.path.dirname(CONFIG_PATH), exist_ok=True)
    tmp_path = CONFIG_PATH + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(config, f, indent=2)
    os.replace(tmp_path, CONFIG_PATH)


# Guards the read-modify-write in set_focus_rules() — Flask serves each
# request on its own worker thread, so two POST /api/focus/rules calls
# landing close together could otherwise both read the same starting
# version and each save with the same bumped value, silently losing one of
# the two updates instead of producing two distinct versions.
_focus_rules_lock = threading.Lock()


def set_focus_rules(domain_whitelist):
    """Persists domain_whitelist as the synced browser-extension ruleset and
    bumps focusRulesVersion/focusRulesUpdatedAt so polling clients (see
    carmen-extension/core/rules-client.js) can cheaply detect the change.
    Returns the updated config dict."""
    with _focus_rules_lock:
        cfg = load_config()
        cfg["domainWhitelist"] = list(domain_whitelist)
        cfg["focusRulesVersion"] = int(cfg.get("focusRulesVersion") or 0) + 1
        cfg["focusRulesUpdatedAt"] = datetime.now(timezone.utc).isoformat()
        save_config(cfg)
        return cfg
