"""KiCad project import, export and round-trip comparison.

Scope and honesty rules:

- Coordinates are kept in KiCad's own millimetre grid converted to integer
  nanometres (``x_nm = round(x_mm * 1e6)``) with KiCad's y-down orientation.
  No board origin is invented, so a re-export is byte-comparable in value even
  for projects whose outline is not centred on the origin.
- Anything electrical that cannot be represented is reported in
  :attr:`ImportedProject.unknown`; it is never silently dropped. Zone fill
  copper, custom pad primitives and curved copper tracks are examples.
- Round-trip differences are split by origin: issues that already exist in the
  source project are kept separate from issues introduced by the Mota export.
"""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Literal

from mota.library.sexpr import (
    Node, atom_value, atoms, child, children_named, head, parse,
)

COORDINATE_SYSTEM = "kicad_mm_y_down"
NM_PER_MM = 1_000_000


class KicadImportError(ValueError):
    def __init__(self, code: str, message: str, path: Path | None = None) -> None:
        self.code = code
        self.path = path
        text = f"{code}: {message}"
        if path is not None:
            text += f" ({path})"
        super().__init__(text)


# --------------------------------------------------------------------------- #
# Model
# --------------------------------------------------------------------------- #

@dataclass(frozen=True)
class UnknownItem:
    area: str        # pcb | schematic | project
    path: str        # file-relative location, e.g. "CM4IOv5.kicad_pcb"
    detail: str
    severity: str    # electrical | geometry | metadata
    count: int = 1


@dataclass(frozen=True)
class ImportedLayer:
    number: int
    name: str
    kind: str
    user_name: str = ""


@dataclass(frozen=True)
class ImportedNet:
    number: int
    name: str


@dataclass(frozen=True)
class ImportedPad:
    number: str
    net: str
    kind: str
    shape: str
    x_nm: int
    y_nm: int
    rotation_deg: float
    w_nm: int
    h_nm: int
    drill_nm: int
    drill_oval: bool
    layers: tuple[str, ...]
    locked: bool
    drill_w_nm: int = 0
    drill_h_nm: int = 0


@dataclass(frozen=True)
class ImportedFootprint:
    reference: str
    footprint_id: str
    layer: str
    x_nm: int
    y_nm: int
    rotation_deg: float
    side: str
    value: str
    sheetfile: str
    attributes: tuple[str, ...]
    pads: tuple[ImportedPad, ...]


@dataclass(frozen=True)
class ImportedTrack:
    net: str
    layer: str
    width_nm: int
    start_nm: tuple[int, int]
    end_nm: tuple[int, int]


@dataclass(frozen=True)
class ImportedVia:
    net: str
    x_nm: int
    y_nm: int
    size_nm: int
    drill_nm: int
    layers: tuple[str, ...]


@dataclass(frozen=True)
class ImportedZone:
    net: str
    layers: tuple[str, ...]
    outline: tuple[tuple[int, int], ...]
    priority: int
    keepout: bool
    filled_polygons: int


@dataclass(frozen=True)
class ImportedGraphic:
    kind: str
    layer: str
    points: tuple[tuple[int, int], ...]
    width_nm: int
    params: tuple[tuple[int, int], ...] = ()


@dataclass(frozen=True)
class ImportedNetclass:
    name: str
    clearance_nm: int
    track_width_nm: int
    via_diameter_nm: int
    via_drill_nm: int
    diff_pair_width_nm: int
    diff_pair_gap_nm: int


@dataclass(frozen=True)
class ImportedSymbol:
    lib_id: str
    reference: str
    value: str
    footprint: str
    x_nm: int
    y_nm: int
    rotation_deg: float
    unit: int
    mirror: str
    pins: tuple[str, ...]


@dataclass(frozen=True)
class ImportedSheet:
    name: str
    file: str
    x_nm: int
    y_nm: int
    width_nm: int
    height_nm: int


@dataclass(frozen=True)
class ImportedSchematic:
    file: str
    version: str
    generator: str
    symbols: tuple[ImportedSymbol, ...]
    wires: tuple[tuple[tuple[int, int], ...], ...]
    labels: tuple[tuple[str, int, int, float], ...]
    junctions: tuple[tuple[int, int], ...]
    no_connects: tuple[tuple[int, int], ...]
    sheets: tuple[ImportedSheet, ...]


@dataclass(frozen=True)
class ImportedProject:
    source_dir: Path
    project_file: str
    pcb_file: str
    pcb_version: str
    pcb_generator: str
    thickness_nm: int
    layers: tuple[ImportedLayer, ...]
    nets: tuple[ImportedNet, ...]
    footprints: tuple[ImportedFootprint, ...]
    tracks: tuple[ImportedTrack, ...]
    vias: tuple[ImportedVia, ...]
    zones: tuple[ImportedZone, ...]
    graphics: tuple[ImportedGraphic, ...]
    outline: tuple[tuple[int, int], ...]
    netclasses: tuple[ImportedNetclass, ...]
    design_rules: dict[str, float]
    schematics: tuple[ImportedSchematic, ...]
    root_schematic: str
    project: dict[str, Any]
    unknown: tuple[UnknownItem, ...]
    counts: dict[str, int]
    file_hashes: dict[str, str]
    coordinate_system: str = COORDINATE_SYSTEM

    def net_names(self) -> set[str]:
        return {net.name for net in self.nets if net.name}

    def copper_layers(self) -> list[str]:
        return [layer.name for layer in self.layers
                if layer.kind in {"signal", "power", "mixed"}]

    def footprint(self, reference: str) -> ImportedFootprint | None:
        for footprint in self.footprints:
            if footprint.reference == reference:
                return footprint
        return None


@dataclass(frozen=True)
class Difference:
    category: Literal["semantic", "geometry", "rule"]
    path: str
    expected: str
    actual: str
    origin: Literal["source", "mota"]


