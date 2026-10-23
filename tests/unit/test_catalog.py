from __future__ import annotations

import pytest

from mota.catalog import builtin
from mota.catalog import service
from mota.catalog.kicad_parser import parse_footprint, parse_symbol_library
from mota.errors import NotFound


def test_mcp1700_to92_real_pinout():
    part = builtin.PARTS["mcp1700_3302e_to"]
    assert part.mpn == "MCP1700-3302E/TO"
    assert {p.number: p.name for p in part.pins} == {"1": "GND", "2": "VIN", "3": "VOUT"}
    assert part.pin_pad_map == {"1": ["1"], "2": ["2"], "3": ["3"]}
    assert part.capabilities.auto_build_ready


def test_ne555_and_ds1621_pinouts():
    ne555 = builtin.PARTS["ne555p"]
    assert {p.number: p.name for p in ne555.pins}["4"] == "RESET"
    assert {p.number: p.name for p in ne555.pins}["7"] == "DISCH"
    ds = builtin.PARTS["ds1621_plus"]
    pins = {p.number: p.name for p in ds.pins}
    assert pins == {"1": "SDA", "2": "SCL", "3": "TOUT", "4": "GND",
                    "5": "A2", "6": "A1", "7": "A0", "8": "VDD"}


def test_inspect_reports_capabilities_and_missing_evidence():
    result = service.inspect("c_2u2_50v")
    codes = {d["code"] for d in result["diagnostics"]}
    assert "EVIDENCE_NOT_VERIFIED" in codes
    assert "MODEL_3D_APPROXIMATE" in codes
    assert result["placeable"] is True
    assert result["pin_pad_map"] == {"1": ["1"], "2": ["2"]}


def test_search_distinguishes_no_match():
    result = service.search("definitely-no-such-part")
    assert result["total"] == 0
    assert result["state"] == "no_match"
    filtered = service.search("", category="capacitor")
    assert filtered["total"] >= 4
    assert all(c["category"] == "capacitor" for c in filtered["candidates"])


def test_unknown_part_raises_part_not_found():
    with pytest.raises(NotFound) as exc:
        service.inspect("not_a_part")
    assert exc.value.code == "PART_NOT_FOUND"


def test_kicad_symbol_parser(tmp_path):
    symbol = '''(kicad_symbol_lib (version 20231120) (generator kicad_symbol_editor)
      (symbol "Test:Widget"
        (property "Reference" "U" (at 0 0 0))
        (symbol "Widget_0_1"
          (rectangle (start -2 -2) (end 2 2)))
        (symbol "Widget_1_1"
          (pin power_in line (at -5 0 0) (length 2.54)
            (name "VCC" (effects (font (size 1.27 1.27))))
            (number "1" (effects (font (size 1.27 1.27)))))
          (pin passive line (at 0 -5 90) (length 2.54)
            (name "GND" (effects (font (size 1.27 1.27))))
            (number "2" (effects (font (size 1.27 1.27))))))))
    '''
    path = tmp_path / "Test.kicad_sym"
    path.write_text(symbol, encoding="utf-8")
    parsed = parse_symbol_library(path)
    assert len(parsed) == 1
    assert parsed[0].lib_id == "Test:Widget"
    assert {p.number for p in parsed[0].pins} == {"1", "2"}
    assert parsed[0].properties["Reference"] == "U"


def test_kicad_footprint_parser_flags_unsupported_pads(tmp_path):
    footprint = '''(footprint "Test:Widget" (layer "F.Cu")
      (pad "1" thru_hole circle (at -1 0) (size 1.6 1.6) (drill 0.8)
        (layers "*.Cu" "*.Mask"))
      (pad "2" smd roundrect (at 1 0) (size 1 1) (layers "F.Cu" "F.Paste" "F.Mask")))
    '''
    path = tmp_path / "Widget.kicad_mod"
    path.write_text(footprint, encoding="utf-8")
    parsed = parse_footprint(path)
    assert len(parsed.pads) == 2
    assert any("UNSUPPORTED_PAD_SHAPE" in d for d in parsed.diagnostics)


def test_all_golden_parts_are_mapped():
    for part in builtin.PARTS.values():
        footprint = builtin.FOOTPRINTS[part.footprint_id]
        pads = {p.number for p in footprint.pads}
        for pin in part.pins:
            assert part.pin_pad_map.get(pin.number), (part.mpn, pin.number)
            assert set(part.pin_pad_map[pin.number]) <= pads
