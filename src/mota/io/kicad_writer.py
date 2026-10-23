"""KiCad project writer used by the C12 round-trip path.

The writer emits KiCad 6 S-expression shapes from :class:`ImportedProject`.
Values are written back in KiCad's own coordinate system and grid, so a
re-export of an imported project preserves numbers exactly.

It does not impersonate ``pcbnew``/``eeschema``: the generator token is
``mota``. Library symbol definitions are not synthesised; a schematic written
here re-imports structurally but needs the original libraries to open in KiCad.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from mota.io.kicad_project import (
    ImportedGraphic, ImportedPad, ImportedProject, ImportedSchematic,
    ImportedSheet, ImportedSymbol, NM_PER_MM,
)

PCB_VERSION = "20211014"
SCHEMATIC_VERSION = "20211123"


@dataclass
class ExportResult:
    files: list[Path] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def _mm(nm: int) -> str:
    value = nm / NM_PER_MM
    text = f"{value:.6f}".rstrip("0").rstrip(".")
    if text in {"", "-0"}:
        return "0"
    return text


def _deg(value: float) -> str:
    text = f"{value:.6f}".rstrip("0").rstrip(".")
    return text if text not in {"", "-0"} else "0"


def _esc(text: str) -> str:
    return text.replace("\\", "\\\\").replace('"', '\\"')


def _u(key: str) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_URL, "mota-c12:" + key))


def _xy(point: tuple[int, int]) -> str:
    return f"(xy {_mm(point[0])} {_mm(point[1])})"


def _at(x_nm: int, y_nm: int, rotation: float = 0.0) -> str:
    return f"(at {_mm(x_nm)} {_mm(y_nm)} {_deg(rotation)})" if rotation else \
        f"(at {_mm(x_nm)} {_mm(y_nm)})"


def write_project(project: ImportedProject, out_dir: str | Path, *,
                  name: str | None = None) -> ExportResult:
    directory = Path(out_dir)
    directory.mkdir(parents=True, exist_ok=True)
    stem = name or Path(project.pcb_file).stem
    result = ExportResult()

    pcb_path = directory / f"{stem}.kicad_pcb"
    pcb_path.write_text(_pcb(project), encoding="utf-8")
    result.files.append(pcb_path)

    for schematic in project.schematics:
        path = directory / schematic.file
        path.write_text(_schematic(schematic), encoding="utf-8")
        result.files.append(path)
    if project.schematics:
        result.warnings.append(
            "lib_symbols definitions are not synthesised; exported schematics "
            "re-import structurally but need the original libraries in KiCad")

    project_path = directory / f"{stem}.kicad_pro"
    project_path.write_text(json.dumps(_project_json(project, stem), indent=2,
                                       sort_keys=True) + "\n", encoding="utf-8")
    result.files.append(project_path)
    result.warnings.append(
        "project sections outside meta/board/net_settings/sheets/libraries are "
        "not exported")
    if any(zone.filled_polygons for zone in project.zones):
        result.warnings.append(
            "zone fill copper is not re-emitted; only zone outlines and nets")
    if any(pad.shape == "custom" for footprint in project.footprints
           for pad in footprint.pads):
        result.warnings.append(
            "custom pad primitives are not re-emitted; pad size/position is kept")
    return result


def _pcb(project: ImportedProject) -> str:
    net_numbers: dict[str, int] = {}
    for net in project.nets:
        if net.name and net.name not in net_numbers:
            net_numbers[net.name] = net.number
    lines: list[str] = [
        f"(kicad_pcb (version {project.pcb_version or PCB_VERSION}) "
        f"(generator mota)",
        "  (general",
        f"    (thickness {_mm(project.thickness_nm)})",
        "  )",
        '  (paper "A4")',
        "  (layers",
    ]
    for layer in project.layers:
        user = f' "{_esc(layer.user_name)}"' if layer.user_name else ""
        lines.append(f'    ({layer.number} "{_esc(layer.name)}" {layer.kind}{user})')
    lines += ["  )", "  (setup (pad_to_mask_clearance 0))"]

    net_table = {net.number: net.name for net in project.nets}
    for number, name in sorted(net_table.items()):
        lines.append(f'  (net {number} "{_esc(name)}")')

    for footprint in project.footprints:
        lines.append(f'  (footprint "{_esc(footprint.footprint_id)}" '
                     f'(layer "{footprint.layer}")')
        lines.append(f"    {_at(footprint.x_nm, footprint.y_nm, footprint.rotation_deg)}")
        lines.append(f'    (uuid "{_u("fp:" + footprint.reference)}")')
        lines.append(f'    (property "Reference" "{_esc(footprint.reference)}" '
                     f"(at 0 0 0) (effects (font (size 1 1))))")
        lines.append(f'    (property "Value" "{_esc(footprint.value)}" '
                     f"(at 0 0 0) (effects (font (size 1 1))))")
        if footprint.sheetfile:
            lines.append(f'    (property "Sheetfile" '
                         f'"{_esc(footprint.sheetfile)}" (at 0 0 0) '
                         f"(effects (font (size 1 1)) hide))")
        if footprint.attributes:
            lines.append(f"    (attr {' '.join(footprint.attributes)})")
        for pad in footprint.pads:
            lines.append(_pad(pad, net_numbers))
        lines.append("  )")

    for track in project.tracks:
        number = net_numbers.get(track.net, 0)
        lines.append(
            f'  (segment (start {_mm(track.start_nm[0])} '
            f'{_mm(track.start_nm[1])}) (end {_mm(track.end_nm[0])} '
            f'{_mm(track.end_nm[1])}) (width {_mm(track.width_nm)}) '
            f'(layer "{_esc(track.layer)}") (net {number}) '
            f'(tstamp {_u("seg:" + track.net + track.layer)}))')

    for via in project.vias:
        number = net_numbers.get(via.net, 0)
        layers = " ".join(f'"{_esc(name)}"' for name in via.layers)
        lines.append(
            f'  (via (at {_mm(via.x_nm)} {_mm(via.y_nm)}) '
            f'(size {_mm(via.size_nm)}) (drill {_mm(via.drill_nm)}) '
            f"(layers {layers}) (net {number}) "
            f'(tstamp {_u("via:" + via.net + str(via.x_nm) + str(via.y_nm))}))')

    for zone in project.zones:
        lines.append(
            f'  (zone (net {net_numbers.get(zone.net, 0)}) '
            f'(net_name "{_esc(zone.net)}")')
        if len(zone.layers) == 1:
            lines.append(f'    (layer "{_esc(zone.layers[0])}")')
        else:
            joined = " ".join(f'"{_esc(name)}"' for name in zone.layers)
            lines.append(f"    (layers {joined})")
        lines.append(f"    (hatch edge 0.508)")
        if zone.priority:
            lines.append(f"    (priority {zone.priority})")
        if zone.keepout:
            lines.append("    (keepout (tracks not_allowed) (vias not_allowed) "
                         "(pads not_allowed) (copperpour not_allowed))")
            lines.append("    (polygon (pts "
                         + " ".join(_xy(point) for point in zone.outline)
                         + "))")
        else:
            lines.append("    (connect_pads (clearance 0.2))")
            lines.append("    (min_thickness 0.254)")
            lines.append("    (fill yes (thermal_gap 0.508) "
                         "(thermal_bridge_width 0.508))")
            lines.append("    (polygon (pts "
                         + " ".join(_xy(point) for point in zone.outline)
                         + "))")
        lines.append("  )")

    for graphic in project.graphics:
        lines.append(_graphic(graphic))

    lines.append(")")
    return "\n".join(lines) + "\n"


def _pad(pad: ImportedPad, net_numbers: dict[str, int]) -> str:
    net_number = net_numbers.get(pad.net, 0)
    net_text = ""
    if pad.net:
        net_text = f' (net {net_number} "{_esc(pad.net)}")'
    drill = f" (drill {_mm(pad.drill_nm)})" if pad.drill_nm else ""
    if pad.drill_oval and pad.drill_nm:
        drill = f" (drill oval {_mm(pad.drill_nm)})"
    locked = " locked" if pad.locked else ""
    layers = " ".join(f'"{_esc(name)}"' for name in pad.layers)
    return (f'    (pad "{_esc(pad.number)}" {pad.kind} {pad.shape}{locked} '
            f"{_at(pad.x_nm, pad.y_nm, pad.rotation_deg)} "
            f"(size {_mm(pad.w_nm)} {_mm(pad.h_nm)}){drill} "
            f"(layers {layers}){net_text})")


def _graphic(graphic: ImportedGraphic) -> str:
    layer = f'"{_esc(graphic.layer)}"'
    width = _mm(graphic.width_nm)
    stamp = f'(tstamp {_u("graphic:" + graphic.kind + graphic.layer)})'
    if graphic.kind == "line" and len(graphic.params) >= 2:
        (start, end) = graphic.params[0], graphic.params[1]
        return (f'  (gr_line (start {_mm(start[0])} {_mm(start[1])}) '
                f'(end {_mm(end[0])} {_mm(end[1])}) (layer {layer}) '
                f"(width {width}) {stamp})")
    if graphic.kind == "arc" and len(graphic.params) >= 3:
        (start, mid, end) = graphic.params[0], graphic.params[1], graphic.params[2]
        return (f'  (gr_arc (start {_mm(start[0])} {_mm(start[1])}) '
                f'(mid {_mm(mid[0])} {_mm(mid[1])}) '
                f'(end {_mm(end[0])} {_mm(end[1])}) (layer {layer}) '
                f"(width {width}) {stamp})")
    if graphic.kind == "circle" and len(graphic.params) >= 2:
        (center, end) = graphic.params[0], graphic.params[1]
        return (f'  (gr_circle (center {_mm(center[0])} {_mm(center[1])}) '
                f'(end {_mm(end[0])} {_mm(end[1])}) (layer {layer}) '
                f"(width {width}) (fill none) {stamp})")
    if graphic.kind == "rect" and len(graphic.params) >= 2:
        (start, end) = graphic.params[0], graphic.params[1]
        return (f'  (gr_rect (start {_mm(start[0])} {_mm(start[1])}) '
                f'(end {_mm(end[0])} {_mm(end[1])}) (layer {layer}) '
                f"(width {width}) (fill none) {stamp})")
    points = " ".join(_xy(point) for point in graphic.points)
    return (f"  (gr_poly (pts {points}) (layer {layer}) (width {width}) "
            f"(fill none) {stamp})")


def _schematic(schematic: ImportedSchematic) -> str:
    lines: list[str] = [
        f"(kicad_sch (version {schematic.version or SCHEMATIC_VERSION}) "
        f"(generator mota)",
        f'  (uuid "{_u("sch:" + schematic.file)}")',
        '  (paper "A4")',
        "  (lib_symbols)",
    ]
    for junction in schematic.junctions:
        lines.append(f'  (junction (at {_mm(junction[0])} {_mm(junction[1])}) '
                     f"(diameter 0) (color 0 0 0 0) "
                     f'(uuid "{_u("junction:" + schematic.file + str(junction))}"))')
    for point in schematic.no_connects:
        lines.append(f'  (no_connect (at {_mm(point[0])} {_mm(point[1])}) '
                     f'(uuid "{_u("nc:" + schematic.file + str(point))}"))')
    for index, wire in enumerate(schematic.wires):
        points = " ".join(_xy(point) for point in wire)
        lines.append(f'  (wire (pts {points}) (stroke (width 0.1524) '
                     f'(type default)) (uuid "{_u(f"wire:{schematic.file}:{index}")}"))')
    for label in schematic.labels:
        text, x_nm, y_nm, rotation = label
        lines.append(f'  (label "{_esc(text)}" {_at(x_nm, y_nm, rotation)} '
                     f"(effects (font (size 1.27 1.27)) (justify left bottom)) "
                     f'(uuid "{_u("label:" + schematic.file + str(label))}"))')
    for symbol in schematic.symbols:
        lines.extend(_symbol(symbol, schematic.file))
    for sheet in schematic.sheets:
        lines.extend(_sheet(sheet, schematic.file))
    lines.append('  (sheet_instances (path "/" (page "1")))')
    lines.append(")")
    return "\n".join(lines) + "\n"


def _symbol(symbol: ImportedSymbol, file: str) -> list[str]:
    lines = [f'  (symbol (lib_id "{_esc(symbol.lib_id)}") '
             f"{_at(symbol.x_nm, symbol.y_nm, symbol.rotation_deg)} "
             f"(unit {symbol.unit})"]
    lines.append(f"    (in_bom yes) (on_board yes) (dnp no)")
    if symbol.mirror:
        lines.append(f"    (mirror {symbol.mirror})")
    lines.append(f'    (uuid "{_u("symbol:" + file + symbol.reference)}")')
    for name, value in (("Reference", symbol.reference), ("Value", symbol.value),
                        ("Footprint", symbol.footprint)):
        if not value and name != "Reference":
            continue
        lines.append(f'    (property "{name}" "{_esc(value)}" (at 0 0 0) '
                     f"(effects (font (size 1.27 1.27))"
                     f"{' hide' if name == 'Footprint' else ''}))")
    for pin in symbol.pins:
        lines.append(f'    (pin "{_esc(pin)}" (uuid '
                     f'"{_u("pin:" + file + symbol.reference + pin)}"))')
    lines.append("  )")
    return lines


def _sheet(sheet: ImportedSheet, file: str) -> list[str]:
    return [
        f"  (sheet {_at(sheet.x_nm, sheet.y_nm)} "
        f"(size {_mm(sheet.width_nm)} {_mm(sheet.height_nm)})",
        f'    (property "Sheetname" "{_esc(sheet.name)}" (at 0 0 0) '
        f"(effects (font (size 1.27 1.27))))",
        f'    (property "Sheetfile" "{_esc(sheet.file)}" (at 0 0 0) '
        f"(effects (font (size 1.27 1.27))))",
        f'    (uuid "{_u("sheet:" + file + sheet.file)}")',
        "  )",
    ]


def _project_json(project: ImportedProject, stem: str) -> dict:
    source = project.project
    classes: list[dict] = []
    for netclass in project.netclasses:
        classes.append({
            "name": netclass.name,
            "clearance": netclass.clearance_nm / NM_PER_MM,
            "track_width": netclass.track_width_nm / NM_PER_MM,
            "via_diameter": netclass.via_diameter_nm / NM_PER_MM,
            "via_drill": netclass.via_drill_nm / NM_PER_MM,
            "diff_pair_width": netclass.diff_pair_width_nm / NM_PER_MM,
            "diff_pair_gap": netclass.diff_pair_gap_nm / NM_PER_MM,
        })
    data = {
        "meta": {"filename": f"{stem}.kicad_pro", "version": 1,
                 "generator": "mota"},
        "boards": [],
        "board": {
            "design_settings": {
                "rules": dict(project.design_rules),
                "defaults": {},
            },
            "layer_presets": source.get("board", {}).get("layer_presets", []),
        },
        "net_settings": {"classes": classes,
                         "meta": {"version": 3}},
        "sheets": source.get("sheets", []),
        "libraries": source.get("libraries", {}),
    }
    return data