@dataclass
class RoundtripReport:
    status: str  # PASS | PASS_WITH_SOURCE_ISSUES | FAIL
    differences: list[Difference] = field(default_factory=list)
    source_issues: list[UnknownItem] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    checked: list[str] = field(default_factory=list)

    @property
    def mota_issues(self) -> list[Difference]:
        return [item for item in self.differences if item.origin == "mota"]

    def format(self) -> str:
        lines = [f"# Round-trip report ({self.status})", ""]
        lines.append(f"- checks: {', '.join(self.checked) or '(none)'}")
        lines.append(f"- differences: {len(self.differences)} "
                     f"(mota: {len(self.mota_issues)})")
        lines.append(f"- source issues: {len(self.source_issues)}")
        for item in self.differences:
            lines.append(f"- [{item.category}/{item.origin}] {item.path}: "
                         f"{item.expected} -> {item.actual}")
        for item in self.source_issues:
            lines.append(f"- [source/{item.severity}] {item.area}:{item.path} "
                         f"{item.detail} (x{item.count})")
        for warning in self.warnings:
            lines.append(f"- [export-warning] {warning}")
        return "\n".join(lines)


# --------------------------------------------------------------------------- #
# Parsing helpers
# --------------------------------------------------------------------------- #

def _text(node: Node | None, name: str, default: str = "") -> str:
    found = child(node, name)
    return atom_value(found, default) if found is not None else default


def _floats(node: Node | None, name: str) -> list[float]:
    found = child(node, name)
    if found is None:
        return []
    values: list[float] = []
    for item in atoms(found):
        try:
            values.append(float(item.value))
        except ValueError:
            continue
    return values


def _int(node: Node | None, name: str, default: int = 0) -> int:
    values = _floats(node, name)
    return int(round(values[0])) if values else default


def _nm(value: float) -> int:
    return int(round(value * NM_PER_MM))


def _points(node: Node | None) -> tuple[tuple[int, int], ...]:
    if node is None:
        return ()
    out: list[tuple[int, int]] = []
    for item in children_named(node, "xy"):
        values = [float(atom.value) for atom in atoms(item)
                  if _is_float(atom.value)]
        if len(values) >= 2:
            out.append((_nm(values[0]), _nm(values[1])))
    return tuple(out)


def _is_float(text: str) -> bool:
    try:
        float(text)
        return True
    except ValueError:
        return False


def _node_head_atoms(node: Node) -> list[str]:
    return [item.value for item in atoms(node)]


def _all_atoms(node: Node) -> list[str]:
    """Every non-list child value, including a data token in first position.

    ``(layers (0 "F.Cu" signal))`` and ``(net 1 "GND")`` put data first, so the
    usual "skip the head token" convention would drop the layer number.
    """

    return [item.value for item in node.children if not item.is_list]


class _UnknownSink:
    def __init__(self) -> None:
        self.items: dict[tuple[str, str, str, str], int] = {}

    def add(self, area: str, path: str, detail: str, severity: str,
            count: int = 1) -> None:
        key = (area, path, detail, severity)
        self.items[key] = self.items.get(key, 0) + count

    def freeze(self) -> tuple[UnknownItem, ...]:
        return tuple(
            UnknownItem(area=area, path=path, detail=detail, severity=severity,
                        count=count)
            for (area, path, detail, severity), count in sorted(self.items.items()))


# --------------------------------------------------------------------------- #
# Import
# --------------------------------------------------------------------------- #

def _discover(directory: Path) -> tuple[Path, Path]:
    projects = sorted(directory.glob("*.kicad_pro"))
    boards = sorted(directory.glob("*.kicad_pcb"))
    if not projects:
        raise KicadImportError("MISSING_PROJECT_FILE",
                               "no .kicad_pro in the project directory", directory)
    if len(projects) > 1:
        raise KicadImportError(
            "AMBIGUOUS_PROJECT",
            "more than one .kicad_pro; pass a single project file instead",
            directory)
    if not boards:
        raise KicadImportError("MISSING_PCB_FILE", "no .kicad_pcb found", directory)
    if len(boards) > 1:
        raise KicadImportError("AMBIGUOUS_PCB",
                               "more than one .kicad_pcb; pass one board file",
                               directory)
    return projects[0], boards[0]


