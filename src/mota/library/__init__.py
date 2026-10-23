"""Versioned KiCad library import, snapshot index and asset lookup."""

from mota.library.catalog import (
    ImportCounts, LibraryImportReport, SourceInfo, SourceSpec,
    import_library, inspect_asset, library_status, load_summaries,
    preview_asset, rollback_library, search_assets,
)
from mota.library.records import (
    AssetRecord, AssetSummary, Diagnostic, PadRecord, PinRecord, make_asset_id,
    parse_asset_id,
)

__all__ = [
    "AssetRecord", "AssetSummary", "Diagnostic", "ImportCounts",
    "LibraryImportReport", "PadRecord", "PinRecord", "SourceInfo", "SourceSpec",
    "import_library", "inspect_asset", "library_status", "load_summaries",
    "make_asset_id", "parse_asset_id", "preview_asset", "rollback_library",
    "search_assets",
]
