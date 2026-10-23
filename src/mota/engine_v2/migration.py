"""Lossless v1 -> v2 migration with derived, reversible artifacts.

Rules:
* v1 revision files are never rewritten. The v2 document is a derived file under
  revisions/<id>/engine_v2/ and can be deleted to roll back.
* Unknown fields or invalid types fail loudly; nothing is silently dropped.
* A migration is idempotent for the same source hash, and refuses to overwrite a
  derived document built from different source bytes.
"""

from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from mota.domain.models import (
    BoardIR, CircuitIR, DesignVersion, RequirementSpec, SchematicIR, ValidationReport,
    utcnow,
)
from mota.engine_v2.schema import (
    BoardV2, CircuitV2, ComponentV2, ConstraintIR, EngineDocumentV2,
    HierarchyIR, PhysicalTerminal, SchematicV2, SourceRef, Stackup, StackupLayer,
    ValidationReportV2, ViaDefinition,
)
from mota.workspace.atomic import sha256_file, write_json_atomic

MIGRATION_VERSION = "mota-engine-v1-to-v2/1"
COPPER_THICKNESS_NM = 35_000
SOURCE_FILES = ("revision.json", "circuit.json", "schematic.json", "board.json",
                "checks.json")
TOP_LEVEL_KEYS = {"requirement", "circuit", "schematic", "board", "checks",
                  "revision", "project_id"}


