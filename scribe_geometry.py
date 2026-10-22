"""Scribe's shape maths, kept free of Qt so it can be unit-tested directly.

Every shape is a dict with a "type":
  stroke  pts [[x, y], ...]            (freehand)
  line    a, b                         arrow  a, b (head at b)
  curve   a, b, c                      (quadratic, c is the bend point)
  rect    a, b   circle  a, b          (opposite corners of the bounding box)
  text    a, w, h, text, size          image  a, w, h, file
plus stroke/fill colours, width, dashed, round and scale. Points are world
coordinates -- the board is infinite, there is no canvas edge.
"""
import math

CLOSED_TYPES = ("rect", "circle", "stroke", "curve")
LINE_TYPES = ("line", "arrow")


def quad_points(a, b, c, steps=24):
    pts = []
    for i in range(steps + 1):
        t = i / steps
        u = 1 - t
        pts.append((u * u * a[0] + 2 * u * t * c[0] + t * t * b[0],
                    u * u * a[1] + 2 * u * t * c[1] + t * t * b[1]))
    return pts


def snap_value(value, step):
    return round(value / step) * step


def snap_angle(anchor, point, degrees=15):
    """`point` moved to the nearest `degrees` multiple of angle around `anchor`,
    keeping its distance."""
    dx, dy = point[0] - anchor[0], point[1] - anchor[1]
    length = math.hypot(dx, dy)
    if length == 0:
        return tuple(point)
    step = math.radians(degrees)
    angle = round(math.atan2(dy, dx) / step) * step
    return (anchor[0] + length * math.cos(angle), anchor[1] + length * math.sin(angle))


def square_up(anchor, point):
    """`point` pulled onto the diagonal from `anchor` (a square / true circle)."""
    dx, dy = point[0] - anchor[0], point[1] - anchor[1]
    side = max(abs(dx), abs(dy))
    return (anchor[0] + math.copysign(side, dx or 1), anchor[1] + math.copysign(side, dy or 1))


def dist_point_segment(px, py, a, b):
    ax, ay = a
    bx, by = b
    dx, dy = bx - ax, by - ay
    length_sq = dx * dx + dy * dy
    if length_sq == 0:
        return math.hypot(px - ax, py - ay)
    t = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / length_sq))
    return math.hypot(px - (ax + t * dx), py - (ay + t * dy))


def _polyline_dist(px, py, pts):
    if len(pts) == 1:
        return math.hypot(px - pts[0][0], py - pts[0][1])
    return min(dist_point_segment(px, py, pts[i], pts[i + 1]) for i in range(len(pts) - 1))


def rect_of(item):
    """(x0, y0, x1, y1) of the corners a and b, normalised."""
    a, b = item["a"], item["b"]
    return min(a[0], b[0]), min(a[1], b[1]), max(a[0], b[0]), max(a[1], b[1])


def outline_points(item):
    kind = item["type"]
    if kind == "stroke":
        return [tuple(p) for p in item["pts"]]
    if kind in LINE_TYPES:
        return [tuple(item["a"]), tuple(item["b"])]
    if kind == "curve":
        return quad_points(item["a"], item["b"], item["c"])
    if kind == "rect":
        x0, y0, x1, y1 = rect_of(item)
        return [(x0, y0), (x1, y0), (x1, y1), (x0, y1), (x0, y0)]
    if kind == "circle":
        x0, y0, x1, y1 = rect_of(item)
        cx, cy, rx, ry = (x0 + x1) / 2, (y0 + y1) / 2, (x1 - x0) / 2, (y1 - y0) / 2
        return [(cx + rx * math.cos(2 * math.pi * i / 48), cy + ry * math.sin(2 * math.pi * i / 48))
                for i in range(49)]
    return []


def bounds(item):
    """(x0, y0, x1, y1) in world coordinates, padded by half the line width."""
    kind = item["type"]
    if kind in ("text", "image"):
        x, y = item["a"]
        return x, y, x + item["w"], y + item["h"]
    pts = outline_points(item)
    if kind == "curve":
        pts = pts + [tuple(item["c"])]
    if not pts:
        return 0.0, 0.0, 0.0, 0.0
    pad = item.get("width", 1) / 2
    xs, ys = [p[0] for p in pts], [p[1] for p in pts]
    return min(xs) - pad, min(ys) - pad, max(xs) + pad, max(ys) + pad


def translate(item, dx, dy):
    for key in ("a", "b", "c"):
        if key in item:
            item[key] = [item[key][0] + dx, item[key][1] + dy]
    if item["type"] == "stroke":
        item["pts"] = [[x + dx, y + dy] for x, y in item["pts"]]


def _in_polygon(px, py, pts):
    inside = False
    n = len(pts)
    for i in range(n):
        x1, y1 = pts[i]
        x2, y2 = pts[(i + 1) % n]
        if (y1 > py) != (y2 > py) and px < (x2 - x1) * (py - y1) / (y2 - y1) + x1:
            inside = not inside
    return inside


def hit_item(item, x, y, tol):
    """True if (x, y) is on the item's line (within tol + half its width) or
    inside a filled shape."""
    kind = item["type"]
    if kind in ("text", "image"):
        x0, y0, x1, y1 = bounds(item)
        return x0 - tol <= x <= x1 + tol and y0 - tol <= y <= y1 + tol
    reach = tol + item.get("width", 1) / 2
    pts = outline_points(item)
    if pts and _polyline_dist(x, y, pts) <= reach:
        return True
    return bool(item.get("fill")) and kind in CLOSED_TYPES and contains_fill(item, x, y)


def contains_fill(item, x, y):
    """True if (x, y) is inside a closed shape (what the Fill tool can colour)."""
    if item["type"] not in CLOSED_TYPES:
        return False
    pts = outline_points(item)
    return len(pts) >= 3 and _in_polygon(x, y, pts)


def rdp(points, epsilon):
    """Ramer-Douglas-Peucker: drops points that lie within epsilon of the line."""
    if len(points) < 3 or epsilon <= 0:
        return list(points)
    first, last = points[0], points[-1]
    index, far = 0, 0.0
    for i in range(1, len(points) - 1):
        d = dist_point_segment(points[i][0], points[i][1], first, last)
        if d > far:
            index, far = i, d
    if far <= epsilon:
        return [first, last]
    return rdp(points[:index + 1], epsilon)[:-1] + rdp(points[index:], epsilon)


def chaikin(points, iterations):
    """Corner-cutting smoothing; keeps the first and last point."""
    pts = [tuple(p) for p in points]
    for _ in range(iterations):
        if len(pts) < 3:
            break
        out = [pts[0]]
        for i in range(len(pts) - 1):
            (x0, y0), (x1, y1) = pts[i], pts[i + 1]
            out.append((0.75 * x0 + 0.25 * x1, 0.75 * y0 + 0.25 * y1))
            out.append((0.25 * x0 + 0.75 * x1, 0.25 * y0 + 0.75 * y1))
        out.append(pts[-1])
        pts = out
    return pts


def smooth_stroke(points, smoothness, zoom):
    """Freehand points tidied by the Smoothness slider (0 = as drawn)."""
    pts = [tuple(p) for p in points]
    if smoothness <= 0 or len(pts) < 3:
        return [list(p) for p in pts]
    pts = rdp(pts, smoothness * 0.35 / max(zoom, 0.02))
    pts = chaikin(pts, 0 if smoothness < 3 else (1 if smoothness < 7 else 2))
    return [list(p) for p in pts]
