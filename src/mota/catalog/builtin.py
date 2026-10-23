"""Curated built-in catalog for the Mota demo.

Every part carries an explicit source list. Pin numbers and package variants
are recorded per MPN; generic passives are explicitly marked as Mota
engineering specifications rather than manufacturer part numbers. No price is
invented. 3D shapes are simplified bodies and are labelled `simplified`.
"""

from __future__ import annotations

from mota.domain.models import (
    Capabilities,
    FootprintDef,
    Model3D,
    PadDef,
    PartDef,
    PartEvidence,
    PartLimit,
    PinDef,
    Rect,
    SourceDef,
    SymbolDef,
    SymbolPinDef,
)
from mota.domain.units import mm

SOURCE_D1 = SourceDef(
    source_id="D1", kind="datasheet", title="Microchip MCP1700 datasheet DS20001826F",
    url="https://ww1.microchip.com/downloads/aemDocuments/documents/APID/ProductDocuments/DataSheets/MCP1700-Data-Sheet-20001826F.pdf",
    note="TO-92 pinout: 1=GND, 2=VIN, 3=VOUT. SOT-23 uses a different pinout.")
SOURCE_D2 = SourceDef(
    source_id="D2", kind="datasheet", title="TI NE555 datasheet (SLFS022)",
    url="https://www.ti.com/lit/ds/symlink/ne555.pdf",
    note="DIP-8 pinout and astable timing relation f=1.44/((RA+2RB)C).")
SOURCE_D3 = SourceDef(
    source_id="D3", kind="datasheet", title="Analog Devices DS1621 datasheet",
    url="https://www.analog.com/media/en/technical-documentation/data-sheets/DS1621.pdf",
    note="DIP-8 pinout, 7-bit address 0x48 with A2..A0 low, 2-wire interface.")
SOURCE_ENG_R = SourceDef(
    source_id="SPEC-R", kind="engineering_spec", title="Mota axial resistor engineering spec",
    note="Demo engineering specification (0.25 W, 1%, axial 10.16 mm). Not a manufacturer datasheet.")
SOURCE_ENG_C = SourceDef(
    source_id="SPEC-C", kind="engineering_spec", title="Mota radial capacitor engineering spec",
    note="Demo engineering specification. Effective capacitance under bias and ESR are not verified; no derating evidence is claimed.")
SOURCE_ENG_LED = SourceDef(
    source_id="SPEC-LED", kind="engineering_spec", title="Mota 5 mm LED engineering spec",
    note="Demo engineering specification. Pad 1 is the anode in this footprint; not a manufacturer drawing.")
SOURCE_ENG_CONN = SourceDef(
    source_id="SPEC-CONN", kind="engineering_spec", title="Mota 2.54 mm pin header engineering spec",
    note="Demo engineering specification (2.54 mm pitch through-hole header).")

SOURCES = [SOURCE_D1, SOURCE_D2, SOURCE_D3, SOURCE_ENG_R, SOURCE_ENG_C,
           SOURCE_ENG_LED, SOURCE_ENG_CONN]


def _pad(number: str, x: float, y: float, w: float = 1.8, h: float | None = None,
         *, shape: str = "circle", drill: float = 0.9, pad_type: str = "tht") -> PadDef:
    return PadDef(
        number=str(number), x_nm=mm(x), y_nm=mm(y), w_nm=mm(w),
        h_nm=mm(h if h is not None else w), shape=shape,  # type: ignore[arg-type]
        drill_nm=mm(drill) if drill else 0, pad_type=pad_type,  # type: ignore[arg-type]
        layers=["F.Cu", "B.Cu"] if pad_type != "smd" else ["F.Cu"],
    )


def _rect(x: float, y: float, w: float, h: float) -> Rect:
    return Rect(x_nm=mm(x), y_nm=mm(y), w_nm=mm(w), h_nm=mm(h))


