"""Loads and saves config.json (app blocklist + domain allow list defaults,
last-used settings)."""
import copy
import json
import os
import secrets
import shutil
import threading
import time
from datetime import datetime, timezone

import server_clock
import paths

CONFIG_PATH = paths.private_path("config.json")

DEFAULT_CONFIG = {
    "processBlocklist": [],
    "domainWhitelist": [],
    # AppUserModelIDs of Chrome/Edge profile windows to block for just that
    # one profile, saved as the default across sessions the same way
    # processBlocklist is -- see session_manager.MULTI_PROFILE_BROWSER_PROCESSES
    # for why this can't just be another processBlocklist entry.
    "browserProfileBlocklist": [],
    "last_duration_minutes": 25,
    "last_lock_mode": "soft",
    "miniTimerGeometry": None,
    "quickNoteGeometry": None,
    "last_hide_taskbar_badges": False,
    "last_stop_taskbar_flashing": False,
    # Bumped only by set_focus_rules() (the browser-extension cross-profile
    # sync path, api_server.py's /api/focus/rules). Existing callers that
    # write domainWhitelist directly (POST /whitelist/domains, calendar_gui's
    # defaults) don't touch these, so they're specifically "when did the
    # synced ruleset last change" rather than "when did domainWhitelist last
    # change" in general.
    "focusRulesVersion": 0,
    "focusRulesUpdatedAt": None,
    # When the shared settings below last changed on any computer (cloud time).
    "settingsUpdatedAt": None,
}

# The saved defaults every computer should agree on: what a new session blocks
# and allows, and what the Start dialog remembers. Everything else in this file
# is about one computer (window geometry, browser profile ids, the API token).
SHARED_SETTINGS = (
    "processBlocklist", "domainWhitelist", "last_duration_minutes", "last_lock_mode",
    "last_hide_taskbar_badges", "last_stop_taskbar_flashing",
)
_change_listeners = []


def add_change_listener(listener):
    _change_listeners.append(listener)


def shared_settings(cfg):
    return {key: copy.deepcopy(cfg.get(key, DEFAULT_CONFIG[key])) for key in SHARED_SETTINGS}


def load_config():
    # Serialized with update_config()'s write (see _config_lock) -- on Windows
    # an unlocked read overlapping another thread's os.replace() of this file
    # either makes that replace fail (PermissionError) or fails itself, and the
    # OSError fallback below then returned defaults for a perfectly good file.
    with _config_lock:
        return _load_config_locked()


def _load_config_locked():
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

    if not isinstance(data, dict):
        # Valid JSON that is not an object (null, a list, a number): update()
        # would raise, and every caller of load_config() with it.
        return copy.deepcopy(DEFAULT_CONFIG)
    merged = copy.deepcopy(DEFAULT_CONFIG)
    merged.update(data)
    return merged


class ConfigLoadError(Exception):
    """config.json exists but could not be read even after retrying."""


def _load_for_update():
    """load_config() for read-modify-write callers. load_config() maps an
    unreadable/corrupt file to DEFAULT_CONFIG, which is fine for display but
    destructive here: the caller saves the result straight back, wiping the
    user's saved lists and regenerating apiToken. So retry a transient
    OSError (raise if it persists) and back up a corrupt file before
    falling back to defaults."""
    if not os.path.exists(CONFIG_PATH):
        return load_config()
    last_exc = None
    for _ in range(3):
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
            if not isinstance(data, dict):
                raise json.JSONDecodeError("config is not an object", "", 0)   # backed up below like a corrupt file
            merged = copy.deepcopy(DEFAULT_CONFIG)
            merged.update(data)
            return merged
        except json.JSONDecodeError:
            try:
                shutil.copy2(CONFIG_PATH, f"{CONFIG_PATH}.corrupt-{int(time.time())}")
            except OSError as exc:
                raise ConfigLoadError(f"config.json is corrupt and could not be backed up: {exc}") from exc
            return copy.deepcopy(DEFAULT_CONFIG)
        except OSError as exc:
            last_exc = exc
            time.sleep(0.05)
    raise ConfigLoadError(f"failed to read {CONFIG_PATH}: {last_exc}") from last_exc


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
_config_lock = threading.RLock()


