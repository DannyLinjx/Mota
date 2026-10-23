"""Typed constraint compiler.

The compiler answers two separate questions:

1. what does the contract require, per layer/net, after intersecting the
   manufacturing floor, platform requirements and stricter user conditions;
2. is the contract itself consistent (no empty hard intersection).

It does not claim that a design passes. Rules whose evidence is missing
(stackup, footprint, copper geometry) stay ``NOT_CHECKED`` and are never
silently treated as inapplicable. A time-budget stop is ``UNKNOWN`` and is
never reported as ``INFEASIBLE``: only an empty hard interval is infeasible.
"""

from __future__ import annotations

import hashlib
import json
import math
import time
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Literal

from pydantic import Field

from mota.domain.models import BoardIR, CircuitIR, DTO, Net

CompileStatus = Literal["SAT", "INFEASIBLE", "UNKNOWN", "INVALID"]
Dimension = Literal["length", "time", "frequency", "impedance", "voltage", "current"]
CoverageStatus = Literal[
    "APPLIED", "NOT_CHECKED", "SKIPPED_PRECONDITION", "SKIPPED_NO_TARGET",
    "REJECTED", "NOT_COMPILED",
]
Severity = Literal["error", "warning", "info"]

# Canonical base units: nm / ps / Hz / ohm / V / A.
UNIT_TABLE: dict[str, dict[str, Decimal]] = {
    "length": {
        "nm": Decimal(1),
        "um": Decimal(1_000),
        "mm": Decimal(1_000_000),
        "mil": Decimal(25_400),
        "in": Decimal(25_400_000),
    },
    "time": {
        "ps": Decimal(1),
        "ns": Decimal(1_000),
        "us": Decimal(1_000_000),
        "ms": Decimal(1_000_000_000),
        "s": Decimal(1_000_000_000_000),
    },
    "frequency": {
        "Hz": Decimal(1), "kHz": Decimal(1_000),
        "MHz": Decimal(1_000_000), "GHz": Decimal(1_000_000_000),
    },
    "impedance": {"ohm": Decimal(1), "kohm": Decimal(1_000)},
    "voltage": {"V": Decimal(1), "mV": Decimal("0.001")},
    "current": {"A": Decimal(1), "mA": Decimal("0.001")},
}
CANONICAL_UNIT: dict[str, str] = {
    "length": "nm", "time": "ps", "frequency": "Hz", "impedance": "ohm",
    "voltage": "V", "current": "A",
}

# Parameter names and their physical dimension per rule kind. A delay rule that
# receives "mm" is rejected instead of being compared to a time value.
PARAMETER_DIMENSIONS: dict[str, dict[str, Dimension]] = {
    "clearance": {"min": "length"},
    "track_width": {"min": "length"},
    "via_drill": {"min": "length"},
    "via_outer": {"min": "length"},
    "via_annular_ring": {"min": "length"},
    "board_edge_clearance": {"min": "length"},
    "hole_clearance": {"min": "length"},
    "keepout": {"min": "length"},
    "impedance": {"min": "impedance", "max": "impedance", "target": "impedance"},
    "propagation_delay": {"min": "time", "max": "time", "target": "time"},
}
MINIMUM_KINDS = {
    "clearance", "track_width", "via_drill", "via_outer", "via_annular_ring",
    "board_edge_clearance", "hole_clearance", "keepout",
}
INTERVAL_KINDS = {"impedance", "propagation_delay"}
# Only these types depend on which net a copper object belongs to. Via, hole
# and board-edge rules are properties of the object, so they are not split per
# net (that would make a false "net X needs 0.2 mm to holes" claim).
NET_SENSITIVE_KINDS = {"clearance", "track_width", "keepout"}
VIA_KINDS = {"via_drill", "via_outer", "via_annular_ring"}
SUPPORTED_KINDS = MINIMUM_KINDS | INTERVAL_KINDS


# --------------------------------------------------------------------------- #
# Rule pack vocabulary
# --------------------------------------------------------------------------- #

class QuantitySpec(DTO):
    """Raw value before compilation; the unit is deliberately not validated yet."""

    value: float
    unit: str = ""


class UnitQuantity(DTO):
    value: float
    unit: str
    dimension: Dimension
    canonical_value: float
    canonical_unit: str
    text: str = ""


class TypedSelector(DTO):
    """Typed object selector. ``all`` is explicit; an empty selector is an error."""

    all: bool = False
    layers: list[str] = Field(default_factory=list)
    net_classes: list[str] = Field(default_factory=list)
    nets: list[str] = Field(default_factory=list)
    components: list[str] = Field(default_factory=list)
    net_scopes: list[str] = Field(default_factory=list)


class ApplicabilityExpr(DTO):
    min_layer_count: int | None = None
    max_layer_count: int | None = None
    requires_footprint: bool = False
    requires_stackup: bool = False
    requires_geometry: bool = False


class RuleSource(DTO):
    source_id: str
    title: str = ""
    kind: str = "engineering_spec"
    note: str = ""


class ConstraintRule(DTO):
    rule_id: str
    kind: str
    hard: bool = True
    required: bool = True
    severity: Severity = "error"
    selector: TypedSelector
    parameters: dict[str, QuantitySpec] = Field(default_factory=dict)
    precondition: ApplicabilityExpr = Field(default_factory=ApplicabilityExpr)
    sources: list[str] = Field(default_factory=list)
    intent: str = ""


class RulePack(DTO):
    pack_id: str
    version: int = 1
    kind: Literal["manufacturing", "platform", "user"]
    title: str = ""
    purpose: str = ""
    sources: list[RuleSource] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list)
    rules: list[ConstraintRule] = Field(default_factory=list)


# --------------------------------------------------------------------------- #
# Compiled result vocabulary
# --------------------------------------------------------------------------- #

class ConstraintDiagnostic(DTO):
    diagnostic_id: str
    code: str
    severity: Severity = "error"
    message: str
    pack_id: str = ""
    rule_id: str = ""
    object_refs: list[str] = Field(default_factory=list)
    evidence: dict[str, Any] = Field(default_factory=dict)


class ConstraintConflict(DTO):
    conflict_id: str
    kind: str
    message: str
    rule_keys: list[str] = Field(default_factory=list)
    effective: dict[str, UnitQuantity] = Field(default_factory=dict)
    object_refs: list[str] = Field(default_factory=list)
    focus_nm: list[int] | None = None
    sources: list[str] = Field(default_factory=list)


class RuleCoverage(DTO):
    rule_key: str
    pack_id: str
    rule_id: str
    kind: str
    required: bool = True
    hard: bool = True
    severity: Severity = "error"
    status: CoverageStatus
    reason: str = ""
    message: str = ""
    applied: dict[str, UnitQuantity] = Field(default_factory=dict)
    clamped: bool = False
    object_refs: list[str] = Field(default_factory=list)
    sources: list[str] = Field(default_factory=list)
    constraint_ids: list[str] = Field(default_factory=list)


class CompiledConstraint(DTO):
    constraint_id: str
    kind: str
    hard: bool = True
    effective: dict[str, UnitQuantity] = Field(default_factory=dict)
    rule_keys: list[str] = Field(default_factory=list)
    object_refs: list[str] = Field(default_factory=list)
    focus_nm: list[int] | None = None
    sources: list[str] = Field(default_factory=list)


class SolverReport(DTO):
    backend: Literal["interval", "ortools"] = "interval"
    status: CompileStatus = "SAT"
    complete: bool = True
    elapsed_ms: float = 0.0
    budget_seconds: float = 0.0
    note: str = ""


class CompiledConstraints(DTO):
    schema_version: int = 2
    status: CompileStatus
    review_ready: bool = False
    packs: list[str] = Field(default_factory=list)
    constraints: list[CompiledConstraint] = Field(default_factory=list)
    soft_constraints: list[CompiledConstraint] = Field(default_factory=list)
    conflicts: list[ConstraintConflict] = Field(default_factory=list)
    coverage: list[RuleCoverage] = Field(default_factory=list)
    diagnostics: list[ConstraintDiagnostic] = Field(default_factory=list)
    unresolved_rules: list[str] = Field(default_factory=list)
    solver: SolverReport = Field(default_factory=SolverReport)
    content_hash: str = ""


