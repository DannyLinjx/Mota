from __future__ import annotations

from pathlib import Path

import pytest

from mota.domain.models import (
    BoardIR, BoardPad, CircuitIR, DesignVersion, Net, Track,
)
from mota.jobs.contracts import kernel_binary, WorkerUnavailable
from mota.verification.service import verify_board, verify_design

MM = 1_000_000

RULES = [
    {"id": "COPPER_SHORTS", "kind": "short", "required": True, "value_nm": 0},
    {"id": "DRC_CLEARANCE", "kind": "clearance", "required": True,
     "value_nm": 200_000},
    {"id": "DRC_TRACK_WIDTH", "kind": "track_width", "required": True,
     "value_nm": 200_000},
    {"id": "DRC_VIA_DRILL", "kind": "via_drill", "required": True,
     "value_nm": 300_000},
    {"id": "DRC_VIA_RING", "kind": "via_annular_ring", "required": True,
     "value_nm": 200_000},
    {"id": "DRC_EDGE", "kind": "board_edge_clearance", "required": True,
     "value_nm": 300_000},
]


def _circle(x: int, y: int, radius: int = MM) -> dict:
    return {"kind": "circle", "center": {"x": x, "y": y}, "radius": radius}


def _board(*, broken: bool = False, short: bool = False,
           with_zone: bool = True, triangle_zone: bool = False,
           missing_endpoint: bool = False) -> dict:
    board = {
        "width_nm": 20 * MM,
        "height_nm": 20 * MM,
        "thickness_nm": 1_600_000,
        "stackup": {"layers": [
            {"id": "F.Cu", "order": 1, "kind": "copper",
             "thickness_nm": 35_000, "material_ref": "copper"},
            {"id": "D1", "order": 2, "kind": "dielectric",
             "thickness_nm": 1_530_000, "material_ref": "FR4"},
            {"id": "B.Cu", "order": 3, "kind": "copper",
             "thickness_nm": 35_000, "material_ref": "copper"},
        ]},
        "pads": [
            {"id": "N1", "net": "N", "component_id": "U1", "pin": "1",
             "plated": True, "copper_by_layer": {"F.Cu": _circle(5 * MM, 5 * MM)}},
            {"id": "N2", "net": "N", "component_id": "U1", "pin": "2",
             "plated": True, "copper_by_layer": {"F.Cu": _circle(15 * MM, 5 * MM)}},
        ],
        "tracks": [
            {"id": "TN", "net": "N", "layer": "F.Cu",
             "points": [{"x": 5 * MM, "y": 5 * MM},
                        {"x": (10 if broken else 15) * MM, "y": 5 * MM}],
             "width_nm": 300_000},
            {"id": "TM", "net": "M", "layer": "F.Cu",
             "points": [{"x": 5 * MM, "y": 12 * MM},
                        {"x": 15 * MM, "y": 12 * MM}],
             "width_nm": 300_000},
        ],
        "vias": [
            {"id": "V1", "net": "N", "kind": "through",
             "start_layer": "F.Cu", "end_layer": "B.Cu", "drill_nm": 400_000,
             "pad": _circle(10 * MM, 5 * MM, 500_000)},
        ],
        "net_classes": {"N": "signal", "M": "signal", "GND": "power"},
    }
    if short:
        board["tracks"].append({
            "id": "TM_SHORT", "net": "M", "layer": "F.Cu",
            "points": [{"x": 5 * MM, "y": 5_200_000},
                       {"x": 15 * MM, "y": 5_200_000}],
            "width_nm": 300_000})
    if missing_endpoint:
        board["required_endpoints"] = ["pad:missing"]
    if with_zone:
        board["pads"].append({
            "id": "GND1", "net": "GND", "component_id": "U2", "pin": "1",
            "plated": True,
            "copper_by_layer": {"F.Cu": _circle(5 * MM, 15 * MM)}})
        outline = (
            [{"x": 2 * MM, "y": 2 * MM}, {"x": 18 * MM, "y": 2 * MM},
             {"x": 10 * MM, "y": 18 * MM}]
            if triangle_zone else
            [{"x": MM, "y": MM}, {"x": 19 * MM, "y": MM},
             {"x": 19 * MM, "y": 19 * MM}, {"x": MM, "y": 19 * MM}])
        board["zones"] = [{
            "id": "Z1", "net": "GND", "layer": "F.Cu", "priority": 0,
            "outline": outline, "clearance_nm": 300_000,
            "thermal_gap_nm": 250_000, "thermal_spoke_width_nm": 250_000,
            "thermal_spokes": 4, "remove_islands": True}]
    return board


