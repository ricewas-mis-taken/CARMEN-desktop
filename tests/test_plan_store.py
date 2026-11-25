from datetime import date

import pytest

import board_store
import plan_ml
import plan_store
import quicknote_store
import score_store

DAY = date(2026, 11, 24)


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(plan_ml, "EXAMPLES_PATH", str(tmp_path / "ex.json"))
    monkeypatch.setattr(plan_ml, "REPORTS_PATH", str(tmp_path / "rep.jsonl"))
    monkeypatch.setattr(plan_ml, "_model", None)
    monkeypatch.setattr(plan_store, "PATH", str(tmp_path / "plans.json"))
    monkeypatch.setattr(plan_store, "NOTE_META_PATH", str(tmp_path / "plan_notes.json"))
    monkeypatch.setattr(board_store, "BOARD_PATH", str(tmp_path / "board.json"))
    monkeypatch.setattr(quicknote_store, "PATH", str(tmp_path / "quicknote.json"))
    monkeypatch.setattr(score_store, "PATH", str(tmp_path / "score.json"))


def _entry(text, kind, minutes=None, tags=None):
    return {"text": text, "kind": kind, "minutes": minutes, "tags": tags or []}


def _plan(*entries):
    return plan_store.save_plan(DAY, list(entries))


def test_each_kind_is_filed_where_it_belongs():
    items = _plan(_entry("write essay 45 min", "task", 45, ["study"]),
                  _entry("portfolio website", "board", tags=["creative"]),
                  _entry("buy stamps", "note"))
    assert [i["kind"] for i in items] == ["task", "board", "note"]
    assert items[0]["text"] == "write essay" and items[0]["minutes"] == 45
    card = board_store.get_task(items[1]["ref"])
    assert card["plannedFor"] == "2026-11-24" and card["tags"] == ["creative"]
    assert "buy stamps" in quicknote_store.load()[0]
    assert plan_store.note_meta("buy stamps")["day"] == "2026-11-24"


def test_a_line_that_is_already_on_the_board_is_not_added_twice():
    first = _plan(_entry("portfolio website", "board"))
    second = plan_store.save_plan(date(2026, 11, 25), [_entry("Portfolio Website", "board")])
    assert first[0]["ref"] == second[0]["ref"]
    assert len(board_store.load_board()) == 1


def test_ticking_a_board_item_finishes_its_card_and_a_note_leaves_the_quicknote():
    items = _plan(_entry("portfolio website", "board"), _entry("buy stamps", "note"))
    plan_store.set_done(DAY, items[0]["id"])
    plan_store.set_done(DAY, items[1]["id"])
    assert board_store.get_task(items[0]["ref"])["finished"]
    assert "buy stamps" not in quicknote_store.load()[0]


def test_the_lines_teach_the_model():
    _plan(_entry("fix bike", "note"))
    assert plan_ml.predict("fix bike")["label"] == "note"


def test_unfinished_items_can_be_carried_to_the_next_plan():
    items = _plan(_entry("a task 30 min", "task", 30), _entry("b task 30 min", "task", 30))
    plan_store.set_done(DAY, items[0]["id"])
    assert [i["text"] for i in plan_store.unfinished(DAY)] == ["b task"]


def test_a_day_is_judged_on_at_most_eight_real_items():
    entries = [_entry("job %d 30 min" % n, "task", 30) for n in range(12)] + [_entry("tiny 5 min", "task", 5)]
    items = _plan(*entries)
    assert plan_store.progress(DAY)[1] == plan_store.MAX_COUNTED_ITEMS
    assert not [i for i in plan_store.counted(items) if i["text"] == "tiny"]


def _settled_points(done_count, total, day=DAY):
    entries = [_entry("job %d 30 min" % n, "task", 30) for n in range(total)]
    items = plan_store.save_plan(day, entries)
    for item in items[:done_count]:
        plan_store.set_done(day, item["id"])
    data = score_store.load()
    plan_store.score_day(day, data)
    return sum(e["points"] for e in data["events"])


def test_the_score_rewards_planning_and_how_much_was_done():
    assert _settled_points(4, 4) == plan_store.PLAN_MADE_POINTS + plan_store.SWING
    assert _settled_points(0, 4, date(2026, 11, 20)) == plan_store.PLAN_MADE_POINTS - plan_store.SWING
    assert _settled_points(2, 4, date(2026, 11, 21)) == plan_store.PLAN_MADE_POINTS


def test_a_one_line_plan_earns_only_the_planning_points_and_scoring_twice_changes_nothing():
    items = _plan(_entry("only job 30 min", "task", 30))
    plan_store.set_done(DAY, items[0]["id"])
    data = score_store.load()
    assert plan_store.score_day(DAY, data) == 1
    assert plan_store.score_day(DAY, data) == 0


def test_settling_a_day_scores_its_plan(monkeypatch):
    items = _plan(_entry("a 30 min", "task", 30), _entry("b 30 min", "task", 30))
    for item in items:
        plan_store.set_done(DAY, item["id"])
    data = score_store.load()
    data["settledThrough"] = "2026-11-23"                                 # the ledger began before the plan's day
    score_store._save(data)
    score_store.settle(today=date(2026, 11, 26), tasks=[], sessions=[])
    events = {e["key"]: e["points"] for e in score_store.load()["events"]}
    assert events == {"plan:2026-11-24": plan_store.PLAN_MADE_POINTS, "plandone:2026-11-24": plan_store.SWING}


def test_saving_the_plan_again_keeps_what_was_already_ticked():
    items = _plan(_entry("a job 30 min", "task", 30), _entry("b job 30 min", "task", 30))
    plan_store.set_done(DAY, items[0]["id"])
    again = _plan(_entry("a job 30 min", "task", 30), _entry("b job 30 min", "task", 30), _entry("c job 30 min", "task", 30))
    assert [i["done"] for i in again] == [True, False, False]
