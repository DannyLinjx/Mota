from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from mota.catalog.templates import get_template
from mota.domain.models import DesignVersion
from mota.domain.units import mm, to_mm
from mota.generation.board import run_build
from mota.projects.store import canonical_design_hash


def test_mm_conversion_is_exact():
    assert mm("1.27") == 1_270_000
    assert mm(0.1) == 100_000
    assert mm(2.54) == 2_540_000
    assert to_mm(mm("3.3")) == 3.3


def test_design_version_is_exclusive():
    with pytest.raises(ValidationError):
        DesignVersion(revision_id="rev_1", draft_id="draft_1", draft_seq=1)
    with pytest.raises(ValidationError):
        DesignVersion()
    with pytest.raises(ValidationError):
        DesignVersion(draft_id="draft_1")
    assert DesignVersion.of_revision("rev_1").key == "rev:rev_1"
    assert DesignVersion.of_draft("draft_x", 3).key == "draft:draft_x:3"


def test_semantic_hash_is_deterministic_across_object_ids():
    recipe = get_template("power_3v3")
    first = run_build(recipe, {})
    second = run_build(recipe, {})
    # Random UUID style identifiers are intentionally different per project; the
    # canonical hash normalizes them by reference/net name.
    assert first.board.tracks
    assert canonical_design_hash(first.requirement, first.circuit, first.board) == \
        canonical_design_hash(second.requirement, second.circuit, second.board)


def test_hashing_changes_when_geometry_changes():
    recipe = get_template("power_3v3")
    first = run_build(recipe, {})
    second = run_build(recipe, {"board_width_mm": 44})
    assert canonical_design_hash(first.requirement, first.circuit, first.board) != \
        canonical_design_hash(second.requirement, second.circuit, second.board)


def test_constraint_rejects_out_of_range_voltage():
    from mota.errors import ConstraintConflict

    recipe = get_template("power_3v3")
    with pytest.raises(ConstraintConflict) as exc:
        recipe.validate_parameters({"input_voltage_v": 12.0})
    assert exc.value.code == "CONSTRAINT_CONFLICT"
    assert "4.75" in str(exc.value)
    with pytest.raises(ConstraintConflict):
        recipe.validate_parameters({"max_load_ma": 80})


def test_template_status_is_candidate_without_verified_runs():
    for template_id in ("power_3v3", "led_timer", "i2c_temperature"):
        recipe = get_template(template_id)
        assert recipe.effective_status in ("candidate", "verified")
        if not recipe.validated_run_ids:
            assert recipe.effective_status == "candidate"
