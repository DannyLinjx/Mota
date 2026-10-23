"""Verified electrical data for imported assets used by real projects.

KiCad geometry is real, but it does not carry recommended operating ranges.
These entries are transcribed from the manufacturers' datasheets and are keyed
by the imported asset id so validation can read them through the same resolver
as builtin parts. Missing entries stay unknown instead of silently passing.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class VerifiedPart:
    supply_min_v: float
    supply_max_v: float
    supply_pins: tuple[str, ...]
    source_title: str
    source_url: str
    note: str = ""


VERIFIED_PARTS: dict[str, VerifiedPart] = {
    "sym:kicad-7.0.11:MCU_ST_STM32F1:STM32F103C8Tx": VerifiedPart(
        2.0, 3.6, ("VDD", "VDDA", "VBAT"),
        "ST STM32F103x8/xB datasheet", 
        "https://www.st.com/resource/en/datasheet/stm32f103c8.pdf",
        "Recommended operating VDD 2.0-3.6 V; VDDA and VBAT share the range."),
    "sym:kicad-7.0.11:Regulator_Linear:TLV75533PDBV": VerifiedPart(
        2.5, 5.5, ("IN",),
        "TI TLV755P datasheet", "https://www.ti.com/lit/ds/symlink/tlv755p.pdf",
        "Input 2.5-5.5 V, fixed 3.3 V output."),
    "sym:kicad-7.0.11:Interface_UART:MAX3485": VerifiedPart(
        3.0, 3.6, ("VCC",),
        "Analog Devices MAX3483-MAX3491 datasheet",
        "https://www.analog.com/media/en/technical-documentation/data-sheets/MAX3483-MAX3491.pdf",
        "3.3 V half-duplex RS-485 transceiver, SOIC-8."),
    "sym:kicad-7.0.11:Interface_UART:THVD1500": VerifiedPart(
        4.5, 5.5, ("VCC",),
        "TI THVD1500 datasheet", "https://www.ti.com/lit/ds/symlink/thvd1500.pdf",
        "5 V RS-485 transceiver; not a 3.3 V replacement."),
    "sym:kicad-7.0.11:Memory_EEPROM:24LC256": VerifiedPart(
        2.5, 5.5, ("VCC",),
        "Microchip 24AA256/24LC256/24FC256 datasheet DS20001203U",
        "https://ww1.microchip.com/downloads/en/DeviceDoc/20001203U.pdf",
        "Device Selection Table: 24LC256 is 2.5-5.5 V (1.8-5.5 V applies to "
        "24AA256, not 24LC256)."),
}


def verified_part(part_id: str) -> VerifiedPart | None:
    return VERIFIED_PARTS.get(part_id)
