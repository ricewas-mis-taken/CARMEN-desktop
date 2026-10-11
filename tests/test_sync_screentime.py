"""Screen time from each computer reaches the others, kept apart per computer so
the tab can show them one at a time or all together."""
from datetime import date

import screentime_store
import sync_live

from test_sync_client import fake_logged_in  # noqa: F401  (shared fixture)
from test_sync_live_session import device, server  # noqa: F401  (shared fixtures)

TODAY = date.today().isoformat()


def _spend(apps=None, domains=None):
    for name, secs in (apps or {}).items():
        screentime_store.add_app_seconds(name, secs)
    for name, secs in (domains or {}).items():
        screentime_store.add_domain_seconds(name, secs)


def test_each_computers_time_is_kept_apart_and_can_be_combined(device, fake_logged_in, server):
    device("a")
    _spend({"discord.exe": 600, "code.exe": 1200}, {"youtube.com": 300})
    sync_live.tick()
    device("b")
    _spend({"code.exe": 100})
    sync_live.tick()
    other = screentime_store.remote_devices()
    assert len(other) == 1
    other_id = other[0]["id"]

    assert screentime_store.get_day(TODAY)["apps"] == {"code.exe": 100}                   # this computer only
    assert screentime_store.get_day(TODAY, other_id)["apps"] == {"discord.exe": 600, "code.exe": 1200}
    assert screentime_store.get_day(TODAY, other_id)["domains"] == {"youtube.com": 300}
    combined = screentime_store.get_day(TODAY, "all")
    assert combined["apps"] == {"discord.exe": 600, "code.exe": 1300} and combined["domains"] == {"youtube.com": 300}


def test_a_computer_never_lists_itself_as_another_computer(device, fake_logged_in, server):
    device("a")
    _spend({"code.exe": 60})
    sync_live.tick()
    sync_live.tick()
    assert screentime_store.remote_devices() == []


def test_the_week_total_covers_the_other_computer_too(device, fake_logged_in, server):
    device("a")
    _spend({"code.exe": 500})
    sync_live.tick()
    device("b")
    sync_live.tick()
    other_id = screentime_store.remote_devices()[0]["id"]
    week = screentime_store.get_range(TODAY, TODAY, other_id)
    assert week["apps"] == {"code.exe": 500}


def test_only_changed_days_are_sent_again_and_not_more_than_once_a_minute(device, fake_logged_in, server):
    device("a")
    _spend({"code.exe": 60})
    sync_live.tick()
    posts = [m for m, _ in server.requests].count("POST")
    _spend({"code.exe": 60})
    sync_live.tick()                                         # within the minute: nothing new is sent
    assert [m for m, _ in server.requests].count("POST") == posts
    sync_live._last_screentime_push = 1.0                    # pretend a minute has passed
    sync_live.tick()
    assert [m for m, _ in server.requests].count("POST") == posts + 1
    sync_live._last_screentime_push = 1.0
    sync_live.tick()                                         # unchanged: nothing sent
    assert [m for m, _ in server.requests].count("POST") == posts + 1


def test_later_totals_replace_earlier_ones_from_the_same_computer(device, fake_logged_in, server):
    device("a")
    _spend({"code.exe": 60})
    sync_live.tick()
    device("b")
    sync_live.tick()
    device("a")
    _spend({"code.exe": 60})
    sync_live._last_screentime_push = 1.0
    sync_live.tick()
    device("b")
    sync_live.tick()
    other_id = screentime_store.remote_devices()[0]["id"]
    assert screentime_store.get_day(TODAY, other_id)["apps"] == {"code.exe": 120}


def test_unusable_remote_data_is_refused():
    assert not screentime_store.merge_remote_day("../evil", "x", TODAY, {}, {})
    assert not screentime_store.merge_remote_day("dev-1", "x", "not-a-day", {}, {})
    assert not screentime_store.merge_remote_day("dev-1", "x", TODAY, "nope", {})
    assert screentime_store.merge_remote_day(
        "dev-1", "Laptop", TODAY, {"ok.exe": 5, "bad.exe": -3, "worse.exe": float("nan"), 7: 1, "big.exe": 1e12}, {})
    assert screentime_store.get_day(TODAY, "dev-1")["apps"] == {"ok.exe": 5}


def test_two_computers_with_the_same_name_are_told_apart():
    screentime_store.merge_remote_day("aaaa1111", "LAPTOP", TODAY, {"a.exe": 1}, {})
    screentime_store.merge_remote_day("bbbb2222", "LAPTOP", TODAY, {"a.exe": 1}, {})
    names = [d["name"] for d in screentime_store.remote_devices()]
    assert len(set(names)) == 2 and all(n.startswith("LAPTOP") for n in names)


def test_the_tab_shows_buttons_only_when_another_computer_exists(qtbot):
    import qt_ui.screentime_tab as tab_module
    tab = tab_module.ScreenTimeTab()
    qtbot.addWidget(tab)
    assert not tab._device_bar.isVisibleTo(tab)
    screentime_store.add_app_seconds("mine.exe", 100)
    screentime_store.merge_remote_day("dev-9", "Laptop", TODAY, {"theirs.exe": 400}, {})
    tab.refresh()
    labels = [b.text() for b in tab._device_group.buttons()]
    assert labels == ["This computer", "Laptop", "All computers"]
    assert tab._device_bar.isVisibleTo(tab)

    tab._set_device("dev-9")
    assert tab._current_data()["apps"] == {"theirs.exe": 400}
    tab._set_device("all")
    assert tab._current_data()["apps"] == {"mine.exe": 100, "theirs.exe": 400}
    tab._set_device("this")
    assert tab._current_data()["apps"] == {"mine.exe": 100}


def test_the_tab_falls_back_to_this_computer_if_the_selected_one_disappears(qtbot):
    import qt_ui.screentime_tab as tab_module
    screentime_store.merge_remote_day("dev-9", "Laptop", TODAY, {"theirs.exe": 400}, {})
    tab = tab_module.ScreenTimeTab()
    qtbot.addWidget(tab)
    tab._set_device("dev-9")
    screentime_store._remote.clear()
    tab.refresh()
    assert tab._device == "this"
