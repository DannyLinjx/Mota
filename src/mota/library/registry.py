"""Catalog index and generation registry in the MoTA 12 contract shape.

`index.json` is a projection generated from the registry and the packages on
disk. It contains only the twelve contract fields per entry, so no caller can
smuggle private state into it. The registry lives at
`.catalog/generations/<g>/registry.json` and records exactly which version is
active, which legacy ids are aliases, and which exact references are disabled
or removed.

Every publish writes a new generation directory first and then replaces the
root index atomically, so a failed publish leaves the previous generation
usable.
"""

from __future__ import annotations

import json
import os
import shutil
from pathlib import Path
from typing import Any, Iterable, Mapping

from mota.library import contract, packages

CATALOG_DIR = ".catalog"
INDEX_FILE = "index.json"
ENTRY_FIELDS = ("asset_id", "kind", "namespace", "display_name", "summary",
                "package_version", "digest", "relative_path", "enabled",
                "availability", "documentation_status", "capabilities")


def library_root() -> Path:
    override = os.environ.get("MOTA_LIBRARY_ROOT")
    if override:
        return Path(override).expanduser()
    return (Path.home() / "Library" / "Application Support" / "Mota"
            / "component_library")


def index_path(root: Path) -> Path:
    return root / INDEX_FILE


