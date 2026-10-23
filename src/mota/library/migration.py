"""Migrate the existing catalog into contract-shaped component packages.

The migration reads the *real* sources: the builtin definitions in Python and
the parsed KiCad 7.0.11 snapshot objects under the catalog data root. Symbol
packages carry the symbol's own S-expression sliced out of the source file;
footprint packages carry the original `.kicad_mod` bytes. Unsupported and
failed assets stay `metadata_only` with their real status recorded in
`provenance.json`; they are never turned into placeable assets.
"""

from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path
from typing import Any, Callable, Iterable

from mota.catalog import builtin
from mota.catalog.verified import VERIFIED_PARTS
from mota.library import kicad_footprints, kicad_symbols, sexpr
from mota.library.packages import (
    PackageWrite, json_bytes, package_relpath, text_bytes, write_package,
)
from mota.library.registry import Registry, _counts, library_root

SOURCE_VERSION = "7.0.11-r2"
SOURCE_ID = "kicad-7.0.11"
BUILTIN_VERSION = "1.1.0"
BUILTIN_SOURCE_ID = "builtin"

_ELECTRICAL = {
    "input", "output", "bidirectional", "tri_state", "passive", "power_in",
    "power_out", "open_collector", "open_emitter", "no_connect",
    "unspecified", "unknown",
}
_DIRECTION_ANGLE = {"right": 0, "up": 90, "left": 180, "down": 270}


def _electrical(value: Any) -> str:
    text = str(value or "").strip().lower()
    return text if text in _ELECTRICAL else "unknown"


def _root_path(root: Path | None) -> Path:
    return Path(root) if root is not None else library_root()


def _evidence(source_id: str, locator: str) -> dict[str, str]:
    return {"source_id": source_id, "locator": locator or source_id}


def _usage_text(*, name: str, kind: str, summary: str, description: str,
                library: str, pins: int = 0, pads: int = 0,
                extra: list[str] | None = None) -> bytes:
    lines = [f"# {name}", ""]
    if description:
        lines += [description, ""]
    elif summary:
        lines += [summary, ""]
    else:
        lines += ["功能说明待补充（源库没有为该条目提供描述）。", ""]
    lines += [f"- 类型：{kind}"]
    if library:
        lines.append(f"- 源库：{library}")
    if pins:
        lines.append(f"- 引脚/端子：{pins}")
    if pads:
        lines.append(f"- 焊盘：{pads}")
    lines += ["", "## 使用说明", ""]
    if kind == "component":
        lines.append("按封装与引脚映射放置；电气参数只引用本包 `parameters.json` 中的条目。")
    elif kind == "symbol":
        lines.append("符号可与任一焊盘编号一致的封装组合；组合关系记录在 `bindings.json`。")
    else:
        lines.append("封装为几何资源，必须与符号的引脚号匹配后才可放置到 PCB。")
    lines += ["", "## 已知限制", ""]
    lines += [f"- {item}" for item in (extra or ["未声明的检查按 NOT_CHECKED 处理。"])]
    lines.append("")
    return text_bytes("\n".join(lines))


def _rules_doc(asset_id: str, rules: list[dict[str, Any]]) -> bytes:
    return json_bytes({"schema_version": 1, "asset_id": asset_id, "rules": rules})


def _parameters_doc(asset_id: str, rows: list[dict[str, Any]]) -> bytes:
    return json_bytes({"schema_version": 1, "asset_id": asset_id,
                       "parameters": rows})


def _parameter_row(key: str, label: str, classification: str, value: Any,
                   units: str, status: str, evidence: dict[str, str],
                   applies_to: list[str],
                   conditions: list[str] | None = None) -> dict[str, Any]:
    return {"key": key, "label": label, "classification": classification,
            "value": value, "units": units,
            "conditions": conditions or [], "applies_to": applies_to,
            "evidence_refs": [evidence], "status": status}


def _builtin_parameter_rows(part) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    verified = VERIFIED_PARTS.get(part.part_id)
    if verified is not None:
        rows.append(_parameter_row(
            "supply_voltage_v", "推荐工作电压", "recommended_operating",
            {"min": verified.supply_min_v, "max": verified.supply_max_v},
            "V", "verified",
            _evidence("datasheet", verified.source_url), [part.mpn],
            ["datasheet recommended operating conditions"]))
    for limit in getattr(part, "limits", []) or []:
        rows.append(_parameter_row(
            str(limit.key), str(limit.key), "design_rule", limit.value, "",
            "source_reported",
            _evidence(str(limit.source_id or "import_record"), "builtin limits"),
            [part.mpn]))
    return rows


