"""Board construction pipeline: outline, footprints, pads, routing."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from mota.catalog.templates import TemplateRecipe
from mota.domain.models import (
    BoardIR, CircuitIR, DesignVersion, RequirementSpec, SchematicIR, ValidationReport,
)
from mota.domain.units import mm
from mota.generation.modules import build_circuit
from mota.generation.placement import (
    board_holes, build_footprints, pads_for, place_components,
)
from mota.generation.routing import (
    Cancelled, RouteConstraints, route_board, route_budget_seconds,
)
from mota.generation.schematic import generate_schematic
from mota.rules import load_rule_profile
from mota.validation.reports import validate_all


@dataclass
class BuildResult:
    requirement: RequirementSpec
    circuit: CircuitIR
    schematic: SchematicIR
    board: BoardIR
    checks: ValidationReport
    moved_components: list[str]
    unrouted_nets: list[str]


def build_board_ir(circuit: CircuitIR, recipe: TemplateRecipe | None, width_nm: int,
                   height_nm: int, *, move_user: bool = False,
                   outline: list[list[int]] | None = None,
                   thickness_nm: int | None = None
                   ) -> tuple[BoardIR, list[str]]:
    holes = board_holes(recipe)
    circuit, moved = place_components(circuit, recipe, width_nm, height_nm, holes,
                                      move_user=move_user)
    if outline is None:
        outline = ([[0, 0], [width_nm, 0], [width_nm, height_nm], [0, height_nm],
                    [0, 0]] if width_nm > 0 and height_nm > 0 else [])
    if thickness_nm is None:
        thickness_nm = (mm(float(recipe.board.get("thickness_mm", 1.6)))
                        if recipe else mm(1.6))
    board = BoardIR(
        outline=[list(point) for point in outline],
        width_nm=width_nm, height_nm=height_nm,
        thickness_nm=thickness_nm,
        layer_count=2, footprints=build_footprints(circuit), holes=holes)
    pads = []
    for comp in circuit.components:
        pads.extend(pads_for(circuit, comp.id))
    board = board.model_copy(update={"pads": pads})
    return board, moved


def run_build(recipe: TemplateRecipe, parameters: dict,
              *, version: DesignVersion | None = None,
              project_id: str = "",
              cancel: Callable[[], bool] | None = None,
              route_budget: float | None = None) -> BuildResult:
    circuit, requirement = build_circuit(recipe, parameters)
    width_nm = mm(float(requirement.board_size["width_mm"]))
    height_nm = mm(float(requirement.board_size["height_mm"]))
    board, moved = build_board_ir(circuit, recipe, width_nm, height_nm)
    schematic = generate_schematic(circuit, title=recipe.title)
    net_classes = {net.id: net.net_class for net in circuit.nets}
    profile = load_rule_profile(recipe.validation_profile)
    board, unrouted = route_board(
        board, RouteConstraints.from_profile(profile), net_classes, cancel=cancel,
        time_budget=(route_budget if route_budget is not None
                     else profile.budget_seconds))
    design_version = version or DesignVersion.of_draft("draft_preview", 0)
    checks = validate_all(project_id, design_version, requirement, circuit, schematic,
                          board, recipe)
    return BuildResult(requirement=requirement, circuit=circuit, schematic=schematic,
                       board=board, checks=checks, moved_components=moved,
                       unrouted_nets=unrouted)


def rebuild(circuit: CircuitIR, requirement: RequirementSpec,
            recipe: TemplateRecipe | None, *,
            reroute: bool = True, cancel: Callable[[], bool] | None = None,
            base_board: BoardIR | None = None
            ) -> tuple[SchematicIR, BoardIR, list[str]]:
    # The requirement is the authority for board size: set_board_size writes it.
    # Reusing base_board dimensions here silently discarded that edit.
    size = requirement.board_size or {}
    width_mm = size.get("width_mm")
    height_mm = size.get("height_mm")
    width_nm = mm(float(width_mm)) if width_mm else (
        base_board.width_nm if base_board and base_board.width_nm else mm(60.0))
    height_nm = mm(float(height_mm)) if height_mm else (
        base_board.height_nm if base_board and base_board.height_nm else mm(40.0))
    same_size = (base_board is not None
                 and base_board.width_nm == width_nm
                 and base_board.height_nm == height_nm)
    outline = list(base_board.outline) if same_size and base_board.outline else None
    thickness_nm = base_board.thickness_nm if base_board else None
    board, _ = build_board_ir(circuit, recipe, width_nm, height_nm,
                              move_user=True, outline=outline,
                              thickness_nm=thickness_nm)
    unrouted: list[str] = []
    if reroute:
        net_classes = {net.id: net.net_class for net in circuit.nets}
        profile = load_rule_profile(circuit.rule_profile_id)
        board, unrouted = route_board(
            board, RouteConstraints.from_profile(profile), net_classes,
            cancel=cancel, time_budget=profile.budget_seconds)
    else:
        unrouted = list(board.unrouted_nets)
    schematic = generate_schematic(
        circuit, title=recipe.title if recipe else "Mota schematic")
    return schematic, board, unrouted
