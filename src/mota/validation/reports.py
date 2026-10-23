"""Validation report assembly and review-status gate."""

from __future__ import annotations

from typing import Any

from mota.catalog.templates import TemplateRecipe
from mota.domain.models import (
    BoardIR, CheckResult, CircuitIR, DesignVersion, RequirementSpec,
    SchematicIR, ValidationReport,
)
from mota.validation import electrical
from mota.validation import geometry as geom
from mota.validation.connectivity import copper_groups
from mota.rules import load_rule_profile

COURTYARD_GAP_MM = 0.0


def _fail_check(check_id: str, title: str, violations: list[geom.Violation],
                *, required: bool = True) -> CheckResult:
    objects: list[str] = []
    for violation in violations:
        objects.extend(violation.object_ids)
    message = violations[0].message if violations else ""
    if len(violations) > 1:
        message += f" (+{len(violations) - 1} more)"
    return CheckResult(
        check_id=check_id, rule_id=check_id, title=title, status="FAIL",
        severity="error", required_for_review=required,
        object_ids=sorted(set(objects))[:40], message=message,
        evidence={"count": len(violations),
                  "violations": [{"code": v.code, "message": v.message,
                                  "objects": v.object_ids,
                                  "detail": v.detail} for v in violations[:20]]},
        coverage="Exact geometry over pads, tracks, vias and NPTH holes.")


def _pass_check(check_id: str, title: str, message: str,
                *, evidence: dict[str, Any] | None = None) -> CheckResult:
    return CheckResult(check_id=check_id, rule_id=check_id, title=title,
                       status="PASS", severity="info", required_for_review=True,
                       message=message, evidence=evidence or {})


def electrical_checks(circuit: CircuitIR, schematic: SchematicIR, board: BoardIR,
                      recipe: TemplateRecipe | None,
                      params: dict[str, Any],
                      include_board_consistency: bool = True) -> list[CheckResult]:
    checks: list[CheckResult] = [
        electrical.check_pin_pad_mapping(circuit),
        electrical.check_net_completeness(circuit),
    ]
    if recipe is not None:
        checks.append(electrical.check_required_connections(circuit, recipe))
    checks.extend([
        electrical.check_voltage_domain(circuit, recipe or _empty_recipe(), params),
        electrical.check_supply_decoupling(circuit),
    ])
    if include_board_consistency:
        checks.append(electrical.check_schematic_consistency(circuit, schematic, board))
    checks.append(electrical.check_visible_text(circuit, schematic))
    if recipe is not None and recipe.id == "power_3v3":
        checks.extend([
            electrical.check_mcp1700_pinmap(circuit),
            electrical.check_output_cap_effective(circuit),
            electrical.check_output_cap_esr(circuit),
            electrical.check_thermal_out_of_scope(circuit, params),
        ])
    elif recipe is not None and recipe.id == "led_timer":
        checks.extend([
            electrical.check_ne555_pinmap(circuit),
            electrical.check_ne555_reset_bias(circuit),
            electrical.check_ne555_timing(circuit, params),
            electrical.check_led_polarity(circuit),
            electrical.check_led_physical_polarity(),
        ])
    elif recipe is not None and recipe.id == "i2c_temperature":
        checks.extend([
            electrical.check_ds1621_pinmap(circuit),
            electrical.check_i2c_pullups(circuit),
            electrical.check_i2c_address(circuit),
            electrical.check_tout_nc(circuit, schematic),
            electrical.check_interface_assumption(circuit, params),
        ])
    checks.append(electrical.check_emc_out_of_scope())
    return checks


def validate_electrical(project_id: str, version: DesignVersion,
                        requirement: RequirementSpec, circuit: CircuitIR,
                        schematic: SchematicIR, board: BoardIR,
                        recipe: TemplateRecipe | None,
                        include_board_consistency: bool = False) -> ValidationReport:
    """Schematic-phase validation: electrical and schematic checks only."""
    params = {k: v.value for k, v in requirement.parameters.items()}
    checks = electrical_checks(circuit, schematic, board, recipe, params,
                               include_board_consistency=include_board_consistency)
    for check in checks:
        check.design_version_key = version.key
    required = [c for c in checks if c.required_for_review]
    if requirement.open_questions:
        review = "NEEDS_INPUT"
    elif all(c.status == "PASS" for c in required):
        review = "READY_FOR_REVIEW"
    else:
        review = "REVIEW_BLOCKED"
    profile = load_rule_profile(recipe.validation_profile if recipe
                                else circuit.rule_profile_id)
    return ValidationReport(
        project_id=project_id, design_version=version, design_version_key=version.key,
        profile_id=profile.rule_profile_id, checks=checks, review_status=review)


