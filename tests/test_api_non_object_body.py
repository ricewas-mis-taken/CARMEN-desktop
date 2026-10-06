"""A request body that is valid JSON but not an object ([1], "x", 5, true)
used to crash every body-reading handler with AttributeError -> HTTP 500."""
import pytest

import api_server
import config

ROUTES = [
    "/session/start", "/session/update", "/violation",
    "/screentime/domain", "/blocklist/apps", "/blocklist/apps/remove",
    "/blocklist/browser-profiles", "/whitelist/domains", "/whitelist/domains/add",
    "/tasks/abc/domain-whitelist", "/api/focus/rules", "/review/topics",
    "/review/topics/1/subjects", "/review/problems/1/finish",
]


@pytest.fixture
def client(isolate_state, isolate_review_db):
    return api_server.app.test_client()


@pytest.mark.parametrize("route", ROUTES)
@pytest.mark.parametrize("raw", ['[1]', '"str"', '5', 'true'])
def test_non_object_json_body_is_a_client_error(client, route, raw):
    resp = client.post(
        route, data=raw, content_type="application/json",
        headers={"X-Carmen-Token": config.get_api_token()},
    )
    assert 400 <= resp.status_code < 500, (route, raw, resp.status_code)
