"""The nightly plan: what you said you would do tomorrow, filed where it belongs, and how it went.

A plan line becomes one of three things (see plan_ml):
  - a task: stays here as a timed block on that day's Today page (it has minutes and can be started as a session)
  - a board item: a card on The Board, tagged and marked as planned for that day
  - a quick note: a line in the QuickNote, remembered as planned for that day

Plans are per computer (private/plans.json). The board cards and the QuickNote lines they create sync like any
other, so on another computer the lines appear but the day's timeline does not. See DESIGN_DECISIONS.txt,
[2026-11-24]. Scoring is in score_day(): called once per finished day by score_store.settle()."""
import json
import os
import threading
import uuid
from datetime import date, datetime, timedelta

import paths
import plan_ml

PATH = paths.private_path("plans.json")
NOTE_META_PATH = paths.private_path("plan_notes.json")
MAX_DAYS = 120

PLAN_MADE_POINTS = 4           # for sitting down and planning at all
MIN_ITEMS_FOR_SCORE = 2        # a plan of one line is not judged
MAX_COUNTED_ITEMS = 8          # more lines than this earn nothing extra, so padding a plan cannot farm points
MIN_COUNTED_MINUTES = 10       # a task shorter than this is not counted either
SWING = 7                      # a day's completion score runs from -SWING (none done) to +SWING (all done)

_lock = threading.RLock()