def _builtin_rules(part, asset_id: str) -> list[dict[str, Any]]:
    verified = VERIFIED_PARTS.get(part.part_id)
    if verified is None:
        return []
    return [{
        "rule_id": "supply_voltage_range", "type": "parameter_in_range",
        "severity": "error",
        "message": f"{part.mpn} supply voltage must stay inside the datasheet range.",
        "applies_to": [asset_id],
        "evidence_refs": [_evidence("datasheet", verified.source_url)],
        "parameters": {"parameter_key": "supply_voltage_v",
                       "min": verified.supply_min_v, "max": verified.supply_max_v,
                       "units": "V", "conditions": ["recommended operating"]},
    }]


# ----------------------------------------------------------------- builtin
def _builtin_symbol(writes: list[PackageWrite], symbol_id: str) -> str:
    symbol = builtin.SYMBOLS[symbol_id]
    asset_id = f"mota:symbol:{symbol_id}"
    terminals: list[dict[str, Any]] = []
    occurrences: list[dict[str, Any]] = []
    geometry_pins: list[dict[str, Any]] = []
    seen: set[str] = set()
    evidence = _evidence(BUILTIN_SOURCE_ID, "src/mota/catalog/builtin.py")
    for pin in symbol.pins:
        number = str(pin.number)
        if number not in seen:
            seen.add(number)
            terminals.append({
                "terminal_id": number, "number": number, "name": pin.name,
                "electrical_type": _electrical(pin.electrical_type),
                "status": "source_reported", "evidence_refs": [evidence]})
        occurrences.append({
            "occurrence_id": f"{asset_id}:{number}:1:1",
            "symbol_asset_id": asset_id, "terminal_id": number,
            "pin_number": number, "pin_name": pin.name, "unit": 1,
            "body_style": 1, "hidden": False, "status": "source_reported",
            "evidence_refs": [evidence]})
        geometry_pins.append({
            "number": number, "name": pin.name,
            "electrical_type": _electrical(pin.electrical_type),
            "unit": 1, "body_style": 1,
            "x_mm": pin.x_nm / 1e6, "y_mm": pin.y_nm / 1e6,
            "angle_deg": _DIRECTION_ANGLE.get(pin.direction, 0),
            "length_mm": pin.length_nm / 1e6, "hidden": False})
    pins_doc = {"schema_version": 1, "asset_id": asset_id,
                "status": "source_reported",
                "physical_terminal_count": len(terminals),
                "physical_terminals": terminals,
                "symbol_occurrences": occurrences}
    geometry = {"schema_version": 1, "asset_id": asset_id,
                "units": 1, "body_styles": 1, "extends": None,
                "properties": {"Reference": symbol.reference_prefix},
                "pins": geometry_pins, "datasheet": ""}
    writes.append(PackageWrite(
        asset_id=asset_id, kind="symbol", namespace="mota",
        display_name=symbol_id, package_version=BUILTIN_VERSION,
        summary=f"{symbol_id} symbol from the builtin definitions",
        category="builtin_symbol", tags=["builtin", "symbol"],
        origin={"provider_id": "mota_builtin", "source_id": BUILTIN_SOURCE_ID,
                "source_version": BUILTIN_VERSION},
        payload={"pins.json": json_bytes(pins_doc),
                 "geometry.json": json_bytes(geometry),
                 "provenance.json": json_bytes({
                     "schema_version": 1, "asset_id": asset_id,
                     "source_status": "parsed", "source_id": BUILTIN_SOURCE_ID,
                     "library": "builtin",
                     "source_file": "src/mota/catalog/builtin.py",
                     "source_sha256": "", "units": 1, "body_styles": 1,
                     "extends": None,
                     "properties": {"Reference": symbol.reference_prefix},
                     "models": [], "datasheet": "",
                     "unknown_electrical": False}),
                 "rules.json": _rules_doc(asset_id, []),
                 "usage.md": _usage_text(name=symbol_id, kind="symbol",
                                         summary="", description="",
                                         library="builtin", pins=len(terminals))},
        legacy_ids=[symbol_id, asset_id],
        documentation_level="basic",
        sources=[{"id": BUILTIN_SOURCE_ID, "type": "library_metadata",
                  "title": "Mota builtin symbol definitions",
                  "locator": "src/mota/catalog/builtin.py"}]))
    return asset_id


