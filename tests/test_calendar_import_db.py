import calendar_store


def _event(title):
    return {"title": title, "start": "2026-10-05T09:00:00", "end": "2026-10-05T10:00:00"}


def test_failed_import_leaves_store_usable(isolate_calendar_db, tmp_path):
    calendar_store.save_event(_event("keep"))
    bad = tmp_path / "bad.db"
    bad.write_bytes(b"this is not sqlite" * 100)

    assert calendar_store.import_db(str(bad)) is False

    assert [e["title"] for e in calendar_store.list_events()] == ["keep"]
    assert calendar_store.save_event(_event("after")) is not None


def test_successful_import_still_works(isolate_calendar_db, tmp_path):
    calendar_store.save_event(_event("exported"))
    backup = tmp_path / "backup.db"
    assert calendar_store.export_db(str(backup))
    calendar_store.save_event(_event("later"))

    assert calendar_store.import_db(str(backup)) is True
    assert [e["title"] for e in calendar_store.list_events()] == ["exported"]
