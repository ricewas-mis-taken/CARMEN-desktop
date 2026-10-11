"""Two computers behave as one: a session started on either runs on both, with
the same blocked apps and rules, and finished sessions land in both histories.
Each "computer" here has its own files and its own session state; the cloud is
the in-memory fake."""
import copy
import types
from datetime import datetime, timedelta

import pytest

import device_id
import calendar_store
import review_store
import server_clock
import session_history
import session_manager as sm
import sync_client
import sync_cloud
import sync_live
import tasks_store
from fake_supabase import FakeSupabase

from test_sync_client import fake_logged_in  # noqa: F401  (shared fixture)


@pytest.fixture
def server(monkeypatch):
    s = FakeSupabase(user_id="user-1")
    s.install(monkeypatch, sync_client)
    return s


@pytest.fixture
def device(tmp_path, monkeypatch):
    states = {}

    def _switch(name):
        base = tmp_path / name
        base.mkdir(exist_ok=True)
        states.setdefault(name, copy.deepcopy(sm._DEFAULT_STATE))
        monkeypatch.setattr(sm, "_state", states[name])
        monkeypatch.setattr(sm, "STATE_PATH", str(base / "session_state.json"))
        monkeypatch.setattr(sm, "_open_violation_index", {"process": None, "domain": None})
        monkeypatch.setattr(sm, "_pending_natural_end", {"value": None})
        monkeypatch.setattr(session_history, "HISTORY_PATH", str(base / "session_history.json"))
        monkeypatch.setattr(sync_live, "CURSOR_PATH", str(base / "live_cursor.txt"))
        monkeypatch.setattr(sync_live, "HISTORY_PUSHED_PATH", str(base / "live_history_pushed.txt"))
        monkeypatch.setattr(sync_live, "LIVE_PUSHED_PATH", str(base / "live_state_pushed.txt"))
        monkeypatch.setattr(sync_client, "SYNC_OWNER_PATH", str(base / "owner.txt"))
        monkeypatch.setattr(device_id, "DEVICE_ID_PATH", str(base / "device_id.txt"))
        monkeypatch.setattr(device_id, "_cached_id", None)
        monkeypatch.setattr(calendar_store, "DB_PATH", str(base / "calendar.db"))
        monkeypatch.setattr(calendar_store, "_conn", None)
        monkeypatch.setattr(review_store, "_schema_ready", False)
        monkeypatch.setattr(tasks_store, "TASKS_PATH", str(base / "tasks.json"))
        monkeypatch.setattr(sync_cloud, "_verified", False)
        monkeypatch.setattr(server_clock, "_offset_seconds", 0.0)
        # a review being timed on its own, and the slower lane's bookkeeping
        monkeypatch.setattr(review_store, "ACTIVE_SESSION_PATH", str(base / "active_review.json"))
        monkeypatch.setattr(review_store, "_active_sessions", {})
        monkeypatch.setattr(review_store, "_active_sessions_loaded", False)
        monkeypatch.setattr(review_store, "_review_stamp", None)
        monkeypatch.setattr(review_store, "_ended_token", None)
        monkeypatch.setattr(review_store, "_last_saved_tokens", set())
        monkeypatch.setattr(review_store, "PHOTOS_DIR", str(base / "review_photos"))
        monkeypatch.setattr(sync_live, "REVIEW_PUSHED_PATH", str(base / "live_review_pushed.txt"))
        monkeypatch.setattr(sync_client, "LAST_SYNC_PATH", str(base / "last_sync.txt"))
        monkeypatch.setattr(sync_client, "_cached_last_sync", None)
        monkeypatch.setattr(sync_client, "PULL_CURSOR_PATH", str(base / "pull_cursor.txt"))
        return states[name]
    return _switch


def _both_tick():
    """What the five-second loops of two computers do, back to back."""
    return sync_live.tick()


def _start(**kw):
    defaults = dict(duration_minutes=30, lock_mode="hard", process_blocklist=["discord.exe", "steam.exe"],
                    domain_whitelist=["school.edu"], source="task", event_id="task-1", event_title="usaco")
    defaults.update(kw)
    return sm.start_session(**defaults)


def test_a_session_started_on_one_computer_runs_on_the_other_with_the_same_rules(device, fake_logged_in, server):
    device("a")
    _start()
    assert sync_live.tick() is None
    device("b")
    assert sync_live.tick() is None
    status = sm.get_status()
    assert status["isActive"] and not status["isPaused"]
    assert status["lockMode"] == "hard"
    assert status["processBlocklist"] == ["discord.exe", "steam.exe"]
    assert status["domainWhitelist"] == ["school.edu"]
    assert status["eventId"] == "task-1" and status["eventTitle"] == "usaco"
    assert 29 * 60 <= status["secondsRemaining"] <= 30 * 60
    assert sm.is_blocked("discord.exe") and not sm.is_blocked("notepad.exe")


