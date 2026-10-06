"""POST /screentime/domain accepted any finite positive `seconds`, so two
reports of 1e308 summed to inf in screentime.json and the Screen Time tab's
_format_duration(int(round(inf))) raised OverflowError on every refresh."""
import api_server
import config
import screentime_store
import qt_ui.screentime_tab as screentime_tab


def test_huge_seconds_cannot_poison_screentime_tab(qtbot, tmp_path, monkeypatch, isolate_config):
    monkeypatch.setattr(screentime_store, "STATE_PATH", str(tmp_path / "screentime.json"))
    monkeypatch.setattr(screentime_store, "_data", {})
    monkeypatch.setattr(screentime_store, "_last_flush", 0.0)
    api_server.app.config["TESTING"] = True
    client = api_server.app.test_client()
    headers = {"X-Carmen-Token": config.get_api_token()}

    for _ in range(2):
        client.post("/screentime/domain", json={"domain": "a.com", "seconds": 1e308}, headers=headers)

    tab = screentime_tab.ScreenTimeTab()  # refresh() runs in __init__
    qtbot.addWidget(tab)
    day = screentime_store.get_day(screentime_store._day_key())
    assert all(v != float("inf") for v in day["domains"].values())