def _builtin_footprint(writes: list[PackageWrite], footprint_id: str) -> str:
    footprint = builtin.FOOTPRINTS[footprint_id]
    asset_id = f"mota:footprint:{footprint_id}"
    pads = [{
        "number": str(pad.number), "pad_type": pad.pad_type, "shape": pad.shape,
        "x_mm": pad.x_nm / 1e6, "y_mm": pad.y_nm / 1e6,
        "width_mm": pad.w_nm / 1e6, "height_mm": pad.h_nm / 1e6,
        "rotation_deg": 0.0, "drill_mm": pad.drill_nm / 1e6,
        "layers": list(pad.layers)} for pad in footprint.pads]
    writes.append(PackageWrite(
        asset_id=asset_id, kind="footprint", namespace="mota",
        display_name=footprint.name or footprint_id,
        package_version=BUILTIN_VERSION,
        summary=f"{footprint_id} footprint from the builtin definitions",
        category="builtin_footprint", tags=["builtin", "footprint"],
        origin={"provider_id": "mota_builtin", "source_id": BUILTIN_SOURCE_ID,
                "source_version": BUILTIN_VERSION},
        payload={"pads.json": json_bytes({"schema_version": 1,
                                          "asset_id": asset_id,
                                          "pads": pads, "models": []}),
                 "provenance.json": json_bytes({
                     "schema_version": 1, "asset_id": asset_id,
                     "source_status": "parsed", "source_id": BUILTIN_SOURCE_ID,
                     "library": "builtin",
                     "source_file": "src/mota/catalog/builtin.py",
                     "source_sha256": "", "models": [], "datasheet": "",
                     "unknown_electrical": False}),
                 "rules.json": _rules_doc(asset_id, []),
                 "usage.md": _usage_text(name=footprint.name or footprint_id,
                                         kind="footprint", summary="",
                                         description="", library="builtin",
                                         pads=len(pads))},
        legacy_ids=[footprint_id, asset_id],
        documentation_level="basic",
        sources=[{"id": BUILTIN_SOURCE_ID, "type": "library_metadata",
                  "title": "Mota builtin footprint definitions",
                  "locator": "src/mota/catalog/builtin.py"}]))
    return asset_id


