"""Production output mapping, drill files, BOM/position consistency.

What is real here:

- layer-to-artifact mapping (Gerber file naming) with explicit unmapped-layer
  failures;
- Excellon drill files for plated and non-plated holes, written and then read
  back by an independent parser in this module;
- BOM and position CSVs plus consistency checks between them.

What is *not* claimed: Gerber artwork emission and any external KiCad/GerbView
comparison. Those checks stay ``NOT_CHECKED`` instead of pretending to pass.
"""

from __future__ import annotations

import csv
import hashlib
import json
import math
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Iterable

from mota.io.kicad_project import ImportedProject, ImportedFootprint, NM_PER_MM

# Logical copper/technical layer -> fabrication artifact stem. Mirrors the
# naming KiCad uses for its own Gerber output so a downstream CAM tool can
# replace Mota without renaming files.
LAYER_FILE_MAP: dict[str, str] = {
    "F.Cu": "F_Cu",
    "In1.Cu": "In1_Cu",
    "In2.Cu": "In2_Cu",
    "In3.Cu": "In3_Cu",
    "In4.Cu": "In4_Cu",
    "In5.Cu": "In5_Cu",
    "In6.Cu": "In6_Cu",
    "B.Cu": "B_Cu",
    "F.SilkS": "F_Silkscreen",
    "B.SilkS": "B_Silkscreen",
    "F.Mask": "F_Mask",
    "B.Mask": "B_Mask",
    "F.Paste": "F_Paste",
    "B.Paste": "B_Paste",
    "F.Fab": "F_Fab",
    "B.Fab": "B_Fab",
    "F.Adhes": "F_Adhesive",
    "B.Adhes": "B_Adhesive",
    "Edge.Cuts": "Edge_Cuts",
    "Margin": "Margin",
}

COPPER_KINDS = {"signal", "power", "mixed"}
NON_PRODUCTION_PREFIXES = ("Dwgs.User", "Cmts.User", "Eco1.User", "Eco2.User",
                           "User.1", "User.2", "User.3", "User.4", "User.5",
                           "User.6", "User.7", "User.8", "User.9")


@dataclass(frozen=True)
class LayerArtifact:
    layer: str
    file_stem: str
    kind: str
    present: bool
    generated: bool
    reason: str = ""


@dataclass(frozen=True)
class DrillTool:
    tool: int
    diameter_nm: int
    plated: bool
    count: int
    sources: tuple[str, ...]


@dataclass(frozen=True)
class HoleRecord:
    plated: bool
    diameter_nm: int
    x_nm: int
    y_nm: int


@dataclass(frozen=True)
class SlotRecord:
    """A routed slot: round tool of ``width_nm`` travelling between endpoints."""

    plated: bool
    width_nm: int
    x1_nm: int
    y1_nm: int
    x2_nm: int
    y2_nm: int
    length_nm: int = 0


@dataclass(frozen=True)
class BomRow:
    reference: str
    value: str
    footprint: str
    side: str
    in_bom: bool


@dataclass(frozen=True)
class PositionRow:
    reference: str
    x_nm: int
    y_nm: int
    rotation_deg: float
    side: str
    mirrored: bool


@dataclass(frozen=True)
class ManufacturingCheck:
    check_id: str
    status: str  # PASS | FAIL | NOT_CHECKED
    detail: str
    objects: int = 0


@dataclass
class ManufacturingManifest:
    status: str
    artifacts: list[LayerArtifact] = field(default_factory=list)
    tools: list[DrillTool] = field(default_factory=list)
    holes: list[HoleRecord] = field(default_factory=list)
    slots: list[SlotRecord] = field(default_factory=list)
    bom: list[BomRow] = field(default_factory=list)
    positions: list[PositionRow] = field(default_factory=list)
    checks: list[ManufacturingCheck] = field(default_factory=list)
    files: dict[str, str] = field(default_factory=dict)
    file_hashes: dict[str, str] = field(default_factory=dict)
    unknown: list[str] = field(default_factory=list)

    def check(self, check_id: str) -> ManufacturingCheck | None:
        for item in self.checks:
            if item.check_id == check_id:
                return item
        return None

    def to_json(self) -> dict:
        return {
            "status": self.status,
            "artifacts": [asdict(item) for item in self.artifacts],
            "tools": [asdict(item) for item in self.tools],
            "holes": [asdict(item) for item in self.holes],
            "slots": [asdict(item) for item in self.slots],
            "bom": [asdict(item) for item in self.bom],
            "positions": [asdict(item) for item in self.positions],
            "checks": [asdict(item) for item in self.checks],
            "files": self.files,
            "file_hashes": self.file_hashes,
            "unknown": self.unknown,
        }


