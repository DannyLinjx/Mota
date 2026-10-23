"""Deterministic module-rule placement with collision legalisation.

The template supplies normalised anchors; the engine converts them to board
coordinates, then resolves courtyard / mounting-hole / board-edge conflicts on
a 1.27 mm candidate lattice. User positions are never overwritten.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from mota.catalog import builtin
from mota.catalog.templates import TemplateRecipe
from mota.circuit.parts import resolve_footprint, resolve_part
from mota.domain.geometry import rotate_nm, to_pt
from mota.domain.models import (
    BoardIR, BoardPad, CircuitIR, FootprintInstance, Hole, Placement, Rect,
)
from mota.domain.units import mm
from mota.errors import ConstraintConflict, NotFound

EDGE_CLEARANCE_MM = 0.5
COURTYARD_GAP_MM = 0.4
HOLE_KEEPOUT_MM = 1.0
PLACEMENT_STEP_MM = 1.27


@dataclass
class Placed:
    component_id: str
    reference: str
    courtyard: Rect


def local_pad_positions(component, footprint, pad) -> tuple[int, int]:
    x, y = pad.x_nm, pad.y_nm
    if component.placement and component.placement.side == "bottom":
        x = -x
    if component.placement:
        x, y = rotate_nm(x, y, component.placement.rotation_deg)
    return x, y


def pads_for(circuit: CircuitIR, component_id: str) -> list[BoardPad]:
    comp = circuit.component(component_id)
    if comp.placement is None or not comp.on_board or not comp.footprint_id:
        return []
    try:
        footprint = resolve_footprint(comp.footprint_id)
    except NotFound:
        return []
    pads: list[BoardPad] = []
    pin_for_pad: dict[str, str] = {}
    for pin_number, pad_numbers in comp.pin_pad_map.items():
        for pad_number in pad_numbers:
            pin_for_pad.setdefault(pad_number, pin_number)
    pin_net: dict[str, str] = {}
    for net in circuit.nets:
        for token in net.pins:
            cid, _, pin = token.partition(":")
            if cid == comp.id:
                pin_net[pin] = net.id
    for pad in footprint.pads:
        dx, dy = local_pad_positions(comp, footprint, pad)
        x = comp.placement.x_nm + dx
        y = comp.placement.y_nm + dy
        pin = pin_for_pad.get(pad.number)
        net = pin_net.get(pin) if pin else None
        pads.append(BoardPad(
            id=f"{comp.id}_pad_{pad.number}",
            component_id=comp.id, number=pad.number, net_id=net,
            x_nm=x, y_nm=y, w_nm=pad.w_nm, h_nm=pad.h_nm, shape=pad.shape,
            drill_nm=pad.drill_nm, pad_type=pad.pad_type,
            layers=["B.Cu"] if (comp.placement.side == "bottom" and pad.pad_type == "smd")
            else list(pad.layers)))
    return pads


def courtyard_abs(component, footprint) -> Rect:
    assert component.placement is not None
    corners = [
        (footprint.courtyard.x_nm, footprint.courtyard.y_nm),
        (footprint.courtyard.x2, footprint.courtyard.y_nm),
        (footprint.courtyard.x2, footprint.courtyard.y2),
        (footprint.courtyard.x_nm, footprint.courtyard.y2),
    ]
    pts = []
    for x, y in corners:
        if component.placement.side == "bottom":
            x = -x
        rx, ry = rotate_nm(x, y, component.placement.rotation_deg)
        pts.append((component.placement.x_nm + rx, component.placement.y_nm + ry))
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    return Rect(x_nm=min(xs), y_nm=min(ys), w_nm=max(xs) - min(xs), h_nm=max(ys) - min(ys))


def _rect_gap(a: Rect, b: Rect) -> float:
    ax1, ay1 = to_pt(a.x_nm, a.y_nm)
    ax2, ay2 = to_pt(a.x2, a.y2)
    bx1, by1 = to_pt(b.x_nm, b.y_nm)
    bx2, by2 = to_pt(b.x2, b.y2)
    dx = max(bx1 - ax2, ax1 - bx2)
    dy = max(by1 - ay2, ay1 - by2)
    if dx >= 0 or dy >= 0:
        return max(dx, dy)
    return -math.hypot(min(ax2 - bx1, bx2 - ax1), min(ay2 - by1, by2 - ay1))


def _inside_board(rect: Rect, width_nm: int, height_nm: int) -> bool:
    margin = mm(EDGE_CLEARANCE_MM)
    return (rect.x_nm >= margin and rect.y_nm >= margin
            and rect.x2 <= width_nm - margin and rect.y2 <= height_nm - margin)


def _hole_clear(rect: Rect, holes: list[Hole]) -> bool:
    for hole in holes:
        cx = hole.x_nm / 1e6
        cy = hole.y_nm / 1e6
        r = hole.drill_nm / 1e6 / 2 + HOLE_KEEPOUT_MM
        nearest_x = min(max(cx, rect.x_nm / 1e6), rect.x2 / 1e6)
        nearest_y = min(max(cy, rect.y_nm / 1e6), rect.y2 / 1e6)
        if math.hypot(cx - nearest_x, cy - nearest_y) < r:
            return False
    return True


def _priority(part_category: str) -> int:
    if part_category == "connector":
        return 0
    if part_category in ("regulator", "timer", "sensor"):
        return 1
    return 2


def place_components(circuit: CircuitIR, recipe: TemplateRecipe | None, width_nm: int,
                     height_nm: int, holes: list[Hole], *, move_user: bool = False
                     ) -> tuple[CircuitIR, list[str]]:
    margin = mm(6.0)
    rules = {row["ref"]: row for row in (recipe.placement if recipe else [])}

    def category(component) -> int:
        try:
            return _priority(resolve_part(component.part_id).category)
        except NotFound:
            return 2

    ordered = sorted(circuit.components,
                     key=lambda c: (category(c), c.reference))
    placed: list[Placed] = []
    moved: list[str] = []
    for comp in ordered:
        if not comp.on_board or not comp.footprint_id:
            continue
        try:
            footprint = resolve_footprint(comp.footprint_id)
        except NotFound:
            continue
        if comp.placement and comp.placement_origin == "user":
            rect = courtyard_abs(comp, footprint)
            placed.append(Placed(comp.id, comp.reference, rect))
            continue
        rule = rules.get(comp.reference, {})
        fx = float(rule.get("fx", 0.5))
        fy = float(rule.get("fy", 0.5))
        rotation = int(rule.get("rotation", comp.placement.rotation_deg if comp.placement else 0))
        anchor_x = int(margin + fx * max(0, width_nm - 2 * margin))
        anchor_y = int(margin + fy * max(0, height_nm - 2 * margin))
        as_rect = _courtyard_for(comp, footprint, anchor_x, anchor_y, rotation)
        if _legal(as_rect, placed, holes, width_nm, height_nm):
            comp.placement = Placement(x_nm=anchor_x, y_nm=anchor_y,
                                       rotation_deg=rotation, side="top")
            comp.placement_origin = "template"
            placed.append(Placed(comp.id, comp.reference, as_rect))
            continue
        candidate = _find_candidate(circuit, comp, footprint, anchor_x, anchor_y, rotation,
                                    placed, holes, width_nm, height_nm)
        if candidate is None:
            raise ConstraintConflict(
                f"Could not place {comp.reference} without overlapping another "
                f"component or a mounting hole on a {width_nm / 1e6:.1f} x "
                f"{height_nm / 1e6:.1f} mm board.",
                data={"component_id": comp.id, "reference": comp.reference,
                      "conflicts": [p.reference for p in placed]})
        x, y, rot = candidate
        comp.placement = Placement(x_nm=x, y_nm=y, rotation_deg=rot, side="top")
        comp.placement_origin = "template"
        placed.append(Placed(comp.id, comp.reference, _courtyard_for(comp, footprint, x, y, rot)))
        moved.append(comp.reference)
    return circuit, moved


def _courtyard_for(comp, footprint, x: int, y: int, rotation: int) -> Rect:
    saved = comp.placement
    comp.placement = Placement(x_nm=x, y_nm=y, rotation_deg=rotation, side="top")
    rect = courtyard_abs(comp, footprint)
    comp.placement = saved
    return rect


def _legal(rect: Rect, placed: list[Placed], holes: list[Hole], width_nm: int,
           height_nm: int) -> bool:
    if not _inside_board(rect, width_nm, height_nm) or not _hole_clear(rect, holes):
        return False
    gap_nm = mm(COURTYARD_GAP_MM)
    for other in placed:
        if (rect.x2 + gap_nm <= other.courtyard.x_nm
                or other.courtyard.x2 + gap_nm <= rect.x_nm
                or rect.y2 + gap_nm <= other.courtyard.y_nm
                or other.courtyard.y2 + gap_nm <= rect.y_nm):
            continue
        return False
    return True


def _find_candidate(circuit, comp, footprint, anchor_x, anchor_y, rotation,
                    placed, holes, width_nm, height_nm):
    step = mm(PLACEMENT_STEP_MM)
    margin = mm(EDGE_CLEARANCE_MM)
    xs = range(margin + step // 2, max(margin + step // 2, width_nm - margin), step)
    ys = range(margin + step // 2, max(margin + step // 2, height_nm - margin), step)
    best = None
    best_cost = float("inf")
    for x in xs:
        for y in ys:
            rect = _courtyard_for(comp, footprint, x, y, rotation)
            if not _legal(rect, placed, holes, width_nm, height_nm):
                continue
            cost = math.hypot((x - anchor_x) / 1e6, (y - anchor_y) / 1e6)
            cost += 0.02 * _net_pull(circuit, comp, x, y, placed)
            if cost < best_cost:
                best_cost = cost
                best = (x, y, rotation)
    return best


def _net_pull(circuit: CircuitIR, comp, x: int, y: int, placed: list[Placed]) -> float:
    centers = {p.reference: ((p.courtyard.x_nm + p.courtyard.w_nm / 2),
                             (p.courtyard.y_nm + p.courtyard.h_nm / 2)) for p in placed}
    total = 0.0
    for net in circuit.nets:
        tokens = [t for t in net.pins if t.startswith(comp.id + ":")]
        if not tokens:
            continue
        for other in net.pins:
            cid, _, _ = other.partition(":")
            c = next((c for c in circuit.components if c.id == cid), None)
            if c is None or c.reference == comp.reference or c.reference not in centers:
                continue
            ox, oy = centers[c.reference]
            total += math.hypot((x - ox) / 1e6, (y - oy) / 1e6)
    return total


def build_footprints(circuit: CircuitIR) -> list[FootprintInstance]:
    out: list[FootprintInstance] = []
    for comp in circuit.components:
        if comp.placement is None or not comp.on_board or not comp.footprint_id:
            continue
        try:
            footprint = resolve_footprint(comp.footprint_id)
        except NotFound:
            continue
        out.append(FootprintInstance(
            component_id=comp.id, footprint_id=comp.footprint_id,
            reference=comp.reference, x_nm=comp.placement.x_nm,
            y_nm=comp.placement.y_nm, rotation_deg=comp.placement.rotation_deg,
            side=comp.placement.side, courtyard=courtyard_abs(comp, footprint),
            courtyard_approximate=footprint.courtyard_approximate,
            body_height_mm=footprint.body_height_mm,
            model_fidelity=(footprint.model.fidelity if footprint.model else "missing")))
    return out


def board_holes(recipe: TemplateRecipe | None) -> list[Hole]:
    holes: list[Hole] = []
    for index, spec in enumerate((recipe.board.get("mounting_holes", []) or [])
                                 if recipe else []):
        holes.append(Hole(id=f"hole_mount_{index + 1}", x_nm=mm(spec["x_mm"]),
                          y_nm=mm(spec["y_mm"]), drill_nm=mm(spec["drill_mm"]),
                          plated=False, purpose="mounting"))
    return holes