def migrate_builtin(root: Path | None = None) -> dict[str, Any]:
    root = _root_path(root)
    base_writes: list[PackageWrite] = []
    symbol_ids = {symbol_id: _builtin_symbol(base_writes, symbol_id)
                  for symbol_id in sorted(builtin.SYMBOLS)}
    footprint_ids = {
        footprint_id: _builtin_footprint(base_writes, footprint_id)
        for footprint_id in sorted(builtin.FOOTPRINTS)}
    written = [write_package(root, write) for write in base_writes]
    refs = {(entry["asset_id"], entry["package_version"]): entry["digest"]
            for entry in written}

    component_writes: list[PackageWrite] = []
    source = _evidence(BUILTIN_SOURCE_ID, "src/mota/catalog/builtin.py")
    for part_id, part in sorted(builtin.PARTS.items()):
        asset_id = f"mota:component:{part_id}"
        symbol_asset = symbol_ids.get(part.symbol_id)
        footprint_asset = footprint_ids.get(part.footprint_id)
        terminals = [{
            "terminal_id": str(pin.number), "number": str(pin.number),
            "name": pin.name, "electrical_type": _electrical(pin.electrical_type),
            "status": "source_reported", "evidence_refs": [source]}
            for pin in part.pins]
        terminal_ids = {row["terminal_id"] for row in terminals}
        mapped = {str(terminal): [str(pad) for pad in pads]
                  for terminal, pads in (part.pin_pad_map or {}).items()}
        unmapped_terminals = sorted(terminal_ids - set(mapped))
        symbol_ref = ({"asset_id": symbol_asset, "version": BUILTIN_VERSION,
                       "digest": refs[(symbol_asset, BUILTIN_VERSION)]}
                      if symbol_asset else None)
        footprint_ref = ({"asset_id": footprint_asset, "version": BUILTIN_VERSION,
                          "digest": refs[(footprint_asset, BUILTIN_VERSION)]}
                         if footprint_asset else None)
        bound = bool(symbol_ref and footprint_ref and mapped
                     and not unmapped_terminals)
        binding_status = "bound" if bound else ("partial" if symbol_ref else "unbound")
        bindings = []
        if symbol_ref:
            bindings.append({
                "binding_id": f"{asset_id}:default",
                "status": binding_status,
                "symbol_ref": symbol_ref, "footprint_ref": footprint_ref,
                "model3d_refs": [],
                "terminal_pad_map": [{"terminal_id": terminal, "pad_numbers": pads}
                                     for terminal, pads in sorted(mapped.items())],
                "unmapped_terminals": unmapped_terminals,
                "unmapped_pads": [],
                "evidence_refs": [source]})
        pins_doc = {"schema_version": 1, "asset_id": asset_id,
                    "status": "source_reported",
                    "physical_terminal_count": len(terminals),
                    "physical_terminals": terminals,
                    "symbol_occurrences": [{
                        "occurrence_id": f"{asset_id}:{row['terminal_id']}:1:1",
                        "symbol_asset_id": symbol_asset or asset_id,
                        "terminal_id": row["terminal_id"],
                        "pin_number": row["terminal_id"],
                        "pin_name": row["name"], "unit": 1, "body_style": 1,
                        "hidden": False, "status": "source_reported",
                        "evidence_refs": [source]} for row in terminals]}
        parameters = _builtin_parameter_rows(part)
        rules = _builtin_rules(part, asset_id)
        dependencies = [{"asset_id": ref["asset_id"], "version": ref["version"],
                         "digest": ref["digest"]}
                        for ref in (symbol_ref, footprint_ref) if ref]
        component_writes.append(PackageWrite(
            asset_id=asset_id, kind="component", namespace="mota",
            display_name=part.mpn, package_version=BUILTIN_VERSION,
            summary=part.description or part.mpn,
            category=str(part.category or "component"),
            tags=[str(part.category or ""), str(part.package or ""), "builtin"],
            origin={"provider_id": "mota_builtin", "source_id": BUILTIN_SOURCE_ID,
                    "source_version": BUILTIN_VERSION},
            payload={
                "parameters.json": _parameters_doc(asset_id, parameters),
                "pins.json": json_bytes(pins_doc),
                "bindings.json": json_bytes({"schema_version": 1,
                                             "asset_id": asset_id,
                                             "status": binding_status,
                                             "bindings": bindings}),
                "rules.json": _rules_doc(asset_id, rules),
                "provenance.json": json_bytes({
                    "schema_version": 1, "asset_id": asset_id,
                    "source_status": "parsed", "source_id": BUILTIN_SOURCE_ID,
                    "library": "builtin",
                    "source_file": "src/mota/catalog/builtin.py",
                    "source_sha256": "",
                    "properties": {"MPN": part.mpn, "Package": str(part.package or "")},
                    "models": [], "datasheet": "",
                    "unknown_electrical": False}),
                "usage.md": _usage_text(
                    name=part.mpn, kind="component", summary=part.description or "",
                    description="", library="builtin", pins=len(terminals),
                    pads=len(mapped),
                    extra=["内置器件定义；采购与停产状态未在本包声明。"]
                    + ([] if parameters else ["缺少手册参数，电气检查为 NOT_CHECKED。"])),
            },
            legacy_ids=[part_id, asset_id], dependencies=dependencies,
            documentation_level="partial" if parameters else "basic",
            sources=[{"id": BUILTIN_SOURCE_ID, "type": "library_metadata",
                      "title": "Mota builtin part definitions",
                      "locator": "src/mota/catalog/builtin.py"}]))
    component_entries = [write_package(root, write) for write in component_writes]

    registry = Registry(root)
    entries = list(registry.entries)
    aliases: dict[str, str] = dict(
        (row["legacy_id"], row["target"]["asset_id"])
        for row in registry.alias_rows())
    for entry in written + component_entries:
        entries.append(entry)
        pkg_dir = Registry(root).entry_dir(entry)
        manifest, _digest = packages_read_manifest(pkg_dir)
        for legacy in manifest.get("legacy_ids") or []:
            aliases[legacy] = entry["asset_id"]
    if written or component_entries:
        registry.publish(entries, aliases=aliases, note="migrate builtin")
    return {"root": str(root), "components": len(component_entries),
            "symbols": len(symbol_ids), "footprints": len(footprint_ids),
            "packages": len(written) + len(component_entries),
            "generation": registry.generation}


