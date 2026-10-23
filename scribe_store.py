"""Scribe: an infinite drawing board that is always saved, kept in private/scribe.json.

One document, local to this computer (not synced -- a board can hold large
attached images, and the cloud rows here are small by design). It holds the
layers with every shape on them, the last view (zoom + the world point at the
centre of the board, so the board reopens exactly where it was left whatever the
window size) and the last-used options (tool, colours, width, background...), so
Scribe always opens as it was closed. Attached images are copied into
private/scribe_images/ and referenced by file name, which keeps the json small.
"""
import json
import os
import shutil
import threading
import uuid

HERE = os.path.dirname(os.path.abspath(__file__))
PATH = os.path.join(HERE, "private", "scribe.json")
IMAGES_DIR = os.path.join(HERE, "private", "scribe_images")

TOOLS = ("line", "curve", "fill", "text", "rect", "circle", "select", "arrow", "freehand")
BACKGROUNDS = ("white", "black", "grid", "darkgrid", "dots", "lined", "paper")
IMAGE_EXTENSIONS = (".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp")
MIN_ZOOM = 0.02
MAX_ZOOM = 64.0
OPACITIES = (100, 75, 50, 25)

DEFAULT_PREFS = {
    "tool": "freehand",
    "stroke": "#000000",
    "fill": None,  # None = no fill
    "width": 4,
    "shouldScale": True,
    "roundCorners": True,
    "dashed": False,
    "smoothness": 5,
    "background": "grid",
    "snapToGrid": True,
    "snapAroundGrid": False,
    "snapTo15": False,
    "showGuides": False,
    "showRulers": False,
}

_lock = threading.Lock()


def new_layer(name, layer_id=None):
    return {"id": layer_id or uuid.uuid4().hex[:8], "name": name, "visible": True, "opacity": 100, "items": []}


def default_doc():
    return {
        "version": 1,
        "layers": [new_layer("Layer 1")],
        "activeLayer": 0,
        "view": {"zoom": 1.0, "x": 0.0, "y": 0.0},
        "prefs": dict(DEFAULT_PREFS),
    }


def _num(value, default, low=None, high=None):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value != value:
        return default
    if low is not None:
        value = max(low, value)
    if high is not None:
        value = min(high, value)
    return value


def _color(value, default):
    if isinstance(value, str) and len(value) in (7, 9) and value.startswith("#"):
        return value
    return default


def _clean_prefs(raw):
    raw = raw if isinstance(raw, dict) else {}
    prefs = dict(DEFAULT_PREFS)
    if raw.get("tool") in TOOLS:
        prefs["tool"] = raw["tool"]
    if raw.get("background") in BACKGROUNDS:
        prefs["background"] = raw["background"]
    prefs["stroke"] = _color(raw.get("stroke"), prefs["stroke"])
    prefs["fill"] = _color(raw.get("fill"), None)
    prefs["width"] = _num(raw.get("width"), prefs["width"], 1, 200)
    prefs["smoothness"] = int(_num(raw.get("smoothness"), prefs["smoothness"], 0, 10))
    for key in ("shouldScale", "roundCorners", "dashed", "snapToGrid", "snapAroundGrid",
                "snapTo15", "showGuides", "showRulers"):
        if isinstance(raw.get(key), bool):
            prefs[key] = raw[key]
    return prefs


def _is_point(value):
    return (isinstance(value, (list, tuple)) and len(value) == 2
            and all(isinstance(n, (int, float)) and not isinstance(n, bool) and n == n for n in value))


def _valid_item(item):
    """True if the item has what painting and hit-testing index straight into.
    A hand-edited or damaged file must lose a bad shape, not crash the board."""
    kind = item.get("type")
    if kind == "stroke":
        pts = item.get("pts")
        ok = isinstance(pts, list) and bool(pts) and all(_is_point(p) for p in pts)
    elif kind in ("line", "arrow", "rect", "circle"):
        ok = _is_point(item.get("a")) and _is_point(item.get("b"))
    elif kind == "curve":
        ok = all(_is_point(item.get(k)) for k in ("a", "b", "c"))
    elif kind == "text":
        ok = (_is_point(item.get("a")) and isinstance(item.get("text"), str)
              and all(isinstance(item.get(k), (int, float)) for k in ("w", "h", "size")))
    elif kind == "image":
        ok = (_is_point(item.get("a")) and isinstance(item.get("file"), str)
              and all(isinstance(item.get(k), (int, float)) for k in ("w", "h")))
    else:
        return False
    if not ok:
        return False
    if kind not in ("text", "image"):
        item["width"] = _num(item.get("width"), 4, 1, 200)
    item["stroke"] = _color(item.get("stroke"), "#000000")
    if item.get("fill") is not None:
        item["fill"] = _color(item.get("fill"), None)
    return True


