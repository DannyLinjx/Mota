"""Portable Mota project bundles: export format validation and offline import.

Import is defensive: path traversal, links, oversize entries, hash mismatches,
unknown schemas and missing catalog resources are rejected without creating a
half-current project.
"""

from __future__ import annotations

import json
import secrets
import zipfile
from pathlib import Path
from typing import Any

from mota.catalog.templates import get_template
from mota.circuit import parts
from mota.domain.models import (
    BoardIR, CircuitIR, RequirementSpec, SchematicIR, ValidationReport,
)
from mota.errors import BundleIncomplete, BundleInvalid, IdempotencyConflict, NotFound
from mota.generation.modules import slug
from mota.projects.store import ProjectStore, Snapshot
from mota.validation.reports import validate_all
from mota.workspace.atomic import read_json, sha256_bytes, write_json_atomic
from mota.workspace.locks import file_lock

MAX_ENTRIES = 2000
MAX_TOTAL = 200 * 1024 * 1024
REQUIRED = {"manifest.json", "revision.json", "circuit.json", "schematic.json",
            "board.json", "checks.json", "assets.json"}


def import_bundle(store: ProjectStore, file_path: str | Path, name: str | None,
                  idempotency_key: str) -> dict[str, Any]:
    path = Path(file_path).expanduser()
    if not path.exists() or not path.is_file():
        raise BundleInvalid("Bundle file does not exist.", code="BUNDLE_INVALID")
    file_hash = sha256_bytes(path.read_bytes())
    index_path = store.base.parent / "import_index.json"
    with file_lock(store.base / ".import.lock"):
        index = read_json(index_path, default={"schema_version": 1, "entries": {}}) or {}
        prior = index.setdefault("entries", {}).get(idempotency_key)
        if prior:
            if prior.get("file_hash") != file_hash:
                raise IdempotencyConflict(
                    "This idempotency key was used for a different bundle.",
                    code="IDEMPOTENCY_CONFLICT")
            return prior["result"]
        result = _import(store, path, name, idempotency_key, file_hash)
        index["entries"][idempotency_key] = {"file_hash": file_hash, "result": result}
        write_json_atomic(index_path, index)
        return result


def _safe_read(zf: zipfile.ZipFile, name: str, limit: int = 32 * 1024 * 1024) -> bytes:
    info = zf.getinfo(name)
    if info.file_size > limit:
        raise BundleInvalid(f"Entry {name} exceeds the size limit.", code="BUNDLE_INVALID")
    return zf.read(name)


def _validate_zip(zf: zipfile.ZipFile) -> None:
    infos = zf.infolist()
    if len(infos) > MAX_ENTRIES:
        raise BundleInvalid("Bundle contains too many files.", code="BUNDLE_INVALID")
    total = 0
    for info in infos:
        name = info.filename
        if name.startswith("/") or ".." in Path(name).parts:
            raise BundleInvalid("Bundle contains an unsafe path.", code="BUNDLE_INVALID")
        mode = (info.external_attr >> 16) & 0o170000
        if mode == 0o120000:
            raise BundleInvalid("Bundle contains a symbolic link.", code="BUNDLE_INVALID")
        total += info.file_size
        if total > MAX_TOTAL:
            raise BundleInvalid("Bundle expands beyond the size limit.", code="BUNDLE_INVALID")
    missing = REQUIRED - set(zf.namelist())
    if missing:
        raise BundleIncomplete(f"Bundle is missing {sorted(missing)}.",
                              code="BUNDLE_INCOMPLETE")