def _read():
    try:
        with open(PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return {"days": {}}
    if not isinstance(data, dict) or not isinstance(data.get("days"), dict):
        return {"days": {}}
    return data


def _write(data):
    os.makedirs(os.path.dirname(PATH), exist_ok=True)
    days = dict(sorted(data["days"].items())[-MAX_DAYS:])
    tmp = PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump({"days": days}, f, separators=(",", ":"))
    os.replace(tmp, PATH)


def _key(day):
    return day.isoformat() if hasattr(day, "isoformat") else str(day)


def load_day(day):
    """{"items": [...], "madeAt": iso or None} for a day; always a dict."""
    with _lock:
        entry = _read()["days"].get(_key(day))
    if not isinstance(entry, dict):
        return {"items": [], "madeAt": None}
    items = [i for i in entry.get("items", []) if isinstance(i, dict) and i.get("id")]
    return {"items": items, "madeAt": entry.get("madeAt")}


def has_plan(day):
    return bool(load_day(day)["items"])


def _store_day(day, items, made_at=None):
    with _lock:
        data = _read()
        entry = data["days"].get(_key(day)) or {}
        entry["items"] = items
        entry["madeAt"] = made_at or entry.get("madeAt") or datetime.now().isoformat()
        data["days"][_key(day)] = entry
        _write(data)


def save_plan(day, entries):
    """Files a plan for `day`. `entries`: [{"text", "kind", "tags", "minutes", "predicted"}] as the person
    confirmed them. Every line also teaches plan_ml, and one the person changed teaches it harder.
    Returns the items now in the plan (replacing any earlier plan for that day)."""
    items = []
    seen = set()
    for entry in entries or []:
        text = (entry.get("text") or "").strip()
        kind = entry.get("kind")
        if not text or kind not in plan_ml.CLASSES or plan_ml.normalize(text) in seen:
            continue
        seen.add(plan_ml.normalize(text))
        tags = [t for t in (entry.get("tags") or []) if isinstance(t, str)][:4]
        minutes = entry.get("minutes") if kind == "task" else None
        title = plan_ml.strip_duration(text) if plan_ml.parse_minutes(text) else text
        item = {"id": uuid.uuid4().hex, "text": title, "kind": kind, "tags": tags,
                "minutes": int(minutes) if minutes else None, "done": False, "doneAt": None,
                "ref": None, "start": None}
        predicted = entry.get("predicted") or {}
        changed = bool(predicted) and (predicted.get("label") != kind or sorted(predicted.get("tags") or []) != sorted(tags))
        plan_ml.teach(text, kind, tags=tags, minutes=item["minutes"], corrected=changed)
        item["ref"] = _file_somewhere(item, day)
        items.append(item)
    if not items:
        return []
    _store_day(day, items)
    return items


def _file_somewhere(item, day):
    """Puts a board item on the board and a note in the QuickNote; returns what to look it up by later."""
    import board_store
    import quicknote_store
    if item["kind"] == "board":
        existing = next((t for t in board_store.load_board()
                         if plan_ml.normalize(t.get("name")) == plan_ml.normalize(item["text"]) and not t.get("finished")), None)
        if existing is not None:
            return existing["id"]
        tags = [t for t in item["tags"] if t in board_store.PRESET_TAGS_BY_ID]
        return board_store.create_task(item["text"], 5, tags=tags, planned_for=_key(day))["id"]
    if item["kind"] == "note":
        lines = [n for n in quicknote_store.load()[0] if n.strip()]
        if item["text"] not in lines:
            quicknote_store.save(lines + [item["text"]])
        _remember_note(item["text"], day, item["tags"])
        return item["text"]
    return None


def _read_notes():
    try:
        with open(NOTE_META_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _remember_note(text, day, tags):
    with _lock:
        notes = _read_notes()
        notes[plan_ml.normalize(text)] = {"day": _key(day), "tags": list(tags)}
        keep = dict(list(notes.items())[-500:])
        os.makedirs(os.path.dirname(NOTE_META_PATH), exist_ok=True)
        tmp = NOTE_META_PATH + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(keep, f, separators=(",", ":"))
        os.replace(tmp, NOTE_META_PATH)


def note_meta(text):
    """{"day", "tags"} when this QuickNote line came from a plan, else None."""
    return _read_notes().get(plan_ml.normalize(text))


def set_done(day, item_id, done=True):
    """Ticks an item off (or back on). A board item finishes its card too; a ticked note leaves the QuickNote."""
    with _lock:
        loaded = load_day(day)
        target = next((i for i in loaded["items"] if i["id"] == item_id), None)
        if target is None:
            return False
        target["done"] = bool(done)
        target["doneAt"] = datetime.now().isoformat() if done else None
        _store_day(day, loaded["items"], loaded["madeAt"])
    if done:
        _close_out(target)
    return True


def _close_out(item):
    if item["kind"] == "board" and item.get("ref"):
        import board_store
        task = board_store.get_task(item["ref"])
        if task is not None and not task.get("finished"):
            board_store.finish_task(item["ref"])
    elif item["kind"] == "note" and item.get("ref"):
        import quicknote_store
        quicknote_store.save([n for n in quicknote_store.load()[0] if n.strip() and n != item["ref"]])


def set_start(day, item_id, minutes_after_midnight):
    """Pins a task to a clock time on the Today page (None to let it flow again)."""
    with _lock:
        loaded = load_day(day)
        target = next((i for i in loaded["items"] if i["id"] == item_id), None)
        if target is None:
            return False
        target["start"] = None if minutes_after_midnight is None else int(minutes_after_midnight)
        _store_day(day, loaded["items"], loaded["madeAt"])
    return True


def set_minutes(day, item_id, minutes):
    with _lock:
        loaded = load_day(day)
        target = next((i for i in loaded["items"] if i["id"] == item_id), None)
        if target is None or target["kind"] != "task":
            return False
        target["minutes"] = max(plan_ml.MIN_MINUTES, min(plan_ml.MAX_MINUTES, int(minutes)))
        _store_day(day, loaded["items"], loaded["madeAt"])
    plan_ml.teach(target["text"], "task", tags=target["tags"], minutes=target["minutes"])
    return True


def unfinished(day):
    """What was planned for `day` and not done, to offer when planning the next one."""
    return [i for i in load_day(day)["items"] if not i["done"]]


def counted(items):
    """The items a day is judged on: no more than MAX_COUNTED_ITEMS, and no tiny tasks."""
    real = [i for i in items if i["kind"] != "task" or (i.get("minutes") or 0) >= MIN_COUNTED_MINUTES]
    return real[:MAX_COUNTED_ITEMS]


def progress(day):
    """(done, counted) for the day's plan, as the Today page and the score both read it."""
    items = counted(load_day(day)["items"])
    return sum(1 for i in items if i["done"]), len(items)


def score_day(day, data):
    """Adds the day's two plan events to the score ledger `data` (see score_store.add_event): points for having
    planned, and a swing from -SWING to +SWING for how much of it was done. Nothing is judged for a plan with
    fewer than MIN_ITEMS_FOR_SCORE lines. Safe to run twice (the keys make the second a no-op)."""
    import score_store
    done, total = progress(day)
    if total == 0:
        return 0
    before = len(data["events"])
    score_store.add_event(f"plan:{_key(day)}", day, "plan", PLAN_MADE_POINTS,
                          f"Planned {total} thing{'s' if total != 1 else ''}", _data=data)
    if total >= MIN_ITEMS_FOR_SCORE:
        ratio = done / total
        points = round((ratio - 0.5) * 2 * SWING)
        if points:
            score_store.add_event(f"plandone:{_key(day)}", day, "plan", points,
                                  f"{done} of {total} planned things done", _data=data)
    return len(data["events"]) - before