def _clean_layer(raw, index):
    if not isinstance(raw, dict):
        return None
    items = [i for i in raw.get("items", []) if isinstance(i, dict) and _valid_item(i)] \
        if isinstance(raw.get("items"), list) else []
    layer_id = raw.get("id") if isinstance(raw.get("id"), str) and raw.get("id") else uuid.uuid4().hex[:8]
    name = raw.get("name") if isinstance(raw.get("name"), str) and raw.get("name") else f"Layer {index + 1}"
    opacity = raw.get("opacity") if raw.get("opacity") in OPACITIES else 100
    return {
        "id": layer_id, "name": name,
        "visible": raw.get("visible") if isinstance(raw.get("visible"), bool) else True,
        "opacity": opacity, "items": items,
    }


def clean_doc(raw):
    """A valid document from whatever was read -- anything missing or malformed
    falls back to its default, so a damaged file never stops Scribe opening."""
    if not isinstance(raw, dict):
        return default_doc()
    layers = []
    for i, layer in enumerate(raw.get("layers") if isinstance(raw.get("layers"), list) else []):
        cleaned = _clean_layer(layer, i)
        if cleaned:
            layers.append(cleaned)
    if not layers:
        layers = [new_layer("Layer 1")]
    view = raw.get("view") if isinstance(raw.get("view"), dict) else {}
    active = raw.get("activeLayer")
    active = active if isinstance(active, int) and not isinstance(active, bool) and 0 <= active < len(layers) else 0
    return {
        "version": 1,
        "layers": layers,
        "activeLayer": active,
        "view": {
            "zoom": _num(view.get("zoom"), 1.0, MIN_ZOOM, MAX_ZOOM),
            "x": _num(view.get("x"), 0.0, -1e9, 1e9),
            "y": _num(view.get("y"), 0.0, -1e9, 1e9),
        },
        "prefs": _clean_prefs(raw.get("prefs")),
    }


def load():
    with _lock:
        try:
            with open(PATH, "r", encoding="utf-8") as f:
                return clean_doc(json.load(f))
        except (OSError, ValueError):
            return default_doc()


def save(doc):
    """Writes the whole board atomically, so a crash mid-save never leaves a
    half-written file in place of the last good one."""
    cleaned = clean_doc(doc)
    with _lock:
        os.makedirs(os.path.dirname(PATH), exist_ok=True)
        tmp = PATH + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(cleaned, f)
        os.replace(tmp, PATH)


def _image_ext(name):
    ext = os.path.splitext(name)[1].lower()
    return ext if ext in IMAGE_EXTENSIONS else ".png"


def store_image_file(source_path):
    """Copies an image into the board's image folder; returns its stored name,
    or None if it could not be copied."""
    try:
        os.makedirs(IMAGES_DIR, exist_ok=True)
        name = uuid.uuid4().hex + _image_ext(source_path)
        shutil.copyfile(source_path, os.path.join(IMAGES_DIR, name))
        return name
    except OSError:
        return None


def store_image_bytes(data, ext=".png"):
    try:
        os.makedirs(IMAGES_DIR, exist_ok=True)
        name = uuid.uuid4().hex + _image_ext("x" + ext)
        with open(os.path.join(IMAGES_DIR, name), "wb") as f:
            f.write(data)
        return name
    except OSError:
        return None


def image_path(name):
    """Full path of a stored image, or None for a name that is not a plain file
    name (a board file must never point Scribe outside its image folder)."""
    if not isinstance(name, str) or not name or name != os.path.basename(name):
        return None
    return os.path.join(IMAGES_DIR, name)