def pad_position(footprint: ImportedFootprint, pad) -> tuple[int, int]:
    """Absolute pad centre in board nanometres.

    KiCad stores footprint rotation counter-clockwise in a y-down file, so the
    board position uses ``-rotation`` in the standard rotation matrix. A bottom
    footprint is mirrored on x before rotating. The convention was checked
    against the vendor drill file of the pinned CM4 IO project.
    """

    angle = math.radians(-footprint.rotation_deg)
    cosine = math.cos(angle)
    sine = math.sin(angle)
    local_x = pad.x_nm
    if footprint.side == "bottom":
        local_x = -local_x
    local_y = pad.y_nm
    x_nm = footprint.x_nm + int(round(local_x * cosine - local_y * sine))
    y_nm = footprint.y_nm + int(round(local_x * sine + local_y * cosine))
    return x_nm, y_nm


def _is_user_layer(layer: str) -> bool:
    if layer in NON_PRODUCTION_PREFIXES:
        return True
    return layer.endswith(".User") and layer not in LAYER_FILE_MAP


def _used_layers(project: ImportedProject) -> set[str]:
    used: set[str] = set()
    for track in project.tracks:
        used.add(track.layer)
    for zone in project.zones:
        if not zone.keepout:
            used.update(zone.layers)
    for footprint in project.footprints:
        for pad in footprint.pads:
            used.update(pad.layers)
    for graphic in project.graphics:
        used.add(graphic.layer)
    for via in project.vias:
        used.update(via.layers)
    return {layer for layer in used
            if layer and not layer.startswith("*")}


def _drill_records(project: ImportedProject) -> tuple[list[HoleRecord],
                                                      list[SlotRecord]]:
    """Round holes and routed slots, both with absolute board coordinates."""

    holes: list[HoleRecord] = []
    slots: list[SlotRecord] = []
    for via in project.vias:
        if via.drill_nm > 0:
            holes.append(HoleRecord(True, via.drill_nm, via.x_nm, via.y_nm))
    for footprint in project.footprints:
        for pad in footprint.pads:
            if pad.drill_nm <= 0:
                continue
            plated = pad.kind != "np_thru_hole"
            if pad.drill_oval and pad.drill_w_nm != pad.drill_h_nm:
                slots.append(_slot_record(footprint, pad, plated))
                continue
            x_nm, y_nm = pad_position(footprint, pad)
            holes.append(HoleRecord(plated, pad.drill_nm, x_nm, y_nm))
    return holes, slots


def _slot_record(footprint: ImportedFootprint, pad, plated: bool) -> SlotRecord:
    center_x, center_y = pad_position(footprint, pad)
    half = (pad.drill_w_nm - pad.drill_h_nm) // 2
    pad_angle = pad.rotation_deg if footprint.side == "top" \
        else -pad.rotation_deg
    angle = math.radians(-(footprint.rotation_deg + pad_angle))
    local_x = -half if footprint.side == "bottom" else half
    dx = int(round(local_x * math.cos(angle)))
    dy = int(round(local_x * math.sin(angle)))
    return SlotRecord(plated=plated, width_nm=pad.drill_h_nm,
                      x1_nm=center_x + dx, y1_nm=center_y + dy,
                      x2_nm=center_x - dx, y2_nm=center_y - dy,
                      length_nm=2 * int(round(math.hypot(dx, dy))))


