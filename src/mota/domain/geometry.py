"""Exact-ish planar geometry in nanometres (drawing and DRC).

All stored coordinates are integers. Distances are computed in float mm for
comparison only; thresholds are never rounded to a grid before deciding a
clearance verdict.
"""

from __future__ import annotations

import math
from typing import Iterable, Sequence

from mota.domain.models import PadShape, Rect
from mota.domain.units import NM_PER_MM

Point = tuple[float, float]


def to_pt(x_nm: int, y_nm: int) -> Point:
    return (x_nm / NM_PER_MM, y_nm / NM_PER_MM)


def rotate_nm(x_nm: int, y_nm: int, degrees: int) -> tuple[int, int]:
    deg = degrees % 360
    if deg == 0:
        return x_nm, y_nm
    if deg == 90:
        return -y_nm, x_nm
    if deg == 180:
        return -x_nm, -y_nm
    if deg == 270:
        return y_nm, -x_nm
    raise ValueError("only 90-degree rotations are supported")


def rotate_point(x: float, y: float, degrees: int) -> Point:
    deg = degrees % 360
    if deg == 0:
        return x, y
    if deg == 90:
        return -y, x
    if deg == 180:
        return -x, -y
    if deg == 270:
        return y, -x
    rad = math.radians(deg)
    return (x * math.cos(rad) - y * math.sin(rad),
            x * math.sin(rad) + y * math.cos(rad))


def point_segment_distance(px: float, py: float, x1: float, y1: float,
                           x2: float, y2: float) -> float:
    dx, dy = x2 - x1, y2 - y1
    if dx == 0 and dy == 0:
        return math.hypot(px - x1, py - y1)
    t = ((px - x1) * dx + (py - y1) * dy) / (dx * dx + dy * dy)
    t = max(0.0, min(1.0, t))
    return math.hypot(px - (x1 + t * dx), py - (y1 + t * dy))


def segments_intersect(a: Sequence[float], b: Sequence[float]) -> bool:
    x1, y1, x2, y2 = a
    x3, y3, x4, y4 = b

    def orient(ax, ay, bx, by, cx, cy):
        value = (bx - ax) * (cy - ay) - (by - ay) * (cx - ax)
        if abs(value) < 1e-12:
            return 0
        return 1 if value > 0 else -1

    o1 = orient(x1, y1, x2, y2, x3, y3)
    o2 = orient(x1, y1, x2, y2, x4, y4)
    o3 = orient(x3, y3, x4, y4, x1, y1)
    o4 = orient(x3, y3, x4, y4, x2, y2)
    if o1 != o2 and o3 != o4:
        return True

    def on_seg(ax, ay, bx, by, cx, cy):
        return (min(ax, bx) - 1e-9 <= cx <= max(ax, bx) + 1e-9
                and min(ay, by) - 1e-9 <= cy <= max(ay, by) + 1e-9)

    return ((o1 == 0 and on_seg(x1, y1, x2, y2, x3, y3))
            or (o2 == 0 and on_seg(x1, y1, x2, y2, x4, y4))
            or (o3 == 0 and on_seg(x3, y3, x4, y4, x1, y1))
            or (o4 == 0 and on_seg(x3, y3, x4, y4, x2, y2)))


def segment_segment_distance(a: Sequence[float], b: Sequence[float]) -> float:
    if segments_intersect(a, b):
        return 0.0
    return min(
        point_segment_distance(a[0], a[1], *b),
        point_segment_distance(a[2], a[3], *b),
        point_segment_distance(b[0], b[1], *a),
        point_segment_distance(b[2], b[3], *a),
    )


def point_rect_distance(px: float, py: float, rect: Rect) -> float:
    x1, y1 = to_pt(rect.x_nm, rect.y_nm)
    x2, y2 = to_pt(rect.x2, rect.y2)
    dx = max(x1 - px, 0.0, px - x2)
    dy = max(y1 - py, 0.0, py - y2)
    return math.hypot(dx, dy)


def rect_rect_distance(a: Rect, b: Rect) -> float:
    ax1, ay1 = to_pt(a.x_nm, a.y_nm)
    ax2, ay2 = to_pt(a.x2, a.y2)
    bx1, by1 = to_pt(b.x_nm, b.y_nm)
    bx2, by2 = to_pt(b.x2, b.y2)
    dx = max(bx1 - ax2, ax1 - bx2, 0.0)
    dy = max(by1 - ay2, ay1 - by2, 0.0)
    return math.hypot(dx, dy)


