"""A pull from another computer must reach the open window, and the realtime
channel must treat slow-lane tables (review, tasks, board) as news."""
import sync_client
import sync_realtime
import qt_ui.main_window as main_window


def _msg(table, device):
    return {"payload": {"data": {"record": {"table_name": table, "device_id": device}}}}


def test_slow_lane_change_from_another_device_is_news():
    assert sync_realtime._is_slow_lane_news(_msg("review_problems", "pc"), "mac")
    assert sync_realtime._is_slow_lane_news(_msg("tasks", "pc"), "mac")


def test_own_changes_and_fast_lane_tables_are_not_slow_lane_news():
    assert not sync_realtime._is_slow_lane_news(_msg("review_problems", "mac"), "mac")
    assert not sync_realtime._is_slow_lane_news(_msg("quicknote", "pc"), "mac")
    assert not sync_realtime._is_slow_lane_news({"payload": {}}, "mac")


def test_listener_is_registered_and_failures_do_not_escape(monkeypatch):
    calls = []
    monkeypatch.setattr(sync_client, "_pulled_listeners", [])
    sync_client.add_pulled_listener(lambda t: calls.append(t))
    sync_client.add_pulled_listener(lambda t: 1 / 0)
    for fn in list(sync_client._pulled_listeners):
        try:
            fn({"tasks"})
        except ZeroDivisionError:
            pass
    assert calls == [{"tasks"}]


def test_refresh_after_pull_refreshes_only_the_changed_pages(monkeypatch):
    class Page:
        def __init__(self):
            self.n = 0

        def refresh(self):
            self.n += 1

    pages = {k: Page() for k in ("home", "tasks", "board", "review")}
    monkeypatch.setattr(main_window, "_win", type("W", (), {"_pages": pages})())
    main_window.refresh_after_pull({"review_sessions", "review_problems"})
    assert (pages["review"].n, pages["home"].n, pages["tasks"].n, pages["board"].n) == (1, 1, 0, 0)
