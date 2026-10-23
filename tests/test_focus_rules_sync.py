"""The extension edits the saved domain list through set_focus_rules(); that edit
must be stamped and announced like any other shared default, or the other
computer never receives it."""
import config
import server_clock


def test_an_extension_whitelist_edit_is_stamped_and_announced():
    config.update_config(lambda cfg: cfg.update({"last_duration_minutes": 30}))  # gives the settings a first stamp
    first = config.export_shared_settings()["stamp"]
    woken = []
    listener = lambda: woken.append(1)  # noqa: E731
    config.add_change_listener(listener)
    try:
        config.set_focus_rules(["example.com", "docs.google.com"])
    finally:
        config._change_listeners.remove(listener)
    exported = config.export_shared_settings()
    assert exported["settings"]["domainWhitelist"] == ["example.com", "docs.google.com"]
    assert server_clock.is_newer(exported["stamp"], first)
    assert woken == [1]


def test_resending_the_same_list_is_not_a_change():
    config.set_focus_rules(["example.com"])
    stamp = config.export_shared_settings()["stamp"]
    woken = []
    listener = lambda: woken.append(1)  # noqa: E731
    config.add_change_listener(listener)
    try:
        config.set_focus_rules(["example.com"])
    finally:
        config._change_listeners.remove(listener)
    assert config.export_shared_settings()["stamp"] == stamp and woken == []
