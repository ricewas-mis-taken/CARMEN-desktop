"""Loads and saves config.json (app blocklist + domain allow list defaults,
last-used settings)."""
import copy
import json
import os
import secrets
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


# Guards every config.json read-modify-write, not just set_focus_rules()'s
# own (the bug that originally motivated adding a lock here at all: two
# POST /api/focus/rules calls landing close together could otherwise both
# read the same starting version and each save with the same bumped
# value, losing one of the two updates). The same class of race exists for
# every other read-load-mutate-save call site -- api_server.py's Flask
# handlers run on their own worker threads, and qt_ui/picker_dialogs.py's
# Save handlers run on the Qt GUI thread of that same process, so a
# blocklist save from the app-picker dialog landing at the same moment as
# a whitelist push from the browser extension could interleave: both read
# the same starting config.json, and whichever finishes last silently
# overwrites the other's change. Route every config mutation through
# update_config() below instead of a raw load/mutate/save sequence.
_config_lock = threading.Lock()


def update_config(mutator):
    """Thread-safe read-modify-write: loads the current config, calls
    mutator(cfg) to mutate it (in place, or by returning a replacement
    dict), saves the result, and returns it. See _config_lock above for
    why every config mutation needs to go through this rather than its own
    ad hoc load/mutate/save."""
    with _config_lock:
        cfg = load_config()
        replacement = mutator(cfg)
        if replacement is not None:
            cfg = replacement
        save_config(cfg)
        return cfg


def get_api_token():
    """The local shared secret api_server.py requires on every state-changing
    request (see its _require_token decorator) -- generated once, on first
    call, and persisted from then on, rather than living in DEFAULT_CONFIG
    (a real secret can't be a static default shipped in source). The browser
    extension is paired with this value once (tray menu -> Copy API Token,
    pasted into the extension's popup), not fetched over the API itself --
    an unauthenticated endpoint that hands out the auth token would defeat
    the whole point of requiring one."""
    cfg = load_config()
    token = cfg.get("apiToken")
    if token:
        return token

    def _mutate(c):
        c["apiToken"] = secrets.token_hex(32)

    cfg = update_config(_mutate)
    return cfg["apiToken"]


def _dedupe_domains(*domain_lists):
    """Merges any number of domain lists into one, keeping only the first
    occurrence of each domain (case-insensitive, trimmed) — so a domain
    typed as "Example.com" in one browser profile and "example.com" in
    another still collapses to a single entry instead of two."""
    seen = set()
    merged = []
    for domains in domain_lists:
        for domain in domains or []:
            trimmed = (domain or "").strip()
            key = trimmed.lower()
            if not trimmed or key in seen:
                continue
            seen.add(key)
            merged.append(trimmed)
    return merged


def set_focus_rules(domain_whitelist, base_version=None):
    """Persists domain_whitelist as the synced browser-extension ruleset and
    bumps focusRulesVersion/focusRulesUpdatedAt so polling clients (see
    carmen-extension/core/rules-client.js) can cheaply detect the change.

    base_version is the focusRulesVersion the caller's edit was based on
    (whatever it last polled/pushed successfully). Two possibilities:

    - It matches the server's current version (or is omitted) -- no other
      instance changed anything in between, so this is a plain edit
      (including deletions) and the caller's list replaces the old one
      outright.
    - It's stale (another Chrome profile/Edge/Firefox instance pushed a
      change this caller never saw) -- replacing outright would silently
      drop that other edit, so this merges instead: union of the current
      server list and the caller's list, deduped. A merge can't tell
      "caller deleted this domain" apart from "caller's view never
      included it," so conflicting edits only ever grow the list; a plain,
      non-conflicting edit is still how a domain actually gets removed.

    Returns the updated config dict.
    """
    with _config_lock:
        cfg = load_config()
        current_version = int(cfg.get("focusRulesVersion") or 0)
        conflict = base_version is not None and int(base_version) != current_version

        if conflict:
            cfg["domainWhitelist"] = _dedupe_domains(cfg.get("domainWhitelist", []), domain_whitelist)
        else:
            cfg["domainWhitelist"] = _dedupe_domains(domain_whitelist)

        cfg["focusRulesVersion"] = current_version + 1
        cfg["focusRulesUpdatedAt"] = datetime.now(timezone.utc).isoformat()
        save_config(cfg)
        return cfg, conflict
