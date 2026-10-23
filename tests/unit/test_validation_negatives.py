from __future__ import annotations

import copy

import pytest

from mota.catalog.templates import get_template
from mota.domain.models import (
    BoardIR, BoardPad, DesignVersion, Hole, Track,
)
from mota.domain.units import mm
from mota.generation.board import run_build
from mota.generation.routing import RouteConstraints, route_board
from mota.validation import electrical
from mota.validation import geometry as geom
from mota.validation.connectivity import copper_groups
from mota.validation.reports import validate_all, validate_electrical


def _led():
    result = run_build(get_template("led_timer"), {})
    return result


def test_required_topology_and_checks_pass():
    result = _led()
    checks = {c.check_id: c for c in result.checks.checks}
    assert checks["NE555_RESET_BIAS"].status == "PASS"
    assert checks["NE555_TIMING"].status == "PASS"
    assert checks["COPPER_CONNECTIVITY"].status == "PASS"
    assert checks["DRC_CLEARANCE"].status == "PASS"
    assert result.checks.review_status == "READY_FOR_REVIEW"


def test_missing_reset_bias_fails():
    result = _led()
    circuit = result.circuit.model_copy(deep=True)
    u1 = next(c for c in circuit.components if c.reference == "U1")
    key = f"{u1.id}:4"
    for net in circuit.nets:
        net.pins = [p for p in net.pins if p != key]
    circuit.nets = [n for n in circuit.nets if n.pins]
    check = electrical.check_ne555_reset_bias(circuit)
    assert check.status == "FAIL"


def test_out_of_spec_timing_fails():
    result = _led()
    circuit = result.circuit.model_copy(deep=True)
    r1 = next(c for c in circuit.components if c.reference == "R1")
    r1.value = "100k"
    check = electrical.check_ne555_timing(circuit, {"target_frequency_hz": 1.0})
    assert check.status == "FAIL"


def test_missing_decoupling_fails():
    result = _led()
    circuit = result.circuit.model_copy(deep=True)
    c3 = next(c for c in circuit.components if c.reference == "C3")
    circuit.components = [c for c in circuit.components if c.id != c3.id]
    for net in circuit.nets:
        net.pins = [p for p in net.pins if not p.startswith(c3.id + ":")]
    circuit.nets = [n for n in circuit.nets if n.pins]
    check = electrical.check_supply_decoupling(circuit)
    assert check.status == "FAIL"


def test_led_polarity_reversed_fails():
    result = _led()
    circuit = result.circuit.model_copy(deep=True)
    led = next(c for c in circuit.components if c.reference == "D1")
    anode_key = f"{led.id}:1"
    for net in circuit.nets:
        net.pins = [p for p in net.pins if p != anode_key]
    circuit.nets = [n for n in circuit.nets if n.pins]
    check = electrical.check_led_polarity(circuit)
    assert check.status == "FAIL"


def test_schematic_phase_skips_board_but_flags_floating_pin():
    result = _led()
    circuit = result.circuit.model_copy(deep=True)
    u1 = next(c for c in circuit.components if c.reference == "U1")
    key = f"{u1.id}:5"
    for net in circuit.nets:
        net.pins = [p for p in net.pins if p != key]
    circuit.nets = [n for n in circuit.nets if n.pins]
    report = validate_electrical("p", DesignVersion.of_draft("d", 1),
                                 result.requirement, circuit, result.schematic,
                                 result.board, get_template("led_timer"),
                                 include_board_consistency=False)
    checks = {c.check_id: c for c in report.checks}
    assert checks["NET_COMPLETENESS"].status == "FAIL"
    assert report.review_status == "REVIEW_BLOCKED"


def test_broken_copper_is_detected_even_if_router_says_complete():
    result = _led()
    board = result.board.model_copy(deep=True)
    assert copper_groups(board)[1] and all(r.connected for r in copper_groups(board)[1])
    victim = board.tracks[0]
    board.tracks = [t for t in board.tracks if t.id != victim.id]
    _, rows = copper_groups(board)
    assert any(not row.connected for row in rows)
    report = validate_all("p", DesignVersion.of_draft("d", 1), result.requirement,
                          result.circuit, result.schematic, board,
                          get_template("led_timer"))
    checks = {c.check_id: c for c in report.checks}
    assert checks["COPPER_CONNECTIVITY"].status == "FAIL"


def test_short_and_clearance_failures():
    result = _led()
    board = result.board.model_copy(deep=True)
    vin_pad = next(p for p in board.pads
                   if p.net_id and "vcc" in p.net_id and p.component_id.endswith("j1"))
    gnd_pad = next(p for p in board.pads
                   if p.net_id and "gnd" in p.net_id and p.component_id.endswith("j1"))
    board.tracks.append(Track(id="track_bad_short", net_id=vin_pad.net_id,
                              layer="F.Cu", width_nm=mm(0.3),
                              points=[[vin_pad.x_nm, vin_pad.y_nm],
                                      [gnd_pad.x_nm, gnd_pad.y_nm]]))
    violations = geom.check_clearance(board, 0.25)
    assert any(v.code == "COPPER_SHORT" for v in violations)