def _tools(holes: Iterable[HoleRecord], slots: Iterable[SlotRecord],
           project: ImportedProject) -> list[DrillTool]:
    grouped: dict[tuple[int, bool], int] = {}
    for hole in holes:
        key = (hole.diameter_nm, hole.plated)
        grouped[key] = grouped.get(key, 0) + 1
    for slot in slots:
        key = (slot.width_nm, slot.plated)
        grouped[key] = grouped.get(key, 0) + 1
    via_sizes = {via.drill_nm for via in project.vias if via.drill_nm}
    pad_sizes = {pad.drill_nm for footprint in project.footprints
                 for pad in footprint.pads if pad.drill_nm}
    slot_sizes = {slot.width_nm for slot in slots}
    tools: list[DrillTool] = []
    index = 0
    for (diameter, plated), count in sorted(grouped.items(),
                                            key=lambda item: (item[0][1],
                                                              item[0][0])):
        index += 1
        sources: list[str] = []
        if diameter in via_sizes:
            sources.append("via")
        if diameter in pad_sizes:
            sources.append("pad")
        if diameter in slot_sizes:
            sources.append("slot")
        tools.append(DrillTool(tool=index, diameter_nm=diameter,
                               plated=plated, count=count,
                               sources=tuple(sources)))
    return tools


def _bom_rows(project: ImportedProject) -> list[BomRow]:
    rows: list[BomRow] = []
    for footprint in sorted(project.footprints, key=lambda item: item.reference):
        attributes = set(footprint.attributes)
        in_bom = not ({"dnp", "exclude_from_bom"} & attributes)
        rows.append(BomRow(reference=footprint.reference,
                           value=footprint.value,
                           footprint=footprint.footprint_id,
                           side=footprint.side, in_bom=in_bom))
    return rows


def _position_rows(project: ImportedProject) -> list[PositionRow]:
    return [PositionRow(reference=footprint.reference,
                        x_nm=footprint.x_nm, y_nm=footprint.y_nm,
                        rotation_deg=footprint.rotation_deg,
                        side=footprint.side,
                        mirrored=footprint.side == "bottom")
            for footprint in sorted(project.footprints,
                                    key=lambda item: item.reference)]


