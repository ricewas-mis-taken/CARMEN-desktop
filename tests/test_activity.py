import activity


def _idle(monkeypatch, seconds):
    monkeypatch.setattr(activity, "idle_seconds", lambda: seconds)
    activity._media_until = 0.0


def test_recent_input_counts(monkeypatch):
    _idle(monkeypatch, 5)
    assert activity.is_active("chrome.exe")


def test_a_long_idle_does_not_count(monkeypatch):
    _idle(monkeypatch, 600)
    assert not activity.is_active("chrome.exe")


def test_sound_playing_in_the_browser_keeps_it_counting(monkeypatch):
    _idle(monkeypatch, 600)
    activity.note_media(True)
    assert activity.is_active("chrome.exe")
    activity.note_media(False)
    assert not activity.is_active("chrome.exe")


def test_a_video_app_in_front_counts_without_input(monkeypatch):
    _idle(monkeypatch, 600)
    assert activity.is_active("VLC.exe")


def test_not_being_able_to_tell_counts_as_present(monkeypatch):
    _idle(monkeypatch, None)
    assert activity.is_active("chrome.exe")


def test_the_activity_route_reports_idle_time(monkeypatch):
    import api_server
    _idle(monkeypatch, 300)
    body = api_server.app.test_client().get("/activity").get_json()
    assert body["idleSeconds"] == 300 and body["active"] is False
