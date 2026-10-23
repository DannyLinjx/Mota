"""Catalog queries over the component library.

Discovery and detail both go through `mota.library.resolver`, the same resolver
used by placement, validation and project restore. The builtin Python table is
no longer the search universe: it was migrated into packages and survives only
as a compatibility fallback when a library root has not been migrated yet.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from mota.catalog import builtin
from mota.catalog.templates import get_template, list_templates, template_detail
from mota.domain.models import PartDef
from mota.errors import AssetUnsupported, NotFound


def capability_labels(part: PartDef) -> list[str]:
    labels = []
    if part.capabilities.catalog:
        labels.append("Catalog")
    if part.capabilities.placeable:
        labels.append("Placeable")
    if part.capabilities.mapped:
        labels.append("Mapped")
    if part.capabilities.auto_build_ready:
        labels.append("Auto-build ready")
    return labels


def part_diagnostics(part: PartDef) -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    footprint = builtin.FOOTPRINTS.get(part.footprint_id)
    if footprint is None:
        out.append({"code": "FOOTPRINT_NOT_FOUND", "detail": part.footprint_id})
    else:
        pad_numbers = {p.number for p in footprint.pads}
        for pin in part.pins:
            target = part.pin_pad_map.get(pin.number)
            if not target:
                out.append({"code": "PIN_PAD_MAPPING_MISSING", "detail": pin.number})
                continue
            for pad_number in target:
                if pad_number not in pad_numbers:
                    out.append({"code": "PIN_PAD_TARGET_MISSING",
                                "detail": f"pin {pin.number} -> pad {pad_number}"})
            if pin.pad_numbers and sorted(pin.pad_numbers) != sorted(target):
                out.append({"code": "PIN_PAD_MAPPING_INCONSISTENT", "detail": pin.number})
    if part.symbol_id not in builtin.SYMBOLS:
        out.append({"code": "SYMBOL_NOT_FOUND", "detail": part.symbol_id})
    if not part.capabilities.model_3d_exact:
        out.append({"code": "MODEL_3D_APPROXIMATE",
                    "detail": "Simplified body; not an exact vendor STEP model."})
    for evidence in part.evidence:
        if evidence.status != "verified":
            out.append({"code": "EVIDENCE_NOT_VERIFIED",
                        "detail": f"{evidence.evidence_id}: {evidence.kind}"})
    return out


def _library_root_arg(root: Path | None) -> Path | None:
    """Translate a data root (the service convention) to the library root."""
    if root is None:
        return None
    base = Path(root)
    candidate = base / "component_library"
    if candidate.exists() or not (base / "index.json").exists():
        return candidate
    return base


def search(query: str = "", *, category: str | None = None,
           package: str | None = None, capability: str | None = None,
           limit: int = 20, offset: int = 0,
           root: Path | None = None, kind: str | None = None,
           include_disabled: bool = False) -> dict[str, Any]:
    from mota.library import resolver

    limit = max(1, min(int(limit), 50))
    kind_filter = kind
    if kind_filter is None and category in ("component", "symbol", "footprint"):
        kind_filter = category
    result = resolver.search(query, kind=kind_filter, limit=limit, offset=offset,
                             root=_library_root_arg(root),
                             include_disabled=include_disabled)
    candidates = [_library_candidate(row) for row in result["candidates"]]
    legacy_total = 0
    legacy_assets = result["candidates"]
    if result["total"] == 0:
        # A root that still only holds the pre-package KiCad snapshot stays
        # searchable. Migrated roots never reach this branch, so the same asset
        # is never listed twice.
        candidates, legacy_total, legacy_assets = _legacy_candidates(
            query, kind_filter, limit, offset, root)
    if category and kind_filter is None:
        candidates = [row for row in candidates if row.get("category") == category]
    if package:
        candidates = [row for row in candidates
                      if package.casefold() in str(row.get("package", "")).casefold()]
    if capability:
        candidates = [row for row in candidates
                      if capability in (row.get("capabilities") or {}).get(
                          _CAPABILITY_KEYS.get(capability, capability), False)]
    summary = resolver_status(root)
    total = result["total"] or legacy_total
    return {
        "query": query,
        "total": total,
        "offset": offset,
        "limit": limit,
        "candidates": candidates,
        "catalog_status": "ok",
        "categories": builtin.PACKAGE_CATEGORIES,
        "state": "ok" if candidates else ("no_match" if total == 0 else "ok"),
        "library_assets": legacy_assets,
        "library_total": result["total"] or legacy_total,
        "library_sources": summary.get("sources", []),
        "generation": result.get("generation"),
        "library_counts": summary.get("counts", {}),
    }


def _legacy_candidates(query: str, kind: str | None, limit: int, offset: int,
                       root: Path | None) -> tuple[list[dict[str, Any]], int,
                                                   list[dict[str, Any]]]:
    from mota.library import catalog as legacy_catalog

    try:
        imported = legacy_catalog.search_assets(
            query, root=root, kind=kind, limit=limit, offset=offset)
    except Exception:  # noqa: BLE001 - no legacy snapshot is a normal state
        return [], 0, []
    return ([_legacy_candidate(row) for row in imported["candidates"]],
            int(imported.get("total") or 0), list(imported["candidates"]))


def _legacy_candidate(asset: dict[str, Any]) -> dict[str, Any]:
    kind = str(asset.get("kind", ""))
    parsed = asset.get("status") == "parsed"
    placeable = parsed and kind == "symbol" and int(asset.get("pin_count") or 0) > 0
    return {
        "asset_id": asset["asset_id"],
        "mpn": asset.get("name", ""),
        "description": asset.get("description", ""),
        "category": kind,
        "package": asset.get("library", ""),
        "value": None,
        "kind": kind,
        "namespace": asset.get("source_id", ""),
        "package_version": None,
        "documentation_status": "stub",
        "enabled": True,
        "availability": "available" if parsed else "incomplete",
        "capabilities": {
            "catalog": True, "placeable": placeable, "mapped": False,
            "auto_build_ready": False, "model_3d_exact": False,
            "schematic_render": kind == "symbol",
            "footprint_render": kind == "footprint"},
        "capability_labels": ["Catalog", "Legacy snapshot"]
                             + (["Placeable"] if placeable else []),
        "source_ids": [asset.get("source_id", "")],
        "limits": [], "diagnostics": [], "origin": "legacy_snapshot",
        "legacy_ids": [asset["asset_id"]],
        "pin_count": asset.get("pin_count", 0),
        "pad_count": asset.get("pad_count", 0),
    }


_CAPABILITY_KEYS = {"catalog": "catalog", "placeable": "placeable",
                    "mapped": "mapped", "auto_build_ready": "auto_build_ready"}


def _library_candidate(asset: dict[str, Any]) -> dict[str, Any]:
    """Present a library entry in the shape the panel and tools already use."""
    caps = set(asset.get("capabilities") or [])
    kind = str(asset.get("kind", ""))
    return {
        "asset_id": asset["asset_id"],
        "mpn": asset.get("display_name", ""),
        "description": asset.get("description") or asset.get("summary", ""),
        "category": kind,
        "package": asset.get("library", ""),
        "value": None,
        "kind": kind,
        "namespace": asset.get("namespace", ""),
        "package_version": asset.get("package_version"),
        "documentation_status": asset.get("documentation_status", "stub"),
        "enabled": asset.get("enabled", True),
        "availability": asset.get("availability", "available"),
        "capabilities": {
            "catalog": True,
            "placeable": "schematic_place" in caps,
            "mapped": "pcb_place" in caps,
            "auto_build_ready": False,
            "model_3d_exact": False,
            "schematic_render": kind in ("symbol", "component"),
            "footprint_render": kind in ("footprint", "component"),
        },
        "capability_labels": asset.get("capability_labels") or ["Catalog"],
        "source_ids": [asset.get("namespace", "")],
        "limits": [],
        "diagnostics": [],
        "origin": "library",
        "legacy_ids": asset.get("legacy_ids") or [],
        "pin_count": asset.get("pin_count", 0),
        "pad_count": asset.get("pad_count", 0),
    }


def resolver_status(root: Path | None = None) -> dict[str, Any]:
    from mota.library.registry import library_summary
    from mota.library.providers import provider_catalog

    summary = library_summary(root)
    return {
        "root": summary["root"],
        "generation": summary["generation"],
        "counts": summary["counts"],
        "entries": summary["entries"],
        "enabled": summary["enabled"],
        "disabled": summary["disabled"],
        "missing": summary["missing"],
        "providers": provider_catalog(),
        "sources": [{
            "source_id": "component_library",
            "snapshot_id": summary["generation"],
            "root": summary["root"],
            "counts": summary["counts"],
        }],
    }


def inspect(asset_id: str, *, root: Path | None = None,
            sections: list[str] | None = None) -> dict[str, Any]:
    from mota.library import resolver

    try:
        detail = resolver.inspect(asset_id, root=_library_root_arg(root),
                                  sections=sections)
    except NotFound:
        if asset_id in builtin.PARTS:
            return _inspect_builtin_fallback(asset_id)
        if asset_id.startswith(("sym:", "fp:")):
            return _inspect_legacy_snapshot(asset_id, root)
        raise
    kind = detail["kind"]
    caps = set(detail.get("capabilities") or [])
    pins_out: list[dict[str, Any]] = []
    pin_pad_map: dict[str, list[str]] = {}
    symbol_block = None
    footprint_block = None
    if kind == "component":
        from mota.circuit import parts as circuit_parts
        view = circuit_parts.resolve_part(asset_id)
        pin_pad_map = {key: list(value) for key, value in view.pin_pad_map.items()}
        pins_out = [{"number": pin.number, "name": pin.name,
                     "electrical_type": pin.electrical_type,
                     "pad_numbers": pin_pad_map.get(pin.number, [])}
                    for pin in view.pins]
        if view.symbol_id:
            symbol_block = _symbol_block(view.symbol_id, root)
        if view.footprint_id:
            footprint_block = _footprint_block(view.footprint_id, root)
    elif kind == "symbol":
        for pin in detail.get("pins") or []:
            pins_out.append({"number": str(pin.get("number")), "name": pin.get("name", "~"),
                             "electrical_type": pin.get("electrical_type", "passive"),
                             "unit": pin.get("unit", 1), "pad_numbers": []})
        symbol_block = _symbol_block(asset_id, root)
    elif kind == "footprint":
        footprint_block = _footprint_block(asset_id, root)
    placeable = "schematic_place" in caps and (
        kind != "component" or bool(symbol_block))
    limits = []
    for row in detail.get("parameters") or []:
        if row.get("name") == "supply_voltage_v":
            limits.append({"key": row["name"],
                           "value": f"{row.get('value_min')}-{row.get('value_max')}",
                           "source_id": (row.get("evidence_refs") or [""])[0],
                           "status": row.get("status")})
    return {
        "part": {
            "asset_id": detail["asset_id"], "mpn": detail.get("display_name", ""),
            "description": detail.get("description") or detail.get("summary", ""),
            "category": kind, "package": detail.get("library", ""), "value": None,
            "capabilities": {
                "catalog": True, "placeable": placeable,
                "mapped": "pcb_place" in caps, "auto_build_ready": False,
                "model_3d_exact": False,
                "schematic_render": kind in ("symbol", "component"),
                "footprint_render": kind in ("footprint", "component")},
            "capability_labels": _labels(detail, caps),
            "source_ids": [detail.get("namespace", "")],
            "limits": limits, "diagnostics": [],
        },
        "mpn": detail.get("display_name", ""), "manufacturer": "",
        "pins": pins_out, "pin_pad_map": pin_pad_map,
        "symbol": symbol_block, "footprint": footprint_block, "model": None,
        "sources": [_source_row(row) for row in (detail.get("sources") or [])],
        "evidence": [],
        "diagnostics": [{"code": gap, "detail": "documentation"} for gap in
                        detail.get("documentation_gaps") or []],
        "placeable": placeable,
        "unsupported_reason": None if placeable else "NO_TERMINALS",
        "capability_labels": _labels(detail, caps),
        "asset_id": detail["asset_id"], "kind": kind,
        "namespace": detail.get("namespace", ""),
        "package_version": detail.get("package_version"),
        "package_path": detail.get("package_path"),
        "digest": detail.get("digest"),
        "documentation_status": detail.get("documentation_status"),
        "documentation_gaps": detail.get("documentation_gaps") or [],
        "usage_md": detail.get("usage_md"),
        "parameters": detail.get("parameters") or [],
        "bindings": detail.get("bindings") or {},
        "rules": detail.get("rules") or [],
        "legacy_ids": detail.get("legacy_ids") or [],
        "enabled": detail.get("enabled", True),
        "availability": detail.get("availability", "available"),
        "capabilities": sorted(caps),
        "next_step": _next_step(kind, caps),
    }


def _source_row(row: dict[str, Any]) -> dict[str, Any]:
    """Normalise package provenance into the shape the UI renders."""
    title = row.get("title") or row.get("file") or row.get("source_id") or "source"
    return {
        "source_id": str(row.get("source_id") or row.get("kind") or "source"),
        "kind": str(row.get("kind", "")),
        "title": str(title),
        "url": str(row.get("url") or ""),
        "note": str(row.get("note") or ""),
    }


def _labels(detail: dict[str, Any], caps: set[str]) -> list[str]:
    labels = ["Catalog", detail.get("kind", "").title()]
    if "schematic_place" in caps:
        labels.append("Placeable")
    if "pcb_place" in caps:
        labels.append("PCB placeable")
    if detail.get("documentation_status") in ("partial", "verified"):
        labels.append("Documented")
    if detail.get("enabled") is False:
        labels.append("Disabled")
    return [label for label in labels if label]


def _next_step(kind: str, caps: set[str]) -> str | None:
    if "schematic_place" not in caps:
        return "This package has no placeable terminals."
    if kind == "symbol" and "pcb_place" not in caps:
        return ("Pass this asset id as part_id and a matching fp: footprint id "
                "as footprint_id in add_component; equal pin/pad numbers are mapped.")
    return None


def _symbol_block(symbol_id: str, root: Path | None) -> dict[str, Any] | None:
    from mota.circuit import parts as circuit_parts
    try:
        symbol = circuit_parts.resolve_symbol(symbol_id)
    except NotFound:
        return None
    return {"symbol_id": symbol.symbol_id,
            "body_w_mm": symbol.body_w_nm / 1e6,
            "body_h_mm": symbol.body_h_nm / 1e6,
            "pins": [p.model_dump() for p in symbol.pins],
            "preview_view": "schematic"}


def _footprint_block(footprint_id: str, root: Path | None) -> dict[str, Any] | None:
    from mota.circuit import parts as circuit_parts
    try:
        footprint = circuit_parts.resolve_footprint(footprint_id)
    except NotFound:
        return None
    return {"footprint_id": footprint.footprint_id, "name": footprint.name,
            "pads": [p.model_dump() for p in footprint.pads],
            "courtyard": footprint.courtyard.model_dump(),
            "body_height_mm": footprint.body_height_mm,
            "preview_view": "pcb2d"}


def _inspect_legacy_snapshot(asset_id: str, root: Path | None) -> dict[str, Any]:
    """Compatibility view for a root that still only has the KiCad snapshot."""
    from mota.library import catalog as legacy_catalog

    record = legacy_catalog.inspect_asset(asset_id, root=root)
    kind = str(record.get("kind", ""))
    placeable = kind == "symbol" and bool(record.get("pins"))
    labels = ["Catalog", "Legacy snapshot"] + (["Placeable"] if placeable else [])
    pins_out = [{"number": str(pin.get("number")),
                 "name": str(pin.get("name", "~")),
                 "electrical_type": str(pin.get("electrical_type", "passive")),
                 "unit": int(pin.get("unit", 1)), "pad_numbers": []}
                for pin in record.get("pins", [])]
    return {
        "kind": "library_asset",
        "asset": record,
        "asset_id": asset_id,
        "placeable": placeable,
        "unsupported_reason": (record.get("unsupported_features") or [None])[0],
        "diagnostics": record.get("diagnostics") or [],
        "capability_labels": labels,
        "documentation_status": "stub",
        "documentation_gaps": ["USAGE_MISSING"],
        "pins": pins_out,
        "pin_pad_map": {},
        "note": ("This asset comes from the pre-package KiCad snapshot; run "
                 "`mota library migrate` to publish it as a versioned package."),
    }


def _inspect_builtin_fallback(asset_id: str) -> dict[str, Any]:
    part = builtin.PARTS[asset_id]
    footprint = builtin.FOOTPRINTS[part.footprint_id]
    symbol = builtin.SYMBOLS[part.symbol_id]
    return {
        "part": _part_summary(part), "mpn": part.mpn,
        "manufacturer": part.manufacturer,
        "pins": [{"number": pin.number, "name": pin.name,
                  "electrical_type": pin.electrical_type,
                  "pad_numbers": part.pin_pad_map.get(pin.number, [])}
                 for pin in part.pins],
        "pin_pad_map": part.pin_pad_map,
        "symbol": {"symbol_id": symbol.symbol_id,
                   "body_w_mm": symbol.body_w_nm / 1e6,
                   "body_h_mm": symbol.body_h_nm / 1e6,
                   "pins": [p.model_dump() for p in symbol.pins],
                   "preview_view": "schematic"},
        "footprint": {"footprint_id": footprint.footprint_id, "name": footprint.name,
                      "pads": [p.model_dump() for p in footprint.pads],
                      "courtyard": footprint.courtyard.model_dump(),
                      "preview_view": "pcb2d"},
        "sources": [s.model_dump() for s in builtin.SOURCES
                    if s.source_id in part.source_ids],
        "evidence": [e.model_dump() for e in part.evidence],
        "diagnostics": part_diagnostics(part),
        "placeable": True, "unsupported_reason": None,
        "capability_labels": capability_labels(part),
        "documentation_status": "partial", "documentation_gaps": [],
        "note": "Library packages are not migrated in this root; builtin fallback.",
    }


def _part_summary(part: PartDef) -> dict[str, Any]:
    return {
        "asset_id": part.part_id,
        "mpn": part.mpn,
        "description": part.description,
        "category": part.category,
        "package": part.package,
        "value": part.value_label,
        "capabilities": part.capabilities.model_dump(),
        "capability_labels": capability_labels(part),
        "source_ids": part.source_ids,
        "limits": [limit.model_dump() for limit in part.limits],
        "diagnostics": part_diagnostics(part),
    }


def templates() -> list[dict[str, Any]]:
    return [template_detail(get_template(t.id)) for t in list_templates()]


def template_inspect(template_id: str) -> dict[str, Any]:
    return template_detail(get_template(template_id))
