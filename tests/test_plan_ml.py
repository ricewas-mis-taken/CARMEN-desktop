import pytest

import plan_ml


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(plan_ml, "EXAMPLES_PATH", str(tmp_path / "plan_examples.json"))
    monkeypatch.setattr(plan_ml, "REPORTS_PATH", str(tmp_path / "plan_reports.jsonl"))
    monkeypatch.setattr(plan_ml, "_model", None)


@pytest.mark.parametrize("text,minutes", [
    ("write essay 45 min", 45), ("study 2h", 120), ("gym 1h30m", 90), ("read 1.5 hours", 90),
    ("deep work 1:30", 90), ("nothing about time", None), ("call at 5", None)])
def test_minutes_are_read_from_the_line(text, minutes):
    assert plan_ml.parse_minutes(text) == minutes


def test_the_time_is_taken_out_of_the_title():
    assert plan_ml.strip_duration("write essay 45 min") == "write essay"
    assert plan_ml.strip_duration("study (2h)") == "study"


def test_the_rules_alone_tell_the_three_kinds_apart():
    assert plan_ml.predict("write essay 45 min")["label"] == "task"
    assert plan_ml.predict("is the library open?")["label"] == "note"
    assert plan_ml.predict("someday project idea")["label"] == "board"


def test_it_asks_before_it_has_learned_anything():
    assert plan_ml.predict("write essay 45 min")["auto"] is False


def test_it_learns_the_persons_own_labels():
    for line in ("portfolio website", "language app", "home lab", "novel outline", "chess trainer", "garden redesign"):
        plan_ml.teach(line, "board")
    for line in ("write essay 45 min", "study chem 1h", "draft slides 20 min", "read ch3 30 min",
                 "revise maths 40 min", "code review 25 min", "practice piano 30 min", "solve set 50 min"):
        plan_ml.teach(line, "task")
    for line in ("buy milk", "call mom", "is it due friday?", "email prof", "what time is lunch?"):
        plan_ml.teach(line, "note")
    assert plan_ml.example_count() >= plan_ml.AUTO_MIN_EXAMPLES
    guess = plan_ml.predict("revise biology 35 min")
    assert guess["label"] == "task" and guess["minutes"] == 35 and guess["minutesSource"] == "typed"
    assert plan_ml.predict("knitting app")["label"] == "board"


def test_a_correction_takes_effect_at_once_and_replaces_the_mistake():
    assert plan_ml.predict("fix bike")["label"] != "note"
    plan_ml.report("fix bike", "task", "note", "this is only a reminder")
    guess = plan_ml.predict("fix bike")
    assert guess["label"] == "note" and guess["auto"] is True
    assert len(plan_ml._load_examples()) == 1                     # replaced, not stacked next to the old lesson


def test_a_report_is_kept_for_later_improvement():
    plan_ml.report("water plants", "task", "note", "too long to be a task")
    saved = plan_ml.reports()
    assert saved == [{"line": "water plants", "predicted": "task", "wanted": "note",
                      "note": "too long to be a task", "examples": 0}]


def test_minutes_for_a_task_come_from_what_the_person_gave_similar_lines():
    for minutes in (20, 25, 20):
        plan_ml.teach("write note %d" % minutes, "task", minutes=minutes)
    guess = plan_ml.predict("write report")
    assert guess["label"] == "task"
    assert 15 <= guess["minutes"] <= 30 and "write" in guess["minutesSource"]


def test_one_odd_answer_cannot_swing_the_estimate():
    plan_ml.teach("study a", "task", minutes=30)
    plan_ml.teach("study b", "task", minutes=300)
    assert plan_ml.predict("study c")["minutes"] < 200


def test_tags_start_from_keywords_and_a_quick_one_is_marked_quick():
    tags = plan_ml.predict("study chapter 3 10 min")["tags"]
    assert "study" in tags and "quick" in tags


def test_corrupt_files_do_not_break_it(tmp_path):
    (tmp_path / "plan_examples.json").write_text("{not json")
    assert plan_ml.predict("anything")["label"] in plan_ml.CLASSES
