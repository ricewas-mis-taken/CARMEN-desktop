from datetime import timedelta

import review_scheduler as sched


def test_one_star_at_final_stage_gets_90_day_gap():
    assert sched.compute_next_interval(5, 1) == 90
    assert sched.compute_next_interval(99, 1) == 90  # clamped to final stage


def test_five_star_at_stage_zero_rounds_up_to_one_day_not_zero():
    assert sched.compute_next_interval(0, 5) == 1


def test_schedule_stage_never_exceeds_final_index():
    stage = 0
    for _ in range(20):
        result = sched.schedule_after_review(stage, stars=3)
        stage = result["schedule_stage"]
        assert stage <= len(sched.BASE_INTERVALS_DAYS) - 1
    assert stage == len(sched.BASE_INTERVALS_DAYS) - 1


def test_schedule_new_problem_sets_stage_zero_and_next_review_date():
    result = sched.schedule_new_problem(stars=3)
    assert result["schedule_stage"] == 0
    expected_interval = sched.compute_next_interval(0, 3)
    assert result["next_review_date"] == sched.today() + timedelta(days=expected_interval)


def test_schedule_after_review_advances_stage_by_one():
    result = sched.schedule_after_review(schedule_stage=2, stars=3)
    assert result["schedule_stage"] == 3


def test_schedule_checked_answer_resets_stage_to_zero_regardless_of_prior_stage():
    """Checking the answer means it wasn't actually recalled -- a stage the
    schedule had "earned" through past solves must not survive that, or a
    problem that climbed to a 21+ day interval would jump right back into
    it after one clean re-solve, as if the lapse never happened."""
    result = sched.schedule_checked_answer(schedule_stage=3, stars=3)
    assert result["schedule_stage"] == 0


def test_schedule_checked_answer_next_review_is_stage_zeros_own_interval():
    for stars in (1, 2, 3, 4, 5):
        result = sched.schedule_checked_answer(schedule_stage=4, stars=stars)
        assert result["next_review_date"] == sched.today() + timedelta(days=1)