class MigrationError(Exception):
    code = "MIGRATION_INVALID"

    def __init__(self, message: str, *, code: str | None = None,
                 data: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.message = message
        if code:
            self.code = code
        self.data = data or {}


def _canonical(payload: Any) -> str:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"))


def _hash(payload: Any) -> str:
    return hashlib.sha256(_canonical(payload).encode("utf-8")).hexdigest()


def _as_dict(value: Any, name: str) -> dict[str, Any]:
    if hasattr(value, "model_dump"):
        return value.model_dump()
    if isinstance(value, dict):
        return dict(value)
    raise MigrationError(f"{name} must be an object.", code="MIGRATION_INVALID")


def _validate_v1(payload: dict[str, Any]) -> tuple[
        RequirementSpec, CircuitIR, SchematicIR, BoardIR, ValidationReport]:
    unknown = sorted(set(payload) - TOP_LEVEL_KEYS)
    if unknown:
        raise MigrationError(
            "Unsupported top-level field(s): " + ", ".join(unknown),
            code="MIGRATION_UNSUPPORTED_FIELD", data={"fields": unknown})
    try:
        requirement = RequirementSpec.model_validate(
            _as_dict(payload.get("requirement"), "requirement"))
        circuit = CircuitIR.model_validate(_as_dict(payload.get("circuit"), "circuit"))
        schematic = SchematicIR.model_validate(
            _as_dict(payload.get("schematic"), "schematic"))
        board = BoardIR.model_validate(_as_dict(payload.get("board"), "board"))
        checks = ValidationReport.model_validate(
            _as_dict(payload.get("checks"), "checks"))
    except ValidationError as exc:
        raise MigrationError(
            "v1 snapshot does not validate: " + "; ".join(
                f"{'.'.join(str(p) for p in err['loc'])}: {err['msg']}"
                for err in exc.errors()[:8]),
            code="MIGRATION_INVALID",
            data={"errors": [{"loc": list(err["loc"]), "msg": err["msg"],
                              "type": err["type"]} for err in exc.errors()]}) from exc
    _assert_v1_semantics(circuit, schematic, board, checks)
    return requirement, circuit, schematic, board, checks


def _assert_v1_semantics(circuit: CircuitIR, schematic: SchematicIR,
                         board: BoardIR, checks: ValidationReport) -> None:
    component_ids = [c.id for c in circuit.components]
    if len(component_ids) != len(set(component_ids)):
        raise MigrationError("Duplicate v1 component ids.", code="MIGRATION_INVALID")
    net_ids = {n.id for n in circuit.nets}
    if len(net_ids) != len(circuit.nets):
        raise MigrationError("Duplicate v1 net ids.", code="MIGRATION_INVALID")
    pad_ids = [p.id for p in board.pads]
    if len(pad_ids) != len(set(pad_ids)):
        raise MigrationError("Duplicate v1 pad ids.", code="MIGRATION_INVALID")
    known = set(component_ids)
    for symbol in schematic.symbols:
        if symbol.component_id not in known:
            raise MigrationError(
                f"Schematic symbol {symbol.component_id} has no circuit component.",
                code="MIGRATION_DANGLING_REFERENCE")
    for footprint in board.footprints:
        if footprint.component_id not in known:
            raise MigrationError(
                f"Footprint {footprint.component_id} has no circuit component.",
                code="MIGRATION_DANGLING_REFERENCE")
    for pad in board.pads:
        if pad.component_id not in known:
            raise MigrationError(
                f"Pad {pad.id} has no circuit component.",
                code="MIGRATION_DANGLING_REFERENCE")
        if pad.net_id is not None and pad.net_id not in net_ids:
            raise MigrationError(
                f"Pad {pad.id} references unknown net {pad.net_id}.",
                code="MIGRATION_DANGLING_REFERENCE")
    for track in board.tracks:
        if track.net_id not in net_ids:
            raise MigrationError(
                f"Track {track.id} references unknown net {track.net_id}.",
                code="MIGRATION_DANGLING_REFERENCE")
    for via in board.vias:
        if via.net_id not in net_ids:
            raise MigrationError(
                f"Via {via.id} references unknown net {via.net_id}.",
                code="MIGRATION_DANGLING_REFERENCE")
    if board.layer_count != 2:
        raise MigrationError(
            f"v1 migration covers the two-layer engine only; found {board.layer_count}.",
            code="MIGRATION_UNSUPPORTED_LAYER_COUNT")
    if board.thickness_nm <= 2 * COPPER_THICKNESS_NM:
        raise MigrationError(
            "Board thickness cannot hold two 35 um copper layers.",
            code="MIGRATION_INVALID_STACKUP")


def _stackup(board: BoardIR) -> Stackup:
    dielectric = board.thickness_nm - 2 * COPPER_THICKNESS_NM
    layers = [
        StackupLayer(id="F.Mask", order=0, kind="mask", thickness_nm=0),
        StackupLayer(id="F.Cu", order=1, kind="copper",
                     thickness_nm=COPPER_THICKNESS_NM),
        StackupLayer(id="Dielectric_1", order=2, kind="dielectric",
                     thickness_nm=dielectric),
        StackupLayer(id="B.Cu", order=3, kind="copper",
                     thickness_nm=COPPER_THICKNESS_NM),
        StackupLayer(id="B.Mask", order=4, kind="mask", thickness_nm=0),
    ]
    return Stackup(
        layers=layers, total_thickness_nm=board.thickness_nm,
        source="migrated-v1",
        assumptions=[
            "Copper thickness defaults to 35 um per layer; v1 does not record it.",
            "Solder-mask thickness is not modelled and recorded as 0.",
            "No reference plane net is declared in v1, so reference_nets stays empty.",
            "Stackup is derived for engine-v2 editing; it is not fabrication data.",
        ])


def _via_definitions(board: BoardIR) -> list[ViaDefinition]:
    seen: set[tuple[int, int]] = set()
    out: list[ViaDefinition] = []
    for via in board.vias:
        key = (via.outer_nm, via.drill_nm)
        if key in seen:
            continue
        seen.add(key)
        out.append(ViaDefinition(
            id=f"via_def_migrated_{len(out) + 1}", kind="through",
            start_layer_id="F.Cu", end_layer_id="B.Cu",
            drill_nm=via.drill_nm, pad_outer_nm=via.outer_nm,
            source="migrated-v1"))
    return out


def _component_v2(component) -> ComponentV2:
    terminals = []
    for pin_number, pads in component.pin_pad_map.items():
        terminals.append(PhysicalTerminal(
            number=str(pin_number), pad_numbers=[str(p) for p in pads],
            hidden=False, unit_id=None, electrical_type="unknown"))
    data = component.model_dump()
    data.update({
        "instance_path": component.reference,
        "physical_terminals": [t.model_dump() for t in terminals],
        "symbol_units": [],
        "on_board": True,
        "in_bom": True,
        "assembly_state": "assembled",
    })
    return ComponentV2.model_validate(data)


def migrate_snapshot(payload: dict[str, Any] | Any, *,
                     design_version: DesignVersion | None = None,
                     source_files: dict[str, str] | None = None,
                     design_hash: str = "") -> EngineDocumentV2:
    """Migrate a v1 snapshot (dict or models) into a validated v2 document."""
    if isinstance(payload, EngineDocumentV2):
        return payload
    data = _as_dict(payload, "snapshot")
    if data.get("schema_version") == 2 and data.get("engine") == "mota-engine-v2":
        return EngineDocumentV2.model_validate(data)

    requirement, circuit, schematic, board, checks = _validate_v1(data)
    source_hash = _hash({
        "requirement": requirement.model_dump(),
        "circuit": circuit.model_dump(),
        "schematic": schematic.model_dump(),
        "board": board.model_dump(),
        "checks": checks.model_dump(),
    })

    circuit_dict = circuit.model_dump()
    circuit_dict["schema_version"] = 2
    circuit_dict["components"] = [_component_v2(c).model_dump()
                                  for c in circuit.components]
    circuit_dict["hierarchy"] = HierarchyIR(
        modules=list(circuit.modules),
        instances=[],
        buses=[],
        unresolved=(
            (["MODULE_DEFINITIONS_C4_NOT_IMPLEMENTED"] if circuit.modules else []) +
            ["HIERARCHY_INSTANCES_C3_NOT_IMPLEMENTED"]),
        source="migrated-v1").model_dump()

    schematic_dict = schematic.model_dump()
    schematic_dict["schema_version"] = 2
    schematic_dict["page_instances"] = [{
        "sheet_path": "/", "page": 1, "title": schematic.title,
        "source": "migrated-v1",
    }]

    stackup = _stackup(board)
    board_dict = board.model_dump()
    board_dict["schema_version"] = 2
    board_dict["stackup"] = stackup.model_dump()
    board_dict["layer_order"] = ["F.Cu", "B.Cu"]
    board_dict["via_definitions"] = [v.model_dump()
                                     for v in _via_definitions(board)]

    checks_dict = checks.model_dump()
    checks_dict["schema_version"] = 2
    checks_dict["checker_version"] = "mota-validator-v1"

    constraints = ConstraintIR(
        profile_id=circuit.rule_profile_id, rules=[],
        unresolved=["RULE_COMPILER_C5_NOT_IMPLEMENTED"], source="migrated-v1")

    doc = EngineDocumentV2(
        schema_version=2,
        migration_version=MIGRATION_VERSION,
        design_version=design_version or checks.design_version,
        source=SourceRef(
            source_schema_version=int(data.get("schema_version", 1)),
            source_hash=source_hash,
            source_files=dict(source_files or {}),
            design_hash=design_hash),
        requirement=requirement,
        circuit=CircuitV2.model_validate(circuit_dict),
        schematic=SchematicV2.model_validate(schematic_dict),
        board=BoardV2.model_validate(board_dict),
        checks=ValidationReportV2.model_validate(checks_dict),
        constraints=constraints,
        assumptions=stackup.assumptions + [
            "v1 checks are preserved as-is; migration executes no new check.",
            "Constraint rules are not compiled yet (C5).",
        ],
        preserved={"unknown_fields": []})
    return doc


def semantic_signature(document: EngineDocumentV2 | Any) -> dict[str, Any]:
    """Stable semantic summary used to prove migration preserves meaning."""
    if isinstance(document, EngineDocumentV2):
        requirement = document.requirement
        circuit = document.circuit
        schematic = document.schematic
        board = document.board
        checks = document.checks
    else:
        requirement = document.requirement
        circuit = document.circuit
        schematic = document.schematic
        board = document.board
        checks = document.checks
    return {
        "board_size": requirement.board_size,
        "components": sorted(
            [{"id": c.id, "reference": c.reference, "part_id": c.part_id,
              "symbol_id": c.symbol_id, "footprint_id": c.footprint_id,
              "pin_pad_map": c.pin_pad_map}
             for c in circuit.components],
            key=lambda row: row["id"]),
        "nets": sorted([{"id": n.id, "name": n.name, "pins": sorted(n.pins)}
                        for n in circuit.nets], key=lambda row: row["id"]),
        "schematic_symbols": sorted(
            [{"component_id": s.component_id, "symbol_id": s.symbol_id,
              "reference": s.reference, "x_nm": s.x_nm, "y_nm": s.y_nm}
             for s in schematic.symbols], key=lambda row: row["component_id"]),
        "footprints": sorted(
            [{"component_id": f.component_id, "footprint_id": f.footprint_id,
              "x_nm": f.x_nm, "y_nm": f.y_nm,
              "rotation_deg": f.rotation_deg, "side": f.side}
             for f in board.footprints], key=lambda row: row["component_id"]),
        "pads": sorted(
            [{"id": p.id, "component_id": p.component_id, "number": p.number,
              "net_id": p.net_id, "x_nm": p.x_nm, "y_nm": p.y_nm,
              "w_nm": p.w_nm, "h_nm": p.h_nm, "shape": p.shape,
              "layers": sorted(p.layers)} for p in board.pads],
            key=lambda row: row["id"]),
        "tracks": sorted(
            [{"id": t.id, "net_id": t.net_id, "layer": t.layer,
              "width_nm": t.width_nm, "points": t.points}
             for t in board.tracks], key=lambda row: row["id"]),
        "vias": sorted(
            [{"id": v.id, "net_id": v.net_id, "x_nm": v.x_nm, "y_nm": v.y_nm,
              "outer_nm": v.outer_nm, "drill_nm": v.drill_nm}
             for v in board.vias], key=lambda row: row["id"]),
        "checks": {c.check_id: {"status": c.status,
                                "required": c.required_for_review,
                                "objects": sorted(c.object_ids)}
                   for c in checks.checks},
        "review_status": checks.review_status,
        "counts": checks.counts,
    }


def semantic_hash(document: EngineDocumentV2 | Any) -> str:
    return _hash(semantic_signature(document))


def _revision_dir(store, project_id: str, revision_id: str) -> Path:
    return store.project_dir(project_id) / "revisions" / revision_id


def _migration_meta(path: Path) -> dict[str, Any]:
    from mota.workspace.atomic import read_json

    return read_json(path, default={}) or {}


def read_migration(store, project_id: str, revision_id: str) -> dict[str, Any]:
    target = _revision_dir(store, project_id, revision_id) / "engine_v2"
    meta = _migration_meta(target / "migration.json")
    if not meta:
        raise MigrationError("No engine-v2 migration exists for this revision.",
                             code="MIGRATION_NOT_FOUND")
    return meta


def migrate_revision(store, project_id: str, revision_id: str) -> dict[str, Any]:
    """Create a derived v2 document for one revision; never rewrite v1 files."""
    rdir = _revision_dir(store, project_id, revision_id)
    if not rdir.is_dir():
        raise MigrationError("Revision not found.", code="REVISION_NOT_FOUND")
    source_files = {name: sha256_file(rdir / name)
                    for name in SOURCE_FILES if (rdir / name).exists()}
    snapshot = store.load_revision(project_id, revision_id)
    doc = migrate_snapshot({
        "requirement": snapshot.requirement,
        "circuit": snapshot.circuit,
        "schematic": snapshot.schematic,
        "board": snapshot.board,
        "checks": snapshot.checks,
    }, design_version=DesignVersion.of_revision(revision_id),
        source_files=source_files, design_hash=snapshot.revision.design_hash)
    document_payload = doc.model_dump()
    document_hash = _hash(document_payload)
    target = rdir / "engine_v2"
    if target.exists():
        meta = _migration_meta(target / "migration.json")
        if meta.get("source_hash") == doc.source.source_hash \
                and meta.get("document_hash") == document_hash:
            return {**meta, "status": "ALREADY_MIGRATED"}
        raise MigrationError(
            "A derived engine-v2 document already exists for different source bytes; "
            "roll it back before migrating again.",
            code="MIGRATION_STALE")
    staging = rdir / f".engine_v2.tmp-{hashlib.sha256(document_hash.encode()).hexdigest()[:8]}"
    if staging.exists():
        shutil.rmtree(staging)
    staging.mkdir(parents=True)
    write_json_atomic(staging / "engine_v2.json", document_payload)
    meta = {
        "status": "MIGRATED",
        "migration_version": MIGRATION_VERSION,
        "engine": "mota-engine-v2",
        "source_schema_version": doc.source.source_schema_version,
        "source_hash": doc.source.source_hash,
        "document_hash": document_hash,
        "semantic_hash": semantic_hash(doc),
        "source_files": source_files,
        "design_hash": snapshot.revision.design_hash,
        "created_at": utcnow(),
        "assumptions": doc.assumptions,
        "rollback": "delete this engine_v2 directory; v1 files are unchanged",
    }
    write_json_atomic(staging / "migration.json", meta)
    unchanged = {name: sha256_file(rdir / name)
                 for name in SOURCE_FILES if (rdir / name).exists()}
    if unchanged != source_files:
        shutil.rmtree(staging)
        raise MigrationError("Source revision changed during migration.",
                             code="MIGRATION_SOURCE_CHANGED")
    staging.rename(target)
    return meta


def rollback_revision(store, project_id: str, revision_id: str) -> dict[str, Any]:
    """Delete only the derived v2 directory; v1 revision files stay authoritative."""
    rdir = _revision_dir(store, project_id, revision_id)
    target = rdir / "engine_v2"
    if not target.exists():
        return {"status": "NOTHING_TO_ROLLBACK", "revision_id": revision_id}
    meta = _migration_meta(target / "migration.json")
    before = {name: sha256_file(rdir / name)
              for name in SOURCE_FILES if (rdir / name).exists()}
    shutil.rmtree(target)
    after = {name: sha256_file(rdir / name)
             for name in SOURCE_FILES if (rdir / name).exists()}
    return {
        "status": "ROLLED_BACK",
        "revision_id": revision_id,
        "removed": "engine_v2",
        "source_unchanged": before == after,
        "source_hash": meta.get("source_hash"),
        "v1_files": sorted(before),
    }
