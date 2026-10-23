"""Platform rule packs: loading, structural validation and summaries.

The published format is ``platform_packs/schema.json``. Packs are data, not
code: a manufacturing floor, platform requirements and a user profile are
independent files so they can be versioned and replaced without touching the
compiler. Semantic checks (units, selectors, intersections) live in
``mota.constraints.compiler``.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from mota.constraints.compiler import (
    ApplicabilityExpr, ConstraintRule, QuantitySpec, RulePack, RuleSource,
    SUPPORTED_KINDS, TypedSelector,
)
from mota.errors import MotaError, NotFound

DEFAULT_PACK_IDS = (
    "manufacturing_fr4_2layer_v1",
    "platform_stm32g071_min_system_v1",
    "user_demo_strict_v1",
)
PACK_KINDS = {"manufacturing", "platform", "user"}
SOURCE_KINDS = {"datasheet", "appnote", "engineering_spec", "library", "user"}
SEVERITIES = {"error", "warning", "info"}
PACK_KEYS = {"pack_id", "version", "kind", "title", "purpose", "assumptions",
             "sources", "rules"}
RULE_KEYS = {"rule_id", "kind", "hard", "required", "severity", "selector",
             "parameters", "precondition", "sources", "intent"}
SELECTOR_KEYS = {"all", "layers", "net_classes", "nets", "components",
                 "net_scopes"}
PRECONDITION_KEYS = {"min_layer_count", "max_layer_count", "requires_footprint",
                     "requires_stackup", "requires_geometry"}
SOURCE_KEYS = {"source_id", "title", "kind", "url", "note"}


class PlatformPackInvalid(MotaError):
    code = "PLATFORM_PACK_INVALID"


def pack_root() -> Path:
    configured = os.environ.get("MOTA_PLATFORM_PACKS")
    if configured:
        return Path(configured).expanduser().resolve()
    return Path(__file__).resolve().parents[3] / "platform_packs"


def pack_path(pack_id: str) -> Path:
    return pack_root() / f"{pack_id}.json"


def list_pack_ids(root: Path | None = None) -> list[str]:
    directory = root or pack_root()
    if not directory.exists():
        return []
    return sorted(path.stem for path in directory.glob("*.json")
                  if path.name != "schema.json")


def load_pack(pack_id: str, root: Path | None = None) -> RulePack:
    path = (root or pack_root()) / f"{pack_id}.json"
    if not path.exists():
        raise NotFound(f"Platform pack {pack_id!r} not found.", code="PACK_NOT_FOUND")
    return load_pack_file(path)


def load_packs(pack_ids: list[str] | None = None,
               root: Path | None = None) -> list[RulePack]:
    ids = list(pack_ids or DEFAULT_PACK_IDS)
    return [load_pack(pack_id, root) for pack_id in ids]


def default_packs() -> list[RulePack]:
    return load_packs(list(DEFAULT_PACK_IDS))


def load_pack_file(path: Path) -> RulePack:
    try:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise PlatformPackInvalid(
            f"{path}: not valid JSON: {exc}", code="PACK_NOT_JSON") from exc
    issues = validate_pack_document(raw)
    if issues:
        raise PlatformPackInvalid(
            f"{path}: {len(issues)} structural problem(s).",
            data={"issues": issues, "path": str(path)})
    return _build_pack(raw)


def validate_pack_document(raw: Any) -> list[str]:
    """Structural checks mirrored by ``platform_packs/schema.json``."""

    issues: list[str] = []
    if not isinstance(raw, dict):
        return ["pack document must be an object"]
    unknown = sorted(set(raw) - PACK_KEYS)
    if unknown:
        issues.append(f"unknown pack fields: {', '.join(unknown)}")
    for key in ("pack_id", "title", "purpose"):
        value = raw.get(key)
        if not isinstance(value, str) or not value.strip():
            issues.append(f"{key} must be a non-empty string")
    version = raw.get("version")
    if not isinstance(version, int) or isinstance(version, bool) or version < 1:
        issues.append("version must be an integer >= 1")
    if raw.get("kind") not in PACK_KINDS:
        issues.append(f"kind must be one of {sorted(PACK_KINDS)}")
    assumptions = raw.get("assumptions", [])
    if not isinstance(assumptions, list) or not all(
            isinstance(item, str) for item in assumptions):
        issues.append("assumptions must be a list of strings")

    sources = raw.get("sources")
    source_ids: set[str] = set()
    if not isinstance(sources, list) or not sources:
        issues.append("sources must be a non-empty list")
    else:
        for index, source in enumerate(sources):
            where = f"sources[{index}]"
            if not isinstance(source, dict):
                issues.append(f"{where} must be an object")
                continue
            unknown = sorted(set(source) - SOURCE_KEYS)
            if unknown:
                issues.append(f"{where} unknown fields: {', '.join(unknown)}")
            source_id = source.get("source_id")
            if not isinstance(source_id, str) or not source_id:
                issues.append(f"{where}.source_id must be a non-empty string")
            elif source_id in source_ids:
                issues.append(f"{where}.source_id {source_id!r} is duplicated")
            else:
                source_ids.add(source_id)
            if "kind" in source and source["kind"] not in SOURCE_KINDS:
                issues.append(f"{where}.kind must be one of {sorted(SOURCE_KINDS)}")

    rules = raw.get("rules")
    if not isinstance(rules, list) or not rules:
        issues.append("rules must be a non-empty list")
        return issues
    seen: set[str] = set()
    for index, rule in enumerate(rules):
        where = f"rules[{index}]"
        if not isinstance(rule, dict):
            issues.append(f"{where} must be an object")
            continue
        unknown = sorted(set(rule) - RULE_KEYS)
        if unknown:
            issues.append(f"{where} unknown fields: {', '.join(unknown)}")
        rule_id = rule.get("rule_id")
        if not isinstance(rule_id, str) or not rule_id:
            issues.append(f"{where}.rule_id must be a non-empty string")
        elif rule_id in seen:
            issues.append(f"{where}.rule_id {rule_id!r} is duplicated")
        else:
            seen.add(rule_id)
        if rule.get("kind") not in SUPPORTED_KINDS:
            issues.append(f"{where}.kind is not a supported rule kind")
        for flag in ("hard", "required"):
            if flag in rule and not isinstance(rule[flag], bool):
                issues.append(f"{where}.{flag} must be a boolean")
        if "severity" in rule and rule["severity"] not in SEVERITIES:
            issues.append(f"{where}.severity must be one of {sorted(SEVERITIES)}")
        issues.extend(_selector_issues(rule.get("selector"), where))
        issues.extend(_parameter_issues(rule.get("parameters"), where))
        issues.extend(_precondition_issues(rule.get("precondition"), where))
        rule_sources = rule.get("sources")
        if not isinstance(rule_sources, list) or not rule_sources or not all(
                isinstance(item, str) and item for item in rule_sources):
            issues.append(f"{where}.sources must be a non-empty list of source ids")
        elif source_ids:
            missing = sorted(set(rule_sources) - source_ids)
            if missing:
                issues.append(
                    f"{where}.sources reference undeclared ids: {', '.join(missing)}")
        if "intent" in rule and not isinstance(rule["intent"], str):
            issues.append(f"{where}.intent must be a string")
    return issues


def _selector_issues(selector: Any, where: str) -> list[str]:
    issues: list[str] = []
    if not isinstance(selector, dict):
        return [f"{where}.selector must be an object"]
    unknown = sorted(set(selector) - SELECTOR_KEYS)
    if unknown:
        issues.append(f"{where}.selector unknown fields: {', '.join(unknown)}")
    if not isinstance(selector.get("all", False), bool):
        issues.append(f"{where}.selector.all must be a boolean")
    for key in ("layers", "net_classes", "nets", "components", "net_scopes"):
        value = selector.get(key, [])
        if not isinstance(value, list) or not all(
                isinstance(item, str) and item for item in value):
            issues.append(f"{where}.selector.{key} must be a list of strings")
    if not selector.get("all", False) and not any(
            selector.get(key) for key in
            ("layers", "net_classes", "nets", "components", "net_scopes")):
        issues.append(f"{where}.selector must set all=true or at least one field")
    if selector.get("all", False) and any(
            selector.get(key) for key in
            ("layers", "net_classes", "nets", "components", "net_scopes")):
        issues.append(f"{where}.selector cannot combine all=true with specific fields")
    return issues


def _parameter_issues(parameters: Any, where: str) -> list[str]:
    if not isinstance(parameters, dict) or not parameters:
        return [f"{where}.parameters must be a non-empty object"]
    issues: list[str] = []
    for name, spec in parameters.items():
        if not isinstance(name, str) or not name:
            issues.append(f"{where}.parameters has an empty name")
            continue
        if not isinstance(spec, dict):
            issues.append(f"{where}.parameters.{name} must be an object")
            continue
        unknown = sorted(set(spec) - {"value", "unit"})
        if unknown:
            issues.append(
                f"{where}.parameters.{name} unknown fields: {', '.join(unknown)}")
        value = spec.get("value")
        if not isinstance(value, (int, float)) or isinstance(value, bool):
            issues.append(f"{where}.parameters.{name}.value must be a number")
        elif value < 0:
            issues.append(f"{where}.parameters.{name}.value must be >= 0")
        if not isinstance(spec.get("unit"), str) or not spec.get("unit"):
            issues.append(
                f"{where}.parameters.{name}.unit must be a non-empty string")
    return issues


def _precondition_issues(precondition: Any, where: str) -> list[str]:
    if precondition is None:
        return []
    if not isinstance(precondition, dict):
        return [f"{where}.precondition must be an object"]
    issues: list[str] = []
    unknown = sorted(set(precondition) - PRECONDITION_KEYS)
    if unknown:
        issues.append(
            f"{where}.precondition unknown fields: {', '.join(unknown)}")
    for key in ("min_layer_count", "max_layer_count"):
        value = precondition.get(key)
        if value is not None and (not isinstance(value, int)
                                  or isinstance(value, bool) or value < 1):
            issues.append(f"{where}.precondition.{key} must be an integer >= 1")
    for key in ("requires_footprint", "requires_stackup", "requires_geometry"):
        if key in precondition and not isinstance(precondition[key], bool):
            issues.append(f"{where}.precondition.{key} must be a boolean")
    low = precondition.get("min_layer_count")
    high = precondition.get("max_layer_count")
    if isinstance(low, int) and isinstance(high, int) and low > high:
        issues.append(
            f"{where}.precondition.min_layer_count exceeds max_layer_count")
    return issues


def _build_pack(raw: dict[str, Any]) -> RulePack:
    sources = [RuleSource(
        source_id=item["source_id"], title=item.get("title", ""),
        kind=item.get("kind", "engineering_spec"), note=item.get("note", ""))
        for item in raw["sources"]]
    rules = []
    for item in raw["rules"]:
        precondition = item.get("precondition", {})
        rules.append(ConstraintRule(
            rule_id=item["rule_id"], kind=item["kind"],
            hard=bool(item.get("hard", True)),
            required=bool(item.get("required", True)),
            severity=item.get("severity", "error"),
            selector=TypedSelector(
                all=bool(item["selector"].get("all", False)),
                layers=list(item["selector"].get("layers", [])),
                net_classes=list(item["selector"].get("net_classes", [])),
                nets=list(item["selector"].get("nets", [])),
                components=list(item["selector"].get("components", [])),
                net_scopes=list(item["selector"].get("net_scopes", []))),
            parameters={
                name: QuantitySpec(value=float(spec["value"]),
                                   unit=str(spec["unit"]))
                for name, spec in item["parameters"].items()},
            precondition=ApplicabilityExpr(
                min_layer_count=precondition.get("min_layer_count"),
                max_layer_count=precondition.get("max_layer_count"),
                requires_footprint=bool(precondition.get("requires_footprint", False)),
                requires_stackup=bool(precondition.get("requires_stackup", False)),
                requires_geometry=bool(precondition.get("requires_geometry", False))),
            sources=list(item["sources"]),
            intent=item.get("intent", "")))
    return RulePack(
        pack_id=raw["pack_id"], version=int(raw["version"]),
        kind=raw["kind"], title=raw["title"], purpose=raw["purpose"],
        sources=sources, assumptions=list(raw.get("assumptions", [])),
        rules=rules)


def pack_summary(pack: RulePack) -> dict[str, Any]:
    return {
        "pack_id": pack.pack_id,
        "version": pack.version,
        "kind": pack.kind,
        "title": pack.title,
        "purpose": pack.purpose,
        "rule_count": len(pack.rules),
        "rule_kinds": sorted({rule.kind for rule in pack.rules}),
        "sources": [source.source_id for source in pack.sources],
        "assumptions": list(pack.assumptions),
    }
