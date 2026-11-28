import pytest

pytest.importorskip("PySide6")
from PySide6.QtWidgets import QApplication

from qt_ui.color_choice import ColorChoice, normalize_hex
from qt_ui import review_tab


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def test_normalize_hex():
    assert normalize_hex("#ABCDEF") == "#abcdef"
    assert normalize_hex("abcdef") == "#abcdef"
    assert normalize_hex("not a colour") is None


def test_palette_pick_reports_change(app):
    seen = []
    w = ColorChoice(["#5B8DEF", "#e53935"], "#5B8DEF", on_change=seen.append)
    w.set_color("#e53935")
    assert w.color() == "#e53935" and seen == ["#e53935"]


def test_custom_colour_gets_its_own_swatch(app):
    w = ColorChoice(["#5B8DEF", "#e53935"], "#123456")
    assert w.color() == "#123456"
    assert w._custom_btn.isVisibleTo(w) and w._custom_btn.property("hex") == "#123456"


def test_unused_colour_when_palette_is_full():
    used = {c.lower() for c in review_tab.COLOR_PALETTE}
    picked = review_tab._unused_color(used)
    assert picked not in used and picked.startswith("#") and len(picked) == 7