def _read_json(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp.write_bytes(packages.json_bytes(payload))
    os.replace(tmp, path)


def _sanitise(entry: Mapping[str, Any]) -> dict[str, Any]:
    return {name: entry[name] for name in ENTRY_FIELDS if name in entry}


def _empty_index() -> dict[str, Any]:
    return {"schema_version": 1, "generation": "g000000",
            "registry_path": f"{CATALOG_DIR}/generations/g000000/registry.json",
            "scope": {"kind": "user", "id": "component_library"},
            "entries": []}


def _empty_registry(generation: str = "g000000") -> dict[str, Any]:
    return {"schema_version": 1, "generation": generation, "active": [],
            "aliases": [], "disabled": [], "removed": []}


def version_key(version: str) -> tuple:
    parts: list[Any] = []
    for chunk in str(version).replace("-", ".").split("."):
        parts.append((0, int(chunk)) if chunk.isdigit() else (1, chunk))
    return tuple(parts)


class Registry:
    def __init__(self, root: Path | None = None) -> None:
        self.root = Path(root) if root else library_root()

    # ------------------------------------------------------------ reading
    def index_file(self) -> Path:
        return index_path(self.root)

    def load(self) -> dict[str, Any]:
        payload = _read_json(self.index_file())
        if payload is None:
            return _empty_index()
        try:
            contract.validate_index(payload)
        except contract.ContractError:
            return _empty_index()
        return payload

    @property
    def generation(self) -> str:
        return self.load()["generation"]

    @property
    def entries(self) -> list[dict[str, Any]]:
        return list(self.load()["entries"])

    def registry_path(self, index: Mapping[str, Any] | None = None) -> Path:
        index = index or self.load()
        return self.root / index["registry_path"]

    def registry_payload(self) -> dict[str, Any]:
        payload = _read_json(self.registry_path()) or _empty_registry(self.generation)
        return payload

    def active_refs(self) -> list[dict[str, str]]:
        return list(self.registry_payload().get("active") or [])

    def active_map(self) -> dict[str, dict[str, str]]:
        return {ref["asset_id"]: ref for ref in self.active_refs()}

    def alias_rows(self) -> list[dict[str, Any]]:
        return list(self.registry_payload().get("aliases") or [])

    def aliases(self) -> dict[str, dict[str, str]]:
        return {row["legacy_id"]: row["target"] for row in self.alias_rows()}

    def disabled_refs(self) -> list[dict[str, str]]:
        return list(self.registry_payload().get("disabled") or [])

    def removed_refs(self) -> list[dict[str, str]]:
        return list(self.registry_payload().get("removed") or [])

    def active_entries(self) -> list[dict[str, Any]]:
        """One row per asset id: the version the registry declares active."""
        active = self.active_map()
        seen: set[str] = set()
        rows: list[dict[str, Any]] = []
        for row in self.entries:
            ref = active.get(row["asset_id"])
            if ref is None:
                continue
            if (row["package_version"] != ref["version"]
                    or row["digest"] != ref["digest"]):
                continue
            if row["asset_id"] in seen:
                continue
            seen.add(row["asset_id"])
            rows.append(row)
        return rows

    def find(self, asset_id: str, version: str | None = None) -> dict[str, Any] | None:
        target = asset_id
        ref = None
        if asset_id not in self.active_map() and asset_id in self.aliases():
            ref = self.aliases()[asset_id]
            target = ref["asset_id"]
        elif asset_id in self.active_map():
            ref = self.active_map()[asset_id]
        if version is None and ref is not None:
            version = ref["version"]
        matches = [row for row in self.entries if row["asset_id"] == target]
        if version:
            matches = [row for row in matches
                       if row["package_version"] == version]
        if not matches:
            return None
        return sorted(matches, key=lambda row: version_key(row["package_version"]))[-1]

    def entry_dir(self, entry: Mapping[str, Any]) -> Path:
        return packages.package_dir(self.root, dict(entry))

    # ------------------------------------------------------------ writing
    def next_generation(self) -> str:
        current = self.load()["generation"]
        number = int(current.lstrip("g") or 0) + 1
        return f"g{number:06d}"

    def publish(self, entries: Iterable[Mapping[str, Any]], *,
                aliases: Mapping[str, Any] | str | None = None,
                disabled: Iterable[Mapping[str, Any]] | None = None,
                removed: Iterable[Mapping[str, Any]] | None = None,
                note: str = "") -> dict[str, Any]:
        rows = [self._normalise(row) for row in entries]
        unique: dict[tuple[str, str], dict[str, Any]] = {}
        for row in rows:
            unique.setdefault((row["asset_id"], row["package_version"]), row)
        rows = sorted(unique.values(), key=lambda row: (row["asset_id"],
                                                        version_key(row["package_version"])))
        self._check_disk(rows)

        previous = self.registry_payload()
        active = self._choose_active(previous, rows)
        alias_rows = self._merge_aliases(previous, aliases, rows)
        disabled_refs = self._refs(disabled) if disabled is not None else [
            {"asset_id": row["asset_id"], "version": row["package_version"],
             "digest": row["digest"]}
            for row in rows if row.get("enabled") is False]
        removed_refs = (self._refs(removed) if removed is not None
                        else list(previous.get("removed") or []))
        present = {(row["asset_id"], row["package_version"]) for row in rows}
        removed_refs = [ref for ref in removed_refs
                        if (ref["asset_id"], ref["version"]) not in present]

        generation = self.next_generation()
        registry = {"schema_version": 1, "generation": generation,
                    "active": active, "aliases": alias_rows,
                    "disabled": disabled_refs, "removed": removed_refs}
        index = {"schema_version": 1, "generation": generation,
                 "registry_path": f"{CATALOG_DIR}/generations/{generation}/registry.json",
                 "scope": {"kind": "user", "id": "component_library"},
                 "entries": [_sanitise(row) for row in rows]}
        contract.validate_registry(registry)
        contract.validate_index(index)

        generation_dir = self.root / CATALOG_DIR / "generations" / generation
        staging = generation_dir.with_name(f".{generation}.{os.getpid()}.tmp")
        shutil.rmtree(staging, ignore_errors=True)
        staging.mkdir(parents=True)
        _write_json(staging / "registry.json", registry)
        if generation_dir.exists():
            shutil.rmtree(generation_dir)
        os.replace(staging, generation_dir)
        _write_json(self.index_file(), index)
        return index

    def _normalise(self, row: Mapping[str, Any]) -> dict[str, Any]:
        entry = _sanitise(row)
        entry.setdefault("enabled", True)
        entry.setdefault("availability", "available")
        entry.setdefault("capabilities",
                        {name: False for name in contract.CAPABILITIES})
        if not isinstance(entry["capabilities"], Mapping):
            entry["capabilities"] = {
                name: name in set(entry["capabilities"] or [])
                for name in contract.CAPABILITIES}
        return entry

    def _check_disk(self, rows: Iterable[Mapping[str, Any]]) -> None:
        for row in rows:
            pkg_dir = self.entry_dir(row)
            manifest_path = pkg_dir / "manifest.json"
            if not manifest_path.exists():
                raise packages.PackageInvalid(
                    f"{row['asset_id']} {row['package_version']} is not on disk")
            import hashlib
            if hashlib.sha256(manifest_path.read_bytes()).hexdigest() != row["digest"]:
                raise packages.PackageInvalid(
                    f"{row['asset_id']} {row['package_version']} digest mismatch")

    def _choose_active(self, previous: Mapping[str, Any],
                       rows: list[dict[str, Any]]) -> list[dict[str, str]]:
        enabled = [row for row in rows if row.get("enabled") is not False]
        by_asset: dict[str, list[dict[str, Any]]] = {}
        for row in enabled:
            by_asset.setdefault(row["asset_id"], []).append(row)
        old = {ref["asset_id"]: ref for ref in previous.get("active") or []}
        active: list[dict[str, str]] = []
        for asset_id, versions in sorted(by_asset.items()):
            chosen = None
            old_ref = old.get(asset_id)
            if old_ref:
                for row in versions:
                    if (row["package_version"] == old_ref.get("version")
                            and row["digest"] == old_ref.get("digest")):
                        chosen = row
                        break
            if chosen is None:
                chosen = sorted(versions,
                                key=lambda row: version_key(row["package_version"]))[-1]
            active.append({"asset_id": asset_id,
                           "version": chosen["package_version"],
                           "digest": chosen["digest"]})
        return active

    def _merge_aliases(self, previous: Mapping[str, Any],
                       aliases: Mapping[str, Any] | str | None,
                       rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        active = {ref["asset_id"]: ref for ref in self._choose_active(previous, rows)}
        out: dict[str, dict[str, Any]] = {}
        for row in previous.get("aliases") or []:
            out[row["legacy_id"]] = dict(row)
        explicit = aliases if isinstance(aliases, Mapping) else {}
        for legacy_id, target in explicit.items():
            if isinstance(target, str):
                ref = active.get(target)
                if ref is None:
                    continue
                target = {"asset_id": target, "version": ref["version"],
                          "digest": ref["digest"]}
            out[legacy_id] = {"legacy_id": legacy_id, "target": dict(target)}
        return [out[key] for key in sorted(out)]

    def _refs(self, rows: Iterable[Mapping[str, Any]]) -> list[dict[str, str]]:
        refs: list[dict[str, str]] = []
        for row in rows:
            target = self.find(row.get("asset_id", ""), row.get("version"))
            if target is None:
                continue
            refs.append({"asset_id": target["asset_id"],
                         "version": target["package_version"],
                         "digest": target["digest"]})
        return refs

    def rebuild_from_disk(self) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for pkg_dir, manifest, digest in packages.iter_package_manifests(self.root):
            out.append(packages.entry_from_manifest(manifest, digest, pkg_dir,
                                                    self.root))
        return out

    def rebuild_aliases(self) -> dict[str, str]:
        out: dict[str, str] = {}
        for _pkg_dir, manifest, _digest in packages.iter_package_manifests(self.root):
            for legacy in manifest.get("legacy_ids") or []:
                out.setdefault(legacy, manifest["asset_id"])
        return out

    def missing_entries(self) -> list[dict[str, Any]]:
        return [row for row in self.entries
                if not (self.entry_dir(row) / "manifest.json").exists()]


def entry_capabilities(row: Mapping[str, Any]) -> list[str]:
    caps = row.get("capabilities")
    if isinstance(caps, Mapping):
        names = [name for name, value in caps.items() if value]
    else:
        names = [name for name in (caps or []) if name in contract.CAPABILITIES]
    return sorted(set(names) | {"catalog"})


def _counts(entries: Iterable[Mapping[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for row in entries:
        kind = row.get("kind", "unknown")
        counts[kind] = counts.get(kind, 0) + 1
    return counts


def library_summary(root: Path | None = None) -> dict[str, Any]:
    registry = Registry(root)
    index = registry.load()
    entries = index["entries"]
    active = registry.active_map()
    active_rows = registry.active_entries()
    return {
        "root": str(registry.root),
        "index_path": str(registry.index_file()),
        "generation": index["generation"],
        "scope": index["scope"],
        "entries": len(entries),
        "active": len(active_rows),
        "enabled": sum(1 for row in entries if row.get("enabled", True)),
        "disabled": sum(1 for row in entries if row.get("enabled") is False),
        "missing": sum(1 for row in entries
                       if not (registry.entry_dir(row) / "manifest.json").exists()),
        "counts": _counts(entries),
        "active_counts": _counts(active_rows),
        "aliases": len(active),
        "legacy_aliases": len(registry.alias_rows()),
    }


def install_packages(root: Path, writes: Iterable[packages.PackageWrite]) -> dict[str, Any]:
    """Write packages and publish their index entries in one generation."""
    registry = Registry(root)
    entries: list[dict[str, Any]] = []
    aliases: dict[str, str] = {}
    for write in writes:
        entry = packages.write_package(root, write)
        entries.append(entry)
        for legacy in write.legacy_ids:
            aliases[legacy] = write.asset_id
    entries = list(registry.entries) + entries
    registry.publish(entries, aliases=aliases)
    return {"generation": registry.generation, "written": len(entries)}


def rebuild(root: Path | None = None) -> dict[str, Any]:
    registry = Registry(root)
    entries = registry.rebuild_from_disk()
    aliases = registry.rebuild_aliases()
    registry.publish(entries, aliases=aliases)
    return {"generation": registry.generation, "entries": len(entries)}