def build_manifest(project: ImportedProject) -> ManufacturingManifest:
    manifest = ManufacturingManifest(status="PASS")
    used = _used_layers(project)
    copper = set(project.copper_layers())

    for layer in sorted(used):
        if _is_user_layer(layer):
            continue
        stem = LAYER_FILE_MAP.get(layer)
        if stem is None and layer in copper:
            manifest.checks.append(ManufacturingCheck(
                "LAYER_MAP", "FAIL",
                f"copper layer {layer} has no Gerber artifact mapping"))
            continue
        if stem is None:
            manifest.checks.append(ManufacturingCheck(
                "LAYER_MAP", "FAIL",
                f"layer {layer} carries production graphics but has no mapping"))
            continue
        manifest.artifacts.append(LayerArtifact(
            layer=layer, file_stem=stem,
            kind="copper" if layer in copper else "technical",
            present=True, generated=False,
            reason="Gerber artwork is not generated by Mota"))
    if not any(item.check_id == "LAYER_MAP" for item in manifest.checks):
        if not used:
            manifest.checks.append(ManufacturingCheck(
                "LAYER_MAP", "FAIL", "project uses no layers"))
        else:
            manifest.checks.append(ManufacturingCheck(
                "LAYER_MAP", "PASS",
                f"{len(manifest.artifacts)} production layers mapped",
                len(manifest.artifacts)))

    if "Edge.Cuts" not in used:
        manifest.checks.append(ManufacturingCheck(
            "BOARD_OUTLINE", "FAIL", "no Edge.Cuts geometry on the board"))
    elif len(project.outline) < 4:
        manifest.checks.append(ManufacturingCheck(
            "BOARD_OUTLINE", "FAIL",
            f"outline chain has {len(project.outline)} points"))
    else:
        manifest.checks.append(ManufacturingCheck(
            "BOARD_OUTLINE", "PASS",
            f"closed outline with {len(project.outline)} points",
            len(project.outline)))

    holes, slots = _drill_records(project)
    manifest.holes = holes
    manifest.slots = slots
    manifest.tools = _tools(holes, slots, project)
    drilled = sum(tool.count for tool in manifest.tools)
    plated = sum(tool.count for tool in manifest.tools if tool.plated)
    non_plated = drilled - plated
    if drilled != len(holes) + len(slots):
        manifest.checks.append(ManufacturingCheck(
            "DRILL_CONSISTENCY", "FAIL",
            f"{len(holes) + len(slots)} features but tools cover {drilled}"))
    else:
        manifest.checks.append(ManufacturingCheck(
            "DRILL_CONSISTENCY", "PASS",
            f"{len(holes)} round holes + {len(slots)} slots: {plated} plated, "
            f"{non_plated} non-plated; {len(manifest.tools)} tools",
            len(holes) + len(slots)))
    if slots:
        bad = [slot for slot in slots
               if abs(math.hypot(slot.x2_nm - slot.x1_nm,
                                 slot.y2_nm - slot.y1_nm)
                      - slot.length_nm) > 2]
        manifest.checks.append(ManufacturingCheck(
            "SLOT_GEOMETRY", "PASS" if not bad else "FAIL",
            f"{len(slots)} routed slots: endpoints match the drill length",
            len(slots)))
        manifest.checks.append(ManufacturingCheck(
            "SLOT_VALIDATION", "NOT_CHECKED",
            "slot orientation uses the pad transform verified on round holes; "
            "the pinned vendor drill contains no slots to compare against"))
    else:
        manifest.checks.append(ManufacturingCheck(
            "SLOT_GEOMETRY", "PASS", "no routed slots in this project"))

    manifest.bom = _bom_rows(project)
    manifest.positions = _position_rows(project)
    bom_refs = {row.reference for row in manifest.bom if row.in_bom}
    position_refs = {row.reference for row in manifest.positions}
    if not manifest.bom:
        manifest.checks.append(ManufacturingCheck(
            "BOM_POSITION_REFERENCES", "FAIL", "BOM is empty"))
    elif bom_refs != position_refs:
        missing = sorted(bom_refs - position_refs)
        extra = sorted(position_refs - bom_refs)
        manifest.checks.append(ManufacturingCheck(
            "BOM_POSITION_REFERENCES", "FAIL",
            f"BOM-only: {missing}; position-only: {extra}"))
    else:
        manifest.checks.append(ManufacturingCheck(
            "BOM_POSITION_REFERENCES", "PASS",
            f"{len(bom_refs)} references present in both", len(bom_refs)))

    duplicates = sorted({row.reference for row in manifest.bom
                         if sum(1 for item in manifest.bom
                                if item.reference == row.reference) > 1})
    if duplicates:
        manifest.checks.append(ManufacturingCheck(
            "BOM_UNIQUE_REFERENCES", "FAIL",
            f"duplicate references: {duplicates}", len(duplicates)))
    else:
        manifest.checks.append(ManufacturingCheck(
            "BOM_UNIQUE_REFERENCES", "PASS", "all references unique",
            len(manifest.bom)))

    incomplete = [row.reference for row in manifest.bom
                  if row.in_bom and (not row.value or not row.footprint)]
    if incomplete:
        manifest.checks.append(ManufacturingCheck(
            "BOM_COMPLETENESS", "FAIL",
            f"missing value or footprint: {incomplete[:10]}"
            + (" ..." if len(incomplete) > 10 else ""), len(incomplete)))
    else:
        manifest.checks.append(ManufacturingCheck(
            "BOM_COMPLETENESS", "PASS", "every BOM row has value and footprint",
            len(manifest.bom)))

    if any(row.mirrored for row in manifest.positions):
        manifest.checks.append(ManufacturingCheck(
            "POSITION_CONVENTION", "NOT_CHECKED",
            "bottom-side rotation is exported in KiCad convention; a specific "
            "assembler convention is not applied"))

    manifest.checks.append(ManufacturingCheck(
        "GERBER_ARTWORK", "NOT_CHECKED",
        "Mota does not emit Gerber artwork; artifacts are named but not written"))
    manifest.checks.append(ManufacturingCheck(
        "EXTERNAL_VALIDATION", "NOT_CHECKED",
        "no kicad-cli or independent Gerber tool was run in this environment"))

    manifest.unknown = [f"{item.area}:{item.path} {item.detail} "
                        f"(x{item.count})" for item in project.unknown]
    manifest.status = ("FAIL" if any(item.status == "FAIL"
                                     for item in manifest.checks)
                       else "NOT_CHECKED"
                       if any(item.status == "NOT_CHECKED"
                              for item in manifest.checks)
                       else "PASS")
    return manifest


