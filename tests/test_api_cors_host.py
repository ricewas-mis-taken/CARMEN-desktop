import pytest

import api_server


@pytest.fixture
def client(isolate_state):
    api_server.app.config["TESTING"] = True
    return api_server.app.test_client()


@pytest.mark.parametrize("path", ["/status", "/history", "/whitelist/domains", "/review/topics", "/api/focus/rules"])
def test_foreign_web_origin_gets_no_cors_grant(client, path):
    resp = client.get(path, headers={"Origin": "https://evil.example"})
    assert resp.headers.get("Access-Control-Allow-Origin") is None


@pytest.mark.parametrize("origin", ["chrome-extension://abc", "moz-extension://abc"])
def test_extension_origin_still_allowed(client, origin):
    resp = client.get("/status", headers={"Origin": origin})
    assert resp.headers.get("Access-Control-Allow-Origin") == origin


def test_dns_rebinding_host_rejected(client):
    assert client.get("/health", headers={"Host": "attacker.example:5847"}).status_code == 403


@pytest.mark.parametrize("host", ["127.0.0.1:5847", "localhost:5847", "127.0.0.1"])
def test_loopback_host_accepted(client, host):
    assert client.get("/health", headers={"Host": host}).status_code == 200