def map_solver_status(raw: str) -> CompileStatus:
    """Map an external solver status name without importing that solver.

    OR-Tools CP-SAT reports ``MODEL_INVALID`` for malformed models. Everything
    that is not a proven answer (including ``UNKNOWN`` and timeouts) maps to
    ``UNKNOWN`` so it can never be presented as a proof of infeasibility.
    """

    normalized = str(raw).strip().upper()
    if normalized in {"OPTIMAL", "FEASIBLE"}:
        return "SAT"
    if normalized == "INFEASIBLE":
        return "INFEASIBLE"
    if normalized == "MODEL_INVALID":
        return "INVALID"
    return "UNKNOWN"


# --------------------------------------------------------------------------- #
# Small helpers
# --------------------------------------------------------------------------- #

class _BudgetExhausted(Exception):
    pass


class _Sink:
    def __init__(self) -> None:
        self.items: list[ConstraintDiagnostic] = []

    def add(self, code: str, message: str, *, severity: Severity = "error",
            pack_id: str = "", rule_id: str = "",
            object_refs: list[str] | None = None,
            evidence: dict[str, Any] | None = None) -> ConstraintDiagnostic:
        item = ConstraintDiagnostic(
            diagnostic_id=f"diag_{len(self.items) + 1:03d}", code=code,
            severity=severity, message=message, pack_id=pack_id,
            rule_id=rule_id, object_refs=list(object_refs or []),
            evidence=dict(evidence or {}))
        self.items.append(item)
        return item

    @property
    def errors(self) -> list[ConstraintDiagnostic]:
        return [item for item in self.items if item.severity == "error"]


def _quantity_text(dimension: str, value: float) -> str:
    if dimension == "length":
        return f"{value / 1_000_000:.3f} mm".replace(".000 mm", " mm")
    if dimension == "time":
        return f"{value:.1f} ps"
    if dimension == "impedance":
        return f"{value:.1f} ohm"
    if dimension == "frequency":
        return f"{value:.0f} Hz"
    if dimension == "voltage":
        return f"{value:.3f} V"
    return f"{value:.3f} A"


def _unit_quantity(dimension: Dimension, canonical: float) -> UnitQuantity:
    return UnitQuantity(
        value=canonical, unit=CANONICAL_UNIT[dimension], dimension=dimension,
        canonical_value=canonical, canonical_unit=CANONICAL_UNIT[dimension],
        text=_quantity_text(dimension, canonical))


def _parse_quantity(spec: QuantitySpec, dimension: Dimension, *,
                    pack_id: str, rule_id: str, parameter: str,
                    sink: _Sink) -> UnitQuantity | None:
    if not spec.unit:
        sink.add("MISSING_UNIT",
                 f"{rule_id}:{parameter} has no unit; a bare number is not a "
                 f"{dimension} quantity.", pack_id=pack_id, rule_id=rule_id,
                 evidence={"parameter": parameter, "expected_dimension": dimension})
        return None
    table = UNIT_TABLE.get(dimension, {})
    if spec.unit not in table:
        other = next((name for name, units in UNIT_TABLE.items()
                      if spec.unit in units), None)
        if other is not None:
            sink.add("UNIT_MISMATCH",
                     f"{rule_id}:{parameter} expects a {dimension} quantity but "
                     f"unit {spec.unit!r} is {other}. Mixed dimensions are "
                     f"rejected, not coerced.",
                     pack_id=pack_id, rule_id=rule_id,
                     evidence={"parameter": parameter, "unit": spec.unit,
                               "expected_dimension": dimension, "actual_dimension": other})
            return None
        sink.add("UNKNOWN_UNIT", f"{rule_id}:{parameter} uses unknown unit {spec.unit!r}.",
                 pack_id=pack_id, rule_id=rule_id,
                 evidence={"parameter": parameter, "unit": spec.unit,
                           "expected_dimension": dimension})
        return None
    if not math.isfinite(spec.value) or spec.value < 0:
        sink.add("INVALID_VALUE",
                 f"{rule_id}:{parameter} must be a finite non-negative number.",
                 pack_id=pack_id, rule_id=rule_id,
                 evidence={"parameter": parameter, "value": spec.value})
        return None
    canonical = Decimal(str(spec.value)) * table[spec.unit]
    if dimension == "length":
        canonical = canonical.quantize(Decimal(1))
    value = float(canonical)
    return UnitQuantity(
        value=spec.value, unit=spec.unit, dimension=dimension,
        canonical_value=value, canonical_unit=CANONICAL_UNIT[dimension],
        text=_quantity_text(dimension, value))


def _layer_names(board: BoardIR) -> list[str]:
    count = max(1, int(board.layer_count or 2))
    if count <= 2:
        return ["F.Cu", "B.Cu"]
    return ["F.Cu", *(f"In{i}.Cu" for i in range(1, count - 1)), "B.Cu"]


def _stackup_ready(board: BoardIR) -> bool:
    stackup = getattr(board, "stackup", None)
    if stackup is None:
        return False
    layers = getattr(stackup, "layers", None) or []
    dielectrics = [layer for layer in layers
                   if getattr(layer, "kind", None) == "dielectric"]
    return bool(dielectrics) and all(getattr(layer, "material_ref", None)
                                     for layer in dielectrics)


def _net_ref(net: Net) -> str:
    return f"net:{net.name}"


def _net_index(circuit: CircuitIR) -> dict[str, Net]:
    index: dict[str, Net] = {}
    for net in circuit.nets:
        index[net.name] = net
        index[net.id] = net
    return index


def _component_index(circuit: CircuitIR) -> dict[str, Any]:
    index: dict[str, Any] = {}
    for component in circuit.components:
        index[component.id] = component
        index[component.reference] = component
    return index


def _selected_nets(circuit: CircuitIR, selector: TypedSelector) -> list[Net]:
    components = _component_index(circuit)
    selected: list[Net] = []
    for net in circuit.nets:
        if selector.all:
            selected.append(net)
            continue
        if selector.nets and net.name not in selector.nets and net.id not in selector.nets:
            continue
        if selector.net_classes and net.net_class not in selector.net_classes:
            continue
        if selector.components:
            hit = False
            for token in net.pins:
                component_id, _, _pin = token.partition(":")
                component = components.get(component_id)
                if component is None:
                    continue
                if (component.id in selector.components
                        or component.reference in selector.components
                        or component.instance_path in selector.components):
                    hit = True
                    break
            if not hit:
                continue
        if selector.net_scopes:
            scopes = {
                components[token.partition(":")[0]].instance_path
                for token in net.pins
                if token.partition(":")[0] in components
                and components[token.partition(":")[0]].instance_path
            }
            if not scopes & set(selector.net_scopes):
                continue
        selected.append(net)
    return selected