def test_pause_resume_and_rule_changes_follow_on_the_other_computer(device, fake_logged_in, server):
    device("a")
    _start()
    sync_live.tick()
    device("b")
    sync_live.tick()

    device("a")
    sm.pause_session()
    sync_live.tick()
    device("b")
    sync_live.tick()
    assert sm.get_status()["isPaused"]

    device("a")
    sm.resume_session()
    sm.update_blocklist(["discord.exe"], ["school.edu", "docs.google.com"])
    sync_live.tick()
    device("b")
    sync_live.tick()
    status = sm.get_status()
    assert not status["isPaused"]
    assert status["processBlocklist"] == ["discord.exe"]
    assert "docs.google.com" in status["domainWhitelist"]


def test_changes_made_on_the_second_computer_come_back_to_the_first(device, fake_logged_in, server):
    device("a")
    _start()
    sync_live.tick()
    device("b")
    sync_live.tick()
    sm.pause_session()
    sync_live.tick()
    device("a")
    sync_live.tick()
    assert sm.get_status()["isPaused"]


def test_ending_on_one_computer_ends_it_on_the_other_and_files_one_history_entry(device, fake_logged_in, server):
    device("a")
    _start()
    sync_live.tick()
    device("b")
    sync_live.tick()
    assert sm.get_status()["isActive"]

    device("a")
    sm.end_session()
    sync_live.tick()
    device("b")
    sync_live.tick()
    assert not sm.get_status()["isActive"]
    assert len(session_history.load_all()) == 1
    device("a")
    sync_live.tick()
    assert len(session_history.load_all()) == 1
    assert session_history.load_all()[0]["processBlocklist"] == ["discord.exe", "steam.exe"]


def test_a_three_second_session_never_flashes_on_the_other_computer_but_is_logged(device, fake_logged_in, server):
    device("a")
    _start(duration_minutes=0.05)
    sm.end_session()
    sync_live.tick()
    device("b")
    sync_live.tick()
    assert not sm.get_status()["isActive"]
    assert len(session_history.load_all()) == 1


def test_waiting_sessions_travel_too(device, fake_logged_in, server):
    device("a")
    _start(event_id="one", event_title="one")
    sm.pause_session()
    _start(event_id="two", event_title="two", process_blocklist=["steam.exe"])
    sync_live.tick()
    device("b")
    sync_live.tick()
    status = sm.get_status()
    assert status["eventTitle"] == "two" and status["processBlocklist"] == ["steam.exe"]
    assert [p["eventTitle"] for p in status["parkedSessions"]] == ["one"]
    # and ending the running one brings the waiting one back, here as on the other computer
    sm.end_session()
    assert sm.get_status()["eventTitle"] == "one" and sm.get_status()["isPaused"]


def test_the_same_session_running_out_on_both_computers_is_filed_once(device, fake_logged_in, server):
    device("a")
    _start(duration_minutes=30)
    sync_live.tick()
    device("b")
    sync_live.tick()
    for name in ("a", "b"):
        state = device(name)
        state["endTime"] = (datetime.now() - timedelta(seconds=1)).isoformat()
        sm.get_status()                                    # each notices it ran out
    device("a")
    sync_live.tick()
    device("b")
    sync_live.tick()
    sync_live.tick()
    assert not sm.get_status()["isActive"]
    assert len(session_history.load_all()) == 1
    device("a")
    sync_live.tick()
    assert len(session_history.load_all()) == 1 and not sm.get_status()["isActive"]


def test_a_computer_with_a_wrong_clock_still_ends_the_session_at_the_same_real_moment(device, fake_logged_in, server,
                                                                                    monkeypatch):
    device("a")
    _start(duration_minutes=30)
    a_end = datetime.fromisoformat(sm._state["endTime"])
    sync_live.tick()

    state = device("b")
    real = server_clock.time.time
    monkeypatch.setattr(server_clock, "time", types.SimpleNamespace(time=lambda: real() - 120))   # two minutes slow
    sync_live.tick()
    assert 119 < server_clock.offset_seconds() < 122
    b_end = datetime.fromisoformat(state["endTime"])
    assert abs((a_end - b_end).total_seconds() - server_clock.offset_seconds()) < 1


def test_newest_change_wins_and_an_applied_state_is_not_sent_back(device, fake_logged_in, server):
    device("a")
    _start()
    sync_live.tick()
    device("b")
    sync_live.tick()
    before = server.writes
    sync_live.tick()
    sync_live.tick()
    assert server.writes == before          # nothing changed: nothing re-sent
    posts = [m for m, _ in server.requests].count("POST")
    sync_live.tick()
    assert [m for m, _ in server.requests].count("POST") == posts


