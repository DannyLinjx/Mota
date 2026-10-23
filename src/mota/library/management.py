"""Install, enable, disable, remove, refresh and garbage-collect packages.

The management layer is the only writer. A provider may fetch or convert; it
never publishes, edits the index or touches a design. Installation stages and
validates every candidate before one atomic generation is published, so a
conflict or an interrupted import leaves the current generation untouched.
"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

from mota.library import contract, packages
from mota.library.packages import PackageConflict, PackageInvalid
from mota.library.providers import (
    PROVIDERS, Candidate, cleanup, get_provider, staging_dir,
)
from mota.library.registry import Registry, library_root, library_summary, version_key


def _dep_digests(registry: Registry) -> dict[tuple[str, str], str]:
    return {(row["asset_id"], row["package_version"]): row["digest"]
            for row in registry.entries}


def _dependencies_available(manifest: dict[str, Any],
                            known: dict[tuple[str, str], str]) -> bool:
    for dep in manifest.get("dependencies") or []:
        if known.get((dep["asset_id"], dep["version"])) != dep["digest"]:
            return False
    return True


def install(source: Path, *, provider: str = "native_folder",
            dry_run: bool = False, root: Path | None = None) -> dict[str, Any]:
    impl = get_provider(provider)
    if not impl.implemented:
        raise PackageInvalid(f"{provider} is not implemented: "
                             f"{impl.describe().note}")
    root = Path(root) if root else library_root()
    registry = Registry(root)
    staging = staging_dir(root)
    try:
        candidates = impl.normalize(impl.enumerate(Path(source), staging))
        if not candidates:
            raise PackageInvalid(f"{provider} returned no candidates")
        existing = _dep_digests(registry)
        batch = {(row.asset_id, row.package_version): row.digest
                 for row in candidates}
        known = {**existing, **batch}
        installed: list[dict[str, Any]] = []
        rejected: list[dict[str, Any]] = []
        index_rows: list[dict[str, Any]] = list(registry.entries)
        aliases: dict[str, str] = {}
        by_key = {(row["asset_id"], row["package_version"]): row
                  for row in index_rows}
        for candidate in _ordered(candidates):
            key = (candidate.asset_id, candidate.package_version)
            current = by_key.get(key)
            if current is not None:
                if current["digest"] == candidate.digest:
                    installed.append({"asset_id": candidate.asset_id,
                                      "version": candidate.package_version,
                                      "digest": candidate.digest,
                                      "note": "already installed"})
                else:
                    rejected.append({
                        "asset_id": candidate.asset_id,
                        "version": candidate.package_version,
                        "error": "same asset id and version with a different digest"})
                continue
            try:
                entry = _install_one(root, candidate, by_key, known, dry_run)
            except (PackageConflict, PackageInvalid, ValueError) as exc:
                rejected.append({"asset_id": candidate.asset_id,
                                 "version": candidate.package_version,
                                 "error": str(exc)})
                continue
            if entry is None:
                installed.append({"asset_id": candidate.asset_id,
                                  "version": candidate.package_version,
                                  "digest": candidate.digest, "dry_run": True})
                continue
            by_key[(entry["asset_id"], entry["package_version"])] = entry
            index_rows.append(entry)
            for legacy in candidate.manifest.get("legacy_ids") or []:
                aliases[legacy] = entry["asset_id"]
            installed.append({"asset_id": entry["asset_id"],
                              "version": entry["package_version"],
                              "digest": entry["digest"],
                              "availability": entry["availability"]})
        generation = registry.generation
        if installed and not rejected and not dry_run:
            registry.publish(index_rows, aliases=aliases,
                             note=f"install via {provider}")
            generation = registry.generation
        return {"provider": provider, "generation": generation,
                "installed": installed, "rejected": rejected,
                "counts": library_summary(root)["counts"]}
    finally:
        cleanup(staging)


def _ordered(candidates: list[Candidate]) -> list[Candidate]:
    """Install dependencies before the assets that reference them."""
    remaining = list(candidates)
    ordered: list[Candidate] = []
    placed: set[str] = set()
    while remaining:
        progress = False
        rest: list[Candidate] = []
        for candidate in remaining:
            deps = {dep["asset_id"]
                    for dep in candidate.manifest.get("dependencies") or []}
            if deps <= placed:
                ordered.append(candidate)
                placed.add(candidate.asset_id)
                progress = True
            else:
                rest.append(candidate)
        if not progress:
            raise PackageInvalid(
                "package dependencies form a cycle or point outside the batch: "
                + ", ".join(row.asset_id for row in remaining[:5]))
        remaining = rest
    return ordered


def _install_one(root: Path, candidate: Candidate,
                 by_key: dict[tuple[str, str], dict[str, Any]],
                 known: dict[tuple[str, str], str],
                 dry_run: bool) -> dict[str, Any] | None:
    manifest, digest = packages.validate_package(candidate.source_dir)
    if manifest["asset_id"] != candidate.asset_id:
        raise PackageInvalid("candidate identity changed while installing")
    key = (manifest["asset_id"], manifest["package_version"])
    current = by_key.get(key)
    if current is not None:
        if current["digest"] == digest:
            raise PackageConflict(
                f"{key[0]} {key[1]} is already installed with identical bytes")
        raise PackageConflict(
            f"{key[0]} {key[1]} already exists with a different digest")
    if dry_run:
        return None
    relative = packages.package_relpath(
        manifest["kind"], manifest["namespace"],
        manifest["display_name"], manifest["asset_id"])
    target = root / relative / "versions" / manifest["package_version"]
    target.parent.mkdir(parents=True, exist_ok=True)
    staging = target.with_name(f".staging-{digest[:10]}")
    shutil.rmtree(staging, ignore_errors=True)
    shutil.copytree(candidate.source_dir, staging)
    if target.exists():
        shutil.rmtree(staging, ignore_errors=True)
        raise PackageConflict(f"{key[0]} {key[1]} appeared while installing")
    staging.replace(target)
    packages.validate_package(target)
    availability = "available" if _dependencies_available(manifest, known) else "incomplete"
    return packages.entry_from_manifest(manifest, digest, target, root,
                                        availability=availability)


def set_enabled(asset_id: str, enabled: bool, *,
                root: Path | None = None) -> dict[str, Any]:
    registry = Registry(root)
    entry = registry.find(asset_id)
    if entry is None:
        raise PackageInvalid(f"{asset_id} is not installed")
    target = entry["asset_id"]
    rows = []
    for row in registry.entries:
        row = dict(row)
        if row["asset_id"] == target:
            row["enabled"] = enabled
        rows.append(row)
    registry.publish(rows, note=("enable" if enabled else "disable") + f" {target}")
    return {"asset_id": target, "enabled": enabled,
            "generation": registry.generation}


def remove(asset_id: str, *, version: str | None = None,
           root: Path | None = None) -> dict[str, Any]:
    registry = Registry(root)
    entry = registry.find(asset_id, version)
    if entry is None:
        raise PackageInvalid(f"{asset_id} is not installed")
    target = entry["asset_id"]
    doomed = {entry["package_version"]}
    if version is None:
        doomed = {row["package_version"] for row in registry.entries
                  if row["asset_id"] == target}
    blocked = _dependents(registry, target, doomed)
    if blocked:
        return {"removed": False, "blocked_by": sorted(blocked),
                "generation": registry.generation}
    rows = [row for row in registry.entries
            if not (row["asset_id"] == target
                    and row["package_version"] in doomed)]
    removed = [{"asset_id": entry["asset_id"], "version": stale,
                "digest": entry["digest"]}
               for stale in sorted(doomed)]
    registry.publish(rows, removed=removed, note=f"remove {target}")
    for stale in doomed:
        pkg_dir = registry.root / entry["relative_path"] / "versions" / stale
        shutil.rmtree(pkg_dir, ignore_errors=True)
    _prune_empty(registry.root / entry["relative_path"] / "versions")
    return {"removed": True, "asset_id": target, "versions": sorted(doomed),
            "generation": registry.generation}


def _dependents(registry: Registry, asset_id: str,
                versions: set[str]) -> set[str]:
    out: set[str] = set()
    for row in registry.entries:
        if row["asset_id"] == asset_id:
            continue
        pkg_dir = registry.entry_dir(row)
        try:
            manifest, _digest = packages.read_manifest(pkg_dir)
        except packages.PackageInvalid:
            continue
        for dep in manifest.get("dependencies") or []:
            if dep["asset_id"] == asset_id and dep["version"] in versions:
                out.add(row["asset_id"])
    return out


def _prune_empty(path: Path) -> None:
    for candidate in (path, path.parent, path.parent.parent, path.parent.parent.parent):
        try:
            candidate.rmdir()
        except OSError:
            break


def refresh(root: Path | None = None) -> dict[str, Any]:
    registry = Registry(root)
    before = len(registry.entries)
    missing = [row["asset_id"] for row in registry.missing_entries()]
    entries = registry.rebuild_from_disk()
    aliases = registry.rebuild_aliases()
    registry.publish(entries, aliases=aliases, note="refresh")
    return {"generation": registry.generation, "before": before,
            "after": len(entries), "removed_missing": missing}


def gc(*, dry_run: bool = True, root: Path | None = None) -> dict[str, Any]:
    registry = Registry(root)
    live: set[str] = set()
    for row in registry.entries:
        live.add(row["digest"])
    generations = registry.root / ".catalog" / "generations"
    if generations.exists():
        for path in generations.glob("*/registry.json"):
            try:
                import json
                payload = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            for ref in payload.get("active") or []:
                live.add(ref.get("digest", ""))
            for ref in payload.get("removed") or []:
                live.add(ref.get("digest", ""))
    orphans: list[Path] = []
    for pkg_dir, _manifest, digest in packages.iter_package_manifests(registry.root):
        if digest not in live:
            orphans.append(pkg_dir)
    if not dry_run:
        for pkg_dir in orphans:
            shutil.rmtree(pkg_dir, ignore_errors=True)
            _prune_empty(pkg_dir.parent)
    return {"count": len(orphans), "dry_run": dry_run,
            "paths": [str(path) for path in orphans[:50]]}


def provider_status() -> list[dict[str, Any]]:
    from mota.library.providers import provider_catalog
    return provider_catalog()
