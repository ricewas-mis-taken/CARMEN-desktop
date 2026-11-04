from datetime import date, datetime, timedelta

import pause_budget
import report_card
import score_store

MON = date(2026, 11, 2)


def test_last_week_is_the_monday_before_this_one():
    assert report_card.last_week(date(2026, 11, 4)) == date(2026, 10, 26)
    assert report_card.last_week(date(2026, 11, 2)) == date(2026, 10, 26)


def test_the_card_shows_how_the_score_moved_and_where_points_went():
    score_store.add_event("a", date(2026, 10, 30), "goals", 10)            # the week before: not in this card
    score_store.add_event("b", MON, "goals", 12)
    score_store.add_event("c", MON + timedelta(days=1), "overdue", -3)
    score_store.add_event("d", MON + timedelta(days=2), "overdue", -3)
    score_store.add_event("e", MON + timedelta(days=3), "review", 6)
    card = report_card.build(MON)
    assert card["before"] == 510 and card["after"] == 522 and card["change"] == 12
    assert {r["kind"]: r["points"] for r in card["rows"]} == {"goals": 12, "overdue": -6, "review": 6}
    assert card["reviews_done"] == 1 and card["overdue_days"] == 2
    assert "Reviews slipped" in card["suggestion"] and card["grade"] == "C"


def test_an_empty_week_is_flagged_and_changes_nothing():
    card = report_card.build(MON)
    assert card["empty"] and card["change"] == 0 and card["after"] == 500


def test_a_clean_week_says_to_keep_going():
    score_store.add_event("b", MON, "goals", 12)
    assert "Keep the same plan" in report_card.build(MON)["suggestion"]


def test_the_pause_breakdown_counts_only_sessions_that_week():
    t0 = datetime(2026, 11, 3, 9, 0)
    log = [{"kind": "pause", "timestamp": (t0 + timedelta(minutes=10)).isoformat(), "pauseReason": "break"},
           {"kind": "resume", "timestamp": (t0 + timedelta(minutes=20)).isoformat()},
           {"kind": "pause", "timestamp": (t0 + timedelta(minutes=30)).isoformat(), "pauseReason": "checking"},
           {"kind": "resume", "timestamp": (t0 + timedelta(minutes=35)).isoformat()}]
    inside = {"startTime": t0.isoformat(), "endTime": (t0 + timedelta(hours=1)).isoformat(), "violationLog": log}
    outside = {**inside, "startTime": datetime(2026, 10, 1, 9).isoformat(), "endTime": datetime(2026, 10, 1, 10).isoformat()}
    pause = report_card.build(MON, sessions=[inside, outside])["pause"]
    assert pause["free_seconds"] == 600 and pause["escape_seconds"] == 300


def test_the_dialog_builds_and_plan_goes_to_the_week_page(qtbot):
    from qt_ui import report_card_dialog
    score_store.add_event("b", MON, "goals", 12)
    went = []
    dialog = report_card_dialog.show(report_card.build(MON), on_plan=lambda: went.append(1))
    qtbot.addWidget(dialog)
    dialog._plan()
    assert went == [1]
