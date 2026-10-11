"""A review being timed with no task session under it is shared too: start it
on one computer and the other shows the same running review; finish it on
either and it ends on both."""
import calendar_store
import review_store
import sync_client
import sync_live

from test_sync_client import fake_logged_in  # noqa: F401  (shared fixture)
from test_sync_live_session import device, server  # noqa: F401  (shared fixtures)


def _problem_on_both(switch):
    """One problem that exists, with the same shared id, on both computers.
    Leaves "a" as the current computer; returns the topic's id there."""
    switch("a")
    topic = review_store.create_topic("USACO")
    subject = review_store.create_subject(topic["id"], "Sets", "#4A90D9")
    review_store.create_problem(topic["id"], subject["id"], "Two Sum", 3, "text", description_text="x")
    assert sync_client.sync_now().success
    switch("b")
    assert sync_client.sync_now().success
    switch("a")
    return topic["id"]


def _local_problem_id(name="Two Sum"):
    with calendar_store._lock:
        review_store._get_conn()
        row = calendar_store._get_conn().execute("SELECT id FROM review_problems WHERE name = ?", (name,)).fetchone()
    return row["id"]


def test_a_review_started_on_one_computer_is_running_on_the_other(device, fake_logged_in, server):
    _problem_on_both(device)
    token = review_store.start_review(_local_problem_id())
    assert token
    assert sync_live.tick() is None
    device("b")
    assert sync_live.tick() is None
    active = review_store.get_active_review()
    assert active and active["token"] == token and active["problemName"] == "Two Sum"
    assert not active["isPaused"]


def test_pause_and_resume_follow_and_finishing_ends_it_everywhere(device, fake_logged_in, server):
    _problem_on_both(device)
    token = review_store.start_review(_local_problem_id())
    sync_live.tick()
    device("b")
    sync_live.tick()

    review_store.pause_active_review()
    sync_live.tick()
    device("a")
    sync_live.tick()
    assert review_store.get_active_review()["isPaused"]

    review_store.resume_active_review()
    sync_live.tick()
    device("b")
    sync_live.tick()
    assert not review_store.get_active_review()["isPaused"]

    review_store.finish_review(token, self_solved=True, shakiness=2, duration_seconds=600)
    sync_live.tick()
    device("a")
    sync_live.tick()
    assert review_store.get_active_review() is None
    # the review's result reaches the other computer through the ordinary sync
    assert sync_client.sync_now().success
    device("b")
    assert sync_client.sync_now().success
    assert review_store.get_problem(_local_problem_id())["reviewCount"] == 1


def test_a_review_started_while_apart_is_kept_when_the_other_says_nothing_is_running(device, fake_logged_in, server):
    _problem_on_both(device)
    token_a = review_store.start_review(_local_problem_id())
    review_store.abandon_review(token_a)              # a: started, then dropped it
    sync_live.tick()
    device("b")
    token_b = review_store.start_review(_local_problem_id())   # b had not heard
    sync_live.tick()
    assert review_store.get_active_review()["token"] == token_b
    device("a")
    sync_live.tick()
    assert review_store.get_active_review()["token"] == token_b


def test_a_review_whose_problem_has_not_arrived_waits_for_it(device, fake_logged_in, server):
    _problem_on_both(device)
    token = review_store.start_review(_local_problem_id())
    sync_live.tick()
    device("b")
    with calendar_store._lock:
        review_store._get_conn()
        conn = calendar_store._get_conn()
        real_id = conn.execute("SELECT sync_id FROM review_problems WHERE name='Two Sum'").fetchone()["sync_id"]
        conn.execute("UPDATE review_problems SET sync_id = 'not-yet' WHERE name='Two Sum'")
        conn.commit()
    sync_live.tick()
    assert review_store.get_active_review() is None
    with calendar_store._lock:
        conn.execute("UPDATE review_problems SET sync_id = ? WHERE name='Two Sum'", (real_id,))
        conn.commit()
    sync_live.tick()
    assert review_store.get_active_review()["token"] == token


def test_a_damaged_review_record_changes_nothing(device, fake_logged_in, server):
    from datetime import datetime
    server[("live_review", "current")] = {
        "table_name": "live_review", "sync_id": "current", "device_id": "x", "is_deleted": False,
        "updated_at": datetime.now().astimezone().isoformat(),
        "data": {"review": {"token": 5, "problemSyncId": [], "startedAt": "garbage"}},
    }
    device("a")
    assert sync_live.tick() is None
    assert review_store.get_active_review() is None


def test_the_banner_follows_the_shared_review_and_lets_go_when_it_ends(qtbot, device, fake_logged_in, server):
    import qt_ui.review_tab as review_tab
    topic_id = _problem_on_both(device)
    token = review_store.start_review(_local_problem_id())
    sync_live.tick()
    device("b")
    view = review_tab._TopicView(review_store.list_topics()[0]["id"])
    qtbot.addWidget(view)
    assert not view._review_banner.is_running()
    sync_live.tick()
    view._show_banner_for_linked_session()
    assert view._review_banner.is_running()
    # finished on the first computer -> the banner here lets go
    device("a")
    review_store.finish_review(token, self_solved=True, shakiness=2, duration_seconds=60)
    sync_live.tick()
    device("b")
    sync_live.tick()
    view._review_banner._tick()
    assert not view._review_banner.is_running()


def test_the_finish_dialog_is_not_covered_by_the_banner_coming_back(qtbot, isolate_review_db, isolate_state):
    import qt_ui.review_tab as review_tab
    topic = review_store.create_topic("T")
    subject = review_store.create_subject(topic["id"], "S", "#123456")
    review_store.create_problem(topic["id"], subject["id"], "P", 3, "text", description_text="x")
    token = review_store.start_review(_local_problem_id("P"))
    view = review_tab._TopicView(topic["id"])
    qtbot.addWidget(view)
    assert view._review_banner.is_running()
    view._review_banner._finish()                 # opens the "how did it go" dialog
    view._show_banner_for_linked_session()
    assert not view._review_banner.isVisible()
    assert review_store.get_active_review()["token"] == token   # not over yet, banner stays down
