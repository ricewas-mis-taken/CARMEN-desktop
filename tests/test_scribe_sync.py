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


def test_live_lane_gathers_once_and_applies_remote(isolate_state, monkeypatch):
    monkeypatch.setattr(scribe_store, "SETTLE_SECONDS", 0)
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


def test_a_second_save_does_not_reread_the_file_and_still_sees_in_place_edits(isolate_state):
    doc = scribe_store.load()
    doc["layers"][0]["items"].append({"type": "rect", "a": [0, 0], "b": [9, 9], "stroke": "#000000", "width": 2})
    scribe_store.save(doc)
    stamp = scribe_store.export()["updatedAt"]
    scribe_store.save(doc)                                   # nothing changed: no new stamp
    assert scribe_store.export()["updatedAt"] == stamp
    doc["layers"][0]["items"][0]["stroke"] = "#ff0000"       # edited in place, as the canvas does
    scribe_store.save(doc)
    assert scribe_store.export()["updatedAt"] != stamp


def test_the_board_is_not_shared_while_it_is_being_drawn_on(monkeypatch):
    import sync_live
    monkeypatch.setattr(scribe_store, "export", lambda: (_ for _ in ()).throw(AssertionError("walked the board")))
    scribe_store._mark_changed()
    assert not scribe_store.is_settled()
    assert sync_live._gather_scribe() == ([], None)


def test_a_save_after_one_new_stroke_reuses_the_text_of_the_rest(monkeypatch):
    doc = _doc_with("hi")
    for k in range(50):
        doc["layers"][0]["items"].append(
            {"type": "stroke", "pts": [[k, 0], [k + 1, 5], [k + 2, 9]], "stroke": "#000000", "width": 3})
    scribe_store.save(doc, trusted=True)
    dumped = []
    real = scribe_store.json.dumps
    monkeypatch.setattr(scribe_store.json, "dumps", lambda *a, **k: dumped.append(1) or real(*a, **k))
    doc["layers"][0]["items"].append({"type": "stroke", "pts": [[0, 0], [9, 9]], "stroke": "#000000", "width": 3})
    scribe_store.save(doc, trusted=True)
    assert len(dumped) < 10                       # the new stroke, the few non-stroke items and the file's head
    assert len(scribe_store.load()["layers"][0]["items"]) == 52     # the text, 50 strokes and the new one


def test_a_stroke_that_changes_is_written_again_not_served_from_memory():
    doc = _doc_with("hi")
    stroke = {"type": "stroke", "pts": [[0, 0], [1, 1]], "stroke": "#000000", "width": 3}
    doc["layers"][0]["items"].append(stroke)
    scribe_store.save(doc, trusted=True)
    stroke["stroke"] = "#FF0000"                   # edited in place
    scribe_store.save(doc, trusted=True)
    assert scribe_store.load()["layers"][0]["items"][-1]["stroke"] == "#FF0000"
    stroke["pts"].append([5, 5])                   # grown in place, like a line being drawn
    scribe_store.save(doc, trusted=True)
    assert len(scribe_store.load()["layers"][0]["items"][-1]["pts"]) == 3
    stroke["pts"] = [[p[0] + 10, p[1]] for p in stroke["pts"]]    # moved: a new list
    scribe_store.save(doc, trusted=True)
    assert scribe_store.load()["layers"][0]["items"][-1]["pts"][0] == [10, 0]


def test_a_board_over_the_sync_limit_is_only_measured_once(monkeypatch):
    doc = _doc_with("hi")
    scribe_store.save(doc)
    monkeypatch.setattr(scribe_store, "MAX_SYNC_BYTES", 10)
    assert scribe_store.export() is None
    calls = []
    real = scribe_store._layers_text
    monkeypatch.setattr(scribe_store, "_layers_text", lambda l: calls.append(1) or real(l))
    assert scribe_store.export() is None and calls == []
