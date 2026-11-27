"""Reads one line of a nightly plan and guesses what it is: a task (timed work), a board item (something to
keep track of) or a quick note (a throwaway reminder or question), plus tags and minutes.

Small on purpose. A multinomial naive Bayes over word, bigram and character features, started from hand-written
rules that fade as the person's own corrections come in (prior weight k / (k + n)). Everything the model knows is
rebuilt from one list of labelled lines (private/plan_examples.json), so a correction is one edit to that list, the
file is easy to sync and to export, and there is no hidden state to go stale.

Nothing here leaves the computer. See DESIGN_DECISIONS.txt, [2026-11-24]."""
import json
import math
import os
import re
import threading
from collections import defaultdict

import paths
import server_clock

EXAMPLES_PATH = paths.private_path("plan_examples.json")
REPORTS_PATH = paths.private_path("plan_reports.jsonl")
MAX_EXAMPLES = 3000
CLASSES = ("task", "board", "note")
LABEL_NAMES = {"task": "Task", "board": "Board", "note": "Quick note"}

AUTO_MIN_PROB = 0.80        # below this the guess is shown for a one-tap confirm
AUTO_MIN_MARGIN = 0.35      # and so is a guess that is close to the runner-up
AUTO_MIN_EXAMPLES = 15      # until it has seen this many of your lines it always asks
PRIOR_K = 10.0
CORRECTION_WEIGHT = 2.0     # a correction counts for more than a line you simply accepted

_WORD = re.compile(r"[a-z0-9']+")
_DUR = re.compile(
    r"(?<![\w:])(?:(\d+(?:\.\d+)?)\s*(?:h|hr|hrs|hour|hours)\s*(?:(\d+)\s*(?:m|min|mins|minutes)?)?"
    r"|(\d+(?:\.\d+)?)\s*(?:m|min|mins|minute|minutes)|(\d+):(\d{2}))(?!\w)", re.I)
TASK_VERBS = {"write", "finish", "study", "read", "review", "draft", "practice", "code", "fix", "clean", "plan",
              "build", "edit", "revise", "solve", "do", "prepare", "implement", "work", "complete", "learn",
              "workout", "run", "submit", "organize", "organise", "research"}
NOTE_VERBS = {"remember", "remind", "ask", "call", "buy", "email", "text", "check", "pick", "pay", "book"}
BOARD_WORDS = {"idea", "project", "app", "explore", "someday", "maybe", "long", "term", "goal"}

# Tags: the keywords that start a tag off, before the person has taught it anything.
TAG_KEYWORDS = {
    "study": {"study", "read", "revise", "review", "homework", "lecture", "exam", "learn", "notes", "chapter"},
    "work": {"report", "email", "meeting", "client", "slides", "presentation", "invoice", "code", "deploy"},
    "health": {"gym", "run", "workout", "walk", "stretch", "sleep", "doctor", "meditate"},
    "home": {"clean", "laundry", "dishes", "cook", "groceries", "toilet", "tidy"},
    "shopping": {"buy", "order", "groceries", "shopping", "pick"},
    "creative": {"write", "draw", "design", "paint", "music", "novel", "essay"},
    "admin": {"pay", "bill", "form", "tax", "book", "renew", "call", "appointment"},
    "quick": set(),
    "long-term": {"project", "goal", "someday", "build", "launch"},
}
QUICK_MINUTES = 15
DEFAULT_MINUTES = 30
MIN_MINUTES, MAX_MINUTES = 5, 480

_lock = threading.Lock()
_change_listeners = []
_model = None          # (signature, _Model): rebuilt only when the examples change


def add_change_listener(listener):
    _change_listeners.append(listener)


def _changed():
    for listener in list(_change_listeners):
        try:
            listener()
        except Exception:
            pass


def normalize(line):
    return " ".join((line or "").lower().split())


def parse_minutes(text):
    """The minutes a line says it will take ("2h", "45 min", "1h30m", "1.5 hours", "1:30"), else None."""
    match = _DUR.search(text or "")
    if not match:
        return None
    hours, extra, minutes, hh, mm = match.groups()
    if hours is not None:
        total = float(hours) * 60 + (int(extra) if extra else 0)
    elif minutes is not None:
        total = float(minutes)
    else:
        total = int(hh) * 60 + int(mm)
    return int(round(total)) if total > 0 else None