@pytest.fixture(scope="module")
def kernel_path() -> Path:
    try:
        return kernel_binary()
    except WorkerUnavailable as error:
        pytest.skip(f"native kernel not built: {error}")


def test_verify_clean_board(kernel_path: Path):
    version = DesignVersion(revision_id="rev_c7_clean")
    outcome = verify_board("project_c7", version, _board(), RULES,
                           binary=kernel_path)
    assert outcome.native_status == "SUCCEEDED", outcome.report.checks
    assert outcome.u_total == 0
    assert outcome.fill_hash
    zones = outcome.fill.get("zones", [])
    assert len(zones) == 1
    assert zones[0]["components"]
    assert zones[0]["components"][0]["spokes"]
    report = outcome.report
    assert report.design_version_key == version.key
    assert report.review_status == "READY_FOR_REVIEW", [
        (check.check_id, check.status, check.message) for check in report.checks]
    by_id = {check.check_id: check for check in report.checks}
    assert by_id["COPPER_CONNECTIVITY"].status == "PASS"
    assert by_id["ZONE_FILL"].status == "PASS"
    assert by_id["EXTERNAL_KICAD"].status == "NOT_CHECKED"
    assert "cross-check" in by_id["EXTERNAL_KICAD"].message
    assert outcome.external_cross_check["status"] == "NOT_CHECKED"


def test_broken_track_blocks_review(kernel_path: Path):
    version = DesignVersion(revision_id="rev_c7_open")
    outcome = verify_board("project_c7", version, _board(broken=True), RULES,
                           binary=kernel_path)
    assert outcome.u_total >= 1
    report = outcome.report
    assert report.review_status == "REVIEW_BLOCKED"
    connectivity = next(check for check in report.checks
                        if check.check_id == "COPPER_CONNECTIVITY")
    assert connectivity.status == "FAIL"
    assert connectivity.evidence["u_total"] >= 1
    assert any(issue["code"] == "NET_OPEN"
               for issue in outcome.connectivity["issues"])


def test_short_blocks_review(kernel_path: Path):
    version = DesignVersion(revision_id="rev_c7_short")
    outcome = verify_board("project_c7", version, _board(short=True), RULES,
                           binary=kernel_path)
    report = outcome.report
    assert report.review_status == "REVIEW_BLOCKED"
    short_check = next(check for check in report.checks
                       if check.check_id == "COPPER_SHORTS")
    assert short_check.status == "FAIL"
    assert any(issue["code"] == "COPPER_SHORT"
               for issue in outcome.connectivity["issues"])


def test_empty_rules_never_pass(kernel_path: Path):
    version = DesignVersion(revision_id="rev_c7_no_rules")
    outcome = verify_board("project_c7", version, _board(), [],
                           binary=kernel_path)
    assert outcome.native_status == "SUCCEEDED"
    assert outcome.report.review_status == "REVIEW_BLOCKED"
    assert any(check.status == "NOT_CHECKED"
               for check in outcome.report.checks)
    assert any(check.check_id == "DRC_RULES" and check.status == "NOT_CHECKED"
               for check in outcome.report.checks)