def packages_read_manifest(pkg_dir: Path):
    from mota.library.packages import read_manifest
    return read_manifest(pkg_dir)


# --------------------------------------------------------------------- kicad
def _kicad_source_root(root: Path | None) -> Path:
    from mota.library.catalog import _library_base
    return _library_base(root)


def _legacy_rows(old_root: Path) -> tuple[dict[str, Any], list[dict[str, Any]], Path]:
    from mota.workspace.atomic import read_json
    current = read_json(old_root / SOURCE_ID / "current.json", default=None)
    if not current:
        raise FileNotFoundError(f"no kicad snapshot under {old_root}")
    snap = old_root / SOURCE_ID / "snapshots" / current["snapshot_id"]
    manifest = read_json(snap / "manifest.json", default={}) or {}
    rows = read_json(snap / "index.json", default=[]) or []
    return {**current, "manifest": manifest, "snapshot_dir": str(snap)}, rows, old_root


def _stable_asset_id(row: dict[str, Any]) -> str:
    name = row["name"]
    legacy_id = str(row.get("asset_id", ""))
    suffix = ""
    if "#" in legacy_id:
        tail = legacy_id.rsplit("#", 1)[1]
        if tail.isdigit():
            suffix = f"#{tail}"
    return f"kicad:{row['kind']}:{row['library']}/{name}{suffix}"


def _package_exists(root: Path, row: dict[str, Any]) -> bool:
    asset_id = _stable_asset_id(row)
    rel = package_relpath(row["kind"], "kicad", row["name"], asset_id)
    return (root / rel / "versions" / SOURCE_VERSION / "manifest.json").exists()


def _record_payload(record) -> dict[str, Any]:
    return record.model_dump() if hasattr(record, "model_dump") else dict(record)


def _kicad_pins(asset_id: str, record, evidence: dict[str, str]) -> tuple[dict[str, Any], list[dict[str, Any]], int]:
    payload = _record_payload(record)
    pins = payload.get("pins") or []
    terminals: list[dict[str, Any]] = []
    occurrences: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, pin in enumerate(pins):
        number = str(pin.get("number", "") or f"pin{index + 1}")
        unit = int(pin.get("unit") or 1)
        body = int(pin.get("body_style") or 1)
        if number not in seen:
            seen.add(number)
            terminals.append({
                "terminal_id": number, "number": number,
                "name": pin.get("name") or None,
                "electrical_type": _electrical(pin.get("electrical_type")),
                "status": "source_reported", "evidence_refs": [evidence]})
        occurrences.append({
            "occurrence_id": f"{asset_id}:{number}:{unit}:{body}:{index + 1}",
            "symbol_asset_id": asset_id, "terminal_id": number,
            "pin_number": number, "pin_name": pin.get("name") or None,
            "unit": unit, "body_style": body,
            "hidden": bool(pin.get("hidden", False)),
            "status": "source_reported", "evidence_refs": [evidence]})
    doc = {"schema_version": 1, "asset_id": asset_id,
           "status": "source_reported" if pins else "unknown",
           "physical_terminal_count": len(terminals) if pins else None,
           "physical_terminals": terminals,
           "symbol_occurrences": occurrences}
    geometry = {"schema_version": 1, "asset_id": asset_id,
                "units": int(payload.get("units") or 1),
                "body_styles": int(payload.get("body_styles") or 1),
                "extends": payload.get("extends"),
                "properties": payload.get("properties") or {},
                "pins": pins,
                "datasheet": payload.get("datasheet", ""),
                "unknown_electrical": bool(payload.get("unknown_electrical"))}
    return doc, geometry["pins"], len(terminals)