def strip_duration(text):
    """The line without the time it states ("write essay 45 min" -> "write essay")."""
    cleaned = _DUR.sub("", text or "")
    cleaned = re.sub(r"\s*[-,(]\s*$", "", cleaned.strip())
    return " ".join(cleaned.replace("()", "").split()) or (text or "").strip()


def features(line):
    s = (line or "").strip().lower()
    toks = _WORD.findall(s)
    f = defaultdict(float)
    for i, tok in enumerate(toks):
        f["w:" + ("<num>" if tok.isdigit() else tok)] += 1.0
        if i + 1 < len(toks):
            f["b:%s_%s" % (tok, toks[i + 1])] += 1.0
    if toks:
        f["first:" + toks[0]] += 1.0
    for tok in toks:
        padded = "^" + tok + "$"
        for i in range(len(padded) - 2):
            f["c:" + padded[i:i + 3]] += 0.3
    if s.endswith("?"):
        f["f:question"] += 1.0
    if _DUR.search(s):
        f["f:duration"] += 1.0
    f["f:len%d" % min(len(toks), 8)] += 1.0
    return f


def rule_prior(line):
    """What the hand-written rules think before anything has been learned: {class: probability}."""
    s = (line or "").strip().lower()
    toks = _WORD.findall(s)
    score = {c: 1.0 for c in CLASSES}
    if s.endswith("?"):
        score["note"] += 3
    if _DUR.search(s):
        score["task"] += 3
    if toks and toks[0] in TASK_VERBS:
        score["task"] += 1.5
    if toks and toks[0] in NOTE_VERBS:
        score["note"] += 2
    if any(t in BOARD_WORDS for t in toks):
        score["board"] += 2
    if len(toks) <= 3 and not _DUR.search(s) and not (toks and toks[0] in NOTE_VERBS | TASK_VERBS):
        score["board"] += 1
    total = sum(score.values())
    return {c: v / total for c, v in score.items()}


class _Model:
    def __init__(self, alpha=0.5):
        self.alpha = alpha
        self.docs = {c: 0.0 for c in CLASSES}
        self.counts = {c: defaultdict(float) for c in CLASSES}
        self.totals = {c: 0.0 for c in CLASSES}
        self.vocab = set()
        self.tag_word = defaultdict(lambda: defaultdict(float))     # tag -> word -> times seen with the tag
        self.word_seen = defaultdict(float)                         # word -> times seen at all
        self.minutes = defaultdict(list)                            # "tag:x" / "first:x" -> minutes the person gave

    @property
    def n(self):
        return int(round(sum(self.docs.values())))

    def learn(self, example):
        label, weight = example["label"], float(example.get("weight", 1.0))
        if label not in CLASSES:
            return
        line = example["line"]
        self.docs[label] += weight
        for key, value in features(line).items():
            self.counts[label][key] += value * weight
            self.totals[label] += value * weight
            self.vocab.add(key)
        words = set(_WORD.findall(line.lower()))
        for word in words:
            self.word_seen[word] += weight
        for tag in example.get("tags") or []:
            for word in words:
                self.tag_word[tag][word] += weight
        minutes = example.get("minutes")
        if minutes and label == "task":
            first = (_WORD.findall(line.lower()) or [""])[0]
            self.minutes["first:" + first].append(minutes)
            for tag in example.get("tags") or []:
                self.minutes["tag:" + tag].append(minutes)
            self.minutes["all"].append(minutes)

    def probabilities(self, line):
        feats = features(line)
        n = self.n
        vocab = max(len(self.vocab), 1)
        lam = PRIOR_K / (PRIOR_K + n)
        prior = rule_prior(line)
        length = max(sum(feats.values()), 1.0)
        raw = {}
        for c in CLASSES:
            log_p = math.log((self.docs[c] + 1) / (n + len(CLASSES)))
            denom = self.totals[c] + self.alpha * vocab
            for key, value in feats.items():
                log_p += value * math.log((self.counts[c].get(key, 0.0) + self.alpha) / denom)
            # Long lines would otherwise saturate; the rules count for less each time the person teaches it.
            raw[c] = log_p / math.sqrt(length) + lam * math.log(prior[c]) * 3
        top = max(raw.values())
        exp = {c: math.exp(v - top) for c, v in raw.items()}
        total = sum(exp.values())
        return {c: v / total for c, v in exp.items()}


