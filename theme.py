"""Colour themes, shared by the desktop app and (through GET /theme) the browser extension.

A theme is six colours. Everything else is derived, so a custom theme can't end up
with unreadable text on a card:

    ink      text and the main buttons
    muted    secondary text
    faint    hints, placeholders
    accent   focus rings, selected states, links
    page     the soft background behind the cards
    sidebar  the navigation column

Cards stay white in every theme; the Home page sky (see sky()) follows the theme too. Dark themes would need every white-on-ink label
swapped too, which this stylesheet does not do yet, so there are none.

The stylesheet (styles.qss) was written against the first theme's literal colours.
`apply_to_stylesheet` swaps those literals for the chosen theme's, in one pass.
"""
import json
import os
import re

TOKENS = ("ink", "muted", "faint", "accent", "page", "sidebar")

# Why these: each is a colour with a known effect on how people work, not a fashion.
THEMES = {
    "Dusk": dict(
        label="Dusk", why="Violet evening. Calm and a little dreamy, easy on the eyes for long sessions.",
        ink="#241B4F", muted="#6B6196", faint="#A59DC9", accent="#7B5FD3", page="#F1EDFB", sidebar="#221A52",
        sky=("#2A2060", "#4C3A8C", "#7A62B4")),
    "Ember": dict(
        label="Ember", why="Glowing coals at dusk. Warm colours push you to start the task you are avoiding.",
        ink="#3A1B20", muted="#85555A", faint="#C29A98", accent="#E2603B", page="#FCF0EA", sidebar="#3A1822",
        sky=("#3A1232", "#8A2F46", "#E8805F")),
    "Tide": dict(
        label="Tide", why="Deep teal water. Cool and steady, good for reading and review.",
        ink="#0E2F3A", muted="#4F7782", faint="#93B7BE", accent="#1E9FAA", page="#EAF6F7", sidebar="#0D2D3C",
        sky=("#0A2742", "#14607B", "#3DB6B0")),
    "Aurora": dict(
        label="Aurora", why="Northern lights over a dark sky. Green is restful and keeps the page lively.",
        ink="#10301F", muted="#4F7A62", faint="#93BBA4", accent="#33B787", page="#EBF8F1", sidebar="#0F2A26",
        sky=("#0D1F3C", "#1A5A6A", "#47C79C")),
}
DEFAULT_THEME = "Dusk"

_HEX = re.compile(r"^#[0-9A-Fa-f]{6}$")

# The literals styles.qss uses, and which token (plus a darkening for the tints) each stands for.
_QSS_MAP = {
    "#1B2230": ("ink", 0), "#1F2328": ("ink", 0),
    "#667085": ("muted", 0), "#5A6070": ("muted", 0), "#5B6472": ("muted", 0),
    "#98A2B3": ("faint", 0), "#A3ABB8": ("faint", 0),
    "#5B8DEF": ("accent", 0),
    "#F7F7F8": ("page", 0), "#F7F8FA": ("page", 0),
    "#F2F4F7": ("page", 0.025), "#F1F3F6": ("page", 0.03), "#F2F4F8": ("page", 0.025),
    "#EEF0F4": ("page", 0.045), "#ECEEF1": ("page", 0.05),
    "#161B26": ("sidebar", 0),
}


def _valid(value):
    return isinstance(value, str) and bool(_HEX.match(value))


def darken(hex_color, amount):
    if not amount:
        return hex_color.upper()
    channels = [int(hex_color[i:i + 2], 16) for i in (1, 3, 5)]
    return "#" + "".join(f"{round(c * (1 - amount)):02X}" for c in channels)


def resolve(setting):
    """The six colours for a saved setting like {'name': 'Moss'} or
    {'name': 'Custom', 'custom': {'accent': '#...'}}. Bad or missing values
    fall back to the named theme, then to the default, never to an error."""
    setting = setting if isinstance(setting, dict) else {}
    base = THEMES.get(setting.get("name"), THEMES[DEFAULT_THEME])
    colours = {key: base[key] for key in TOKENS}
    custom = setting.get("custom")
    if isinstance(custom, dict):
        for key in TOKENS:
            if _valid(custom.get(key)):
                colours[key] = custom[key].upper()
    return colours


def sky(setting=None):
    """(top, middle, bottom) colours of the animated Home sky for a saved setting. A custom theme
    gets one made from its own sidebar and accent."""
    setting = setting if isinstance(setting, dict) else {}
    base = THEMES.get(setting.get("name"), THEMES[DEFAULT_THEME])
    colours = resolve(setting)
    if isinstance(setting.get("custom"), dict) and setting["custom"]:
        return colours["sidebar"], darken(colours["accent"], 0.35), colours["accent"]
    return base["sky"]


def current_sky():
    try:
        return sky(load_setting())
    except Exception:
        return sky(None)


def apply_to_stylesheet(qss, colours):
    def swap(match):
        entry = _QSS_MAP.get(match.group(0).upper())
        if entry is None:
            return match.group(0)
        token, amount = entry
        return darken(colours[token], amount)

    return re.sub(r"#[0-9A-Fa-f]{6}\b", swap, qss)


# --- saved choice -------------------------------------------------------------------------

def load_setting():
    import config
    return config.load_config().get("theme") or {"name": DEFAULT_THEME}


def save_setting(setting):
    import config

    def mutate(cfg):
        cfg["theme"] = setting
    config.update_config(mutate)


def current_colours():
    try:
        return resolve(load_setting())
    except Exception:
        return resolve(None)


def catalogue():
    """What GET /theme returns: the chosen colours plus every named theme."""
    setting = load_setting()
    return {
        "name": setting.get("name", DEFAULT_THEME) if isinstance(setting, dict) else DEFAULT_THEME,
        "colours": resolve(setting),
        "themes": {name: {key: t[key] for key in TOKENS} for name, t in THEMES.items()},
    }


def startup_colours():
    """The colours to build the interface with at launch. Read-only (never creates
    config.json), and the default under pytest so a developer's chosen theme can't
    change what the tests see."""
    import sys
    if "pytest" in sys.modules:
        return resolve(None)
    try:
        import config
        with open(config.CONFIG_PATH, "r", encoding="utf-8") as f:
            return resolve(json.load(f).get("theme"))
    except (OSError, ValueError, AttributeError):
        return resolve(None)
