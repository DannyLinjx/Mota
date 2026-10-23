from __future__ import annotations

import math

import pytest

from mota.catalog.templates import get_template
from mota.domain.models import DesignVersion
from mota.generation.board import run_build
from mota.projects.store import canonical_design_hash
from mota.render import meshes
from mota.validation.reports import validate_all


def test_power_3v3_is_honestly_review_blocked_on_missing_cap_evidence():
    result = run_build(get_template("power_3v3"), {})
    checks = {c.check_id: c for c in result.checks.checks}
    assert checks["OUTPUT_CAP_EFFECTIVE"].status == "NOT_CHECKED"
    assert checks["OUTPUT_CAP_ESR"].status == "NOT_CHECKED"
    assert checks["OUTPUT_CAP_EFFECTIVE"].required_for_review is True
    assert result.checks.review_status == "REVIEW_BLOCKED"
    assert result.checks.counts["FAIL"] == 0
    assert result.board.tracks
    assert not result.board.unrouted_nets
    assert checks["COPPER_CONNECTIVITY"].status == "PASS"


def test_two_golden_templates_are_ready_for_review():
    for template_id in ("led_timer", "i2c_temperature"):
        result = run_build(get_template(template_id), {})
        assert result.checks.review_status == "READY_FOR_REVIEW", template_id
        assert result.checks.counts["FAIL"] == 0
        assert not result.board.unrouted_nets
        required = [c for c in result.checks.checks if c.required_for_review]
        assert all(c.status == "PASS" for c in required)


def test_board_size_change_really_moves_connector_and_copper():
    small = run_build(get_template("power_3v3"), {"board_width_mm": 40})
    wide = run_build(get_template("power_3v3"), {"board_width_mm": 55})
    j2_small = next(c for c in small.circuit.components if c.reference == "J2")
    j2_wide = next(c for c in wide.circuit.components if c.reference == "J2")
    assert j2_small.placement.x_nm != j2_wide.placement.x_nm
    assert {tuple(t.points[0]) for t in small.board.tracks} != \
        {tuple(t.points[0]) for t in wide.board.tracks}
    assert canonical_design_hash(small.requirement, small.circuit, small.board) != \
        canonical_design_hash(wide.requirement, wide.circuit, wide.board)


def test_12v_request_is_rejected_before_synthesis(service, data_root):
    from tests.conftest import build_template, make_user, new_project

    session = make_user(service, "voltage")
    project_id, _ = new_project(service, session)
    opened = service.dispatch("project.open", {"project_id": project_id}, session)
    with pytest.raises(Exception) as exc:
        service.dispatch("run.start", {
            "project_id": project_id,
            "expected_revision_id": opened["current_revision_id"],
            "input": {"kind": "template", "template_id": "power_3v3",
                      "parameters": {"input_voltage_v": 12.0}},
            "idempotency_key": "twelve_volt"}, session)
    # The constraint is checked before any draft mutation; no revision appears.
    error = exc.value
    assert getattr(error, "code", "") == "CONSTRAINT_CONFLICT"
    revisions = service.dispatch("revision.list", {"project_id": project_id},
                                 session)["revisions"]
    assert len(revisions) == 1


def test_component_rotations_and_side_matrix():
    matrix = meshes.component_world_matrix(10.0, 5.0, 90, "bottom", 3.0)
    point = [1.0, 0.0, 1.0, 1.0]
    out = [
        matrix[0] * point[0] + matrix[1] * point[1] + matrix[2] * point[2] + matrix[3],
        matrix[4] * point[0] + matrix[5] * point[1] + matrix[6] * point[2] + matrix[7],
        matrix[8] * point[0] + matrix[9] * point[1] + matrix[10] * point[2] + matrix[11],
    ]
    # mirror X then rotate 90 deg CCW: local (1,0) -> (-1,0) -> (0,-1)
    assert out[0] == pytest.approx(10.0)
    assert out[1] == pytest.approx(4.0)
    assert out[2] == pytest.approx(1.0)


def test_scene_views_share_component_ids():
    from mota.render.scene import make_scene

    result = run_build(get_template("i2c_temperature"), {})
    version = DesignVersion.of_draft("draft_test", 1)
    ids = {}
    for view in ("schematic", "pcb2d", "pcb3d"):
        scene = make_scene("p", version, view, result.circuit, result.schematic,
                           result.board)
        ids[view] = {o.component_id for o in scene.objects if o.component_id}
    expected = {c.id for c in result.circuit.components}
    assert ids["schematic"] == expected
    assert ids["pcb2d"] == expected
    assert ids["pcb3d"] == expected


def test_pcb3d_missing_models_are_explicit():
    from mota.render.scene import make_scene

    result = run_build(get_template("power_3v3"), {})
    scene = make_scene("p", DesignVersion.of_draft("d", 1), "pcb3d",
                       result.circuit, result.schematic, result.board)
    assert scene.missing_models
    assert all(m["reference"] for m in scene.missing_models)
    assert all(o.fidelity in ("simplified", "missing") for o in scene.objects
               if o.kind == "mesh" and o.layer == "Component")
