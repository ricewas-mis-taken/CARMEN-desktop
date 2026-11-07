"""How vacation minutes are earned: the rules, with no state. tasks_store applies them to a task's history.

A day's goal is the target. Time worked past it is banked as vacation, but not one for one:

  The ladder (width of each step is a share of that day's target; the rate is vacation earned per extra minute):
      first 25% past the target        1.0
      next 25%                         0.5
      next 50%                         1/3        (3 minutes of work for 1 of vacation)
      next 100%                        0.2
      beyond twice the target          nothing

  Then three small adjustments, all about how that day went:
      clean sessions   minutes in a session with no violations count fully; in one with violations, half
                       (the day's share of clean minutes scales it between half and full)
      hard lock        minutes under a hard lock earn 25% more (scaled by the day's share of hard-lock minutes)
      streak           each earlier scheduled day in a row that met its target adds 10%, at most 50%

  And a weekly shape: once a week has banked a day's target of vacation, the rest of that week earns half.

Cashing in is untouched: a vacation minute still covers a minute of a day's goal."""

TIERS = ((0.25, 1.0), (0.25, 0.5), (0.5, 1 / 3), (1.0, 0.2))
CLEAN_FLOOR = 0.5            # a day with no clean minutes earns half
HARD_BONUS = 0.25            # a day entirely under a hard lock earns a quarter more
STREAK_STEP = 0.10
STREAK_MAX = 0.50
WEEK_OVER_RATE = 0.5         # earning once the week has banked a day's target


def ladder(over_minutes, target_minutes):
    """Vacation minutes for `over_minutes` worked past a `target_minutes` goal, by the ladder alone."""
    if over_minutes <= 0 or target_minutes <= 0:
        return 0.0
    earned, left = 0.0, over_minutes
    for width, rate in TIERS:
        step = min(left, width * target_minutes)
        earned += step * rate
        left -= step
        if left <= 0:
            break
    return earned


def most_a_day(target_minutes):
    """The most one day can earn from the ladder: a day at twice the target."""
    return ladder(2 * target_minutes, target_minutes)


def quality_factor(clean_share):
    return CLEAN_FLOOR + (1 - CLEAN_FLOOR) * max(0.0, min(1.0, clean_share))


def hard_factor(hard_share):
    return 1 + HARD_BONUS * max(0.0, min(1.0, hard_share))


def streak_factor(days_in_a_row):
    return 1 + min(STREAK_MAX, STREAK_STEP * max(0, days_in_a_row))


def week_shaped(banked_so_far, earned_today, day_target):
    """`earned_today` after the weekly shape: full rate until the week has banked a day's target, half after."""
    if day_target <= 0:
        return earned_today
    room = max(0.0, day_target - banked_so_far)
    full = min(earned_today, room)
    return full + (earned_today - full) * WEEK_OVER_RATE