FOOTPRINTS: dict[str, FootprintDef] = {
    "TO92_Inline_Mota": FootprintDef(
        footprint_id="TO92_Inline_Mota", name="TO-92 inline (Mota)",
        description="3 leads on a 1.27 mm inline pitch, 1.0 mm pads (0.55 mm drill)",
        pads=[_pad("1", -1.27, 0.0, 1.0, drill=0.55),
              _pad("2", 0.0, 0.0, 1.0, drill=0.55),
              _pad("3", 1.27, 0.0, 1.0, drill=0.55)],
        courtyard=_rect(-2.7, -2.2, 5.4, 4.4), body_height_mm=4.5,
        model=Model3D(model_id="model_to92", kind="box", height_mm=4.5, fidelity="simplified"),
        source_ids=["D1", "SPEC-CONN"]),
    "DIP-8_W7.62mm_Mota": FootprintDef(
        footprint_id="DIP-8_W7.62mm_Mota", name="DIP-8 7.62 mm (Mota)",
        description="8 through-hole pads, 2.54 mm pitch, 7.62 mm row spacing",
        pads=[
            _pad("1", -3.81, 3.81, 1.6, drill=0.8),
            _pad("2", -3.81, 1.27, 1.6, drill=0.8),
            _pad("3", -3.81, -1.27, 1.6, drill=0.8),
            _pad("4", -3.81, -3.81, 1.6, drill=0.8),
            _pad("5", 3.81, -3.81, 1.6, drill=0.8),
            _pad("6", 3.81, -1.27, 1.6, drill=0.8),
            _pad("7", 3.81, 1.27, 1.6, drill=0.8),
            _pad("8", 3.81, 3.81, 1.6, drill=0.8),
        ],
        courtyard=_rect(-5.4, -5.4, 10.8, 10.8), body_height_mm=3.6,
        model=Model3D(model_id="model_dip8", kind="box", height_mm=3.6, fidelity="simplified"),
        source_ids=["D2", "D3"]),
    "R_Axial_D10.16mm_Mota": FootprintDef(
        footprint_id="R_Axial_D10.16mm_Mota", name="Axial resistor, 10.16 mm (Mota)",
        description="2 through-hole pads on a 10.16 mm pitch",
        pads=[_pad("1", -5.08, 0.0), _pad("2", 5.08, 0.0)],
        courtyard=_rect(-6.5, -1.6, 13.0, 3.2), body_height_mm=2.4,
        model=Model3D(model_id="model_axial", kind="box", height_mm=2.4, fidelity="simplified"),
        source_ids=["SPEC-R"]),
    "C_Radial_D5.0mm_P2.54mm_Mota": FootprintDef(
        footprint_id="C_Radial_D5.0mm_P2.54mm_Mota", name="Radial capacitor, 2.54 mm (Mota)",
        description="2 through-hole pads on a 2.54 mm pitch",
        pads=[_pad("1", -1.27, 0.0), _pad("2", 1.27, 0.0)],
        courtyard=_rect(-3.2, -3.2, 6.4, 6.4), body_height_mm=7.0,
        model=Model3D(model_id="model_radial", kind="cylinder", height_mm=7.0, fidelity="simplified"),
        source_ids=["SPEC-C"]),
    "LED_D5.0mm_P2.54mm_Mota": FootprintDef(
        footprint_id="LED_D5.0mm_P2.54mm_Mota", name="LED 5 mm, 2.54 mm (Mota)",
        description="Anode pad 1, cathode pad 2, 2.54 mm pitch",
        pads=[_pad("1", -1.27, 0.0, shape="rect"), _pad("2", 1.27, 0.0)],
        courtyard=_rect(-3.2, -3.2, 6.4, 6.4), body_height_mm=8.6,
        model=Model3D(model_id="model_led", kind="cylinder", height_mm=8.6, fidelity="simplified"),
        source_ids=["SPEC-LED"]),
    "PinHeader_1x02_P2.54mm_Mota": FootprintDef(
        footprint_id="PinHeader_1x02_P2.54mm_Mota", name="Pin header 1x02 (Mota)",
        description="2 through-hole pads on a 2.54 mm pitch",
        pads=[_pad("1", -1.27, 0.0, 1.7, drill=1.0), _pad("2", 1.27, 0.0, 1.7, drill=1.0)],
        courtyard=_rect(-2.9, -1.3, 5.8, 2.6), body_height_mm=8.5,
        model=Model3D(model_id="model_header2", kind="box", height_mm=8.5, fidelity="simplified"),
        source_ids=["SPEC-CONN"]),
    "PinHeader_1x04_P2.54mm_Mota": FootprintDef(
        footprint_id="PinHeader_1x04_P2.54mm_Mota", name="Pin header 1x04 (Mota)",
        description="4 through-hole pads on a 2.54 mm pitch",
        pads=[_pad(str(i + 1), -3.81 + i * 2.54, 0.0, 1.7, drill=1.0) for i in range(4)],
        courtyard=_rect(-5.4, -1.3, 10.8, 2.6), body_height_mm=8.5,
        model=Model3D(model_id="model_header4", kind="box", height_mm=8.5, fidelity="simplified"),
        source_ids=["SPEC-CONN"]),
}


