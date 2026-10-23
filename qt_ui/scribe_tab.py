"""Scribe tab: an infinite drawing board that is always saved.

Laid out like virtual-graph-paper.com (tools on the left, options on the right,
zoom bar on top) but it opens exactly as it was left: same tool, colours, width,
background, zoom and position on the board. Every change is written to
private/scribe.json a moment later (see scribe_store.py), and again when the tab
is hidden or the app quits. Shape maths lives in scribe_geometry.py.
"""
import copy
import math
import time

from PySide6.QtCore import QBuffer, QByteArray, QPointF, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import (
    QColor,
    QFont,
    QFontMetricsF,
    QImage,
    QKeySequence,
    QPainter,
    QPen,
    QShortcut,
)
from PySide6.QtWidgets import (
    QApplication,
    QButtonGroup,
    QCheckBox,
    QColorDialog,
    QComboBox,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QSlider,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

import scribe_geometry as geo
import scribe_store

GRID = 20.0  # world units between grid lines at 100% zoom
SAVE_DELAY_MS = 600
MAX_UNDO = 100
RULER = 20

# background -> (paper, minor line / dot, major line, is dark)
_BACKGROUNDS = {
    "white": ("#FFFFFF", None, None, False),
    "black": ("#000000", None, None, True),
    "grid": ("#FFFFFF", "#9AD3DC", "#5FB3C2", False),
    "darkgrid": ("#0B0F14", "#1E2A35", "#33485A", True),
    "dots": ("#FFFFFF", "#7C828C", None, False),
    "lined": ("#FBF8EF", "#A9C4E8", None, False),
    "paper": ("#F6F1E4", None, None, False),
}
BACKGROUND_LABELS = {
    "white": "White", "black": "Black", "grid": "Grid", "darkgrid": "Dark grid",
    "dots": "Dots", "lined": "Lined", "paper": "Paper",
}
_TOOL_KEYS = {"1": "line", "2": "curve", "3": "fill", "4": "text", "5": "rect",
              "6": "circle", "7": "select", "8": "arrow", "0": "freehand"}
_ITEM_FLAGS = {"shouldScale": "scale", "roundCorners": "round", "dashed": "dashed"}


class ScribeCanvas(QWidget):
    changed = Signal()          # anything worth saving (shapes, view, options)
    zoomChanged = Signal(float)
    toolChanged = Signal(str)
    prefsChanged = Signal()     # options changed from inside the canvas
    layersChanged = Signal()

    def __init__(self, doc):
        super().__init__()
        self.doc = doc
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setAcceptDrops(True)
        self.setMinimumSize(320, 320)
        self._undo = []
        self._redo = []
        self._draft = None
        self._phase = 0            # curve: 0 = dragging the chord, 1 = placing the bend
        self._sel = []
        self._pan = None           # (start pos, view x, view y)
        self._space = False
        self.pan_mode = False
        self._move_last = None
        self._moved = False
        self._band = None
        self._cursor_world = None
        self._images = {}
        self._last_edit = None
        self._update_cursor()

    # ---- shortcuts to the document -------------------------------------
    @property
    def prefs(self):
        return self.doc["prefs"]

    @property
    def view(self):
        return self.doc["view"]

    @property
    def zoom(self):
        return self.view["zoom"]

    @property
    def tool(self):
        return self.prefs["tool"]

    def _active_items(self):
        layers = self.doc["layers"]
        return layers[max(0, min(self.doc["activeLayer"], len(layers) - 1))]["items"]

    # ---- coordinates -----------------------------------------------------
    def _to_world(self, pos):
        z = self.zoom
        return ((pos.x() - self.width() / 2) / z + self.view["x"],
                (pos.y() - self.height() / 2) / z + self.view["y"])

    def _to_screen(self, x, y):
        z = self.zoom
        return (x - self.view["x"]) * z + self.width() / 2, (y - self.view["y"]) * z + self.height() / 2

    # ---- undo ------------------------------------------------------------
    def _snapshot(self):
        return copy.deepcopy(self.doc["layers"]), self.doc["activeLayer"]

    def _push_undo(self):
        self._undo.append(self._snapshot())
        del self._undo[:-MAX_UNDO]
        self._redo.clear()

    def _restore(self, snapshot):
        self.doc["layers"], self.doc["activeLayer"] = snapshot
        self.doc["activeLayer"] = min(self.doc["activeLayer"], len(self.doc["layers"]) - 1)
        self._sel = []
        self._draft = None
        self.layersChanged.emit()
        self.changed.emit()
        self.update()

    def undo(self):
        if self._undo:
            self._redo.append(self._snapshot())
            self._restore(self._undo.pop())

    def redo(self):
        if self._redo:
            self._undo.append(self._snapshot())
            self._restore(self._redo.pop())

    # ---- options ---------------------------------------------------------
    def set_tool(self, tool):
        self._cancel_draft()
        self.prefs["tool"] = tool
        self.pan_mode = False
        self._update_cursor()
        self.toolChanged.emit(tool)
        self.changed.emit()

    def set_pan_mode(self, on):
        self.pan_mode = on
        self._update_cursor()

    def _apply_to_selection(self, fn, key):
        if not self._sel:
            return
        # One undo step for a run of the same edit (a spinner ticking through
        # widths, a colour dialog) rather than one per tick.
        now = time.monotonic()
        marker = (key, tuple(id(i) for i in self._sel))
        if not (self._last_edit and self._last_edit[0] == marker and now - self._last_edit[1] < 1.5):
            self._push_undo()
        self._last_edit = (marker, now)
        for item in self._sel:
            fn(item)
        self.update()

    def set_stroke(self, color):
        self.prefs["stroke"] = color
        self._apply_to_selection(lambda i: i.__setitem__("stroke", color), "stroke")
        self.changed.emit()
        self.update()

    def set_fill(self, color):
        self.prefs["fill"] = color
        self._apply_to_selection(
            lambda i: i.__setitem__("fill", color) if i["type"] in geo.CLOSED_TYPES else None, "fill")
        self.changed.emit()

    def set_width(self, width):
        self.prefs["width"] = width
        self._apply_to_selection(
            lambda i: i.__setitem__("width", width) if i["type"] not in ("text", "image") else None, "width")
        self.changed.emit()

    def set_flag(self, key, value):
        self.prefs[key] = value
        if key in _ITEM_FLAGS:
            self._apply_to_selection(lambda i: i.__setitem__(_ITEM_FLAGS[key], value), key)
        self.changed.emit()
        self.update()

    def set_background(self, background):
        self.prefs["background"] = background
        self.changed.emit()
        self.update()

    def _is_dark(self):
        return _BACKGROUNDS[self.prefs["background"]][3]

    def _ink(self, color):
        """Pure black ink shows as white on a dark board, so the default pen
        never vanishes after changing background; the stored colour is
        untouched, so switching back restores it exactly."""
        if self._is_dark() and color.lower() == "#000000":
            return "#ffffff"
        return color

    def _update_cursor(self):
        if self._space or self.pan_mode:
            self.setCursor(Qt.OpenHandCursor)
        elif self.tool == "select":
            self.setCursor(Qt.ArrowCursor)
        elif self.tool == "text":
            self.setCursor(Qt.IBeamCursor)
        else:
            self.setCursor(Qt.CrossCursor)

    # ---- view ------------------------------------------------------------
    def set_zoom(self, zoom, anchor=None):
        zoom = max(scribe_store.MIN_ZOOM, min(scribe_store.MAX_ZOOM, zoom))
        if anchor is not None:
            wx, wy = self._to_world(anchor)
            self.view["zoom"] = zoom
            self.view["x"] = wx - (anchor.x() - self.width() / 2) / zoom
            self.view["y"] = wy - (anchor.y() - self.height() / 2) / zoom
        else:
            self.view["zoom"] = zoom
        self.zoomChanged.emit(zoom)
        self.changed.emit()
        self.update()

    def zoom_by(self, factor):
        self.set_zoom(self.zoom * factor)

    def reset_view(self):
        self.view.update({"x": 0.0, "y": 0.0})
        self.set_zoom(1.0)

    def _all_bounds(self):
        boxes = [geo.bounds(i) for layer in self.doc["layers"] if layer["visible"] for i in layer["items"]]
        if not boxes:
            return None
        return (min(b[0] for b in boxes), min(b[1] for b in boxes),
                max(b[2] for b in boxes), max(b[3] for b in boxes))

    def fit_all(self):
        box = self._all_bounds()
        if box is None:
            return self.reset_view()
        bw, bh = max(box[2] - box[0], 1.0), max(box[3] - box[1], 1.0)
        self.view["x"], self.view["y"] = (box[0] + box[2]) / 2, (box[1] + box[3]) / 2
        self.set_zoom(0.9 * min(self.width() / bw, self.height() / bh))

    # ---- layers ----------------------------------------------------------
    def add_layer(self):
        self._push_undo()
        layers = self.doc["layers"]
        layers.insert(self.doc["activeLayer"] + 1, scribe_store.new_layer(f"Layer {len(layers) + 1}"))
        self.doc["activeLayer"] += 1
        self._layers_edited()

    def remove_layer(self):
        layers = self.doc["layers"]
        if len(layers) <= 1:
            return
        self._push_undo()
        del layers[self.doc["activeLayer"]]
        self.doc["activeLayer"] = min(self.doc["activeLayer"], len(layers) - 1)
        self._sel = []
        self._layers_edited()

    def move_layer(self, step):
        layers, i = self.doc["layers"], self.doc["activeLayer"]
        j = i + step
        if not 0 <= j < len(layers):
            return
        self._push_undo()
        layers[i], layers[j] = layers[j], layers[i]
        self.doc["activeLayer"] = j
        self._layers_edited()

    def set_active_layer(self, index):
        if 0 <= index < len(self.doc["layers"]):
            self.doc["activeLayer"] = index
            self.changed.emit()

    def set_layer_opacity(self, percent):
        self.doc["layers"][self.doc["activeLayer"]]["opacity"] = percent
        self.changed.emit()
        self.update()

    def layer_edited(self):
        """The layer list changed a name or visibility (no undo step)."""
        self.changed.emit()
        self.update()

    def _layers_edited(self):
        self.layersChanged.emit()
        self.changed.emit()
        self.update()

    # ---- painting --------------------------------------------------------
    def _image(self, name):
        if name not in self._images:
            path = scribe_store.image_path(name)
            self._images[name] = QImage(path) if path else QImage()
        return self._images[name]

    def paintEvent(self, event):
        p = QPainter(self)
        self._paint_background(p)
        p.setRenderHint(QPainter.Antialiasing, True)
        self._paint_scene(p, self.width(), self.height(), self.zoom, self.view["x"], self.view["y"], ui=True)
        if self.prefs["showGuides"]:
            self._paint_guides(p)
        if self.prefs["showRulers"]:
            self._paint_rulers(p)
        p.end()

    def _grid_spacing(self):
        spacing = GRID
        while spacing * self.zoom < 8:
            spacing *= 5
        return spacing

    def _paint_background(self, p):
        paper, minor, major, dark = _BACKGROUNDS[self.prefs["background"]]
        p.fillRect(self.rect(), QColor(paper))
        kind = self.prefs["background"]
        if minor is None:
            return
        z, w, h = self.zoom, self.width(), self.height()
        spacing = self._grid_spacing()
        step = spacing * z
        left, top = self.view["x"] - w / 2 / z, self.view["y"] - h / 2 / z
        first_i, first_j = math.floor(left / spacing), math.floor(top / spacing)
        count_x, count_y = int(w / step) + 2, int(h / step) + 2
        if kind == "dots":
            p.setPen(QPen(QColor(minor), 2.5, Qt.SolidLine, Qt.RoundCap))
            for i in range(first_i, first_i + count_x):
                for j in range(first_j, first_j + count_y):
                    sx, sy = self._to_screen(i * spacing, j * spacing)
                    p.drawPoint(QPointF(sx, sy))
            return
        if kind != "lined":
            for i in range(first_i, first_i + count_x):
                sx = round(self._to_screen(i * spacing, 0)[0]) + 0.5
                p.setPen(QPen(QColor(major if i % 5 == 0 else minor), 1))
                p.drawLine(QPointF(sx, 0), QPointF(sx, h))
        for j in range(first_j, first_j + count_y):
            sy = round(self._to_screen(0, j * spacing)[1]) + 0.5
            p.setPen(QPen(QColor(major if (major and j % 5 == 0) else minor), 1))
            p.drawLine(QPointF(0, sy), QPointF(w, sy))

    def _paint_scene(self, p, width, height, zoom, cx, cy, ui):
        p.save()
        p.translate(width / 2, height / 2)
        p.scale(zoom, zoom)
        p.translate(-cx, -cy)
        for layer in self.doc["layers"]:
            if not layer["visible"]:
                continue
            p.setOpacity(layer["opacity"] / 100)
            for item in layer["items"]:
                self._paint_item(p, item, zoom)
        p.setOpacity(1)
        if ui:
            if self._draft:
                self._paint_item(p, self._draft, zoom)
            pen = QPen(QColor("#3B82F6"), 1 / zoom, Qt.DashLine)
            p.setPen(pen)
            p.setBrush(Qt.NoBrush)
            pad = 4 / zoom
            for item in self._sel:
                x0, y0, x1, y1 = geo.bounds(item)
                p.drawRect(QRectF(x0 - pad, y0 - pad, x1 - x0 + 2 * pad, y1 - y0 + 2 * pad))
            if self._band:
                (ax, ay), (bx, by) = self._band
                p.setBrush(QColor(59, 130, 246, 40))
                p.drawRect(QRectF(QPointF(ax, ay), QPointF(bx, by)).normalized())
        p.restore()

    def _paint_item(self, p, item, zoom):
        kind = item["type"]
        if kind == "image":
            x, y = item["a"]
            rect = QRectF(x, y, item["w"], item["h"])
            image = self._image(item["file"])
            if image.isNull():
                p.setPen(QPen(QColor("#999999"), 1 / zoom, Qt.DashLine))
                p.setBrush(Qt.NoBrush)
                p.drawRect(rect)
                p.drawLine(rect.topLeft(), rect.bottomRight())
                p.drawLine(rect.topRight(), rect.bottomLeft())
            else:
                p.drawImage(rect, image)
            return
        color = QColor(self._ink(item["stroke"]))
        if kind == "text":
            font = QFont()
            font.setPixelSize(max(1, int(item["size"])))
            p.setFont(font)
            p.setPen(color)
            x, y = item["a"]
            p.drawText(QRectF(x, y, item["w"] + 4, item["h"] + 4), Qt.AlignLeft | Qt.AlignTop, item["text"])
            return
        pen_width = item["width"] if item.get("scale", True) else item["width"] / zoom
        rounded = item.get("round", True)
        pen = QPen(color, pen_width, Qt.DashLine if item.get("dashed") else Qt.SolidLine,
                   Qt.RoundCap if rounded else Qt.FlatCap, Qt.RoundJoin if rounded else Qt.MiterJoin)
        p.setPen(pen)
        fill = item.get("fill")
        p.setBrush(QColor(self._ink(fill)) if fill and kind in geo.CLOSED_TYPES else Qt.NoBrush)
        if kind == "stroke":
            pts = item["pts"]
            if len(pts) == 1:
                p.setPen(Qt.NoPen)
                p.setBrush(color)
                p.drawEllipse(QPointF(*pts[0]), pen_width / 2, pen_width / 2)
            else:
                p.drawPolyline([QPointF(x, y) for x, y in pts]) if not fill else \
                    p.drawPolygon([QPointF(x, y) for x, y in pts])
        elif kind in geo.LINE_TYPES:
            a, b = QPointF(*item["a"]), QPointF(*item["b"])
            p.drawLine(a, b)
            if kind == "arrow" and a != b:
                angle = math.atan2(b.y() - a.y(), b.x() - a.x())
                head = max(12.0, pen_width * 4)
                for turn in (math.radians(155), -math.radians(155)):
                    p.drawLine(b, QPointF(b.x() + head * math.cos(angle + turn),
                                          b.y() + head * math.sin(angle + turn)))
        elif kind == "curve":
            pts = geo.quad_points(item["a"], item["b"], item["c"])
            pts = [QPointF(x, y) for x, y in pts]
            p.drawPolygon(pts) if fill else p.drawPolyline(pts)
        elif kind in ("rect", "circle"):
            x0, y0, x1, y1 = geo.rect_of(item)
            rect = QRectF(x0, y0, x1 - x0, y1 - y0)
            p.drawRect(rect) if kind == "rect" else p.drawEllipse(rect)

    def _paint_guides(self, p):
        w, h = self.width(), self.height()
        p.setPen(QPen(QColor(220, 80, 80, 170), 1, Qt.DashLine))
        ox, oy = self._to_screen(0, 0)
        p.drawLine(QPointF(ox, 0), QPointF(ox, h))
        p.drawLine(QPointF(0, oy), QPointF(w, oy))
        if self._cursor_world:
            sx, sy = self._to_screen(*self._cursor_world)
            p.setPen(QPen(QColor(59, 130, 246, 150), 1, Qt.DotLine))
            p.drawLine(QPointF(sx, 0), QPointF(sx, h))
            p.drawLine(QPointF(0, sy), QPointF(w, sy))
            p.setPen(QColor("#3B82F6"))
            p.drawText(QPointF(sx + 8, sy - 8), f"{self._cursor_world[0]:.0f}, {self._cursor_world[1]:.0f}")

    def _paint_rulers(self, p):
        dark = self._is_dark()
        back, ink = (QColor("#1B222B"), QColor("#AAB4C0")) if dark else (QColor("#F0F1F3"), QColor("#59616B"))
        w, h, z = self.width(), self.height(), self.zoom
        p.fillRect(QRectF(0, 0, w, RULER), back)
        p.fillRect(QRectF(0, 0, RULER, h), back)
        step = next(10 ** e * m for e in range(-3, 8) for m in (1, 2, 5) if 10 ** e * m * z >= 60)
        font = QFont()
        font.setPixelSize(9)
        p.setFont(font)
        p.setPen(ink)
        left, top = self.view["x"] - w / 2 / z, self.view["y"] - h / 2 / z
        for i in range(math.floor(left / step), int((left + w / z) / step) + 2):
            sx, _ = self._to_screen(i * step, 0)
            p.drawLine(QPointF(sx, RULER - 6), QPointF(sx, RULER))
            p.drawText(QPointF(sx + 3, 11), f"{i * step:g}")
        for j in range(math.floor(top / step), int((top + h / z) / step) + 2):
            _, sy = self._to_screen(0, j * step)
            p.drawLine(QPointF(RULER - 6, sy), QPointF(RULER, sy))
            p.save()
            p.translate(10, sy - 3)
            p.rotate(-90)
            p.drawText(QPointF(0, 0), f"{j * step:g}")
            p.restore()

    # ---- drawing ---------------------------------------------------------
    def _make(self, kind, a):
        prefs = self.prefs
        # Only closed shapes take the fill colour when drawn; freehand and lines
        # stay unfilled (the Fill tool can still colour a freehand loop later).
        fill = prefs["fill"] if kind in ("rect", "circle", "curve") else None
        item = {"type": kind, "stroke": prefs["stroke"], "fill": fill, "width": prefs["width"],
                "dashed": prefs["dashed"], "round": prefs["roundCorners"], "scale": prefs["shouldScale"]}
        if kind == "stroke":
            item["pts"] = [list(a)]
        else:
            item["a"], item["b"] = list(a), list(a)
            if kind == "curve":
                item["c"] = list(a)
        return item

    def _grid_step(self):
        if self.prefs["snapAroundGrid"]:
            return GRID / 2
        if self.prefs["snapToGrid"]:
            return GRID
        return None

    def _snap(self, w):
        step = self._grid_step()
        return (geo.snap_value(w[0], step), geo.snap_value(w[1], step)) if step else tuple(w)

    def _end_point(self, kind, a, w, mods):
        shift = bool(mods & Qt.ShiftModifier)
        if kind in ("line", "arrow", "curve") and (shift or self.prefs["snapTo15"]):
            return geo.snap_angle(a, w)
        pt = self._snap(w)
        if shift and kind in ("rect", "circle"):
            pt = geo.square_up(a, pt)
        return pt

    def _commit(self, item):
        self._push_undo()
        self._active_items().append(item)
        self._draft = None
        self._phase = 0
        self.changed.emit()
        self.update()

    def _cancel_draft(self):
        self._draft = None
        self._phase = 0
        self._band = None
        self._move_last = None
        self.update()

    def _visible_items_top_first(self):
        for layer in reversed(self.doc["layers"]):
            if layer["visible"]:
                yield from reversed(layer["items"])

    def _item_at(self, w):
        tol = 6 / self.zoom
        return next((i for i in self._visible_items_top_first() if geo.hit_item(i, w[0], w[1], tol)), None)

    def _is_selected(self, item):
        return any(item is s for s in self._sel)

    def _fill_at(self, w):
        target = next((i for i in self._visible_items_top_first() if geo.contains_fill(i, w[0], w[1])), None)
        if target is None:
            return
        self._push_undo()
        target["fill"] = self.prefs["fill"]
        self.changed.emit()
        self.update()

    def _add_text(self, w):
        text, ok = QInputDialog.getMultiLineText(self, "Text", "Text:")
        if not ok or not text.strip():
            return
        size = int(max(12, self.prefs["width"] * 5))
        font = QFont()
        font.setPixelSize(size)
        box = QFontMetricsF(font).boundingRect(QRectF(0, 0, 10000, 10000), Qt.AlignLeft | Qt.AlignTop, text)
        item = {"type": "text", "a": list(w), "w": box.width(), "h": box.height(), "text": text,
                "size": size, "stroke": self.prefs["stroke"]}
        self._commit(item)

    # ---- images ----------------------------------------------------------
    def insert_images(self):
        paths, _ = QFileDialog.getOpenFileNames(
            self, "Insert image", "", "Images (*.png *.jpg *.jpeg *.gif *.bmp *.webp)")
        names = [n for n in (scribe_store.store_image_file(p) for p in paths) if n]
        self._place_images(names)

    def paste_image(self):
        image = QApplication.clipboard().image()
        if image.isNull():
            return False
        data = QByteArray()
        buffer = QBuffer(data)
        buffer.open(QBuffer.WriteOnly)
        image.save(buffer, "PNG")
        name = scribe_store.store_image_bytes(bytes(data))
        if name:
            self._place_images([name])
        return bool(name)

    def _place_images(self, names, at=None):
        x, y = at or (self.view["x"], self.view["y"])
        added = []
        for name in names:
            image = self._image(name)
            if image.isNull():
                continue
            k = min(1.0, 400 / max(image.width(), image.height()))
            w, h = image.width() * k, image.height() * k
            added.append({"type": "image", "a": [x - w / 2, y - h / 2], "w": w, "h": h, "file": name})
            x, y = x + 30, y + 30
        if not added:
            return
        self._push_undo()
        self._active_items().extend(added)
        self._sel = added
        self.changed.emit()
        self.update()

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event):
        paths = [u.toLocalFile() for u in event.mimeData().urls() if u.isLocalFile()]
        names = [n for n in (scribe_store.store_image_file(p) for p in paths
                             if p.lower().endswith(scribe_store.IMAGE_EXTENSIONS)) if n]
        self._place_images(names, self._to_world(event.position()))

    # ---- export ----------------------------------------------------------
    def export_png(self, path):
        box = self._all_bounds()
        if box is None:
            return False
        margin = 40
        bw, bh = box[2] - box[0] + 2 * margin, box[3] - box[1] + 2 * margin
        scale = min(2.0, 8000 / max(bw, bh))
        image = QImage(max(1, int(bw * scale)), max(1, int(bh * scale)), QImage.Format_ARGB32_Premultiplied)
        image.fill(QColor(_BACKGROUNDS[self.prefs["background"]][0]))
        p = QPainter(image)
        p.setRenderHint(QPainter.Antialiasing, True)
        self._paint_scene(p, image.width(), image.height(), scale,
                          (box[0] + box[2]) / 2, (box[1] + box[3]) / 2, ui=False)
        p.end()
        return image.save(path)

    def clear_board(self):
        if not any(layer["items"] for layer in self.doc["layers"]):
            return
        self._push_undo()
        for layer in self.doc["layers"]:
            layer["items"] = []
        self._sel = []
        self.changed.emit()
        self.update()

    # ---- mouse / keys ----------------------------------------------------
    def mousePressEvent(self, e):
        self.setFocus()
        pos = e.position()
        if e.button() == Qt.MiddleButton or (e.button() == Qt.LeftButton and (self._space or self.pan_mode)):
            self._pan = (pos, self.view["x"], self.view["y"])
            self.setCursor(Qt.ClosedHandCursor)
            return
        if e.button() != Qt.LeftButton:
            return
        w = self._to_world(pos)
        tool = self.tool
        if tool == "curve" and self._draft and self._phase == 1:
            self._draft["c"] = list(self._snap(w))
            self._commit(self._draft)
        elif tool == "freehand":
            self._draft = self._make("stroke", w)
        elif tool in ("line", "arrow", "curve"):
            self._draft = self._make(tool, self._snap(w))
            self._phase = 0
        elif tool in ("rect", "circle"):
            self._draft = self._make(tool, self._snap(w))
        elif tool == "text":
            self._add_text(w)
        elif tool == "fill":
            self._fill_at(w)
        elif tool == "select":
            self._select_press(w, e.modifiers())
        self.update()

    def _select_press(self, w, mods):
        hit = self._item_at(w)
        shift = bool(mods & Qt.ShiftModifier)
        if hit is not None:
            if self._is_selected(hit) and shift:
                self._sel = [s for s in self._sel if s is not hit]
                return
            if not self._is_selected(hit):
                self._sel = (self._sel if shift else []) + [hit]
            self._move_last = w
            self._moved = False
        else:
            if not shift:
                self._sel = []
            self._band = (w, w)

    def mouseMoveEvent(self, e):
        pos = e.position()
        w = self._to_world(pos)
        self._cursor_world = w
        if self._pan:
            start, vx, vy = self._pan
            self.view["x"] = vx - (pos.x() - start.x()) / self.zoom
            self.view["y"] = vy - (pos.y() - start.y()) / self.zoom
            self.changed.emit()
        elif self._draft is not None:
            d = self._draft
            kind = d["type"]
            if kind == "stroke":
                last = d["pts"][-1]
                if math.hypot(w[0] - last[0], w[1] - last[1]) >= 1.2 / self.zoom and len(d["pts"]) < 4000:
                    d["pts"].append(list(w))
            elif kind == "curve" and self._phase == 1:
                d["c"] = list(self._snap(w))
            else:
                end = list(self._end_point(kind, d["a"], w, e.modifiers()))
                d["b"] = end
                if kind == "curve":
                    d["c"] = [(d["a"][0] + end[0]) / 2, (d["a"][1] + end[1]) / 2]
        elif self._move_last is not None:
            dx, dy = w[0] - self._move_last[0], w[1] - self._move_last[1]
            if not self._moved:
                self._push_undo()
                self._moved = True
            for item in self._sel:
                geo.translate(item, dx, dy)
            self._move_last = w
        elif self._band:
            self._band = (self._band[0], w)
        elif not self.prefs["showGuides"]:
            return
        self.update()

    def mouseReleaseEvent(self, e):
        if self._pan:
            self._pan = None
            self._update_cursor()
            return
        if e.button() != Qt.LeftButton:
            return
        d = self._draft
        if d is not None:
            kind = d["type"]
            if kind == "stroke":
                d["pts"] = geo.smooth_stroke(d["pts"], self.prefs["smoothness"], self.zoom)
                self._commit(d)
            elif kind == "curve":
                if d["a"] == d["b"]:
                    self._cancel_draft()
                else:
                    self._phase = 1
            elif d["a"] == d["b"]:
                self._cancel_draft()
            else:
                self._commit(d)
        elif self._move_last is not None:
            self._move_last = None
            if self._moved:
                self.changed.emit()
        elif self._band:
            (ax, ay), (bx, by) = self._band
            box = QRectF(QPointF(ax, ay), QPointF(bx, by)).normalized()
            for item in self._visible_items_top_first():
                x0, y0, x1, y1 = geo.bounds(item)
                if box.intersects(QRectF(x0, y0, x1 - x0, y1 - y0)) and not self._is_selected(item):
                    self._sel.append(item)
            self._band = None
        self.update()

    def wheelEvent(self, e):
        self.set_zoom(self.zoom * 1.1 ** (e.angleDelta().y() / 120), e.position())

    def keyPressEvent(self, e):
        key, text = e.key(), e.text()
        ctrl = bool(e.modifiers() & Qt.ControlModifier)
        if key == Qt.Key_Space and not e.isAutoRepeat():
            self._space = True
            self._update_cursor()
        elif ctrl and key == Qt.Key_A:
            self._sel = [i for i in self._visible_items_top_first()]
            self.update()
        elif ctrl and key == Qt.Key_V:
            self.paste_image()
        elif key in (Qt.Key_Delete, Qt.Key_Backspace) and self._sel:
            self._push_undo()
            for layer in self.doc["layers"]:
                layer["items"] = [i for i in layer["items"] if not self._is_selected(i)]
            self._sel = []
            self.changed.emit()
            self.update()
        elif key == Qt.Key_Escape:
            self._sel = []
            self._cancel_draft()
        elif text in _TOOL_KEYS and not ctrl:
            self.set_tool(_TOOL_KEYS[text])
        else:
            super().keyPressEvent(e)

    def keyReleaseEvent(self, e):
        if e.key() == Qt.Key_Space and not e.isAutoRepeat():
            self._space = False
            self._update_cursor()
        else:
            super().keyReleaseEvent(e)


