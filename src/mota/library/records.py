"""Library-layer records shared by the KiCad readers and the catalog index.

All geometry is stored in millimetres exactly as written in the source file;
conversion to nanometres happens only when an asset is turned into a Mota
footprint/symbol by a later layer.
"""

from __future__ import annotations

from typing import Literal

from mota.domain.models import DTO

AssetKind = Literal["symbol", "footprint"]
AssetStatus = Literal["parsed", "unsupported", "failed"]
Severity = Literal["info", "warning", "error"]


class Diagnostic(DTO):
    severity: Severity
    code: str
    message: str
    file: str
    line: int | None = None
    column: int | None = None
    asset_id: str | None = None


class PinRecord(DTO):
    number: str
    name: str
    electrical_type: str
    unit: int = 1
    body_style: int = 1
    x_mm: float
    y_mm: float
    angle_deg: float = 0.0
    length_mm: float = 0.0
    hidden: bool = False


class PadRecord(DTO):
    number: str
    pad_type: str
    shape: str
    x_mm: float
    y_mm: float
    width_mm: float
    height_mm: float
    rotation_deg: float = 0.0
    drill_mm: float = 0.0
    drill_height_mm: float = 0.0
    layers: list[str] = []
    unsupported_type: bool = False
    unsupported_shape: bool = False


class AssetSummary(DTO):
    """Compact index entry; full pins/pads are re-read on demand."""

    asset_id: str
    kind: AssetKind
    source_id: str
    library: str
    name: str
    status: AssetStatus
    mapped: bool = False
    placeable: bool = False
    content_hash: str
    source_file: str
    source_sha256: str
    offset: int = 0
    line: int = 1
    units: int = 1
    body_styles: int = 1
    extends: str | None = None
    pin_count: int = 0
    pad_count: int = 0
    description: str = ""
    keywords: str = ""
    datasheet: str = ""
    unsupported_features: list[str] = []
    unknown_electrical: bool = False
    diagnostic_codes: dict[str, int] = {}
    # Kept only for failed/unsupported assets so their location survives even
    # when the source file is no longer available.
    diagnostics: list[Diagnostic] = []

    def public(self) -> dict[str, object]:
        return {
            "asset_id": self.asset_id,
            "kind": self.kind,
            "source_id": self.source_id,
            "library": self.library,
            "name": self.name,
            "status": self.status,
            "mapped": self.mapped,
            "placeable": self.placeable,
            "units": self.units,
            "body_styles": self.body_styles,
            "pin_count": self.pin_count,
            "pad_count": self.pad_count,
            "description": self.description,
            "keywords": self.keywords,
            "source_file": self.source_file,
            "unsupported_features": list(self.unsupported_features),
        }


class AssetRecord(DTO):
    """One symbol or footprint discovered in a source snapshot.

    Failed records are kept (with `status="failed"`) so that the coverage
    identity `discovered = parsed + unsupported + failed` is auditable and the
    failure can be located by asset id.
    """

    asset_id: str
    kind: AssetKind
    source_id: str
    library: str
    name: str
    status: AssetStatus
    mapped: bool = False
    placeable: bool = False
    content_hash: str
    source_file: str
    source_sha256: str
    offset: int = 0
    line: int = 1
    units: int = 1
    body_styles: int = 1
    extends: str | None = None
    pins: list[PinRecord] = []
    pads: list[PadRecord] = []
    properties: dict[str, str] = {}
    description: str = ""
    keywords: str = ""
    datasheet: str = ""
    models: list[str] = []
    attributes: list[str] = []
    unsupported_features: list[str] = []
    unknown_electrical: bool = False
    diagnostics: list[Diagnostic] = []

    @property
    def pin_numbers(self) -> list[str]:
        return sorted({pin.number for pin in self.pins})

    @property
    def pad_numbers(self) -> list[str]:
        return sorted({pad.number for pad in self.pads})

    def to_summary(self) -> AssetSummary:
        codes: dict[str, int] = {}
        for diag in self.diagnostics:
            codes[diag.code] = codes.get(diag.code, 0) + 1
        keep_diagnostics = (list(self.diagnostics)
                            if self.status in ("failed", "unsupported") else [])
        return AssetSummary(
            asset_id=self.asset_id, kind=self.kind, source_id=self.source_id,
            library=self.library, name=self.name, status=self.status,
            mapped=self.mapped, placeable=self.placeable,
            content_hash=self.content_hash, source_file=self.source_file,
            source_sha256=self.source_sha256, offset=self.offset,
            line=self.line, units=self.units, body_styles=self.body_styles,
            extends=self.extends, pin_count=len(self.pins),
            pad_count=len(self.pads), description=self.description,
            keywords=self.keywords, datasheet=self.datasheet,
            unsupported_features=list(self.unsupported_features),
            unknown_electrical=self.unknown_electrical,
            diagnostic_codes=codes, diagnostics=keep_diagnostics)


def make_asset_id(kind: AssetKind, source_id: str, library: str, name: str,
                  duplicate_index: int = 0) -> str:
    prefix = "sym" if kind == "symbol" else "fp"
    base = f"{prefix}:{source_id}:{library}:{name}"
    return f"{base}#{duplicate_index + 1}" if duplicate_index else base


def parse_asset_id(asset_id: str) -> tuple[AssetKind, str, str, str]:
    """Split an asset id into (kind, source_id, library, name).

    Asset ids are `sym:<source>:<library>:<name>` or
    `fp:<source>:<library>:<name>`; failed duplicates append `#N`.
    """
    duplicate = 0
    raw = asset_id
    if "#" in raw:
        raw, _, suffix = raw.rpartition("#")
        duplicate = int(suffix) if suffix.isdigit() else 0
    parts = raw.split(":", 3)
    if len(parts) != 4 or parts[0] not in ("sym", "fp"):
        raise ValueError(f"not a Mota library asset id: {asset_id}")
    kind: AssetKind = "symbol" if parts[0] == "sym" else "footprint"
    return kind, parts[1], parts[2], parts[3]
