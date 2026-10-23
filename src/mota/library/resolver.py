"""One resolver for the panel, CLI, agent tools, placement and validation.

Every lookup goes through the active generation. An index entry is only a
pointer; the package manifest is the source of truth for files, capabilities
and provenance. Legacy `sym:` / `fp:` / part ids resolve through registry
aliases, so old projects and old documents keep working after a rename.

The same resolver also owns the project lock: it copies the exact package
bytes a project uses into the project, records their digests and can restore
them from those copies after the asset has been removed from the library.
"""

from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path
from typing import Any, Mapping

from mota.errors import NotFound
from mota.library import contract, packages
from mota.library.registry import Registry, entry_capabilities

SECTIONS = ("parameters", "pins", "bindings", "rules", "documentation", "sources")


def _root(root: Path | None) -> Path:
    return Path(root) if root is not None else Registry().root


def invalidate() -> None:
    """Kept for callers that used to clear a module cache; reads are live."""
    return None


def _library_of(asset_id: str) -> str:
    try:
        namespace, _kind, key = packages.parse_stable_id(asset_id)
    except ValueError:
        return ""
    return key.rsplit("/", 1)[0] if "/" in key else namespace


def _aliases_reverse(reg: Registry) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for legacy_id, ref in reg.aliases().items():
        out.setdefault(ref["asset_id"], []).append(legacy_id)
    return out


def _entry_dir(reg: Registry, entry: Mapping[str, Any]) -> Path:
    return reg.root / entry["relative_path"] / "versions" / entry["package_version"]


def _enrich(reg: Registry, entry: Mapping[str, Any],
            aliases_rev: Mapping[str, list[str]]) -> dict[str, Any]:
    pkg_dir = _entry_dir(reg, entry)
    manifest: dict[str, Any] | None = None
    digest = entry["digest"]
    try:
        manifest, digest = packages.read_manifest(pkg_dir)
    except packages.PackageInvalid:
        manifest = None
    caps = ({name for name, value in entry["capabilities"].items() if value}
            if manifest is None else
            set(packages.package_capabilities(
                manifest, enabled=entry.get("enabled", True),
                available=entry.get("availability") == "available")))
    row = {
        "asset_id": entry["asset_id"],
        "kind": entry["kind"],
        "namespace": entry["namespace"],
        "display_name": entry["display_name"],
        "summary": entry["summary"],
        "package_version": entry["package_version"],
        "digest": digest,
        "relative_path": entry["relative_path"],
        "library": _library_of(entry["asset_id"]),
        "documentation_status": entry["documentation_status"],
        "availability": entry["availability"],
        "enabled": entry.get("enabled", True),
        "capabilities": sorted(caps | {"catalog"}),
        "legacy_ids": sorted(aliases_rev.get(entry["asset_id"], [])),
        "pin_count": 0,
        "pad_count": 0,
        "description": entry["summary"],
        "keywords": "",
        "package_mode": "metadata_only",
        "package_path": str(pkg_dir),
    }
    if manifest is None:
        return row
    files = packages.manifest_files(manifest)
    geometry = packages.read_named(pkg_dir, manifest, "geometry.json") or {}
    pads = packages.read_named(pkg_dir, manifest, "pads.json") or {}
    pins = packages.read_named(pkg_dir, manifest, "pins.json") or {}
    row.update({
        "summary": manifest["summary"],
        "description": manifest["summary"],
        "keywords": " ".join(manifest.get("tags") or []),
        "package_mode": manifest["package_mode"],
        "documentation_status": manifest["documentation"]["level"],
        "license": manifest["license"],
        "origin": manifest["origin"],
        "tags": manifest.get("tags") or [],
        "dependencies": manifest["dependencies"],
        "sources": manifest["sources"],
        "pin_count": len(geometry.get("pins") or pins.get("symbol_occurrences") or []),
        "pad_count": len(pads.get("pads") or []),
        "legacy_ids": sorted(set(manifest.get("legacy_ids") or [])
                             | set(aliases_rev.get(entry["asset_id"], []))),
        "documentation_gaps": list(
            manifest["documentation"].get("missing_fields") or []),
        "files": {name: list(rows) for name, rows in files.items()},
    })
    return row


