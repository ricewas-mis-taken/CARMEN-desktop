"""Colour themes, shared by the desktop app and (through GET /theme) the browser extension.

A theme is six colours. Everything else is derived, so a custom theme can't end up
with unreadable text on a card:

    ink      text and the main buttons
    muted    secondary text
    faint    hints, placeholders
    accent   focus rings, selected states, links
    page     the soft background behind the cards
    sidebar  the navigation column

Cards stay white in every theme. Dark themes would need every white-on-ink label
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
    "Paper": dict(
        label="Paper", why="Cool grey and ink blue. Neutral, so nothing competes with the work.",
        ink="#1B2230", muted="#667085", faint="#98A2B3", accent="#5B8DEF", page="#F7F7F8", sidebar="#161B26"),
    "Deep work": dict(
        label="Deep work", why="Navy and steel. Blue is the colour most tied to sustained attention.",
        ink="#14213D", muted="#5A6785", faint="#94A0BA", accent="#2F5DA8", page="#F5F7FB", sidebar="#0F1A33"),
    "Moss": dict(
        label="Moss", why="Sage and warm white. Green is restful, good for long review sessions.",
        ink="#1F2A24", muted="#62705F", faint="#9AA796", accent="#4F8A6B", page="#F6F7F3", sidebar="#17211C"),
    "Clay": dict(
        label="Clay", why="Terracotta on warm paper. Warm hues push you to start, good for a task you are avoiding.",
        ink="#2A1F1B", muted="#7A6A63", faint="#B09F97", accent="#C2623F", page="#FAF6F2", sidebar="#231915"),
    "Graphite": dict(
        label="Graphite", why="Grey with almost no colour. The quietest option, for when everything else is loud.",
        ink="#202124", muted="#6B6F76", faint="#A1A5AB", accent="#4B5563", page="#F6F6F6", sidebar="#1A1B1E"),
}
DEFAULT_THEME = "Paper"

_HEX = re.compile(r"^#[0-9A-Fa-f]{6}$")

# The literals styles.qss uses, and which token (plus a darkening for the tints) each stands for.
_BASE = THEMES[DEFAULT_THEME]
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
