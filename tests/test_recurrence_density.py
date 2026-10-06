import time
from datetime import datetime

import calendar_recurrence as recurrence


def _event(rrule):
    return {"id": "x", "title": "t", "start": "2026-01-01T09:00:00",
            "end": "2026-01-01T09:30:00", "rrule": rrule}


def test_secondly_rule_is_rejected_quickly():
    start = time.time()
    occ = recurrence.next_occurrences([_event("FREQ=SECONDLY")], datetime(2026, 10, 4, 12, 0))
    assert occ == []
    assert time.time() - start < 2


def test_dense_byhour_byminute_rule_is_rejected():
    rule = "FREQ=DAILY;BYHOUR=" + ",".join(map(str, range(24))) + ";BYMINUTE=0,15,30,45"
    assert recurrence.expand_occurrences(_event(rule), datetime(2026, 10, 4), datetime(2026, 10, 5)) == []


def test_normal_weekly_rule_still_expands():
    occ = recurrence.expand_occurrences(
        _event("FREQ=WEEKLY;INTERVAL=1;BYDAY=MO,WE,FR"), datetime(2026, 10, 4), datetime(2026, 10, 11))
    assert len(occ) == 3
