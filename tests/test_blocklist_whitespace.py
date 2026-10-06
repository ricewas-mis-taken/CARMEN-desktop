"""The API validates blocklist entries with item.strip() but stores them
unstripped, so " discord.exe" is accepted (200) yet never matches the real
process name -- enforcement silently does nothing for that entry."""
import api_server
import config
import session_manager


def test_padded_blocklist_entry_still_blocks(isolate_state):
    api_server.app.config["TESTING"] = True
    client = api_server.app.test_client()
    headers = {"X-Carmen-Token": config.get_api_token()}
    resp = client.post(
        "/session/start",
        json={"duration_minutes": 10, "lock_mode": "hard",
              "process_blocklist": [" Discord.exe\t"], "domain_whitelist": ["example.com"]},
        headers=headers,
    )
    assert resp.status_code == 200
    assert session_manager.is_blocked("discord.exe")


def test_update_blocklist_pads_too(isolate_state):
    session_manager.start_session(10, "soft", [], [])
    session_manager.update_blocklist([" steam.exe "], [" example.com "])
    assert session_manager.is_blocked("steam.exe")
    assert session_manager.get_status()["domainWhitelist"] == ["example.com"]