# --------------------------------------------------------------------------- #
# Drill files
# --------------------------------------------------------------------------- #

def write_drill(project: ImportedProject, directory: Path, *,
                stem: str | None = None) -> dict[str, Path]:
    """Write plated and non-plated Excellon files; coordinates in mm."""

    directory.mkdir(parents=True, exist_ok=True)
    base = stem or Path(project.pcb_file).stem
    holes, slots = _drill_records(project)
    written: dict[str, Path] = {}
    for plated, suffix in ((True, "PTH"), (False, "NPTH")):
        subset_holes = [hole for hole in holes if hole.plated == plated]
        subset_slots = [slot for slot in slots if slot.plated == plated]
        if not subset_holes and not subset_slots:
            continue
        path = directory / f"{base}-{suffix}.drl"
        path.write_text(_drill_text(subset_holes, subset_slots),
                        encoding="utf-8")
        written[suffix] = path
    return written


def _drill_text(holes: list[HoleRecord], slots: list[SlotRecord]) -> str:
    diameters = sorted({hole.diameter_nm for hole in holes}
                       | {slot.width_nm for slot in slots})
    tool_of = {diameter: index + 1 for index, diameter in enumerate(diameters)}
    lines = ["M48", "; Mota drill file", "FMAT,2", "METRIC"]
    for diameter in diameters:
        lines.append(f"T{tool_of[diameter]}C{diameter / NM_PER_MM:.4f}")
    lines += ["%", "G90", "G05"]
    by_tool: dict[int, list[str]] = {}
    for hole in holes:
        tool = tool_of[hole.diameter_nm]
        by_tool.setdefault(tool, []).append(
            f"X{hole.x_nm / NM_PER_MM:.4f}Y{hole.y_nm / NM_PER_MM:.4f}")
    for slot in slots:
        tool = tool_of[slot.width_nm]
        by_tool.setdefault(tool, []).append(
            f"X{slot.x1_nm / NM_PER_MM:.4f}Y{slot.y1_nm / NM_PER_MM:.4f}"
            f"G85X{slot.x2_nm / NM_PER_MM:.4f}Y{slot.y2_nm / NM_PER_MM:.4f}")
    for tool in sorted(by_tool):
        lines.append(f"T{tool}")
        lines.extend(by_tool[tool])
    lines += ["T0", "M30"]
    return "\n".join(lines) + "\n"


@dataclass(frozen=True)
class DrillRead:
    holes: list[tuple[float, float, float]]      # x_mm, y_mm, diameter_mm
    slots: list[tuple[float, float, float, float, float]]  # x1,y1,x2,y2,width


def _coordinates(text: str) -> tuple[float, float] | None:
    if "X" not in text or "Y" not in text:
        return None
    x_text, _, y_text = text.partition("Y")
    try:
        return float(x_text.lstrip("X")), float(y_text)
    except ValueError:
        return None


def read_drill(path: str | Path) -> DrillRead:
    """Independent Excellon reader used to verify the files we just wrote."""

    text = Path(path).read_text(encoding="utf-8")
    if not text.startswith("M48"):
        raise ValueError(f"{path}: not an Excellon file")
    tools: dict[str, float] = {}
    current = ""
    holes: list[tuple[float, float, float]] = []
    slots: list[tuple[float, float, float, float, float]] = []
    in_header = True
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith(";"):
            continue
        if stripped == "%":
            in_header = False
            continue
        if in_header:
            if stripped.startswith("T") and "C" in stripped:
                name, _, diameter = stripped.partition("C")
                tools[name] = float(diameter)
            continue
        if stripped.startswith("T"):
            current = stripped
            continue
        diameter = tools.get(current, 0.0)
        if "G85" in stripped:
            start_text, _, end_text = stripped.partition("G85")
            start = _coordinates(start_text)
            end = _coordinates(end_text)
            if start and end:
                slots.append((start[0], start[1], end[0], end[1], diameter))
            continue
        point = _coordinates(stripped)
        if point:
            holes.append((point[0], point[1], diameter))
    return DrillRead(holes=holes, slots=slots)


