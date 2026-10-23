from __future__ import annotations

import json

import pytest

from mota.constraints.compiler import (
    ApplicabilityExpr, ConstraintRule, QuantitySpec, RulePack, RuleSource,
    TypedSelector, compile_constraints, format_compile_report,
    map_solver_status,
)
from mota.domain.models import BoardIR, Placement
from mota.engine_v2.schema import BoardV2, Stackup, StackupLayer
from mota.generation.placement import build_footprints, pads_for
from mota.knowledge.modules import compose_modules, scenario_mcu_i2c_temp_uart
from mota.knowledge.platforms import (
    PlatformPackInvalid, default_packs, load_pack, load_pack_file, pack_summary,
)

MM = 1_000_000


def _circuit_and_board() -> tuple:
    instances, connections = scenario_mcu_i2c_temp_uart()
    result = compose_modules(instances, connections, scenario_id="c5-test")
    assert result.status == "OK", result.diagnostics
    circuit = result.circuit
    for index, component in enumerate(circuit.components):
        component.placement = Placement(
            x_nm=5_000_000 + (index % 4) * 6_000_000,
            y_nm=5_000_000 + (index // 4) * 8_000_000)
    pads = []
    for component in circuit.components:
        pads.extend(pads_for(circuit, component.id))
    board = BoardIR(
        outline=[[0, 0], [40_000_000, 0], [40_000_000, 40_000_000],
                 [0, 40_000_000], [0, 0]],
        width_nm=40_000_000, height_nm=40_000_000, layer_count=2,
        footprints=build_footprints(circuit), pads=pads)
    return circuit, board


def _rule(rule_id: str, kind: str, parameters: dict[str, tuple[float, str]], *,
          selector: TypedSelector | None = None, hard: bool = True,
          required: bool = True, severity: str = "error",
          precondition: ApplicabilityExpr | None = None,
          sources: tuple[str, ...] = ("src",)) -> ConstraintRule:
    return ConstraintRule(
        rule_id=rule_id, kind=kind, hard=hard, required=required,
        severity=severity, selector=selector or TypedSelector(all=True),
        parameters={name: QuantitySpec(value=value, unit=unit)
                    for name, (value, unit) in parameters.items()},
        precondition=precondition or ApplicabilityExpr(),
        sources=list(sources))


def _pack(pack_id: str, rules: list[ConstraintRule], *,
          kind: str = "platform") -> RulePack:
    return RulePack(
        pack_id=pack_id, version=1, kind=kind, title=pack_id,
        purpose="unit-test pack",
        sources=[RuleSource(source_id="src", title="test source")],
        rules=rules)


def _coverage(result, rule_key: str):
    matches = [item for item in result.coverage if item.rule_key == rule_key]
    assert len(matches) == 1, (rule_key, [item.rule_key for item in result.coverage])
    return matches[0]


def test_default_packs_intersect_and_stay_located():
    circuit, board = _circuit_and_board()
    result = compile_constraints(circuit, board, default_packs())

    assert result.status == "SAT"
    assert result.conflicts == []
    # The delay rule requires stackup data, so review cannot be called ready.
    assert result.review_ready is False
    assert result.unresolved_rules == [
        "platform_stm32g071_min_system_v1:uart_propagation_delay"]

    clearance = next(item for item in result.constraints
                     if item.kind == "clearance")
    assert clearance.effective["min"].canonical_value == 0.3 * MM
    widths = {item.effective["min"].canonical_value
              for item in result.constraints if item.kind == "track_width"}
    assert 0.35 * MM in widths and 0.6 * MM in widths
    via = next(item for item in result.constraints if item.kind == "via_drill")
    assert via.effective["min"].canonical_value == 0.3 * MM

    floor = _coverage(
        result, "manufacturing_fr4_2layer_v1:min_clearance")
    assert floor.status == "APPLIED"
    assert floor.clamped is True
    assert floor.applied["min"].canonical_value == 0.3 * MM

    soft = next(item for item in result.soft_constraints
                if item.kind == "clearance")
    assert soft.hard is False
    assert soft.effective["min"].canonical_value == 0.1 * MM
    # A soft 0.10 mm preference must never loosen the hard 0.30 mm contract.
    assert clearance.effective["min"].canonical_value == 0.3 * MM

    for constraint in [*result.constraints, *result.soft_constraints]:
        assert constraint.object_refs
        assert constraint.sources
    for item in result.coverage:
        assert item.status in {"APPLIED", "NOT_CHECKED", "SKIPPED_NO_TARGET"}

    report = format_compile_report(result)
    assert "## Rule coverage" in report
    assert "## Conflicts" in report
    assert "manufacturing_fr4_2layer_v1:min_clearance" in report

    again = compile_constraints(circuit, board, default_packs())
    assert again.content_hash == result.content_hash

    summaries = [pack_summary(pack) for pack in default_packs()]
    assert len(summaries) == 3
    assert all(row["rule_count"] and row["sources"] for row in summaries)


def test_mm_time_mix_is_rejected():
    circuit, board = _circuit_and_board()

    valid = _pack("valid_delay", [_rule(
        "uart_delay", "propagation_delay", {"max": (80, "ps")},
        selector=TypedSelector(nets=["UART_TX"]))])
    accepted = compile_constraints(circuit, board, [valid])
    assert accepted.status == "SAT"
    delay = next(item for item in accepted.constraints
                 if item.kind == "propagation_delay")
    assert delay.effective["max"].canonical_value == 80
    assert delay.effective["max"].canonical_unit == "ps"

    mixed = _pack("mixed_units", [_rule(
        "uart_delay_mm", "propagation_delay", {"max": (0.08, "mm")},
        selector=TypedSelector(nets=["UART_TX"]))])
    rejected = compile_constraints(circuit, board, [mixed])
    assert rejected.status == "INVALID"
    assert rejected.status != "INFEASIBLE"
    assert rejected.constraints == []
    by_code = {item.code for item in rejected.diagnostics}
    assert "UNIT_MISMATCH" in by_code
    assert "mixed dimensions" in next(
        item.message for item in rejected.diagnostics
        if item.code == "UNIT_MISMATCH").lower()
    assert _coverage(rejected, "mixed_units:uart_delay_mm").status == "REJECTED"

    missing_unit = _pack("missing_unit", [_rule(
        "uart_delay_bare", "propagation_delay", {"max": (80, "")},
        selector=TypedSelector(nets=["UART_TX"]))])
    bare = compile_constraints(circuit, board, [missing_unit])
    assert bare.status == "INVALID"
    assert "MISSING_UNIT" in {item.code for item in bare.diagnostics}


def test_required_rule_with_empty_selector_is_rejected():
    circuit, board = _circuit_and_board()
    pack = _pack("empty_selector", [_rule(
        "nameless_clearance", "clearance", {"min": (0.2, "mm")},
        selector=TypedSelector())])
    result = compile_constraints(circuit, board, [pack])

    assert result.status == "INVALID"
    assert result.constraints == []
    diagnostic = next(item for item in result.diagnostics
                      if item.code == "EMPTY_SELECTOR")
    assert "required=True" in diagnostic.message
    coverage = _coverage(result, "empty_selector:nameless_clearance")
    assert coverage.status == "REJECTED"
    assert coverage.required is True


def test_delay_without_footprint_or_stackup_is_not_checked_not_skipped():
    circuit, board = _circuit_and_board()
    pack = load_pack("platform_stm32g071_min_system_v1")

    no_stackup = compile_constraints(circuit, board, [pack])
    coverage = _coverage(
        no_stackup, "platform_stm32g071_min_system_v1:uart_propagation_delay")
    assert coverage.status == "NOT_CHECKED"
    assert coverage.reason == "MISSING_STACKUP"
    assert coverage.status != "SKIPPED_PRECONDITION"
    assert no_stackup.status == "SAT"
    assert no_stackup.review_ready is False

    missing_footprint = circuit.model_copy(deep=True)
    uart = next(net for net in missing_footprint.nets if net.name == "UART_TX")
    component_id = uart.pins[0].partition(":")[0]
    component = next(item for item in missing_footprint.components
                     if item.id == component_id)
    component.footprint_id = ""
    blocked = compile_constraints(missing_footprint, board, [pack])
    coverage = _coverage(
        blocked, "platform_stm32g071_min_system_v1:uart_propagation_delay")
    assert coverage.status == "NOT_CHECKED"
    assert coverage.reason == "MISSING_FOOTPRINT"
    assert any(ref.startswith("component:")
               for ref in coverage.object_refs)

    stackup = Stackup(
        layers=[
            StackupLayer(id="F.Cu", order=1, kind="copper",
                         thickness_nm=35_000),
            StackupLayer(id="D1", order=2, kind="dielectric",
                         thickness_nm=1_500_000, material_ref="FR4"),
            StackupLayer(id="B.Cu", order=3, kind="copper",
                         thickness_nm=35_000),
        ],
        total_thickness_nm=1_570_000, source="declared")
    ready_board = BoardV2(
        **board.model_dump(), stackup=stackup,
        layer_order=["F.Cu", "B.Cu"])
    ready = compile_constraints(circuit, ready_board, [pack])
    coverage = _coverage(
        ready, "platform_stm32g071_min_system_v1:uart_propagation_delay")
    assert coverage.status == "APPLIED"
    assert ready.review_ready is True
    assert any(item.kind == "propagation_delay" for item in ready.constraints)


def test_hard_conflict_is_infeasible_but_soft_conflict_is_not():
    circuit, board = _circuit_and_board()
    minimum = _pack("min_pack", [_rule(
        "min_impedance", "impedance", {"min": (60, "ohm")},
        selector=TypedSelector(nets=["I2C_SDA"]))])
    maximum = _pack("max_pack", [_rule(
        "max_impedance", "impedance", {"max": (50, "ohm")},
        selector=TypedSelector(nets=["I2C_SDA"]))])

    result = compile_constraints(circuit, board, [minimum, maximum])
    assert result.status == "INFEASIBLE"
    assert len(result.conflicts) == 1
    conflict = result.conflicts[0]
    assert conflict.kind == "impedance"
    assert conflict.rule_keys == ["max_pack:max_impedance",
                                  "min_pack:min_impedance"]
    assert "net:I2C_SDA" in conflict.object_refs
    assert conflict.focus_nm is not None
    assert conflict.sources
    assert conflict.effective["min"].canonical_value == 60
    assert conflict.effective["max"].canonical_value == 50
    assert result.review_ready is False

    soft_minimum = _pack("soft_min", [_rule(
        "soft_min_impedance", "impedance", {"min": (60, "ohm")},
        selector=TypedSelector(nets=["I2C_SDA"]), hard=False,
        required=False, severity="warning")])
    soft_maximum = _pack("soft_max", [_rule(
        "soft_max_impedance", "impedance", {"max": (50, "ohm")},
        selector=TypedSelector(nets=["I2C_SDA"]), hard=False,
        required=False, severity="warning")])
    soft = compile_constraints(circuit, board, [soft_minimum, soft_maximum])
    assert soft.status == "SAT"
    assert soft.conflicts == []
    assert len(soft.soft_constraints) == 1
    soft_target = soft.soft_constraints[0]
    assert soft_target.hard is False
    assert soft_target.effective["min"].canonical_value == 60
    assert soft_target.effective["max"].canonical_value == 50


def test_budget_exhaustion_is_unknown_not_infeasible():
    circuit, board = _circuit_and_board()
    result = compile_constraints(circuit, board, default_packs(),
                                 budget_seconds=0.0)

    assert result.status == "UNKNOWN"
    assert result.status != "INFEASIBLE"
    assert result.conflicts == []
    assert result.solver.complete is False
    assert all(item.status == "NOT_COMPILED" for item in result.coverage)
    diagnostic = next(item for item in result.diagnostics
                      if item.code == "BUDGET_EXHAUSTED")
    assert "not a proof" in diagnostic.message

    assert map_solver_status("OPTIMAL") == "SAT"
    assert map_solver_status("FEASIBLE") == "SAT"
    assert map_solver_status("INFEASIBLE") == "INFEASIBLE"
    assert map_solver_status("MODEL_INVALID") == "INVALID"
    assert map_solver_status("UNKNOWN") == "UNKNOWN"
    assert map_solver_status("SOMETHING_NEW") == "UNKNOWN"


def test_pack_loader_rejects_bad_structure(tmp_path):
    raw = {
        "pack_id": "broken", "version": 1, "kind": "platform",
        "title": "broken", "purpose": "test",
        "sources": [{"source_id": "src"}],
        "rules": [{
            "rule_id": "r1", "kind": "clearance",
            "selector": {"all": True},
            "parameters": {"min": {"value": 0.2, "unit": "mm"}},
            "sources": ["src"]}],
        "unexpected_field": True,
    }
    path = tmp_path / "broken.json"
    path.write_text(json.dumps(raw), encoding="utf-8")
    with pytest.raises(PlatformPackInvalid) as error:
        load_pack_file(path)
    assert "unexpected_field" in json.dumps(error.value.data)

    raw.pop("unexpected_field")
    raw["rules"] = []
    path.write_text(json.dumps(raw), encoding="utf-8")
    with pytest.raises(PlatformPackInvalid):
        load_pack_file(path)


def test_no_packs_is_invalid_not_pass():
    circuit, board = _circuit_and_board()
    result = compile_constraints(circuit, board, [])
    assert result.status == "INVALID"
    assert result.review_ready is False
    assert any(item.code == "NO_RULES" for item in result.diagnostics)

    empty_pack = RulePack(
        pack_id="empty", version=1, kind="platform", title="empty",
        purpose="empty contract", sources=[RuleSource(source_id="src")],
        rules=[])
    empty = compile_constraints(circuit, board, [empty_pack])
    assert empty.status == "INVALID"
    assert any(item.code == "PACK_NO_RULES" for item in empty.diagnostics)
