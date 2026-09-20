"""Category classification for screentime_tab.py's grouped view.

Domains are looked up against the bundled list in screentime_domains.json --
a hand-curated set of well-known sites merged with ~45k domains imported
from the UT1 blacklists (github.com/olbat/ut1-blacklists, CC BY-SA), kept
fresh by scripts/update_screentime_domains.py. See that file's own
"_comment" for the exact breakdown and why "Tools" stays mostly
hand-curated. Apps are looked up against the small _APP_CATEGORIES map
below -- there's no equivalent public dataset for executables (see that
map's own comment for what was actually checked). Anything not found in
either falls back to "Other".
"""
import json
import os

CATEGORIES = ["Entertainment", "Education", "Games", "Tools", "Other"]

_DOMAINS_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "screentime_domains.json")

# A handful of common desktop apps -- nowhere near exhaustive. Unlike the
# domain list, there's no free bulk "exe name -> category" dataset to pull
# from: Steam's own launch-executable metadata (appinfo.vdf) is exactly the
# right shape of data, but the only public mirror of it
# (github.com/WindowsGSM/SteamAppInfo) is scoped to dedicated-server
# binaries, not the games themselves, and every general Steam-games dataset
# (Kaggle, HuggingFace) only has title/genre/price, never the executable
# filename. PCGamingWiki tracks a real "Executable name" field per game but
# now requires bot-password auth for API access, so it's no longer a
# zero-friction free source either. This stays hand-maintained -- add
# entries here (never guessed from a game's title alone, to avoid silently
# miscategorizing something) when you spot a missing one.
_APP_CATEGORIES = {
    "steam.exe": "Games",
    "epicgameslauncher.exe": "Games",
    "riotclientservices.exe": "Games",
    "leagueclient.exe": "Games",
    "valorant.exe": "Games",
    "javaw.exe": "Games",
    "minecraft.exe": "Games",
    "battle.net.exe": "Games",
    "csgo.exe": "Games",
    "cs2.exe": "Games",
    "dota2.exe": "Games",
    "amongus.exe": "Games",
    "terraria.exe": "Games",
    "stardewvalley.exe": "Games",
    "robloxplayerbeta.exe": "Games",
    "overwatch.exe": "Games",
    "eldenring.exe": "Games",
    "cyberpunk2077.exe": "Games",
    "witcher3.exe": "Games",
    # Apex Legends' actual process is r5apex.exe (its old codename, "r5",
    # never got renamed) -- easy to miss if you're only going by the store
    # listing name.
    "r5apex.exe": "Games",
    "destiny2.exe": "Games",
    "warframe.x64.exe": "Games",
    "gta5.exe": "Games",
    "pubg.exe": "Games",
    "factorio.exe": "Games",
    "spotify.exe": "Entertainment",
    "vlc.exe": "Entertainment",
    "discord.exe": "Entertainment",
    "netflix.exe": "Entertainment",
    "plexmediaplayer.exe": "Entertainment",
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
    "notion.exe": "Tools",
    "obs64.exe": "Tools",
    # Browsers -- previously missing entirely, so time spent in any of them
    # fell to "Other" at the app level (the domain the browser is actually
    # on is still tracked separately and correctly categorized).
    "chrome.exe": "Tools",
    "msedge.exe": "Tools",
    "firefox.exe": "Tools",
    "brave.exe": "Tools",
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
