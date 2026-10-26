"""Fast ways into Review: log a miss, add a pasted list, score a set.

All of it sits on top of review_store's ordinary writes (no schema change), so
nothing here needs new sync fields: a miss is a problem in a "Mistakes"
subject, a batch is many problems, and a set is a problem whose score is
turned into the same outcome a timed review gives.
"""
import re

import review_store

MISTAKES_SUBJECT = "Mistakes"
MAX_BATCH = 200

_SUBJECT_COLORS = ["#D14B3F", "#C9892B", "#4F7CAC", "#5B8C5A", "#8A63A8", "#3E8E8B", "#A5675A", "#6B7A8F"]


def _mistakes_subject(topic_id):
    for subject in review_store.list_subjects(topic_id):
        if subject["name"].lower() == MISTAKES_SUBJECT.lower():
            return subject
    used = {s["color"].lower() for s in review_store.list_subjects(topic_id)}
    for color in _SUBJECT_COLORS:
        if color.lower() in used:
            continue
        try:
            subject = review_store.create_subject(topic_id, MISTAKES_SUBJECT, color)
        except review_store.DuplicateColorError:
            continue
        if subject:
            return subject
    return None


def parse_miss(text):
    """'Ch7 #14' -> ('Ch7 #14', 3); a trailing '!' or '!!' means harder (3 -> 4 -> 5 stars)."""
    text = (text or "").strip()
    bangs = len(text) - len(text.rstrip("!"))
    name = text.rstrip("!").strip()
    return name, min(5, 3 + min(bangs, 2))


def log_miss(topic_id, text, subject_id=None, stars=None):
    """Adds the miss as a problem that comes back for review soon. Logging the same
    name again raises its stars one step. `subject_id` is the category to file it
    under (the "Mistakes" category when None); `stars` is how hard it was (3, or
    what trailing !s say, when None). Returns {'problem': ..., 'repeat': bool}
    or None for empty text."""
    name, bang_stars = parse_miss(text)
    if not name:
        return None
    stars = stars or bang_stars
    if subject_id is None:
        subject = _mistakes_subject(topic_id)
    else:
        subject = next((x for x in review_store.list_subjects(topic_id) if x["id"] == subject_id), None)
    if subject is None:
        return None
    for existing in review_store.list_problems(topic_id, due_only=False):
        if existing["subjectId"] == subject["id"] and existing["name"].lower() == name.lower():
            bumped = min(5, max(existing["stars"], stars) + 1)
            problem = review_store.update_problem(
                existing["id"], subject["id"], existing["name"], bumped,
                existing["descriptionType"], existing["descriptionText"], existing["descriptionLink"],
            )
            return {"problem": problem, "repeat": True}
    problem = review_store.create_problem(topic_id, subject["id"], name, stars, "text", description_text="")
    return {"problem": problem, "repeat": False} if problem else None


# --- batch add ------------------------------------------------------------------------------

_RANGE = re.compile(r"^(?P<prefix>.*?)(?P<a>\d+)\s*(?:-|–|to)\s*(?P<b>\d+)(?P<suffix>\D*)$", re.IGNORECASE)


def parse_batch(text):
    """Pasted text -> list of names. One per line; a comma or tab splits a
    line (the first cell is the name, so a spreadsheet column pastes cleanly);
    'Ch 7 problems 1-30' becomes 30 names. Blank lines and repeats drop out.
    Capped at MAX_BATCH so a stray paste can't flood the list."""
    names, seen = [], set()

    def add(name):
        name = name.strip()
        if name and name.lower() not in seen and len(names) < MAX_BATCH:
            seen.add(name.lower())
            names.append(name)

    for line in (text or "").splitlines():
        cell = re.split(r"[\t,]", line, maxsplit=1)[0].strip()
        match = _RANGE.match(cell)
        if match and int(match["b"]) >= int(match["a"]) and int(match["b"]) - int(match["a"]) < MAX_BATCH:
            prefix, suffix = match["prefix"].strip(), match["suffix"].strip()
            for n in range(int(match["a"]), int(match["b"]) + 1):
                add(" ".join(part for part in (prefix, str(n), suffix) if part))
        else:
            add(cell)
    return names


def add_batch(topic_id, subject_id, names, stars=3):
    """Creates each name as a problem. Names already in the subject are
    skipped, not failed. Returns (created_count, skipped_count)."""
    created = skipped = 0
    for name in names:
        try:
            problem = review_store.create_problem(topic_id, subject_id, name, stars, "text", description_text="")
        except review_store.DuplicateNameError:
            skipped += 1
            continue
        if problem:
            created += 1
        else:
            skipped += 1
    return created, skipped


# --- sets with a score ----------------------------------------------------------------------

def outcome_for_score(correct, total):
    """(self_solved, shakiness) for a set score: 90%+ is solid, 60-89% shaky,
    under 60% counts as having needed the answers."""
    if total <= 0:
        return None
    ratio = correct / total
    if ratio >= 0.9:
        return True, 4
    if ratio >= 0.6:
        return True, 2
    return False, None


def score_set(problem_id, correct, total, duration_seconds=0):
    """Feeds a set's score into the normal scheduler. Returns the updated
    problem, or None for an impossible score."""
    if not isinstance(correct, int) or not isinstance(total, int) or not (0 <= correct <= total) or total < 1:
        return None
    self_solved, shakiness = outcome_for_score(correct, total)
    review_store._apply_review_outcome(problem_id, max(0, int(duration_seconds)), self_solved, shakiness)
    return review_store.get_problem(problem_id)
