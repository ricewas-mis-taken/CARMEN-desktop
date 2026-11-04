import pytest

import devices_store


@pytest.fixture(autouse=True)
def _own_file(tmp_path, monkeypatch):
    monkeypatch.setattr(devices_store, "PATH", str(tmp_path / "devices.json"))
    devices_store._last_write.clear()
    monkeypatch.setattr(devices_store.device_id, "get_device_id", lambda: "pc-1")


CHROME = "Mozilla/5.0 Chrome/120.0 Safari/537.36"


def test_a_request_from_an_extension_is_remembered_as_a_link():
    assert devices_store.note_extension("chrome-extension://abc", CHROME)
    pcs, links, live = devices_store.totals()
    assert (pcs, links, live) == (1, 1, 1)
    assert devices_store.summary()[0]["links"][0]["browser"] == "Chrome"


def test_requests_that_are_not_from_an_extension_are_ignored():
    assert not devices_store.note_extension("http://localhost:3000", CHROME)
    assert not devices_store.note_extension("", CHROME)
    assert devices_store.totals() == (0, 0, 0)


def test_last_seen_is_not_rewritten_on_every_request():
    assert devices_store.note_extension("chrome-extension://abc", CHROME)
    assert not devices_store.note_extension("chrome-extension://abc", CHROME)


def test_two_browsers_on_one_pc_are_two_links():
    devices_store.note_extension("chrome-extension://abc", CHROME)
    devices_store.note_extension("moz-extension://xyz", "Mozilla/5.0 Firefox/120.0")
    assert devices_store.totals()[:2] == (1, 2)


def test_another_computers_row_counts_as_a_second_pc():
    devices_store.note_extension("chrome-extension://abc", CHROME)
    rows = devices_store.load(include_deleted=True)
    rows.append({"id": "pc-2", "computer": "LAPTOP", "account": "a@b.c", "updatedAt": "2026-01-01T00:00:00+00:00",
                 "extensions": [{"browser": "Edge", "extensionId": "q", "lastSeen": "2026-01-01T00:00:00+00:00"}]})
    devices_store.save(rows)
    assert devices_store.totals() == (2, 2, 1)       # the laptop's link was last seen long ago: not connected
