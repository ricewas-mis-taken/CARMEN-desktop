"""The Scribe board is shared between computers: layers by last change, images as photos."""
import pytest

import scribe_store
import server_clock
import sync_live
import sync_photos


@pytest.fixture(autouse=True)
def _fresh_clock(monkeypatch):
    monkeypatch.setattr(server_clock, "_last_stamp", None)
    monkeypatch.setattr(server_clock, "_offset_seconds", 0.0)


def _doc_with(text):
    doc = scribe_store.default_doc()
    doc["layers"][0]["items"].append({"type": "text", "a": [0, 0], "text": text, "w": 50, "h": 20, "size": 16})
    return doc


def test_only_layer_changes_are_stamped_and_shared(isolate_state):
    assert scribe_store.export() is None                      # an untouched board shares nothing
    doc = _doc_with("hello")
    scribe_store.save(doc)
    first = scribe_store.export()
    assert first["layers"][0]["items"][0]["text"] == "hello"
    doc["view"]["zoom"] = 3.0                                 # panning/zooming is local
    scribe_store.save(doc)
    assert scribe_store.export()["updatedAt"] == first["updatedAt"]
    doc["layers"][0]["items"][0]["text"] = "changed"
    scribe_store.save(doc)
    assert scribe_store.export()["updatedAt"] != first["updatedAt"]


def test_a_newer_remote_board_replaces_layers_but_keeps_the_local_view(isolate_state):
    local = _doc_with("mine")
    local["view"]["zoom"] = 2.0
    scribe_store.save(local)
    remote = _doc_with("theirs")["layers"]
    assert scribe_store.apply_remote(remote, "2999-01-01T00:00:00+00:00")
    loaded = scribe_store.load()
    assert loaded["layers"][0]["items"][0]["text"] == "theirs" and loaded["view"]["zoom"] == 2.0
    assert not scribe_store.apply_remote(_doc_with("old")["layers"], "2000-01-01T00:00:00+00:00")
    assert not scribe_store.apply_remote("junk", "2999-02-01T00:00:00+00:00")


def test_live_lane_gathers_once_and_applies_remote(isolate_state):
    scribe_store.save(_doc_with("hi"))
    records, stamp = sync_live._gather_scribe()
    assert records[0]["table_name"] == "scribe" and records[0]["sync_id"] == "board" and stamp
    sync_live._write(sync_live.SCRIBE_PUSHED_PATH, stamp)
    assert sync_live._gather_scribe() == ([], None)           # nothing new: nothing sent
    sync_live._apply_scribe([{
        "sync_id": "board", "updated_at": "2999-01-01T00:00:00+00:00",
        "data": {"layers": _doc_with("remote")["layers"]}}])
    assert scribe_store.load()["layers"][0]["items"][0]["text"] == "remote"
    assert sync_live._gather_scribe() == ([], None)           # what arrived is not echoed back


def test_images_on_the_board_are_synced_as_photos(isolate_state):
    doc = scribe_store.default_doc()
    doc["layers"][0]["items"].append({"type": "image", "a": [0, 0], "w": 10, "h": 10, "file": "abc123.png"})
    scribe_store.save(doc)
    assert ("scribe", "abc123.png") in sync_photos._referenced_photos()
    assert scribe_store.image_names() == ["abc123.png"]


def test_zoom_out_has_no_practical_floor():
    assert scribe_store.MIN_ZOOM <= 1e-5
