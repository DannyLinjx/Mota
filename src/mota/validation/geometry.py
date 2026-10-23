"""Independent geometric DRC: clearance, edge, width, holes, courtyards.

Curves are not approximated here; pads are circles or rectangles and tracks are
capsules. Verdicts are never rounded to the routing grid.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from mota.domain.geometry import (
    pad_distance, pad_segment_distance, segment_segment_distance, to_pt,
)
from mota.domain.models import BoardIR, Rect
from mota.domain.units import NM_PER_MM, mm

EPS = 1e-9


@dataclass
class Violation:
    code: str
    message: str
    object_ids: list[str] = field(default_factory=list)
    detail: dict[str, float | str] = field(default_factory=dict)


def _mm(value_nm: int) -> float:
    return value_nm / NM_PER_MM


def _track_segments(board: BoardIR):
    for track in board.tracks:
        for seg in track.segments():
            yield track, tuple(v / NM_PER_MM for v in seg)


def _object_bounds(pad) -> tuple[float, float, float, float]:
    r = max(pad.w_nm, pad.h_nm) / 2 / NM_PER_MM
    cx, cy = pad.x_nm / NM_PER_MM, pad.y_nm / NM_PER_MM
    return (cx - r, cy - r, cx + r, cy + r)


def _pad_on_layer(pad, layer: str) -> bool:
    if pad.pad_type in ("tht", "npth"):
        return True
    return layer in (pad.layers or [])


def check_clearance(board: BoardIR, clearance_mm: float) -> list[Violation]:
    violations: list[Violation] = []
    pads = board.pads
    tracks = list(_track_segments(board))
    for index, a in enumerate(pads):
        for b in pads[index + 1:]:
            if a.net_id == b.net_id and a.net_id is not None:
                continue
            if not (set(a.layers or []) & set(b.layers or [])):
                continue
            distance = pad_distance(a, b)
            if distance + EPS < clearance_mm:
                violations.append(Violation(
                    "COPPER_SHORT" if distance <= EPS else "CLEARANCE",
                    f"Pad {a.component_id}.{a.number} is {distance:.3f} mm from "
                    f"pad {b.component_id}.{b.number}; required {clearance_mm:.2f} mm.",
                    [a.id, b.id], {"distance_mm": round(distance, 4)}))
    for pad in pads:
        for track, seg in tracks:
            if pad.net_id == track.net_id and pad.net_id is not None:
                continue
            if not _pad_on_layer(pad, track.layer):
                continue
            distance = pad_segment_distance(pad, seg) - track.width_nm / 2 / NM_PER_MM
            if distance + EPS < clearance_mm:
                violations.append(Violation(
                    "COPPER_SHORT" if distance <= EPS else "CLEARANCE",
                    f"Track {track.id} is {distance:.3f} mm from "
                    f"pad {pad.component_id}.{pad.number}.",
                    [track.id, pad.id], {"distance_mm": round(distance, 4)}))
    for index, (ta, sa) in enumerate(tracks):
        for tb, sb in tracks[index + 1:]:
            if ta.id == tb.id or ta.layer != tb.layer:
                continue
            if ta.net_id == tb.net_id:
                continue
            distance = segment_segment_distance(sa, sb) - (
                ta.width_nm + tb.width_nm) / 2 / NM_PER_MM
            if distance + EPS < clearance_mm:
                violations.append(Violation(
                    "COPPER_SHORT" if distance <= EPS else "CLEARANCE",
                    f"Track {ta.id} is {distance:.3f} mm from track {tb.id} "
                    f"on {ta.layer}.",
                    [ta.id, tb.id], {"distance_mm": round(distance, 4)}))
    for via in board.vias:
        for pad in pads:
            if via.net_id == pad.net_id and via.net_id is not None:
                continue
            cx, cy = via.x_nm / NM_PER_MM, via.y_nm / NM_PER_MM
            from mota.domain.geometry import point_rect_distance, circle_rect_distance
            if pad.shape == "circle":
                distance = max(0.0, ((cx - pad.x_nm / NM_PER_MM) ** 2
                                     + (cy - pad.y_nm / NM_PER_MM) ** 2) ** 0.5
                               - pad.w_nm / 2 / NM_PER_MM - via.outer_nm / 2 / NM_PER_MM)
            else:
                distance = circle_rect_distance(cx, cy, via.outer_nm / 2 / NM_PER_MM,
                                                pad.rect)
            if distance + EPS < clearance_mm:
                violations.append(Violation(
                    "COPPER_SHORT" if distance <= EPS else "CLEARANCE",
                    f"Via {via.id} is {distance:.3f} mm from pad {pad.id}.",
                    [via.id, pad.id], {"distance_mm": round(distance, 4)}))
        for track, seg in tracks:
            if via.net_id == track.net_id and via.net_id is not None:
                continue
            from mota.domain.geometry import point_segment_distance
            distance = point_segment_distance(via.x_nm / NM_PER_MM, via.y_nm / NM_PER_MM,
                                              *seg) - via.outer_nm / 2 / NM_PER_MM \
                - track.width_nm / 2 / NM_PER_MM
            if distance + EPS < clearance_mm:
                violations.append(Violation(
                    "COPPER_SHORT" if distance <= EPS else "CLEARANCE",
                    f"Via {via.id} is {distance:.3f} mm from track {track.id}.",
                    [via.id, track.id], {"distance_mm": round(distance, 4)}))
    for index, via in enumerate(board.vias):
        for other in board.vias[index + 1:]:
            if via.net_id == other.net_id:
                continue
            distance = ((via.x_nm - other.x_nm) ** 2
                        + (via.y_nm - other.y_nm) ** 2) ** 0.5 / NM_PER_MM \
                - (via.outer_nm + other.outer_nm) / 2 / NM_PER_MM
            if distance + EPS < clearance_mm:
                violations.append(Violation(
                    "COPPER_SHORT" if distance <= EPS else "CLEARANCE",
                    f"Vias {via.id} and {other.id} are {distance:.3f} mm apart.",
                    [via.id, other.id], {"distance_mm": round(distance, 4)}))
    return violations


def check_board_edge(board: BoardIR, edge_mm: float) -> list[Violation]:
    violations: list[Violation] = []
    width_mm = _mm(board.width_nm)
    height_mm = _mm(board.height_nm)
    for pad in board.pads:
        x1, y1, x2, y2 = _object_bounds(pad)
        if x1 < edge_mm - EPS or y1 < edge_mm - EPS or x2 > width_mm - edge_mm + EPS \
                or y2 > height_mm - edge_mm + EPS:
            violations.append(Violation(
                "BOARD_EDGE", f"Pad {pad.component_id}.{pad.number} crosses the board edge keep-out.",
                [pad.id], {"x_mm": round(pad.x_nm / NM_PER_MM, 3),
                           "y_mm": round(pad.y_nm / NM_PER_MM, 3)}))
    for track, seg in _track_segments(board):
        half = track.width_nm / 2 / NM_PER_MM
        if (min(seg[0], seg[2]) - half < edge_mm - EPS
                or min(seg[1], seg[3]) - half < edge_mm - EPS
                or max(seg[0], seg[2]) + half > width_mm - edge_mm + EPS
                or max(seg[1], seg[3]) + half > height_mm - edge_mm + EPS):
            violations.append(Violation(
                "BOARD_EDGE", f"Track {track.id} crosses the board edge keep-out.",
                [track.id], {}))
    for via in board.vias:
        r = via.outer_nm / 2 / NM_PER_MM
        cx, cy = via.x_nm / NM_PER_MM, via.y_nm / NM_PER_MM
        if (cx - r < edge_mm - EPS or cy - r < edge_mm - EPS
                or cx + r > width_mm - edge_mm + EPS
                or cy + r > height_mm - edge_mm + EPS):
            violations.append(Violation("BOARD_EDGE", f"Via {via.id} crosses the board edge keep-out.",
                                        [via.id], {}))
    return violations


def check_widths(board: BoardIR, min_signal_mm: float, min_power_mm: float,
                 net_classes: dict[str, str]) -> list[Violation]:
    violations: list[Violation] = []
    for track in board.tracks:
        required = min_power_mm if net_classes.get(track.net_id) in ("power", "ground") \
            else min_signal_mm
        if _mm(track.width_nm) + EPS < required:
            violations.append(Violation(
                "TRACK_WIDTH",
                f"Track {track.id} is {_mm(track.width_nm):.3f} mm wide; "
                f"{required:.2f} mm is required for its net class.",
                [track.id]))
    return violations


def check_vias(board: BoardIR, outer_mm: float, drill_mm: float,
               min_ring_mm: float) -> list[Violation]:
    violations: list[Violation] = []
    for via in board.vias:
        outer = _mm(via.outer_nm)
        drill = _mm(via.drill_nm)
        if outer + EPS < outer_mm or drill + EPS < drill_mm:
            violations.append(Violation("VIA_SIZE",
                                        f"Via {via.id} is {outer:.2f}/{drill:.2f} mm; "
                                        f"minimum {outer_mm:.2f}/{drill_mm:.2f} mm.",
                                        [via.id]))
        if (outer - drill) / 2 + EPS < min_ring_mm:
            violations.append(Violation("VIA_ANNULAR_RING",
                                        f"Via {via.id} annular ring is "
                                        f"{(outer - drill) / 2:.3f} mm.",
                                        [via.id]))
    return violations


def check_holes(board: BoardIR, hole_clearance_mm: float) -> list[Violation]:
    """NPTH/mechanical holes are barriers on both copper layers."""
    violations: list[Violation] = []
    for hole in board.holes:
        cx, cy = hole.x_nm / NM_PER_MM, hole.y_nm / NM_PER_MM
        r = hole.drill_nm / 2 / NM_PER_MM
        kind = "NPTH" if not hole.plated else "PTH"

        class _CirclePad:
            pass

        circle_pad = _CirclePad()
        circle_pad.x_nm = hole.x_nm
        circle_pad.y_nm = hole.y_nm
        circle_pad.w_nm = hole.drill_nm
        circle_pad.h_nm = hole.drill_nm
        circle_pad.shape = "circle"
        for pad in board.pads:
            if hole.plated and pad.net_id:
                continue
            distance = pad_distance(pad, circle_pad)
            if distance + EPS < hole_clearance_mm:
                violations.append(Violation(
                    "HOLE_CLEARANCE",
                    f"Copper pad {pad.component_id}.{pad.number} is {distance:.3f} mm "
                    f"from {kind} hole {hole.id}; required {hole_clearance_mm:.2f} mm.",
                    [pad.id, hole.id], {"distance_mm": round(distance, 4)}))
        for track, seg in _track_segments(board):
            distance = _segment_circle_distance(seg, cx, cy) - r - track.width_nm / 2 / NM_PER_MM
            if distance + EPS < hole_clearance_mm:
                violations.append(Violation(
                    "HOLE_CLEARANCE",
                    f"Track {track.id} is {distance:.3f} mm from {kind} hole {hole.id}.",
                    [track.id, hole.id], {"distance_mm": round(distance, 4)}))
        for via in board.vias:
            distance = ((via.x_nm / NM_PER_MM - cx) ** 2
                        + (via.y_nm / NM_PER_MM - cy) ** 2) ** 0.5 \
                - r - via.outer_nm / 2 / NM_PER_MM
            if distance + EPS < hole_clearance_mm:
                violations.append(Violation(
                    "HOLE_CLEARANCE", f"Via {via.id} is {distance:.3f} mm from hole {hole.id}.",
                    [via.id, hole.id], {"distance_mm": round(distance, 4)}))
    return violations


def _segment_circle_distance(seg, cx: float, cy: float) -> float:
    from mota.domain.geometry import point_segment_distance
    return point_segment_distance(cx, cy, *seg)


def check_courtyards(board: BoardIR, gap_mm: float = 0.0) -> list[Violation]:
    violations: list[Violation] = []
    fps = board.footprints
    for index, a in enumerate(fps):
        for b in fps[index + 1:]:
            ra, rb = a.courtyard, b.courtyard
            if (ra.x2 + mm(gap_mm) <= rb.x_nm or rb.x2 + mm(gap_mm) <= ra.x_nm
                    or ra.y2 + mm(gap_mm) <= rb.y_nm or rb.y2 + mm(gap_mm) <= ra.y_nm):
                continue
            violations.append(Violation(
                "COURTYARD_OVERLAP",
                f"Courtyards of {a.reference} and {b.reference} overlap.",
                [a.component_id, b.component_id]))
    for fp in fps:
        c = fp.courtyard
        if (c.x_nm < 0 or c.y_nm < 0 or c.x2 > board.width_nm or c.y2 > board.height_nm):
            violations.append(Violation(
                "COURTYARD_EDGE", f"Courtyard of {fp.reference} leaves the board.",
                [fp.component_id]))
    return violations


def extract_copper(begin: int, end: int) -> tuple[list, list]:
    return [], []
