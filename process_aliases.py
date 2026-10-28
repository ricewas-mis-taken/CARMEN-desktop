"""Cross-platform process-name matching.

The same app has a different process name on each OS ("chrome.exe" on
Windows, "Google Chrome" on macOS). Block lists store whatever name the
picker handed over, so a block made on one computer would never match on the
other. canonical() reduces a name to a shared key: lowercase, no ".exe", no
spaces, then a small alias table for apps whose two names differ by more than
that. Comparing canonical keys makes "discord.exe" == "Discord" and
"chrome.exe" == "Google Chrome" without changing what is stored or synced.
"""

_ALIASES = {
    "googlechrome": "chrome",
    "microsoftedge": "msedge",
    "code": "vscode",
    "visualstudiocode": "vscode",
    "msteams": "teams",
    "ms-teams": "teams",
    "microsoftteams": "teams",
    "winword": "word",
    "microsoftword": "word",
    "microsoftexcel": "excel",
    "powerpnt": "powerpoint",
    "microsoftpowerpoint": "powerpoint",
    "microsoftoutlook": "outlook",
}

# Browsers where every profile shares one OS process (canonical keys).
MULTI_PROFILE_BROWSERS = {"chrome", "msedge"}


def canonical(process_name):
    if not process_name:
        return ""
    key = str(process_name).strip().lower()
    if key.endswith(".exe"):
        key = key[:-4]
    key = key.replace(" ", "")
    return _ALIASES.get(key, key)


def same_process(a, b):
    ca = canonical(a)
    return bool(ca) and ca == canonical(b)


def is_multi_profile_browser(process_name):
    return canonical(process_name) in MULTI_PROFILE_BROWSERS
