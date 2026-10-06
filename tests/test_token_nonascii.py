import api_server


def test_non_ascii_token_header_is_401_not_500(isolate_state):
    client = api_server.app.test_client()
    resp = client.post("/session/end", headers={"X-Carmen-Token": "caf\u00e9"})
    assert resp.status_code == 401
