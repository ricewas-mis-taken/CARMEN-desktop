import config


def test_theme_is_a_shared_setting():
    assert "theme" in config.SHARED_SETTINGS
    clean = config._clean_shared_settings({"theme": {"name": "Ember"}})
    assert clean["theme"] == {"name": "Ember"}
    assert "theme" not in config._clean_shared_settings({"theme": "Ember"})
