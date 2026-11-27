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


def test_event_filters_do_nothing_while_one_is_already_running(qtbot):
    from qt_ui import filter_guard, motion, popup_fit
    seen = []
    with filter_guard.quiet() as outermost:
        assert outermost and filter_guard.busy()
        with filter_guard.quiet() as inner:
            assert not inner                                   # already quiet: the inner call is a no-op
        card, fitter = motion.CardHover(), popup_fit._Fitter()
        assert card.eventFilter(None, object()) is False       # would raise on a real use of the event
        assert fitter.eventFilter(None, object()) is False
    assert not filter_guard.busy()
