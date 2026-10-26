import pytest

import api_server
import theme


def test_default_and_unknown_fall_back():
    assert theme.resolve(None) == theme.resolve({"name": "nope"})
    assert theme.resolve(None)["accent"] == "#5B8DEF"


def test_custom_overrides_only_valid_colours():
    colours = theme.resolve({"name": "Moss", "custom": {"accent": "#ff0000", "ink": "red", "page": 5}})
    assert colours["accent"] == "#FF0000"
    assert colours["ink"] == theme.THEMES["Moss"]["ink"]
    assert colours["page"] == theme.THEMES["Moss"]["page"]


def test_stylesheet_is_rewritten_in_one_pass():
    qss = "a{color:#1B2230;background:#5b8def} b{border:1px solid #ABCDEF} c{background:#F2F4F7}"
    out = theme.apply_to_stylesheet(qss, theme.resolve({"name": "Clay"}))
    assert "#2A1F1B" in out and "#C2623F" in out
    assert "#ABCDEF" in out          # unknown colours are left alone
    assert "#1B2230" not in out and "#5B8DEF" not in out.upper()


def test_default_theme_leaves_the_stylesheet_unchanged_except_tints():
    qss = "x{color:#1B2230;background:#F7F7F8}"
    assert theme.apply_to_stylesheet(qss, theme.resolve(None)) == qss


def test_every_theme_has_all_tokens():
    for name, t in theme.THEMES.items():
        assert all(theme._valid(t[k]) for k in theme.TOKENS), name
        assert t["why"]


def test_theme_route(isolate_config):
    client = api_server.app.test_client()
    data = client.get("/theme").get_json()
    assert data["colours"]["accent"] and "Moss" in data["themes"]
