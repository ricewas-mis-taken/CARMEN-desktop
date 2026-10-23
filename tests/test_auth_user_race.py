"""get_current_user() must not blow up when a logout lands between its login check and its read."""
import auth_manager


def test_logout_between_check_and_read_gives_none_not_a_crash(monkeypatch):
    monkeypatch.setattr(auth_manager, "is_logged_in", lambda: True)
    monkeypatch.setattr(auth_manager, "_current_user", None)
    assert auth_manager.get_current_user() is None


def test_logged_in_user_is_returned_as_a_copy(monkeypatch):
    monkeypatch.setattr(auth_manager, "is_logged_in", lambda: True)
    monkeypatch.setattr(auth_manager, "_current_user", {"id": "u1", "email": "a@b.c"})
    user = auth_manager.get_current_user()
    assert user == {"id": "u1", "email": "a@b.c"}
    user["id"] = "changed"
    assert auth_manager._current_user["id"] == "u1"
