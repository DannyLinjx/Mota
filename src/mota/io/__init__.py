"""Project import/export, round-trip comparison and manufacturing outputs."""

from mota.io.kicad_project import (
    COORDINATE_SYSTEM, Difference, ImportedFootprint, ImportedGraphic,
    ImportedLayer, ImportedNet, ImportedNetclass, ImportedPad, ImportedProject,
    ImportedSchematic, ImportedSheet, ImportedSymbol, ImportedTrack,
    ImportedVia, ImportedZone, KicadImportError, RoundtripReport, UnknownItem,
    compare_projects, export_and_compare, export_project, import_project,
    project_manifest,
)
from mota.io.kicad_writer import ExportResult, write_project
from mota.io.manufacturing import (
    BomRow, DrillRead, DrillTool, HoleRecord, LayerArtifact, LAYER_FILE_MAP,
    ManufacturingCheck, ManufacturingManifest, PositionRow, SlotRecord,
    build_manifest, pad_position, read_drill, write_bom, write_drill,
    write_package, write_positions,
)

__all__ = [
    "BomRow", "COORDINATE_SYSTEM", "Difference", "DrillRead", "DrillTool",
    "ExportResult", "HoleRecord", "ImportedFootprint", "ImportedGraphic",
    "ImportedLayer", "ImportedNet", "ImportedNetclass", "ImportedPad",
    "ImportedProject", "ImportedSchematic", "ImportedSheet", "ImportedSymbol",
    "ImportedTrack", "ImportedVia", "ImportedZone", "KicadImportError",
    "LAYER_FILE_MAP", "LayerArtifact", "ManufacturingCheck",
    "ManufacturingManifest", "PositionRow", "RoundtripReport", "SlotRecord",
    "UnknownItem", "build_manifest", "compare_projects", "export_and_compare",
    "export_project", "import_project", "pad_position", "project_manifest",
    "read_drill", "write_bom", "write_drill", "write_package",
    "write_positions", "write_project",
]
