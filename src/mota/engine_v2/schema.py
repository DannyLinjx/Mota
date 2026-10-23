"""Versioned engine-v2 envelope layered on the existing v1 IR.

The v1 models remain the authority for the three regression circuits. The v2
envelope adds explicit stackup, constraint, hierarchy and physical-terminal
fields without changing v1 files on disk.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import Field

from mota.domain.models import (
    BoardIR, CircuitIR, Component, DTO, DesignVersion, RequirementSpec,
    SchematicIR, ValidationReport,
)


class StackupLayer(DTO):
    id: str
    order: int
    kind: Literal["copper", "dielectric", "mask"]
    thickness_nm: int
    material_ref: str | None = None
    reference_nets: list[str] = Field(default_factory=list)


class Stackup(DTO):
    schema_version: int = 2
    layers: list[StackupLayer]
    total_thickness_nm: int
    source: Literal["migrated-v1", "declared"] = "migrated-v1"
    assumptions: list[str] = Field(default_factory=list)


class PhysicalTerminal(DTO):
    number: str
    pad_numbers: list[str] = Field(default_factory=list)
    hidden: bool = False
    unit_id: int | None = None
    electrical_type: str = "unknown"


class ComponentV2(Component):
    instance_path: str
    physical_terminals: list[PhysicalTerminal] = Field(default_factory=list)
    symbol_units: list[dict[str, Any]] = Field(default_factory=list)
    on_board: bool = True
    in_bom: bool = True
    assembly_state: Literal["assembled", "dnp", "not_fitted"] = "assembled"


class HierarchyIR(DTO):
    schema_version: int = 2
    modules: list[dict[str, Any]] = Field(default_factory=list)
    instances: list[dict[str, Any]] = Field(default_factory=list)
    buses: list[dict[str, Any]] = Field(default_factory=list)
    unresolved: list[str] = Field(default_factory=list)
    source: Literal["migrated-v1", "declared"] = "migrated-v1"


class CircuitV2(CircuitIR):
    schema_version: int = 2
    components: list[ComponentV2] = Field(default_factory=list)
    hierarchy: HierarchyIR = Field(default_factory=HierarchyIR)


class SchematicV2(SchematicIR):
    schema_version: int = 2
    page_instances: list[dict[str, Any]] = Field(default_factory=list)


class ViaDefinition(DTO):
    id: str
    kind: Literal["through", "blind", "buried", "micro"] = "through"
    start_layer_id: str
    end_layer_id: str
    drill_nm: int
    pad_outer_nm: int
    plating: Literal["plated", "non_plated"] = "plated"
    source: Literal["migrated-v1", "declared"] = "migrated-v1"


class BoardV2(BoardIR):
    schema_version: int = 2
    layer_order: list[str] = Field(default_factory=list)
    stackup: Stackup
    via_definitions: list[ViaDefinition] = Field(default_factory=list)


class ValidationReportV2(ValidationReport):
    schema_version: int = 2
    checker_version: str = "mota-validator-v1"


class ConstraintIR(DTO):
    schema_version: int = 2
    profile_id: str
    rules: list[dict[str, Any]] = Field(default_factory=list)
    unresolved: list[str] = Field(default_factory=list)
    source: Literal["migrated-v1", "declared"] = "migrated-v1"


class SourceRef(DTO):
    source_schema_version: int
    source_hash: str
    source_files: dict[str, str] = Field(default_factory=dict)
    design_hash: str = ""


class UnitContract(DTO):
    length: Literal["nm"] = "nm"
    voltage: Literal["V"] = "V"
    current: Literal["mA"] = "mA"
    time: Literal["s"] = "s"
    frequency: Literal["Hz"] = "Hz"
    coordinate_range_nm: int = 1_000_000_000_000_000


class PreservedFields(DTO):
    unknown_fields: list[str] = Field(default_factory=list)


class EngineDocumentV2(DTO):
    schema_version: Literal[2] = 2
    engine: Literal["mota-engine-v2"] = "mota-engine-v2"
    migration_version: str
    design_version: DesignVersion
    source: SourceRef
    units: UnitContract = Field(default_factory=UnitContract)
    requirement: RequirementSpec
    circuit: CircuitV2
    schematic: SchematicV2
    board: BoardV2
    checks: ValidationReportV2
    constraints: ConstraintIR
    assumptions: list[str] = Field(default_factory=list)
    preserved: PreservedFields = Field(default_factory=PreservedFields)
