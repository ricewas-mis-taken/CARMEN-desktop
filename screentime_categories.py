"""Category classification for screentime_tab.py's grouped view.

Domains are looked up against the bundled seed list in
screentime_domains.json (a hand-curated set of well-known sites -- see that
file's own "_comment" for why it isn't the ~20k-domain dataset originally
asked for). Apps are looked up against the small _APP_CATEGORIES map below.
Anything not found in either falls back to "Other".
"""
import json
import os

CATEGORIES = ["Entertainment", "Education", "Games", "Tools", "Other"]

_DOMAINS_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "screentime_domains.json")

# A handful of common desktop apps -- nowhere near exhaustive (there's no
# equivalent of a "top 20k apps" public dataset the way there is for
# websites), just enough that the most common non-browser distractions/tools
# don't all pile into "Other".
_APP_CATEGORIES = {
    "steam.exe": "Games",
    "epicgameslauncher.exe": "Games",
    "riotclientservices.exe": "Games",
    "leagueclient.exe": "Games",
    "valorant.exe": "Games",
    "javaw.exe": "Games",
    "minecraft.exe": "Games",
    "battle.net.exe": "Games",
    "spotify.exe": "Entertainment",
    "vlc.exe": "Entertainment",
    "discord.exe": "Entertainment",
    "netflix.exe": "Entertainment",
    "code.exe": "Tools",
    "devenv.exe": "Tools",
    "pycharm64.exe": "Tools",
    "idea64.exe": "Tools",
    "notepad++.exe": "Tools",
    "cmd.exe": "Tools",
    "powershell.exe": "Tools",
    "pwsh.exe": "Tools",
    "windowsterminal.exe": "Tools",
    "excel.exe": "Tools",
    "word.exe": "Tools",
    "outlook.exe": "Tools",
    "slack.exe": "Tools",
    "teams.exe": "Tools",
    "figma.exe": "Tools",
}


def _load_domain_categories():
    try:
        with open(_DOMAINS_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        return {}
    return {k: v for k, v in data.items() if not k.startswith("_")}


_DOMAIN_CATEGORIES = _load_domain_categories()


def _registrable_domain(domain):
    if not domain:
        return domain
    domain = domain.lower().strip()
    if domain.startswith("www."):
        domain = domain[4:]
    return domain


def categorize_domain(domain):
    key = _registrable_domain(domain)
    if not key:
        return "Other"
    if key in _DOMAIN_CATEGORIES:
        return _DOMAIN_CATEGORIES[key]
    # A subdomain of a known site (mail.google.com) still counts as that
    # site (google.com) for categorization purposes.
    parts = key.split(".")
    if len(parts) > 2:
        parent = ".".join(parts[-2:])
        if parent in _DOMAIN_CATEGORIES:
            return _DOMAIN_CATEGORIES[parent]
    return "Other"


def categorize_app(process_name):
    if not process_name:
        return "Other"
    return _APP_CATEGORIES.get(process_name.lower(), "Other")