def _parse_pcb(path: Path, nets: dict[int, str], unknown: _UnknownSink,
               schematics: tuple[ImportedSchematic, ...]) -> dict[str, Any]:
    text = path.read_text(encoding="utf-8")
    root = parse(text)
    if head(root) != "kicad_pcb":
        raise KicadImportError("UNSUPPORTED_FORMAT",
                               f"expected kicad_pcb, found {head(root)!r}", path)
    version = _text(root, "version")
    generator = _text(root, "generator")

    layers: list[ImportedLayer] = []
    layers_node = child(root, "layers")
    if layers_node is not None:
        for entry in layers_node.children:
            if not entry.is_list:
                continue
            values = _all_atoms(entry)
            if len(values) < 3:
                continue
            try:
                number = int(values[0])
            except ValueError:
                continue
            user_name = values[3] if len(values) > 3 else ""
            layers.append(ImportedLayer(number=number, name=values[1],
                                        kind=values[2], user_name=user_name))
    if not layers:
        raise KicadImportError("NO_LAYERS", "board declares no layers", path)

    net_table: dict[int, str] = {}
    for item in children_named(root, "net"):
        values = _node_head_atoms(item)
        if not values:
            continue
        try:
            number = int(values[0])
        except ValueError:
            continue
        name = values[1] if len(values) > 1 else ""
        net_table[number] = name
        nets[number] = name

    thickness_values = _floats(child(root, "general"), "thickness")
    thickness_nm = _nm(thickness_values[0]) if thickness_values else 0

    footprints: list[ImportedFootprint] = []
    tracks: list[ImportedTrack] = []
    vias: list[ImportedVia] = []
    zones: list[ImportedZone] = []
    graphics: list[ImportedGraphic] = []
    references: set[str] = set()

    def net_name(number: int) -> str:
        return net_table.get(number, "")

    for item in root.children:
        if not item.is_list:
            continue
        tag = head(item)
        if tag == "footprint":
            footprint = _parse_footprint(item, net_name, unknown)
            if footprint.reference in references and footprint.reference:
                unknown.add("pcb", path.name,
                            f"duplicate reference {footprint.reference}",
                            "electrical")
            references.add(footprint.reference)
            footprints.append(footprint)
        elif tag == "segment":
            start = _floats(item, "start")
            end = _floats(item, "end")
            if len(start) < 2 or len(end) < 2:
                unknown.add("pcb", path.name, "segment without endpoints",
                            "geometry")
                continue
            tracks.append(ImportedTrack(
                net=net_name(_int(item, "net", 0)),
                layer=_text(item, "layer"),
                width_nm=_nm(_floats(item, "width")[0]) if _floats(item, "width") else 0,
                start_nm=(_nm(start[0]), _nm(start[1])),
                end_nm=(_nm(end[0]), _nm(end[1]))))
        elif tag == "arc":
            unknown.add("pcb", path.name,
                        "curved copper track (arc) is not imported", "electrical")
        elif tag == "via":
            at = _floats(item, "at")
            if len(at) < 2:
                unknown.add("pcb", path.name, "via without position", "geometry")
                continue
            layers_node = child(item, "layers")
            layer_names = tuple(atom.value for atom in atoms(layers_node))
            vias.append(ImportedVia(
                net=net_name(_int(item, "net", 0)),
                x_nm=_nm(at[0]), y_nm=_nm(at[1]),
                size_nm=_nm(_floats(item, "size")[0]) if _floats(item, "size") else 0,
                drill_nm=_nm(_floats(item, "drill")[0]) if _floats(item, "drill") else 0,
                layers=layer_names))
        elif tag == "zone":
            zones.append(_parse_zone(item, net_name, unknown, path.name))
        elif tag in {"gr_line", "gr_arc", "gr_circle", "gr_rect", "gr_poly"}:
            graphics.extend(_parse_graphic(item, unknown, path.name))
        elif tag in {"gr_curve", "dimension", "target", "group", "image"}:
            unknown.add("pcb", path.name, f"{tag} is not imported", "geometry")
        elif tag == "zone_fill":
            unknown.add("pcb", path.name, "zone_fill is not imported", "geometry")

    outline = _chain_outline([g for g in graphics if g.layer == "Edge.Cuts"],
                             unknown, path.name)
    sheetfiles = {footprint.sheetfile for footprint in footprints
                  if footprint.sheetfile}
    known_files = {schematic.file for schematic in schematics}
    known_files |= {sheet.file for schematic in schematics
                    for sheet in schematic.sheets}
    for name in sorted(sheetfiles - known_files - {""}):
        unknown.add("pcb", path.name,
                    f"footprint references sheet {name} that was not imported",
                    "electrical")

    return {
        "version": version,
        "generator": generator,
        "layers": tuple(layers),
        "nets": tuple(ImportedNet(number=number, name=name)
                      for number, name in sorted(net_table.items())),
        "footprints": tuple(footprints),
        "tracks": tuple(tracks),
        "vias": tuple(vias),
        "zones": tuple(zones),
        "graphics": tuple(graphics),
        "outline": outline,
        "thickness_nm": thickness_nm,
    }


def _parse_footprint(node: Node, net_name, unknown: _UnknownSink) -> ImportedFootprint:
    values = _node_head_atoms(node)
    footprint_id = values[0] if values else ""
    layer = _text(node, "layer", "F.Cu")
    at = _floats(node, "at")
    x_nm = _nm(at[0]) if at else 0
    y_nm = _nm(at[1]) if len(at) > 1 else 0
    rotation = at[2] if len(at) > 2 else 0.0
    reference = ""
    value = ""
    for text_node in children_named(node, "fp_text"):
        words = _node_head_atoms(text_node)
        if len(words) >= 2 and words[0] == "reference":
            reference = words[1]
        elif len(words) >= 2 and words[0] == "value":
            value = words[1]
    sheetfile = ""
    for prop in children_named(node, "property"):
        words = _node_head_atoms(prop)
        if len(words) >= 2 and words[0] == "Reference" and not reference:
            reference = words[1]
        elif len(words) >= 2 and words[0] == "Value" and not value:
            value = words[1]
        elif len(words) >= 2 and words[0] == "Sheetfile":
            sheetfile = words[1]
    pads = tuple(_parse_pad(pad, net_name, unknown) for pad in children_named(node, "pad"))
    attributes_node = child(node, "attr")
    attributes = tuple(atom.value for atom in atoms(attributes_node))
    return ImportedFootprint(
        reference=reference, footprint_id=footprint_id, layer=layer,
        x_nm=x_nm, y_nm=y_nm, rotation_deg=rotation,
        side="bottom" if layer.startswith("B.") else "top",
        value=value, sheetfile=sheetfile, attributes=attributes, pads=pads)


def _parse_pad(node: Node, net_name, unknown: _UnknownSink) -> ImportedPad:
    values = _node_head_atoms(node)
    number = values[0] if values else ""
    kind = values[1] if len(values) > 1 else ""
    shape = values[2] if len(values) > 2 else ""
    locked = "locked" in values
    at = _floats(node, "at")
    size = _floats(node, "size")
    drill_values = _floats(node, "drill")
    drill_node = child(node, "drill")
    drill_oval = bool(drill_node is not None
                      and any(atom.value == "oval" for atom in atoms(drill_node)))
    layers_node = child(node, "layers")
    layers = tuple(atom.value for atom in atoms(layers_node))
    net_number = _int(node, "net", 0)
    if shape == "custom" or child(node, "primitives") is not None:
        unknown.add("pcb", "pad", "custom pad copper primitives are not imported",
                    "electrical")
    if drill_oval:
        if len(drill_values) >= 2:
            unknown.add("pcb", "pad",
                        "oval drill is stored as a width/height pair and "
                        "exported as a slot", "geometry")
        else:
            unknown.add("pcb", "pad",
                        "oval drill has a single dimension; slot endpoints "
                        "cannot be derived", "electrical")
    drill_w = _nm(drill_values[0]) if drill_values else 0
    drill_h = _nm(drill_values[1]) if len(drill_values) > 1 else drill_w
    if not number and kind not in {"np_thru_hole"}:
        unknown.add("pcb", "pad", "pad without number is not matched to a pin",
                    "metadata")
    return ImportedPad(
        number=number, net=net_name(net_number), kind=kind, shape=shape,
        x_nm=_nm(at[0]) if at else 0,
        y_nm=_nm(at[1]) if len(at) > 1 else 0,
        rotation_deg=at[2] if len(at) > 2 else 0.0,
        w_nm=_nm(size[0]) if size else 0,
        h_nm=_nm(size[1]) if len(size) > 1 else (_nm(size[0]) if size else 0),
        drill_nm=max(drill_w, drill_h),
        drill_oval=drill_oval, layers=layers, locked=locked,
        drill_w_nm=drill_w, drill_h_nm=drill_h)