def _kicad_parameters(asset_id: str, row: dict[str, Any]) -> list[dict[str, Any]]:
    verified = VERIFIED_PARTS.get(str(row.get("asset_id")))
    if verified is None:
        return []
    return [_parameter_row(
        "supply_voltage_v", "推荐工作电压", "recommended_operating",
        {"min": verified.supply_min_v, "max": verified.supply_max_v}, "V",
        "verified", _evidence("datasheet", verified.source_url),
        [row["name"]], ["datasheet recommended operating conditions"])]


def _symbol_geometry_text(parsed: kicad_symbols.SymbolFileResult,
                          record, text: str, version: str) -> str | None:
    node = None
    for raw in parsed.raw_symbols:
        if raw.node.offset == getattr(record, "offset", -1):
            node = raw.node
            break
    if node is None:
        for raw in parsed.raw_symbols:
            if raw.name == record.name:
                node = raw.node
                break
    if node is None or not node.end:
        return None
    block = text[node.offset:node.end]
    return (f"(kicad_symbol_lib (version {version}) (generator \"mota\")\n"
            f"  {block}\n)\n")


def _kicad_write(row: dict[str, Any], record, snapshot_id: str, *,
                 text: str = "", symbol_parsed=None,
                 symbol_version: str = "") -> PackageWrite:
    kind = row["kind"]
    name = row["name"]
    library = row["library"]
    asset_id = _stable_asset_id(row)
    status = str(row.get("status", "parsed"))
    parsed = status == "parsed" and record is not None
    source_file = row.get("source_file", "")
    sha = row.get("source_sha256", "")
    evidence = _evidence(SOURCE_ID, f"{source_file}:{row.get('line', 1)}")
    payload: dict[str, bytes] = {}
    pin_count = 0
    pad_count = 0

    if parsed and kind == "symbol":
        pins_doc, geometry_pins, pin_count = _kicad_pins(asset_id, record, evidence)
        payload["pins.json"] = json_bytes(pins_doc)
        payload["geometry.json"] = json_bytes({
            "schema_version": 1, "asset_id": asset_id,
            "units": int(pins_doc["symbol_occurrences"][0]["unit"]) if pins_doc["symbol_occurrences"] else 1,
            "body_styles": 1, "extends": getattr(record, "extends", None),
            "properties": getattr(record, "properties", {}) or {},
            "pins": geometry_pins,
            "datasheet": getattr(record, "datasheet", ""),
            "unknown_electrical": getattr(record, "unknown_electrical", False)})
        if symbol_parsed is not None and text:
            geometry_text = _symbol_geometry_text(symbol_parsed, record, text,
                                                  symbol_version or "20231120")
            if geometry_text:
                payload["symbol.kicad_sym"] = text_bytes(geometry_text)
    if parsed and kind == "footprint":
        data = _record_payload(record)
        pads = data.get("pads") or []
        payload["pads.json"] = json_bytes({
            "schema_version": 1, "asset_id": asset_id, "pads": pads,
            "models": list(data.get("models") or []),
            "attributes": list(data.get("attributes") or [])})
        pad_count = len(pads)
        if text:
            payload["footprint.kicad_mod"] = text_bytes(text)

    parameters = _kicad_parameters(asset_id, row)
    payload["parameters.json"] = _parameters_doc(asset_id, parameters)
    payload["rules.json"] = _rules_doc(asset_id, [])
    payload["provenance.json"] = json_bytes({
        "schema_version": 1, "asset_id": asset_id,
        "source_status": status, "source_id": SOURCE_ID,
        "library": library, "source_file": source_file,
        "source_sha256": sha, "snapshot": snapshot_id,
        "units": int(getattr(record, "units", 1) or 1) if record else 1,
        "body_styles": int(getattr(record, "body_styles", 1) or 1) if record else 1,
        "extends": getattr(record, "extends", None) if record else None,
        "properties": (getattr(record, "properties", {}) or {}) if record else {},
        "models": list(getattr(record, "models", []) or []) if record else [],
        "datasheet": (getattr(record, "datasheet", "") or "") if record else "",
        "unknown_electrical": bool(getattr(record, "unknown_electrical", False))
        if record else False,
    })
    payload["usage.md"] = _usage_text(
        name=name, kind=kind, summary=row.get("description", ""),
        description=row.get("description", ""), library=library,
        pins=pin_count, pads=pad_count,
        extra=(["源库未提供功能描述，功能说明待补充。"]
               if not row.get("description") else [])
        + ([] if parsed else [f"源条目状态为 {status}，仅保留身份与定位，不能放置。"]))
    documentation = "basic" if row.get("description") else "stub"
    if parameters:
        documentation = "partial"
    return PackageWrite(
        asset_id=asset_id, kind=kind, namespace="kicad", display_name=name,
        package_version=SOURCE_VERSION,
        summary=row.get("description") or f"{kind} {name}",
        category=library or kind,
        tags=[library, str(row.get("keywords", "")).strip(), kind],
        origin={"provider_id": "kicad_snapshot", "source_id": SOURCE_ID,
                "source_version": "7.0.11", "snapshot": snapshot_id},
        payload=payload,
        legacy_ids=[str(row["asset_id"]), asset_id],
        documentation_level=documentation,
        missing_fields=[] if row.get("description") else ["description"],
        package_mode="asset" if parsed else "metadata_only",
        license={"expression": "CC-BY-SA-4.0", "status": "known"},
        sources=[{"id": SOURCE_ID, "type": "library_metadata",
                  "title": "KiCad 7.0.11 symbol and footprint libraries",
                  "locator": source_file or SOURCE_ID,
                  "revision": snapshot_id}])


