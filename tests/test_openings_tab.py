import os

import qt_ui.openings_tab as openings_tab
from qt_ui.main_window import _PAGE_MIN_WIDTH


def test_the_pages_ship_with_the_app():
    assert os.path.isfile(openings_tab.PAGE)
    assert os.path.isfile(os.path.join(os.path.dirname(openings_tab.PAGE), "more.js"))
    assert "openings" in _PAGE_MIN_WIDTH


def test_the_web_view_is_only_built_when_first_shown(qtbot, monkeypatch):
    built = []
    monkeypatch.setattr(openings_tab.OpeningsTab, "_build", lambda self: built.append(1))
    tab = openings_tab.OpeningsTab()
    qtbot.addWidget(tab)
    assert built == []
    tab.show()
    assert built == [1]
