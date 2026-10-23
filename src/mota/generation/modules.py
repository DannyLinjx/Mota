"""Circuit synthesis from a template recipe.

Deterministic code owns pin maps, nets and identifiers. The model never writes
these structures directly; it can only request a template and typed patches.
"""

from __future__ import annotations

from mota.catalog import builtin
from mota.catalog.templates import TemplateRecipe
from mota.domain.models import (
    CircuitIR, Component, Net, Placement, RequirementSpec,
)
from mota.domain.units import mm
from mota.errors import ConstraintConflict, NotFound, PinmapMismatch


def slug(text: str) -> str:
    out = []
    for ch in text.lower():
        if ch.isalnum():
            out.append(ch)
        elif ch in ("-", " ", "/", "."):
            out.append("_")
    value = "".join(out).strip("_")
    while "__" in value:
        value = value.replace("__", "_")
    return value


def component_id(reference: str) -> str:
    return f"component_{slug(reference)}"


def net_id(name: str) -> str:
    return f"net_{slug(name)}"


def build_circuit(recipe: TemplateRecipe, parameters: dict) -> tuple[CircuitIR, RequirementSpec]:
    requirement = recipe.requirement(parameters)
    components: list[Component] = []
    references: set[str] = set()
    for spec in recipe.parts:
        if spec.reference in references:
            raise PinmapMismatch(f"Duplicate reference {spec.reference} in template {recipe.id}.",
                                 code="DUPLICATE_REFERENCE")
        references.add(spec.reference)
        part = builtin.PARTS.get(spec.part_id)
        if part is None:
            raise NotFound(f"Part {spec.part_id} for {spec.reference} is not in the catalog.",
                           code="PART_NOT_FOUND")
        if part.footprint_id not in builtin.FOOTPRINTS:
            raise NotFound(f"Footprint {part.footprint_id} is missing.", code="ASSET_UNSUPPORTED")
        if part.symbol_id not in builtin.SYMBOLS:
            raise NotFound(f"Symbol {part.symbol_id} is missing.", code="ASSET_UNSUPPORTED")
        components.append(Component(
            id=component_id(spec.reference), reference=spec.reference,
            part_id=part.part_id, symbol_id=part.symbol_id,
            footprint_id=part.footprint_id,
            pin_pad_map={k: list(v) for k, v in part.pin_pad_map.items()},
            value=spec.value or part.value_label or part.mpn,
            placement=None, module_id=spec.module or None))

    by_ref = {c.reference: c for c in components}
    pin_owner: dict[str, str] = {}
    nets: list[Net] = []
    for net_spec in recipe.nets:
        pins: list[str] = []
        for token in net_spec["pins"]:
            reference, _, pin_number = token.partition(":")
            comp = by_ref.get(reference)
            if comp is None:
                raise PinmapMismatch(f"{net_spec['name']} references unknown part {reference}.",
                                     code="PINMAP_MISMATCH")
            part = builtin.PARTS[comp.part_id]
            pin = next((p for p in part.pins if p.number == pin_number), None)
            if pin is None:
                raise PinmapMismatch(
                    f"{reference} has no pin {pin_number} (net {net_spec['name']}).",
                    code="PINMAP_MISMATCH",
                    data={"component": reference, "pin": pin_number})
            key = f"{comp.id}:{pin_number}"
            if key in pin_owner:
                raise PinmapMismatch(
                    f"Pin {reference}.{pin_number} is assigned to two nets.",
                    code="PINMAP_MISMATCH")
            pin_owner[key] = net_spec["name"]
            pins.append(key)
        nets.append(Net(id=net_id(net_spec["name"]), name=net_spec["name"], pins=pins,
                        net_class=net_spec.get("class", "signal"),
                        voltage_v=net_spec.get("voltage_v")))

    no_connects: list[str] = []
    for token in recipe.no_connects:
        reference, _, pin_number = token.partition(":")
        comp = by_ref.get(reference)
        if comp is None:
            raise PinmapMismatch(f"NC references unknown part {reference}.",
                                 code="PINMAP_MISMATCH")
        key = f"{comp.id}:{pin_number}"
        if key in pin_owner:
            raise PinmapMismatch(f"Pin {token} is both connected and NC.", code="PINMAP_MISMATCH")
        no_connects.append(key)

    # Every physical pin that has no connection must be explained (NC or flagged
    # unconnected later by the electrical checks). Template pins are complete.
    circuit = CircuitIR(
        components=components, nets=nets, no_connects=no_connects,
        modules=[{"module_id": m} for m in recipe.modules],
        rule_profile_id=recipe.validation_profile,
        library_snapshot_id="mota_builtin_v1")
    _assign_default_rotations(circuit, recipe)
    requirement.board_size = {
        "width_mm": float(parameters.get("board_width_mm", recipe.defaults.get("board_width_mm", 40))),
        "height_mm": float(parameters.get("board_height_mm", recipe.defaults.get("board_height_mm", 30))),
    }
    return circuit, requirement


def _assign_default_rotations(circuit: CircuitIR, recipe: TemplateRecipe) -> None:
    rotations = {p.reference: p.rotation for p in recipe.parts}
    for comp in circuit.components:
        rotation = int(rotations.get(comp.reference, 0))
        comp.placement = Placement(x_nm=0, y_nm=0, rotation_deg=rotation, side="top")
        comp.placement_origin = "template"


def apply_board_size(requirement: RequirementSpec, width_mm: float, height_mm: float) -> None:
    if width_mm < 20 or height_mm < 20 or width_mm > 300 or height_mm > 300:
        raise ConstraintConflict("Board size is outside the supported demo range (20..300 mm).",
                                 data={"width_mm": width_mm, "height_mm": height_mm})
    requirement.board_size = {"width_mm": float(width_mm), "height_mm": float(height_mm)}
    if requirement.parameters.get("board_width_mm"):
        requirement.parameters["board_width_mm"].value = float(width_mm)
        requirement.parameters["board_height_mm"].value = float(height_mm)