def update_config(mutator):
    """Thread-safe read-modify-write: loads the current config, calls
    mutator(cfg) to mutate it (in place, or by returning a replacement
    dict), saves the result, and returns it. See _config_lock above for
    why every config mutation needs to go through this rather than its own
    ad hoc load/mutate/save."""
    with _config_lock:
        cfg = _load_for_update()
        before = shared_settings(cfg)
        replacement = mutator(cfg)
        if replacement is not None:
            cfg = replacement
        changed = shared_settings(cfg) != before
        if changed:
            cfg["settingsUpdatedAt"] = server_clock.next_stamp()
        save_config(cfg)
    if changed:
        for listener in list(_change_listeners):
            try:
                listener()
            except Exception:
                pass
    return cfg


# --- sharing the saved defaults with another computer (see sync_live.py) ---

def export_shared_settings():
    """{"stamp", "settings"} for the other computer. Defaults saved before they
    were shared get a stamp the first time they are sent."""
    with _config_lock:
        cfg = _load_for_update()
        settings = shared_settings(cfg)
        stamp = cfg.get("settingsUpdatedAt")
        if stamp is None and settings != shared_settings({}):
            stamp = cfg["settingsUpdatedAt"] = server_clock.next_stamp()
            save_config(cfg)
    return {"stamp": stamp, "settings": settings}


def _clean_shared_settings(data):
    """Only the sane parts of what another computer sent."""
    if not isinstance(data, dict):
        return {}
    clean = {}
    for key in ("processBlocklist", "domainWhitelist"):
        value = data.get(key)
        if (isinstance(value, list) and len(value) <= 2000
                and all(isinstance(v, str) and v.strip() and len(v) <= 300 for v in value)):
            clean[key] = list(value)
    minutes = data.get("last_duration_minutes")
    if (isinstance(minutes, (int, float)) and not isinstance(minutes, bool)
            and minutes == minutes and 0 < minutes <= 100000):
        clean["last_duration_minutes"] = minutes
    if data.get("last_lock_mode") in ("soft", "hard"):
        clean["last_lock_mode"] = data["last_lock_mode"]
    for key in ("last_hide_taskbar_badges", "last_stop_taskbar_flashing"):
        if isinstance(data.get(key), bool):
            clean[key] = data[key]
    return clean


def apply_remote_settings(settings, stamp):
    """Takes the saved defaults another computer shared, if newer than ours.
    Returns True if they were applied. A changed domain list also bumps the
    focus-rules version so the browser extension picks it up."""
    clean = _clean_shared_settings(settings)
    if not clean or server_clock.parse(stamp) is None:
        return False
    with _config_lock:
        cfg = _load_for_update()
        if not server_clock.is_newer(stamp, cfg.get("settingsUpdatedAt")):
            return False
        domains_changed = "domainWhitelist" in clean and clean["domainWhitelist"] != cfg.get("domainWhitelist")
        cfg.update(copy.deepcopy(clean))
        cfg["settingsUpdatedAt"] = stamp
        if domains_changed:
            cfg["focusRulesVersion"] = int(cfg.get("focusRulesVersion") or 0) + 1
            cfg["focusRulesUpdatedAt"] = datetime.now(timezone.utc).isoformat()
        save_config(cfg)
    return True


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
        # Re-checked under the lock so a stale/empty read above can never
        # overwrite a token another caller already minted.
        if not c.get("apiToken"):
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
        cfg = _load_for_update()
        before = shared_settings(cfg)
        current_version = int(cfg.get("focusRulesVersion") or 0)
        conflict = base_version is not None and int(base_version) != current_version

        if conflict:
            cfg["domainWhitelist"] = _dedupe_domains(cfg.get("domainWhitelist", []), domain_whitelist)
        else:
            cfg["domainWhitelist"] = _dedupe_domains(domain_whitelist)

        cfg["focusRulesVersion"] = current_version + 1
        cfg["focusRulesUpdatedAt"] = datetime.now(timezone.utc).isoformat()
        # The domain list is one of the shared defaults, and this is the path the
        # extension's popup edits it through: without a new stamp (and the wake-up)
        # the other computer never heard about the change.
        changed = shared_settings(cfg) != before
        if changed:
            cfg["settingsUpdatedAt"] = server_clock.next_stamp()
        save_config(cfg)
    if changed:
        for listener in list(_change_listeners):
            try:
                listener()
            except Exception:
                pass
    return cfg, conflict
