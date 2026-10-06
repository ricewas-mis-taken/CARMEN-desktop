"""POST /review/problems/<id>/finish without duration_seconds must use the
pause-aware elapsed time the server already tracks, not raw wall-clock time
since start (which counts every paused minute as time spent solving)."""
from datetime import datetime, timedelta

import pytest

import api_server
import config
import review_store


class _Clock:
    now_value = datetime(2026, 1, 1, 12, 0, 0)


class _FakeDatetime(datetime):
    @classmethod
    def now(cls, tz=None):
        return _Clock.now_value


@pytest.fixture
def client(isolate_config, monkeypatch):
    monkeypatch.setattr(review_store, "datetime", _FakeDatetime)
    api_server.app.config["TESTING"] = True
    c = api_server.app.test_client()
    c.environ_base["HTTP_X_CARMEN_TOKEN"] = config.get_api_token()
    return c


def test_finish_without_duration_excludes_paused_time(client, isolate_review_db):
    t = client.post("/review/topics", json={"name": "Math"}).get_json()
    s = client.post(f"/review/topics/{t['id']}/subjects", json={"name": "A", "color": "#111111"}).get_json()
    p = client.post(
        f"/review/topics/{t['id']}/problems",
        data={"name": "P", "subject_id": str(s["id"]), "stars": "3",
              "description_type": "text", "description_text": "x"},
    ).get_json()
    _Clock.now_value = datetime(2026, 1, 1, 12, 0, 0)
    token = client.post(f"/review/problems/{p['id']}/start").get_json()["sessionToken"]
    _Clock.now_value += timedelta(seconds=60)
    client.post("/review/pause")
    _Clock.now_value += timedelta(seconds=3000)   # lunch break while paused
    client.post("/review/resume")
    _Clock.now_value += timedelta(seconds=40)
    resp = client.post(f"/review/problems/{p['id']}/finish", json={"session_token": token})
    assert resp.status_code == 200
    assert resp.get_json()["firstAttemptSeconds"] == 100
