"""The saved block and allow lists (and the Start dialog's remembered choices)
are the same on every computer, so a new session blocks the same things."""
import config
import sync_live

from test_sync_client import fake_logged_in  # noqa: F401  (shared fixture)
from test_sync_live_session import device, server  # noqa: F401  (shared fixtures)


def test_changing_the_saved_blocklist_reaches_the_other_computer(device, fake_logged_in, server):
    device("a")
    config.update_config(lambda cfg: cfg.update({"processBlocklist": ["discord.exe", "steam.exe"],
                                                "domainWhitelist": ["school.edu"]}))
    sync_live.tick()
    device("b")
    sync_live.tick()
    cfg = config.load_config()
    assert cfg["processBlocklist"] == ["discord.exe", "steam.exe"] and cfg["domainWhitelist"] == ["school.edu"]


def test_unblocking_an_app_on_one_computer_unblocks_it_on_the_other(device, fake_logged_in, server):
    device("a")
    config.update_config(lambda cfg: cfg.update({"processBlocklist": ["discord.exe", "steam.exe"]}))
    sync_live.tick()
    device("b")
    sync_live.tick()
    config.update_config(lambda cfg: cfg.update({"processBlocklist": ["steam.exe"]}))
    sync_live.tick()
    device("a")
    sync_live.tick()
    assert config.load_config()["processBlocklist"] == ["steam.exe"]


def test_a_changed_domain_list_makes_the_browser_extension_refetch_its_rules(device, fake_logged_in, server):
    device("a")
    config.update_config(lambda cfg: cfg.update({"domainWhitelist": ["school.edu"]}))
    sync_live.tick()
    device("b")
    before = config.load_config()["focusRulesVersion"]
    sync_live.tick()
    assert config.load_config()["focusRulesVersion"] == before + 1


def test_things_that_belong_to_one_computer_stay_there(device, fake_logged_in, server):
    device("a")
    config.update_config(lambda cfg: cfg.update({"processBlocklist": ["x.exe"], "miniTimerGeometry": [1, 2, 300, 300],
                                                "browserProfileBlocklist": ["profile-a"]}))
    token_a = config.get_api_token()
    sync_live.tick()
    device("b")
    token_b = config.get_api_token()
    sync_live.tick()
    cfg = config.load_config()
    assert cfg["miniTimerGeometry"] is None and cfg["browserProfileBlocklist"] == []
    assert cfg["apiToken"] == token_b != token_a


def test_a_change_made_here_is_not_sent_back_and_unrelated_edits_send_nothing(device, fake_logged_in, server):
    device("a")
    config.update_config(lambda cfg: cfg.update({"processBlocklist": ["x.exe"]}))
    sync_live.tick()
    device("b")
    sync_live.tick()
    posts = [m for m, _ in server.requests].count("POST")
    config.update_config(lambda cfg: cfg.update({"miniTimerGeometry": [5, 5, 200, 200]}))   # not a shared setting
    sync_live.tick()
    sync_live.tick()
    assert [m for m, _ in server.requests].count("POST") == posts


def test_defaults_saved_before_sharing_existed_are_sent_once(device, fake_logged_in, server):
    device("a")
    config.save_config({**config.DEFAULT_CONFIG, "processBlocklist": ["old.exe"]})   # no stamp, as before this feature
    sync_live.tick()
    device("b")
    sync_live.tick()
    assert config.load_config()["processBlocklist"] == ["old.exe"]


def test_damaged_shared_settings_are_ignored(device, fake_logged_in, server):
    from datetime import datetime
    server[("settings", "defaults")] = {
        "table_name": "settings", "sync_id": "defaults", "device_id": "x", "is_deleted": False,
        "updated_at": datetime.now().astimezone().isoformat(),
        "data": {"settings": {"processBlocklist": "discord.exe", "last_lock_mode": "nuclear",
                              "last_duration_minutes": -4, "domainWhitelist": [5]}},
    }
    device("a")
    assert sync_live.tick() is None
    cfg = config.load_config()
    assert cfg["processBlocklist"] == [] and cfg["last_lock_mode"] == "soft" and cfg["last_duration_minutes"] == 25


def test_only_the_valid_part_of_a_partly_damaged_record_is_used(device, fake_logged_in, server):
    from datetime import datetime
    server[("settings", "defaults")] = {
        "table_name": "settings", "sync_id": "defaults", "device_id": "x", "is_deleted": False,
        "updated_at": datetime.now().astimezone().isoformat(),
        "data": {"settings": {"processBlocklist": ["good.exe"], "last_lock_mode": "nuclear"}},
    }
    device("a")
    sync_live.tick()
    cfg = config.load_config()
    assert cfg["processBlocklist"] == ["good.exe"] and cfg["last_lock_mode"] == "soft"


def test_every_table_the_fast_lane_handles_is_left_out_of_the_slow_lane():
    handled = {sync_live.HISTORY_TABLE, sync_live.LIVE_TABLE, sync_live.REVIEW_TABLE,
               sync_live.SCREENTIME_TABLE, sync_live.SETTINGS_TABLE, sync_live.QUICKNOTE_TABLE}
    assert handled == set(sync_live.LANE_TABLES)
