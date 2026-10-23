from __future__ import annotations

import hashlib
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
from kicad_fixture import write_minimal_project  # noqa: E402

from mota.io.kicad_project import import_project  # noqa: E402
from mota.io.manufacturing import (  # noqa: E402
    LAYER_FILE_MAP, _drill_records, build_manifest, pad_position, read_drill,
    write_drill, write_package,
)

MM = 1_000_000
CM4_DIR = Path(os.environ.get("MOTA_CM4IO_DIR", "/tmp/mota-cm4io/design"))


@pytest.fixture()
def project(tmp_path: Path):
    return import_project(write_minimal_project(tmp_path / "project"))


def test_layer_mapping_covers_used_production_layers(project):
    manifest = build_manifest(project)
    mapped = {item.layer: item.file_stem for item in manifest.artifacts}

    assert mapped["F.Cu"] == "F_Cu"
    assert mapped["B.Cu"] == "B_Cu"
    assert mapped["Edge.Cuts"] == "Edge_Cuts"
    assert mapped["F.SilkS"] == "F_Silkscreen"
    assert manifest.check("LAYER_MAP").status == "PASS"
    assert manifest.check("BOARD_OUTLINE").status == "PASS"
    # Nothing is claimed to have been generated as artwork.
    assert all(item.generated is False for item in manifest.artifacts)
    assert manifest.check("GERBER_ARTWORK").status == "NOT_CHECKED"


def test_unmapped_copper_layer_fails(tmp_path: Path):
    directory = write_minimal_project(tmp_path / "project")
    text = (directory / "c12.kicad_pcb").read_text(encoding="utf-8")
    text = text.replace(
        '(31 "B.Cu" signal)',
        '(31 "B.Cu" signal)\n    (4 "Mid.Cu" signal)')
    text = text.replace(
        '(segment (start 20 15) (end 20 10) (width 0.25) (layer "B.Cu") (net 2))',
        '(segment (start 20 15) (end 20 10) (width 0.25) (layer "Mid.Cu") '
        '(net 2))')
    (directory / "c12.kicad_pcb").write_text(text, encoding="utf-8")

    manifest = build_manifest(import_project(directory))
    assert manifest.check("LAYER_MAP").status == "FAIL"
    assert "Mid.Cu" in manifest.check("LAYER_MAP").detail
    assert manifest.status == "FAIL"


