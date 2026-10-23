"""KiCad symbol readers for the v2 library importer.

Supports packed libraries (one `.kicad_sym` with many symbols), unpacked
directories (one file per symbol library), `extends` inheritance inside a
library, multi-unit / multi-body-style symbols and string pin numbers.
Unknown tokens and unknown electrical semantics are reported with a source
location instead of being silently dropped.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field

from mota.library import sexpr
from mota.library.records import (
    AssetRecord, Diagnostic, PinRecord, make_asset_id,
)

PARSER_VERSION = "mota-kicad-symbols/1"

KNOWN_PIN_TYPES = frozenset({
    "input", "output", "bidirectional", "tri_state", "passive", "free",
    "unspecified", "power_in", "power_out", "open_collector", "open_emitter",
    "no_connect",
})
KNOWN_PIN_GRAPHICS = frozenset({
    "line", "inverted", "clock", "inverted_clock", "input_low", "clock_low",
    "output_low", "edge_clock_high", "non_logic",
})
ROOT_TOKENS = frozenset({
    "version", "generator", "generator_version", "symbol", "uuid",
    "embedded_fonts",
})
SYMBOL_TOKENS = frozenset({
    "extends", "pin_names", "pin_numbers", "exclude_from_sim", "in_bom",
    "on_board", "property", "symbol", "power", "embedded_fonts",
    "duplicate_pin_numbers_are_jumpers", "jumper_pin_groups", "uuid",
})
SUB_SYMBOL_TOKENS = frozenset({
    "pin", "rectangle", "polyline", "arc", "circle", "bezier", "text",
    "text_box", "group", "image",
})
PIN_TOKENS = frozenset({
    "at", "length", "name", "number", "effects", "alternate", "hide", "uuid",
})


@dataclass
class RawSymbol:
    name: str
    extends: str | None
    node: sexpr.Node
    locator: sexpr.TextLocator
    source_file: str
    source_sha256: str


@dataclass
class SymbolFileResult:
    library: str
    source_id: str
    source_file: str
    source_sha256: str
    raw_symbols: list[RawSymbol] = field(default_factory=list)
    failures: list[AssetRecord] = field(default_factory=list)
    diagnostics: list[Diagnostic] = field(default_factory=list)
    parser_version: str = PARSER_VERSION


@dataclass
class _SymbolData:
    properties: dict[str, str] = field(default_factory=dict)
    pins: list[PinRecord] = field(default_factory=list)
    units: int = 1
    body_styles: int = 1
    unknown_electrical: bool = False
    diagnostics: list[Diagnostic] = field(default_factory=list)


def parse_symbol_file(text: str, *, library: str, source_id: str,
                      source_file: str, source_sha256: str) -> SymbolFileResult:
    result = SymbolFileResult(library=library, source_id=source_id,
                              source_file=source_file,
                              source_sha256=source_sha256)
    try:
        root = sexpr.parse(text)
    except sexpr.SexprError as exc:
        name = source_file.rsplit("/", 1)[-1].rsplit(".", 1)[0]
        result.failures.append(_failure(
            kind="symbol", library=library, name=name, source_id=source_id,
            source_file=source_file, source_sha256=source_sha256,
            code="SYNTAX_ERROR", message=str(exc), line=exc.line,
            column=exc.column, offset=exc.offset))
        return result

    locator = sexpr.TextLocator(text)
    if sexpr.head(root) != "kicad_symbol_lib":
        line, column = locator.locate(root.offset)
        result.failures.append(_failure(
            kind="symbol", library=library,
            name=source_file.rsplit("/", 1)[-1].rsplit(".", 1)[0],
            source_id=source_id, source_file=source_file,
            source_sha256=source_sha256, code="NOT_SYMBOL_LIBRARY",
            message=f"root node is {sexpr.head(root)!r}, expected kicad_symbol_lib",
            line=line, column=column, offset=root.offset))
        return result

    result.diagnostics.extend(_unknown_tokens(
        root, ROOT_TOKENS, locator, source_file, "library root"))

    for node in sexpr.children_named(root, "symbol"):
        declared = sexpr.atom_value(node)
        if not declared:
            line, column = locator.locate(node.offset)
            result.failures.append(_failure(
                kind="symbol", library=library, name="<unnamed>",
                source_id=source_id, source_file=source_file,
                source_sha256=source_sha256, code="SYMBOL_WITHOUT_NAME",
                message="symbol node has no name", line=line, column=column,
                offset=node.offset))
            continue
        name = declared.split(":")[-1]
        extends_node = sexpr.child(node, "extends")
        extends = sexpr.atom_value(extends_node) or None
        if extends:
            extends = extends.split(":")[-1]
        result.raw_symbols.append(RawSymbol(
            name=name, extends=extends, node=node, locator=locator,
            source_file=source_file, source_sha256=source_sha256))

    if not result.raw_symbols and not result.failures:
        line, column = locator.locate(root.offset)
        result.failures.append(_failure(
            kind="symbol", library=library,
            name=source_file.rsplit("/", 1)[-1].rsplit(".", 1)[0],
            source_id=source_id, source_file=source_file,
            source_sha256=source_sha256, code="EMPTY_SYMBOL_LIBRARY",
            message="kicad_symbol_lib contains no symbol nodes",
            line=line, column=column, offset=root.offset))
    return result


def build_symbol_assets(result: SymbolFileResult) -> list[AssetRecord]:
    """Resolve inheritance and produce one record per discovered symbol."""
    records: list[AssetRecord] = list(result.failures)
    grouped: dict[str, list[RawSymbol]] = {}
    for raw in result.raw_symbols:
        grouped.setdefault(raw.name, []).append(raw)

    resolved: dict[str, _SymbolData | str] = {}
    for name, raws in grouped.items():
        first = raws[0]
        data = _resolve(name, grouped, resolved, stack=())
        if isinstance(data, str):
            line, column = first.locator.locate(first.node.offset)
            records.append(_failure(
                kind="symbol", library=result.library, name=name,
                source_id=result.source_id, source_file=first.source_file,
                source_sha256=first.source_sha256, code=data,
                message=f"{data}: {name}", line=line, column=column,
                offset=first.node.offset))
        else:
            records.append(_record_from_data(
                result, first, data))
        for index, duplicate in enumerate(raws[1:], start=1):
            line, column = duplicate.locator.locate(duplicate.node.offset)
            records.append(_failure(
                kind="symbol", library=result.library, name=name,
                source_id=result.source_id, source_file=duplicate.source_file,
                source_sha256=duplicate.source_sha256,
                code="DUPLICATE_ASSET_NAME",
                message=f"symbol {name!r} is defined more than once in this library",
                line=line, column=column, offset=duplicate.node.offset,
                duplicate_index=index))
    records.sort(key=lambda record: (record.name, record.asset_id))
    return records


def _resolve(name: str, grouped: dict[str, list[RawSymbol]],
             resolved: dict[str, _SymbolData | str],
             stack: tuple[str, ...]) -> _SymbolData | str:
    if name in resolved:
        return resolved[name]
    raws = grouped.get(name)
    if not raws:
        return "EXTENDS_BASE_MISSING"
    raw = raws[0]
    if raw.extends:
        base_name = raw.extends
        if base_name in stack:
            resolved[name] = "EXTENDS_CYCLE"
            return "EXTENDS_CYCLE"
        base = _resolve(base_name, grouped, resolved, stack + (name,))
        if isinstance(base, str):
            resolved[name] = base
            return base
    else:
        base = None

    own, diagnostics = _parse_own(raw)
    data = _merge(base, own)
    data.diagnostics = (base.diagnostics if base else []) + diagnostics
    data.unknown_electrical = bool(
        (base.unknown_electrical if base else False) or own.unknown_electrical)
    resolved[name] = data
    return data


def _merge(base: _SymbolData | None, own: _SymbolData) -> _SymbolData:
    if base is None:
        return own
    pins: dict[tuple[str, int, int], PinRecord] = {
        (pin.number, pin.unit, pin.body_style): pin for pin in base.pins}
    for pin in own.pins:
        pins[(pin.number, pin.unit, pin.body_style)] = pin
    return _SymbolData(
        properties={**base.properties, **own.properties},
        pins=list(pins.values()),
        units=max(base.units, own.units),
        body_styles=max(base.body_styles, own.body_styles),
        unknown_electrical=base.unknown_electrical or own.unknown_electrical,
        diagnostics=list(base.diagnostics),
    )


def _parse_own(raw: RawSymbol) -> tuple[_SymbolData, list[Diagnostic]]:
    node = raw.node
    locator = raw.locator
    diagnostics = _unknown_tokens(node, SYMBOL_TOKENS, locator,
                                  raw.source_file, f"symbol {raw.name}")
    data = _SymbolData()

    for prop in sexpr.children_named(node, "property"):
        values = sexpr.atoms(prop)
        if len(values) >= 2:
            data.properties[values[0].value] = values[1].value

    data.pins.extend(_pins(node, locator, raw.source_file, data, diagnostics,
                           default_unit=1, default_style=1))

    for sub in sexpr.children_named(node, "symbol"):
        sub_name = sexpr.atom_value(sub)
        unit, style = _unit_style(sub_name, raw.name)
        data.units = max(data.units, unit)
        data.body_styles = max(data.body_styles, style)
        if sub_name and sub_name.rsplit("_", 2)[0] != raw.name:
            line, column = locator.locate(sub.offset)
            diagnostics.append(Diagnostic(
                severity="warning", code="SUBSYMBOL_NAME_MISMATCH",
                message=f"sub-symbol {sub_name!r} does not start with {raw.name!r}",
                file=raw.source_file, line=line, column=column,
                asset_id=None))
        diagnostics.extend(_unknown_tokens(
            sub, SUB_SYMBOL_TOKENS, locator, raw.source_file,
            f"sub-symbol {sub_name or raw.name}"))
        data.pins.extend(_pins(sub, locator, raw.source_file, data,
                               diagnostics, default_unit=unit,
                               default_style=style))

    for pin in data.pins:
        data.units = max(data.units, pin.unit)
        data.body_styles = max(data.body_styles, pin.body_style)
    return data, diagnostics


def _pins(node: sexpr.Node, locator: sexpr.TextLocator, source_file: str,
          data: _SymbolData, diagnostics: list[Diagnostic], *,
          default_unit: int, default_style: int) -> list[PinRecord]:
    out: list[PinRecord] = []
    for pin_node in sexpr.children_named(node, "pin"):
        atoms = sexpr.atoms(pin_node)
        electrical = atoms[0].value if atoms else "passive"
        graphic = atoms[1].value if len(atoms) > 1 else "line"
        at = sexpr.child(pin_node, "at")
        coords = sexpr.atom_floats(at)
        if not coords:
            line, column = locator.locate(pin_node.offset)
            diagnostics.append(Diagnostic(
                severity="warning", code="PIN_WITHOUT_AT",
                message="pin has no (at ...) node; pin skipped",
                file=source_file, line=line, column=column))
            continue
        number = sexpr.atom_value(sexpr.child(pin_node, "number"))
        if not number:
            line, column = locator.locate(pin_node.offset)
            diagnostics.append(Diagnostic(
                severity="warning", code="PIN_WITHOUT_NUMBER",
                message="pin has no number; pin skipped",
                file=source_file, line=line, column=column))
            continue
        name = sexpr.atom_value(sexpr.child(pin_node, "name"), "~")
        length = sexpr.atom_floats(sexpr.child(pin_node, "length"))
        hidden = any(child.is_list is False and child.value == "hide"
                     for child in pin_node.children) or sexpr.child(pin_node, "hide") is not None
        if electrical not in KNOWN_PIN_TYPES:
            line, column = locator.locate(pin_node.offset)
            diagnostics.append(Diagnostic(
                severity="error", code="UNKNOWN_PIN_ELECTRICAL_TYPE",
                message=(f"pin {number} uses unknown electrical type "
                         f"{electrical!r}; electrical semantics are unknown"),
                file=source_file, line=line, column=column))
            data.unknown_electrical = True
        if graphic not in KNOWN_PIN_GRAPHICS:
            line, column = locator.locate(pin_node.offset)
            diagnostics.append(Diagnostic(
                severity="warning", code="UNKNOWN_PIN_GRAPHIC_STYLE",
                message=f"pin {number} uses unknown graphic style {graphic!r}",
                file=source_file, line=line, column=column))
        diagnostics.extend(_unknown_tokens(
            pin_node, PIN_TOKENS, locator, source_file, f"pin {number}"))
        out.append(PinRecord(
            number=number, name=name, electrical_type=electrical,
            unit=default_unit, body_style=default_style,
            x_mm=coords[0], y_mm=coords[1],
            angle_deg=coords[2] if len(coords) > 2 else 0.0,
            length_mm=length[0] if length else 0.0, hidden=hidden))
    return out


def _unit_style(sub_name: str, base: str) -> tuple[int, int]:
    parts = sub_name.rsplit("_", 2)
    if len(parts) == 3 and parts[1].isdigit() and parts[2].isdigit():
        # Unit/style 0 is the KiCad "common to all units/styles" body.
        return int(parts[1]), int(parts[2])
    return 1, 1


def _record_from_data(result: SymbolFileResult, raw: RawSymbol,
                      data: _SymbolData) -> AssetRecord:
    line, column = raw.locator.locate(raw.node.offset)
    pins = sorted(data.pins, key=lambda p: (p.unit, p.body_style, p.number,
                                            p.name, p.x_mm, p.y_mm))
    unsupported: list[str] = []
    status = "parsed"
    if not pins:
        status = "unsupported"
        unsupported.append("NO_PINS")
    if data.unknown_electrical:
        unsupported.append("UNKNOWN_PIN_ELECTRICAL_TYPE")
    description = (data.properties.get("Description")
                   or data.properties.get("ki_description") or "")
    keywords = data.properties.get("ki_keywords", "")
    datasheet = data.properties.get("Datasheet", "")
    content_hash = _content_hash({
        "library": result.library, "name": raw.name, "units": data.units,
        "body_styles": data.body_styles, "properties": data.properties,
        "pins": [pin.model_dump() for pin in pins],
    })
    asset_id = make_asset_id("symbol", result.source_id, result.library, raw.name)
    diagnostics = [diag.model_copy(update={"asset_id": asset_id})
                   for diag in data.diagnostics]
    return AssetRecord(
        asset_id=asset_id, kind="symbol", source_id=result.source_id,
        library=result.library, name=raw.name, status=status,
        mapped=status == "parsed" and not data.unknown_electrical,
        placeable=status == "parsed", content_hash=content_hash,
        source_file=raw.source_file, source_sha256=raw.source_sha256,
        offset=raw.node.offset, line=line, units=data.units,
        body_styles=data.body_styles, extends=raw.extends, pins=pins,
        properties=data.properties, description=description,
        keywords=keywords, datasheet=datasheet,
        unsupported_features=unsupported, unknown_electrical=data.unknown_electrical,
        diagnostics=diagnostics)


def _failure(*, kind: str, library: str, name: str, source_id: str,
             source_file: str, source_sha256: str, code: str, message: str,
             line: int, column: int, offset: int,
             duplicate_index: int = 0) -> AssetRecord:
    asset_id = make_asset_id(kind, source_id, library, name,  # type: ignore[arg-type]
                             duplicate_index)
    content_hash = _content_hash(
        {"failure": code, "file": source_file, "line": line, "name": name})
    return AssetRecord(
        asset_id=asset_id, kind=kind,  # type: ignore[arg-type]
        source_id=source_id, library=library, name=name, status="failed",
        content_hash=content_hash, source_file=source_file,
        source_sha256=source_sha256, offset=offset, line=line,
        unsupported_features=[code], diagnostics=[Diagnostic(
            severity="error", code=code, message=message, file=source_file,
            line=line, column=column, asset_id=asset_id)])


def _unknown_tokens(node: sexpr.Node, known: frozenset[str],
                    locator: sexpr.TextLocator, source_file: str,
                    context: str) -> list[Diagnostic]:
    out: list[Diagnostic] = []
    for child in node.children:
        if not child.is_list:
            continue
        token = sexpr.head(child)
        if token and token not in known:
            line, column = locator.locate(child.offset)
            out.append(Diagnostic(
                severity="info", code="UNKNOWN_TOKEN",
                message=f"unknown token {token!r} in {context}",
                file=source_file, line=line, column=column))
    return out


def _content_hash(payload: dict) -> str:
    text = json.dumps(payload, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False, default=float)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()