def _parse_zone(node: Node, net_name, unknown: _UnknownSink,
                file_name: str) -> ImportedZone:
    net_number = _int(node, "net", 0)
    net = net_name(net_number) or _text(node, "net_name")
    layer = _text(node, "layer")
    layers_node = child(node, "layers")
    layers = tuple(atom.value for atom in atoms(layers_node)) if layers_node else ()
    if layer:
        layers = (layer,)
    outlines: list[tuple[tuple[int, int], ...]] = []
    for polygon in children_named(node, "polygon"):
        points = _points(child(polygon, "pts"))
        if len(points) >= 3:
            outlines.append(points)
    filled = len(children_named(node, "filled_polygon"))
    if filled:
        unknown.add("pcb", file_name,
                    "zone fill copper geometry is not imported (outline only)",
                    "electrical", filled)
    keepout = child(node, "keepout") is not None
    if keepout:
        unknown.add("pcb", file_name,
                    "keepout restriction detail is not imported "
                    "(presence only)", "geometry")
    priority = 0
    priority_node = child(node, "priority")
    if priority_node is not None:
        priority = int(round(float(atom_value(priority_node, "0"))))
    return ImportedZone(net=net, layers=layers,
                        outline=outlines[0] if outlines else (),
                        priority=priority, keepout=keepout,
                        filled_polygons=filled)


def _parse_graphic(node: Node, unknown: _UnknownSink,
                   file_name: str) -> list[ImportedGraphic]:
    tag = head(node)
    layer = _text(node, "layer")
    width = _nm(_floats(node, "width")[0]) if _floats(node, "width") else 0
    if tag == "gr_line":
        start = _floats(node, "start")
        end = _floats(node, "end")
        if len(start) < 2 or len(end) < 2:
            unknown.add("pcb", file_name, "gr_line without endpoints", "geometry")
            return []
        pair = ((_nm(start[0]), _nm(start[1])), (_nm(end[0]), _nm(end[1])))
        return [ImportedGraphic("line", layer, pair, width, pair)]
    if tag == "gr_arc":
        start = _floats(node, "start")
        mid = _floats(node, "mid")
        end = _floats(node, "end")
        if len(start) < 2 or len(end) < 2:
            unknown.add("pcb", file_name, "gr_arc without endpoints", "geometry")
            return []
        if len(mid) >= 2:
            points = _arc_points(start, mid, end)
            params = ((_nm(start[0]), _nm(start[1])),
                      (_nm(mid[0]), _nm(mid[1])),
                      (_nm(end[0]), _nm(end[1])))
        else:
            points = ((_nm(start[0]), _nm(start[1])), (_nm(end[0]), _nm(end[1])))
            params = points
            unknown.add("pcb", file_name,
                        "gr_arc without mid point is stored as a chord",
                        "geometry")
        return [ImportedGraphic("arc", layer, points, width, params)]
    if tag == "gr_circle":
        center = _floats(node, "center")
        end = _floats(node, "end")
        if len(center) < 2 or len(end) < 2:
            unknown.add("pcb", file_name, "gr_circle without geometry", "geometry")
            return []
        points = _circle_points(center, end)
        params = ((_nm(center[0]), _nm(center[1])), (_nm(end[0]), _nm(end[1])))
        return [ImportedGraphic("circle", layer, points, width, params)]
    if tag == "gr_rect":
        start = _floats(node, "start")
        end = _floats(node, "end")
        if len(start) < 2 or len(end) < 2:
            unknown.add("pcb", file_name, "gr_rect without corners", "geometry")
            return []
        points = ((_nm(start[0]), _nm(start[1])), (_nm(end[0]), _nm(start[1])),
                  (_nm(end[0]), _nm(end[1])), (_nm(start[0]), _nm(end[1])),
                  (_nm(start[0]), _nm(start[1])))
        params = ((_nm(start[0]), _nm(start[1])), (_nm(end[0]), _nm(end[1])))
        return [ImportedGraphic("rect", layer, points, width, params)]
    if tag == "gr_poly":
        points = _points(child(node, "pts"))
        if len(points) < 3:
            unknown.add("pcb", file_name, "gr_poly with fewer than 3 points",
                        "geometry")
            return []
        return [ImportedGraphic("poly", layer, points, width, points)]
    unknown.add("pcb", file_name, f"{tag} is not imported", "geometry")
    return []


def _arc_points(start: list[float], mid: list[float],
                end: list[float]) -> tuple[tuple[int, int], ...]:
    """Sample a three-point arc; the writer re-emits the original points."""

    ax, ay = start[0], start[1]
    bx, by = mid[0], mid[1]
    cx, cy = end[0], end[1]
    determinant = 2.0 * (ax * (by - cy) + bx * (cy - ay) + cx * (ay - by))
    if abs(determinant) < 1e-9:
        return ((_nm(ax), _nm(ay)), (_nm(cx), _nm(cy)))
    ux = ((ax * ax + ay * ay) * (by - cy) + (bx * bx + by * by) * (cy - ay)
          + (cx * cx + cy * cy) * (ay - by)) / determinant
    uy = ((ax * ax + ay * ay) * (cx - bx) + (bx * bx + by * by) * (ax - cx)
          + (cx * cx + cy * cy) * (bx - ax)) / determinant
    radius = math.hypot(ax - ux, ay - uy)
    start_angle = math.atan2(ay - uy, ax - ux)
    mid_angle = math.atan2(by - uy, bx - ux)
    end_angle = math.atan2(cy - uy, cx - ux)

    def normalize(angle: float) -> float:
        while angle < 0:
            angle += 2 * math.pi
        return angle

    sweep_forward = (normalize(end_angle - start_angle)
                     >= normalize(mid_angle - start_angle))
    if sweep_forward:
        sweep = normalize(end_angle - start_angle)
    else:
        sweep = -normalize(start_angle - end_angle)
    steps = 16
    points: list[tuple[int, int]] = [(_nm(ax), _nm(ay))]
    for index in range(1, steps):
        angle = start_angle + sweep * index / steps
        points.append((_nm(ux + radius * math.cos(angle)),
                       _nm(uy + radius * math.sin(angle))))
    points.append((_nm(cx), _nm(cy)))
    return tuple(points)


