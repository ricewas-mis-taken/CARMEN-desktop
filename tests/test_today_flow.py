import today_flow


def _t(id_, minutes, **kw):
    return {"id": id_, "kind": "task", "minutes": minutes, "done": False, "start": None, **kw}


def test_tasks_flow_one_after_another_with_a_breather():
    out = today_flow.flow([_t("a", 30), _t("b", 45)], now_minutes=9 * 60, stop_minutes=22 * 60 + 30)
    first, second = out["blocks"]
    assert (first["start"], first["end"]) == (540, 570)
    assert second["start"] == 570 + today_flow.BREATHER
    assert out["overflow"] == 0 and out["slack"] > 0


def test_a_plan_made_ahead_starts_at_the_usual_day_start():
    out = today_flow.flow([_t("a", 30)], now_minutes=23 * 60, stop_minutes=22 * 60 + 30, planning_ahead=True)
    assert out["blocks"][0]["start"] == today_flow.DAY_START


def test_blocks_start_on_five_minute_marks():
    out = today_flow.flow([_t("a", 20)], now_minutes=9 * 60 + 3, stop_minutes=24 * 60)
    assert out["blocks"][0]["start"] % 5 == 0 and out["blocks"][0]["start"] >= 9 * 60 + 3


def test_a_pinned_task_keeps_its_time_and_others_fit_around_it():
    items = [_t("a", 30), _t("pin", 60, start=11 * 60), _t("b", 120)]
    out = today_flow.flow(items, now_minutes=9 * 60, stop_minutes=24 * 60)
    by_id = {b["item"]["id"]: b for b in out["blocks"]}
    assert by_id["pin"]["start"] == 11 * 60 and by_id["pin"]["pinned"]
    assert by_id["a"]["end"] <= 11 * 60                       # fits before the pin
    assert by_id["b"]["start"] >= by_id["pin"]["end"]         # does not fit before it, so goes after


def test_what_does_not_fit_before_the_stop_is_reported_as_overflow():
    out = today_flow.flow([_t("a", 120), _t("b", 120)], now_minutes=20 * 60, stop_minutes=22 * 60 + 30)
    assert out["overflow"] > 0 and out["slack"] == 0


def test_done_tasks_stay_and_do_not_use_up_the_rest_of_the_day():
    items = [_t("a", 60, done=True), _t("b", 30)]
    out = today_flow.flow(items, now_minutes=13 * 60, stop_minutes=22 * 60 + 30)
    blocks = {b["item"]["id"]: b for b in out["blocks"]}
    assert blocks["a"]["item"]["done"] and blocks["b"]["start"] >= 13 * 60


def test_only_tasks_with_minutes_are_laid_out():
    items = [{"id": "n", "kind": "note", "text": "x"}, {"id": "bd", "kind": "board"}, _t("a", 30)]
    assert [b["item"]["id"] for b in today_flow.flow(items, 9 * 60, 24 * 60)["blocks"]] == ["a"]


def test_clock_and_length_text():
    assert today_flow.clock_text(0) == "12:00 am" and today_flow.clock_text(13 * 60 + 5) == "1:05 pm"
    assert today_flow.clock_text(25 * 60) == "1:00 am"
    assert today_flow.length_text(45) == "45 min" and today_flow.length_text(90) == "1 h 30 min"


def test_a_done_task_sits_where_it_really_ended():
    out = today_flow.flow([_t("a", 45, done=True, doneMinute=10 * 60 + 30)], now_minutes=14 * 60, stop_minutes=24 * 60)
    block = out["blocks"][0]
    assert block["end"] == 10 * 60 + 30 and block["start"] == 10 * 60 + 30 - 45