def _dip8_symbol(symbol_id: str) -> SymbolDef:
    pins = []
    for index, number in enumerate(["1", "2", "3", "4"]):
        pins.append(SymbolPinDef(number=number, name="", x_nm=mm(-7.62), y_nm=mm(3.81 - index * 2.54),
                                 direction="left", length_nm=mm(2.54)))
    for index, number in enumerate(["5", "6", "7", "8"]):
        pins.append(SymbolPinDef(number=number, name="", x_nm=mm(7.62), y_nm=mm(-3.81 + index * 2.54),
                                 direction="right", length_nm=mm(2.54)))
    return SymbolDef(symbol_id=symbol_id, body_w_nm=mm(10.16), body_h_nm=mm(10.16),
                     pins=pins, reference_prefix="U")


SYMBOLS: dict[str, SymbolDef] = {
    "SYM_MCP1700_TO92": SymbolDef(
        symbol_id="SYM_MCP1700_TO92", body_w_nm=mm(7.62), body_h_nm=mm(7.62),
        pins=[
            SymbolPinDef(number="1", name="GND", x_nm=mm(-6.35), y_nm=mm(-1.27), direction="left", length_nm=mm(2.54), electrical_type="power_in"),
            SymbolPinDef(number="2", name="VIN", x_nm=mm(-6.35), y_nm=mm(1.27), direction="left", length_nm=mm(2.54), electrical_type="power_in"),
            SymbolPinDef(number="3", name="VOUT", x_nm=mm(6.35), y_nm=mm(0), direction="right", length_nm=mm(2.54), electrical_type="power_out"),
        ], reference_prefix="U"),
    "SYM_NE555P": _dip8_symbol("SYM_NE555P"),
    "SYM_DS1621": _dip8_symbol("SYM_DS1621"),
    "SYM_R": SymbolDef(
        symbol_id="SYM_R", body_w_nm=mm(7.62), body_h_nm=mm(2.54), reference_prefix="R",
        pins=[SymbolPinDef(number="1", name="1", x_nm=mm(-7.62), y_nm=0, direction="left", length_nm=mm(2.54)),
              SymbolPinDef(number="2", name="2", x_nm=mm(7.62), y_nm=0, direction="right", length_nm=mm(2.54))]),
    "SYM_C": SymbolDef(
        symbol_id="SYM_C", body_w_nm=mm(4.0), body_h_nm=mm(5.08), reference_prefix="C",
        pins=[SymbolPinDef(number="1", name="1", x_nm=0, y_nm=mm(5.08), direction="up", length_nm=mm(2.54)),
              SymbolPinDef(number="2", name="2", x_nm=0, y_nm=mm(-5.08), direction="down", length_nm=mm(2.54))]),
    "SYM_LED": SymbolDef(
        symbol_id="SYM_LED", body_w_nm=mm(5.08), body_h_nm=mm(5.08), reference_prefix="D",
        pins=[SymbolPinDef(number="1", name="A", x_nm=mm(-5.08), y_nm=0, direction="left", length_nm=mm(2.54)),
              SymbolPinDef(number="2", name="K", x_nm=mm(5.08), y_nm=0, direction="right", length_nm=mm(2.54))]),
    "SYM_CONN_2": SymbolDef(
        symbol_id="SYM_CONN_2", body_w_nm=mm(5.08), body_h_nm=mm(6.35), reference_prefix="J",
        pins=[SymbolPinDef(number="1", name="1", x_nm=mm(-5.08), y_nm=mm(1.27), direction="left", length_nm=mm(2.54)),
              SymbolPinDef(number="2", name="2", x_nm=mm(-5.08), y_nm=mm(-1.27), direction="left", length_nm=mm(2.54))]),
    "SYM_CONN_4": SymbolDef(
        symbol_id="SYM_CONN_4", body_w_nm=mm(5.08), body_h_nm=mm(11.43), reference_prefix="J",
        pins=[SymbolPinDef(number=str(i + 1), name=str(i + 1), x_nm=mm(-5.08),
                           y_nm=mm(3.81 - i * 2.54), direction="left", length_nm=mm(2.54))
              for i in range(4)]),
}