def test_track_width_and_board_edge_failures():
    board = BoardIR(outline=[[0, 0], [mm(20), 0], [mm(20), mm(20)], [0, mm(20)],
                             [0, 0]],
                    width_nm=mm(20), height_nm=mm(20))
    board.tracks.append(Track(id="thin", net_id="n1", layer="F.Cu",
                              width_nm=mm(0.1),
                              points=[[mm(2), mm(10)], [mm(10), mm(10)]]))
    board.tracks.append(Track(id="outside", net_id="n2", layer="F.Cu",
                              width_nm=mm(0.3),
                              points=[[mm(19.9), mm(19.9)], [mm(25), mm(25)]]))
    width_v = geom.check_widths(board, 0.30, 0.60, {"n1": "signal"})
    edge_v = geom.check_board_edge(board, 0.5)
    assert any(v.code == "TRACK_WIDTH" for v in width_v)
    assert any(v.code == "BOARD_EDGE" for v in edge_v)


def test_npth_copper_intrusion_fails():
    board = BoardIR(outline=[[0, 0], [mm(20), 0], [mm(20), mm(20)], [0, mm(20)],
                             [0, 0]],
                    width_nm=mm(20), height_nm=mm(20))
    board.tracks.append(Track(id="through_hole", net_id="n1", layer="F.Cu",
                              width_nm=mm(0.3),
                              points=[[mm(2), mm(10)], [mm(18), mm(10)]]))
    board.holes.append(Hole(id="hole_1", x_nm=mm(10), y_nm=mm(10),
                            drill_nm=mm(3.2), plated=False))
    violations = geom.check_holes(board, 0.25)
    assert any(v.code == "HOLE_CLEARANCE" for v in violations)


def test_cross_layer_without_via_is_not_connected():
    board = BoardIR(outline=[[0, 0], [mm(20), 0], [mm(20), mm(20)], [0, mm(20)],
                             [0, 0]],
                    width_nm=mm(20), height_nm=mm(20))
    pad_a = BoardPad(id="a", component_id="x", number="1", net_id="n1",
                     x_nm=mm(4), y_nm=mm(10), w_nm=mm(1.6), h_nm=mm(1.6),
                     shape="circle", drill_nm=mm(0.8))
    pad_b = BoardPad(id="b", component_id="y", number="1", net_id="n1",
                     x_nm=mm(16), y_nm=mm(10), w_nm=mm(1.6), h_nm=mm(1.6),
                     shape="circle", drill_nm=mm(0.8))
    board.pads = [pad_a, pad_b]
    board.tracks = [
        Track(id="f", net_id="n1", layer="F.Cu", width_nm=mm(0.3),
              points=[[mm(4), mm(10)], [mm(10), mm(10)]]),
        Track(id="b", net_id="n1", layer="B.Cu", width_nm=mm(0.3),
              points=[[mm(10), mm(10)], [mm(16), mm(10)]]),
    ]
    _, rows = copper_groups(board)
    assert not rows[0].connected


def test_router_reports_sealed_pad_as_unrouted():
    board = BoardIR(outline=[[0, 0], [mm(30), 0], [mm(30), mm(20)], [0, mm(20)],
                             [0, 0]],
                    width_nm=mm(30), height_nm=mm(20))
    board.pads = [
        BoardPad(id="a", component_id="x", number="1", net_id="n1",
                 x_nm=mm(10), y_nm=mm(10), w_nm=mm(1.6), h_nm=mm(1.6),
                 shape="circle", drill_nm=mm(0.8)),
        BoardPad(id="b", component_id="y", number="1", net_id="n1",
                 x_nm=mm(20), y_nm=mm(10), w_nm=mm(1.6), h_nm=mm(1.6),
                 shape="circle", drill_nm=mm(0.8)),
    ]
    box = [(8, 8), (12, 8), (12, 12), (8, 12), (8, 8)]
    for layer in ("F.Cu", "B.Cu"):
        for index in range(4):
            board.tracks.append(Track(
                id=f"wall_{layer.replace('.', '')}_{index}", net_id="gnd_net",
                layer=layer, width_nm=mm(0.3),
                points=[[mm(box[index][0]), mm(box[index][1])],
                        [mm(box[index + 1][0]), mm(box[index + 1][1])]]))
    routed, unrouted = route_board(board, RouteConstraints(), {"n1": "signal"},
                                   time_budget=10.0)
    assert "n1" in unrouted
    assert routed.ratsnest
    assert not [t for t in routed.tracks if t.net_id == "n1"]