def search(query: str = "", *, kind: str | None = None,
           namespace: str | None = None, limit: int = 40, offset: int = 0,
           root: Path | None = None, include_disabled: bool = False) -> dict[str, Any]:
    reg = Registry(_root(root))
    index = reg.load()
    active = reg.active_entries()
    rows = list(active)
    if include_disabled:
        present = {(row["asset_id"], row["package_version"]) for row in rows}
        for row in index["entries"]:
            key = (row["asset_id"], row["package_version"])
            if key in present or row.get("enabled", True):
                continue
            rows.append(row)
    aliases_rev = _aliases_reverse(reg)
    if kind:
        rows = [row for row in rows if row["kind"] == kind]
    if namespace:
        rows = [row for row in rows if row["namespace"] == namespace
                or row["asset_id"].startswith(f"{namespace}:")]
    tokens = [token for token in str(query).casefold().split() if token]
    if tokens:
        scored: list[tuple[int, dict[str, Any]]] = []
        for row in rows:
            haystack = " ".join([
                row["asset_id"], row["display_name"], row["summary"],
                _library_of(row["asset_id"]),
                " ".join(aliases_rev.get(row["asset_id"], [])),
            ]).casefold()
            if all(token in haystack for token in tokens):
                score = 0 if any(row["asset_id"].casefold() == token
                                 for token in tokens) else 1
                scored.append((score, row))
        scored.sort(key=lambda item: (item[0], item[1]["asset_id"]))
        rows = [row for _score, row in scored]
    else:
        rows = sorted(rows, key=lambda row: row["asset_id"])
    total = len(rows)
    page = rows[max(0, offset):max(0, offset) + max(1, limit)]
    candidates = [_enrich(reg, row, aliases_rev) for row in page]
    return {"generation": index["generation"], "total": total,
            "offset": offset, "limit": limit, "candidates": candidates}


def find_entry(asset_id: str, *, root: Path | None = None) -> dict[str, Any] | None:
    return Registry(_root(root)).find(asset_id)


def _require_entry(asset_id: str, reg: Registry) -> dict[str, Any]:
    entry = reg.find(asset_id)
    if entry is None:
        raise NotFound(f"Asset {asset_id} is not in the component library.",
                       code="ASSET_NOT_FOUND")
    return entry


def _adapted_parameters(pkg_dir: Path, manifest: Mapping[str, Any]) -> list[dict[str, Any]]:
    doc = packages.read_role(pkg_dir, manifest, "parameters") or {}
    out: list[dict[str, Any]] = []
    for row in doc.get("parameters") or []:
        value = row.get("value") or {}
        scalar = value.get("nominal")
        out.append({
            "name": row.get("key"),
            "key": row.get("key"),
            "kind": row.get("type"),
            "value": scalar,
            "value_min": value.get("min"),
            "value_max": value.get("max"),
            "unit": row.get("unit"),
            "status": row.get("status"),
            "conditions": row.get("conditions") or {},
            "applies_to": row.get("applies_to") or [],
            "evidence_refs": [ref.get("source_id", "")
                              for ref in row.get("evidence_refs") or []],
        })
    return out