GOLDEN_CAPS = Capabilities(
    catalog=True, placeable=True, mapped=True, auto_build_ready=True,
    model_3d_exact=False, schematic_render=True, footprint_render=True)
GENERIC_CAPS = Capabilities(
    catalog=True, placeable=True, mapped=True, auto_build_ready=False,
    model_3d_exact=False, schematic_render=True, footprint_render=True)


def _resistor(part_id: str, value: str) -> PartDef:
    return PartDef(
        part_id=part_id, mpn=f"MOTA-R-0.25W-{value}", manufacturer="Mota engineering spec",
        description=f"{value} axial through-hole resistor (demo engineering spec)",
        category="resistor", package="Axial 10.16 mm",
        footprint_id="R_Axial_D10.16mm_Mota", symbol_id="SYM_R",
        pins=[PinDef(number="1", name="1", electrical_type="passive", pad_numbers=["1"]),
              PinDef(number="2", name="2", electrical_type="passive", pad_numbers=["2"])],
        pin_pad_map={"1": ["1"], "2": ["2"]},
        capabilities=GENERIC_CAPS, value_label=value,
        limits=[PartLimit(key="power_w", value="0.25", source_id="SPEC-R")],
        source_ids=["SPEC-R"], attributes={"resistance": value})


def _capacitor(part_id: str, value: str, dielectric: str, kind: str,
               *, polarized: bool = False) -> PartDef:
    return PartDef(
        part_id=part_id, mpn=f"MOTA-C-{value}-{dielectric}", manufacturer="Mota engineering spec",
        description=f"{value} {dielectric} radial {kind} capacitor (demo engineering spec)",
        category="capacitor", package="Radial 2.54 mm",
        footprint_id="C_Radial_D5.0mm_P2.54mm_Mota", symbol_id="SYM_C",
        pins=[PinDef(number="1", name="1", electrical_type="passive", pad_numbers=["1"]),
              PinDef(number="2", name="2", electrical_type="passive", pad_numbers=["2"])],
        pin_pad_map={"1": ["1"], "2": ["2"]},
        capabilities=GENERIC_CAPS, value_label=value,
        limits=[PartLimit(key="polarized", value=str(polarized).lower(), source_id="SPEC-C")],
        evidence=[PartEvidence(
            evidence_id=f"{part_id}_derating", kind="capacitor_derating",
            status="not_checked", source_id=None,
            note="No effective-capacitance or ESR evidence is recorded for this demo part.")],
        source_ids=["SPEC-C"],
        attributes={"capacitance": value, "dielectric": dielectric, "polarized": str(polarized).lower()})


