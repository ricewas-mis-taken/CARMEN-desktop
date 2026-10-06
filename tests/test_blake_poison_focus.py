import calendar_store, sync_client


def _rec(table, sid, data, ts="2030-01-01T00:00:00+00:00"):
    return {"table_name": table, "sync_id": sid, "data": data, "device_id": "dev-x", "updated_at": ts, "is_deleted": False}


def test_one_bad_synced_focus_profile_does_not_blank_the_whole_calendar(isolate_calendar_db):
    good = calendar_store.save_event({"title": "Good", "start": "2030-06-10T09:00:00", "end": "2030-06-10T10:00:00"})
    victim = calendar_store.save_event({"title": "Victim", "start": "2030-06-11T09:00:00", "end": "2030-06-11T10:00:00"})
    print("events before:", [e["title"] for e in calendar_store.list_events()])
    res = sync_client._apply_calendar_records({
        "focus_profiles": [_rec("focus_profiles", victim,
                                {"enabled": True, "lockMode": "soft", "processBlocklist": "not-json", "domainWhitelist": "[]", "warningMinutes": 5})],
    })
    print("apply result (applied, skipped, failed):", res)
    titles = [e["title"] for e in calendar_store.list_events()]
    print("events after:", titles)
    assert "Good" in titles
