from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from mota.domain.models import (
    BoardIR, CircuitIR, Component, DesignVersion, FootprintInstance, Net,
    Placement, Rect,
)
from mota.jobs.contracts import WorkerUnavailable, kernel_binary
from mota.placement.service import (
    apply_placement_to_circuit, place_board, rules_from_compiled,
)

MM = 1_000_000


def _component(component_id: str, reference: str, x: int, y: int,
               width: int, height: int, *, locked: bool = False,
               pins: int = 8) -> tuple[Component, FootprintInstance]:
    pin_map = {str(index): [str(index)] for index in range(1, pins + 1)}
    component = Component(
        id=component_id, reference=reference, part_id="ne555p",
        symbol_id="ne555p", footprint_id="fp", pin_pad_map=pin_map,
        placement=Placement(x_nm=x, y_nm=y), locked=locked)
    footprint = FootprintInstance(
        component_id=component_id, footprint_id="fp", reference=reference,
        x_nm=x, y_nm=y, rotation_deg=0,
        courtyard=Rect(x_nm=x - width // 2, y_nm=y - height // 2,
                       w_nm=width, h_nm=height))
    return component, footprint


def _design(*, overlap: bool = False) -> tuple[CircuitIR, BoardIR]:
    components = []
    footprints = []
    j1, f1 = _component("c_j1", "J1", 5 * MM, 5 * MM, 8 * MM, 6 * MM,
                        locked=True, pins=4)
    components.append(j1)
    footprints.append(f1)
    if overlap:
        u1, f2 = _component("c_u1", "U1", 7 * MM, 7 * MM, 8 * MM, 8 * MM,
                            locked=True, pins=8)
    else:
        u1, f2 = _component("c_u1", "U1", 25 * MM, 18 * MM, 6 * MM, 6 * MM,
                            pins=8)
        c1, f3 = _component("c_c1", "C1", 20 * MM, 22 * MM, 3 * MM, 2 * MM,
                            pins=2)
        components.append(c1)
        footprints.append(f3)
    components.append(u1)
    footprints.append(f2)
    circuit = CircuitIR(components=components, nets=[
        Net(id="n1", name="N1", pins=["c_j1:1", "c_u1:1"]),
        Net(id="n2", name="N2", pins=["c_u1:1", "c_c1:1"]),
    ])
    board = BoardIR(
        outline=[[0, 0], [40 * MM, 0], [40 * MM, 30 * MM], [0, 30 * MM], [0, 0]],
        width_nm=40 * MM, height_nm=30 * MM, thickness_nm=1_600_000,
        layer_count=2, footprints=footprints)
    return circuit, board


@pytest.fixture(scope="module")
def kernel_path() -> Path:
    try:
        return kernel_binary()
    except WorkerUnavailable as error:
        pytest.skip(f"native kernel not built: {error}")


def test_place_board_keeps_fixed_and_avoids_keepout(kernel_path: Path):
    circuit, board = _design()
    version = DesignVersion(revision_id="rev_c8_clean")
    outcome = place_board(
        "project_c8", version, circuit, board,
        keepouts=[{"id": "K1", "x0": 12 * MM, "y0": 4 * MM,
                   "x1": 20 * MM, "y1": 16 * MM}],
        seed=42, binary=kernel_path)
    assert outcome.status == "SUCCEEDED", outcome.hard_violations
    assert outcome.native_status == "SUCCEEDED"
    assert outcome.semantic_hash
    by_id = {move.component_id: move for move in outcome.patch}
    assert set(by_id) == {"c_j1", "c_u1", "c_c1"}
    assert by_id["c_j1"].fixed
    assert (by_id["c_j1"].x_nm, by_id["c_j1"].y_nm) == (5 * MM, 5 * MM)
    u1 = by_id["c_u1"]
    assert not (u1.x_nm + 3 * MM > 12 * MM and u1.x_nm - 3 * MM < 20 * MM
                and u1.y_nm + 3 * MM > 4 * MM and u1.y_nm - 3 * MM < 16 * MM)
    assert outcome.congestion.columns > 0
    assert outcome.congestion.max_load > 0
    assert outcome.wirelength_nm > 0
    assert outcome.search_nodes > 0

    applied = apply_placement_to_circuit(circuit, outcome.patch)
    placed = {row.id: row for row in applied.components}
    assert placed["c_u1"].placement.x_nm == u1.x_nm


def test_seed_replay_is_identical(kernel_path: Path):
    circuit, board = _design()
    version = DesignVersion(revision_id="rev_c8_replay")
    first = place_board("project_c8", version, circuit, board, seed=7,
                        binary=kernel_path)
    second = place_board("project_c8", version, circuit, board, seed=7,
                         binary=kernel_path)
    assert first.status == second.status == "SUCCEEDED"
    assert first.semantic_hash == second.semantic_hash
    assert [move.model_dump() for move in first.patch] == [
        move.model_dump() for move in second.patch]


def test_fixed_overlap_is_infeasible(kernel_path: Path):
    circuit, board = _design(overlap=True)
    version = DesignVersion(revision_id="rev_c8_bad")
    outcome = place_board("project_c8", version, circuit, board,
                          binary=kernel_path)
    assert outcome.status == "INFEASIBLE"
    assert any(item.startswith("FIXED_OVERLAP")
               for item in outcome.hard_violations)


def test_rules_from_compiled_tighten_placement():
    minimum = SimpleNamespace(canonical_value=300_000)
    compiled = SimpleNamespace(status="SAT", constraints=[
        SimpleNamespace(kind="clearance", hard=True, effective={"min": minimum}),
        SimpleNamespace(kind="board_edge_clearance", hard=True,
                        effective={"min": SimpleNamespace(canonical_value=700_000)}),
    ])
    rules = rules_from_compiled(compiled)
    assert rules["courtyard_gap_nm"] >= 300_000
    assert rules["edge_clearance_nm"] >= 700_000

    blocked = SimpleNamespace(status="INFEASIBLE", constraints=[])
    with pytest.raises(Exception):
        rules_from_compiled(blocked)