_TOOLS = [
    ("line", "╱", "Line", "1"), ("curve", "⌒", "Curve", "2"), ("fill", "🪣", "Fill", "3"),
    ("text", "Aa", "Text", "4"), ("rect", "▭", "Rectangle", "5"), ("circle", "◯", "Circle", "6"),
    ("select", "⬚", "Select", "7"), ("arrow", "➘", "Arrow", "8"), ("insert", "📎", "Insert", ""),
    ("freehand", "✎", "Freehand", "0"),
]

_STYLE = """
QFrame#ScribePanel { background: #FFFFFF; border: 1px solid #E3E5E9; border-radius: 8px; }
QLabel#ScribePanelTitle { background: #EEF0F3; color: #59616B; font-weight: 600; padding: 3px 8px;
    border-top-left-radius: 8px; border-top-right-radius: 8px; }
QPushButton[scribe="tool"] { text-align: left; padding: 6px 8px; border: 1px solid #E3E5E9;
    border-radius: 6px; background: #FAFBFC; color: #1F2328; }
QPushButton[scribe="tool"]:checked { background: #CFE3FF; border-color: #8DB8F5; }
QPushButton[scribe="tool"]:hover { background: #EEF4FF; }
QLabel#ScribeKey { background: #FFF200; color: #333; font-size: 10px; padding: 2px 4px; border-radius: 3px; }
QLabel { color: #1F2328; }
QCheckBox { color: #1F2328; }
QCheckBox::indicator { width: 14px; height: 14px; border: 1px solid #9AA0A8; border-radius: 3px; background: #FFFFFF; }
QCheckBox::indicator:checked { background: #5B8DEF; border-color: #5B8DEF; }
QPushButton { color: #1F2328; background: #FAFBFC; border: 1px solid #E3E5E9; border-radius: 6px; padding: 5px 8px; }
QPushButton:hover { background: #EEF4FF; }
QPushButton:checked { background: #CFE3FF; border-color: #8DB8F5; }
QComboBox, QSpinBox, QListWidget { background: #FAFBFC; color: #1F2328; border: 1px solid #E3E5E9;
    border-radius: 6px; padding: 2px 6px; }
QListWidget::item:selected { background: #CFE3FF; color: #1F2328; }
"""


