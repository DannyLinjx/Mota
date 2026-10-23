"""Minimal but real KiCad S-expression readers for symbols, footprints and
symbol libraries. Used by catalog sync and by round-trip checks; Mota's own
writer is independent.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import sexpdata


class ParseError(ValueError):
    pass


def _head(node: Any) -> str:
    if isinstance(node, list) and node and isinstance(node[0], sexpdata.Symbol):
        return str(node[0])
    return ""


def _children(node: Any, name: str) -> list[list[Any]]:
    if not isinstance(node, list):
        return []
    return [c for c in node if isinstance(c, list) and _head(c) == name]


def _child(node: Any, name: str) -> list[Any] | None:
    rows = _children(node, name)
    return rows[0] if rows else None


def _values(node: list[Any]) -> list[Any]:
    return [v for v in node[1:] if not isinstance(v, list)]


def _symbol_text(value: Any) -> str:
    if isinstance(value, sexpdata.Symbol):
        return str(value)
    if isinstance(value, str):
        return value
    return str(value)


@dataclass
class ParsedPin:
    number: str
    name: str
    electrical_type: str
    x: float
    y: float
    angle: float
    length: float


@dataclass
class ParsedSymbol:
    lib_id: str
    name: str
    pins: list[ParsedPin] = field(default_factory=list)
    properties: dict[str, str] = field(default_factory=dict)
    units: int = 1
    diagnostics: list[str] = field(default_factory=list)


@dataclass
class ParsedPad:
    number: str
    pad_type: str
    shape: str
    x: float
    y: float
    width: float
    height: float
    drill: float
    layers: list[str]


@dataclass
class ParsedFootprint:
    lib_id: str
    name: str
    pads: list[ParsedPad] = field(default_factory=list)
    diagnostics: list[str] = field(default_factory=list)


def parse_symbol_library(path: Path, library_name: str | None = None) -> list[ParsedSymbol]:
    text = path.read_text(encoding="utf-8", errors="replace")
    try:
        tree = sexpdata.loads(text)
    except Exception as exc:  # sexpdata raises several types
        raise ParseError(f"{path.name}: {exc}") from exc
    if _head(tree) != "kicad_symbol_lib":
        raise ParseError(f"{path.name}: not a kicad_symbol_lib")
    lib = library_name or path.stem
    out: list[ParsedSymbol] = []
    for node in _children(tree, "symbol"):
        values = _values(node)
        if not values:
            continue
        raw_name = _symbol_text(values[0])
        lib_id = raw_name if ":" in raw_name else f"{lib}:{raw_name}"
        sym = ParsedSymbol(lib_id=lib_id, name=raw_name.split(":")[-1])
        for prop in _children(node, "property"):
            pv = _values(prop)
            if len(pv) >= 2:
                sym.properties[_symbol_text(pv[0])] = _symbol_text(pv[1])
        unit_names = set()
        for sub in _children(node, "symbol"):
            sub_values = _values(sub)
            if sub_values:
                sub_name = _symbol_text(sub_values[0])
                if "_" in sub_name:
                    unit_names.add(sub_name.rsplit("_", 1)[0])
            for pin in _children(sub, "pin"):
                parsed = _parse_pin(pin)
                if parsed:
                    sym.pins.append(parsed)
        # Direct pins (rare, but valid)
        for pin in _children(node, "pin"):
            parsed = _parse_pin(pin)
            if parsed:
                sym.pins.append(parsed)
        sym.units = max(1, len(unit_names))
        if sym.units > 1:
            sym.diagnostics.append("MULTI_UNIT_SYMBOL: unit mapping requires explicit review")
        if not sym.pins:
            sym.diagnostics.append("NO_PINS_PARSED")
        out.append(sym)
    return out


def _parse_pin(node: list[Any]) -> ParsedPin | None:
    values = _values(node)
    # (pin passive line (at x y a) (length l) (name "x" (effects ...)) (number "1" ...))
    electrical = _symbol_text(values[0]) if values else "passive"
    at = _child(node, "at")
    length = _child(node, "length")
    name_node = _child(node, "name")
    number_node = _child(node, "number")
    coords = [float(_symbol_text(v)) for v in _values(at)[:3]] if at else []
    if not coords:
        return None
    name = _symbol_text(_values(name_node)[0]) if name_node and _values(name_node) else "~"
    number = _symbol_text(_values(number_node)[0]) if number_node and _values(number_node) else ""
    if not number:
        return None
    return ParsedPin(number=number, name=name, electrical_type=electrical,
                     x=coords[0], y=coords[1], angle=coords[2] if len(coords) > 2 else 0.0,
                     length=float(_symbol_text(_values(length)[0])) if length and _values(length) else 0.0)


def parse_footprint(path: Path, library_name: str | None = None) -> ParsedFootprint:
    text = path.read_text(encoding="utf-8", errors="replace")
    try:
        tree = sexpdata.loads(text)
    except Exception as exc:
        raise ParseError(f"{path.name}: {exc}") from exc
    if _head(tree) not in ("footprint", "module"):
        raise ParseError(f"{path.name}: not a footprint")
    values = _values(tree)
    raw_name = _symbol_text(values[0]) if values else path.stem
    lib = library_name or path.parent.name.replace(".pretty", "")
    fp = ParsedFootprint(lib_id=f"{lib}:{raw_name}", name=raw_name)
    for pad in _children(tree, "pad"):
        parsed = _parse_pad(pad)
        if parsed:
            fp.pads.append(parsed)
    if not fp.pads:
        fp.diagnostics.append("NO_PADS_PARSED")
    numbers = {p.number for p in fp.pads}
    supported_shapes = {"circle", "rect", "oval", "trapezoid"}
    for pad in fp.pads:
        if pad.pad_type not in ("thru_hole", "smd", "np_thru_hole", "connect"):
            fp.diagnostics.append(f"UNSUPPORTED_PAD_TYPE:{pad.number}:{pad.pad_type}")
        if pad.shape not in supported_shapes:
            fp.diagnostics.append(f"UNSUPPORTED_PAD_SHAPE:{pad.number}:{pad.shape}")
        if pad.number == "" and pad.pad_type != "np_thru_hole":
            fp.diagnostics.append("UNNUMBERED_NON_NPTH_PAD")
    if len(numbers) != len(fp.pads):
        fp.diagnostics.append("DUPLICATE_PAD_NUMBERS")
    return fp


def _parse_pad(node: list[Any]) -> ParsedPad | None:
    values = _values(node)
    if len(values) < 2:
        return None
    number = _symbol_text(values[0])
    pad_type = _symbol_text(values[1])
    shape = _symbol_text(values[2]) if len(values) > 2 else "rect"
    at = _child(node, "at")
    size = _child(node, "size")
    drill = _child(node, "drill")
    layers = _child(node, "layers")
    if not at or not size:
        return None
    at_v = [float(_symbol_text(v)) for v in _values(at)[:3]]
    size_v = [float(_symbol_text(v)) for v in _values(size)[:2]]
    drill_mm = 0.0
    if drill:
        dv = [v for v in _values(drill) if not isinstance(v, str) or v.replace(".", "", 1).isdigit()]
        nums = [float(_symbol_text(v)) for v in dv[:2] if _symbol_text(v).replace(".", "", 1).isdigit()]
        drill_mm = max(nums) if nums else 0.0
    layer_values = [_symbol_text(v) for v in _values(layers)] if layers else []
    return ParsedPad(number=number, pad_type=pad_type, shape=shape,
                     x=at_v[0], y=at_v[1], width=size_v[0], height=size_v[1],
                     drill=drill_mm, layers=layer_values)
