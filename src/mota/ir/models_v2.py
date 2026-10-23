"""Versioned component-instance view used by free editing (C2).

`Component` remains the storage projection inside a v1 revision. This module
provides the explicit instance shape that the editor and exports operate on,
so physical identity (component id / instance path) is never confused with a
reference designator or a sheet position.
"""

from __future__ import annotations

from typing import Any, Literal

from mota.domain.models import Component, DTO, Placement

MappingStatus = Literal["ok", "missing_footprint", "unmapped", "pad_not_found",
                        "pinmap_key_unknown", "footprint_unknown", "part_unknown"]


class PinPadMappingV2(DTO):
    component_id: str
    pin: str
    pad_numbers: list[str] = []
    status: MappingStatus = "ok"
    message: str = ""


class ComponentInstanceV2(DTO):
    schema_version: int = 2
    component_id: str
    reference: str
    part_id: str
    instance_path: str
    symbol_id: str
    footprint_id: str = ""
    pin_pad_map: dict[str, list[str]] = {}
    on_board: bool = True
    in_bom: bool = True
    assembly_state: Literal["assembled", "dnp", "not_fitted"] = "assembled"
    placement: Placement | None = None
    symbol_units: list[int] = [1]
    footprint_instance_id: str | None = None
    parameter_bindings: dict[str, str] = {}

    @classmethod
    def from_component(cls, component: Component, *,
                       symbol_units: list[int] | None = None
                       ) -> "ComponentInstanceV2":
        return cls(
            component_id=component.id, reference=component.reference,
            part_id=component.part_id,
            instance_path=component.instance_path or component.reference,
            symbol_id=component.symbol_id,
            footprint_id=component.footprint_id,
            pin_pad_map={key: list(value)
                         for key, value in component.pin_pad_map.items()},
            on_board=component.on_board, in_bom=component.in_bom,
            assembly_state=component.assembly_state,
            placement=component.placement,
            symbol_units=symbol_units or [1],
            footprint_instance_id=component.footprint_instance_id,
            parameter_bindings=dict(component.parameter_bindings),
        )


class ComponentInstanceSet(DTO):
    schema_version: int = 2
    instances: list[ComponentInstanceV2] = []
    mappings: list[PinPadMappingV2] = []


class SymbolUnitViewV2(DTO):
    """Display placement of one unit of a physical component."""

    component_id: str
    sheet_instance_id: str
    unit_id: int = 1
    body_style_id: int = 1
    x_nm: int = 0
    y_nm: int = 0
    rotation_deg: int = 0


class SchematicPageV2(DTO):
    page_id: str
    title: str
    sheet_instance_id: str
    instance_path: str
    page_index: int = 1
    symbols: list[dict] = []
    wires: list[dict] = []
    labels: list[dict] = []
    ports: list[dict] = []
    locked: bool = False


class MultiPageSchematicV2(DTO):
    schema_version: int = 2
    page_w_nm: int
    page_h_nm: int
    pages: list[SchematicPageV2] = []
    unit_views: list[SymbolUnitViewV2] = []
    source: Literal["hierarchy", "single-page"] = "hierarchy"


class HierarchyDiagnostic(DTO):
    id: str
    code: str
    severity: Literal["error", "warning", "info"] = "error"
    instance_path: str = ""
    message: str = ""
    object_ref: str = ""


class CircuitCompileResult(DTO):
    circuit: Any
    schematic: MultiPageSchematicV2
    diagnostics: list[HierarchyDiagnostic] = []
    instance_paths: dict[str, str] = {}
    page_by_component: dict[str, str] = {}
    page_records: list[dict] = []
    unit_views: list[dict] = []
    net_scopes: dict[str, str] = {}
    semantic_hash: str = ""