def _load_examples():
    try:
        with open(EXAMPLES_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return []
    if not isinstance(data, list):
        return []
    return [e for e in data if isinstance(e, dict) and isinstance(e.get("line"), str)
            and e.get("label") in CLASSES]


def _save_examples(examples):
    os.makedirs(os.path.dirname(EXAMPLES_PATH), exist_ok=True)
    tmp = EXAMPLES_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(examples[-MAX_EXAMPLES:], f, separators=(",", ":"))
    os.replace(tmp, EXAMPLES_PATH)


def _signature():
    try:
        info = os.stat(EXAMPLES_PATH)
        return (info.st_mtime_ns, info.st_size)
    except OSError:
        return None


def _current_model():
    global _model
    signature = _signature()
    with _lock:
        if _model is not None and _model[0] == signature and _model[2] == EXAMPLES_PATH:
            return _model[1]
        model = _Model()
        for example in _load_examples():
            model.learn(example)
        _model = (signature, model, EXAMPLES_PATH)
        return model


def example_count():
    return _current_model().n


def teach(line, label, tags=None, minutes=None, corrected=False):
    """Records one labelled line. The same line taught again replaces its earlier lesson (the latest word is the
    person's), so a correction takes effect at once and is never counted next to the mistake."""
    text = (line or "").strip()
    if not text or label not in CLASSES:
        return False
    key = normalize(text)
    entry = {"line": text, "label": label, "tags": sorted(set(tags or [])),
             "minutes": int(minutes) if minutes else None,
             "weight": CORRECTION_WEIGHT if corrected else 1.0, "at": server_clock.next_stamp()}
    with _lock:
        examples = [e for e in _load_examples() if normalize(e["line"]) != key]
        examples.append(entry)
        _save_examples(examples)
    _changed()
    return True


def export_examples():
    """{"items": every lesson, "updatedAt": the newest lesson's stamp or None}, for sharing with other computers."""
    with _lock:
        items = _load_examples()
    stamps = [e["at"] for e in items if isinstance(e.get("at"), str) and server_clock.parse(e["at"])]
    newest = None
    for stamp in stamps:
        if newest is None or server_clock.is_newer(stamp, newest):
            newest = stamp
    return {"items": items, "updatedAt": newest}


def apply_remote_examples(items):
    """Merges lessons another computer taught: a line known to both keeps whichever lesson is newer, a line only
    the other computer knows is added. Nothing local is dropped. Returns True when anything changed here."""
    if not isinstance(items, list):
        return False
    with _lock:
        mine = {normalize(e["line"]): e for e in _load_examples()}
        changed = False
        for theirs in items:
            if not (isinstance(theirs, dict) and isinstance(theirs.get("line"), str)
                    and theirs.get("label") in CLASSES and server_clock.parse(theirs.get("at")) is not None):
                continue
            key = normalize(theirs["line"])
            if not key:
                continue
            ours = mine.get(key)
            if ours is None or server_clock.is_newer(theirs["at"], ours.get("at")):
                mine[key] = {"line": theirs["line"][:500], "label": theirs["label"],
                             "tags": [t for t in (theirs.get("tags") or []) if isinstance(t, str)][:6],
                             "minutes": theirs["minutes"] if isinstance(theirs.get("minutes"), int) else None,
                             "weight": CORRECTION_WEIGHT if theirs.get("weight") == CORRECTION_WEIGHT else 1.0,
                             "at": theirs["at"]}
                changed = True
        if changed:
            ordered = sorted(mine.values(), key=lambda e: e.get("at") or "")
            _save_examples(ordered)
    return changed


def predict(line):
    """The whole guess for a line: {"label", "probs", "confidence", "auto", "tags", "minutes", "minutesSource"}.
    `auto` is True only when it is sure enough to file the line without asking."""
    text = (line or "").strip()
    model = _current_model()
    exact = _exact_lesson(text)
    if exact is not None:
        probs = {c: (0.97 if c == exact["label"] else 0.015) for c in CLASSES}
        label = exact["label"]
        tags = list(exact.get("tags") or [])
        minutes, source = exact.get("minutes") or None, "you"
        auto = True
    else:
        probs = model.probabilities(text)
        ranked = sorted(probs.items(), key=lambda kv: -kv[1])
        label = ranked[0][0]
        auto = (ranked[0][1] >= AUTO_MIN_PROB and ranked[0][1] - ranked[1][1] >= AUTO_MIN_MARGIN
                and model.n >= AUTO_MIN_EXAMPLES)
        tags = predict_tags(text, model)
        minutes, source = None, None
    typed = parse_minutes(text)
    if typed:
        minutes, source = typed, "typed"
    elif label == "task" and not minutes:
        minutes, source = estimate_minutes(text, tags, model)
    elif label == "task" and "quick" in tags:
        minutes = minutes or QUICK_MINUTES
    return {"label": label, "probs": probs, "confidence": probs[label], "auto": auto, "tags": tags,
            "minutes": minutes if label == "task" else None, "minutesSource": source if label == "task" else None}


def _exact_lesson(text):
    key = normalize(strip_duration(text))
    full = normalize(text)
    for example in reversed(_load_examples()):
        mine = normalize(example["line"])
        if mine == full or normalize(strip_duration(example["line"])) == key:
            return example
    return None


def predict_tags(line, model=None):
    model = model or _current_model()
    words = set(_WORD.findall((line or "").lower()))
    found = []
    for tag, keywords in TAG_KEYWORDS.items():
        if words & keywords:
            found.append(tag)
    for tag, seen in model.tag_word.items():
        if tag in found:
            continue
        votes = [seen[w] / model.word_seen[w] for w in words if seen.get(w, 0) >= 2 and model.word_seen[w] > 0]
        if votes and sum(votes) / len(votes) >= 0.6:
            found.append(tag)
    parsed = parse_minutes(line)
    if parsed and parsed <= QUICK_MINUTES and "quick" not in found:
        found.append("quick")
    return found[:4]


def estimate_minutes(line, tags=None, model=None):
    """(minutes, source): what this task will probably take, from the minutes the person has given similar lines
    (same first word, then same tag), shrunk toward a typical figure so a single odd answer cannot swing it."""
    model = model or _current_model()
    first = (_WORD.findall((line or "").lower()) or [""])[0]
    pool = model.minutes.get("first:" + first) or []
    source = "your earlier " + first + " lines" if pool else None
    for tag in tags or []:
        if pool:
            break
        pool = model.minutes.get("tag:" + tag) or []
        source = "your earlier " + tag + " tasks" if pool else None
    overall = _median(model.minutes.get("all") or []) or DEFAULT_MINUTES
    if not pool:
        return overall, ("your usual task" if model.minutes.get("all") else "a guess")
    k = 2.0                       # as if two typical answers sat beside the person's own
    typical = overall
    est = (k * math.log(typical) + sum(math.log(m) for m in pool)) / (k + len(pool))
    return _round5(math.exp(est)), source


def _median(values):
    ordered = sorted(values)
    if not ordered:
        return None
    mid = len(ordered) // 2
    return ordered[mid] if len(ordered) % 2 else (ordered[mid - 1] + ordered[mid]) / 2


def _round5(minutes):
    return int(max(MIN_MINUTES, min(MAX_MINUTES, 5 * round(minutes / 5))))


def report(line, predicted, wanted, note=""):
    """The right-click "this guess was wrong" record, kept locally so it can be sent in or read later when the model is
    improved. The correction also teaches the model right away. Returns True when it was written."""
    text = (line or "").strip()
    if not text:
        return False
    entry = {"line": text, "predicted": predicted, "wanted": wanted, "note": (note or "")[:500],
             "examples": example_count()}
    os.makedirs(os.path.dirname(REPORTS_PATH), exist_ok=True)
    with open(REPORTS_PATH, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, separators=(",", ":")) + "\n")
    if wanted in CLASSES:
        teach(text, wanted, corrected=True)
    return True


def reports():
    out = []
    try:
        with open(REPORTS_PATH, "r", encoding="utf-8") as f:
            for raw in f:
                try:
                    out.append(json.loads(raw))
                except ValueError:
                    continue
    except OSError:
        pass
    return out
