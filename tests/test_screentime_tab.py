from datetime import date, timedelta

import pytest

import screentime_store
import qt_ui.screentime_tab as screentime_tab


@pytest.fixture
def isolate_screentime(tmp_path, monkeypatch):
    monkeypatch.setattr(screentime_store, "STATE_PATH", str(tmp_path / "screentime.json"))
    monkeypatch.setattr(screentime_store, "_data", {})
    monkeypatch.setattr(screentime_store, "_last_flush", 0.0)
    yield


def test_shows_empty_state_with_no_data(qtbot, isolate_screentime):
    tab = screentime_tab.ScreenTimeTab()
    qtbot.addWidget(tab)
    assert tab._sections_layout.count() == 2  # empty-state label + trailing stretch


def test_groups_by_category_and_bucket_threshold(qtbot, isolate_screentime):
    screentime_store.add_app_seconds("code.exe", 20 * 60, when=date.today())
    screentime_store.add_domain_seconds("youtube.com", 10 * 60, when=date.today())
    screentime_store.add_domain_seconds("some-tiny-site.com", 30, when=date.today())  # unknown -> Other

    tab = screentime_tab.ScreenTimeTab()
    qtbot.addWidget(tab)

    # One section per non-empty category (Tools, Entertainment, Other) --
    # the trailing stretch is the last layout item.
    section_widgets = [
        tab._sections_layout.itemAt(i).widget()
        for i in range(tab._sections_layout.count() - 1)
    ]
    assert len(section_widgets) == 3

    tools_section = next(w for w in section_widgets if "Tools" in w.findChild(
        screentime_tab.QLabel).text())
    assert tools_section is not None


def test_under_five_minutes_is_collapsed_until_toggled(qtbot, isolate_screentime):
    # Both Entertainment, so they land in the same category section --
    # reddit.com stays under the 5-minute threshold, youtube.com doesn't.
    screentime_store.add_domain_seconds("youtube.com", 10 * 60, when=date.today())
    screentime_store.add_domain_seconds("reddit.com", 30, when=date.today())

    tab = screentime_tab.ScreenTimeTab()
    qtbot.addWidget(tab)
    tab.show()

    section = tab._sections_layout.itemAt(0).widget()
    toggle = next(
        c for c in section.findChildren(screentime_tab.QPushButton)
    )
    assert not toggle.isChecked()

    details = section.layout().itemAt(section.layout().count() - 1).widget()
    assert not details.isVisible()

    toggle.setChecked(True)
    assert details.isVisible()


def test_weekly_range_sums_across_the_week(qtbot, isolate_screentime):
    today = date.today()
    monday = today - timedelta(days=today.weekday())
    screentime_store.add_domain_seconds("github.com", 600, when=monday)
    screentime_store.add_domain_seconds("github.com", 300, when=today)

    tab = screentime_tab.ScreenTimeTab()
    qtbot.addWidget(tab)
    tab._week_btn.click()

    section = tab._sections_layout.itemAt(0).widget()
    row_text = " ".join(
        lbl.text() for lbl in section.findChildren(screentime_tab.QLabel)
    )
    assert "github.com" in row_text
    assert "15m" in row_text


# ------------------------------------------------------------ hero + bars ----

def test_the_total_and_caption_lead_the_page(qtbot, isolate_screentime):
    screentime_store.add_app_seconds("code.exe", 2 * 3600 + 5 * 60, when=date.today())
    screentime_store.add_domain_seconds("youtube.com", 30 * 60, when=date.today())
    tab = screentime_tab.ScreenTimeTab()
    qtbot.addWidget(tab)
    assert tab._total_label.text() == "2h 35m"
    assert tab._caption.text() == "today · this computer"
    tab._week_btn.click()
    assert tab._caption.text() == "this week · this computer"


def test_the_total_of_an_empty_day_is_zero(qtbot, isolate_screentime):
    tab = screentime_tab.ScreenTimeTab()
    qtbot.addWidget(tab)
    assert tab._total_label.text() == "0s"


def test_the_stacked_bar_splits_the_total_by_category(qtbot, isolate_screentime):
    screentime_store.add_app_seconds("code.exe", 3 * 3600, when=date.today())            # Tools
    screentime_store.add_domain_seconds("youtube.com", 3600, when=date.today())          # Entertainment
    tab = screentime_tab.ScreenTimeTab()
    qtbot.addWidget(tab)
    segments = dict((label, w) for label, _x, w in tab._chart.segments(403))
    # 403px minus one 3px gap = 400px, split 3:1
    assert segments["Tools"] == pytest.approx(300) and segments["Entertainment"] == pytest.approx(100)
    legend = " ".join(label.text() for label in tab._legend_labels)
    assert "Tools" in legend and "3h 00m" in legend and "Entertainment" in legend


def test_each_row_has_a_bar_as_long_as_its_share_of_the_biggest(qtbot, isolate_screentime):
    screentime_store.add_domain_seconds("youtube.com", 40 * 60, when=date.today())
    screentime_store.add_domain_seconds("netflix.com", 10 * 60, when=date.today())
    tab = screentime_tab.ScreenTimeTab()
    qtbot.addWidget(tab)
    section = tab._sections_layout.itemAt(0).widget()
    bars = section.findChildren(screentime_tab._MiniBar)
    assert sorted(round(b._fraction, 2) for b in bars) == [0.25, 1.0]


def test_a_section_names_its_category_in_its_first_label(qtbot, isolate_screentime):
    screentime_store.add_app_seconds("code.exe", 20 * 60, when=date.today())
    tab = screentime_tab.ScreenTimeTab()
    qtbot.addWidget(tab)
    section = tab._sections_layout.itemAt(0).widget()
    assert section.findChild(screentime_tab.QLabel).text() == "Tools"
