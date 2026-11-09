import json

import config
import score_store
import theme


def _ev(key, points, day="2026-11-01"):
    return {"key": key, "date": day, "kind": "goals", "points": points, "note": ""}


def test_score_ledgers_are_united(tmp_path, monkeypatch):
    monkeypatch.setattr(score_store, "PATH", str(tmp_path / "score.json"))
    score_store._save({"version": 1, "events": [_ev("a", 10), _ev("b", -5)], "settledThrough": "2026-11-01"})
    assert score_store.apply_remote([_ev("b", -5), _ev("c", 7)], "2026-11-03", "2026-11-04T00:00:00Z")
    data = score_store.load()
    assert {e["key"] for e in data["events"]} == {"a", "b", "c"}
    assert data["settledThrough"] == "2026-11-03"
    assert score_store.score(data) == 500 + 10 - 5 + 7


def test_score_same_key_resolves_the_same_on_both_sides(tmp_path, monkeypatch):
    monkeypatch.setattr(score_store, "PATH", str(tmp_path / "score.json"))
    score_store._save({"version": 1, "events": [_ev("a", 4)], "settledThrough": None})
    score_store.apply_remote([_ev("a", 9)], None, "2026-11-04T00:00:00Z")
    assert score_store.load()["events"][0]["points"] == 9


def test_score_rejects_junk(tmp_path, monkeypatch):
    monkeypatch.setattr(score_store, "PATH", str(tmp_path / "score.json"))
    assert score_store.apply_remote("nope", None, "2026-11-04T00:00:00Z") is False


def test_theme_is_a_shared_setting():
    assert "theme" in config.SHARED_SETTINGS
    clean = config._clean_shared_settings({"theme": {"name": "Ember"}})
    assert clean["theme"] == {"name": "Ember"}
    assert "theme" not in config._clean_shared_settings({"theme": "Ember"})
