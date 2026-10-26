"""A session_state.json holding valid JSON that is not an object must not stop the app
starting: session_manager loads it at import."""
import json

import pytest

import session_manager


@pytest.mark.parametrize("content", ["null", "123", "[1, 2, 3]", '"text"', "true"])
def test_non_object_state_file_is_ignored(isolate_state, content):
    with open(session_manager.STATE_PATH, "w", encoding="utf-8") as f:
        f.write(content)
    session_manager._load()                      # must not raise
    assert session_manager.get_status()["isActive"] is False


def test_a_real_state_file_still_loads(isolate_state):
    with open(session_manager.STATE_PATH, "w", encoding="utf-8") as f:
        json.dump({"lockMode": "hard"}, f)
    session_manager._load()
    assert session_manager.get_lock_mode() == "hard"
