import gc

import gc_guard


def test_the_main_thread_takes_over_cycle_collection_and_scribe_cannot_turn_it_back_on(qtbot, monkeypatch):
    was = gc.isenabled()
    try:
        gc_guard.install()
        assert not gc.isenabled() and gc_guard.ACTIVE
        gc_guard.resume()
        assert not gc.isenabled()
    finally:
        gc_guard._timer.stop()
        gc_guard.ACTIVE = False
        gc.enable() if was else gc.disable()