def migrate_kicad(root: Path | None = None, *, old_root: Path | None = None,
                  limit: int | None = None,
                  progress: Callable[[int, int], None] | None = None) -> dict[str, Any]:
    target = _root_path(root)
    old = _kicad_source_root(old_root)
    current, rows, base = _legacy_rows(old)
    snapshot_id = current["snapshot_id"]
    source_manifest = current.get("manifest") or {}
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        groups[str(row.get("source_sha256", ""))].append(row)
    registry = Registry(target)
    entries = list(registry.entries)
    by_key = {(row["asset_id"], row["package_version"]): row for row in entries}
    aliases: dict[str, str] = dict(
        (row["legacy_id"], row["target"]["asset_id"])
        for row in registry.alias_rows())
    stats = {"parsed": 0, "unsupported": 0, "failed": 0, "packages": 0,
             "missing_content": 0, "files": 0, "skipped": 0,
             "geometry_symbols": 0, "geometry_footprints": 0}
    processed = 0
    for sha, group in groups.items():
        if all(_package_exists(target, row) for row in group):
            stats["skipped"] += len(group)
            for row in group:
                aliases[str(row["asset_id"])] = _stable_asset_id(row)
            stats["files"] += 1
            continue
        data = None
        object_path = base / SOURCE_ID / "objects" / sha[:2] / sha
        if object_path.exists():
            data = object_path.read_bytes()
        else:
            raw = (source_manifest.get("source") or {}).get("path", "")
            candidate = Path(raw) / group[0].get("source_file", "") if raw else None
            if candidate and candidate.exists():
                data = candidate.read_bytes()
        text = data.decode("utf-8-sig", errors="replace") if data else ""
        records: dict[str, Any] = {}
        symbol_parsed = None
        version = ""
        if text:
            try:
                if group[0]["kind"] == "symbol":
                    symbol_parsed = kicad_symbols.parse_symbol_file(
                        text, library=group[0]["library"], source_id=SOURCE_ID,
                        source_file=group[0].get("source_file", ""),
                        source_sha256=sha)
                    records = {rec.asset_id: rec
                               for rec in kicad_symbols.build_symbol_assets(symbol_parsed)}
                    try:
                        root_node = sexpr.parse(text)
                        version = sexpr.atom_value(sexpr.child(root_node, "version")) or "20231120"
                    except sexpr.SexprError:
                        version = "20231120"
                else:
                    parsed = kicad_footprints.parse_footprint_file(
                        text, library=group[0]["library"], source_id=SOURCE_ID,
                        source_file=group[0].get("source_file", ""),
                        source_sha256=sha)
                    records = {rec.asset_id: rec for rec in parsed.records}
            except Exception:  # noqa: BLE001 - source file level failure
                records = {}
        for row in group:
            record = records.get(row["asset_id"])
            if record is None:
                stats["missing_content"] += 1
            write = _kicad_write(row, record, snapshot_id, text=text,
                                 symbol_parsed=symbol_parsed,
                                 symbol_version=version)
            try:
                entry = write_package(target, write)
            except Exception as exc:  # noqa: BLE001 - audited per asset
                stats["conflicts"] = stats.get("conflicts", 0) + 1
                stats.setdefault("conflict_examples", []).append(
                    {"asset_id": write.asset_id, "error": str(exc)})
                continue
            entries.append(entry)
            by_key[(entry["asset_id"], entry["package_version"])] = entry
            aliases[str(row["asset_id"])] = entry["asset_id"]
            for legacy in write.legacy_ids:
                aliases[legacy] = entry["asset_id"]
            stats["packages"] += 1
            stats[str(row.get("status", "parsed"))] = \
                stats.get(str(row.get("status", "parsed")), 0) + 1
            if "symbol.kicad_sym" in write.payload:
                stats["geometry_symbols"] += 1
            if "footprint.kicad_mod" in write.payload:
                stats["geometry_footprints"] += 1
            processed += 1
            if limit and processed >= limit:
                break
        stats["files"] += 1
        if progress:
            progress(processed, len(rows))
        if limit and processed >= limit:
            break
    registry.publish(entries, aliases=aliases, note="migrate kicad snapshot")
    stats["generation"] = registry.generation
    stats["root"] = str(target)
    stats["source_snapshot"] = snapshot_id
    stats["source_rows"] = len(rows)
    return stats


