"""Local, offline validation of the MoTA 12 catalog contract.

The schemas vendored next to this module use the reserved `mota.example`
domain only as identifiers. Every remote reference is resolved from the local
files, so validation never reaches the network.

JSON Schema only checks shape. The helpers at the bottom add the semantic
checks the contract calls out explicitly: identity/path alignment, file
completeness, exact references and lifecycle consistency.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterable, Mapping

from jsonschema import Draft202012Validator
from referencing import Registry as ResourceRegistry
from referencing.jsonschema import DRAFT202012

SCHEMA_DIR = Path(__file__).with_name("schemas")

SCHEMA_FILES = (
    "common.schema.json",
    "manifest.schema.json",
    "index.schema.json",
    "parameters.schema.json",
    "pins.schema.json",
    "bindings.schema.json",
    "rules.schema.json",
    "provider.schema.json",
    "project-lock.schema.json",
)

KINDS = ("component", "symbol", "footprint", "model3d", "module", "template")
PLURAL = {"component": "components", "symbol": "symbols",
          "footprint": "footprints", "model3d": "models3d",
          "module": "modules", "template": "templates"}
CAPABILITIES = ("metadata_only", "documentation_read", "parameter_read",
                "schematic_place", "pcb_place", "model3d_view",
                "module_expand", "template_instantiate")
FILE_ROLES = ("readme", "usage", "parameters", "pins", "bindings", "rules",
              "symbol_geometry", "footprint_geometry", "model3d_geometry",
              "module_definition", "template_definition", "native_source",
              "preview", "license", "provenance", "other")


class ContractError(RuntimeError):
    """A document does not satisfy the catalog contract."""


_cache: dict[str, Any] = {}


def _load_schemas() -> dict[str, dict[str, Any]]:
    schemas: dict[str, dict[str, Any]] = {}
    for name in SCHEMA_FILES:
        path = SCHEMA_DIR / name
        if not path.exists():
            raise ContractError(f"contract schema {name} is missing")
        try:
            schemas[name] = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise ContractError(f"contract schema {name} is not valid JSON: {exc}") from exc
    return schemas


def _resources() -> tuple[dict[str, dict[str, Any]],
                          ResourceRegistry]:
    if "schemas" not in _cache:
        schemas = _load_schemas()
        resources = ResourceRegistry()
        for name, schema in schemas.items():
            resource = DRAFT202012.create_resource(schema)
            resources = resources.with_resource(name, resource)
            if schema.get("$id"):
                resources = resources.with_resource(schema["$id"], resource)
        _cache["schemas"] = schemas
        _cache["resources"] = resources
    return _cache["schemas"], _cache["resources"]


def schema(name: str) -> dict[str, Any]:
    schemas, _ = _resources()
    if name not in schemas:
        raise ContractError(f"unknown contract schema {name!r}")
    return schemas[name]


def validator(name: str) -> Draft202012Validator:
    _, resources = _resources()
    return Draft202012Validator(schema(name), registry=resources)


def check_schemas() -> None:
    """Fail loudly if a vendored schema is not itself a valid 2020-12 schema."""
    for name in SCHEMA_FILES:
        Draft202012Validator.check_schema(schema(name))


def validate(name: str, document: Any) -> None:
    """Validate and raise one readable ContractError for all violations."""
    errors = sorted(validator(name).iter_errors(document),
                    key=lambda error: list(error.absolute_path))
    if not errors:
        return
    shown: list[str] = []
    for error in errors[:6]:
        location = "/".join(str(part) for part in error.absolute_path) or "<root>"
        shown.append(f"{location}: {error.message}")
    raise ContractError(f"{name}: " + "; ".join(shown))


# ------------------------------------------------------------------ helpers
def parse_asset_id(asset_id: str) -> tuple[str, str, str]:
    namespace, kind, key = str(asset_id).split(":", 2)
    return namespace, kind, key


def asset_library(asset_id: str) -> str:
    _, _, key = parse_asset_id(asset_id)
    if "/" in key:
        return key.rsplit("/", 1)[0]
    return parse_asset_id(asset_id)[0]


def safe_relpath(relative: str) -> str:
    """Reject absolute paths, `..`, drive letters and backslashes."""
    if not relative or relative.startswith(("/", "\\")) or ":" in relative:
        raise ContractError(f"unsafe contract path {relative!r}")
    parts = Path(relative).parts
    if any(part in ("", ".", "..") for part in parts) or "\\" in relative:
        raise ContractError(f"unsafe contract path {relative!r}")
    return "/".join(parts)


def _persistent_files(pkg_dir: Path) -> list[str]:
    out: list[str] = []
    for path in pkg_dir.rglob("*"):
        if not path.is_file() or path.name == "manifest.json":
            continue
        if any(part.startswith(".") for part in path.relative_to(pkg_dir).parts):
            continue
        out.append(path.relative_to(pkg_dir).as_posix())
    return sorted(out)


def validate_manifest(manifest: Mapping[str, Any], *, pkg_dir: Path | None = None,
                      check_files: bool = True,
                      expected_asset: str | None = None) -> None:
    """Schema plus identity, path and content checks for one package."""
    validate("manifest.schema.json", manifest)
    namespace, kind, key = parse_asset_id(manifest["asset_id"])
    if not key:
        raise ContractError("asset_id key is empty")
    if namespace != manifest["namespace"]:
        raise ContractError(
            f"asset_id namespace {namespace!r} does not match manifest "
            f"namespace {manifest['namespace']!r}")
    if kind != manifest["kind"]:
        raise ContractError(
            f"asset_id kind {kind!r} does not match manifest kind "
            f"{manifest['kind']!r}")
    if expected_asset is not None and manifest["asset_id"] != expected_asset:
        raise ContractError(f"expected {expected_asset}, found {manifest['asset_id']}")

    seen: set[str] = set()
    for row in manifest["files"]:
        path = safe_relpath(row["path"])
        if path in seen:
            raise ContractError(f"file {path} is listed more than once")
        seen.add(path)
        if path == "manifest.json":
            raise ContractError("manifest.json must not list itself")
        if not pkg_dir or not check_files:
            continue
        target = pkg_dir / path
        if not target.is_file():
            raise ContractError(f"listed file {path} is missing")
        import hashlib
        actual = hashlib.sha256(target.read_bytes()).hexdigest()
        if actual != row["sha256"]:
            raise ContractError(f"file {path} does not match its sha256")

    if pkg_dir is not None and check_files:
        unlisted = sorted(set(_persistent_files(pkg_dir)) - seen)
        if unlisted:
            raise ContractError(
                "package contains unlisted persistent files: "
                + ", ".join(unlisted[:5]))

    if manifest["package_mode"] == "metadata_only":
        geometry = {"symbol_geometry", "footprint_geometry", "model3d_geometry",
                    "module_definition", "template_definition"}
        if any(row["role"] in geometry for row in manifest["files"]):
            raise ContractError(
                "metadata_only package must not carry placement geometry")


def validate_index(index: Mapping[str, Any]) -> None:
    validate("index.schema.json", index)
    seen: set[tuple[str, str]] = set()
    for row in index["entries"]:
        key = (row["asset_id"], row["package_version"])
        if key in seen:
            raise ContractError(
                f"index lists {key[0]} {key[1]} more than once")
        seen.add(key)
        safe_relpath(row["relative_path"])


def validate_registry(registry: Mapping[str, Any]) -> None:
    """Registry v1 is part of the contract but has no JSON Schema file."""
    if registry.get("schema_version") != 1:
        raise ContractError("registry schema_version must be 1")
    for key in ("generation", "active", "aliases", "disabled", "removed"):
        if key not in registry:
            raise ContractError(f"registry is missing {key}")
    active_ids: set[str] = set()
    for ref in registry["active"]:
        _check_ref(ref, "active")
        if ref["asset_id"] in active_ids:
            raise ContractError(
                f"registry has more than one active version for {ref['asset_id']}")
        active_ids.add(ref["asset_id"])
    for ref in registry["disabled"] + registry["removed"]:
        _check_ref(ref, "disabled/removed")
    legacy: set[str] = set()
    for row in registry["aliases"]:
        if not row.get("legacy_id"):
            raise ContractError("registry alias has no legacy_id")
        if row["legacy_id"] in legacy:
            raise ContractError(
                f"legacy id {row['legacy_id']!r} maps to more than one target")
        legacy.add(row["legacy_id"])
        _check_ref(row.get("target"), "alias target")


def _check_ref(ref: Any, where: str) -> None:
    if not isinstance(ref, dict):
        raise ContractError(f"{where} entry is not an exact reference")
    if set(ref) != {"asset_id", "version", "digest"}:
        raise ContractError(f"{where} entry must be asset_id/version/digest")
    namespace, kind, key = parse_asset_id(ref["asset_id"])
    if not key or kind not in KINDS:
        raise ContractError(f"{where} has an invalid asset id")
    import re
    if not re.fullmatch(r"[0-9a-f]{64}", str(ref["digest"])):
        raise ContractError(f"{where} digest is not lowercase sha256")


def validate_lock(lock: Mapping[str, Any]) -> None:
    validate("project-lock.schema.json", lock)
    for row in lock["assets"]:
        safe_relpath(row["vendored_path"])
        for dep in row["dependencies"]:
            _check_ref(dep, "lock dependency")
    if lock["closure_status"] == "complete" and lock["unresolved"]:
        raise ContractError("complete closure cannot have unresolved assets")


def validate_provider(descriptor: Mapping[str, Any]) -> None:
    validate("provider.schema.json", descriptor)


def iter_contract_errors(document: Any) -> Iterable[str]:
    for error in validator("manifest.schema.json").iter_errors(document):
        yield "/".join(str(part) for part in error.absolute_path)
