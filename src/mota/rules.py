"""Versioned manufacturing rule profiles shared by layout, routing and DRC."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml

from mota.domain.units import mm

RULES_DIR = Path(__file__).parent / "resources" / "rules"
DEFAULT_PROFILE = "mota_demo_2layer_v1"


@dataclass(frozen=True)
class RuleProfile:
    rule_profile_id: str
    version: int
    description: str
    clearance_nm: int
    signal_width_nm: int
    power_width_nm: int
    edge_nm: int
    hole_clearance_nm: int
    via_outer_nm: int
    via_drill_nm: int
    via_ring_nm: int
    grid_nm: int
    budget_seconds: float
    source: str = ""


def load_rule_profile(profile_id: str | None = None) -> RuleProfile:
    """Load one profile by id; unknown ids fall back to the demo profile."""
    wanted = (profile_id or DEFAULT_PROFILE).strip() or DEFAULT_PROFILE
    path = RULES_DIR / f"{wanted}.yaml"
    if not path.exists():
        path = RULES_DIR / f"{DEFAULT_PROFILE}.yaml"
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return RuleProfile(
        rule_profile_id=str(data.get("rule_profile_id", path.stem)),
        version=int(data.get("version", 1)),
        description=str(data.get("description", "")),
        clearance_nm=mm(float(data.get("clearance_mm", 0.25))),
        signal_width_nm=mm(float(data.get("track_width_signal_mm", 0.30))),
        power_width_nm=mm(float(data.get("track_width_power_mm", 0.60))),
        edge_nm=mm(float(data.get("board_edge_clearance_mm", 0.50))),
        hole_clearance_nm=mm(float(data.get("npth_copper_clearance_mm", 0.25))),
        via_outer_nm=mm(float(data.get("via_outer_mm", 0.80))),
        via_drill_nm=mm(float(data.get("via_drill_mm", 0.40))),
        via_ring_nm=mm(float(data.get("via_annular_ring_mm", 0.20))),
        grid_nm=mm(float(data.get("routing_grid_mm", 0.127))),
        budget_seconds=float(data.get("routing_budget_seconds", 30)),
        source=str(data.get("source", "")))
