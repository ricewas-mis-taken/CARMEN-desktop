"""A failing polling tick must be logged (once per distinct error), not swallowed."""
import threading
import types

import window_tracker


def test_a_failing_tick_is_logged_once_not_swallowed_or_spammed(monkeypatch):
    logged = []

    class FakeLogger:
        def exception(self, message, *args):
            logged.append(message)

    def boom():
        raise RuntimeError("bad status")

    monkeypatch.setattr(window_tracker, "logger", FakeLogger())
    monkeypatch.setattr(window_tracker, "POLL_INTERVAL_SECONDS", 0.01)
    # Only the tracker's own reference is replaced: live Qt widgets from other tests
    # call the real session_manager from the main thread and must not see this.
    monkeypatch.setattr(window_tracker, "session_manager", types.SimpleNamespace(get_status=boom))
    stop = threading.Event()
    thread = threading.Thread(target=window_tracker.run_polling_loop, args=(stop,), daemon=True)
    thread.start()
    stop.wait(0.3)  # many ticks, all failing the same way
    stop.set()
    thread.join(2)
    assert len(logged) == 1 and "polling tick failed" in logged[0]
