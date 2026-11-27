import pytest

import board_store


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(board_store, "BOARD_PATH", str(tmp_path / "board.json"))


def _suggest(name):
    return ["study"] if "exam" in name else []


def test_untagged_cards_get_a_guess_once():
    a = board_store.create_task("exam prep", 5)
    b = board_store.create_task("random thing", 5)
    assert board_store.auto_tag_untagged(_suggest) == 1
    assert board_store.get_task(a["id"])["tags"] == ["study"]
    assert board_store.get_task(b["id"])["tags"] == []
    assert board_store.auto_tag_untagged(lambda name: ["work"]) == 0        # already looked at, never retried


def test_a_tag_the_person_cleared_is_not_put_back():
    a = board_store.create_task("exam prep", 5)
    board_store.auto_tag_untagged(_suggest)
    board_store.update_task(a["id"], "exam prep", tags=[])
    assert board_store.auto_tag_untagged(_suggest) == 0 and board_store.get_task(a["id"])["tags"] == []


def test_cards_that_already_have_tags_are_left_alone():
    a = board_store.create_task("exam prep", 5, tags=["work"])
    assert board_store.auto_tag_untagged(_suggest) == 0 and board_store.get_task(a["id"])["tags"] == ["work"]


def test_a_card_from_before_the_flag_is_not_judged_again_if_it_had_tags():
    task = board_store.create_task("old", 5, tags=["quick"])
    tasks = board_store.load_board(include_deleted=True)
    for t in tasks:
        t.pop("autoTagged", None)
    board_store.save_board(tasks)
    assert board_store.get_task(task["id"])["autoTagged"] is True
