"""Small, hand-written KiCad 6 project used by the C12 tests.

It is intentionally synthetic: it exercises rotation, a slot drill, a custom
pad, a zone fill, schematic sheets and project rules without shipping any
third-party design into the repository.
"""

from __future__ import annotations

import json
from pathlib import Path

PCB = """(kicad_pcb (version 20211014) (generator pcbnew)
  (general (thickness 1.6))
  (paper "A4")
  (layers
    (0 "F.Cu" signal)
    (31 "B.Cu" signal)
    (36 "B.SilkS" user "B.Silkscreen")
    (37 "F.SilkS" user "F.Silkscreen")
    (39 "F.Mask" user)
    (44 "Edge.Cuts" user)
  )
  (setup (pad_to_mask_clearance 0))
  (net 0 "")
  (net 1 "GND")
  (net 2 "SIG")
  (footprint "Test:Conn" (layer "F.Cu")
    (at 10 10 90)
    (fp_text reference "J1" (at 0 0) (layer "F.SilkS")
      (effects (font (size 1 1))))
    (fp_text value "Conn_01x04" (at 0 -2) (layer "F.Fab")
      (effects (font (size 1 1))))
    (property "Sheetfile" "c12.kicad_sch")
    (attr through_hole)
    (pad "1" thru_hole circle (at 0 0) (size 1.7 1.7) (drill 1)
      (layers "*.Cu" "*.Mask") (net 1 "GND"))
    (pad "2" thru_hole oval (at 2.54 0 90) (size 1.7 1.2)
      (drill oval 1.2 0.6) (layers "*.Cu" "*.Mask") (net 2 "SIG"))
    (pad "3" smd rect (at 5.08 0) (size 1 1)
      (layers "F.Cu" "F.Paste" "F.Mask") (net 2 "SIG"))
    (pad "4" smd custom (at 7 0) (size 2 2)
      (layers "F.Cu" "F.Paste" "F.Mask") (net 2 "SIG")
      (primitives (gr_poly (pts (xy -1 -1) (xy 1 -1) (xy 0 1))
        (width 0.1) (fill yes))))
  )
  (footprint "Test:R_0603" (layer "B.Cu")
    (at 20 15 0)
    (fp_text reference "R1" (at 0 0) (layer "B.SilkS")
      (effects (font (size 1 1))))
    (fp_text value "10k" (at 0 2) (layer "B.Fab")
      (effects (font (size 1 1))))
    (attr smd)
    (pad "1" smd rect (at -1 0) (size 1 0.6)
      (layers "B.Cu" "B.Paste" "B.Mask") (net 2 "SIG"))
    (pad "2" smd rect (at 1 0) (size 1 0.6)
      (layers "B.Cu" "B.Paste" "B.Mask") (net 1 "GND"))
  )
  (segment (start 10 10) (end 15 10) (width 0.25) (layer "F.Cu") (net 1))
  (segment (start 20 15) (end 20 10) (width 0.25) (layer "B.Cu") (net 2))
  (via (at 15 10) (size 0.8) (drill 0.4) (layers "F.Cu" "B.Cu") (net 1))
  (zone (net 1) (net_name "GND") (layer "F.Cu") (hatch edge 0.508)
    (connect_pads (clearance 0.2))
    (min_thickness 0.254)
    (fill yes (thermal_gap 0.508) (thermal_bridge_width 0.508))
    (polygon (pts (xy 0 0) (xy 30 0) (xy 30 30) (xy 0 30)))
    (filled_polygon (layer "F.Cu")
      (pts (xy 0 0) (xy 30 0) (xy 30 30) (xy 0 30)))
  )
  (gr_line (start 0 0) (end 40 0) (layer "Edge.Cuts") (width 0.1))
  (gr_line (start 40 0) (end 40 20) (layer "Edge.Cuts") (width 0.1))
  (gr_line (start 40 20) (end 0 20) (layer "Edge.Cuts") (width 0.1))
  (gr_line (start 0 20) (end 0 0) (layer "Edge.Cuts") (width 0.1))
  (gr_arc (start 5 5) (mid 6 -1) (end 9 2) (layer "F.SilkS") (width 0.12))
  (gr_rect (start 30 10) (end 32 12) (layer "F.SilkS") (width 0.12)
    (fill none))
  (dimension 1 (type aligned) (layer "Dwgs.User") (pts (xy 0 22) (xy 40 22))
    (height 2) (gr_text "40mm" (at 20 24) (layer "Dwgs.User")
      (effects (font (size 1 1)))))
)
"""