def test_missing_endpoint_blocks_review(kernel_path: Path):
    version = DesignVersion(revision_id="rev_c7_missing")
    outcome = verify_board("project_c7", version,
                           _board(missing_endpoint=True), RULES,
                           binary=kernel_path)
    assert outcome.report.review_status == "REVIEW_BLOCKED"
    assert any(issue["code"] == "MISSING_ENDPOINT"
               for issue in outcome.connectivity["issues"])


def test_non_rectilinear_zone_is_not_approximated(kernel_path: Path):
    version = DesignVersion(revision_id="rev_c7_triangle")
    outcome = verify_board("project_c7", version,
                           _board(triangle_zone=True), RULES,
                           binary=kernel_path)
    assert outcome.report.review_status == "REVIEW_BLOCKED"
    fill_check = next(check for check in outcome.report.checks
                      if check.check_id == "ZONE_FILL")
    assert fill_check.status == "NOT_CHECKED"
    assert outcome.fill["zones"][0]["non_rectilinear"]
    assert outcome.fill["zones"][0]["components"] == []


IR_RULES = [
    {"id": "COPPER_SHORTS", "kind": "short", "required": True, "value_nm": 0},
    {"id": "DRC_CLEARANCE", "kind": "clearance", "required": True,
     "value_nm": 200_000},
    {"id": "DRC_TRACK_WIDTH", "kind": "track_width", "required": True,
     "value_nm": 200_000},
    {"id": "DRC_EDGE", "kind": "board_edge_clearance", "required": True,
     "value_nm": 300_000},
]


def _ir_board(*, broken: bool = False) -> tuple[CircuitIR, BoardIR]:
    circuit = CircuitIR(nets=[
        Net(id="n1", name="N", net_class="signal"),
        Net(id="n2", name="M", net_class="signal"),
    ])
    pads = [
        BoardPad(id="P1", component_id="U1", number="1", net_id="n1",
                 x_nm=5 * MM, y_nm=5 * MM, w_nm=2 * MM, h_nm=2 * MM,
                 shape="circle", drill_nm=500_000, pad_type="tht"),
        BoardPad(id="P2", component_id="U1", number="2", net_id="n1",
                 x_nm=15 * MM, y_nm=5 * MM, w_nm=2 * MM, h_nm=2 * MM,
                 shape="circle", drill_nm=500_000, pad_type="tht"),
    ]
    tracks = [
        Track(id="TN", net_id="n1", layer="F.Cu", width_nm=300_000,
              points=[[5 * MM, 5 * MM],
                      [(10 if broken else 15) * MM, 5 * MM]]),
        Track(id="TM", net_id="n2", layer="F.Cu", width_nm=300_000,
              points=[[5 * MM, 12 * MM], [15 * MM, 12 * MM]]),
    ]
    board = BoardIR(
        outline=[[0, 0], [20 * MM, 0], [20 * MM, 20 * MM], [0, 20 * MM], [0, 0]],
        width_nm=20 * MM, height_nm=20 * MM, thickness_nm=1_600_000,
        layer_count=2, pads=pads, tracks=tracks)
    return circuit, board


def test_verify_design_from_frozen_ir(kernel_path: Path):
    version = DesignVersion(revision_id="rev_c7_ir")
    circuit, board = _ir_board()
    outcome = verify_design("project_c7", version, circuit, board, IR_RULES,
                            binary=kernel_path)
    assert outcome.native_status == "SUCCEEDED", outcome.report.checks
    assert outcome.report.review_status == "READY_FOR_REVIEW", [
        (check.check_id, check.status, check.message)
        for check in outcome.report.checks]
    assert outcome.u_total == 0
    assert outcome.report.profile_id.startswith("native-verify-v1:")

    broken_circuit, broken_board = _ir_board(broken=True)
    broken = verify_design("project_c7", version, broken_circuit, broken_board,
                           IR_RULES, binary=kernel_path)
    assert broken.report.review_status == "REVIEW_BLOCKED"
    assert broken.u_total >= 1
