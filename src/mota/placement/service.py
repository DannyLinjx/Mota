"""Placement service: frozen CircuitIR/BoardIR -> native placement patch.

The native solver owns candidate generation, legalisation, hard constraints and
the bounded search. This service only projects the typed IR and maps the
result; it never relaxes a hard constraint to improve wirelength.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import Field

from mota.circuit.parts import resolve_part
from mota.domain.models import (
    BoardIR, CircuitIR, DTO, DesignVersion, Placement,
)
from mota.engine_v2.contracts import JobBudget
from mota.errors import MotaError, NotFound
from mota.jobs.contracts import run_native_job


class PlacementConstraintError(MotaError):
    code = "PLACEMENT_CONSTRAINTS_INVALID"


class PlacementMove(DTO):
    component_id: str
    x_nm: int
    y_nm: int
    rotation_deg: int = 0
    fixed: bool = False


class CongestionSummary(DTO):
    columns: int = 0
    rows: int = 0
    cell_nm: int = 0
    max_load: float = 0.0
    mean_load: float = 0.0
    hot_cells: list[dict[str, Any]] = Field(default_factory=list)


class PlacementOutcome(DTO):
    status: Literal["SUCCEEDED", "PARTIAL", "UNKNOWN", "INFEASIBLE"]
    patch: list[PlacementMove] = Field(default_factory=list)
    unplaced_components: list[str] = Field(default_factory=list)
    hard_violations: list[str] = Field(default_factory=list)
    congestion: CongestionSummary = Field(default_factory=CongestionSummary)
    wirelength_nm: float = 0.0
    escape_pressure: float = 0.0
    moves: int = 0
    search_nodes: int = 0
    search_exhausted: bool = True
    seed: int = 0
    semantic_hash: str = ""
    diagnostics: list[str] = Field(default_factory=list)
    native_status: str = ""
    metrics: dict[str, Any] = Field(default_factory=dict)


DEFAULT_RULES = {
    "grid_nm": 1_270_000,
    "edge_clearance_nm": 500_000,
    "courtyard_gap_nm": 400_000,
    "decoupling_max_nm": 3_000_000,
    "congestion_cell_nm": 2_000_000,
    "wirelength_weight": 1.0,
    "congestion_weight": 1.0,
    "escape_weight": 0.5,
    "module_weight": 0.5,
    "move_weight": 0.1,
    "local_rounds": 4,
    "max_search_nodes": 20_000,
    "max_candidates_per_component": 2_000,
}


def rules_from_compiled(compiled) -> dict[str, Any]:
    """Derive placement hard rules from C5 CompiledConstraints."""

    if compiled is None:
        return dict(DEFAULT_RULES)
    status = getattr(compiled, "status", "SAT")
    if status in {"INFEASIBLE", "INVALID", "UNKNOWN"}:
        raise PlacementConstraintError(
            f"compiled constraints are {status}; placement cannot start.",
            data={"status": status})
    clearance = 0
    edge = 0
    for constraint in getattr(compiled, "constraints", []):
        if not constraint.hard:
            continue
        minimum = constraint.effective.get("min")
        if minimum is None:
            continue
        if constraint.kind == "clearance":
            clearance = max(clearance, int(minimum.canonical_value))
        elif constraint.kind == "board_edge_clearance":
            edge = max(edge, int(minimum.canonical_value))
    rules = dict(DEFAULT_RULES)
    rules["courtyard_gap_nm"] = max(rules["courtyard_gap_nm"], clearance)
    rules["edge_clearance_nm"] = max(rules["edge_clearance_nm"], edge)
    return rules


def place_board(project_id: str, version: DesignVersion, circuit: CircuitIR,
                board: BoardIR, *, rules: dict[str, Any] | None = None,
                keepouts: list[dict[str, Any]] | None = None,
                module_regions: list[dict[str, Any]] | None = None,
                decoupling: dict[str, list[str]] | None = None,
                compiled=None, seed: int = 0,
                binary: str | None = None,
                budget: JobBudget | None = None) -> PlacementOutcome:
    effective_rules = rules_from_compiled(compiled)
    if rules:
        effective_rules.update(rules)
    effective_rules["seed"] = seed
    placement = _placement_spec(circuit, board, effective_rules, keepouts or [],
                                module_regions or [], decoupling or {})
    document = {
        "schema_version": 2,
        "input_id": f"place-{project_id}-{version.key}",
        "board": _board_spec(board),
        "placement": placement,
        "queries": [{"id": "place", "kind": "place"}],
    }
    native = run_native_job(document, budget=budget or JobBudget(
        wall_seconds=30.0, max_expansions=100_000), binary=binary)
    if native.status != "SUCCEEDED" or not native.results:
        raise PlacementConstraintError(
            "native placement did not complete",
            data={"native_status": native.status,
                  "diagnostics": native.diagnostics})
    result = native.results[0]
    if result.get("status") != "OK":
        raise PlacementConstraintError(
            str(result.get("error", {})), data={"result": result})
    payload = dict(result.get("placement", {}))
    congestion = CongestionSummary.model_validate(
        payload.get("congestion", {}))
    return PlacementOutcome(
        status=payload.get("status", "UNKNOWN"),  # type: ignore[arg-type]
        patch=[PlacementMove.model_validate(item)
               for item in payload.get("patch", [])],
        unplaced_components=list(payload.get("unplaced_components", [])),
        hard_violations=list(payload.get("hard_violations", [])),
        congestion=congestion,
        wirelength_nm=float(payload.get("wirelength_nm", 0.0)),
        escape_pressure=float(payload.get("escape_pressure", 0.0)),
        moves=int(payload.get("moves", 0)),
        search_nodes=int(payload.get("search_nodes", 0)),
        search_exhausted=bool(payload.get("search_exhausted", True)),
        seed=int(payload.get("seed", seed)),
        semantic_hash=str(payload.get("semantic_hash", "")),
        diagnostics=list(payload.get("diagnostics", [])),
        native_status=native.status,
        metrics=dict(result.get("metrics", {})))


def apply_placement_to_circuit(circuit: CircuitIR,
                               patch: list[PlacementMove]) -> CircuitIR:
    """Return a copy with component placements updated; routing stays untouched."""

    updated = circuit.model_copy(deep=True)
    by_id = {move.component_id: move for move in patch}
    for component in updated.components:
        move = by_id.get(component.id)
        if move is None:
            continue
        component.placement = Placement(
            x_nm=move.x_nm, y_nm=move.y_nm,
            rotation_deg=move.rotation_deg,
            side=component.placement.side if component.placement else "top")
    return updated


def _board_spec(board: BoardIR) -> dict[str, Any]:
    thickness = max(70_000, board.thickness_nm)
    outline = [[int(x), int(y)] for x, y in board.outline]
    if len(outline) > 1 and outline[0] == outline[-1]:
        outline = outline[:-1]
    if len(outline) < 3:
        outline = [[0, 0], [board.width_nm, 0],
                   [board.width_nm, board.height_nm], [0, board.height_nm]]
    return {
        "width_nm": board.width_nm,
        "height_nm": board.height_nm,
        "thickness_nm": thickness,
        "outline": outline,
        "stackup": {"layers": [
            {"id": "F.Cu", "order": 1, "kind": "copper",
             "thickness_nm": 35_000, "material_ref": "copper"},
            {"id": "D1", "order": 2, "kind": "dielectric",
             "thickness_nm": max(1, thickness - 70_000),
             "material_ref": "FR4"},
            {"id": "B.Cu", "order": 3, "kind": "copper",
             "thickness_nm": 35_000, "material_ref": "copper"},
        ]},
    }


def _placement_spec(circuit: CircuitIR, board: BoardIR,
                    rules: dict[str, Any], keepouts: list[dict[str, Any]],
                    module_regions: list[dict[str, Any]],
                    decoupling: dict[str, list[str]]) -> dict[str, Any]:
    components_by_id = {component.id: component for component in circuit.components}
    footprints_by_id = {footprint.component_id: footprint
                        for footprint in board.footprints}
    if not footprints_by_id:
        raise PlacementConstraintError(
            "BoardIR has no footprint instances; placement needs courtyards.")
    components = []
    for component_id, footprint in footprints_by_id.items():
        component = components_by_id.get(component_id)
        if component is None:
            continue
        pin_count = _pin_count(component)
        critical = pin_count >= 8 or component.reference.startswith("J")
        components.append({
            "id": component_id,
            "reference": component.reference,
            "module": component.module_id or component.instance_path or "",
            "x_nm": footprint.x_nm,
            "y_nm": footprint.y_nm,
            "w_nm": footprint.courtyard.w_nm,
            "h_nm": footprint.courtyard.h_nm,
            "rotation_deg": footprint.rotation_deg,
            "fixed": bool(component.locked),
            "critical": critical,
            "pins": pin_count,
            "pin_pitch_nm": 0,
            "decoupling_for": list(decoupling.get(component_id, [])),
        })
    nets = []
    for net in circuit.nets:
        ids = []
        for token in net.pins:
            component_id = token.partition(":")[0]
            if component_id in components_by_id and component_id not in ids:
                ids.append(component_id)
        if len(ids) >= 2:
            nets.append({"net": net.name, "components": ids})
    return {
        "components": components,
        "nets": nets,
        "keepouts": [_area(item) for item in keepouts],
        "module_regions": [
            {"module": str(item.get("module", "")),
             **_area(item)} for item in module_regions
        ],
        "rules": rules,
        "seed": int(rules.get("seed", 0)),
    }


def _pin_count(component) -> int:
    try:
        return len(resolve_part(component.part_id).pins)
    except NotFound:
        return max(1, len(component.pin_pad_map))


def _area(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": str(item.get("id", "")),
        "x0": int(item["x0"]),
        "y0": int(item["y0"]),
        "x1": int(item["x1"]),
        "y1": int(item["y1"]),
    } if "x0" in item else {
        "id": str(item.get("id", "")),
        "x0": int(item["area"][0]), "y0": int(item["area"][1]),
        "x1": int(item["area"][2]), "y1": int(item["area"][3]),
    }
