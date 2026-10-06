import json

import api_server
import config


def test_subject_color_must_be_hex(isolate_state, isolate_review_db):
    client = api_server.app.test_client()
    client.environ_base["HTTP_X_CARMEN_TOKEN"] = config.get_api_token()
    post = lambda p, b: client.post(p, data=json.dumps(b), content_type="application/json")
    topic = post("/review/topics", {"name": "T"}).get_json()
    bad = post(f"/review/topics/{topic['id']}/subjects", {"name": "S", "color": "zzzzzz"})
    assert bad.status_code == 400
    good = post(f"/review/topics/{topic['id']}/subjects", {"name": "S2", "color": "#12aBcd"})
    assert good.status_code == 201