def _circle_points(center: list[float], end: list[float]) -> tuple[tuple[int, int], ...]:
    cx, cy = center[0], center[1]
    radius = math.hypot(end[0] - cx, end[1] - cy)
    points = [(_nm(cx + radius * math.cos(2 * math.pi * index / 32)),
               _nm(cy + radius * math.sin(2 * math.pi * index / 32)))
              for index in range(32)]
    points.append(points[0])
    return tuple(points)


def _chain_outline(graphics: list[ImportedGraphic], unknown: _UnknownSink,
                   file_name: str) -> tuple[tuple[int, int], ...]:
    if not graphics:
        unknown.add("pcb", file_name, "no Edge.Cuts outline found", "geometry")
        return ()
    segments: list[tuple[tuple[int, int], tuple[int, int]]] = []
    for graphic in graphics:
        points = list(graphic.points)
        if len(points) < 2:
            continue
        for index in range(len(points) - 1):
            segments.append((points[index], points[index + 1]))
        if graphic.kind in {"rect", "poly", "circle"} and points[0] != points[-1]:
            segments.append((points[-1], points[0]))
    if not segments:
        return ()
    key = lambda point: (round(point[0] / 1000), round(point[1] / 1000))
    adjacency: dict[tuple[int, int], list[int]] = {}
    for index, (start, end) in enumerate(segments):
        adjacency.setdefault(key(start), []).append(index)
        adjacency.setdefault(key(end), []).append(index)
    chain: list[tuple[int, int]] = []
    used: set[int] = set()
    current = segments[0][0]
    chain.append(current)
    for _ in range(len(segments) + 1):
        candidates = [index for index in adjacency.get(key(current), [])
                      if index not in used]
        if not candidates:
            break
        index = candidates[0]
        used.add(index)
        start, end = segments[index]
        current = end if key(start) == key(current) else start
        chain.append(current)
        if key(current) == key(chain[0]) and len(used) == len(segments):
            break
    if len(used) < len(segments):
        unknown.add("pcb", file_name,
                    f"Edge.Cuts outline is not a single closed chain "
                    f"({len(used)}/{len(segments)} segments used)", "geometry")
    return tuple(chain)


def _parse_schematic(path: Path, unknown: _UnknownSink) -> ImportedSchematic:
    text = path.read_text(encoding="utf-8")
    root = parse(text)
    if head(root) != "kicad_sch":
        raise KicadImportError("UNSUPPORTED_FORMAT",
                               f"expected kicad_sch, found {head(root)!r}", path)
    values = [atom.value for atom in atoms(root)]
    version = _text(root, "version") or (values[0] if values else "")
    generator = _text(root, "generator") or (values[1] if len(values) > 1 else "")
    symbols: list[ImportedSymbol] = []
    for node in children_named(root, "symbol"):
        lib_id = _text(node, "lib_id")
        if not lib_id:
            continue
        at = _floats(node, "at")
        properties: dict[str, str] = {}
        for prop in children_named(node, "property"):
            words = _node_head_atoms(prop)
            if len(words) >= 2:
                properties[words[0]] = words[1]
        mirror = ""
        mirror_node = child(node, "mirror")
        if mirror_node is not None:
            mirror = "".join(atom.value for atom in atoms(mirror_node))
        pins = tuple(atom_value(pin) for pin in children_named(node, "pin"))
        symbols.append(ImportedSymbol(
            lib_id=lib_id, reference=properties.get("Reference", ""),
            value=properties.get("Value", ""),
            footprint=properties.get("Footprint", ""),
            x_nm=_nm(at[0]) if at else 0,
            y_nm=_nm(at[1]) if len(at) > 1 else 0,
            rotation_deg=at[2] if len(at) > 2 else 0.0,
            unit=_int(node, "unit", 1), mirror=mirror, pins=pins))
    wires: list[tuple[tuple[int, int], ...]] = []
    for node in children_named(root, "wire"):
        points = _points(child(node, "pts"))
        if len(points) >= 2:
            wires.append(points)
    labels: list[tuple[str, int, int, float]] = []
    for tag in ("label", "global_label", "hierarchical_label"):
        for node in children_named(root, tag):
            words = _node_head_atoms(node)
            at = _floats(node, "at")
            if words:
                labels.append((words[0],
                               _nm(at[0]) if at else 0,
                               _nm(at[1]) if len(at) > 1 else 0,
                               at[2] if len(at) > 2 else 0.0))
    junctions = tuple(
        (_nm(_floats(node, "at")[0]), _nm(_floats(node, "at")[1]))
        for node in children_named(root, "junction")
        if len(_floats(node, "at")) >= 2)
    no_connects = tuple(
        (_nm(_floats(node, "at")[0]), _nm(_floats(node, "at")[1]))
        for node in children_named(root, "no_connect")
        if len(_floats(node, "at")) >= 2)
    sheets: list[ImportedSheet] = []
    for node in children_named(root, "sheet"):
        at = _floats(node, "at")
        size = _floats(node, "size")
        properties: dict[str, str] = {}
        for prop in children_named(node, "property"):
            words = _node_head_atoms(prop)
            if len(words) >= 2:
                properties[words[0]] = words[1]
        sheets.append(ImportedSheet(
            name=properties.get("Sheetname", ""),
            file=properties.get("Sheetfile", ""),
            x_nm=_nm(at[0]) if at else 0,
            y_nm=_nm(at[1]) if len(at) > 1 else 0,
            width_nm=_nm(size[0]) if size else 0,
            height_nm=_nm(size[1]) if len(size) > 1 else 0))
    for tag in ("bus", "bus_entry"):
        count = len(children_named(root, tag))
        if count:
            unknown.add("schematic", path.name,
                        f"{tag} connectivity is not imported", "electrical", count)
    if not symbols and not wires:
        unknown.add("schematic", path.name, "no symbols or wires found",
                    "metadata")
    return ImportedSchematic(
        file=path.name, version=version, generator=generator,
        symbols=tuple(symbols), wires=tuple(wires), labels=tuple(labels),
        junctions=junctions, no_connects=no_connects, sheets=tuple(sheets))