def _adapted_pins(pkg_dir: Path, manifest: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Legacy pin rows for the UI; coordinates come from the geometry file."""
    geometry = packages.read_named(pkg_dir, manifest, "geometry.json") or {}
    if geometry.get("pins"):
        return list(geometry["pins"])
    doc = packages.read_role(pkg_dir, manifest, "pins") or {}
    out: list[dict[str, Any]] = []
    for occurrence in doc.get("symbol_occurrences") or []:
        out.append({
            "number": occurrence.get("pin_number"),
            "name": occurrence.get("pin_name") or "~",
            "electrical_type": "unknown",
            "unit": occurrence.get("unit", 1),
            "body_style": occurrence.get("body_style", 1),
            "hidden": bool(occurrence.get("hidden")),
            "x_mm": 0.0, "y_mm": 0.0, "angle_deg": 0.0, "length_mm": 0.0,
        })
    if out:
        return out
    terminal_electrical = {row["terminal_id"]: row.get("electrical_type", "unknown")
                           for row in doc.get("physical_terminals") or []}
    for terminal in doc.get("physical_terminals") or []:
        out.append({
            "number": terminal.get("number"),
            "name": terminal.get("name") or "~",
            "electrical_type": terminal_electrical.get(
                terminal["terminal_id"], "unknown"),
            "unit": 1, "body_style": 1, "hidden": False,
            "x_mm": 0.0, "y_mm": 0.0, "angle_deg": 0.0, "length_mm": 0.0,
        })
    return out


def _adapted_bindings(pkg_dir: Path, manifest: Mapping[str, Any]) -> dict[str, Any]:
    doc = packages.read_role(pkg_dir, manifest, "bindings") or {}
    if not doc:
        return {}
    chosen = None
    for row in doc.get("bindings") or []:
        if row.get("status") == "bound":
            chosen = row
            break
    if chosen is None and doc.get("bindings"):
        chosen = doc["bindings"][0]
    out: dict[str, Any] = {"status": doc.get("status", "unbound")}
    if not chosen:
        return out
    symbol_ref = chosen.get("symbol_ref") or {}
    footprint_ref = chosen.get("footprint_ref") or {}
    out.update({
        "binding_id": chosen.get("binding_id"),
        "status": chosen.get("status"),
        "symbol": ({"asset_id": symbol_ref.get("asset_id"),
                    "version": symbol_ref.get("version"),
                    "digest": symbol_ref.get("digest")} if symbol_ref else None),
        "footprint": ({"asset_id": footprint_ref.get("asset_id"),
                       "version": footprint_ref.get("version"),
                       "digest": footprint_ref.get("digest")} if footprint_ref else None),
        "pin_pad_map": {row["terminal_id"]: list(row["pad_numbers"])
                        for row in chosen.get("terminal_pad_map") or []},
        "unmapped_terminals": list(chosen.get("unmapped_terminals") or []),
        "unmapped_pads": list(chosen.get("unmapped_pads") or []),
        "evidence_refs": list(chosen.get("evidence_refs") or []),
    })
    return out


def _adapted_rules(pkg_dir: Path, manifest: Mapping[str, Any]) -> list[dict[str, Any]]:
    doc = packages.read_role(pkg_dir, manifest, "rules") or {}
    out: list[dict[str, Any]] = []
    for row in doc.get("rules") or []:
        out.append({
            "rule_id": row.get("rule_id"),
            "type": row.get("type"),
            "severity": row.get("severity"),
            "status": "implemented",
            "message": row.get("message"),
            "parameters": row.get("parameters") or {},
            "evidence_refs": row.get("evidence_refs") or [],
        })
    return out


def inspect(asset_id: str, *, root: Path | None = None,
            sections: list[str] | None = None) -> dict[str, Any]:
    reg = Registry(_root(root))
    entry = _require_entry(asset_id, reg)
    aliases_rev = _aliases_reverse(reg)
    row = _enrich(reg, entry, aliases_rev)
    pkg_dir = Path(row["package_path"])
    manifest, digest = packages.read_manifest(pkg_dir)
    wanted = set(sections or SECTIONS)
    detail: dict[str, Any] = dict(row)
    detail.update({
        "asset_id": entry["asset_id"],
        "digest": digest,
        "package_version": entry["package_version"],
        "package_path": str(pkg_dir),
        "usage_md": packages.read_text_role(pkg_dir, manifest, "usage", "readme"),
        "sources": [dict(row, source_id=row.get("id", ""),
                         kind=row.get("type", ""), url=row.get("url", ""),
                         note=row.get("locator", ""))
                    for row in manifest.get("sources") or []],
        "parameters": _adapted_parameters(pkg_dir, manifest) if "parameters" in wanted else [],
        "pins": _adapted_pins(pkg_dir, manifest) if "pins" in wanted else [],
        "pads": (packages.read_named(pkg_dir, manifest, "pads.json") or {}).get("pads", [])
                if "pins" in wanted else [],
        "bindings": _adapted_bindings(pkg_dir, manifest) if "bindings" in wanted else {},
        "rules": _adapted_rules(pkg_dir, manifest) if "rules" in wanted else [],
    })
    return detail


def record(asset_id: str, *, root: Path | None = None) -> dict[str, Any]:
    """Full record used by symbol/footprint/part resolution and validation."""
    reg = Registry(_root(root))
    entry = _require_entry(asset_id, reg)
    pkg_dir = reg.entry_dir(entry)
    manifest, digest = packages.read_manifest(pkg_dir)
    provenance = packages.read_role(pkg_dir, manifest, "provenance") or {}
    pads_doc = packages.read_named(pkg_dir, manifest, "pads.json") or {}
    pins = _adapted_pins(pkg_dir, manifest)
    bindings = _adapted_bindings(pkg_dir, manifest)
    return {
        "asset_id": entry["asset_id"],
        "kind": manifest["kind"],
        "namespace": manifest["namespace"],
        "name": manifest["display_name"],
        "description": manifest["summary"],
        "keywords": " ".join(manifest.get("tags") or []),
        "source_id": provenance.get("source_id") or manifest["origin"]["source_id"],
        "library": provenance.get("library") or _library_of(entry["asset_id"]),
        "status": provenance.get("source_status", "parsed"),
        "source_file": provenance.get("source_file", ""),
        "source_sha256": provenance.get("source_sha256", ""),
        "units": provenance.get("units", 1),
        "body_styles": provenance.get("body_styles", 1),
        "extends": provenance.get("extends"),
        "properties": provenance.get("properties") or {},
        "models": provenance.get("models") or [],
        "datasheet": provenance.get("datasheet", ""),
        "unknown_electrical": bool(provenance.get("unknown_electrical")),
        "pins": pins,
        "pads": pads_doc.get("pads") or [],
        "bindings": bindings,
        "package_version": entry["package_version"],
        "digest": digest,
        "package_path": str(pkg_dir),
        "documentation_status": manifest["documentation"]["level"],
        "capabilities": entry_capabilities(dict(entry)),
        "enabled": entry.get("enabled", True),
        "availability": entry.get("availability", "available"),
        "legacy_ids": manifest.get("legacy_ids") or [],
        "sources": manifest.get("sources") or [],
        "license": manifest["license"],
        "package_mode": manifest["package_mode"],
    }


def canonical_id(asset_id: str, *, root: Path | None = None) -> str:
    """Map a legacy id onto the stable asset id without changing payloads."""
    if not asset_id:
        return asset_id
    reg = Registry(_root(root))
    alias = reg.aliases().get(asset_id)
    if alias:
        return alias["asset_id"]
    return asset_id


def parameter_rows(asset_id: str, *, root: Path | None = None) -> list[dict[str, Any]]:
    reg = Registry(_root(root))
    entry = reg.find(asset_id)
    if entry is None:
        return []
    pkg_dir = reg.entry_dir(entry)
    manifest, _digest = packages.read_manifest(pkg_dir)
    return _adapted_parameters(pkg_dir, manifest)


def rule_rows(asset_id: str, *, root: Path | None = None) -> list[dict[str, Any]]:
    reg = Registry(_root(root))
    entry = reg.find(asset_id)
    if entry is None:
        return []
    pkg_dir = reg.entry_dir(entry)
    manifest, _digest = packages.read_manifest(pkg_dir)
    return _adapted_rules(pkg_dir, manifest)


# ---------------------------------------------------------------- project lock
def _lock_files(project_dir: Path) -> list[Path]:
    locks = sorted(project_dir.glob("revisions/*/library.lock.json"))
    root_lock = project_dir / "library.lock.json"
    if root_lock.exists():
        locks.append(root_lock)
    return locks


def project_lock(asset_ids, *, root: Path | None = None,
                 project_dir: Path | None = None,
                 project_id: str = "", lock_revision: int = 1) -> dict[str, Any]:
    """Record exact refs and vendor every package's bytes into the project."""
    reg = Registry(_root(root))
    assets: list[dict[str, Any]] = []
    unresolved: list[dict[str, Any]] = []
    seen: set[str] = set()
    for asset_id in asset_ids:
        if not asset_id or asset_id in seen:
            continue
        seen.add(asset_id)
        entry = reg.find(asset_id)
        if entry is None:
            unresolved.append({"asset_id": asset_id, "version": None,
                               "digest": None,
                               "reason": "not present in the library"})
            continue
        pkg_dir = reg.entry_dir(entry)
        manifest, digest = packages.read_manifest(pkg_dir)
        if digest != entry["digest"]:
            unresolved.append({"asset_id": asset_id,
                               "version": entry["package_version"],
                               "digest": digest,
                               "reason": "installed package changed since index"})
            continue
        vendored = f"assets/packages/{digest}"
        if project_dir is not None:
            target = project_dir / vendored
            if not (target / "manifest.json").exists():
                target.parent.mkdir(parents=True, exist_ok=True)
                staging = target.with_name(f".{target.name}.{hashlib.sha256(str(target)).hexdigest()[:8]}.tmp")
                shutil.rmtree(staging, ignore_errors=True)
                shutil.copytree(pkg_dir, staging)
                os_replace(staging, target)
        assets.append({"asset_id": entry["asset_id"],
                       "version": entry["package_version"], "digest": digest,
                       "vendored_path": vendored,
                       "dependencies": list(manifest.get("dependencies") or [])})
    lock = {"schema_version": 1,
            "project_id": project_id or (project_dir.name if project_dir else "project"),
            "lock_revision": max(1, int(lock_revision)),
            "closure_status": "complete" if not unresolved else "incomplete",
            "assets": assets, "unresolved": unresolved,
            "instance_bindings": []}
    contract.validate_lock(lock)
    return lock


def os_replace(staging: Path, target: Path) -> None:
    import os
    if target.exists():
        shutil.rmtree(target)
    os.replace(staging, target)


def ensure_project_assets(project_dir: Path, *, root: Path | None = None) -> dict[str, Any]:
    """Restore every lock entry that is gone or changed, from vendored copies."""
    project_dir = Path(project_dir)
    reg = Registry(_root(root))
    restored: list[str] = []
    for lock_path in _lock_files(project_dir):
        lock = _read_lock(lock_path)
        if not lock:
            continue
        for row in lock.get("assets") or []:
            if _is_installed(reg, row):
                continue
            vendored = project_dir / row["vendored_path"]
            if not (vendored / "manifest.json").exists():
                continue
            manifest, digest = packages.validate_package(vendored)
            if digest != row["digest"]:
                continue
            relative = packages.package_relpath(
                manifest["kind"], manifest["namespace"],
                manifest["display_name"], manifest["asset_id"])
            target = (reg.root / relative / "versions"
                      / manifest["package_version"])
            target.parent.mkdir(parents=True, exist_ok=True)
            staging = target.with_name(
                f".{target.name}.{hashlib.sha256(str(target)).hexdigest()[:8]}.tmp")
            shutil.rmtree(staging, ignore_errors=True)
            shutil.copytree(vendored, staging)
            os_replace(staging, target)
            restored.append(row["asset_id"])
    if restored:
        entries = reg.rebuild_from_disk()
        aliases = reg.rebuild_aliases()
        reg.publish(entries, aliases=aliases, note="restore from project lock")
    return {"restored": sorted(set(restored)), "generation": reg.generation}


def _read_lock(path: Path) -> dict[str, Any] | None:
    try:
        lock = json.loads(path.read_text(encoding="utf-8"))
        contract.validate_lock(lock)
        return lock
    except (OSError, json.JSONDecodeError, contract.ContractError):
        return None


def _is_installed(reg: Registry, row: Mapping[str, Any]) -> bool:
    entry = reg.find(row["asset_id"], row.get("version"))
    if entry is None:
        return False
    if entry["digest"] != row["digest"]:
        return False
    pkg_dir = reg.entry_dir(entry)
    if not (pkg_dir / "manifest.json").exists():
        return False
    from mota.library.packages import sha256_bytes
    return sha256_bytes((pkg_dir / "manifest.json").read_bytes()) == row["digest"]
