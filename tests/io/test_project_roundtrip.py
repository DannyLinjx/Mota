from __future__ import annotations

import hashlib
import os
import shutil
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
from kicad_fixture import write_minimal_project  # noqa: E402

from mota.io.kicad_project import (  # noqa: E402
    COORDINATE_SYSTEM, KicadImportError, export_and_compare, import_project,
    project_manifest,
)

MM = 1_000_000
CM4_ZIP_SHA256 = "5ea867e17968cb9c117fbce7a982ea395d66a6ee605253a9a0879483bbfcb0aa"
CM4_ZIP = Path(os.environ.get("MOTA_CM4IO_ZIP", "/tmp/mota-cm4io/CM4IO-KiCAD.zip"))
CM4_DIR = Path(os.environ.get("MOTA_CM4IO_DIR", "/tmp/mota-cm4io/design"))


@pytest.fixture()
def project_dir(tmp_path: Path) -> Path:
    return write_minimal_project(tmp_path / "project")


def _copy_project(source: Path, target: Path) -> Path:
    shutil.copytree(source, target)
    return target


def test_import_reads_board_semantics(project_dir: Path):
    project = import_project(project_dir)

    assert project.coordinate_system == COORDINATE_SYSTEM
    assert project.counts["copper_layers"] == 2
    assert project.counts["footprints"] == 2
    assert project.counts["pads"] == 6
    assert project.counts["tracks"] == 2
    assert project.counts["vias"] == 1
    assert project.counts["zones"] == 1
    assert project.counts["schematic_files"] == 2
    assert project.thickness_nm == 1_600_000
    assert project.pcb_version == "20211014"

    assert {net.name for net in project.nets} == {"", "GND", "SIG"}
    assert [footprint.reference for footprint in project.footprints] == ["J1", "R1"]
    conn = project.footprint("J1")
    assert conn is not None and conn.layer == "F.Cu"
    assert (conn.x_nm, conn.y_nm) == (10 * MM, 10 * MM)
    assert conn.rotation_deg == 90
    assert [pad.number for pad in conn.pads] == ["1", "2", "3", "4"]
    assert conn.pads[1].drill_oval is True
    assert conn.pads[1].drill_w_nm == 1_200_000
    assert conn.pads[1].drill_h_nm == 600_000
    res = project.footprint("R1")
    assert res is not None and res.side == "bottom" and res.layer == "B.Cu"

    netclass = {item.name: item for item in project.netclasses}
    assert netclass["POWER"].clearance_nm == 300_000
    assert netclass["POWER"].track_width_nm == 500_000
    assert project.design_rules["min_clearance"] == pytest.approx(0.2)
    assert project.design_rules["allow_microvias"] == 0.0

    assert len(project.outline) == 5
    assert project.outline[0] == project.outline[-1] == (0, 0)
    assert project.file_hashes["c12.kicad_pcb"] == hashlib.sha256(
        (project_dir / "c12.kicad_pcb").read_bytes()).hexdigest()

    manifest = project_manifest(project)
    assert manifest["counts"]["vias"] == 1
    assert manifest["netclasses"][1]["name"] == "POWER"


def test_unknown_electrical_content_is_reported(project_dir: Path):
    project = import_project(project_dir)
    items = {(item.area, item.path, item.detail) for item in project.unknown}

    assert ("pcb", "c12.kicad_pcb",
            "zone fill copper geometry is not imported (outline only)") in items
    assert ("pcb", "pad",
            "custom pad copper primitives are not imported") in items
    assert ("pcb", "c12.kicad_pcb", "dimension is not imported") in items
    assert ("project", "net_settings.classes.bus_width",
            "netclass field is not compiled into Mota rules") in items
    assert ("project", "pcbnew",
            "project section is not exported by Mota") in items
    assert any(item.severity == "electrical" for item in project.unknown)
    assert any(item.severity == "geometry" for item in project.unknown)
    assert any(item.severity == "metadata" for item in project.unknown)


