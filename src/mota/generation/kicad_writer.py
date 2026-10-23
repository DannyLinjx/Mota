"""Best-effort KiCad schematic/PCB writer for development verification.

Compatibility with a real KiCad release has NOT been verified in this
environment (no kicad-cli). The product therefore keeps the KiCad export entry
disabled until an external compatibility run records a PASS. The writer uses the
official S-expression shapes and generator name `mota`; it never impersonates
eeschema/pcbnew.
"""

from __future__ import annotations

import uuid
from pathlib import Path

from mota.circuit.parts import resolve_footprint, resolve_symbol
from mota.domain.models import BoardIR, CircuitIR, SchematicIR
from mota.domain.units import NM_PER_MM
from mota.errors import NotFound


def _u(value: str) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_URL, "mota:" + value))


def _f(nm: int) -> str:
    value = nm / NM_PER_MM
    return f"{value:.4f}".rstrip("0").rstrip(".")


def _esc(text: str) -> str:
    return text.replace("\\", "\\\\").replace('"', '\\"')


def _pin_type(electrical: str) -> str:
    return {
        "power_in": "power_in", "power_out": "power_out", "input": "input",
        "output": "output", "bidirectional": "bidirectional",
        "passive": "passive", "no_connect": "no_connect",
        "open_collector": "open_collector",
    }.get(electrical, "passive")


