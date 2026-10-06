import json

import pytest

import api_server
import config


@pytest.mark.parametrize("field", ["event_title", "event_id"])
@pytest.mark.parametrize("bad", [12345, {"a": 1}, ["x"], True])
def test_session_start_rejects_non_string_event_fields(isolate_state, field, bad):
    client = api_server.app.test_client()
    client.environ_base["HTTP_X_CARMEN_TOKEN"] = config.get_api_token()
    body = {"duration_minutes": 5, "lock_mode": "soft", "domain_whitelist": [], field: bad}
    resp = client.post("/session/start", data=json.dumps(body), content_type="application/json")
    assert resp.status_code == 400
