"""Sharing or saving a big board while the pen is down held the interpreter long enough for a stroke's points to
arrive late (the line came out straight). Neither happens under a stroke now."""
import time

import scribe_store
from qt_ui import scribe_tab


def test_a_board_is_not_shared_while_the_pen_is_down(monkeypatch):
    monkeypatch.setattr(scribe_store, "_changed_at", time.monotonic() - 1000)
    assert scribe_store.is_settled()
    scribe_store.set_pen_down(True)
    try:
        assert not scribe_store.is_settled()
    finally:
        scribe_store.set_pen_down(False)
    assert scribe_store.is_settled()


def test_a_missed_release_does_not_block_sharing_forever(monkeypatch):
    monkeypatch.setattr(scribe_store, "_changed_at", time.monotonic() - 1000)
    scribe_store.set_pen_down(True)
    monkeypatch.setattr(scribe_store, "_pen_down_at", time.monotonic() - 3600)
    assert scribe_store.is_settled()
    scribe_store.set_pen_down(False)


def test_an_oversized_board_is_not_read_back_to_find_out(monkeypatch, tmp_path):
    big = tmp_path / "scribe.json"
    big.write_bytes(b" " * (2 * scribe_store.MAX_SYNC_BYTES + 1))
    monkeypatch.setattr(scribe_store, "PATH", str(big))
    monkeypatch.setattr(scribe_store, "load", lambda: (_ for _ in ()).throw(AssertionError("read the board")))
    assert scribe_store.export() is None


def test_saving_waits_for_the_pen(qtbot):
    tab = scribe_tab.ScribeTab()
    qtbot.addWidget(tab)
    tab._dirty = True
    scribe_store.set_pen_down(True)
    try:
        tab._flush_in_background()
        assert tab._saver is None and tab._dirty and tab._save_timer.isActive()
    finally:
        scribe_store.set_pen_down(False)
        tab._dirty = False