def write_report(root: Path | None, payload: dict[str, Any]) -> Path:
    root = _root_path(root)
    path = root / ".catalog" / "migration-report.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=1,
                               sort_keys=True) + "\n", encoding="utf-8")
    return path


def migrate_all(root: Path | None = None, *, limit: int | None = None) -> dict[str, Any]:
    root = _root_path(root)
    builtin_stats = migrate_builtin(root)
    kicad_stats = migrate_kicad(root, limit=limit)
    registry = Registry(root)
    report = {
        "migrated_at": __import__("datetime").datetime.utcnow().isoformat() + "Z",
        "library_root": str(root), "builtin": builtin_stats, "kicad": kicad_stats,
        "baseline": {"builtin": 14, "symbols": 20688, "footprints": 13316,
                     "unsupported": 203, "failed": 1},
        "counts": _counts(registry.entries),
        "active": len(registry.active_entries()),
        "aliases": len(registry.alias_rows()),
    }
    report["report_path"] = str(write_report(root, report))
    return report


# ------------------------------------------------------------------- verify
def verify_migration(seed_path: Path, root: Path | None = None) -> dict[str, Any]:
    """Reconcile the seed inventory with the registry without using it at runtime."""
    seed = json.loads(Path(seed_path).read_text(encoding="utf-8"))
    expected = [row["legacy_id"] for row in seed.get("entries") or []
                if row.get("legacy_id")]
    registry = Registry(_root_path(root))
    aliases = set(registry.aliases())
    entries = registry.entries
    legacy_in_manifests: set[str] = set()
    for pkg_dir, manifest, _digest in packages_iter(root):
        legacy_in_manifests.update(manifest.get("legacy_ids") or [])
    known = aliases | legacy_in_manifests | {row["asset_id"] for row in entries}
    missing = sorted(set(expected) - known)
    extra = sorted(known - set(expected))
    counts = seed.get("counts") or {}
    actual = _counts(entries)
    per_kind = {
        "component": actual.get("component", 0),
        "symbol": actual.get("symbol", 0),
        "footprint": actual.get("footprint", 0),
    }
    metadata_only = sum(1 for row in entries
                        if row.get("capabilities", {}).get("metadata_only"))
    return {
        "seed_entries": len(expected),
        "seed_counts": counts,
        "library_counts": actual,
        "status_counts": {"asset": len(entries) - metadata_only,
                          "metadata_only": metadata_only},
        "missing_legacy_ids": missing[:50],
        "missing_count": len(missing),
        "extra_ids_sample": extra[:50],
        "extra_count": len(extra),
        "counts_match": all(per_kind.get(kind, 0) >= int(value)
                            for kind, value in counts.items()
                            if kind in ("component", "symbol", "footprint")),
        "ok": not missing,
    }


def packages_iter(root: Path | None):
    from mota.library.packages import iter_package_manifests
    return iter_package_manifests(_root_path(root))