SCHEMATIC = """(kicad_sch (version 20211123) (generator eeschema)
  (uuid "0f0f0f0f-1111-2222-3333-444444444444")
  (paper "A4")
  (lib_symbols)
  (junction (at 55 50) (diameter 0) (color 0 0 0 0)
    (uuid "11111111-1111-1111-1111-111111111111"))
  (no_connect (at 62 50) (uuid "22222222-2222-2222-2222-222222222222"))
  (wire (pts (xy 50 50) (xy 60 50)) (stroke (width 0.1524) (type default))
    (uuid "33333333-3333-3333-3333-333333333333"))
  (label "SIG" (at 60 50 0) (effects (font (size 1.27 1.27))
    (justify left bottom)) (uuid "44444444-4444-4444-4444-444444444444"))
  (symbol (lib_id "Test:Conn") (at 50 50 0) (unit 1)
    (in_bom yes) (on_board yes) (dnp no)
    (uuid "55555555-5555-5555-5555-555555555555")
    (property "Reference" "J1" (at 0 0 0) (effects (font (size 1.27 1.27))))
    (property "Value" "Conn_01x04" (at 0 2 0)
      (effects (font (size 1.27 1.27))))
    (property "Footprint" "Test:Conn" (at 0 0 0)
      (effects (font (size 1.27 1.27)) hide))
    (pin "1" (uuid "66666666-6666-6666-6666-666666666666"))
    (pin "2" (uuid "77777777-7777-7777-7777-777777777777"))
  )
  (symbol (lib_id "Test:R") (at 20 15 180) (unit 1)
    (in_bom yes) (on_board yes) (dnp no)
    (uuid "88888888-8888-8888-8888-888888888888")
    (property "Reference" "R1" (at 0 0 0) (effects (font (size 1.27 1.27))))
    (property "Value" "10k" (at 0 2 0) (effects (font (size 1.27 1.27))))
    (property "Footprint" "Test:R_0603" (at 0 0 0)
      (effects (font (size 1.27 1.27)) hide))
    (pin "1" (uuid "99999999-9999-9999-9999-999999999999"))
    (pin "2" (uuid "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"))
  )
  (sheet (at 100 50) (size 20 10)
    (property "Sheetname" "Child" (at 0 0 0)
      (effects (font (size 1.27 1.27))))
    (property "Sheetfile" "child.kicad_sch" (at 0 0 0)
      (effects (font (size 1.27 1.27))))
    (uuid "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"))
  (sheet_instances (path "/" (page "1")))
)
"""

CHILD_SCHEMATIC = """(kicad_sch (version 20211123) (generator eeschema)
  (uuid "cccccccc-cccc-cccc-cccc-cccccccccccc")
  (paper "A4")
  (lib_symbols)
  (wire (pts (xy 10 10) (xy 20 10)) (stroke (width 0.1524) (type default))
    (uuid "dddddddd-dddd-dddd-dddd-dddddddddddd"))
  (sheet_instances (path "/" (page "2")))
)
"""

PROJECT = {
    "meta": {"filename": "c12.kicad_pro", "version": 1},
    "board": {
        "design_settings": {
            "rules": {
                "min_clearance": 0.2,
                "min_track_width": 0.2,
                "min_via_diameter": 0.6,
                "allow_microvias": False,
            },
            "defaults": {},
        },
    },
    "net_settings": {
        "classes": [
            {
                "name": "Default",
                "clearance": 0.2,
                "track_width": 0.25,
                "via_diameter": 0.8,
                "via_drill": 0.4,
                "diff_pair_width": 0.2,
                "diff_pair_gap": 0.25,
                "bus_width": 12,
            },
            {
                "name": "POWER",
                "clearance": 0.3,
                "track_width": 0.5,
                "via_diameter": 1.0,
                "via_drill": 0.5,
                "diff_pair_width": 0.2,
                "diff_pair_gap": 0.25,
            },
        ],
        "meta": {"version": 3},
    },
    "sheets": [["0f0f0f0f-1111-2222-3333-444444444444", ""]],
    "libraries": {"pinned_footprint_libs": ["Test"]},
    "pcbnew": {"last_paths": {"pcb": "c12.kicad_pcb"}},
}


def write_minimal_project(directory: str | Path) -> Path:
    """Write the synthetic project and return its directory."""

    path = Path(directory)
    path.mkdir(parents=True, exist_ok=True)
    (path / "c12.kicad_pcb").write_text(PCB, encoding="utf-8")
    (path / "c12.kicad_sch").write_text(SCHEMATIC, encoding="utf-8")
    (path / "child.kicad_sch").write_text(CHILD_SCHEMATIC, encoding="utf-8")
    (path / "c12.kicad_pro").write_text(
        json.dumps(PROJECT, indent=2) + "\n", encoding="utf-8")
    return path