PARTS: dict[str, PartDef] = {
    # ------------------------------------------------------------- golden ICs
    "mcp1700_3302e_to": PartDef(
        part_id="mcp1700_3302e_to", mpn="MCP1700-3302E/TO", manufacturer="Microchip",
        description="250 mA low quiescent current LDO, 3.3 V fixed, TO-92",
        category="regulator", package="TO-92",
        footprint_id="TO92_Inline_Mota", symbol_id="SYM_MCP1700_TO92",
        pins=[
            PinDef(number="1", name="GND", electrical_type="power_in", pad_numbers=["1"]),
            PinDef(number="2", name="VIN", electrical_type="power_in", pad_numbers=["2"]),
            PinDef(number="3", name="VOUT", electrical_type="power_out", pad_numbers=["3"]),
        ],
        pin_pad_map={"1": ["1"], "2": ["2"], "3": ["3"]},
        capabilities=GOLDEN_CAPS,
        limits=[
            PartLimit(key="input_voltage_v", value="2.3-6.0", source_id="D1"),
            PartLimit(key="output_voltage_v", value="3.3", source_id="D1"),
            PartLimit(key="output_current_ma", value="250", source_id="D1"),
        ],
        source_ids=["D1"],
        evidence=[PartEvidence(
            evidence_id="mcp1700_to92_pinmap", kind="pin_map", status="verified",
            source_id="D1", values={"1": "GND", "2": "VIN", "3": "VOUT"},
            note="TO-92 physical pin order per datasheet; SOT-23 differs and is not used.")],
        attributes={"output_voltage_v": "3.3"}),
    "ne555p": PartDef(
        part_id="ne555p", mpn="NE555P", manufacturer="Texas Instruments",
        description="Single precision timer, DIP-8",
        category="timer", package="DIP-8 (300 mil)",
        footprint_id="DIP-8_W7.62mm_Mota", symbol_id="SYM_NE555P",
        pins=[
            PinDef(number="1", name="GND", electrical_type="power_in", pad_numbers=["1"]),
            PinDef(number="2", name="TRIG", electrical_type="input", pad_numbers=["2"]),
            PinDef(number="3", name="OUT", electrical_type="output", pad_numbers=["3"]),
            PinDef(number="4", name="RESET", electrical_type="input", pad_numbers=["4"]),
            PinDef(number="5", name="CONT", electrical_type="input", pad_numbers=["5"]),
            PinDef(number="6", name="THRES", electrical_type="input", pad_numbers=["6"]),
            PinDef(number="7", name="DISCH", electrical_type="open_collector", pad_numbers=["7"]),
            PinDef(number="8", name="VCC", electrical_type="power_in", pad_numbers=["8"]),
        ],
        pin_pad_map={str(i): [str(i)] for i in range(1, 9)},
        capabilities=GOLDEN_CAPS,
        limits=[
            PartLimit(key="supply_voltage_v", value="4.5-16", source_id="D2"),
            PartLimit(key="package", value="PDIP-8", source_id="D2"),
        ],
        source_ids=["D2"],
        evidence=[PartEvidence(
            evidence_id="ne555p_pinmap", kind="pin_map", status="verified", source_id="D2",
            values={str(i): n for i, n in enumerate(
                ["GND", "TRIG", "OUT", "RESET", "CONT", "THRES", "DISCH", "VCC"], start=1)})]),
    "ds1621_plus": PartDef(
        part_id="ds1621_plus", mpn="DS1621+", manufacturer="Analog Devices",
        description="Digital thermometer and thermostat, 2-wire interface, DIP-8",
        category="sensor", package="DIP-8 (300 mil)",
        footprint_id="DIP-8_W7.62mm_Mota", symbol_id="SYM_DS1621",
        pins=[
            PinDef(number="1", name="SDA", electrical_type="bidirectional", pad_numbers=["1"]),
            PinDef(number="2", name="SCL", electrical_type="input", pad_numbers=["2"]),
            PinDef(number="3", name="TOUT", electrical_type="output", pad_numbers=["3"]),
            PinDef(number="4", name="GND", electrical_type="power_in", pad_numbers=["4"]),
            PinDef(number="5", name="A2", electrical_type="input", pad_numbers=["5"]),
            PinDef(number="6", name="A1", electrical_type="input", pad_numbers=["6"]),
            PinDef(number="7", name="A0", electrical_type="input", pad_numbers=["7"]),
            PinDef(number="8", name="VDD", electrical_type="power_in", pad_numbers=["8"]),
        ],
        pin_pad_map={str(i): [str(i)] for i in range(1, 9)},
        capabilities=GOLDEN_CAPS,
        limits=[
            PartLimit(key="supply_voltage_v", value="2.7-5.5", source_id="D3"),
            PartLimit(key="interface", value="2-wire, 100 kHz in this design", source_id="D3"),
            PartLimit(key="address", value="0x48 with A2..A0 = 0", source_id="D3"),
        ],
        source_ids=["D3"],
        evidence=[PartEvidence(
            evidence_id="ds1621_pinmap", kind="pin_map", status="verified", source_id="D3",
            values={str(i): n for i, n in enumerate(
                ["SDA", "SCL", "TOUT", "GND", "A2", "A1", "A0", "VDD"], start=1)})]),
    # ------------------------------------------------------------- passives
    "r_1k": _resistor("r_1k", "1k"),
    "r_3k3": _resistor("r_3k3", "3.3k"),
    "r_10k": _resistor("r_10k", "10k"),
    "r_68k": _resistor("r_68k", "68k"),
    "c_2u2_50v": _capacitor("c_2u2_50v", "2.2uF", "X7R", "ceramic"),
    "c_10u_16v": _capacitor("c_10u_16v", "10uF", "electrolytic", "electrolytic", polarized=True),
    "c_10n_50v": _capacitor("c_10n_50v", "10nF", "C0G", "ceramic"),
    "c_100n_50v": _capacitor("c_100n_50v", "100nF", "X7R", "ceramic"),
    "led_red_5mm": PartDef(
        part_id="led_red_5mm", mpn="MOTA-LED-RED-5MM", manufacturer="Mota engineering spec",
        description="5 mm red LED, through-hole (demo engineering spec)",
        category="led", package="LED 5 mm",
        footprint_id="LED_D5.0mm_P2.54mm_Mota", symbol_id="SYM_LED",
        pins=[PinDef(number="1", name="A", electrical_type="passive", pad_numbers=["1"]),
              PinDef(number="2", name="K", electrical_type="passive", pad_numbers=["2"])],
        pin_pad_map={"1": ["1"], "2": ["2"]},
        capabilities=GENERIC_CAPS,
        limits=[PartLimit(key="forward_voltage_v", value="~1.8-2.0 (demo)", source_id="SPEC-LED")],
        source_ids=["SPEC-LED"],
        evidence=[PartEvidence(evidence_id="led_polarity", kind="polarity", status="candidate",
                               source_id="SPEC-LED", values={"pad_1": "A", "pad_2": "K"},
                               note="Pad 1 is the anode in this Mota footprint; verify against the chosen physical part.")]),
    "conn_1x02": PartDef(
        part_id="conn_1x02", mpn="MOTA-HDR-1X02-2.54", manufacturer="Mota engineering spec",
        description="2-pin 2.54 mm through-hole header (demo engineering spec)",
        category="connector", package="Pin header 1x02",
        footprint_id="PinHeader_1x02_P2.54mm_Mota", symbol_id="SYM_CONN_2",
        pins=[PinDef(number=str(i), name=str(i), electrical_type="passive", pad_numbers=[str(i)])
              for i in (1, 2)],
        pin_pad_map={"1": ["1"], "2": ["2"]},
        capabilities=GENERIC_CAPS, source_ids=["SPEC-CONN"]),
    "conn_1x04": PartDef(
        part_id="conn_1x04", mpn="MOTA-HDR-1X04-2.54", manufacturer="Mota engineering spec",
        description="4-pin 2.54 mm through-hole header (demo engineering spec)",
        category="connector", package="Pin header 1x04",
        footprint_id="PinHeader_1x04_P2.54mm_Mota", symbol_id="SYM_CONN_4",
        pins=[PinDef(number=str(i), name=str(i), electrical_type="passive", pad_numbers=[str(i)])
              for i in range(1, 5)],
        pin_pad_map={str(i): [str(i)] for i in range(1, 5)},
        capabilities=GENERIC_CAPS, source_ids=["SPEC-CONN"]),
}

PACKAGE_CATEGORIES = sorted({p.category for p in PARTS.values()})


def get_part(part_id: str) -> PartDef:
    if part_id not in PARTS:
        raise KeyError(part_id)
    return PARTS[part_id]


def get_footprint(footprint_id: str) -> FootprintDef:
    return FOOTPRINTS[footprint_id]


def get_symbol(symbol_id: str) -> SymbolDef:
    return SYMBOLS[symbol_id]