def segment_rect_distance(seg: Sequence[float], rect: Rect) -> float:
    x1, y1 = to_pt(rect.x_nm, rect.y_nm)
    x2, y2 = to_pt(rect.x2, rect.y2)
    edges = [(x1, y1, x2, y1), (x2, y1, x2, y2), (x2, y2, x1, y2), (x1, y2, x1, y1)]
    for edge in edges:
        if segments_intersect(seg, edge):
            return 0.0
    # Inside rectangle
    if x1 <= seg[0] <= x2 and y1 <= seg[1] <= y2:
        return 0.0
    return min(segment_segment_distance(seg, edge) for edge in edges)


def circle_rect_distance(cx: float, cy: float, radius: float, rect: Rect) -> float:
    return max(0.0, point_rect_distance(cx, cy, rect) - radius)


def pad_geometry(pad, x_nm: int | None = None, y_nm: int | None = None,
                 w_nm: int | None = None, h_nm: int | None = None):
    """Return ('rect', Rect) or ('circle', (cx, cy, r_mm))."""
    px = pad.x_nm if x_nm is None else x_nm
    py = pad.y_nm if y_nm is None else y_nm
    pw = pad.w_nm if w_nm is None else w_nm
    ph = pad.h_nm if h_nm is None else h_nm
    shape: PadShape = getattr(pad, "shape", "rect")
    if shape == "circle":
        return ("circle", (px / NM_PER_MM, py / NM_PER_MM, (min(pw, ph) / NM_PER_MM) / 2.0))
    return ("rect", Rect(x_nm=px - pw // 2, y_nm=py - ph // 2, w_nm=pw, h_nm=ph))


def pad_distance(pad_a, pad_b) -> float:
    """Copper-shape distance between two pads (0 when overlapping)."""
    kind_a, geo_a = pad_geometry(pad_a)
    kind_b, geo_b = pad_geometry(pad_b)
    if kind_a == "circle" and kind_b == "circle":
        ax, ay, ar = geo_a
        bx, by, br = geo_b
        return max(0.0, math.hypot(ax - bx, ay - by) - ar - br)
    if kind_a == "circle":
        cx, cy, r = geo_a
        return circle_rect_distance(cx, cy, r, geo_b)
    if kind_b == "circle":
        cx, cy, r = geo_b
        return circle_rect_distance(cx, cy, r, geo_a)
    return rect_rect_distance(geo_a, geo_b)


def pad_segment_distance(pad, seg: Sequence[float]) -> float:
    kind, geo = pad_geometry(pad)
    if kind == "circle":
        cx, cy, r = geo
        return max(0.0, point_segment_distance(cx, cy, *seg) - r)
    return segment_rect_distance(seg, geo)


def pad_contains(pad, x_nm: int, y_nm: int, tolerance_nm: int = 0) -> bool:
    kind, geo = pad_geometry(pad)
    px = x_nm / NM_PER_MM
    py = y_nm / NM_PER_MM
    if kind == "circle":
        cx, cy, r = geo
        return math.hypot(px - cx, py - cy) <= r + tolerance_nm / NM_PER_MM
    rect: Rect = geo
    tol = tolerance_nm
    return (rect.x_nm - tol <= x_nm <= rect.x2 + tol
            and rect.y_nm - tol <= y_nm <= rect.y2 + tol)


def segment_bounds(seg: Sequence[float]) -> tuple[float, float, float, float]:
    return (min(seg[0], seg[2]), min(seg[1], seg[3]),
            max(seg[0], seg[2]), max(seg[1], seg[3]))


def bbox_gap(a: tuple[float, float, float, float],
             b: tuple[float, float, float, float]) -> float:
    dx = max(b[0] - a[2], a[0] - b[2], 0.0)
    dy = max(b[1] - a[3], a[1] - b[3], 0.0)
    return math.hypot(dx, dy)


class UnionFind:
    def __init__(self) -> None:
        self.parent: dict[str, str] = {}

    def add(self, item: str) -> None:
        self.parent.setdefault(item, item)

    def find(self, item: str) -> str:
        self.add(item)
        root = item
        while self.parent[root] != root:
            root = self.parent[root]
        while self.parent[item] != root:
            self.parent[item], item = root, self.parent[item]
        return root

    def union(self, a: str, b: str) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.parent[rb] = ra

    def groups(self) -> dict[str, list[str]]:
        out: dict[str, list[str]] = {}
        for item in self.parent:
            out.setdefault(self.find(item), []).append(item)
        return out
