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