# --------------------------------------------------------------------------- #
# BOM / position / package
# --------------------------------------------------------------------------- #

def write_bom(manifest: ManufacturingManifest, path: Path) -> None:
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(["Reference", "Value", "Footprint", "Side", "InBOM"])
        for row in manifest.bom:
            writer.writerow([row.reference, row.value, row.footprint, row.side,
                             "yes" if row.in_bom else "no"])


def write_positions(manifest: ManufacturingManifest, path: Path) -> None:
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(["Reference", "Side", "X(mm)", "Y(mm)",
                         "Rotation(deg)", "Mirrored"])
        for row in manifest.positions:
            writer.writerow([row.reference, row.side,
                             f"{row.x_nm / NM_PER_MM:.4f}",
                             f"{row.y_nm / NM_PER_MM:.4f}",
                             f"{row.rotation_deg:.4f}",
                             "yes" if row.mirrored else "no"])


def write_package(project: ImportedProject, directory: str | Path, *,
                  stem: str | None = None) -> ManufacturingManifest:
    """Write drill, BOM, position files and the manifest; verify the drill file."""

    out = Path(directory)
    out.mkdir(parents=True, exist_ok=True)
    base = stem or Path(project.pcb_file).stem
    manifest = build_manifest(project)
    drills = write_drill(project, out, stem=base)
    for suffix, path in drills.items():
        manifest.files[f"drill_{suffix}"] = str(path)
        manifest.file_hashes[path.name] = hashlib.sha256(
            path.read_bytes()).hexdigest()

    bom_path = out / f"{base}-bom.csv"
    write_bom(manifest, bom_path)
    position_path = out / f"{base}-positions.csv"
    write_positions(manifest, position_path)
    for path in (bom_path, position_path):
        manifest.files[path.stem] = str(path)
        manifest.file_hashes[path.name] = hashlib.sha256(
            path.read_bytes()).hexdigest()

    # Independent re-read of the drill files.
    expected_plated = sum(1 for hole in manifest.holes if hole.plated) + \
        sum(1 for slot in manifest.slots if slot.plated)
    expected_non_plated = (len(manifest.holes) + len(manifest.slots)
                           - expected_plated)
    read_holes = 0
    read_slots = 0
    for path in drills.values():
        parsed = read_drill(path)
        read_holes += len(parsed.holes)
        read_slots += len(parsed.slots)
    read_total = read_holes + read_slots
    if read_total != len(manifest.holes) + len(manifest.slots):
        manifest.checks.append(ManufacturingCheck(
            "DRILL_ROUNDTRIP", "FAIL",
            f"drill files hold {read_holes} holes + {read_slots} slots; "
            f"expected {len(manifest.holes)} + {len(manifest.slots)}"))
    else:
        manifest.checks.append(ManufacturingCheck(
            "DRILL_ROUNDTRIP", "PASS",
            f"independent reader found {read_holes} holes and {read_slots} "
            f"slots ({expected_plated} plated, {expected_non_plated} "
            f"non-plated)", read_total))

    manifest.status = ("FAIL" if any(item.status == "FAIL"
                                     for item in manifest.checks)
                       else "NOT_CHECKED"
                       if any(item.status == "NOT_CHECKED"
                              for item in manifest.checks)
                       else "PASS")
    manifest_path = out / f"{base}-manufacturing.json"
    manifest_path.write_text(json.dumps(manifest.to_json(), indent=2,
                                        sort_keys=True) + "\n",
                             encoding="utf-8")
    manifest.files["manifest"] = str(manifest_path)
    manifest.file_hashes[manifest_path.name] = hashlib.sha256(
        manifest_path.read_bytes()).hexdigest()
    return manifest
