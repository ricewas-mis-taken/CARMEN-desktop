"""Lays a day's planned tasks out as a flow: one after another from now, with a short breather between, around any
that were pinned to a clock time, up to the usual stop. Plain data, no Qt, so it can be tested.

The Today page draws what this returns. Times are minutes after midnight."""
BREATHER = 10               # minutes left between two blocks
SNAP = 5                    # blocks start on multiples of this
DAY_START = 9 * 60          # a day planned ahead starts here when nothing is pinned earlier
MIN_BLOCK = 10


def _snap_up(minutes):
    return int(-(-minutes // SNAP) * SNAP)


def flow(items, now_minutes, stop_minutes, planning_ahead=False):
    """Blocks for the tasks in `items` (dicts with id, minutes, done, start), in the order given.

    Returns {"blocks": [{"item", "start", "end", "pinned"}], "overflow": minutes past the stop (0 if it fits),
    "slack": minutes free before the stop}. Done tasks keep the place they took (they are drawn muted at the top
    of the day); undone ones flow from `now_minutes` (or DAY_START when planning ahead)."""
    tasks = [i for i in items if i.get("kind", "task") == "task" and i.get("minutes")]
    cursor = _snap_up(DAY_START if planning_ahead else max(now_minutes, 0))
    pinned = sorted((t for t in tasks if t.get("start") is not None and not t.get("done")), key=lambda t: t["start"])
    blocks = []
    done_blocks = []
    clock = DAY_START if planning_ahead else 0
    for task in tasks:
        if task.get("done"):
            minutes = max(MIN_BLOCK, int(task["minutes"]))
            start = task.get("start")
            if start is None:
                start = clock
            done_blocks.append({"item": task, "start": start, "end": start + minutes, "pinned": task.get("start") is not None})
            clock = start + minutes
    free = [t for t in tasks if not t.get("done") and t.get("start") is None]
    queue = list(free)
    for pin in pinned:
        start = max(int(pin["start"]), 0)
        # what fits before this pinned block goes first, in the order given
        while queue:
            nxt = queue[0]
            need = max(MIN_BLOCK, int(nxt["minutes"]))
            if _snap_up(cursor) + need + BREATHER <= start:
                begin = _snap_up(cursor)
                blocks.append({"item": nxt, "start": begin, "end": begin + need, "pinned": False})
                cursor = begin + need + BREATHER
                queue.pop(0)
            else:
                break
        minutes = max(MIN_BLOCK, int(pin["minutes"]))
        blocks.append({"item": pin, "start": start, "end": start + minutes, "pinned": True})
        cursor = max(cursor, start + minutes + BREATHER)
    for task in queue:
        need = max(MIN_BLOCK, int(task["minutes"]))
        begin = _snap_up(cursor)
        blocks.append({"item": task, "start": begin, "end": begin + need, "pinned": False})
        cursor = begin + need + BREATHER
    blocks = done_blocks + sorted(blocks, key=lambda b: b["start"])
    last = max((b["end"] for b in blocks if not b["item"].get("done")), default=None)
    overflow = max(0, last - stop_minutes) if last is not None else 0
    slack = max(0, stop_minutes - last) if last is not None else max(0, stop_minutes - cursor)
    return {"blocks": blocks, "overflow": overflow, "slack": slack}


def clock_text(minutes):
    """"9:05 am" style text for minutes after midnight (past 24 h wraps to the next day)."""
    minutes = int(minutes) % (24 * 60)
    hour, minute = divmod(minutes, 60)
    suffix = "am" if hour < 12 else "pm"
    return f"{hour % 12 or 12}:{minute:02d} {suffix}"


def length_text(minutes):
    minutes = int(minutes)
    if minutes < 60:
        return f"{minutes} min"
    hours, rest = divmod(minutes, 60)
    return f"{hours} h" if not rest else f"{hours} h {rest} min"
