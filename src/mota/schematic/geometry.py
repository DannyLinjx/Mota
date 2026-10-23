"""Shared schematic sheet / occupancy geometry.

One source of truth for the sheet, the title-block exclusion and the full
occupied extent of a symbol (body, pins, pin numbers, reference, value and the
net label attached to every pin).  Layout, validation and the renderer all use
these values, so the stored position and the visible geometry cannot drift.
"""

from __future__ import annotations

from dataclasses import dataclass

from mota.domain.models import PartDef, SymbolDef, SymbolInstance

# Standard landscape sheets, smallest first. A larger sheet is only selected
# when the complete content does not fit the smaller one.
SHEETS: tuple[tuple[float, float, str], ...] = (
    (297.0, 210.0, "A4"),
    (420.0, 297.0, "A3"),
    (594.0, 420.0, "A2"),
)
MARGIN_MM = 10.0
TITLE_BLOCK_W_MM = 88.0
TITLE_BLOCK_H_MM = 20.0
TITLE_BLOCK_INSET_MM = 2.0
# Content keeps clear of the whole bottom band occupied by the title block.
CONTENT_BOTTOM_MM = TITLE_BLOCK_H_MM + TITLE_BLOCK_INSET_MM + 4.0

STUB_MM = 2.54
LABEL_FONT_MM = 2.2
LABEL_CHAR_W_MM = 0.58 * LABEL_FONT_MM
REF_FONT_MM = 2.4
REF_CHAR_W_MM = 1.1
VALUE_FONT_MM = 2.0
VALUE_CHAR_W_MM = 0.9
PIN_NUMBER_PAD_MM = 1.4


@dataclass(frozen=True)
class Extent:
    """Axis-aligned extent in schematic millimetres (Y up)."""

    x0: float
    y0: float
    x1: float
    y1: float

    @property
    def w(self) -> float:
        return self.x1 - self.x0

    @property
    def h(self) -> float:
        return self.y1 - self.y0

    def translated(self, x: float, y: float) -> "Extent":
        return Extent(self.x0 + x, self.y0 + y, self.x1 + x, self.y1 + y)


def title_block_rect(page_w_mm: float) -> tuple[float, float, float, float]:
    x0 = page_w_mm - TITLE_BLOCK_W_MM - TITLE_BLOCK_INSET_MM
    y0 = TITLE_BLOCK_INSET_MM
    return x0, y0, x0 + TITLE_BLOCK_W_MM, y0 + TITLE_BLOCK_H_MM


def usable_region(page_w_mm: float, page_h_mm: float) -> tuple[float, float, float, float]:
    return MARGIN_MM, CONTENT_BOTTOM_MM, page_w_mm - MARGIN_MM, page_h_mm - MARGIN_MM


def symbol_local_extent(part: PartDef | None, symbol_def: SymbolDef,
                        reference: str, value: str,
                        pin_nets: dict[str, str]) -> Extent:
    """Full extent of one symbol relative to its placement point (mm, Y up)."""
    half_w = symbol_def.body_w_nm / 2 / 1e6
    half_h = symbol_def.body_h_nm / 2 / 1e6
    x0, y0, x1, y1 = -half_w, -half_h, half_w, half_h
    pin_names = {pin.number: pin.name for pin in part.pins} if part else {}
    for pin in symbol_def.pins:
        px = pin.x_nm / 1e6
        py = pin.y_nm / 1e6
        x0, y0 = min(x0, px), min(y0, py)
        x1, y1 = max(x1, px), max(y1, py)
        net_name = pin_nets.get(pin.number) or pin_names.get(pin.number, "")
        if not net_name:
            continue
        length = max(4.0, len(net_name) * LABEL_CHAR_W_MM) + 1.0
        if pin.direction == "left":
            x0 = min(x0, px - STUB_MM - length)
        elif pin.direction == "right":
            x1 = max(x1, px + STUB_MM + length)
        elif pin.direction == "up":
            y1 = max(y1, py + STUB_MM + length)
        else:
            y0 = min(y0, py - STUB_MM - length)
    x0 -= PIN_NUMBER_PAD_MM
    x1 += PIN_NUMBER_PAD_MM
    y0 -= PIN_NUMBER_PAD_MM
    y1 += PIN_NUMBER_PAD_MM
    ref_w = max(4.0, len(reference) * REF_CHAR_W_MM)
    val_w = max(4.0, len(value) * VALUE_CHAR_W_MM)
    x0 = min(x0, -ref_w / 2, -val_w / 2)
    x1 = max(x1, ref_w / 2, val_w / 2)
    y1 = max(y1, half_h + REF_FONT_MM + 1.0)
    y0 = min(y0, -half_h - VALUE_FONT_MM - 1.6)
    return Extent(x0, y0, x1, y1)


def symbol_occupancy(sym: SymbolInstance, part: PartDef | None,
                     symbol_def: SymbolDef,
                     pin_nets: dict[str, str]) -> Extent:
    local = symbol_local_extent(part, symbol_def, sym.reference, sym.value, pin_nets)
    return local.translated(sym.x_nm / 1e6, sym.y_nm / 1e6)