class ScribeTab(QWidget):
    def __init__(self):
        super().__init__()
        self._dirty = False
        self.canvas = ScribeCanvas(scribe_store.load())
        self._save_timer = QTimer(self)
        self._save_timer.setSingleShot(True)
        self._save_timer.setInterval(SAVE_DELAY_MS)
        self._save_timer.timeout.connect(self.flush)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(8)
        layout.addWidget(self._build_left())
        center = QVBoxLayout()
        center.addWidget(self._build_topbar())
        center.addWidget(self.canvas, 1)
        layout.addLayout(center, 1)
        layout.addWidget(self._build_right())

        self.canvas.changed.connect(self._schedule_save)
        self.canvas.zoomChanged.connect(self._show_zoom)
        self.canvas.toolChanged.connect(self._show_tool)
        self.canvas.layersChanged.connect(self._refresh_layers)
        self._show_zoom(self.canvas.zoom)
        self._show_tool(self.canvas.tool)
        self._refresh_layers()

        for keys, fn in (("Ctrl+Z", self.canvas.undo), ("Ctrl+Y", self.canvas.redo),
                         ("Ctrl+Shift+Z", self.canvas.redo)):
            shortcut = QShortcut(QKeySequence(keys), self)
            shortcut.setContext(Qt.WidgetWithChildrenShortcut)
            shortcut.activated.connect(fn)
        app = QApplication.instance()
        if app is not None:
            app.aboutToQuit.connect(self.flush)

    # ---- saving ----------------------------------------------------------
    def _schedule_save(self):
        self._dirty = True
        self._save_timer.start()

    def flush(self):
        if not self._dirty:
            return
        try:
            scribe_store.save(self.canvas.doc)
            self._dirty = False
        except OSError:
            pass  # stays dirty; the next change (or quit) tries again

    def hideEvent(self, event):
        self.flush()
        super().hideEvent(event)

    # ---- layout helpers --------------------------------------------------
    def _panel(self, title, width=None):
        frame = QFrame()
        frame.setObjectName("ScribePanel")
        outer = QVBoxLayout(frame)
        outer.setContentsMargins(0, 0, 0, 6)
        outer.setSpacing(4)
        label = QLabel(title)
        label.setObjectName("ScribePanelTitle")
        outer.addWidget(label)
        body = QVBoxLayout()
        body.setContentsMargins(8, 2, 8, 2)
        body.setSpacing(4)
        outer.addLayout(body)
        return frame, body

    def _build_left(self):
        column = QWidget()
        # Styled on the columns and top bar only, never on the whole tab: dialogs
        # parented to the tab (New, Text, colour picker) would inherit the dark
        # label colour and show blank on a dark dialog background.
        column.setStyleSheet(_STYLE)
        column.setFixedWidth(178)
        box = QVBoxLayout(column)
        box.setContentsMargins(0, 0, 0, 0)
        box.setSpacing(8)

        tools, body = self._panel("Tools")
        self._tool_group = QButtonGroup(self)
        self._tool_group.setExclusive(True)
        self._tool_buttons = {}
        for key, glyph, name, hotkey in _TOOLS:
            row = QHBoxLayout()
            row.setSpacing(4)
            button = QPushButton(f"{glyph}   {name}")
            button.setProperty("scribe", "tool")
            if key == "insert":
                button.clicked.connect(self.canvas.insert_images)
            else:
                button.setCheckable(True)
                self._tool_group.addButton(button)
                self._tool_buttons[key] = button
                button.clicked.connect(lambda checked, k=key: self.canvas.set_tool(k))
            row.addWidget(button, 1)
            if hotkey:
                badge = QLabel(hotkey)
                badge.setObjectName("ScribeKey")
                row.addWidget(badge)
            body.addLayout(row)
        box.addWidget(tools)

        view, body = self._panel("View")
        self._pref_checks = {}
        for key, label in (("snapToGrid", "Snap to grid"), ("snapAroundGrid", "Snap around grid"),
                           ("snapTo15", "Snap to 15°"), ("showGuides", "Show guides"),
                           ("showRulers", "Show rulers")):
            check = QCheckBox(label)
            check.setChecked(self.canvas.prefs[key])
            check.toggled.connect(lambda on, k=key: self.canvas.set_flag(k, on))
            self._pref_checks[key] = check
            body.addWidget(check)
        box.addWidget(view)

        layers, body = self._panel("Layers")
        self._opacity = QComboBox()
        for pct in scribe_store.OPACITIES:
            self._opacity.addItem(f"{pct}%", pct)
        self._opacity.currentIndexChanged.connect(
            lambda: self.canvas.set_layer_opacity(self._opacity.currentData()))
        row = QHBoxLayout()
        row.addWidget(QLabel("Opacity:"))
        row.addWidget(self._opacity, 1)
        body.addLayout(row)
        self._layer_list = QListWidget()
        self._layer_list.setFixedHeight(110)
        self._layer_list.currentRowChanged.connect(self._layer_row_changed)
        self._layer_list.itemChanged.connect(self._layer_item_changed)
        body.addWidget(self._layer_list)
        row = QHBoxLayout()
        for text, fn in (("+", self.canvas.add_layer), ("−", self.canvas.remove_layer),
                         ("↑", lambda: self.canvas.move_layer(1)), ("↓", lambda: self.canvas.move_layer(-1))):
            button = QPushButton(text)
            button.setFixedWidth(34)
            button.clicked.connect(fn)
            row.addWidget(button)
        body.addLayout(row)
        box.addWidget(layers)
        box.addStretch(1)
        return column

    def _build_topbar(self):
        holder = QWidget()
        holder.setStyleSheet(_STYLE)
        bar = QHBoxLayout(holder)
        bar.setContentsMargins(0, 0, 0, 0)
        bar.setSpacing(6)
        minus = QPushButton("−")
        plus = QPushButton("+")
        for button in (minus, plus):
            button.setFixedWidth(30)
        minus.clicked.connect(lambda: self.canvas.zoom_by(1 / 1.25))
        plus.clicked.connect(lambda: self.canvas.zoom_by(1.25))
        self._zoom_label = QLabel("100%")
        self._zoom_label.setMinimumWidth(48)
        self._zoom_label.setAlignment(Qt.AlignCenter)
        self._pan_button = QPushButton("✋ Pan")
        self._pan_button.setCheckable(True)
        self._pan_button.toggled.connect(self.canvas.set_pan_mode)
        reset = QPushButton("↻ Reset")
        reset.clicked.connect(self.canvas.reset_view)
        fit = QPushButton("⤢ Fit")
        fit.clicked.connect(self.canvas.fit_all)
        for widget in (minus, self._zoom_label, plus, self._pan_button, reset, fit):
            bar.addWidget(widget)
        hint = QLabel("Scroll to zoom · hold Space or middle-drag to pan · always saved")
        hint.setStyleSheet("color: #8A9099; font-size: 11px;")
        bar.addWidget(hint)
        bar.addStretch(1)
        return holder

    def _swatch(self, color):
        button = QPushButton()
        button.setFixedSize(44, 28)
        self._paint_swatch(button, color)
        return button

    @staticmethod
    def _paint_swatch(button, color):
        if color:
            button.setText("")
            button.setStyleSheet(f"background: {color}; border: 1px solid #888; border-radius: 4px;")
        else:
            button.setText("∅")
            button.setStyleSheet("background: #FFFFFF; color: #D33; border: 1px solid #888; border-radius: 4px;")

    def _build_right(self):
        column = QWidget()
        # Styled on the columns and top bar only, never on the whole tab: dialogs
        # parented to the tab (New, Text, colour picker) would inherit the dark
        # label colour and show blank on a dark dialog background.
        column.setStyleSheet(_STYLE)
        column.setFixedWidth(168)
        box = QVBoxLayout(column)
        box.setContentsMargins(0, 0, 0, 0)
        box.setSpacing(8)
        prefs = self.canvas.prefs

        free, body = self._panel("Freehand")
        body.addWidget(QLabel("Smoothness"))
        self._smooth = QSlider(Qt.Horizontal)
        self._smooth.setRange(0, 10)
        self._smooth.setValue(prefs["smoothness"])
        self._smooth.valueChanged.connect(self._smoothness_changed)
        body.addWidget(self._smooth)
        box.addWidget(free)

        colors, body = self._panel("Colors")
        row = QHBoxLayout()
        row.addWidget(QLabel("Stroke:"), 1)
        self._stroke_swatch = self._swatch(prefs["stroke"])
        self._stroke_swatch.clicked.connect(self._pick_stroke)
        row.addWidget(self._stroke_swatch)
        body.addLayout(row)
        row = QHBoxLayout()
        row.addWidget(QLabel("Fill:"), 1)
        self._fill_swatch = self._swatch(prefs["fill"])
        self._fill_swatch.clicked.connect(self._pick_fill)
        row.addWidget(self._fill_swatch)
        body.addLayout(row)
        no_fill = QPushButton("No fill")
        no_fill.clicked.connect(lambda: self._set_fill(None))
        body.addWidget(no_fill)
        box.addWidget(colors)

        stroke, body = self._panel("Stroke")
        row = QHBoxLayout()
        row.addWidget(QLabel("Width:"), 1)
        self._width = QSpinBox()
        self._width.setRange(1, 200)
        self._width.setValue(int(prefs["width"]))
        self._width.valueChanged.connect(self.canvas.set_width)
        row.addWidget(self._width)
        body.addLayout(row)
        for key, label in (("shouldScale", "Should scale"), ("roundCorners", "Round corners"),
                           ("dashed", "Dashed")):
            check = QCheckBox(label)
            check.setChecked(prefs[key])
            check.toggled.connect(lambda on, k=key: self.canvas.set_flag(k, on))
            self._pref_checks[key] = check
            body.addWidget(check)
        box.addWidget(stroke)

        background, body = self._panel("Background")
        self._background = QComboBox()
        for key, label in BACKGROUND_LABELS.items():
            self._background.addItem(label, key)
        self._background.setCurrentIndex(self._background.findData(prefs["background"]))
        self._background.currentIndexChanged.connect(
            lambda: self.canvas.set_background(self._background.currentData()))
        body.addWidget(self._background)
        box.addWidget(background)

        menu, body = self._panel("Menu")
        for text, fn in (("↶  Undo (Ctrl+Z)", self.canvas.undo), ("↷  Redo (Ctrl+Y)", self.canvas.redo),
                         ("📄  New", self._new_board), ("💾  Export PNG", self._export)):
            button = QPushButton(text)
            button.clicked.connect(fn)
            body.addWidget(button)
        box.addWidget(menu)
        box.addStretch(1)
        return column

    # ---- panel callbacks -------------------------------------------------
    def _show_zoom(self, zoom):
        self._zoom_label.setText(f"{zoom * 100:.0f}%")

    def _show_tool(self, tool):
        button = self._tool_buttons.get(tool)
        if button:
            button.setChecked(True)
        if self._pan_button.isChecked():
            self._pan_button.setChecked(False)

    def _smoothness_changed(self, value):
        self.canvas.prefs["smoothness"] = value
        self.canvas.changed.emit()

    def _pick_stroke(self):
        color = QColorDialog.getColor(QColor(self.canvas.prefs["stroke"]), self, "Stroke color")
        if color.isValid():
            self._paint_swatch(self._stroke_swatch, color.name())
            self.canvas.set_stroke(color.name())

    def _pick_fill(self):
        start = QColor(self.canvas.prefs["fill"] or "#FFD54F")
        color = QColorDialog.getColor(start, self, "Fill color")
        if color.isValid():
            self._set_fill(color.name())

    def _set_fill(self, color):
        self._paint_swatch(self._fill_swatch, color)
        self.canvas.set_fill(color)

    def _new_board(self):
        answer = QMessageBox.question(
            self, "New board", "Clear every shape on every layer? (Undo brings it back until you quit.)")
        if answer == QMessageBox.Yes:
            self.canvas.clear_board()

    def _export(self):
        path, _ = QFileDialog.getSaveFileName(self, "Export PNG", "scribe.png", "PNG image (*.png)")
        if path and not self.canvas.export_png(path):
            QMessageBox.information(self, "Export PNG", "Nothing to export yet -- the board is empty.")

    # ---- layer list ------------------------------------------------------
    def _refresh_layers(self):
        layers, active = self.canvas.doc["layers"], self.canvas.doc["activeLayer"]
        self._layer_list.blockSignals(True)
        self._layer_list.clear()
        for index in reversed(range(len(layers))):  # top layer first
            layer = layers[index]
            item = QListWidgetItem(layer["name"])
            item.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable | Qt.ItemIsUserCheckable | Qt.ItemIsEditable)
            item.setCheckState(Qt.Checked if layer["visible"] else Qt.Unchecked)
            self._layer_list.addItem(item)
        self._layer_list.setCurrentRow(len(layers) - 1 - active)
        self._opacity.blockSignals(True)
        self._opacity.setCurrentIndex(self._opacity.findData(layers[active]["opacity"]))
        self._opacity.blockSignals(False)
        self._layer_list.blockSignals(False)

    def _layer_index(self, row):
        return len(self.canvas.doc["layers"]) - 1 - row

    def _layer_row_changed(self, row):
        if row < 0:
            return
        self.canvas.set_active_layer(self._layer_index(row))
        self._opacity.blockSignals(True)
        self._opacity.setCurrentIndex(
            self._opacity.findData(self.canvas.doc["layers"][self._layer_index(row)]["opacity"]))
        self._opacity.blockSignals(False)

    def _layer_item_changed(self, item):
        layer = self.canvas.doc["layers"][self._layer_index(self._layer_list.row(item))]
        layer["visible"] = item.checkState() == Qt.Checked
        layer["name"] = item.text().strip() or layer["name"]
        self.canvas.layer_edited()