def write_schematic(circuit: CircuitIR, schematic: SchematicIR, out: Path) -> None:
    lines: list[str] = []
    lines.append("(kicad_sch (version 20230121) (generator mota)")
    lines.append(f'  (uuid "{_u("sch:" + schematic.title)}")')
    lines.append('  (paper "A4")')
    lines.append("  (lib_symbols")
    for symbol_id in sorted({c.symbol_id for c in circuit.components}):
        try:
            symbol = resolve_symbol(symbol_id)
        except NotFound:
            continue
        lines.append(f'    (symbol "Mota:{_esc(symbol_id)}" (pin_names (offset 0.254)) '
                     f'(in_bom yes) (on_board yes)')
        lines.append('      (property "Reference" "U" (at 0 0 0) '
                     '(effects (font (size 1.27 1.27))))')
        lines.append('      (property "Value" "" (at 0 0 0) '
                     '(effects (font (size 1.27 1.27))))')
        lines.append(f'      (symbol "{_esc(symbol_id)}_0_1"')
        lines.append(f'        (rectangle (start {_f(-symbol.body_w_nm // 2)} '
                     f'{_f(symbol.body_h_nm // 2)}) (end {_f(symbol.body_w_nm // 2)} '
                     f'{_f(-symbol.body_h_nm // 2)}) (stroke (width 0.254) (type default)) '
                     f'(fill (type none)))')
        lines.append("      )")
        lines.append(f'      (symbol "{_esc(symbol_id)}_1_1"')
        for pin in symbol.pins:
            angle = {"right": 0, "up": 90, "left": 180, "down": 270}[pin.direction]
            lines.append(
                f'        (pin {_pin_type(pin.electrical_type)} line (at {_f(pin.x_nm)} '
                f'{_f(pin.y_nm)} {angle}) (length {_f(pin.length_nm)}) '
                f'(name "{_esc(pin.name)}" (effects (font (size 1.27 1.27)))) '
                f'(number "{_esc(pin.number)}" (effects (font (size 1.27 1.27)))))')
        lines.append("      )")
        lines.append("    )")
    lines.append("  )")
    net_names = {n.id: n.name for n in circuit.nets}
    for wire_index, wire in enumerate(schematic.wires):
        if wire.net_id == "__nc__":
            continue
        for index in range(len(wire.points) - 1):
            a, b = wire.points[index], wire.points[index + 1]
            lines.append(
                f'  (wire (pts (xy {_f(a[0])} {_f(a[1])}) (xy {_f(b[0])} {_f(b[1])})) '
                f'(stroke (width 0.1524) (type default)) '
                f'(uuid "{_u(f"wire{wire_index}-{index}")}"))')
    for junction in schematic.junctions:
        lines.append(f'  (junction (at {_f(junction.x_nm)} {_f(junction.y_nm)}) '
                     f'(diameter 0) (color 0 0 0 0) (uuid "{_u(f"junction{junction.x_nm}{junction.y_nm}")}"))')
    for label in schematic.labels:
        lines.append(f'  (label "{_esc(label.text)}" (at {_f(label.x_nm)} {_f(label.y_nm)} '
                     f'{label.rotation_deg}) (effects (font (size 1.27 1.27)) '
                     f'(justify left bottom)) (uuid "{_u(f"label{label.x_nm}{label.y_nm}{label.net_id}")}"))')
    for comp in circuit.components:
        sym = schematic.symbol(comp.id)
        lines.append(f'  (symbol (lib_id "Mota:{_esc(comp.symbol_id)}") '
                     f'(at {_f(sym.x_nm)} {_f(sym.y_nm)} 0) (unit 1)')
        lines.append('    (in_bom yes) (on_board yes) (dnp no)')
        lines.append(f'    (uuid "{_u("comp:" + comp.id)}")')
        lines.append(f'    (property "Reference" "{_esc(comp.reference)}" (at {_f(sym.x_nm)} '
                     f'{_f(sym.y_nm + sym.body_h_nm // 2 + 1_270_000)} 0) '
                     f'(effects (font (size 1.27 1.27))))')
        lines.append(f'    (property "Value" "{_esc(comp.value)}" (at {_f(sym.x_nm)} '
                     f'{_f(sym.y_nm - sym.body_h_nm // 2 - 1_270_000)} 0) '
                     f'(effects (font (size 1.27 1.27))))')
        lines.append(f'    (property "Footprint" "Mota:{_esc(comp.footprint_id)}" (at 0 0 0) '
                     f'(effects (font (size 1.27 1.27)) hide))')
        for pin_number in sorted(comp.pin_pad_map):
            lines.append(f'    (pin "{_esc(pin_number)}" (uuid "{_u(f"pin:{comp.id}:{pin_number}")}"))')
        lines.append(f'    (instances (project "mota" (path "/{_u("sch:" + schematic.title)}" '
                     f'(reference "{_esc(comp.reference)}") (unit 1))))')
        lines.append("  )")
    lines.append('  (sheet_instances (path "/" (page "1")))')
    lines.append(")")
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_pcb(circuit: CircuitIR, board: BoardIR, out: Path) -> None:
    height_mm = board.height_nm / NM_PER_MM

    def fy(nm: int) -> str:
        return f"{height_mm - nm / NM_PER_MM:.4f}".rstrip("0").rstrip(".")

    def fx(nm: int) -> str:
        return _f(nm)

    net_names = {n.id: n.name for n in circuit.nets}
    net_numbers = {n.id: index + 1 for index, n in enumerate(circuit.nets)}
    lines: list[str] = []
    lines.append("(kicad_pcb (version 20221018) (generator mota)")
    lines.append(f"  (general (thickness {board.thickness_nm / NM_PER_MM:.3f}))")
    lines.append('  (paper "A4")')
    lines.append("  (layers")
    lines.append('    (0 "F.Cu" signal)')
    lines.append('    (31 "B.Cu" signal)')
    lines.append('    (36 "B.SilkS" user "B.Silkscreen")')
    lines.append('    (37 "F.SilkS" user "F.Silkscreen")')
    lines.append('    (44 "Edge.Cuts" user)')
    lines.append("  )")
    lines.append("  (setup (pad_to_mask_clearance 0))")
    lines.append('  (net 0 "")')
    for net in circuit.nets:
        lines.append(f'  (net {net_numbers[net.id]} "{_esc(net.name)}")')
    for hole in board.holes:
        lines.append(f'    (footprint "Mota:MountingHole" (layer "F.Cu") '
                     f'(at {fx(hole.x_nm)} {fy(hole.y_nm)}) (uuid "{_u(hole.id)}")')
        lines.append(f'      (property "Reference" "H{hole.id[-1]}" (at 0 0) '
                     f'(effects (font (size 1 1))))')
        lines.append(f'      (pad "" np_thru_hole circle (at 0 0) '
                     f'(size {hole.drill_nm / NM_PER_MM:.3f} {hole.drill_nm / NM_PER_MM:.3f}) '
                     f'(drill {hole.drill_nm / NM_PER_MM:.3f}) (layers "*.Cu" "*.Mask") '
                     f'(uuid "{_u("holepad:" + hole.id)}"))')
        lines.append("    )")
    for fp in board.footprints:
        comp = circuit.component(fp.component_id)
        try:
            footprint = resolve_footprint(fp.footprint_id)
        except NotFound:
            continue
        rotation = -fp.rotation_deg if fp.side == "top" else fp.rotation_deg
        layer = "F.Cu" if fp.side == "top" else "B.Cu"
        lines.append(f'  (footprint "Mota:{_esc(fp.footprint_id)}" (layer "{layer}") '
                     f'(at {fx(fp.x_nm)} {fy(fp.y_nm)} {rotation}) '
                     f'(uuid "{_u("fp:" + fp.component_id)}")')
        lines.append(f'    (property "Reference" "{_esc(fp.reference)}" (at 0 '
                     f'{(fp.courtyard.h_nm / 2 + 1_000_000) / NM_PER_MM:.3f}) '
                     f'(effects (font (size 1 1))))')
        lines.append(f'    (property "Value" "{_esc(comp.value)}" (at 0 0) '
                     f'(effects (font (size 1 1)) hide))')
        pin_for_pad: dict[str, str] = {}
        for pin, pads in comp.pin_pad_map.items():
            for pad_number in pads:
                pin_for_pad[pad_number] = pin
        for pad in footprint.pads:
            dx = pad.x_nm
            dy = -pad.y_nm   # file coordinates are y-down
            net_id = None
            for abs_pad in board.pads:
                if abs_pad.component_id == fp.component_id and abs_pad.number == pad.number:
                    net_id = abs_pad.net_id
                    break
            net_text = ""
            if net_id:
                net_text = f' (net {net_numbers[net_id]} "{_esc(net_names[net_id])}")'
            drill = f" (drill {pad.drill_nm / NM_PER_MM:.3f})" if pad.drill_nm else ""
            lines.append(
                f'    (pad "{_esc(pad.number)}" thru_hole {pad.shape} '
                f'(at {_f(dx)} {_f(dy)}) (size {pad.w_nm / NM_PER_MM:.3f} '
                f'{pad.h_nm / NM_PER_MM:.3f}){drill} '
                f'(layers "*.Cu" "*.Mask"){net_text} '
                f'(uuid "{_u(f"pad:{fp.component_id}:{pad.number}")}"))')
        lines.append("  )")
    for track in board.tracks:
        for index in range(len(track.points) - 1):
            a, b = track.points[index], track.points[index + 1]
            lines.append(
                f'  (segment (start {fx(a[0])} {fy(a[1])}) (end {fx(b[0])} {fy(b[1])}) '
                f'(width {track.width_nm / NM_PER_MM:.3f}) (layer "{track.layer}") '
                f'(net {net_numbers[track.net_id]}) '
                f'(uuid "{_u(f"seg:{track.id}:{index}")}"))')
    for via in board.vias:
        lines.append(f'  (via (at {fx(via.x_nm)} {fy(via.y_nm)}) '
                     f'(size {via.outer_nm / NM_PER_MM:.3f}) '
                     f'(drill {via.drill_nm / NM_PER_MM:.3f}) (layers "F.Cu" "B.Cu") '
                     f'(net {net_numbers[via.net_id]}) (uuid "{_u("via:" + via.id)}"))')
    outline = board.outline
    for index in range(len(outline) - 1):
        a, b = outline[index], outline[index + 1]
        lines.append(f'  (gr_line (start {fx(a[0])} {fy(a[1])}) (end {fx(b[0])} {fy(b[1])}) '
                     f'(layer "Edge.Cuts") (width 0.1) '
                     f'(uuid "{_u(f"edge:{index}")}"))')
    lines.append(")")
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_project(project_name: str, out: Path) -> None:
    out.write_text(
        '{\n  "board": {"design_settings": {"defaults": {"board_outline_line_width": 0.1}}},\n'
        '  "meta": {"filename": "design.kicad_pro", "version": 1},\n'
        f'  "sheets": [["00000000-0000-0000-0000-000000000000", "Root"]],\n'
        f'  "libraries": {{"pinned_footprint_libs": ["Mota"], "pinned_symbol_libs": ["Mota"]}}\n'
        "}\n", encoding="utf-8")
