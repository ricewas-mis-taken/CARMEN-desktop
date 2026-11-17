import config


def test_theme_is_a_shared_setting():
    assert "theme" in config.SHARED_SETTINGS
    clean = config._clean_shared_settings({"theme": {"name": "Ember"}})
    assert clean["theme"] == {"name": "Ember"}
    assert "theme" not in config._clean_shared_settings({"theme": "Ember"})


def test_score_ignores_stray_and_deleted_events(tmp_path, monkeypatch):
    import score_store
    monkeypatch.setattr(score_store, "PATH", str(tmp_path / "score.json"))
    score_store._save({"version": 1, "settledThrough": None, "events": [
        {"key": "a", "date": "2026-11-01", "kind": "goals", "points": 10, "note": ""},
        {"key": "ledger", "events": [], "settledThrough": None},            # a whole ledger pulled in as an "event"
        {"key": "gone", "isDeleted": True},
        {"key": "b", "date": "2026-11-02", "kind": "goals", "points": -4, "note": "", "isDeleted": True},
    ]})
    assert score_store.score() == 510
    assert [e["key"] for e in score_store.events_between(
        __import__("datetime").date(2026, 11, 1), __import__("datetime").date(2026, 11, 30))] == ["a"]