def validate_all(project_id: str, version: DesignVersion, requirement: RequirementSpec,
                 circuit: CircuitIR, schematic: SchematicIR, board: BoardIR,
                 recipe: TemplateRecipe | None) -> ValidationReport:
    params = {k: v.value for k, v in requirement.parameters.items()}
    checks = electrical_checks(circuit, schematic, board, recipe, params)
    net_classes = {net.id: net.net_class for net in circuit.nets}
    profile = load_rule_profile(recipe.validation_profile if recipe
                                else circuit.rule_profile_id)
    clearance_mm = profile.clearance_nm / 1e6
    signal_mm = profile.signal_width_nm / 1e6
    power_mm = profile.power_width_nm / 1e6
    edge_mm = profile.edge_nm / 1e6
    hole_mm = profile.hole_clearance_nm / 1e6
    via_outer_mm = profile.via_outer_nm / 1e6
    via_drill_mm = profile.via_drill_nm / 1e6
    via_ring_mm = profile.via_ring_nm / 1e6

    # ------------------------------------------------------------- routing
    if board.unrouted_nets or board.ratsnest:
        checks.append(CheckResult(
            check_id="ROUTING_COMPLETE", rule_id="ROUTING_COMPLETE",
            title="Routing complete", status="FAIL", severity="error",
            required_for_review=True,
            object_ids=list(board.unrouted_nets),
            message=f"{len(board.unrouted_nets)} net(s) are not fully routed: "
                    f"{', '.join(_net_names(circuit, board.unrouted_nets))}.",
            evidence={"unrouted_nets": board.unrouted_nets,
                      "ratsnest_lines": len(board.ratsnest)}))
    else:
        checks.append(_pass_check(
            "ROUTING_COMPLETE", "Routing complete",
            f"All {len(circuit.nets)} nets have routed copper.",
            evidence={"tracks": len(board.tracks), "vias": len(board.vias)}))

    # ------------------------------------------------------------- geometry
    clearance_violations = geom.check_clearance(board, clearance_mm)
    shorts = [v for v in clearance_violations if v.code == "COPPER_SHORT"]
    spacing = [v for v in clearance_violations if v.code != "COPPER_SHORT"]
    if spacing:
        checks.append(_fail_check("DRC_CLEARANCE", "Copper clearance", spacing))
    else:
        checks.append(_pass_check(
            "DRC_CLEARANCE", "Copper clearance",
            f"No clearance violation below {clearance_mm:.2f} mm "
            f"({profile.rule_profile_id} v{profile.version}).",
            evidence={"checked_pairs": len(board.pads) ** 2}))

    if shorts:
        checks.append(_fail_check("COPPER_SHORTS", "Copper shorts", shorts))
    else:
        checks.append(_pass_check("COPPER_SHORTS", "Copper shorts",
                                  "No copper of different nets touches."))

    edge_violations = geom.check_board_edge(board, edge_mm)
    checks.append(_fail_check("DRC_BOARD_EDGE", "Board edge", edge_violations)
                  if edge_violations else
                  _pass_check("DRC_BOARD_EDGE", "Board edge",
                              f"Copper keeps {edge_mm:.2f} mm from the board edge."))

    width_violations = geom.check_widths(board, signal_mm, power_mm, net_classes)
    checks.append(_fail_check("DRC_TRACK_WIDTH", "Track width", width_violations)
                  if width_violations else
                  _pass_check("DRC_TRACK_WIDTH", "Track width",
                              f"Track widths meet {profile.rule_profile_id} "
                              f"({signal_mm:.2f} / {power_mm:.2f} mm)."))

    via_violations = geom.check_vias(board, via_outer_mm, via_drill_mm, via_ring_mm)
    checks.append(_fail_check("DRC_VIA", "Via geometry", via_violations)
                  if via_violations else
                  _pass_check("DRC_VIA", "Via geometry",
                              "Via outer/drill/ring dimensions meet the project rules."))

    hole_violations = geom.check_holes(board, hole_mm)
    checks.append(_fail_check("DRC_HOLES", "Hole clearance", hole_violations)
                  if hole_violations else
                  _pass_check("DRC_HOLES", "Hole clearance",
                              f"Copper keeps {hole_mm:.2f} mm from NPTH holes "
                              f"on both layers."))

    courtyard_violations = geom.check_courtyards(board, COURTYARD_GAP_MM)
    checks.append(_fail_check("COURTYARD", "Courtyard", courtyard_violations)
                  if courtyard_violations else
                  _pass_check("COURTYARD", "Courtyard",
                              "Component courtyards are inside the board and do not overlap."))

    _, connectivity = copper_groups(board)
    unconnected = [row for row in connectivity if not row.connected]
    if unconnected:
        checks.append(CheckResult(
            check_id="COPPER_CONNECTIVITY", rule_id="COPPER_CONNECTIVITY",
            title="Copper connectivity", status="FAIL", severity="error",
            required_for_review=True,
            object_ids=[pad for row in unconnected for pad in row.unconnected_pads],
            message=f"{len(unconnected)} net(s) are split into separate copper groups: "
                    f"{', '.join(_net_names(circuit, [r.net_id for r in unconnected]))}.",
            evidence={"nets": [{"net_id": r.net_id, "groups": r.groups,
                                "unconnected_pads": r.unconnected_pads}
                               for r in unconnected]}))
    else:
        checks.append(_pass_check(
            "COPPER_CONNECTIVITY", "Copper connectivity",
            f"All {len(connectivity)} nets are connected by real copper.",
            evidence={"nets": len(connectivity)}))

    # ------------------------------------------------------------ review gate
    required = [c for c in checks if c.required_for_review]
    all_pass = all(c.status == "PASS" for c in required)
    if requirement.open_questions:
        review = "NEEDS_INPUT"
    elif all_pass:
        review = "READY_FOR_REVIEW"
    else:
        review = "REVIEW_BLOCKED"
    report = ValidationReport(
        project_id=project_id, design_version=version, design_version_key=version.key,
        profile_id=recipe.validation_profile if recipe else circuit.rule_profile_id,
        checks=checks, review_status=review)
    for check in report.checks:
        check.design_version_key = version.key
    return report


def _net_names(circuit: CircuitIR, net_ids: list[str]) -> list[str]:
    names = {n.id: n.name for n in circuit.nets}
    return [names.get(nid, nid) for nid in net_ids]


def _empty_recipe() -> TemplateRecipe:
    from mota.catalog.templates import TemplateRecipe

    return TemplateRecipe(
        id="custom", version=0, title="Custom", subtitle="", description="",
        status="candidate", validation_profile="mota_demo_2layer_v1", modules=[],
        required_fields=[], defaults={}, constraints={}, board={}, parts=[],
        nets=[], no_connects=[], placement=[], required_checks=[], source_ids=[])
