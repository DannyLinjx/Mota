"""Verified circuit templates and their parameter contracts.

Templates stay `candidate` until a real regression run records validated run
ids in `<id>.verification.json`. Nothing in this module upgrades a template by
assertion.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from mota.domain.models import RequirementSpec, RequirementValue
from mota.errors import ConstraintConflict, NotFound

RESOURCE_ROOT = Path(__file__).resolve().parent.parent / "resources"
TEMPLATE_DIR = RESOURCE_ROOT / "templates"
MODULE_DIR = RESOURCE_ROOT / "modules"

TEMPLATE_IDS = ["power_3v3", "led_timer", "i2c_temperature"]


@dataclass
class TemplatePart:
    reference: str
    part_id: str
    value: str = ""
    module: str = ""
    rotation: int = 0


@dataclass
class TemplateRecipe:
    id: str
    version: int
    title: str
    subtitle: str
    description: str
    status: str
    validation_profile: str
    modules: list[str]
    required_fields: list[str]
    defaults: dict[str, Any]
    constraints: dict[str, dict[str, Any]]
    board: dict[str, Any]
    parts: list[TemplatePart]
    nets: list[dict[str, Any]]
    no_connects: list[str]
    placement: list[dict[str, Any]]
    required_checks: list[str]
    source_ids: list[str]
    raw: dict[str, Any] = field(default_factory=dict)
    validated_run_ids: list[str] = field(default_factory=list)

    @property
    def effective_status(self) -> str:
        return "verified" if self.validated_run_ids else self.status

    def parameter(self, name: str, provided: dict[str, Any] | None = None) -> Any:
        supplied = provided or {}
        if name in supplied and supplied[name] is not None:
            return supplied[name]
        if name in self.defaults:
            return self.defaults[name]
        raise ConstraintConflict(f"Template {self.id} requires parameter '{name}'.",
                                 data={"field": name})

    def validate_parameters(self, params: dict[str, Any]) -> dict[str, Any]:
        merged = dict(self.defaults)
        merged.update({k: v for k, v in (params or {}).items() if v is not None})
        for name, rule in self.constraints.items():
            if name == "layer_count":
                continue
            if name not in merged:
                continue
            value = merged[name]
            if isinstance(value, (int, float)):
                if "min" in rule and value < rule["min"]:
                    raise ConstraintConflict(
                        f"{name}={value} is outside the supported range "
                        f"({rule['min']}..{rule['max']}).",
                        data={"field": name, "value": value, "constraint": rule,
                              "template_id": self.id,
                              "next_steps": ["Choose a supported value",
                                             "Start from parts instead of this template"]})
                if "max" in rule and value > rule["max"]:
                    raise ConstraintConflict(
                        f"{name}={value} is outside the supported range "
                        f"({rule['min']}..{rule['max']}).",
                        data={"field": name, "value": value, "constraint": rule,
                              "template_id": self.id,
                              "next_steps": ["Choose a supported value",
                                             "Start from parts instead of this template"]})
        return merged

    def requirement(self, params: dict[str, Any], input_kind: str = "template",
                    input_text: str | None = None) -> RequirementSpec:
        values: dict[str, RequirementValue] = {}
        for key, value in self.defaults.items():
            values[key] = RequirementValue(value=value, unit=_unit_for(key), origin="template")
        for key, value in (params or {}).items():
            if value is None:
                continue
            values[key] = RequirementValue(value=value, unit=_unit_for(key), origin="user")
        size = {"width_mm": values.get("board_width_mm", RequirementValue(value=self.defaults.get("board_width_mm"))).value,
                "height_mm": values.get("board_height_mm", RequirementValue(value=self.defaults.get("board_height_mm"))).value}
        return RequirementSpec(
            purpose=self.description.strip(),
            inputs=[f"Input: {values.get('input_voltage_v', values.get('supply_voltage_v', RequirementValue(value=''))).value} V"],
            outputs=[self.subtitle],
            supply=str(values.get("input_voltage_v", values.get("supply_voltage_v", RequirementValue(value=""))).value) + " V",
            load=(f"{values['max_load_ma'].value} mA" if "max_load_ma" in values else None),
            interfaces=[f"{values['bus_frequency_khz'].value} kHz 2-wire"
                        if "bus_frequency_khz" in values else "2.54 mm pin header"],
            board_size=size,
            connector_preferences=["2.54 mm through-hole header"],
            assembly_preferences=["through-hole"],
            hard_constraints=[f"{k} in {v}" for k, v in self.constraints.items() if k != "layer_count"],
            assumptions=[f"Generated from verified-recipe candidate {self.id} v{self.version}"],
            sources=[{"source_id": s} for s in self.source_ids],
            parameters=values,
            template_id=self.id, template_version=self.version,
            input_kind=input_kind,  # type: ignore[arg-type]
            input_text=input_text,
        )


def _unit_for(key: str) -> str | None:
    return {"input_voltage_v": "V", "supply_voltage_v": "V", "max_load_ma": "mA",
            "target_frequency_hz": "Hz", "bus_frequency_khz": "kHz",
            "board_width_mm": "mm", "board_height_mm": "mm",
            "timing_cap_uf": "uF", "ra_kohm": "kohm", "rb_kohm": "kohm"}.get(key)


def _load_recipe(template_id: str) -> TemplateRecipe:
    path = TEMPLATE_DIR / f"{template_id}.yaml"
    if not path.exists():
        raise NotFound(f"Template {template_id} not found.", code="TEMPLATE_NOT_FOUND")
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    verification_path = TEMPLATE_DIR / f"{template_id}.verification.json"
    validated: list[str] = []
    if verification_path.exists():
        import json

        data = json.loads(verification_path.read_text(encoding="utf-8"))
        validated = list(data.get("validated_run_ids", []))
    return TemplateRecipe(
        id=raw["id"], version=int(raw.get("version", 1)), title=raw["title"],
        subtitle=raw.get("subtitle", ""), description=raw.get("description", ""),
        status=raw.get("status", "candidate"),
        validation_profile=raw.get("validation_profile", "mota_demo_2layer_v1"),
        modules=list(raw.get("modules", [])),
        required_fields=list(raw.get("required_fields", [])),
        defaults=dict(raw.get("defaults", {})),
        constraints=dict(raw.get("constraints", {})),
        board=dict(raw.get("board", {})),
        parts=[TemplatePart(**p) for p in raw.get("parts", [])],
        nets=list(raw.get("nets", [])),
        no_connects=list(raw.get("no_connects", [])),
        placement=list(raw.get("placement", [])),
        required_checks=list(raw.get("required_checks", [])),
        source_ids=list(raw.get("source_ids", [])),
        raw=raw, validated_run_ids=validated)


_cache: dict[str, TemplateRecipe] = {}


def get_template(template_id: str) -> TemplateRecipe:
    if template_id not in _cache:
        _cache[template_id] = _load_recipe(template_id)
    return _cache[template_id]


def list_templates() -> list[TemplateRecipe]:
    return [get_template(tid) for tid in TEMPLATE_IDS]


def template_summary(recipe: TemplateRecipe) -> dict[str, Any]:
    return {
        "template_id": recipe.id,
        "version": recipe.version,
        "title": recipe.title,
        "subtitle": recipe.subtitle,
        "status": recipe.effective_status,
        "declared_status": recipe.status,
        "validated_run_ids": recipe.validated_run_ids,
        "required_fields": recipe.required_fields,
        "defaults": recipe.defaults,
        "constraints": recipe.constraints,
        "board": recipe.board,
        "component_count": len(recipe.parts),
        "net_count": len(recipe.nets),
        "modules": recipe.modules,
        "source_ids": recipe.source_ids,
        "required_checks": recipe.required_checks,
        "capabilities": {
            "template_build": True,
            "auto_build_ready": True,
            "interactive_edit": True,
            "kicad_export": True,
        },
    }


def template_detail(recipe: TemplateRecipe) -> dict[str, Any]:
    summary = template_summary(recipe)
    summary.update({
        "description": recipe.description,
        "parts": [p.__dict__ for p in recipe.parts],
        "nets": recipe.nets,
        "no_connects": recipe.no_connects,
        "placement": recipe.placement,
        "validation_profile": recipe.validation_profile,
    })
    return summary
