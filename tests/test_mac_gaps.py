import sys

import pytest

import auto_updater
import screentime_categories


@pytest.mark.skipif(sys.platform != "darwin", reason="reads the Mac's input idle time")
def test_a_mac_can_tell_how_long_it_has_been_idle():
    idle = auto_updater.user_idle_seconds()
    assert isinstance(idle, float) and idle >= 0


@pytest.mark.parametrize("name,category", [
    ("Google Chrome", "Tools"), ("firefox", "Tools"), ("Spotify", "Entertainment"),
    ("Discord", "Entertainment"), ("Safari", "Tools"), ("steam_osx", "Games"), ("Some Unknown App", "Other"),
])
def test_mac_app_names_are_categorised_like_their_windows_twins(name, category):
    assert screentime_categories.categorize_app(name) == category