def _net_focus(board: BoardIR, circuit: CircuitIR,
               net_names: list[str]) -> list[int] | None:
    ids = {net.id for net in circuit.nets if net.name in set(net_names)}
    ids |= {net.id for net in circuit.nets if net.id in set(net_names)}
    if not ids:
        return None
    xs: list[int] = []
    ys: list[int] = []
    for pad in board.pads:
        if pad.net_id in ids:
            xs.extend((pad.x_nm - pad.w_nm // 2, pad.x_nm + pad.w_nm // 2))
            ys.extend((pad.y_nm - pad.h_nm // 2, pad.y_nm + pad.h_nm // 2))
    for track in board.tracks:
        if track.net_id in ids:
            for x, y in track.points:
                xs.append(x)
                ys.append(y)
    for via in board.vias:
        if via.net_id in ids:
            xs.append(via.x_nm)
            ys.append(via.y_nm)
    if not xs:
        return None
    return [min(xs), min(ys), max(xs), max(ys)]


@dataclass(frozen=True)
class _Target:
    kind: Literal["geometric", "net"]
    key: str
    refs: tuple[str, ...]
    net_name: str | None = None


@dataclass
class _RuleRecord:
    pack: RulePack
    rule: ConstraintRule
    key: str
    params: dict[str, UnitQuantity] = field(default_factory=dict)


@dataclass
class _Bound:
    lo: float | None = None
    hi: float | None = None
    lo_rules: list[str] = field(default_factory=list)
    hi_rules: list[str] = field(default_factory=list)
    sources: set[str] = field(default_factory=set)


# --------------------------------------------------------------------------- #
# Validation
# --------------------------------------------------------------------------- #

def _validate_packs(packs: list[RulePack], board: BoardIR,
                    sink: _Sink) -> tuple[list[_RuleRecord], dict[str, str]]:
    records: list[_RuleRecord] = []
    rejected: dict[str, str] = {}
    seen_packs: set[str] = set()
    seen_rules: set[str] = set()
    layers = set(_layer_names(board))

    for pack in packs:
        pack_problem = ""
        if not pack.pack_id.strip():
            pack_problem = "pack_id is empty"
            sink.add("PACK_ID_MISSING", "A rule pack has no pack_id.",
                     evidence={"version": pack.version})
        elif pack.pack_id in seen_packs:
            pack_problem = "duplicate pack_id"
            sink.add("DUPLICATE_PACK",
                     f"Rule pack {pack.pack_id!r} is supplied more than once.",
                     pack_id=pack.pack_id)
        seen_packs.add(pack.pack_id)
        if pack.version < 1:
            pack_problem = pack_problem or "invalid version"
            sink.add("PACK_VERSION", f"{pack.pack_id}: version must be >= 1.",
                     pack_id=pack.pack_id, evidence={"version": pack.version})
        if not pack.sources:
            pack_problem = pack_problem or "missing sources"
            sink.add("PACK_SOURCE_MISSING",
                     f"{pack.pack_id}: a rule pack without sources cannot be "
                     f"cited; add at least one source.",
                     pack_id=pack.pack_id)
        if not pack.rules:
            pack_problem = pack_problem or "no rules"
            sink.add("PACK_NO_RULES",
                     f"{pack.pack_id}: a pack without rules would make an "
                     f"empty contract look satisfied.",
                     pack_id=pack.pack_id)
        source_ids = {source.source_id for source in pack.sources}

        for rule in pack.rules:
            key = f"{pack.pack_id}:{rule.rule_id}"
            problems: list[str] = []
            if not rule.rule_id.strip():
                problems.append("empty rule_id")
                sink.add("RULE_ID_MISSING", f"{pack.pack_id}: a rule has no rule_id.",
                         pack_id=pack.pack_id)
            elif key in seen_rules:
                problems.append("duplicate rule")
                sink.add("DUPLICATE_RULE", f"Rule {key!r} is declared twice.",
                         pack_id=pack.pack_id, rule_id=rule.rule_id)
            seen_rules.add(key)

            if rule.kind not in SUPPORTED_KINDS:
                problems.append("unknown kind")
                sink.add("UNKNOWN_RULE_KIND",
                         f"{key}: unsupported rule kind {rule.kind!r}.",
                         pack_id=pack.pack_id, rule_id=rule.rule_id,
                         evidence={"kind": rule.kind,
                                   "supported": sorted(SUPPORTED_KINDS)})

            selector = rule.selector
            selector_fields = [selector.layers, selector.net_classes,
                               selector.nets, selector.components,
                               selector.net_scopes]
            if selector.all and any(selector_fields):
                problems.append("ambiguous selector")
                sink.add("SELECTOR_AMBIGUOUS",
                         f"{key}: selector all=true cannot be combined with "
                         f"specific layers/nets/components.",
                         pack_id=pack.pack_id, rule_id=rule.rule_id)
            elif not selector.all and not any(selector_fields):
                problems.append("empty selector")
                sink.add("EMPTY_SELECTOR",
                         f"{key}: required={rule.required} rules need a typed "
                         f"selector; an empty selector silently applies to "
                         f"nothing and is rejected.",
                         pack_id=pack.pack_id, rule_id=rule.rule_id,
                         evidence={"required": rule.required})
            unknown_layers = [name for name in selector.layers if name not in layers]
            if unknown_layers:
                problems.append("unknown layer")
                sink.add("SELECTOR_UNKNOWN_LAYER",
                         f"{key}: layers {unknown_layers} are not on a "
                         f"{len(layers)}-layer board.",
                         pack_id=pack.pack_id, rule_id=rule.rule_id,
                         object_refs=[f"layer:{name}" for name in unknown_layers],
                         evidence={"layers": unknown_layers,
                                   "board_layers": sorted(layers)})

            params: dict[str, UnitQuantity] = {}
            if rule.kind in SUPPORTED_KINDS:
                dimensions = PARAMETER_DIMENSIONS[rule.kind]
                for name in rule.parameters:
                    if name not in dimensions:
                        problems.append("unknown parameter")
                        sink.add("UNKNOWN_PARAMETER",
                                 f"{key}: rule kind {rule.kind} has no parameter "
                                 f"{name!r}.",
                                 pack_id=pack.pack_id, rule_id=rule.rule_id,
                                 evidence={"parameter": name,
                                           "supported": sorted(dimensions)})
                expected = ("min",) if rule.kind in MINIMUM_KINDS else ()
                for name in expected:
                    if name not in rule.parameters:
                        problems.append("missing parameter")
                        sink.add("MISSING_PARAMETER",
                                 f"{key}: {rule.kind} requires parameter {name!r}.",
                                 pack_id=pack.pack_id, rule_id=rule.rule_id,
                                 evidence={"parameter": name})
                if rule.kind in INTERVAL_KINDS and not (
                        {"min", "max", "target"} & set(rule.parameters)):
                    problems.append("missing interval")
                    sink.add("MISSING_PARAMETER",
                             f"{key}: {rule.kind} needs at least one of "
                             f"min/max/target.",
                             pack_id=pack.pack_id, rule_id=rule.rule_id)
                for name, spec in rule.parameters.items():
                    dimension = dimensions.get(name)
                    if dimension is None:
                        continue
                    parsed = _parse_quantity(
                        spec, dimension, pack_id=pack.pack_id,
                        rule_id=rule.rule_id, parameter=name, sink=sink)
                    if parsed is None:
                        problems.append("invalid parameter")
                    else:
                        params[name] = parsed
                if "min" in params and "max" in params:
                    if params["min"].canonical_value > params["max"].canonical_value:
                        problems.append("min above max")
                        sink.add("PARAMETER_RANGE_INVALID",
                                 f"{key}: min {params['min'].text} is above max "
                                 f"{params['max'].text}.",
                                 pack_id=pack.pack_id, rule_id=rule.rule_id,
                                 evidence={"min": params["min"].model_dump(),
                                           "max": params["max"].model_dump()})
                if "target" in params:
                    target = params["target"].canonical_value
                    if "min" in params and params["min"].canonical_value > target:
                        problems.append("target below min")
                        sink.add("PARAMETER_RANGE_INVALID",
                                 f"{key}: target {params['target'].text} is below "
                                 f"min {params['min'].text}.",
                                 pack_id=pack.pack_id, rule_id=rule.rule_id)
                    if "max" in params and params["max"].canonical_value < target:
                        problems.append("target above max")
                        sink.add("PARAMETER_RANGE_INVALID",
                                 f"{key}: target {params['target'].text} is above "
                                 f"max {params['max'].text}.",
                                 pack_id=pack.pack_id, rule_id=rule.rule_id)
                if not rule.sources:
                    problems.append("missing rule source")
                    sink.add("RULE_SOURCE_MISSING",
                             f"{key}: every rule must cite at least one source.",
                             pack_id=pack.pack_id, rule_id=rule.rule_id)
                else:
                    unknown_sources = [ref for ref in rule.sources
                                       if ref not in source_ids]
                    if unknown_sources:
                        problems.append("unknown source")
                        sink.add("UNKNOWN_SOURCE",
                                 f"{key}: sources {unknown_sources} are not "
                                 f"declared by {pack.pack_id}.",
                                 pack_id=pack.pack_id, rule_id=rule.rule_id,
                                 evidence={"sources": unknown_sources,
                                           "declared": sorted(source_ids)})

            if pack_problem or problems:
                rejected[key] = "; ".join([pack_problem] if pack_problem else problems)
            else:
                records.append(_RuleRecord(pack=pack, rule=rule, key=key, params=params))
    return records, rejected


# --------------------------------------------------------------------------- #
# Target resolution and applicability
# --------------------------------------------------------------------------- #

def _targets_for(rule: ConstraintRule, circuit: CircuitIR,
                 board: BoardIR) -> list[_Target]:
    selected = _selected_nets(circuit, rule.selector)
    if rule.kind in INTERVAL_KINDS:
        return [
            _Target(kind="net", key=f"net:{net.name}",
                    refs=(_net_ref(net),), net_name=net.name)
            for net in selected]

    layers = rule.selector.layers or _layer_names(board)
    if rule.kind in VIA_KINDS:
        return [_Target(kind="geometric", key="vias", refs=("vias",))]
    if rule.kind not in NET_SENSITIVE_KINDS:
        return [_Target(kind="geometric", key=f"layer:{layer}",
                        refs=(f"layer:{layer}",))
                for layer in layers]
    if not selected:
        return [_Target(kind="geometric", key=f"layer:{layer}|net:*",
                        refs=(f"layer:{layer}", "net:*"))
                for layer in layers]
    many = rule.selector.all or len(selected) > 1
    targets: list[_Target] = []
    for layer in layers:
        for net in selected:
            refs = [f"layer:{layer}", _net_ref(net)]
            if many:
                refs.append(f"net_class:{net.net_class}")
            targets.append(_Target(
                kind="geometric", key=f"layer:{layer}|net:{net.name}",
                refs=tuple(refs), net_name=net.name))
    return targets


def _applicability(rule: ConstraintRule, circuit: CircuitIR, board: BoardIR,
                   targets: list[_Target]) -> tuple[CoverageStatus, str, str, list[str]]:
    precondition = rule.precondition
    layer_count = max(1, int(board.layer_count or 2))
    if precondition.min_layer_count is not None and layer_count < precondition.min_layer_count:
        return ("SKIPPED_PRECONDITION", "LAYER_COUNT_BELOW",
                f"board has {layer_count} layers; rule needs at least "
                f"{precondition.min_layer_count}.", [])
    if precondition.max_layer_count is not None and layer_count > precondition.max_layer_count:
        return ("SKIPPED_PRECONDITION", "LAYER_COUNT_ABOVE",
                f"board has {layer_count} layers; rule allows at most "
                f"{precondition.max_layer_count}.", [])

    selected_components = _target_components(circuit, targets)
    net_refs = sorted({f"net:{target.net_name}" for target in targets
                       if target.net_name})
    if precondition.requires_footprint:
        missing = [component.reference for component in selected_components
                   if not component.footprint_id]
        if missing:
            return ("NOT_CHECKED", "MISSING_FOOTPRINT",
                    "footprint is not assigned; the rule still applies and "
                    "cannot be evaluated yet.",
                    [f"component:{ref}" for ref in missing] + net_refs)
        board_ids = {footprint.component_id for footprint in board.footprints}
        absent = [component.reference for component in selected_components
                  if component.on_board and component.id not in board_ids]
        if absent and board.footprints:
            return ("NOT_CHECKED", "MISSING_BOARD_FOOTPRINT",
                    "the board has no footprint instance for the selected "
                    "component; the rule still applies and cannot be "
                    "evaluated yet.",
                    [f"component:{ref}" for ref in absent] + net_refs)

    if precondition.requires_stackup and not _stackup_ready(board):
        return ("NOT_CHECKED", "MISSING_STACKUP",
                "stackup material data is not declared; delay/impedance rules "
                "remain applicable and are NOT_CHECKED, not skipped.",
                ["board:stackup", *net_refs])

    if precondition.requires_geometry:
        selected_nets = _selected_nets(circuit, rule.selector)
        net_ids = {net.id for net in selected_nets}
        has_geometry = any(pad.net_id in net_ids for pad in board.pads) or any(
            track.net_id in net_ids for track in board.tracks)
        if not has_geometry:
            return ("NOT_CHECKED", "MISSING_GEOMETRY",
                    "no copper geometry exists for the selected nets; the rule "
                    "still applies and cannot be evaluated yet.",
                    [f"net:{net.name}" for net in selected_nets])
    return ("APPLIED", "", "", [])


def _target_components(circuit: CircuitIR, targets: list[_Target]) -> list[Any]:
    names = {target.net_name for target in targets if target.net_name}
    if not names:
        return [component for component in circuit.components if component.on_board]
    ids: set[str] = set()
    for net in circuit.nets:
        if net.name in names:
            ids |= {token.partition(":")[0] for token in net.pins}
    return [component for component in circuit.components if component.id in ids]


# --------------------------------------------------------------------------- #
# Intersection
# --------------------------------------------------------------------------- #

def _apply(bound: _Bound, *, lo: float | None, hi: float | None,
           rule_key: str, sources: list[str]) -> None:
    if lo is not None:
        if bound.lo is None or lo > bound.lo:
            bound.lo = lo
            bound.lo_rules = [rule_key]
        elif lo == bound.lo and rule_key not in bound.lo_rules:
            bound.lo_rules.append(rule_key)
    if hi is not None:
        if bound.hi is None or hi < bound.hi:
            bound.hi = hi
            bound.hi_rules = [rule_key]
        elif hi == bound.hi and rule_key not in bound.hi_rules:
            bound.hi_rules.append(rule_key)
    bound.sources.update(sources)


def _bounds_of(record: _RuleRecord) -> tuple[float | None, float | None]:
    params = record.params
    if record.rule.kind in MINIMUM_KINDS:
        return params["min"].canonical_value, None
    lo = params["min"].canonical_value if "min" in params else None
    hi = params["max"].canonical_value if "max" in params else None
    if "target" in params:
        target = params["target"].canonical_value
        lo = target if lo is None else max(lo, target)
        hi = target if hi is None else min(hi, target)
    return lo, hi


def _constraint_id(kind: str, hard: bool, effective: dict[str, float],
                   refs: list[str]) -> str:
    payload = json.dumps(
        {"kind": kind, "hard": hard,
         "effective": {name: round(value, 6) for name, value in sorted(effective.items())},
         "refs": sorted(refs)}, sort_keys=True, separators=(",", ":"))
    return "cc_" + hashlib.sha256(payload.encode("utf-8")).hexdigest()[:12]


def _group_constraints(
        bounds: dict[tuple[str, str], dict[str, _Bound]],
        hard: bool, refs_by_key: dict[str, tuple[str, ...]]) -> list[CompiledConstraint]:
    groups: dict[tuple, dict[str, Any]] = {}
    for (kind, target_key), params in bounds.items():
        effective = {}
        rule_keys: set[str] = set()
        sources: set[str] = set()
        for name, bound in params.items():
            effective[name] = bound.lo if bound.lo is not None else bound.hi
            rule_keys.update(bound.lo_rules)
            rule_keys.update(bound.hi_rules)
            sources.update(bound.sources)
        signature = (kind, hard, tuple(sorted((name, round(value, 6))
                                              for name, value in effective.items())))
        entry = groups.setdefault(signature, {
            "kind": kind, "hard": hard, "effective": effective,
            "rule_keys": set(), "sources": set(), "refs": set(),
        })
        entry["rule_keys"].update(rule_keys)
        entry["sources"].update(sources)
        entry["refs"].update(refs_by_key.get(target_key, ()))

    compiled: list[CompiledConstraint] = []
    for entry in groups.values():
        kind = entry["kind"]
        refs = sorted(entry["refs"])
        quantities = {
            name: _unit_quantity(PARAMETER_DIMENSIONS[kind][name], value)
            for name, value in entry["effective"].items()
        }
        compiled.append(CompiledConstraint(
            constraint_id=_constraint_id(
                kind, hard, entry["effective"], refs),
            kind=kind, hard=hard, effective=quantities,
            rule_keys=sorted(entry["rule_keys"]),
            object_refs=refs,
            sources=sorted(entry["sources"])))
    compiled.sort(key=lambda item: (item.kind, item.constraint_id))
    return compiled


# --------------------------------------------------------------------------- #
# Public entry point
# --------------------------------------------------------------------------- #

def compile_constraints(circuit: CircuitIR, board: BoardIR,
                        packs: list[RulePack], *,
                        budget_seconds: float = 2.0) -> CompiledConstraints:
    started = time.perf_counter()

    def elapsed_ms() -> float:
        return (time.perf_counter() - started) * 1000.0

    def check_budget() -> None:
        if (time.perf_counter() - started) > budget_seconds:
            raise _BudgetExhausted

    sink = _Sink()
    if not packs:
        sink.add("NO_RULES",
                 "No rule pack was supplied. An empty rule set cannot be "
                 "reported as a satisfied contract.")
    records, rejected = _validate_packs(packs, board, sink)

    if sink.errors:
        coverage = _coverage_stub(packs, rejected, reason="pack invalid")
        return CompiledConstraints(
            status="INVALID", review_ready=False,
            packs=[pack.pack_id for pack in packs], coverage=coverage,
            diagnostics=sink.items, unresolved_rules=[
                item.rule_key for item in coverage
                if item.required and item.status in {"REJECTED", "NOT_COMPILED"}],
            solver=SolverReport(
                backend="interval", status="INVALID", complete=True,
                elapsed_ms=elapsed_ms(), budget_seconds=budget_seconds,
                note="Rule-pack errors prevented a trustworthy contract; this "
                     "is not a statement that the design is impossible."),
            content_hash=_content_hash(circuit, board, packs))

    hard_bounds: dict[tuple[str, str], dict[str, _Bound]] = {}
    soft_bounds: dict[tuple[str, str], dict[str, _Bound]] = {}
    refs_by_key: dict[str, tuple[str, ...]] = {}
    coverage: list[RuleCoverage] = []
    rule_values: dict[str, tuple[str, float | None, float | None]] = {}
    rule_targets: dict[str, set[str]] = {}

    try:
        for record in records:
            check_budget()
            targets = _targets_for(record.rule, circuit, board)
            if not targets:
                coverage.append(_coverage(
                    record, "SKIPPED_NO_TARGET", "NO_MATCH",
                    "the selector matched no object in this design; the rule "
                    "is not applicable here.", []))
                continue
            for target in targets:
                refs_by_key[target.key] = target.refs
            status, reason, message, refs = _applicability(
                record.rule, circuit, board, targets)
            if status != "APPLIED":
                coverage.append(_coverage(record, status, reason, message, refs))
                continue

            lo, hi = _bounds_of(record)
            rule_values[record.key] = (record.rule.kind, lo, hi)
            rule_targets[record.key] = {target.key for target in targets}
            bucket = hard_bounds if record.rule.hard else soft_bounds
            for target in targets:
                check_budget()
                params = bucket.setdefault((record.rule.kind, target.key), {})
                for name in record.params:
                    check_budget()
                    bound = params.setdefault(name, _Bound())
                    _apply(bound, lo=lo if name in {"min", "target"} else None,
                           hi=hi if name in {"max", "target"} else None,
                           rule_key=record.key, sources=record.rule.sources)
            coverage.append(_coverage(record, "APPLIED", "",
                                      "rule applied to "
                                      f"{len(targets)} target(s).",
                                      [ref for target in targets
                                       for ref in target.refs]))

        conflicts = _conflicts(hard_bounds, refs_by_key, circuit, board)
        hard_constraints = _group_constraints(hard_bounds, True, refs_by_key)
        soft_constraints = _group_constraints(soft_bounds, False, refs_by_key)
        for constraint in [*hard_constraints, *soft_constraints]:
            net_names = [ref.split(":", 1)[1] for ref in constraint.object_refs
                         if ref.startswith("net:") and ref != "net:*"]
            constraint.focus_nm = _net_focus(board, circuit, net_names)
    except _BudgetExhausted:
        coverage = _coverage_stub(packs, rejected,
                                  reason="budget exhausted")
        return CompiledConstraints(
            status="UNKNOWN", review_ready=False,
            packs=[pack.pack_id for pack in packs], coverage=coverage,
            diagnostics=sink.items + [ConstraintDiagnostic(
                diagnostic_id=f"diag_{len(sink.items) + 1:03d}",
                code="BUDGET_EXHAUSTED", severity="warning",
                message="Constraint compilation stopped at the time budget. "
                        "This is UNKNOWN, not a proof that the design is "
                        "impossible.")],
            unresolved_rules=[item.rule_key for item in coverage if item.required],
            solver=SolverReport(
                backend="interval", status="UNKNOWN", complete=False,
                elapsed_ms=elapsed_ms(), budget_seconds=budget_seconds,
                note="Time budget exhausted; no feasibility claim is made."),
            content_hash=_content_hash(circuit, board, packs))

    _annotate_coverage(coverage, rule_values, rule_targets, hard_bounds,
                       soft_bounds, hard_constraints, soft_constraints)
    unresolved = [item.rule_key for item in coverage
                  if item.required and item.status in {"NOT_CHECKED", "REJECTED"}]
    status: CompileStatus = "INFEASIBLE" if conflicts else "SAT"
    review_ready = status == "SAT" and not unresolved

    return CompiledConstraints(
        status=status, review_ready=review_ready,
        packs=[pack.pack_id for pack in packs],
        constraints=hard_constraints, soft_constraints=soft_constraints,
        conflicts=conflicts, coverage=coverage,
        diagnostics=sink.items, unresolved_rules=unresolved,
        solver=SolverReport(
            backend="interval", status=status, complete=True,
            elapsed_ms=elapsed_ms(), budget_seconds=budget_seconds,
            note=("Hard constraint intervals intersect without contradiction."
                  if status == "SAT" else
                  "A hard constraint intersection is empty. This is a contract "
                  "contradiction, not a routing result.")),
        content_hash=_content_hash(circuit, board, packs))


def _coverage(record: _RuleRecord, status: CoverageStatus, reason: str,
              message: str, refs: list[str]) -> RuleCoverage:
    return RuleCoverage(
        rule_key=record.key, pack_id=record.pack.pack_id,
        rule_id=record.rule.rule_id, kind=record.rule.kind,
        required=record.rule.required, hard=record.rule.hard,
        severity=record.rule.severity, status=status, reason=reason,
        message=message, object_refs=refs, sources=list(record.rule.sources))


def _coverage_stub(packs: list[RulePack], rejected: dict[str, str], *,
                   reason: str) -> list[RuleCoverage]:
    """Cover every declared rule, including those that could not be compiled."""

    seen: set[str] = set()
    items: list[RuleCoverage] = []
    for pack in packs:
        for rule in pack.rules:
            key = f"{pack.pack_id}:{rule.rule_id}"
            if key in seen:
                continue
            seen.add(key)
            if key in rejected:
                items.append(RuleCoverage(
                    rule_key=key, pack_id=pack.pack_id, rule_id=rule.rule_id,
                    kind=rule.kind, required=rule.required, hard=rule.hard,
                    severity=rule.severity, status="REJECTED",
                    reason="PACK_INVALID", message=rejected[key],
                    sources=list(rule.sources)))
            else:
                items.append(RuleCoverage(
                    rule_key=key, pack_id=pack.pack_id, rule_id=rule.rule_id,
                    kind=rule.kind, required=rule.required, hard=rule.hard,
                    severity=rule.severity, status="NOT_COMPILED",
                    reason="NOT_COMPILED", message=reason,
                    sources=list(rule.sources)))
    return items


def _conflicts(bounds: dict[tuple[str, str], dict[str, _Bound]],
               refs_by_key: dict[str, tuple[str, ...]], circuit: CircuitIR,
               board: BoardIR) -> list[ConstraintConflict]:
    conflicts: list[ConstraintConflict] = []
    for (kind, target_key), params in sorted(bounds.items()):
        lows = [bound for bound in params.values() if bound.lo is not None]
        highs = [bound for bound in params.values() if bound.hi is not None]
        if not lows or not highs:
            continue
        lo = max(bound.lo for bound in lows)
        hi = min(bound.hi for bound in highs)
        if lo <= hi + 1e-9:
            continue
        dimension = PARAMETER_DIMENSIONS[kind]["min"] \
            if "min" in PARAMETER_DIMENSIONS[kind] \
            else next(iter(PARAMETER_DIMENSIONS[kind].values()))
        rules: list[str] = []
        sources: set[str] = set()
        for bound in lows:
            if bound.lo is not None and abs(bound.lo - lo) <= 1e-9:
                rules.extend(bound.lo_rules)
                sources.update(bound.sources)
        for bound in highs:
            if bound.hi is not None and abs(bound.hi - hi) <= 1e-9:
                rules.extend(bound.hi_rules)
                sources.update(bound.sources)
        refs = list(refs_by_key.get(target_key, ()))
        net_names = [ref.split(":", 1)[1] for ref in refs if ref.startswith("net:")]
        conflicts.append(ConstraintConflict(
            conflict_id="cf_" + hashlib.sha256(
                f"{kind}|{target_key}|{lo}|{hi}".encode("utf-8")).hexdigest()[:10],
            kind=kind,
            message=(f"{kind} on {', '.join(refs)} requires >= "
                     f"{_quantity_text(dimension, lo)} and <= "
                     f"{_quantity_text(dimension, hi)}."),
            rule_keys=sorted(set(rules)),
            effective={"min": _unit_quantity(dimension, lo),
                       "max": _unit_quantity(dimension, hi)},
            object_refs=refs,
            focus_nm=_net_focus(board, circuit, net_names),
            sources=sorted(sources)))
    return conflicts


def _annotate_coverage(coverage: list[RuleCoverage],
                       rule_values: dict[str, tuple[str, float | None, float | None]],
                       rule_targets: dict[str, set[str]],
                       hard_bounds: dict[tuple[str, str], dict[str, _Bound]],
                       soft_bounds: dict[tuple[str, str], dict[str, _Bound]],
                       hard_constraints: list[CompiledConstraint],
                       soft_constraints: list[CompiledConstraint]) -> None:
    by_rule: dict[str, list[str]] = {}
    for constraint in [*hard_constraints, *soft_constraints]:
        for rule_key in constraint.rule_keys:
            by_rule.setdefault(rule_key, []).append(constraint.constraint_id)
    for entry in coverage:
        entry.constraint_ids = sorted(set(by_rule.get(entry.rule_key, [])))
        if entry.status != "APPLIED" or entry.rule_key not in rule_values:
            continue
        kind, own_lo, own_hi = rule_values[entry.rule_key]
        target_keys = rule_targets.get(entry.rule_key, set())
        bucket = hard_bounds if entry.hard else soft_bounds
        applied: dict[str, UnitQuantity] = {}
        clamped = False
        for (bound_kind, target_key), params in bucket.items():
            if bound_kind != kind or target_key not in target_keys:
                continue
            for name, bound in params.items():
                dimension = PARAMETER_DIMENSIONS[kind][name]
                if name in {"min", "target"} and bound.lo is not None:
                    if (name not in applied
                            or bound.lo > applied[name].canonical_value + 1e-9):
                        applied[name] = _unit_quantity(dimension, bound.lo)
                    if own_lo is not None and bound.lo > own_lo + 1e-9:
                        clamped = True
                elif name == "max" and bound.hi is not None:
                    if (name not in applied
                            or bound.hi < applied[name].canonical_value - 1e-9):
                        applied[name] = _unit_quantity(dimension, bound.hi)
                    if own_hi is not None and bound.hi < own_hi - 1e-9:
                        clamped = True
        entry.applied = applied
        entry.clamped = clamped


def _content_hash(circuit: CircuitIR, board: BoardIR,
                  packs: list[RulePack]) -> str:
    payload = {
        "circuit": circuit.model_dump(mode="json"),
        "board": board.model_dump(mode="json"),
        "packs": [
            {"pack_id": pack.pack_id, "version": pack.version,
             "kind": pack.kind,
             "rules": [rule.model_dump(mode="json") for rule in pack.rules]}
            for pack in packs],
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"),
                         default=str)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


# --------------------------------------------------------------------------- #
# Highspeed request compilation (C11)
# --------------------------------------------------------------------------- #

HIGHSPEED_KINDS = {"length_match", "differential_pair", "propagation_delay"}
HIGHSPEED_PARAMETER_DIMENSIONS: dict[str, dict[str, Dimension]] = {
    "length_match": {"max": "length", "min": "length"},
    "differential_pair": {
        "gap": "length", "gap_tolerance": "length", "skew": "length",
        "uncoupled": "length", "delay_skew": "time",
    },
    "propagation_delay": {"max": "time", "min": "time", "target": "time"},
}


class ChannelNodeSpec(DTO):
    id: str
    kind: Literal["driver", "receiver", "series"] = "receiver"
    component_id: str
    pin: str
    net: str


class ChannelLinkSpec(DTO):
    id: str
    from_node: str
    to_node: str
    kind: Literal["copper", "component"] = "copper"
    internal_length_nm: int = 0


class HighspeedChannelSpec(DTO):
    """Explicit end-to-end channel. Nothing here is inferred from net names."""

    id: str
    net: str = ""
    driver: str
    receivers: list[str]
    series: list[str] = Field(default_factory=list)
    branches: list[str] = Field(default_factory=list)
    nodes: list[ChannelNodeSpec] = Field(default_factory=list)
    links: list[ChannelLinkSpec] = Field(default_factory=list)
    length_tolerance_nm: int = 0
    target_delay_ps: float = 0.0
    delay_tolerance_ps: float = 0.0


class LayerModelSpec(DTO):
    layer: str
    propagation_ps_per_mm: float
    reference_layer: str = ""
    thickness_nm: int = 0
    source: str = ""


class ReferencePlaneSpec(DTO):
    layer: str
    polygons: list[list[list[int]]] = Field(default_factory=list)


class PackageModelSpec(DTO):
    component_id: str
    pin: str = ""
    delay_ps: float = 0.0
    length_nm: int = 0
    present: bool = True
    source: str = ""


class TuningRegionSpec(DTO):
    id: str
    net: str
    start: tuple[int, int]
    end: tuple[int, int]
    layer: str = ""
    width_nm: int = 0
    max_extra_nm: int = 0


class HighspeedCompileResult(DTO):
    status: Literal["SAT", "INVALID", "UNKNOWN"] = "SAT"
    review_ready: bool = False
    request: dict[str, Any] = Field(default_factory=dict)
    coverage: list[RuleCoverage] = Field(default_factory=list)
    diagnostics: list[ConstraintDiagnostic] = Field(default_factory=list)
    si_status: Literal["NOT_CHECKED"] = "NOT_CHECKED"
    pi_status: Literal["NOT_CHECKED"] = "NOT_CHECKED"
    content_hash: str = ""
    note: str = (
        "A compiled highspeed request is not a signal- or power-integrity "
        "result. Geometry and length PASS values never upgrade SI or PI.")


def _hs_coverage(pack: RulePack, rule: ConstraintRule, *, status: CoverageStatus,
                 reason: str, message: str,
                 refs: list[str] | None = None) -> RuleCoverage:
    return RuleCoverage(
        rule_key=f"{pack.pack_id}:{rule.rule_id}", pack_id=pack.pack_id,
        rule_id=rule.rule_id, kind=rule.kind, required=rule.required,
        hard=rule.hard, severity=rule.severity, status=status,
        reason=reason, message=message, object_refs=list(refs or []),
        sources=list(rule.sources))


def _hs_params(rule: ConstraintRule, pack: RulePack, sink: _Sink, *,
               required: tuple[str, ...]) -> dict[str, UnitQuantity] | None:
    dimensions = HIGHSPEED_PARAMETER_DIMENSIONS[rule.kind]
    params: dict[str, UnitQuantity] = {}
    ok = True
    for name, spec in rule.parameters.items():
        dimension = dimensions.get(name)
        if dimension is None:
            sink.add("UNKNOWN_PARAMETER",
                     f"{pack.pack_id}:{rule.rule_id} has no parameter {name!r} "
                     f"for kind {rule.kind}.",
                     pack_id=pack.pack_id, rule_id=rule.rule_id,
                     evidence={"supported": sorted(dimensions)})
            ok = False
            continue
        parsed = _parse_quantity(spec, dimension, pack_id=pack.pack_id,
                                 rule_id=rule.rule_id, parameter=name, sink=sink)
        if parsed is None:
            ok = False
            continue
        params[name] = parsed
    for name in required:
        if name not in params:
            sink.add("MISSING_PARAMETER",
                     f"{pack.pack_id}:{rule.rule_id} requires {name!r}.",
                     pack_id=pack.pack_id, rule_id=rule.rule_id)
            ok = False
    return params if ok else None


def _canonical_length(params: dict[str, UnitQuantity], name: str) -> int:
    quantity = params.get(name)
    return int(round(quantity.canonical_value)) if quantity else 0


def compile_highspeed(
        circuit: CircuitIR, board: BoardIR, packs: list[RulePack], *,
        channels: list[HighspeedChannelSpec] | None = None,
        layer_models: list[LayerModelSpec] | None = None,
        reference_planes: list[ReferencePlaneSpec] | None = None,
        package_models: list[PackageModelSpec] | None = None,
        tuning_regions: list[TuningRegionSpec] | None = None,
        rules: dict[str, Any] | None = None,
        budget_seconds: float = 1.0) -> HighspeedCompileResult:
    """Compile path/group constraints into a native highspeed request.

    Channels must be declared explicitly; they are never derived from net
    names. A channel whose pads are not on the board is left out of the request
    and covered as ``NOT_CHECKED`` instead of shipping invented coordinates.
    """

    started = time.perf_counter()
    sink = _Sink()
    coverage: list[RuleCoverage] = []
    records: list[tuple[RulePack, ConstraintRule, dict[str, UnitQuantity]]] = []

    if not packs:
        sink.add("NO_RULES",
                 "No rule pack was supplied. An empty rule set cannot be "
                 "presented as a satisfied highspeed contract.")

    for pack in packs:
        pack_problem = ""
        if not pack.pack_id.strip():
            pack_problem = "pack_id is empty"
            sink.add("PACK_ID_MISSING", "A rule pack has no pack_id.")
        if not pack.sources:
            pack_problem = pack_problem or "missing sources"
            sink.add("PACK_SOURCE_MISSING",
                     f"{pack.pack_id}: a highspeed pack without sources cannot "
                     f"be cited.", pack_id=pack.pack_id)
        if not pack.rules:
            pack_problem = pack_problem or "no rules"
            sink.add("PACK_NO_RULES",
                     f"{pack.pack_id}: an empty pack would make an unchecked "
                     f"contract look satisfied.", pack_id=pack.pack_id)
        for rule in pack.rules:
            if pack_problem:
                coverage.append(_hs_coverage(
                    pack, rule, status="REJECTED", reason="PACK_INVALID",
                    message=pack_problem))
                continue
            if rule.kind not in HIGHSPEED_KINDS:
                sink.add("UNKNOWN_RULE_KIND",
                         f"{pack.pack_id}:{rule.rule_id}: {rule.kind!r} is not "
                         f"a highspeed rule kind.",
                         pack_id=pack.pack_id, rule_id=rule.rule_id)
                coverage.append(_hs_coverage(
                    pack, rule, status="REJECTED", reason="UNKNOWN_RULE_KIND",
                    message=f"{rule.kind!r} is not compiled here."))
                continue
            required = ("max",) if rule.kind == "length_match" else ()
            params = _hs_params(rule, pack, sink, required=required)
            if params is None:
                coverage.append(_hs_coverage(
                    pack, rule, status="REJECTED", reason="INVALID_PARAMETER",
                    message="Rule parameters are not usable."))
                continue
            if not params:
                message = ("A differential_pair rule needs at least one of "
                           "gap/skew/uncoupled/delay_skew."
                           if rule.kind == "differential_pair" else
                           "A propagation_delay rule needs at least one of "
                           "target/min/max.")
                sink.add("MISSING_PARAMETER",
                         f"{pack.pack_id}:{rule.rule_id}: {message}",
                         pack_id=pack.pack_id, rule_id=rule.rule_id)
                coverage.append(_hs_coverage(
                    pack, rule, status="REJECTED", reason="MISSING_PARAMETER",
                    message=message))
                continue
            records.append((pack, rule, params))

    if sink.errors:
        return HighspeedCompileResult(
            status="INVALID", coverage=coverage, diagnostics=sink.items,
            content_hash=_highspeed_hash(circuit, board, packs, channels,
                                         layer_models, reference_planes,
                                         package_models, tuning_regions))

    pads: dict[tuple[str, str], Any] = {}
    for pad in board.pads:
        pads[(pad.component_id, pad.number)] = pad

    net_names = {net.id: net.name for net in circuit.nets}
    request_channels: list[dict[str, Any]] = []
    channel_nets: dict[str, set[str]] = {}
    channel_payload: dict[str, dict[str, Any]] = {}
    dropped_channels: set[str] = set()

    for spec in channels or []:
        nodes: list[dict[str, Any]] = []
        missing: list[str] = []
        if not spec.nodes:
            dropped_channels.add(spec.id)
            coverage.append(RuleCoverage(
                rule_key=f"channel:{spec.id}", pack_id="", rule_id=spec.id,
                kind="channel", status="NOT_CHECKED", reason="MISSING_NODE",
                message="Channel declares no nodes; there is no terminal to "
                        "resolve against the board.",
                object_refs=[f"channel:{spec.id}"]))
            continue
        for node in spec.nodes:
            pad = pads.get((node.component_id, node.pin)) if node.pin else None
            if pad is None and not node.pin:
                # An empty pin is an explicit wildcard; a wrong pin never
                # falls back to another pin of the same component.
                candidates = [entry for (component, _pin), entry in pads.items()
                              if component == node.component_id]
                pad = candidates[0] if len(candidates) == 1 else None
            if pad is None:
                missing.append(f"{node.component_id}:{node.pin}")
                continue
            nodes.append({
                "id": node.id, "kind": node.kind, "net": node.net,
                "component_id": node.component_id, "pin": node.pin,
                "x_nm": pad.x_nm, "y_nm": pad.y_nm,
            })
        if missing:
            dropped_channels.add(spec.id)
            coverage.append(RuleCoverage(
                rule_key=f"channel:{spec.id}", pack_id="", rule_id=spec.id,
                kind="channel", status="NOT_CHECKED",
                reason="MISSING_PAD",
                message="Channel nodes are not on the board: " +
                        ", ".join(missing) + "; no coordinates were invented.",
                object_refs=[f"pad:{ref}" for ref in missing]))
            continue
        payload = {
            "id": spec.id, "net": spec.net, "driver": spec.driver,
            "receivers": list(spec.receivers), "series": list(spec.series),
            "branches": list(spec.branches), "nodes": nodes,
            "links": [{"id": link.id, "from": link.from_node,
                       "to": link.to_node, "kind": link.kind,
                       "internal_length_nm": link.internal_length_nm}
                      for link in spec.links],
            "length_tolerance_nm": spec.length_tolerance_nm,
            "target_delay_ps": spec.target_delay_ps,
            "delay_tolerance_ps": spec.delay_tolerance_ps,
        }
        request_channels.append(payload)
        channel_payload[spec.id] = payload
        channel_nets[spec.id] = {node.net for node in spec.nodes}

    request_pairs: list[dict[str, Any]] = []
    budget_stop = False
    for pack, rule, params in records:
        if (time.perf_counter() - started) > budget_seconds:
            budget_stop = True
            sink.add("BUDGET_EXHAUSTED",
                     "Highspeed compilation stopped at the time budget. This "
                     "is UNKNOWN, not a proof that a constraint is unusable.",
                     severity="warning")
            break
        selected = _selected_nets(circuit, rule.selector)
        names = [net.name for net in selected]
        refs = [f"net:{name}" for name in names]
        if rule.kind == "length_match":
            tolerance = _canonical_length(params, "max") or \
                _canonical_length(params, "min")
            applied = False
            for spec_id, nets in channel_nets.items():
                if not set(names) & nets:
                    continue
                payload = channel_payload[spec_id]
                payload["length_tolerance_nm"] = max(
                    int(payload["length_tolerance_nm"]), tolerance)
                applied = True
            if applied:
                coverage.append(_hs_coverage(
                    pack, rule, status="APPLIED",
                    reason="",
                    message=f"Length window {tolerance} nm applied to every "
                            f"declared channel on the selected nets.",
                    refs=refs))
            else:
                coverage.append(_hs_coverage(
                    pack, rule, status="NOT_CHECKED", reason="NO_CHANNEL",
                    message="No explicit end-to-end channel was declared for "
                            "the selected nets; a length window cannot be "
                            "evaluated without one.", refs=refs))
            continue

        if rule.kind == "propagation_delay":
            target = params.get("target") or params.get("max")
            tolerance = 0.0
            if target is not None:
                if "min" in params and "max" in params:
                    tolerance = (params["max"].canonical_value -
                                 params["min"].canonical_value) / 2.0
                elif "min" in params and "target" in params:
                    tolerance = (params["target"].canonical_value -
                                 params["min"].canonical_value)
            applied = False
            for spec_id, nets in channel_nets.items():
                if not set(names) & nets:
                    continue
                payload = channel_payload[spec_id]
                if target is not None:
                    payload["target_delay_ps"] = target.canonical_value
                    payload["delay_tolerance_ps"] = tolerance
                applied = True
            status: CoverageStatus = "APPLIED" if applied else "NOT_CHECKED"
            reason = "" if applied else "NO_CHANNEL"
            message = (
                "Delay target applied to declared channels." if applied
                else "No explicit channel exists for the selected nets.")
            if applied and not layer_models:
                status = "NOT_CHECKED"
                reason = "MISSING_STACKUP_MODEL"
                message = ("No per-layer propagation model was supplied; the "
                           "delay rule stays NOT_CHECKED.")
            coverage.append(_hs_coverage(
                pack, rule, status=status, reason=reason, message=message,
                refs=refs))
            continue

        # differential_pair
        rule_refs = [f"rule:{pack.pack_id}:{rule.rule_id}", *refs]
        if len(names) != 2:
            coverage.append(_hs_coverage(
                pack, rule, status="NOT_CHECKED", reason="PAIR_NET_COUNT",
                message=f"A differential pair needs exactly two nets; the "
                        f"selector matched {len(names)}.", refs=rule_refs))
            continue
        pair = {
            "id": f"{pack.pack_id}:{rule.rule_id}",
            "net_p": names[0], "net_n": names[1],
            "gap_nm": _canonical_length(params, "gap"),
            "gap_tolerance_nm": _canonical_length(params, "gap_tolerance"),
            "skew_tolerance_nm": _canonical_length(params, "skew"),
            "max_uncoupled_nm": _canonical_length(params, "uncoupled"),
            "delay_skew_tolerance_ps":
                params["delay_skew"].canonical_value
                if "delay_skew" in params else 0.0,
            "pair_layer_transitions": True,
        }
        request_pairs.append(pair)
        coverage.append(_hs_coverage(
            pack, rule, status="APPLIED", reason="",
            message="Differential pair compiled with the declared gap, skew "
                    "and coupling limits.", refs=refs))

    request: dict[str, Any] = {
        "channels": request_channels,
        "pairs": request_pairs,
        "tracks": [
            {"id": track.id, "net": net_names.get(track.net_id, track.net_id),
             "layer": str(track.layer), "width_nm": track.width_nm,
             "points": [{"x": x, "y": y} for x, y in track.points]}
            for track in board.tracks if len(track.points) >= 2
        ],
        "vias": [
            {"id": via.id, "net": net_names.get(via.net_id, via.net_id),
             "x_nm": via.x_nm, "y_nm": via.y_nm,
             "drill_nm": via.drill_nm, "has_delay": False}
            for via in board.vias
        ],
        "layer_models": [
            {"layer": model.layer, "thickness_nm": model.thickness_nm,
             "reference_layer": model.reference_layer,
             "propagation_ps_per_mm": model.propagation_ps_per_mm,
             "source": model.source}
            for model in layer_models or []
        ],
        "reference_planes": [
            {"layer": plane.layer,
             "polygons": [[{"x": x, "y": y} for x, y in polygon]
                          for polygon in plane.polygons if len(polygon) >= 3]}
            for plane in reference_planes or []
        ],
        "package_models": [
            {"component_id": model.component_id, "pin": model.pin,
             "delay_ps": model.delay_ps, "length_nm": model.length_nm,
             "present": model.present, "source": model.source}
            for model in package_models or []
        ],
        "tuning_regions": [
            {"id": region.id, "net": region.net, "layer": region.layer,
             "start": {"x": region.start[0], "y": region.start[1]},
             "end": {"x": region.end[0], "y": region.end[1]},
             "width_nm": region.width_nm,
             "max_extra_nm": region.max_extra_nm}
            for region in tuning_regions or []
        ],
        "rules": dict(rules or {}),
    }

    status: CompileStatus = "SAT"
    if budget_stop:
        status = "UNKNOWN"
    elif dropped_channels and not request_channels and not request_pairs:
        status = "UNKNOWN"
    return HighspeedCompileResult(
        status=status, review_ready=False, request=request,
        coverage=coverage, diagnostics=sink.items,
        content_hash=_highspeed_hash(circuit, board, packs, channels,
                                     layer_models, reference_planes,
                                     package_models, tuning_regions))


def _highspeed_hash(circuit: CircuitIR, board: BoardIR, packs: list[RulePack],
                    channels: list[HighspeedChannelSpec] | None,
                    layer_models: list[LayerModelSpec] | None,
                    reference_planes: list[ReferencePlaneSpec] | None,
                    package_models: list[PackageModelSpec] | None,
                    tuning_regions: list[TuningRegionSpec] | None) -> str:
    payload = {
        "circuit": circuit.model_dump(mode="json"),
        "board": {
            "outline": board.outline, "layer_count": board.layer_count,
            "pads": [pad.model_dump(mode="json") for pad in board.pads],
            "tracks": [track.model_dump(mode="json") for track in board.tracks],
            "vias": [via.model_dump(mode="json") for via in board.vias],
        },
        "packs": [pack.model_dump(mode="json") for pack in packs],
        "channels": [spec.model_dump(mode="json") for spec in channels or []],
        "layer_models": [model.model_dump(mode="json")
                         for model in layer_models or []],
        "reference_planes": [plane.model_dump(mode="json")
                             for plane in reference_planes or []],
        "package_models": [model.model_dump(mode="json")
                           for model in package_models or []],
        "tuning_regions": [region.model_dump(mode="json")
                           for region in tuning_regions or []],
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"),
                         default=str)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def format_highspeed_report(result: HighspeedCompileResult) -> str:
    lines = [
        "# Highspeed compile report",
        "",
        f"- status: **{result.status}**",
        f"- review ready: {'yes' if result.review_ready else 'no'}",
        f"- SI: {result.si_status}",
        f"- PI: {result.pi_status}",
        f"- content hash: `{result.content_hash}`",
        "",
        result.note,
        "",
        "## Request",
        f"- channels: {len(result.request.get('channels', []))}",
        f"- pairs: {len(result.request.get('pairs', []))}",
        f"- tracks: {len(result.request.get('tracks', []))}",
        f"- vias: {len(result.request.get('vias', []))}",
        f"- layer models: {len(result.request.get('layer_models', []))}",
        f"- package models: {len(result.request.get('package_models', []))}",
        "",
        "## Rule coverage",
    ]
    for entry in result.coverage:
        detail = f" — {entry.message}" if entry.message else ""
        lines.append(f"- `{entry.rule_key}` ({entry.kind}) {entry.status}{detail}")
    if result.diagnostics:
        lines += ["", "## Diagnostics"]
        for item in result.diagnostics:
            lines.append(f"- `{item.code}` ({item.severity}) {item.message}")
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# Report
# --------------------------------------------------------------------------- #

def format_compile_report(result: CompiledConstraints) -> str:
    lines = [
        "# Constraint compile report",
        "",
        f"- status: **{result.status}**",
        f"- review ready: {'yes' if result.review_ready else 'no'}",
        f"- packs: {', '.join(result.packs) or '(none)'}",
        f"- backend: {result.solver.backend} "
        f"(complete={result.solver.complete}, "
        f"{result.solver.elapsed_ms:.2f} ms / budget "
        f"{result.solver.budget_seconds:.2f} s)",
        f"- content hash: `{result.content_hash}`",
        "",
        f"{result.solver.note}",
        "",
        "## Hard constraints",
    ]
    if result.constraints:
        for constraint in result.constraints:
            values = ", ".join(f"{name} {q.text}"
                               for name, q in sorted(constraint.effective.items()))
            lines.append(
                f"- `{constraint.kind}` {values} → "
                f"{', '.join(constraint.object_refs)} "
                f"({', '.join(constraint.rule_keys)})")
    else:
        lines.append("- none")
    lines += ["", "## Soft targets"]
    if result.soft_constraints:
        for constraint in result.soft_constraints:
            values = ", ".join(f"{name} {q.text}"
                               for name, q in sorted(constraint.effective.items()))
            lines.append(
                f"- `{constraint.kind}` {values} → "
                f"{', '.join(constraint.object_refs)}")
    else:
        lines.append("- none")
    lines += ["", "## Conflicts"]
    if result.conflicts:
        for conflict in result.conflicts:
            lines.append(f"- `{conflict.conflict_id}` {conflict.message}")
            lines.append(f"  - rules: {', '.join(conflict.rule_keys)}")
            lines.append(f"  - objects: {', '.join(conflict.object_refs)}")
            if conflict.focus_nm:
                x0, y0, x1, y1 = (value / 1e6 for value in conflict.focus_nm)
                lines.append(f"  - focus: ({x0:.2f}, {y0:.2f}) – "
                             f"({x1:.2f}, {y1:.2f}) mm")
            lines.append(f"  - sources: {', '.join(conflict.sources)}")
    else:
        lines.append("- none")
    lines += ["", "## Rule coverage"]
    for entry in result.coverage:
        flags = [entry.status]
        if entry.required:
            flags.append("required")
        if not entry.hard:
            flags.append("soft")
        if entry.clamped:
            flags.append("clamped")
        detail = f" — {entry.message}" if entry.message else ""
        lines.append(f"- `{entry.rule_key}` ({entry.kind}) "
                     f"{' / '.join(flags)}{detail}")
        if entry.applied:
            lines.append("  - effective: " + ", ".join(
                f"{name} {q.text}" for name, q in sorted(entry.applied.items())))
        if entry.object_refs:
            refs = entry.object_refs[:12]
            suffix = "" if len(entry.object_refs) <= 12 else \
                f" (+{len(entry.object_refs) - 12} more)"
            lines.append(f"  - objects: {', '.join(refs)}{suffix}")
    if result.diagnostics:
        lines += ["", "## Diagnostics"]
        for item in result.diagnostics:
            lines.append(f"- `{item.code}` ({item.severity}) {item.message}")
    return "\n".join(lines)
