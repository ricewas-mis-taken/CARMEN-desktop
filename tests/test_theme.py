import pytest

import api_server
import theme


def test_default_and_unknown_fall_back():
    assert theme.resolve(None) == theme.resolve({"name": "nope"})
    assert theme.resolve(None)["accent"] == theme.THEMES["Dusk"]["accent"]


def test_custom_overrides_only_valid_colours():
    colours = theme.resolve({"name": "Aurora", "custom": {"accent": "#ff0000", "ink": "red", "page": 5}})
    assert colours["accent"] == "#FF0000"
    assert colours["ink"] == theme.THEMES["Aurora"]["ink"]
    assert colours["page"] == theme.THEMES["Aurora"]["page"]


def test_stylesheet_is_rewritten_in_one_pass():
    qss = "a{color:#1B2230;background:#5b8def} b{border:1px solid #ABCDEF} c{background:#F2F4F7}"
    out = theme.apply_to_stylesheet(qss, theme.resolve({"name": "Ember"}))
    assert theme.THEMES["Ember"]["ink"] in out and theme.THEMES["Ember"]["accent"] in out
    assert "#ABCDEF" in out          # unknown colours are left alone
    assert "#1B2230" not in out and "#5B8DEF" not in out.upper()


def test_default_theme_swaps_the_stylesheet_literals_for_its_own_colours():
    qss = "x{color:#1B2230;background:#F7F7F8}"
    out = theme.apply_to_stylesheet(qss, theme.resolve(None))
    assert theme.THEMES[theme.DEFAULT_THEME]["ink"] in out and theme.THEMES[theme.DEFAULT_THEME]["page"] in out


def test_every_theme_has_a_three_colour_sky():
    for name, t in theme.THEMES.items():
        assert len(t["sky"]) == 3 and all(theme._valid(c) for c in t["sky"]), name
    assert theme.sky({"name": "Tide"}) == theme.THEMES["Tide"]["sky"]


def test_every_theme_has_all_tokens():
    for name, t in theme.THEMES.items():
        assert all(theme._valid(t[k]) for k in theme.TOKENS), name
        assert t["why"]


def test_theme_route(isolate_config):
    client = api_server.app.test_client()
    data = client.get("/theme").get_json()
    assert data["colours"]["accent"] and "Aurora" in data["themes"]
