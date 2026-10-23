"""Integer nanometre geometry helpers.

All persisted geometry uses integer nanometres. Floats are only used for
drawing and for approximate geometry math, never as the stored project truth.
"""

from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP

NM_PER_MM = 1_000_000


def mm(value: float | int | str | Decimal) -> int:
    """Convert millimetres to integer nanometres without binary float drift."""
    if isinstance(value, int):
        return value * NM_PER_MM
    dec = Decimal(str(value)) * NM_PER_MM
    return int(dec.quantize(Decimal(1), rounding=ROUND_HALF_UP))


def to_mm(nm: int) -> float:
    return nm / NM_PER_MM


def fmt_mm(nm: int, digits: int = 2) -> str:
    return f"{to_mm(nm):.{digits}f} mm"
