from datetime import datetime, timedelta

import calendar_scheduler
import calendar_store


def test_absurd_warning_minutes_does_not_stop_event_start(isolate_calendar_db, monkeypatch):
    fired = []
    monkeypatch.setattr(calendar_scheduler, "_fired", set())
    monkeypatch.setattr(calendar_scheduler, "_fire_event_start", lambda ev, oe: fired.append(ev["title"]))
    monkeypatch.setattr(calendar_scheduler, "_fire_focus_warning", lambda ev: None)
    now = datetime.now()
    calendar_store.save_event({
        "title": "huge-warn",
        "start": (now - timedelta(seconds=5)).isoformat(),
        "end": (now + timedelta(hours=1)).isoformat(),
        "focusProfile": {"enabled": True, "lockMode": "hard", "processBlocklist": [],
                         "domainWhitelist": [], "warningMinutes": 10 ** 13},
    })
    calendar_scheduler._tick()
    assert fired == ["huge-warn"]
