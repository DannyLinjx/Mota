"""C0: the three v1 circuits stay the regression baseline through v2 migration."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from mota.domain.models import DesignVersion
from mota.engine_v2 import (
    JobRequest, JobResult, MigrationError, migrate_revision, migrate_snapshot,
    read_migration, rollback_revision, semantic_hash, semantic_signature,
)
from mota.engine_v2.contracts import JobBudget
from mota.projects.store import ProjectStore
from mota.workspace.atomic import sha256_file
from tests.conftest import build_template, make_user, new_project

BASELINE_DIR = Path(__file__).resolve().parents[2] / "docs" / "engine-v2" / "baseline"
TEMPLATES = ["power_3v3", "led_timer", "i2c_temperature"]
SOURCE_FILES = ("revision.json", "circuit.json", "schematic.json", "board.json",
                "checks.json")


def _baseline(template_id: str) -> dict:
    return json.loads((BASELINE_DIR / f"{template_id}.json").read_text(encoding="utf-8"))


def _snapshot_payload(snapshot) -> dict:
    return {"requirement": snapshot.requirement, "circuit": snapshot.circuit,
            "schematic": snapshot.schematic, "board": snapshot.board,
            "checks": snapshot.checks}


def _build(service, username: str, template_id: str):
    session = make_user(service, username)
    project, _ = new_project(service, session, f"C0 {template_id}")
    result = build_template(service, session, project, template_id)
    revision_id = result["run"]["committed_revision_id"]
    store = ProjectStore(session.scope(), Path(service.root))
    snapshot = store.load_revision(project, revision_id)
    return session, project, revision_id, store, snapshot


@pytest.mark.parametrize("template_id", TEMPLATES)
def test_baseline_semantics_and_v2_migration(service, template_id):
    session, project, revision_id, store, snapshot = _build(
        service, f"c0{template_id[:6]}", template_id)
    baseline = _baseline(template_id)

    # A budget-exhausted build is UNKNOWN, not a design regression. Keep that
    # distinction visible instead of reporting a confusing hash mismatch.
    if snapshot.board.unrouted_nets:
        pytest.fail(
            f"{template_id}: routing ended with unrouted nets "
            f"{snapshot.board.unrouted_nets}; the 30 s budget was exhausted or "
            "the route is incomplete, so this revision cannot be compared with "
            "the baseline (status UNKNOWN, not INFEASIBLE).")

    assert snapshot.revision.design_hash == baseline["design_hash"]
    assert snapshot.checks.review_status == baseline["review_status"]
    assert snapshot.checks.counts == baseline["counts"]
    assert len(snapshot.circuit.components) == baseline["components"]
    assert len(snapshot.circuit.nets) == baseline["nets"]
    assert len(snapshot.board.pads) == baseline["pads"]
    assert len(snapshot.board.tracks) == baseline["tracks"]
    assert len(snapshot.board.vias) == baseline["vias"]
    assert {c.check_id: c.status for c in snapshot.checks.checks} == \
        baseline["check_statuses"]

    doc = migrate_snapshot(_snapshot_payload(snapshot),
                           design_version=snapshot.version,
                           design_hash=snapshot.revision.design_hash)
    assert doc.schema_version == 2
    assert doc.engine == "mota-engine-v2"
    assert doc.design_version == snapshot.version
    assert semantic_hash(snapshot) == semantic_hash(doc)
    assert doc.source.source_schema_version == 1
    assert doc.source.design_hash == snapshot.revision.design_hash
    assert doc.units.length == "nm"

    # Stackup is explicit, sums to the v1 thickness, and declares its assumptions.
    assert doc.board.stackup.total_thickness_nm == snapshot.board.thickness_nm
    assert [layer.id for layer in doc.board.stackup.layers
            if layer.kind == "copper"] == ["F.Cu", "B.Cu"]
    assert doc.board.layer_order == ["F.Cu", "B.Cu"]
    assert sum(layer.thickness_nm for layer in doc.board.stackup.layers) == \
        snapshot.board.thickness_nm
    assert any("35 um" in item for item in doc.board.stackup.assumptions)

    # Physical terminals come from the v1 pin-pad map, not a new guess.
    for component in doc.circuit.components:
        v1 = snapshot.circuit.component(component.id)
        assert component.instance_path == v1.reference
        mapping = {row.number: row.pad_numbers for row in component.physical_terminals}
        assert mapping == v1.pin_pad_map
        assert component.assembly_state == "assembled"
        assert component.on_board and component.in_bom
    assert doc.circuit.hierarchy.source == "migrated-v1"
    assert doc.constraints.profile_id == snapshot.circuit.rule_profile_id
    assert doc.constraints.rules == []
    assert "RULE_COMPILER_C5_NOT_IMPLEMENTED" in doc.constraints.unresolved

    # Cross-view identity: every schematic symbol / board footprint / pad refers to
    # the same component ids, and every copper object refers to a real net.
    component_ids = {c.id for c in snapshot.circuit.components}
    net_ids = {n.id for n in snapshot.circuit.nets}
    assert {s.component_id for s in snapshot.schematic.symbols} <= component_ids
    assert {f.component_id for f in snapshot.board.footprints} <= component_ids
    assert {p.component_id for p in snapshot.board.pads} <= component_ids
    assert {p.net_id for p in snapshot.board.pads if p.net_id} <= net_ids
    assert {t.net_id for t in snapshot.board.tracks} <= net_ids
    assert {v.net_id for v in snapshot.board.vias} <= net_ids
    for pad in snapshot.board.pads:
        assert isinstance(pad.x_nm, int) and isinstance(pad.w_nm, int)


def test_migration_is_derived_idempotent_and_reversible(service):
    session, project, revision_id, store, snapshot = _build(
        service, "c0file", "led_timer")
    rdir = store.project_dir(project) / "revisions" / revision_id
    source_before = {name: sha256_file(rdir / name) for name in SOURCE_FILES}

    first = migrate_revision(store, project, revision_id)
    assert first["status"] == "MIGRATED"
    assert (rdir / "engine_v2" / "engine_v2.json").exists()
    assert {name: sha256_file(rdir / name) for name in SOURCE_FILES} == source_before
    meta = read_migration(store, project, revision_id)
    assert meta["source_hash"] == first["source_hash"]
    document_hash = first["document_hash"]

    second = migrate_revision(store, project, revision_id)
    assert second["status"] == "ALREADY_MIGRATED"
    assert second["document_hash"] == document_hash

    # Old revision still opens through the v1 path while the derived document exists.
    reopened = store.load_revision(project, revision_id)
    assert reopened.revision.design_hash == snapshot.revision.design_hash

    rolled = rollback_revision(store, project, revision_id)
    assert rolled["status"] == "ROLLED_BACK"
    assert rolled["source_unchanged"] is True
    assert not (rdir / "engine_v2").exists()
    assert {name: sha256_file(rdir / name) for name in SOURCE_FILES} == source_before
    store.load_revision(project, revision_id)

    again = migrate_revision(store, project, revision_id)
    assert again["document_hash"] == document_hash


def test_invalid_or_unknown_input_never_touches_the_revision(service):
    session, project, revision_id, store, snapshot = _build(
        service, "c0bad", "power_3v3")
    rdir = store.project_dir(project) / "revisions" / revision_id
    source_before = {name: sha256_file(rdir / name) for name in SOURCE_FILES}

    payload = _snapshot_payload(snapshot)
    broken = payload["circuit"].model_dump()
    broken.pop("nets")
    payload["circuit"] = broken
    with pytest.raises(MigrationError) as exc:
        migrate_snapshot(payload, design_version=snapshot.version)
    assert exc.value.code in {"MIGRATION_INVALID", "MIGRATION_DANGLING_REFERENCE"}
    assert "net" in str(exc.value).lower()

    wrong_type = _snapshot_payload(snapshot)
    wrong_type["circuit"] = "not-an-object"
    with pytest.raises(MigrationError) as exc:
        migrate_snapshot(wrong_type, design_version=snapshot.version)
    assert exc.value.code == "MIGRATION_INVALID"

    unknown = _snapshot_payload(snapshot)
    circuit = unknown["circuit"].model_dump()
    circuit["future_field"] = {"value": 1}
    unknown["circuit"] = circuit
    with pytest.raises(MigrationError) as exc:
        migrate_snapshot(unknown, design_version=snapshot.version)
    assert exc.value.code == "MIGRATION_INVALID"
    assert "future_field" in json.dumps(exc.value.data, ensure_ascii=False)

    top = _snapshot_payload(snapshot)
    top["unknown_top"] = True
    with pytest.raises(MigrationError) as exc:
        migrate_snapshot(top, design_version=snapshot.version)
    assert exc.value.code == "MIGRATION_UNSUPPORTED_FIELD"

    assert not (rdir / "engine_v2").exists()
    assert {name: sha256_file(rdir / name) for name in SOURCE_FILES} == source_before


def test_job_contract_keeps_unknown_and_infeasible_distinct():
    version = DesignVersion.of_draft("draft_c0", 3)
    request = JobRequest(job_id="job_1", kind="route", base_version=version,
                         budget=JobBudget(wall_seconds=2.5),
                         requested_targets=["net_a"], constraints_hash="abc")
    assert request.budget.wall_seconds == 2.5
    unknown = JobResult(job_id="job_1", base_version=version, status="UNKNOWN",
                        requested_targets=["net_a"], completed_targets=[],
                        unimplemented_targets=["net_a"],
                        unimplemented_checks=["COPPER_CONNECTIVITY"])
    infeasible = JobResult(job_id="job_1", base_version=version, status="INFEASIBLE",
                           requested_targets=["net_a"])
    assert unknown.status != infeasible.status
    assert unknown.unimplemented_targets == ["net_a"]
    assert "UNKNOWN" in ["SUCCEEDED", "PARTIAL", "INFEASIBLE", "UNKNOWN",
                         "CANCELLED", "FAILED"]

    with pytest.raises(ValueError):
        DesignVersion(revision_id="rev_1", draft_id="draft_1", draft_seq=0)
    with pytest.raises(ValueError):
        DesignVersion()
