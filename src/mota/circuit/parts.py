"""Part / symbol / footprint resolution and pin-pad mapping for free editing.

Builtin curated parts and imported KiCad assets are resolved through one
interface. Mapping is explicit: string pin numbers stay strings, one pin may
own several pads (including several geometries with the same pad number), and
a missing or wrong footprint is reported instead of guessed.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Literal

from mota.catalog import builtin
from mota.domain.models import (
    CircuitIR, Component, FootprintDef, Model3D, PadDef, PinDef, Rect,
    SymbolDef, SymbolPinDef,
)
from mota.domain.units import mm
from mota.errors import NotFound
from mota.ir.models_v2 import (
    ComponentInstanceSet, ComponentInstanceV2, PinPadMappingV2,
)
from mota.library import resolver as library_resolver
from mota.workspace import paths

IssueSeverity = Literal["error", "warning", "info"]


@dataclass(frozen=True)
class MappingIssue:
    code: str
    message: str
    component_id: str = ""
    pin: str = ""
    pad: str = ""
    severity: IssueSeverity = "error"


@dataclass(frozen=True)
class PartView:
    part_id: str
    mpn: str
    description: str
    category: str
    symbol_id: str
    footprint_id: str
    pins: tuple[PinDef, ...]
    pin_pad_map: dict[str, list[str]] = field(default_factory=dict)
    value_label: str | None = None
    reference_prefix: str = "U"
    on_board: bool = True
    units: tuple[int, ...] = (1,)
    pin_units: dict[str, int] = field(default_factory=dict)
    source_ids: tuple[str, ...] = ()


_ELECTRICAL_MAP = {
    "power_in": "power_in", "power_out": "power_out", "input": "input",
    "output": "output", "bidirectional": "bidirectional", "passive": "passive",
    "no_connect": "no_connect", "open_collector": "open_collector",
    "tri_state": "bidirectional", "free": "passive", "unspecified": "passive",
    "open_emitter": "open_collector",
}
_PAD_TYPE_MAP = {"smd": "smd", "thru_hole": "tht", "np_thru_hole": "npth",
                 "connect": "tht"}
_PAD_SHAPE_MAP = {"circle": "circle", "oval": "oval"}


def _root_key() -> str:
    return str(paths.data_root())


_RUNTIME_PARTS: dict[str, PartView] = {}
_RUNTIME_SYMBOLS: dict[str, SymbolDef] = {}
_RUNTIME_FOOTPRINTS: dict[str, FootprintDef] = {}


def register_runtime_part(view: PartView, symbol: SymbolDef,
                          footprint: FootprintDef | None = None) -> None:
    """Register a module-local part for composition and simple rendering.

    These definitions are explicit module fixtures, not vendor-verified
    package models; footprints built here are marked approximate.
    """
    _RUNTIME_PARTS[view.part_id] = view
    _RUNTIME_SYMBOLS[symbol.symbol_id] = symbol
    if footprint is not None:
        _RUNTIME_FOOTPRINTS[footprint.footprint_id] = footprint


def clear_runtime_parts() -> None:
    _RUNTIME_PARTS.clear()
    _RUNTIME_SYMBOLS.clear()
    _RUNTIME_FOOTPRINTS.clear()


def make_runtime_part(*, part_id: str, mpn: str, description: str,
                      pins: list[tuple[str, str, str]],
                      reference_prefix: str = "U") -> PartView:
    """Build a simple rectangular symbol and a single-row approximate footprint.

    `pins` entries are `(number, name, electrical_type)`; pin numbers are kept
    as strings exactly as supplied.
    """
    count = len(pins)
    spacing = mm(2.54)
    height = mm(5.08) + spacing * max(1, (count + 1) // 2)
    body_w = mm(15.24)
    symbol_pins = []
    for index, (number, name, electrical_type) in enumerate(pins):
        left = index % 2 == 0
        row = index // 2
        x = -body_w // 2 if left else body_w // 2
        y = height // 2 - mm(2.54) - row * spacing
        symbol_pins.append(SymbolPinDef(
            number=number, name=name, x_nm=x, y_nm=y,
            direction="left" if left else "right", length_nm=mm(2.54),
            electrical_type=_ELECTRICAL_MAP.get(electrical_type, "passive")))
    symbol = SymbolDef(symbol_id=f"mota:local:{part_id}", body_w_nm=body_w,
                       body_h_nm=height, pins=symbol_pins,
                       reference_prefix=reference_prefix)
    pads = []
    for index, pin in enumerate(symbol_pins):
        pads.append(PadDef(
            number=pin.number,
            x_nm=int((index - (count - 1) / 2) * mm(1.27)),
            y_nm=0, w_nm=mm(0.9), h_nm=mm(1.2), shape="rect",
            pad_type="smd", layers=["F.Cu"]))
    courtyard = Rect(x_nm=-mm(1.0) - count * mm(0.635), y_nm=-mm(1.0),
                     w_nm=count * mm(1.27) + mm(2.0), h_nm=mm(2.0))
    footprint = FootprintDef(
        footprint_id=f"mota:local:{part_id}", name=f"{mpn}_approx",
        description=f"Approximate single-row package for {mpn}; NOT vendor verified",
        pads=pads, courtyard=courtyard, courtyard_approximate=True,
        mounting="smd", model=Model3D(model_id=part_id, kind="box",
                                      height_mm=1.0, fidelity="simplified"))
    pin_map = {pin.number: [pin.number] for pin in symbol_pins}
    view = PartView(
        part_id=part_id, mpn=mpn, description=description,
        category="module_local", symbol_id=symbol.symbol_id,
        footprint_id=footprint.footprint_id,
        pins=tuple(PinDef(number=pin.number, name=pin.name,
                          electrical_type=pin.electrical_type,
                          pad_numbers=[pin.number]) for pin in symbol_pins),
        pin_pad_map=pin_map, value_label=mpn,
        reference_prefix=reference_prefix, on_board=True, units=(1,),
        pin_units={pin.number: 1 for pin in symbol_pins})
    register_runtime_part(view, symbol, footprint)
    return view


@lru_cache(maxsize=512)
def _imported_record(asset_id: str, root_key: str) -> dict:
    """Read one asset through the single component-library resolver.

    The resolver maps legacy `sym:` / `fp:` / builtin part ids onto immutable
    package versions, so this is the only lookup path the circuit, validation,
    agent and desktop share. `root_key` only keys the cache; the library root
    itself comes from `MOTA_LIBRARY_ROOT` / the data root.

    A root that still only holds the pre-package KiCad snapshot keeps working
    through the legacy reader; migrated roots never reach it.
    """
    try:
        return library_resolver.record(asset_id)
    except NotFound:
        from mota.library import inspect_asset

        return inspect_asset(asset_id)


def resolve_symbol(symbol_id: str) -> SymbolDef:
    if symbol_id in _RUNTIME_SYMBOLS:
        return _RUNTIME_SYMBOLS[symbol_id]
    try:
        record = _imported_record(symbol_id, _root_key())
    except NotFound as exc:
        if symbol_id in builtin.SYMBOLS:
            # Compatibility fallback only while a library root has no migrated
            # package yet; the package copy is the normal source of truth.
            return builtin.SYMBOLS[symbol_id]
        raise NotFound(f"Symbol {symbol_id} is not in the component library.",
                       code="SYMBOL_NOT_FOUND") from exc
    pins = []
    for pin in record.get("pins", []):
        angle = int(round(float(pin.get("angle_deg", 0)))) % 360
        direction = {0: "right", 90: "up", 180: "left", 270: "down"}.get(
            angle, "right")
        pins.append(SymbolPinDef(
            number=str(pin["number"]), name=str(pin.get("name", "~")),
            x_nm=mm(float(pin.get("x_mm", 0.0))),
            y_nm=mm(float(pin.get("y_mm", 0.0))),
            direction=direction,
            length_nm=mm(float(pin.get("length_mm", 2.54))),
            electrical_type=_ELECTRICAL_MAP.get(
                str(pin.get("electrical_type", "passive")), "passive")))
    if not pins:
        raise NotFound(f"Symbol {symbol_id} has no pins.", code="SYMBOL_NOT_FOUND")
    xs = [pin.x_nm for pin in pins]
    ys = [pin.y_nm for pin in pins]
    body_w = max(mm(5.08), max(xs) - min(xs) + mm(5.08))
    body_h = max(mm(5.08), max(ys) - min(ys) + mm(5.08))
    prefix = _reference_prefix(record.get("properties", {}))
    return SymbolDef(symbol_id=symbol_id, body_w_nm=body_w, body_h_nm=body_h,
                     pins=pins, reference_prefix=prefix)


def resolve_footprint(footprint_id: str) -> FootprintDef:
    if footprint_id in _RUNTIME_FOOTPRINTS:
        return _RUNTIME_FOOTPRINTS[footprint_id]
    try:
        record = _imported_record(footprint_id, _root_key())
    except NotFound as exc:
        if footprint_id in builtin.FOOTPRINTS:
            return builtin.FOOTPRINTS[footprint_id]
        raise NotFound(f"Footprint {footprint_id} is not in the component library.",
                       code="FOOTPRINT_NOT_FOUND") from exc
    pads: list[PadDef] = []
    # C1 does not parse courtyard graphics; the envelope is a pad-derived
    # approximation and must never be presented as a vendor-verified courtyard.
    approximate = True
    for pad in record.get("pads", []):
        rotation = float(pad.get("rotation_deg", 0)) % 360
        width = mm(float(pad.get("width_mm", 0.0)))
        height = mm(float(pad.get("height_mm", 0.0)))
        if rotation % 90 == 0:
            if int(rotation) in (90, 270):
                width, height = height, width
            approximate = approximate or rotation != 0
        else:
            angle = math.radians(rotation)
            bbox_w = abs(width * math.cos(angle)) + abs(height * math.sin(angle))
            bbox_h = abs(width * math.sin(angle)) + abs(height * math.cos(angle))
            width, height = int(bbox_w), int(bbox_h)
            approximate = True
        pad_type = str(pad.get("pad_type", "smd"))
        layers = _copper_layers(pad.get("layers", []), pad_type)
        pads.append(PadDef(
            number=str(pad.get("number", "")),
            x_nm=mm(float(pad.get("x_mm", 0.0))),
            y_nm=mm(float(pad.get("y_mm", 0.0))),
            w_nm=int(width), h_nm=int(height),
            shape=_PAD_SHAPE_MAP.get(str(pad.get("shape", "rect")), "rect"),
            drill_nm=mm(float(pad.get("drill_mm", 0.0))),
            pad_type=_PAD_TYPE_MAP.get(pad_type, "tht"),
            layers=layers))
    if pads:
        min_x = min(pad.x_nm - pad.w_nm // 2 for pad in pads) - mm(0.25)
        min_y = min(pad.y_nm - pad.h_nm // 2 for pad in pads) - mm(0.25)
        max_x = max(pad.x_nm + pad.w_nm // 2 for pad in pads) + mm(0.25)
        max_y = max(pad.y_nm + pad.h_nm // 2 for pad in pads) + mm(0.25)
        courtyard = Rect(x_nm=min_x, y_nm=min_y,
                         w_nm=max(mm(1.0), max_x - min_x),
                         h_nm=max(mm(1.0), max_y - min_y))
    else:
        courtyard = Rect(x_nm=-mm(1.0), y_nm=-mm(1.0),
                         w_nm=mm(2.0), h_nm=mm(2.0))
        approximate = True
    mounting = ("through_hole" if any(pad.pad_type in ("tht", "npth") for pad in pads)
                else "smd")
    models = record.get("models", [])
    return FootprintDef(
        footprint_id=footprint_id, name=str(record.get("name", footprint_id)),
        description=str(record.get("description", "")), pads=pads,
        courtyard=courtyard, courtyard_approximate=approximate,
        body_height_mm=1.0,
        model=Model3D(model_id=models[0] if models else footprint_id,
                      kind="box" if models else "none",
                      height_mm=1.0,
                      fidelity="simplified" if models else "missing"),
        mounting=mounting, source_ids=[str(record.get("source_id", ""))])


def _pins_from_json(pins_json: list[dict]) -> tuple[dict[str, PinDef], set[int], dict[str, int]]:
    pins: dict[str, PinDef] = {}
    units: set[int] = set()
    pin_units: dict[str, int] = {}
    for pin in pins_json:
        number = str(pin["number"])
        unit = int(pin.get("unit", 1)) or 1
        units.add(unit)
        pin_units.setdefault(number, unit)
        if number in pins:
            continue
        pins[number] = PinDef(
            number=number, name=str(pin.get("name", "~")),
            electrical_type=_ELECTRICAL_MAP.get(
                str(pin.get("electrical_type", "passive")), "passive"))
    return pins, units, pin_units


def _part_from_component(part_id: str, record: dict) -> PartView:
    """Assemble a complete part from its `bindings.json` package.

    A component package may declare its own physical terminals (with names);
    otherwise the bound symbol supplies the pin table.
    """
    bindings = record.get("bindings") or {}
    symbol_ref = (bindings.get("symbol") or {}).get("asset_id")
    footprint_ref = (bindings.get("footprint") or {}).get("asset_id")
    pins_json = record.get("pins") or []
    if pins_json:
        pins, units, pin_units = _pins_from_json(pins_json)
        reference_prefix = _reference_prefix(record.get("properties", {}))
    else:
        if not symbol_ref:
            raise NotFound(f"{part_id} has no symbol binding.", code="PART_NOT_FOUND")
        symbol_record = _imported_record(symbol_ref, _root_key())
        pins, units, pin_units = _pins_from_json(symbol_record.get("pins", []))
        reference_prefix = _reference_prefix(symbol_record.get("properties", {}))
    if not pins:
        raise NotFound(f"{part_id} has no physical terminals.", code="PART_NOT_FOUND")
    mapping = {str(key): [str(pad) for pad in value]
               for key, value in (bindings.get("pin_pad_map") or {}).items()}
    if not mapping:
        footprint = None
        try:
            footprint = resolve_footprint(footprint_ref) if footprint_ref else None
        except NotFound:
            footprint = None
        mapping = default_pin_pad_map(tuple(pins[key] for key in sorted(pins)), footprint)
    return PartView(
        part_id=part_id, mpn=str(record.get("name", part_id)),
        description=str(record.get("description", "")), category="component",
        symbol_id=symbol_ref, footprint_id=footprint_ref or "",
        pins=tuple(pins[key] for key in sorted(pins)), pin_pad_map=mapping,
        value_label=str(record.get("name", part_id)),
        reference_prefix=reference_prefix.lstrip("#") or "U",
        on_board=not reference_prefix.startswith("#"),
        units=tuple(sorted(units or {1})), pin_units=pin_units,
        source_ids=(str(record.get("source_id", "")),))


def resolve_part(part_id: str) -> PartView:
    if part_id in _RUNTIME_PARTS:
        return _RUNTIME_PARTS[part_id]
    try:
        record = _imported_record(part_id, _root_key())
    except NotFound as exc:
        if part_id in builtin.PARTS:
            part = builtin.PARTS[part_id]
            units = tuple(sorted({1}))
            return PartView(
                part_id=part.part_id, mpn=part.mpn, description=part.description,
                category=part.category, symbol_id=part.symbol_id,
                footprint_id=part.footprint_id, pins=tuple(part.pins),
                pin_pad_map={key: list(value) for key, value in part.pin_pad_map.items()},
                value_label=part.value_label,
                reference_prefix=builtin.SYMBOLS[part.symbol_id].reference_prefix,
                on_board=True, units=units,
                pin_units={pin.number: 1 for pin in part.pins},
                source_ids=tuple(part.source_ids))
        raise NotFound(f"Part {part_id} is not in the component library.",
                       code="PART_NOT_FOUND") from exc
    if record.get("kind") == "component":
        return _part_from_component(part_id, record)
    pins, units, pin_units = _pins_from_json(record.get("pins", []))
    if not pins:
        raise NotFound(f"Part {part_id} has no physical terminals.",
                       code="PART_NOT_FOUND")
    reference_prefix = _reference_prefix(record.get("properties", {}))
    on_board = not reference_prefix.startswith("#")
    return PartView(
        part_id=part_id, mpn=str(record.get("name", part_id)),
        description=str(record.get("description", "")), category="imported",
        symbol_id=part_id, footprint_id="",
        pins=tuple(pins[key] for key in sorted(pins)),
        pin_pad_map={}, value_label=str(record.get("name", part_id)),
        reference_prefix=reference_prefix.lstrip("#") or "U",
        on_board=on_board, units=tuple(sorted(units or {1})),
        pin_units=pin_units,
        source_ids=(str(record.get("source_id", "")),))


def default_pin_pad_map(pins: tuple[PinDef, ...] | list[PinDef],
                        footprint: FootprintDef | None) -> dict[str, list[str]]:
    if footprint is None:
        return {}
    mapping: dict[str, list[str]] = {}
    for pin in pins:
        matches = sorted({pad.number for pad in footprint.pads
                          if pad.number and pad.number == pin.number})
        if matches:
            mapping[pin.number] = matches
    return mapping


def normalize_mapping(pins: tuple[PinDef, ...] | list[PinDef],
                      footprint: FootprintDef,
                      explicit: dict[str, list[str]] | None,
                      *, component_id: str = "") -> tuple[dict[str, list[str]],
                                                         list[MappingIssue]]:
    pin_numbers = {pin.number for pin in pins}
    pad_numbers = {pad.number for pad in footprint.pads}
    issues: list[MappingIssue] = []
    if explicit is None:
        return default_pin_pad_map(pins, footprint), issues
    mapping: dict[str, list[str]] = {}
    for pin_number, pads in explicit.items():
        key = str(pin_number)
        values = [str(pad) for pad in pads]
        if key not in pin_numbers:
            issues.append(MappingIssue(
                code="PINMAP_KEY_UNKNOWN", component_id=component_id,
                pin=key, message=f"pin {key} does not exist on this part"))
            continue
        missing = [pad for pad in values if pad not in pad_numbers]
        for pad in missing:
            issues.append(MappingIssue(
                code="PAD_NOT_FOUND", component_id=component_id, pin=key,
                pad=pad, message=f"pad {pad} does not exist on this footprint"))
        mapping[key] = values
    return mapping, issues


def validate_component_mapping(component: Component,
                               wired_pins: set[str] | None = None
                               ) -> list[MappingIssue]:
    try:
        part = resolve_part(component.part_id)
    except NotFound:
        return [MappingIssue(
            code="PART_UNKNOWN", component_id=component.id,
            message=f"part {component.part_id} cannot be resolved")]
    footprint = None
    if component.footprint_id:
        try:
            footprint = resolve_footprint(component.footprint_id)
        except NotFound:
            return [MappingIssue(
                code="FOOTPRINT_UNKNOWN", component_id=component.id,
                message=f"footprint {component.footprint_id} cannot be resolved")]
    issues: list[MappingIssue] = []
    if not component.on_board:
        return issues
    if footprint is None:
        return [MappingIssue(
            code="FOOTPRINT_MISSING", component_id=component.id,
            message=f"{component.reference} is on the board but has no footprint")]
    pad_numbers = {pad.number for pad in footprint.pads}
    wired = wired_pins or set()
    for pin in part.pins:
        pads = component.pin_pad_map.get(pin.number, [])
        if pin.electrical_type == "no_connect":
            if pin.number in wired:
                issues.append(MappingIssue(
                    code="NC_PIN_CONNECTED", component_id=component.id,
                    pin=pin.number,
                    message=f"{component.reference} pin {pin.number} is a "
                            "part-defined no-connect but is wired"))
            continue
        if not pads:
            issues.append(MappingIssue(
                code="PIN_UNMAPPED", component_id=component.id, pin=pin.number,
                message=f"{component.reference} pin {pin.number} has no pad"))
            continue
        for pad in pads:
            if pad not in pad_numbers:
                issues.append(MappingIssue(
                    code="PAD_NOT_FOUND", component_id=component.id,
                    pin=pin.number, pad=pad,
                    message=f"{component.reference} pin {pin.number} maps to "
                            f"missing pad {pad}"))
    for pin_number in component.pin_pad_map:
        if pin_number not in {pin.number for pin in part.pins}:
            issues.append(MappingIssue(
                code="PINMAP_KEY_UNKNOWN", component_id=component.id,
                pin=pin_number,
                message=f"{component.reference} maps unknown pin {pin_number}"))
    return issues


def mapping_issues_for_circuit(circuit: CircuitIR) -> list[MappingIssue]:
    wired: dict[str, set[str]] = {}
    for net in circuit.nets:
        for token in net.pins:
            cid, _, pin = token.partition(":")
            wired.setdefault(cid, set()).add(pin)
    issues: list[MappingIssue] = []
    for component in circuit.components:
        issues.extend(validate_component_mapping(
            component, wired.get(component.id, set())))
    return issues


def mapping_report(component: Component) -> list[PinPadMappingV2]:
    try:
        part = resolve_part(component.part_id)
    except NotFound:
        return [PinPadMappingV2(
            component_id=component.id, pin="", status="part_unknown",
            message=f"part {component.part_id} cannot be resolved")]
    footprint = None
    if component.footprint_id:
        try:
            footprint = resolve_footprint(component.footprint_id)
        except NotFound:
            return [PinPadMappingV2(
                component_id=component.id, pin="", status="footprint_unknown",
                message=f"footprint {component.footprint_id} cannot be resolved")]
    rows: list[PinPadMappingV2] = []
    if not component.on_board:
        return [PinPadMappingV2(
            component_id=component.id, pin=pin.number,
            pad_numbers=list(component.pin_pad_map.get(pin.number, [])),
            status="ok", message="not on board; footprint not required")
            for pin in part.pins]
    pad_numbers = {pad.number for pad in footprint.pads} if footprint else set()
    for pin in part.pins:
        pads = list(component.pin_pad_map.get(pin.number, []))
        if footprint is None:
            status, message = "missing_footprint", "component has no footprint"
        elif not pads:
            status, message = "unmapped", "pin has no pads"
        elif any(pad not in pad_numbers for pad in pads):
            status, message = "pad_not_found", "mapped pad is missing"
        else:
            status, message = "ok", ""
        rows.append(PinPadMappingV2(component_id=component.id, pin=pin.number,
                                    pad_numbers=pads, status=status,
                                    message=message))
    return rows


def no_connect_kinds(circuit: CircuitIR, component: Component) -> dict[str, str]:
    """Distinguish ordinary, explicit and part-defined no-connects."""
    wired = {token.partition(":")[2] for net in circuit.nets
             for token in net.pins if token.startswith(component.id + ":")}
    explicit = {token.partition(":")[2] for token in circuit.no_connects
                if token.startswith(component.id + ":")}
    part = resolve_part(component.part_id)
    out: dict[str, str] = {}
    for pin in part.pins:
        if pin.electrical_type == "no_connect":
            out[pin.number] = "part_defined"
        elif pin.number in explicit:
            out[pin.number] = "explicit"
        elif pin.number in wired:
            out[pin.number] = "connected"
        else:
            out[pin.number] = "ordinary"
    return out


def component_instances(circuit: CircuitIR) -> ComponentInstanceSet:
    instances: list[ComponentInstanceV2] = []
    mappings: list[PinPadMappingV2] = []
    for component in circuit.components:
        try:
            part = resolve_part(component.part_id)
            units = list(part.units)
        except NotFound:
            units = [1]
        instances.append(ComponentInstanceV2.from_component(
            component, symbol_units=units))
        mappings.extend(mapping_report(component))
    return ComponentInstanceSet(instances=instances, mappings=mappings)


def next_reference(circuit: CircuitIR, prefix: str) -> str:
    used = {component.reference for component in circuit.components}
    index = 1
    while f"{prefix}{index}" in used:
        index += 1
    return f"{prefix}{index}"


def _reference_prefix(properties: dict) -> str:
    reference = str(properties.get("Reference", "U") or "U")
    letters = "".join(ch for ch in reference if ch.isalpha() or ch == "#")
    return letters or "U"


def _copper_layers(layers: list, pad_type: str) -> list[str]:
    out: list[str] = []
    for layer in layers:
        if layer == "*.Cu":
            out.extend(["F.Cu", "B.Cu"])
        elif layer in ("F.Cu", "B.Cu"):
            out.append(layer)
    if not out:
        out = ["F.Cu", "B.Cu"] if pad_type in ("thru_hole", "np_thru_hole") else ["F.Cu"]
    return sorted(set(out))
