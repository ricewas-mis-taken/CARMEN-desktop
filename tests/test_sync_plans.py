"""The nightly plan and what plan_ml has learned travel between computers like the QuickNote does."""
from datetime import date, timedelta

import plan_ml
import plan_store
import quicknote_store
import sync_live

from test_sync_live_session import device, server  # noqa: F401  (shared fixtures)
from test_sync_client import fake_logged_in  # noqa: F401

DAY = date.today() + timedelta(days=1)


def _task(text, minutes=30):
    return {"text": text, "kind": "task", "minutes": minutes, "tags": ["study"]}


def test_a_plan_made_on_one_computer_shows_on_the_other(device, fake_logged_in, server):
    device("a")
    plan_store.save_plan(DAY, [_task("write essay 45 min", 45), _task("read ch3 30 min")])
    assert sync_live.tick() is None
    device("b")
    assert sync_live.tick() is None
    texts = [i["text"] for i in plan_store.load_day(DAY)["items"]]
    assert texts == ["write essay", "read ch3"]


def test_ticking_an_item_on_one_computer_reaches_the_other(device, fake_logged_in, server):
    device("a")
    items = plan_store.save_plan(DAY, [_task("write essay 45 min", 45)])
    sync_live.tick()
    device("b")
    sync_live.tick()
    plan_store.set_done(DAY, items[0]["id"])
    sync_live.tick()
    device("a")
    sync_live.tick()
    assert plan_store.load_day(DAY)["items"][0]["done"]


def test_an_older_copy_of_a_day_never_replaces_a_newer_one(device, fake_logged_in, server):
    device("a")
    first = plan_store.save_plan(DAY, [_task("old plan 30 min")])
    sync_live.tick()
    device("b")
    sync_live.tick()
    plan_store.save_plan(DAY, [_task("newer plan 30 min")])
    sync_live.tick()
    device("a")
    sync_live.tick()
    assert [i["text"] for i in plan_store.load_day(DAY)["items"]] == ["newer plan"]
    assert first[0]["text"] == "old plan"


def test_planned_quicknote_lines_keep_their_planned_for_mark(device, fake_logged_in, server):
    device("a")
    plan_store.save_plan(DAY, [{"text": "buy stamps", "kind": "note", "tags": ["shopping"]}])
    sync_live.tick()
    device("b")
    sync_live.tick()
    meta = plan_store.note_meta("buy stamps")
    assert meta and meta["day"] == DAY.isoformat() and meta["tags"] == ["shopping"]


def test_what_one_computer_learned_is_merged_into_the_other(device, fake_logged_in, server):
    device("a")
    plan_ml.teach("fix bike", "note")
    sync_live.tick()
    device("b")
    plan_ml.teach("garden redesign", "board")
    sync_live.tick()
    assert plan_ml.predict("fix bike")["label"] == "note"                # learned on a, now on b
    sync_live.tick()
    device("a")
    sync_live.tick()
    assert plan_ml.predict("garden redesign")["label"] == "board"        # and the other way round
    assert plan_ml.predict("fix bike")["label"] == "note"                # nothing local was lost


def test_the_newer_lesson_for_the_same_line_wins(device, fake_logged_in, server):
    device("a")
    plan_ml.teach("water plants", "task", minutes=10)
    sync_live.tick()
    device("b")
    sync_live.tick()
    plan_ml.teach("water plants", "note", corrected=True)
    sync_live.tick()
    device("a")
    sync_live.tick()
    assert plan_ml.predict("water plants")["label"] == "note"


def test_damaged_shared_plans_change_nothing(device, fake_logged_in, server):
    from datetime import datetime
    stamp = datetime.now().astimezone().isoformat()
    server[("plans", DAY.isoformat())] = {"table_name": "plans", "sync_id": DAY.isoformat(), "device_id": "x",
                                           "is_deleted": False, "updated_at": stamp, "data": {"items": "nope"}}
    server[("plan_examples", "abc")] = {"table_name": "plan_examples", "sync_id": "abc", "device_id": "x",
                                         "is_deleted": False, "updated_at": stamp,
                                         "data": {"line": 5, "label": "task", "at": stamp}}
    device("a")
    assert sync_live.tick() is None
    assert not plan_store.has_plan(DAY) and plan_ml.example_count() == 0
