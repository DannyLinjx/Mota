"""KiCad footprint readers for the v2 library importer.

Reads `.kicad_mod` files (`footprint` and legacy `module` roots), pads with
drill / rotation / layer data, text descriptions, attributes and 3D model
references. Unsupported pad shapes and types are flagged per pad instead of
aborting the footprint.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field

from mota.library import sexpr
from mota.library.records import (
    AssetRecord, Diagnostic, PadRecord, make_asset_id,
)

PARSER_VERSION = "mota-kicad-footprints/2"

KNOWN_PAD_TYPES = frozenset({"smd", "thru_hole", "np_thru_hole", "connect"})
KNOWN_PAD_SHAPES = frozenset({
    "circle", "rect", "oval", "trapezoid", "roundrect", "custom",
})
FOOTPRINT_TOKENS = frozenset({
    "version", "generator", "generator_version", "layer", "descr", "tags",
    "property", "attr", "fp_text", "fp_text_box", "fp_line", "fp_rect",
    "fp_circle", "fp_arc", "fp_poly", "fp_curve", "pad", "model", "group",
    "zone", "net_tie_pad_groups", "private_layers", "embedded_fonts", "uuid",
    "path", "sheetname", "sheetfile",
})
PAD_TOKENS = frozenset({
    "at", "size", "drill", "layers", "roundrect_rratio", "chamfer_ratio",
    "chamfer", "rect_delta", "thermal_bridge_angle", "thermal_bridge_width",
    "thermal_gap", "zone_connect", "net", "pinfunction", "pintype",
    "property", "primitives", "options", "remove_unused_layers",
    "keep_end_layers", "teardrop", "uuid",
})


@dataclass
class FootprintFileResult:
    library: str
    source_id: str
    source_file: str
    source_sha256: str
    records: list[AssetRecord] = field(default_factory=list)
    diagnostics: list[Diagnostic] = field(default_factory=list)
    parser_version: str = PARSER_VERSION


def parse_footprint_file(text: str, *, library: str, source_id: str,
                         source_file: str,
                         source_sha256: str) -> FootprintFileResult:
    result = FootprintFileResult(library=library, source_id=source_id,
                                 source_file=source_file,
                                 source_sha256=source_sha256)
    stem = source_file.rsplit("/", 1)[-1].rsplit(".", 1)[0]
    try:
        root = sexpr.parse(text)
    except sexpr.SexprError as exc:
        result.records.append(_failure(
            source_id=source_id, library=library, name=stem,
            source_file=source_file, source_sha256=source_sha256,
            code="SYNTAX_ERROR", message=str(exc), line=exc.line,
            column=exc.column, offset=exc.offset))
        return result

    locator = sexpr.TextLocator(text)
    root_token = sexpr.head(root)
    if root_token not in ("footprint", "module"):
        line, column = locator.locate(root.offset)
        result.records.append(_failure(
            source_id=source_id, library=library, name=stem,
            source_file=source_file, source_sha256=source_sha256,
            code="NOT_FOOTPRINT", message=(
                f"root node is {root_token!r}, expected footprint or module"),
            line=line, column=column, offset=root.offset))
        return result

    name = sexpr.atom_value(root, stem).split(":")[-1] or stem
    result.diagnostics.extend(_unknown_tokens(
        root, FOOTPRINT_TOKENS, locator, source_file, f"footprint {name}"))

    pads: list[PadRecord] = []
    diagnostics: list[Diagnostic] = list(result.diagnostics)
    for pad_node in sexpr.children_named(root, "pad"):
        pad = _parse_pad(pad_node, locator, source_file, diagnostics)
        if pad is not None:
            pads.append(pad)

    numbers = [pad.number for pad in pads if pad.number]
    if len(numbers) != len(set(numbers)):
        line, column = locator.locate(root.offset)
        diagnostics.append(Diagnostic(
            severity="info", code="DUPLICATE_PAD_NUMBERS",
            message="several pads share the same number (stacked pads)",
            file=source_file, line=line, column=column))

    properties: dict[str, str] = {}
    for prop in sexpr.children_named(root, "property"):
        values = sexpr.atoms(prop)
        if len(values) >= 2:
            properties[values[0].value] = values[1].value

    description = (sexpr.atom_value(sexpr.child(root, "descr"))
                   or properties.get("Description", ""))
    keywords = sexpr.atom_value(sexpr.child(root, "tags"))
    attributes = [atom.value for atom in sexpr.atoms(sexpr.child(root, "attr"))]
    models: list[str] = []
    for model in sexpr.children_named(root, "model"):
        path = sexpr.atom_value(model)
        if path:
            models.append(path)

    unsupported: list[str] = []
    status = "parsed"
    if not pads:
        status = "unsupported"
        unsupported.append("NO_PADS")
    if any(pad.unsupported_type for pad in pads):
        unsupported.append("UNSUPPORTED_PAD_TYPE")
    if any(pad.unsupported_shape for pad in pads):
        unsupported.append("UNSUPPORTED_PAD_SHAPE")
    mapped = (status == "parsed"
              and any(pad.number for pad in pads)
              and all(not pad.unsupported_type and not pad.unsupported_shape
                      for pad in pads if pad.number))

    line, column = locator.locate(root.offset)
    asset_id = make_asset_id("footprint", source_id, library, name)
    content_hash = _content_hash({
        "library": library, "name": name, "attributes": attributes,
        "pads": [pad.model_dump() for pad in pads],
        "models": models,
    })
    diagnostics = [diag.model_copy(update={"asset_id": asset_id})
                   for diag in diagnostics]
    result.records.append(AssetRecord(
        asset_id=asset_id, kind="footprint", source_id=source_id,
        library=library, name=name, status=status, mapped=mapped,
        placeable=status == "parsed", content_hash=content_hash,
        source_file=source_file, source_sha256=source_sha256,
        offset=root.offset, line=line, pads=pads, properties=properties,
        description=description, keywords=keywords, models=models,
        attributes=attributes, unsupported_features=unsupported,
        diagnostics=diagnostics))
    return result


def _parse_pad(node: sexpr.Node, locator: sexpr.TextLocator, source_file: str,
               diagnostics: list[Diagnostic]) -> PadRecord | None:
    values = sexpr.atoms(node)
    if len(values) < 2:
        line, column = locator.locate(node.offset)
        diagnostics.append(Diagnostic(
            severity="warning", code="MALFORMED_PAD",
            message="pad node has no number/type", file=source_file,
            line=line, column=column))
        return None
    number = values[0].value
    pad_type = values[1].value
    shape = values[2].value if len(values) > 2 else "rect"
    at = sexpr.atom_floats(sexpr.child(node, "at"))
    size = sexpr.atom_floats(sexpr.child(node, "size"))
    if not at or not size:
        line, column = locator.locate(node.offset)
        diagnostics.append(Diagnostic(
            severity="warning", code="PAD_WITHOUT_GEOMETRY",
            message=f"pad {number or '<unnumbered>'} has no (at ...) or (size ...)",
            file=source_file, line=line, column=column))
        return None
    drill = _drill(sexpr.child(node, "drill"))
    layers = [atom.value for atom in sexpr.atoms(sexpr.child(node, "layers"))]
    unsupported_type = pad_type not in KNOWN_PAD_TYPES
    unsupported_shape = shape not in KNOWN_PAD_SHAPES
    if unsupported_type:
        line, column = locator.locate(node.offset)
        diagnostics.append(Diagnostic(
            severity="error", code="UNSUPPORTED_PAD_TYPE",
            message=f"pad {number or '<unnumbered>'} has unsupported type {pad_type!r}",
            file=source_file, line=line, column=column))
    if unsupported_shape:
        line, column = locator.locate(node.offset)
        diagnostics.append(Diagnostic(
            severity="error", code="UNSUPPORTED_PAD_SHAPE",
            message=f"pad {number or '<unnumbered>'} has unsupported shape {shape!r}",
            file=source_file, line=line, column=column))
    if not number and pad_type != "np_thru_hole":
        line, column = locator.locate(node.offset)
        diagnostics.append(Diagnostic(
            severity="warning", code="UNNUMBERED_NON_NPTH_PAD",
            message=f"pad of type {pad_type!r} has no number",
            file=source_file, line=line, column=column))
    diagnostics.extend(_unknown_tokens(
        node, PAD_TOKENS, locator, source_file,
        f"pad {number or '<unnumbered>'}"))
    return PadRecord(
        number=number, pad_type=pad_type, shape=shape,
        x_mm=at[0], y_mm=at[1],
        width_mm=size[0], height_mm=size[1],
        rotation_deg=at[2] if len(at) > 2 else 0.0,
        drill_mm=drill[0], drill_height_mm=drill[1], layers=layers,
        unsupported_type=unsupported_type, unsupported_shape=unsupported_shape)


def _drill(node: sexpr.Node | None) -> tuple[float, float]:
    if node is None:
        return 0.0, 0.0
    numbers = sexpr.atom_floats(node)
    if not numbers:
        return 0.0, 0.0
    if len(numbers) >= 2 and any(
            child.is_list is False and child.value == "oval"
            for child in node.children):
        return numbers[0], numbers[1]
    return numbers[0], 0.0


def _failure(*, source_id: str, library: str, name: str, source_file: str,
             source_sha256: str, code: str, message: str, line: int,
             column: int, offset: int) -> AssetRecord:
    asset_id = make_asset_id("footprint", source_id, library, name)
    return AssetRecord(
        asset_id=asset_id, kind="footprint", source_id=source_id,
        library=library, name=name, status="failed",
        content_hash=_content_hash({"failure": code, "file": source_file,
                                    "line": line, "name": name}),
        source_file=source_file, source_sha256=source_sha256,
        offset=offset, line=line, unsupported_features=[code],
        diagnostics=[Diagnostic(
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
