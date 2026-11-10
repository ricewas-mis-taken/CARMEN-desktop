from datetime import date

import score_store
import sync_client
from tests.test_sync_client import isolate_device, fake_logged_in, fake_server  # noqa: F401


def test_two_computers_end_up_with_the_same_score(isolate_device, fake_logged_in, fake_server, tmp_path, monkeypatch):
    monkeypatch.setattr(score_store, "PATH", str(tmp_path / "a_score.json"))
    isolate_device("a")
    score_store.add_event("goal:2026-11-07", date(2026, 11, 7), "goals", 12, "all goals")
    score_store.add_event("review:1", date(2026, 11, 7), "review", 6, "on time")
    sync_client.sync_now()

    monkeypatch.setattr(score_store, "PATH", str(tmp_path / "b_score.json"))
    isolate_device("b")
    score_store.add_event("review:2", date(2026, 11, 6), "review", -3, "late")         # only this one knows it
    score_store.add_event("goal:2026-11-07", date(2026, 11, 7), "goals", 2, "67%")      # an older, different figure
    sync_client.sync_now()
    b_score = score_store.score()

    monkeypatch.setattr(score_store, "PATH", str(tmp_path / "a_score.json"))
    isolate_device("a")
    sync_client.sync_now()
    assert score_store.score() == b_score
    keys = sorted(e["key"] for e in score_store.load()["events"])
    assert keys == ["goal:2026-11-07", "review:1", "review:2"]


def test_legacy_events_without_a_stamp_get_one_so_they_are_sent(tmp_path, monkeypatch):
    monkeypatch.setattr(score_store, "PATH", str(tmp_path / "s.json"))
    score_store._save({"version": 1, "events": [{"key": "k", "date": "2026-01-01", "kind": "goals", "points": 5, "note": ""}],
                       "settledThrough": None})
    score_store.stamp_unsynced()
    assert score_store.load()["events"][0]["updatedAt"]


def test_review_days_follow_you_to_another_computer(monkeypatch):
    import config
    import sync_client
    store = {"reviewDays": [1, 3], "reviewDaysUpdatedAt": "2026-11-09T10:00:00"}
    monkeypatch.setattr(config, "load_config", lambda: dict(store))
    monkeypatch.setattr(config, "update_config", lambda fn: fn(store))
    records = sync_client._gather_settings(None)
    assert records and records[0]["data"] == {"days": [1, 3]}
    store.clear()
    store.update({"reviewDays": [], "reviewDaysUpdatedAt": "2026-11-01T10:00:00"})
    assert sync_client._apply_settings(records)[0] == 1
    assert store["reviewDays"] == [1, 3]
