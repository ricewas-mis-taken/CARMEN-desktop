"""Per-Chrome/Edge-profile identification on macOS.

Windows reads an AppUserModelID off each window. macOS has no such thing, but
every Chromium window's Accessibility title ends with the profile name:

    "Example Domain - Google Chrome - Lucas"
    "Calendar - Google Chrome - Lucas (stu.powayusd.com)"

(see PORT_SPEC.md, Subsystem 3 spike). The name is matched against the
browser's own Local State `profile.info_cache` to recover the profile folder,
and the folder is turned into the *same* AUMI string Windows uses
("Chrome", "Chrome.UserData.Profile4", "MSEdge...") so a profile block means
the same thing on both platforms.

Everything here is pure data lookup apart from window_title_for(), which is
the only Accessibility call; callers can pass titles in directly.
"""
import json
import os

from ApplicationServices import (
    AXUIElementCopyAttributeValue,
    AXUIElementCreateApplication,
    kAXFocusedWindowAttribute,
    kAXTitleAttribute,
    kAXWindowsAttribute,
)

_SUPPORT = os.path.expanduser("~/Library/Application Support")

# keyed by lower-case macOS process name
BROWSERS = {
    "google chrome": {
        "label": "Google Chrome",
        "data_dir": os.path.join(_SUPPORT, "Google", "Chrome"),
        "aumi": "Chrome",
    },
    "microsoft edge": {
        "label": "Microsoft Edge",
        "data_dir": os.path.join(_SUPPORT, "Microsoft Edge"),
        "aumi": "MSEdge",
    },
}


def browser_for(process_name):
    return BROWSERS.get((process_name or "").strip().lower())


def is_profile_browser(process_name):
    return browser_for(process_name) is not None


def folder_to_aumi(process_name, folder):
    b = browser_for(process_name)
    if not b:
        return None
    return b["aumi"] if folder == "Default" else f"{b['aumi']}.UserData.{folder.replace(' ', '')}"


def read_profiles(process_name):
    """{folder: [names the browser may show for it]} from Local State, or {}
    on any failure (missing, malformed, mid-write)."""
    b = browser_for(process_name)
    if not b:
        return {}
    try:
        with open(os.path.join(b["data_dir"], "Local State"), "r", encoding="utf-8") as f:
            info_cache = json.load(f).get("profile", {}).get("info_cache", {})
        out = {}
        for folder, info in info_cache.items():
            names = []
            for key in ("shortcut_name", "name"):
                v = (info.get(key) or "").strip()
                if v and v not in names:
                    names.append(v)
            if names:
                out[folder] = names
        return out
    except Exception:
        return {}


def profile_display_names(process_name):
    """{folder: preferred display name}, shortcut_name first like Windows."""
    return {folder: names[0] for folder, names in read_profiles(process_name).items()}


def list_known_aumis(process_name):
    return [folder_to_aumi(process_name, f) for f in read_profiles(process_name)]


def folder_for_title(process_name, title, profiles=None):
    """The profile folder a window title belongs to, or None.

    The longest matching profile-name suffix wins, so "Lucas (stu.x.com)" is
    not mistaken for "Lucas". A title ending with only the browser name has no
    profile suffix -- Chrome omits it when there is a single profile -- and
    maps to Default."""
    b = browser_for(process_name)
    if not b or not title:
        return None
    if profiles is None:
        profiles = read_profiles(process_name)
    best = None
    for folder, names in profiles.items():
        for name in names:
            if title.endswith(f" - {b['label']} - {name}") or title == f"{b['label']} - {name}":
                if best is None or len(name) > best[0]:
                    best = (len(name), folder)
    if best:
        return best[1]
    if title.endswith(f" - {b['label']}") or title == b["label"]:
        return "Default" if (not profiles or "Default" in profiles) else None
    return None


def aumi_for_title(process_name, title, profiles=None):
    folder = folder_for_title(process_name, title, profiles)
    return folder_to_aumi(process_name, folder) if folder else None


def describe_aumi(process_name, aumi):
    """Picker label, same shape as Windows: "<process> — <profile name>"."""
    if not aumi:
        return process_name
    b = browser_for(process_name)
    prefix = b["aumi"] if b else None
    if prefix and aumi == prefix:
        folder = "Default"
    elif prefix and aumi.startswith(f"{prefix}.UserData."):
        suffix = aumi.split(".UserData.", 1)[1]
        folder = next(
            (f for f in read_profiles(process_name) if f.replace(" ", "") == suffix), suffix
        )
    else:
        return f"{process_name} — {aumi}"
    return f"{process_name} — {profile_display_names(process_name).get(folder) or folder}"


def _ax_title(window):
    err, title = AXUIElementCopyAttributeValue(window, kAXTitleAttribute, None)
    return None if err else title


def window_title_for(pid, focused_only=True):
    """AXTitle of pid's focused window (or every window as a list)."""
    try:
        app_ref = AXUIElementCreateApplication(pid)
        if focused_only:
            err, window = AXUIElementCopyAttributeValue(app_ref, kAXFocusedWindowAttribute, None)
            return None if err or window is None else _ax_title(window)
        err, windows = AXUIElementCopyAttributeValue(app_ref, kAXWindowsAttribute, None)
        if err or not windows:
            return []
        return [(w, _ax_title(w)) for w in windows]
    except Exception:
        return None if focused_only else []