def test_a_session_started_while_apart_survives_the_other_computer_saying_nothing_is_running(
        device, fake_logged_in, server):
    device("a")
    _start(event_title="from a")
    sm.end_session()                         # a: ran and finished, then idle
    sync_live.tick()
    device("b")
    _start(event_title="from b")             # b: started its own while it had not heard
    sync_live.tick()
    status = sm.get_status()
    assert status["isActive"] and status["eventTitle"] == "from b"
    device("a")
    sync_live.tick()
    assert sm.get_status()["isActive"] and sm.get_status()["eventTitle"] == "from b"


def test_a_newer_session_from_the_other_computer_replaces_ours_and_ours_is_filed(device, fake_logged_in, server):
    device("a")
    _start(event_title="old on a")
    device("b")
    _start(event_title="newer on b")
    sync_live.tick()
    device("a")
    sync_live.tick()
    assert sm.get_status()["eventTitle"] == "newer on b"
    filed = session_history.load_all()
    assert [e["eventTitle"] for e in filed] == ["old on a"] and filed[0]["endType"] == "superseded"


def test_each_computers_own_window_facts_are_never_overwritten(device, fake_logged_in, server):
    state_a = device("a")
    _start()
    state_a["blockedBrowserProfiles"] = ["A-profile"]
    sm._save()
    sync_live.tick()
    state_b = device("b")
    state_b["blockedBrowserProfiles"] = ["B-profile"]
    sync_live.tick()
    assert sm.get_status()["isActive"] and state_b["blockedBrowserProfiles"] == ["B-profile"]


def test_a_damaged_cloud_record_changes_nothing(device, fake_logged_in, server):
    server[("live_session", "current")] = {
        "table_name": "live_session", "sync_id": "current", "device_id": "x", "is_deleted": False,
        "updated_at": datetime.now().astimezone().isoformat(),
        "data": {"isActive": "yes", "processBlocklist": "discord.exe", "violationLog": 7, "lockMode": 5,
                 "parkedSessions": "no", "endTime": ["x"]},
    }
    device("a")
    assert sync_live.tick() is None
    status = sm.get_status()
    assert not status["isActive"] and status["processBlocklist"] == [] and status["lockMode"] == "soft"


def test_the_shared_review_problem_id_replaces_the_local_row_number(monkeypatch):
    monkeypatch.setattr(sync_live, "_review_problem_sync_id", lambda local: f"sync-{local}" if local else None)
    monkeypatch.setattr(sync_live, "_review_problem_local_id", lambda sid: 99 if sid == "sync-7" else None)
    state = copy.deepcopy(sm._DEFAULT_STATE)
    state.update(isActive=True, reviewProblemId=7, startTime="2026-10-10T09:00:00")
    wire = sync_live.live_state_to_wire(state)
    assert wire["reviewProblemId"] is None and wire["reviewProblemSyncId"] == "sync-7"
    assert sync_live.live_state_from_wire(wire)["reviewProblemId"] == 99


def test_times_inside_waiting_sessions_are_converted_too(monkeypatch):
    monkeypatch.setattr(sync_live, "_review_problem_sync_id", lambda local: None)
    monkeypatch.setattr(sync_live, "_review_problem_local_id", lambda sid: None)
    state = copy.deepcopy(sm._DEFAULT_STATE)
    state["parkedSessions"] = [{**copy.deepcopy(sm._DEFAULT_STATE), "parkId": "p", "startTime": "2026-10-10T09:00:00",
                                "violationLog": [{"kind": "pause", "timestamp": "2026-10-10T09:10:00"}]}]
    wire = sync_live.live_state_to_wire(state)
    assert wire["parkedSessions"][0]["startTime"].endswith("+00:00")
    back = sync_live.live_state_from_wire(wire)
    assert back["parkedSessions"][0]["startTime"] == "2026-10-10T09:00:00"
    assert back["parkedSessions"][0]["violationLog"][0]["timestamp"] == "2026-10-10T09:10:00"


def test_stamps_only_go_up():
    stamps = [server_clock.next_stamp() for _ in range(200)]
    assert stamps == sorted(set(stamps), key=server_clock.parse)
    assert server_clock.parse(server_clock.next_stamp(at_least=stamps[-1])) > server_clock.parse(stamps[-1])


def test_a_response_without_a_usable_date_keeps_the_old_gap(monkeypatch):
    monkeypatch.setattr(server_clock, "_offset_seconds", 5.0)
    server_clock.note_response(types.SimpleNamespace(headers={}))
    server_clock.note_response(types.SimpleNamespace(headers={"date": "not a date"}))
    server_clock.note_response(types.SimpleNamespace(headers={"date": "Mon, 01 Jan 2001 00:00:00 GMT"}))  # absurd gap
    assert server_clock.offset_seconds() == 5.0


def test_logging_out_stops_the_loop_from_doing_anything(device, monkeypatch):
    device("a")
    import auth_manager
    monkeypatch.setattr(auth_manager, "is_logged_in", lambda: False)
    assert sync_live.tick() == "not logged in"