def _import(store: ProjectStore, path: Path, name: str | None, key: str,
            file_hash: str) -> dict[str, Any]:
    try:
        zf = zipfile.ZipFile(path)
    except zipfile.BadZipFile as exc:
        raise BundleInvalid(f"Not a valid bundle: {exc}", code="BUNDLE_INVALID") from exc
    with zf:
        _validate_zip(zf)
        manifest = json.loads(_safe_read(zf, "manifest.json"))
        if manifest.get("bundle_format") != "mota.bundle.v1" or \
                manifest.get("schema_version") != 1:
            raise BundleInvalid("Unsupported bundle format or schema version.",
                                code="BUNDLE_INVALID")
        for entry, meta in manifest.get("files", {}).items():
            if entry not in zf.namelist():
                raise BundleIncomplete(f"Missing {entry}.", code="BUNDLE_INCOMPLETE")
            data = _safe_read(zf, entry)
            if sha256_bytes(data) != meta.get("sha256"):
                raise BundleInvalid(f"Hash mismatch for {entry}.", code="BUNDLE_INVALID")
        circuit = CircuitIR.model_validate(json.loads(_safe_read(zf, "circuit.json")))
        schematic = SchematicIR.model_validate(json.loads(_safe_read(zf, "schematic.json")))
        board = BoardIR.model_validate(json.loads(_safe_read(zf, "board.json")))
        requirement = RequirementSpec.model_validate(json.loads(_safe_read(zf, "requirement.json"))) \
            if "requirement.json" in zf.namelist() else RequirementSpec()
        assets = json.loads(_safe_read(zf, "assets.json"))
        imported_checks = ValidationReport.model_validate(
            json.loads(_safe_read(zf, "checks.json")))

    # Resource completeness: every referenced part/footprint/symbol must resolve.
    problems = []
    for comp in circuit.components:
        try:
            parts.resolve_part(comp.part_id)
        except NotFound:
            problems.append(f"part {comp.part_id} is not installed")
        if comp.footprint_id:
            try:
                parts.resolve_footprint(comp.footprint_id)
            except NotFound:
                problems.append(f"footprint {comp.footprint_id} is not installed")
        try:
            parts.resolve_symbol(comp.symbol_id)
        except NotFound:
            problems.append(f"symbol {comp.symbol_id} is not installed")
    if problems:
        raise BundleIncomplete("Bundle references resources that are not available: "
                              + "; ".join(problems[:6]), code="BUNDLE_INCOMPLETE")

    token = secrets.token_hex(3)
    circuit, schematic, board = rekey(circuit, schematic, board, token)
    recipe = None
    if requirement.template_id:
        try:
            recipe = get_template(requirement.template_id)
        except NotFound:
            recipe = None
    project_id = "project_" + secrets.token_hex(8)
    from mota.domain.models import DesignVersion

    version = DesignVersion.of_revision("rev_000001")
    checks = validate_all(project_id, version, requirement, circuit, schematic, board, recipe)
    display_name = (name or f"{manifest.get('template_id') or 'Imported'} board").strip()
    meta = store.create(display_name, requirement, circuit, schematic, board, checks,
                        template_id=requirement.template_id, source_kind="import",
                        revision_id="rev_000001", commit_source="import",
                        project_id=project_id)
    from mota.app.scenes import write_revision_scenes

    write_revision_scenes(store.load_revision(project_id, "rev_000001"), store)
    pdir = store.project_dir(project_id)
    report = {
        "schema_version": 1,
        "source_bundle": path.name,
        "source_bundle_sha256": file_hash,
        "source_project_id": manifest.get("project_id"),
        "source_revision_id": manifest.get("revision_id"),
        "imported_at": meta.created_at,
        "imported_checks_summary": imported_checks.counts,
        "imported_review_status": imported_checks.review_status,
        "revalidated_checks_summary": checks.counts,
        "revalidated_review_status": checks.review_status,
        "note": "Imported checks are preserved as provenance; the current revision "
                "uses locally re-run Mota checks.",
        "idempotency_key": key,
    }
    write_json_atomic(pdir / "sources" / "import_report.json", report)
    write_json_atomic(pdir / "sources" / "imported_checks.json", imported_checks.model_dump())
    project = read_json(pdir / "project.json", default={}) or {}
    project["name"] = display_name
    project["imported_from"] = {
        "source_project_id": manifest.get("project_id"),
        "source_revision_id": manifest.get("revision_id"),
        "source_bundle_sha256": file_hash,
    }
    write_json_atomic(pdir / "project.json", project)
    return {
        "project_id": project_id,
        "revision_id": "rev_000001",
        "import_report": report,
        "review_status": checks.review_status,
    }


def rekey(circuit: CircuitIR, schematic: SchematicIR, board: BoardIR,
          token: str) -> tuple[CircuitIR, SchematicIR, BoardIR]:
    comp_map = {c.id: f"{c.id}_{token}" for c in circuit.components}
    net_map = {n.id: f"{n.id}_{token}" for n in circuit.nets}
    circuit = circuit.model_copy(deep=True)
    schematic = schematic.model_copy(deep=True)
    board = board.model_copy(deep=True)
    for comp in circuit.components:
        comp.id = comp_map[comp.id]
    for net in circuit.nets:
        net.id = net_map[net.id]
        net.pins = [f"{comp_map.get(p.split(':', 1)[0], p.split(':', 1)[0])}:{p.split(':', 1)[1]}"
                    for p in net.pins]
    circuit.no_connects = [
        f"{comp_map.get(p.split(':', 1)[0], p.split(':', 1)[0])}:{p.split(':', 1)[1]}"
        for p in circuit.no_connects]
    for sym in schematic.symbols:
        sym.component_id = comp_map.get(sym.component_id, sym.component_id)
    for wire in schematic.wires:
        if wire.net_id in net_map:
            wire.net_id = net_map[wire.net_id]
    for junction in schematic.junctions:
        junction.net_id = net_map.get(junction.net_id, junction.net_id)
    for label in schematic.labels:
        label.net_id = net_map.get(label.net_id, label.net_id)
    schematic.no_connect_pins = [
        f"{comp_map.get(p.split(':', 1)[0], p.split(':', 1)[0])}:{p.split(':', 1)[1]}"
        for p in schematic.no_connect_pins]
    for fp in board.footprints:
        fp.component_id = comp_map.get(fp.component_id, fp.component_id)
    for pad in board.pads:
        pad.component_id = comp_map.get(pad.component_id, pad.component_id)
        pad.id = f"{pad.component_id}_pad_{pad.number}"
        if pad.net_id in net_map:
            pad.net_id = net_map[pad.net_id]
    for track in board.tracks:
        track.net_id = net_map.get(track.net_id, track.net_id)
    for via in board.vias:
        via.net_id = net_map.get(via.net_id, via.net_id)
    for line in board.ratsnest:
        line.net_id = net_map.get(line.net_id, line.net_id)
    board.unrouted_nets = [net_map.get(n, n) for n in board.unrouted_nets]
    return circuit, schematic, board