def _parse_project_json(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise KicadImportError("PARSE_ERROR", f"project file is not JSON: {error}",
                               path) from error
    if not isinstance(data, dict):
        raise KicadImportError("PARSE_ERROR", "project root is not an object", path)
    return data


KNOWN_NETCLASS_KEYS = {
    "name", "clearance", "track_width", "via_diameter", "via_drill",
    "diff_pair_width", "diff_pair_gap",
}
EXPORTED_PROJECT_SECTIONS = {"meta", "board", "net_settings", "sheets",
                             "libraries"}


def _netclasses(project: dict[str, Any], unknown: _UnknownSink) -> tuple[tuple[ImportedNetclass, ...], dict[str, float]]:
    settings = project.get("net_settings") or {}
    classes = settings.get("classes") if isinstance(settings, dict) else None
    out: list[ImportedNetclass] = []
    if isinstance(classes, list):
        for entry in classes:
            if not isinstance(entry, dict):
                unknown.add("project", "net_settings.classes",
                            "non-object netclass entry", "metadata")
                continue
            for key in sorted(set(entry) - KNOWN_NETCLASS_KEYS):
                unknown.add("project", f"net_settings.classes.{key}",
                            "netclass field is not compiled into Mota rules",
                            "metadata")
            out.append(ImportedNetclass(
                name=str(entry.get("name", "")),
                clearance_nm=_mm_value(entry.get("clearance")),
                track_width_nm=_mm_value(entry.get("track_width")),
                via_diameter_nm=_mm_value(entry.get("via_diameter")),
                via_drill_nm=_mm_value(entry.get("via_drill")),
                diff_pair_width_nm=_mm_value(entry.get("diff_pair_width")),
                diff_pair_gap_nm=_mm_value(entry.get("diff_pair_gap"))))
    else:
        unknown.add("project", "net_settings.classes",
                    "no netclass table found", "metadata")
    design = project.get("board", {}).get("design_settings", {})
    rules = design.get("rules", {}) if isinstance(design, dict) else {}
    design_rules: dict[str, float] = {}
    if isinstance(rules, dict):
        for name, value in rules.items():
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                design_rules[str(name)] = float(value)
            elif isinstance(value, bool):
                design_rules[str(name)] = 1.0 if value else 0.0
            else:
                unknown.add("project", f"board.design_settings.rules.{name}",
                            "non-numeric rule value is not compiled", "metadata")
    else:
        unknown.add("project", "board.design_settings.rules",
                    "no design rule table found", "metadata")
    return tuple(out), design_rules


def _mm_value(value: Any) -> int:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return int(round(float(value) * NM_PER_MM))
    return 0


def import_project(path: str | Path) -> ImportedProject:
    """Import a KiCad project directory (or a .kicad_pro/.kicad_pcb file)."""

    given = Path(path)
    if given.is_file():
        directory = given.parent
        if given.suffix == ".kicad_pro":
            project_file = given
            _, pcb_file = _discover(directory)
        elif given.suffix == ".kicad_pcb":
            project_file, _ = _discover(directory)
            pcb_file = given
        else:
            raise KicadImportError("UNSUPPORTED_FORMAT",
                                   "expected .kicad_pro or .kicad_pcb", given)
    else:
        directory = given
        project_file, pcb_file = _discover(directory)
    if not directory.is_dir():
        raise KicadImportError("MISSING_DIRECTORY", "project directory not found",
                               directory)

    unknown = _UnknownSink()
    project_json = _parse_project_json(project_file)

    schematic_files = sorted(directory.glob("*.kicad_sch"))
    schematics = tuple(_parse_schematic(item, unknown)
                       for item in schematic_files)
    root_name = project_file.stem + ".kicad_sch"
    root_schematic = root_name if any(item.file == root_name
                                      for item in schematics) else (
        schematics[0].file if schematics else "")

    nets: dict[int, str] = {}
    parsed = _parse_pcb(pcb_file, nets, unknown, schematics)
    netclasses, design_rules = _netclasses(project_json, unknown)
    for section in sorted(set(project_json) - EXPORTED_PROJECT_SECTIONS):
        unknown.add("project", section,
                    "project section is not exported by Mota", "metadata")

    declared_sheets = {sheet.file for schematic in schematics
                       for sheet in schematic.sheets if sheet.file}
    for schematic in schematics:
        for sheet in schematic.sheets:
            if sheet.file and not (directory / sheet.file).exists():
                unknown.add("schematic", schematic.file,
                            f"sheet file {sheet.file} is missing", "electrical")
    if schematics and root_schematic:
        imported_names = {item.file for item in schematics}
        missing_sheets = declared_sheets - imported_names
        if missing_sheets:
            unknown.add("schematic", root_schematic,
                        "declared child sheets were not imported", "electrical",
                        len(missing_sheets))

    hashes: dict[str, str] = {}
    for item in [project_file, pcb_file, *schematic_files]:
        if item.exists():
            hashes[item.name] = hashlib.sha256(item.read_bytes()).hexdigest()

    counts = {
        "layers": len(parsed["layers"]),
        "copper_layers": len([layer for layer in parsed["layers"]
                              if layer.kind in {"signal", "power", "mixed"}]),
        "nets": len(parsed["nets"]),
        "footprints": len(parsed["footprints"]),
        "pads": sum(len(footprint.pads) for footprint in parsed["footprints"]),
        "tracks": len(parsed["tracks"]),
        "vias": len(parsed["vias"]),
        "zones": len(parsed["zones"]),
        "graphics": len(parsed["graphics"]),
        "schematic_files": len(schematics),
        "symbols": sum(len(item.symbols) for item in schematics),
        "wires": sum(len(item.wires) for item in schematics),
        "labels": sum(len(item.labels) for item in schematics),
    }

    return ImportedProject(
        source_dir=directory,
        project_file=project_file.name,
        pcb_file=pcb_file.name,
        pcb_version=parsed["version"],
        pcb_generator=parsed["generator"],
        thickness_nm=parsed["thickness_nm"],
        layers=parsed["layers"],
        nets=parsed["nets"],
        footprints=parsed["footprints"],
        tracks=parsed["tracks"],
        vias=parsed["vias"],
        zones=parsed["zones"],
        graphics=parsed["graphics"],
        outline=parsed["outline"],
        netclasses=netclasses,
        design_rules=design_rules,
        schematics=schematics,
        root_schematic=root_schematic,
        project=project_json,
        unknown=unknown.freeze(),
        counts=counts,
        file_hashes=hashes)


# --------------------------------------------------------------------------- #
# Comparison
# --------------------------------------------------------------------------- #

def _counter(values: Iterable[Any]) -> dict[Any, int]:
    out: dict[Any, int] = {}
    for value in values:
        out[value] = out.get(value, 0) + 1
    return out


def _diff(category: str, path: str, expected: Any, actual: Any,
          origin: str) -> Difference:
    return Difference(category=category, path=path, expected=str(expected),
                      actual=str(actual), origin=origin)


def compare_projects(source: ImportedProject,
                     exported: ImportedProject) -> RoundtripReport:
    """Compare semantics, geometry and rules between two imports."""

    report = RoundtripReport(status="PASS")
    report.checked = ["layers", "nets", "footprints", "pads", "tracks",
                      "vias", "zones", "graphics", "netclasses", "rules",
                      "schematic"]

    # Layers.
    source_layers = {(layer.number, layer.name, layer.kind)
                     for layer in source.layers}
    exported_layers = {(layer.number, layer.name, layer.kind)
                       for layer in exported.layers}
    if source_layers != exported_layers:
        report.differences.append(_diff(
            "semantic", "layers",
            sorted(source_layers - exported_layers),
            sorted(exported_layers - source_layers), "mota"))

    # Nets.
    if {(n.number, n.name) for n in source.nets} != {
            (n.number, n.name) for n in exported.nets}:
        report.differences.append(_diff(
            "semantic", "nets",
            sorted((n.number, n.name) for n in source.nets),
            sorted((n.number, n.name) for n in exported.nets), "mota"))

    # Footprints and pads.
    reference_counts = _counter(footprint.reference
                                for footprint in source.footprints)
    duplicates = {reference for reference, count in reference_counts.items()
                  if count > 1 and reference}
    if duplicates:
        # Duplicate references are ambiguous in the source; matching instances
        # by reference would blame the exporter for an input problem.
        report.differences.append(_diff(
            "semantic", "footprints.duplicate_references",
            sorted(duplicates), "ambiguous in source", "source"))
    for footprint in source.footprints:
        if footprint.reference in duplicates:
            continue
        other = exported.footprint(footprint.reference)
        path = f"footprint:{footprint.reference}"
        if other is None:
            report.differences.append(_diff(
                "semantic", path, "present", "missing", "mota"))
            continue
        for name, left, right in (
                ("footprint_id", footprint.footprint_id, other.footprint_id),
                ("layer", footprint.layer, other.layer),
                ("value", footprint.value, other.value),
                ("x_nm", footprint.x_nm, other.x_nm),
                ("y_nm", footprint.y_nm, other.y_nm),
                ("rotation_deg", footprint.rotation_deg,
                 other.rotation_deg)):
            if left != right:
                category = "semantic" if name in {"footprint_id", "layer"} \
                    else "geometry"
                report.differences.append(_diff(category, f"{path}.{name}",
                                                left, right, "mota"))
        if len(footprint.pads) != len(other.pads):
            report.differences.append(_diff(
                "geometry", f"{path}.pad_count", len(footprint.pads),
                len(other.pads), "mota"))
        left_pads = _counter(
            (pad.number, pad.net, pad.kind, pad.shape, pad.w_nm, pad.h_nm,
             pad.drill_nm, tuple(sorted(pad.layers)))
            for pad in footprint.pads)
        right_pads = _counter(
            (pad.number, pad.net, pad.kind, pad.shape, pad.w_nm, pad.h_nm,
             pad.drill_nm, tuple(sorted(pad.layers)))
            for pad in other.pads)
        if left_pads != right_pads:
            missing = {key: value for key, value in left_pads.items()
                       if right_pads.get(key, 0) < value}
            extra = {key: value for key, value in right_pads.items()
                     if left_pads.get(key, 0) < value}
            report.differences.append(_diff(
                "geometry", f"{path}.pads", missing, extra, "mota"))

    extra_footprints = {footprint.reference for footprint in exported.footprints} - \
        {footprint.reference for footprint in source.footprints}
    for reference in sorted(extra_footprints):
        report.differences.append(_diff(
            "semantic", f"footprint:{reference}", "absent", "present", "mota"))

    # Copper geometry.
    track_key = lambda track: (track.net, track.layer, track.width_nm,
                               track.start_nm, track.end_nm)
    left_tracks = _counter(track_key(track) for track in source.tracks)
    right_tracks = _counter(track_key(track) for track in exported.tracks)
    if left_tracks != right_tracks:
        report.differences.append(_diff(
            "geometry", "tracks",
            _counter_delta(left_tracks, right_tracks),
            _counter_delta(right_tracks, left_tracks), "mota"))

    via_key = lambda via: (via.net, via.x_nm, via.y_nm, via.size_nm,
                           via.drill_nm, tuple(via.layers))
    left_vias = _counter(via_key(via) for via in source.vias)
    right_vias = _counter(via_key(via) for via in exported.vias)
    if left_vias != right_vias:
        report.differences.append(_diff(
            "geometry", "vias",
            _counter_delta(left_vias, right_vias),
            _counter_delta(right_vias, left_vias), "mota"))

    zone_key = lambda zone: (zone.net, tuple(zone.layers), zone.outline,
                             zone.priority, zone.keepout)
    left_zones = _counter(zone_key(zone) for zone in source.zones)
    right_zones = _counter(zone_key(zone) for zone in exported.zones)
    if left_zones != right_zones:
        report.differences.append(_diff(
            "geometry", "zones",
            _counter_delta(left_zones, right_zones),
            _counter_delta(right_zones, left_zones), "mota"))

    graphic_key = lambda graphic: (graphic.kind, graphic.layer,
                                   graphic.points, graphic.width_nm)
    left_graphics = _counter(graphic_key(item) for item in source.graphics)
    right_graphics = _counter(graphic_key(item) for item in exported.graphics)
    if left_graphics != right_graphics:
        report.differences.append(_diff(
            "geometry", "graphics",
            _counter_delta(left_graphics, right_graphics),
            _counter_delta(right_graphics, left_graphics), "mota"))

    # Rules.
    left_classes = {item.name: item for item in source.netclasses}
    right_classes = {item.name: item for item in exported.netclasses}
    if set(left_classes) != set(right_classes):
        report.differences.append(_diff(
            "rule", "netclasses.names", sorted(left_classes),
            sorted(right_classes), "mota"))
    for name in sorted(set(left_classes) & set(right_classes)):
        if left_classes[name] != right_classes[name]:
            report.differences.append(_diff(
                "rule", f"netclass:{name}", left_classes[name],
                right_classes[name], "mota"))
    if source.design_rules != exported.design_rules:
        keys = set(source.design_rules) | set(exported.design_rules)
        mismatch = {key: (source.design_rules.get(key),
                          exported.design_rules.get(key))
                    for key in sorted(keys)
                    if source.design_rules.get(key) != exported.design_rules.get(key)}
        report.differences.append(_diff(
            "rule", "design_rules", mismatch, "see expected", "mota"))
    if source.thickness_nm != exported.thickness_nm:
        report.differences.append(_diff(
            "rule", "board.thickness_nm", source.thickness_nm,
            exported.thickness_nm, "mota"))

    # Schematic structure.
    left_schematics = {item.file: item for item in source.schematics}
    right_schematics = {item.file: item for item in exported.schematics}
    if set(left_schematics) != set(right_schematics):
        report.differences.append(_diff(
            "semantic", "schematic.files", sorted(left_schematics),
            sorted(right_schematics), "mota"))
    for name in sorted(set(left_schematics) & set(right_schematics)):
        left = left_schematics[name]
        right = right_schematics[name]
        left_symbols = _counter(
            (symbol.reference, symbol.lib_id, symbol.value, symbol.x_nm,
             symbol.y_nm, symbol.rotation_deg)
            for symbol in left.symbols)
        right_symbols = _counter(
            (symbol.reference, symbol.lib_id, symbol.value, symbol.x_nm,
             symbol.y_nm, symbol.rotation_deg)
            for symbol in right.symbols)
        if left_symbols != right_symbols:
            report.differences.append(_diff(
                "semantic", f"schematic:{name}.symbols",
                _counter_delta(left_symbols, right_symbols),
                _counter_delta(right_symbols, left_symbols), "mota"))
        if _counter(left.wires) != _counter(right.wires):
            report.differences.append(_diff(
                "geometry", f"schematic:{name}.wires", len(left.wires),
                len(right.wires), "mota"))
        if _counter(left.labels) != _counter(right.labels):
            report.differences.append(_diff(
                "semantic", f"schematic:{name}.labels",
                _counter(left.labels), _counter(right.labels), "mota"))
        if _counter(left.sheets) != _counter(right.sheets):
            report.differences.append(_diff(
                "semantic", f"schematic:{name}.sheets",
                _counter(left.sheets), _counter(right.sheets), "mota"))

    report.source_issues = list(source.unknown)
    report.status = ("FAIL" if report.mota_issues
                     else "PASS_WITH_SOURCE_ISSUES" if report.source_issues
                     else "PASS")
    return report


def _counter_delta(left: dict[Any, int], right: dict[Any, int]) -> dict[Any, int]:
    return {key: value - right.get(key, 0)
            for key, value in left.items() if value > right.get(key, 0)}


# --------------------------------------------------------------------------- #
# Convenience
# --------------------------------------------------------------------------- #

def export_project(source: ImportedProject, out_dir: str | Path, *,
                    name: str | None = None):
    """Write the imported project back as KiCad files."""

    from mota.io.kicad_writer import write_project

    return write_project(source, out_dir, name=name)


def export_and_compare(source: ImportedProject, out_dir: str | Path,
                       *, name: str | None = None) -> RoundtripReport:
    from mota.io.kicad_writer import write_project

    result = write_project(source, Path(out_dir), name=name)
    exported = import_project(Path(out_dir))
    report = compare_projects(source, exported)
    report.warnings = list(result.warnings)
    return report


def project_manifest(project: ImportedProject) -> dict[str, Any]:
    """Small, JSON-serialisable evidence bundle for a report."""

    return {
        "coordinate_system": project.coordinate_system,
        "source_dir": str(project.source_dir),
        "project_file": project.project_file,
        "pcb_file": project.pcb_file,
        "pcb_version": project.pcb_version,
        "pcb_generator": project.pcb_generator,
        "thickness_nm": project.thickness_nm,
        "counts": project.counts,
        "file_hashes": project.file_hashes,
        "layers": [
            {"number": layer.number, "name": layer.name, "kind": layer.kind,
             "user_name": layer.user_name}
            for layer in project.layers],
        "netclasses": [
            {"name": item.name, "clearance_nm": item.clearance_nm,
             "track_width_nm": item.track_width_nm,
             "via_diameter_nm": item.via_diameter_nm,
             "via_drill_nm": item.via_drill_nm,
             "diff_pair_width_nm": item.diff_pair_width_nm,
             "diff_pair_gap_nm": item.diff_pair_gap_nm}
            for item in project.netclasses],
        "design_rules": project.design_rules,
        "unknown": [
            {"area": item.area, "path": item.path, "detail": item.detail,
             "severity": item.severity, "count": item.count}
            for item in project.unknown],
    }
