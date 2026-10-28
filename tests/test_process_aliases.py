"""One block entry must match the same app under either OS's process name."""
import pytest

import process_aliases
import session_manager


@pytest.mark.parametrize("a,b", [
    ("chrome.exe", "Google Chrome"),
    ("msedge.exe", "Microsoft Edge"),
    ("Discord.exe", "Discord"),
    ("Code.exe", "Code"),
    ("Spotify.exe", "spotify"),
    ("  slack.exe ", "Slack"),
])
def test_same_app_either_spelling(a, b):
    assert process_aliases.same_process(a, b)


@pytest.mark.parametrize("a,b", [
    ("chrome.exe", "Microsoft Edge"),
    ("discord.exe", "Slack"),
    ("", "Discord"),
    (None, None),
])
def test_different_apps_do_not_match(a, b):
    assert not process_aliases.same_process(a, b)


def test_multi_profile_browser_either_spelling():
    assert process_aliases.is_multi_profile_browser("chrome.exe")
    assert process_aliases.is_multi_profile_browser("Google Chrome")
    assert process_aliases.is_multi_profile_browser("Microsoft Edge")
    assert not process_aliases.is_multi_profile_browser("Firefox")


def test_block_made_on_windows_applies_on_mac_and_back(isolate_state):
    session_manager.start_session(25, "hard", ["chrome.exe", "Discord"], [])
    assert session_manager.is_blocked("Google Chrome")
    assert session_manager.is_blocked("discord.exe")
    assert not session_manager.is_blocked("Safari")
    session_manager.end_session()
    session_manager.start_session(25, "hard", ["Google Chrome"], [])
    assert session_manager.is_blocked("chrome.exe")
