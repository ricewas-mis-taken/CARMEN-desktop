"""Loads and saves config.json (whitelist defaults, last-used settings)."""
import copy
import json
import os

CONFIG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.json")

DEFAULT_CONFIG = {
    "processWhitelist": [],
    "domainWhitelist": [],
    "last_duration_minutes": 25,
    "last_lock_mode": "soft",
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
    tmp_path = CONFIG_PATH + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(config, f, indent=2)
    os.replace(tmp_path, CONFIG_PATH)