def test_roundtrip_preserves_semantics_geometry_and_rules(
        project_dir: Path, tmp_path: Path):
    project = import_project(project_dir)
    report = export_and_compare(project, tmp_path / "export")

    assert report.status == "PASS_WITH_SOURCE_ISSUES"
    assert report.differences == []
    assert report.checked
    assert report.source_issues
    assert report.warnings
    assert any("lib_symbols" in warning for warning in report.warnings)

    exported = import_project(tmp_path / "export")
    assert exported.counts["footprints"] == project.counts["footprints"]
    assert exported.counts["tracks"] == project.counts["tracks"]
    assert exported.counts["vias"] == project.counts["vias"]
    assert exported.counts["zones"] == project.counts["zones"]
    assert exported.counts["symbols"] == project.counts["symbols"]
    assert exported.counts["wires"] == project.counts["wires"]
    assert exported.thickness_nm == project.thickness_nm
    assert {item.name for item in exported.netclasses} == {
        item.name for item in project.netclasses}


def test_export_is_deterministic(project_dir: Path, tmp_path: Path):
    project = import_project(project_dir)
    first = export_and_compare(project, tmp_path / "one")
    second = export_and_compare(project, tmp_path / "two")
    assert first.differences == []
    assert second.differences == []
    for name in ("c12.kicad_pcb", "c12.kicad_sch", "c12.kicad_pro"):
        left = hashlib.sha256((tmp_path / "one" / name).read_bytes()).hexdigest()
        right = hashlib.sha256((tmp_path / "two" / name).read_bytes()).hexdigest()
        assert left == right, name


def test_duplicate_reference_is_a_source_issue(project_dir: Path):
    text = (project_dir / "c12.kicad_pcb").read_text(encoding="utf-8")
    text = text.replace('(fp_text reference "J1"', '(fp_text reference "R1"', 1)
    (project_dir / "c12.kicad_pcb").write_text(text, encoding="utf-8")

    project = import_project(project_dir)
    assert any("duplicate reference" in item.detail for item in project.unknown)
    report = export_and_compare(project, project_dir.parent / "export")
    assert report.status == "PASS_WITH_SOURCE_ISSUES"
    assert report.mota_issues == []
    assert any(item.path == "footprints.duplicate_references"
               and item.origin == "source" for item in report.differences)


def test_missing_project_file_raises(tmp_path: Path):
    with pytest.raises(KicadImportError) as error:
        import_project(tmp_path)
    assert error.value.code == "MISSING_PROJECT_FILE"


def test_unsupported_root_raises(project_dir: Path):
    (project_dir / "c12.kicad_pcb").write_text("(not_a_board (version 1))",
                                                encoding="utf-8")
    with pytest.raises(KicadImportError) as error:
        import_project(project_dir)
    assert error.value.code == "UNSUPPORTED_FORMAT"


def test_missing_sheet_file_is_reported(project_dir: Path):
    (project_dir / "child.kicad_sch").unlink()
    project = import_project(project_dir)
    assert any("sheet file child.kicad_sch is missing" in item.detail
               for item in project.unknown)


def test_single_project_file_entry_point(project_dir: Path):
    project = import_project(project_dir / "c12.kicad_pro")
    assert project.counts["footprints"] == 2


@pytest.mark.skipif(not (CM4_ZIP.exists() and CM4_DIR.exists()),
                    reason="CM4 IO design package is not cached locally")
def test_cm4io_pinned_package_roundtrip(tmp_path: Path):
    digest = hashlib.sha256(CM4_ZIP.read_bytes()).hexdigest()
    assert digest == CM4_ZIP_SHA256, "cached CM4 IO zip does not match the pin"

    project = import_project(CM4_DIR)
    assert project.counts["copper_layers"] == 4
    assert project.counts["footprints"] == 151
    assert project.counts["pads"] == 944
    assert project.counts["tracks"] == 5655
    assert project.counts["vias"] == 781
    assert project.counts["zones"] == 27
    assert project.counts["schematic_files"] == 7
    assert project.counts["symbols"] == 225
    assert project.thickness_nm == 1_600_000
    assert len(project.outline) >= 4
    assert {item.name for item in project.netclasses} == {
        "Default", "100R", "50R", "75R", "90R", "HDMI", "POE TAPS"}

    report = export_and_compare(project, tmp_path / "cm4", name="CM4IOv5")
    assert report.status == "PASS_WITH_SOURCE_ISSUES"
    assert report.mota_issues == []
    assert not report.differences
    assert any("zone fill" in item.detail for item in report.source_issues)
    assert any("custom pad" in item.detail for item in report.source_issues)
