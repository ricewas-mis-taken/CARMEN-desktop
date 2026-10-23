"""The tray's Status text must count a review up, not down from its internal 8-hour ceiling."""
from datetime import datetime, timedelta

import session_manager
import tray


def _status(**extra):
    base = {
        "isActive": True, "isBurnout": False, "source": "manual", "reviewProblemName": None,
        "secondsRemaining": 28800 - 125, "lockMode": "soft", "violationCount": 0, "violationLog": [],
        "startTime": (datetime.now() - timedelta(seconds=125)).isoformat(),
        "reviewProblemId": None,
    }
    base.update(extra)
    return base


def test_a_review_reads_as_elapsed_time(monkeypatch):
    monkeypatch.setattr(session_manager, "get_status",
                        lambda: _status(source="review", reviewProblemName="Factor it", reviewProblemId=3))
    text = tray._format_status_text()
    assert "Review: Factor it" in text and "elapsed" in text and "remaining" not in text


def test_a_first_attempt_is_named_as_one(monkeypatch):
    monkeypatch.setattr(session_manager, "get_status",
                        lambda: _status(source="review", reviewProblemName=session_manager.FIRST_ATTEMPT_NAME))
    assert "First attempt" in tray._format_status_text()


def test_a_plain_session_still_counts_down(monkeypatch):
    monkeypatch.setattr(session_manager, "get_status", lambda: _status(secondsRemaining=600))
    assert "10m 0s remaining" in tray._format_status_text()
