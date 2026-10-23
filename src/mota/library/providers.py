"""Providers: they fetch and convert, they never publish or touch a design.

`native_folder` imports a standard package directory (or a zip of one).
`kicad_snapshot` reuses the existing KiCad importer and converts its snapshot
into library packages. Unimplemented formats are reported as unsupported
instead of returning an empty success.
"""

from __future__ import annotations

import shutil
import tempfile
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from mota.library import contract
from mota.library.packages import PackageInvalid, validate_package


@dataclass
class ProviderInfo:
    name: str
    api_version: int = 1
    adapter_version: str = "1.0.0"
    source_formats: list[str] = field(default_factory=list)
    capabilities: list[str] = field(default_factory=list)
    implemented: bool = True
    note: str = ""
    asset_kinds: list[str] = field(default_factory=list)
    source_types: list[str] = field(default_factory=list)
    geometry_kinds: list[str] = field(default_factory=list)
    documentation_levels: list[str] = field(default_factory=list)
    reference_mode: bool = False
    snapshot_copy: bool = False
    license_metadata: bool = True


@dataclass
class Candidate:
    asset_id: str
    package_version: str
    digest: str
    source_dir: Path
    manifest: dict[str, Any]


class Provider:
    name = "provider"
    implemented = True

    def describe(self) -> ProviderInfo:
        return ProviderInfo(name=self.name, implemented=self.implemented)

    def enumerate(self, source: Path, staging: Path) -> list[Candidate]:
        raise NotImplementedError

    def normalize(self, candidates: list[Candidate]) -> list[Candidate]:
        return candidates


class NativeFolderProvider(Provider):
    """A standard package, a directory of packages, or a zip of either."""

    name = "native_folder"

    def describe(self) -> ProviderInfo:
        return ProviderInfo(
            name=self.name, source_formats=["package_dir", "package_zip"],
            capabilities=["local_import", "dependency_import"],
            asset_kinds=list(contract.KINDS),
            source_types=["local_directory", "local_archive"],
            geometry_kinds=["symbol", "footprint", "model3d"],
            documentation_levels=list(contract.DOC_LEVELS
                                      if hasattr(contract, "DOC_LEVELS")
                                      else ("stub", "basic", "partial", "verified")),
            reference_mode=False, snapshot_copy=False, license_metadata=True,
            note="Reads manifest.json packages; never executes bundled scripts.")

    def enumerate(self, source: Path, staging: Path) -> list[Candidate]:
        if not source.exists():
            raise FileNotFoundError(f"{source} does not exist")
        root = source
        if source.is_file() and zipfile.is_zipfile(source):
            root = staging / "unpacked"
            with zipfile.ZipFile(source) as archive:
                for member in archive.namelist():
                    target = (root / member).resolve()
                    if not str(target).startswith(str(root.resolve())):
                        raise PackageInvalid(f"zip entry escapes the archive: {member}")
                archive.extractall(root)
        candidates: list[Candidate] = []
        manifests = sorted(root.glob("**/manifest.json"))
        for manifest_path in manifests:
            pkg_dir = manifest_path.parent
            manifest, digest = validate_package(pkg_dir)
            candidates.append(Candidate(
                asset_id=manifest["asset_id"],
                package_version=manifest["package_version"],
                digest=digest, source_dir=pkg_dir, manifest=manifest))
        if not candidates:
            raise PackageInvalid(f"no manifest.json package found under {source}")
        return candidates


class KicadSnapshotProvider(Provider):
    """Convert an imported KiCad 7.0.11 snapshot into library packages."""

    name = "kicad_snapshot"

    def describe(self) -> ProviderInfo:
        return ProviderInfo(
            name=self.name, source_formats=["kicad_snapshot", "kicad_sym", "kicad_mod"],
            capabilities=["local_import", "incremental_sync"],
            asset_kinds=["symbol", "footprint"],
            source_types=["local_directory", "local_archive"],
            geometry_kinds=["symbol", "footprint"],
            documentation_levels=["stub", "basic", "partial", "verified"],
            reference_mode=True, snapshot_copy=True, license_metadata=False,
            note=("Reuses the existing importer; output is packages. A raw "
                  ".kicad_sym/.kicad_mod directory must be imported first."))

    def enumerate(self, source: Path, staging: Path) -> list[Candidate]:
        raise PackageInvalid(
            "kicad_snapshot is a migration provider: run "
            "`mota library refresh` to rescan packages or "
            "`mota library import --provider kicad_snapshot --source <snapshot>`.")

    def migrate(self, old_root: Path | None = None) -> dict[str, Any]:
        from mota.library.migration import migrate_kicad
        return migrate_kicad(old_root=old_root)


class UnsupportedProvider(Provider):
    implemented = False

    def __init__(self, name: str, note: str) -> None:
        self.name = name
        self.note = note

    def describe(self) -> ProviderInfo:
        return ProviderInfo(
            name=self.name, implemented=False,
            source_formats=[f"{self.name}_export"],
            capabilities=[], note=self.note,
            asset_kinds=["component"], source_types=["local_archive"],
            geometry_kinds=[], documentation_levels=["stub"],
            reference_mode=False, snapshot_copy=False, license_metadata=False)

    def enumerate(self, source: Path, staging: Path) -> list[Candidate]:
        raise PackageInvalid(f"{self.name} is not implemented: {self.note}")


PROVIDERS: dict[str, Provider] = {
    "native_folder": NativeFolderProvider(),
    "kicad_snapshot": KicadSnapshotProvider(),
    "easyeda_standard": UnsupportedProvider(
        "easyeda_standard", "no parser for the EasyEDA Standard source format"),
    "easyeda_pro": UnsupportedProvider(
        "easyeda_pro", "no parser for EasyEDA Pro export files"),
}


def get_provider(name: str) -> Provider:
    provider = PROVIDERS.get(name)
    if provider is None:
        raise PackageInvalid(f"unknown provider {name!r}")
    return provider


def _descriptor(info: ProviderInfo) -> dict[str, Any]:
    implemented = info.implemented
    descriptor = {
        "schema_version": 1,
        "provider_id": info.name,
        "display_name": info.name.replace("_", " ").title(),
        "version": info.adapter_version,
        "implementation_status": "integrated" if implemented else "contract_only",
        "runtime": {
            "type": "builtin",
            "entrypoint": f"mota.library.providers:{type(PROVIDERS[info.name]).__name__}",
            "protocol": "in_process",
        },
        "operations": {
            "describe": True, "enumerate": True,
            "fetch": True, "normalize": True, "search": False,
        },
        "capabilities": {
            "asset_kinds": info.asset_kinds or ["component"],
            "source_types": info.source_types or ["local_directory"],
            "formats": info.source_formats or ["unknown"],
            "geometry_kinds": info.geometry_kinds,
            "documentation_levels": info.documentation_levels
            or ["stub", "basic", "partial", "verified"],
            "reference_mode": info.reference_mode,
            "snapshot_copy": info.snapshot_copy,
            "license_metadata": info.license_metadata,
        },
        "config_schema_path": None,
    }
    contract.validate_provider(descriptor)
    return descriptor


def provider_catalog() -> list[dict[str, Any]]:
    return [_descriptor(provider.describe())
            for provider in PROVIDERS.values()]


def staging_dir(library_root: Path) -> Path:
    path = library_root / ".staging" / f"tx-{next(tempfile._get_candidate_names())}"
    path.mkdir(parents=True, exist_ok=True)
    return path


def cleanup(path: Path) -> None:
    shutil.rmtree(path, ignore_errors=True)