def test_pad_position_uses_footprint_rotation(project):
    conn = project.footprint("J1")
    assert conn is not None
    # Footprint at (10,10) rotated 90 degrees: local (2.54, 0) becomes (10, 7.46).
    x_nm, y_nm = pad_position(conn, conn.pads[1])
    assert (x_nm // 1000, y_nm // 1000) == (10_000, 7_460)


def test_drill_files_roundtrip_and_keep_slots(project, tmp_path: Path):
    holes, slots = _drill_records(project)
    assert len(holes) == 2
    assert len(slots) == 1

    written = write_drill(project, tmp_path)
    parsed = {suffix: read_drill(path) for suffix, path in written.items()}
    assert sum(len(item.holes) for item in parsed.values()) == len(holes)
    assert sum(len(item.slots) for item in parsed.values()) == len(slots)

    slot = parsed["PTH"].slots[0]
    assert slot[4] == pytest.approx(0.6)
    assert abs(slot[0] - 9.7) < 0.01 and abs(slot[2] - 10.3) < 0.01
    assert all(hole[2] > 0 for hole in parsed["PTH"].holes)


def test_package_checks_bom_and_positions(project, tmp_path: Path):
    manifest = write_package(project, tmp_path / "package")

    assert manifest.check("DRILL_ROUNDTRIP").status == "PASS"
    assert manifest.check("BOM_POSITION_REFERENCES").status == "PASS"
    assert manifest.check("BOM_UNIQUE_REFERENCES").status == "PASS"
    assert manifest.check("BOM_COMPLETENESS").status == "PASS"
    assert manifest.check("SLOT_GEOMETRY").status == "PASS"
    assert manifest.check("SLOT_VALIDATION").status == "NOT_CHECKED"
    assert manifest.check("POSITION_CONVENTION").status == "NOT_CHECKED"
    assert manifest.check("EXTERNAL_VALIDATION").status == "NOT_CHECKED"
    assert manifest.status == "NOT_CHECKED"
    assert [row.reference for row in manifest.bom] == ["J1", "R1"]
    assert [row.reference for row in manifest.positions] == ["J1", "R1"]
    bottom = next(row for row in manifest.positions if row.reference == "R1")
    assert bottom.side == "bottom" and bottom.mirrored is True

    files = {name: Path(path) for name, path in manifest.files.items()}
    for path in files.values():
        assert path.exists()
        assert hashlib.sha256(path.read_bytes()).hexdigest() == \
            manifest.file_hashes[path.name]


def test_bom_flags_missing_value_and_duplicate_reference(tmp_path: Path):
    directory = write_minimal_project(tmp_path / "project")
    text = (directory / "c12.kicad_pcb").read_text(encoding="utf-8")
    text = text.replace('(fp_text value "10k"', '(fp_text value ""')
    text = text.replace('(fp_text reference "R1"', '(fp_text reference "J1"')
    (directory / "c12.kicad_pcb").write_text(text, encoding="utf-8")

    manifest = build_manifest(import_project(directory))
    assert manifest.check("BOM_UNIQUE_REFERENCES").status == "FAIL"
    assert manifest.check("BOM_COMPLETENESS").status == "FAIL"
    assert manifest.status == "FAIL"


def test_missing_outline_is_a_failure(tmp_path: Path):
    directory = write_minimal_project(tmp_path / "project")
    text = (directory / "c12.kicad_pcb").read_text(encoding="utf-8")
    text = "\n".join(line for line in text.splitlines()
                     if "Edge.Cuts" not in line)
    (directory / "c12.kicad_pcb").write_text(text, encoding="utf-8")

    manifest = build_manifest(import_project(directory))
    assert manifest.check("BOARD_OUTLINE").status == "FAIL"


@pytest.mark.skipif(not CM4_DIR.exists(), reason="CM4 IO design is not cached")
def test_cm4io_manufacturing_manifest(tmp_path: Path):
    project = import_project(CM4_DIR)
    manifest = build_manifest(project)

    assert manifest.check("LAYER_MAP").status == "PASS"
    mapped = {item.layer for item in manifest.artifacts}
    assert {"F.Cu", "In1.Cu", "In2.Cu", "B.Cu", "Edge.Cuts"} <= mapped
    assert LAYER_FILE_MAP["In1.Cu"] == "In1_Cu"
    assert manifest.check("BOARD_OUTLINE").status == "PASS"
    assert manifest.check("DRILL_CONSISTENCY").status == "PASS"
    assert manifest.check("SLOT_GEOMETRY").status == "PASS"
    assert manifest.check("BOM_POSITION_REFERENCES").status == "PASS"
    assert manifest.check("BOM_UNIQUE_REFERENCES").status == "PASS"
    assert len(manifest.bom) == 151
    assert len(manifest.positions) == 151
    assert len(manifest.holes) == 968
    assert len(manifest.slots) == 15
    # Nothing is silently upgraded to PASS.
    assert manifest.status == "NOT_CHECKED"

    package = write_package(project, tmp_path / "cm4", stem="CM4IOv5")
    assert package.check("DRILL_ROUNDTRIP").status == "PASS"
    pth = read_drill(Path(package.files["drill_PTH"]))
    npth = read_drill(Path(package.files["drill_NPTH"]))
    assert len(pth.holes) + len(npth.holes) == 968
    assert len(pth.slots) == 15

    # Cross-check every round pad hole against the vendor drill file shipped in
    # the design package. Vendor coordinates are y-up; board coordinates y-down.
    vendor_path = CM4_DIR / "RPI-CM4IO-Gerber" / "CM4IOv5.drl"
    if vendor_path.exists():
        vendor = read_drill(vendor_path)
        vendor_points = {(round(x * 1000), round(-y * 1000))
                         for x, y, _d in vendor.holes}
        via_points = {(round(via.x_nm / 1000), round(via.y_nm / 1000))
                      for via in project.vias}
        pad_holes = [hole for hole in manifest.holes
                     if (round(hole.x_nm / 1000), round(hole.y_nm / 1000))
                     not in via_points]
        assert len(pad_holes) == 187
        matched = [hole for hole in pad_holes
                   if (round(hole.x_nm / 1000), round(hole.y_nm / 1000))
                   in vendor_points]
        assert len(matched) == len(pad_holes)
        unmatched = [hole for hole in manifest.holes
                     if (round(hole.x_nm / 1000), round(hole.y_nm / 1000))
                     not in vendor_points]
        # The vendor drill predates the current board: only vias differ.
        assert len(unmatched) == 9
        assert all((round(hole.x_nm / 1000), round(hole.y_nm / 1000))
                   in via_points for hole in unmatched)
