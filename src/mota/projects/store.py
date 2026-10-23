"""Project storage: immutable revisions, drafts, atomic commit point.

Layout mirrors the implementation contract:

    workspace/projects/<project_id>/
      project.json  commit_index.json  current.json
      revisions/<revision_id>/{revision,circuit,schematic,board,checks,artifacts}.json
      drafts/<run_id>/draft.json
      runs/<run_id>/{run.json,events.jsonl,checkpoint.json,metrics.json}
      sources/  sessions/  exports/  trash/

`current.json` replacement is the only commit point.
"""

from __future__ import annotations

import json
import shutil
import secrets
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from mota.app.state import UserScope
from mota.domain.models import (
    ArtifactRef,
    BoardIR,
    BuildManifest,
    CircuitIR,
    CommitMeta,
    DesignVersion,
    ProjectCard,
    ProjectRevisionMeta,
    RequirementSpec,
    RevisionSummary,
    SchematicIR,
    ValidationReport,
    utcnow,
)
from mota.errors import (
    Conflict, IdempotencyConflict, InvalidRequest, NotFound, ProjectBusy,
    RevisionConflict, SaveFailed,
)
from mota.workspace import paths
from mota.workspace.atomic import (
    read_json, sha256_bytes, write_json_atomic, write_text_atomic,
)
from mota.workspace.locks import file_lock


@dataclass
class DesignDraft:
    draft_id: str
    project_id: str
    base_revision_id: str | None
    run_id: str | None
    draft_seq: int
    requirement: RequirementSpec
    circuit: CircuitIR
    schematic: SchematicIR | None
    board: BoardIR | None
    checks: ValidationReport | None
    created_at: str
    updated_at: str
    parameters: dict[str, Any]

    @property
    def version(self) -> DesignVersion:
        return DesignVersion.of_draft(self.draft_id, self.draft_seq)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "draft_id": self.draft_id,
            "project_id": self.project_id,
            "base_revision_id": self.base_revision_id,
            "run_id": self.run_id,
            "draft_seq": self.draft_seq,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "parameters": self.parameters,
            "requirement": self.requirement.model_dump(),
            "circuit": self.circuit.model_dump(),
            "schematic": self.schematic.model_dump() if self.schematic else None,
            "board": self.board.model_dump() if self.board else None,
            "checks": self.checks.model_dump() if self.checks else None,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "DesignDraft":
        return cls(
            draft_id=data["draft_id"], project_id=data["project_id"],
            base_revision_id=data.get("base_revision_id"), run_id=data.get("run_id"),
            draft_seq=data.get("draft_seq", 0),
            requirement=RequirementSpec.model_validate(data["requirement"]),
            circuit=CircuitIR.model_validate(data["circuit"]),
            schematic=SchematicIR.model_validate(data["schematic"]) if data.get("schematic") else None,
            board=BoardIR.model_validate(data["board"]) if data.get("board") else None,
            checks=ValidationReport.model_validate(data["checks"]) if data.get("checks") else None,
            created_at=data.get("created_at", utcnow()), updated_at=data.get("updated_at", utcnow()),
            parameters=data.get("parameters", {}),
        )


@dataclass
class Snapshot:
    project_id: str
    revision: ProjectRevisionMeta
    requirement: RequirementSpec
    circuit: CircuitIR
    schematic: SchematicIR
    board: BoardIR
    checks: ValidationReport

    @property
    def version(self) -> DesignVersion:
        return DesignVersion.of_revision(self.revision.revision_id)


class ProjectStore:
    def __init__(self, owner: UserScope, root: Path | None = None) -> None:
        self.owner = owner
        self.root = root or paths.data_root()
        self.base = paths.ensure_dir(paths.projects_dir(owner.user_id, self.root))

    # ------------------------------------------------------------------ utils
    def project_dir(self, project_id: str) -> Path:
        if not project_id or "/" in project_id or "\\" in project_id or project_id in (".", ".."):
            raise InvalidRequest("Invalid project id.", code="INVALID_PROJECT_ID")
        path = self.base / project_id
        if not path.exists():
            raise NotFound("Project not found.", code="PROJECT_NOT_FOUND")
        return path

    def _lock(self, project_id: str):
        return file_lock(self.base / f".{project_id}.lock")

    def _project_json(self, project_id: str) -> dict[str, Any]:
        data = read_json(self.project_dir(project_id) / "project.json")
        if not data:
            raise NotFound("Project not found.", code="PROJECT_NOT_FOUND")
        return data

    def _save_project(self, project_id: str, data: dict[str, Any]) -> None:
        write_json_atomic(self.project_dir(project_id) / "project.json", data)

    def current_revision_id(self, project_id: str) -> str | None:
        current = read_json(self.project_dir(project_id) / "current.json", default={}) or {}
        return current.get("revision_id")

    def _revision_dir(self, project_id: str, revision_id: str) -> Path:
        return self.project_dir(project_id) / "revisions" / revision_id

    def has_active_run(self, project_id: str) -> bool:
        active = {"CREATED", "EXTRACTING", "PLANNING", "WAITING_FOR_USER", "RESOLVING",
                  "SYNTHESIZING", "VALIDATING_SCHEMATIC", "PLACING", "ROUTING",
                  "VALIDATING_BOARD", "RENDERING", "COMMITTING"}
        runs = self.project_dir(project_id) / "runs"
        if not runs.exists():
            return False
        for run_file in runs.glob("*/run.json"):
            data = read_json(run_file, default={}) or {}
            if data.get("state") in active:
                return True
        return False

    def _require_not_busy(self, project_id: str) -> None:
        if self.has_active_run(project_id):
            raise ProjectBusy(
                "Finish or cancel the current build to edit this project.",
                data={"project_id": project_id})

    # ------------------------------------------------------------------ create
    def create(self, name: str, requirement: RequirementSpec, circuit: CircuitIR,
               schematic: SchematicIR, board: BoardIR, checks: ValidationReport,
               *, template_id: str | None = None, source_kind: str = "template",
               build_manifest: BuildManifest | None = None,
               revision_id: str = "rev_000001",
               parent_revision_id: str | None = None,
               commit_source: str = "initial",
               restored_from_revision_id: str | None = None,
               project_id: str | None = None) -> ProjectRevisionMeta:
        project_id = project_id or ("project_" + secrets.token_hex(8))
        pdir = self.base / project_id
        pdir.mkdir(parents=True, exist_ok=False)
        now = utcnow()
        project = {
            "schema_version": 1,
            "project_id": project_id,
            "name": name.strip() or "Untitled board",
            "owner_user_id": self.owner.user_id,
            "created_at": now,
            "updated_at": now,
            "template_id": template_id,
            "source_kind": source_kind,
            "archived": False,
            "current_revision_id": revision_id,
            "review_status": checks.review_status,
            "latest_run_state": None,
            "component_count": len(circuit.components),
            "checks_summary": checks.counts,
            "thumbnail_artifact_id": None,
        }
        write_json_atomic(pdir / "project.json", project)
        write_json_atomic(pdir / "commit_index.json", {"schema_version": 1, "entries": []})
        for sub in ("sources", "exports", "drafts", "runs", "sessions", "revisions"):
            (pdir / sub).mkdir(parents=True, exist_ok=True)
        meta = self._write_revision(
            project_id, revision_id, parent_revision_id, requirement, circuit,
            schematic, board, checks, build_manifest or BuildManifest(),
            commit_meta=CommitMeta(
                commit_id="commit_" + secrets.token_hex(8), run_id=None,
                idempotency_key=None, parent_revision_id=parent_revision_id,
                source=commit_source,  # type: ignore[arg-type]
                restored_from_revision_id=restored_from_revision_id),
            summary="Initial revision")
        write_json_atomic(pdir / "current.json", {
            "revision_id": revision_id, "commit_id": meta.commit_meta.commit_id,
            "updated_at": now})
        return meta

    def _write_revision(self, project_id: str, revision_id: str,
                        parent_revision_id: str | None, requirement: RequirementSpec,
                        circuit: CircuitIR, schematic: SchematicIR, board: BoardIR,
                        checks: ValidationReport, build_manifest: BuildManifest,
                        *, commit_meta: CommitMeta, summary: str,
                        restored_from_revision_id: str | None = None) -> ProjectRevisionMeta:
        pdir = self.project_dir(project_id)
        staging = pdir / "revisions" / f".staging_{revision_id}"
        if staging.exists():
            shutil.rmtree(staging)
        staging.mkdir(parents=True)
        version_key = f"rev:{revision_id}"
        checks = checks.model_copy(deep=True)
        checks.project_id = project_id
        checks.design_version = DesignVersion.of_revision(revision_id)
        checks.design_version_key = version_key
        for check in checks.checks:
            check.design_version_key = version_key
            check.outdated = False

        artifacts: list[ArtifactRef] = []

        def put(name: str, payload: Any, kind: str) -> None:
            data = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
            (staging / name).write_bytes(data)
            artifacts.append(ArtifactRef(
                artifact_id=f"artifact_{revision_id}_{name.replace('.', '_')}",
                kind=kind, relative_path=f"revisions/{revision_id}/{name}",
                sha256=sha256_bytes(data), bytes=len(data), design_version_key=version_key))

        put("circuit.json", circuit.model_dump(), "circuit")
        put("schematic.json", schematic.model_dump(), "schematic")
        put("board.json", board.model_dump(), "board")
        put("checks.json", checks.model_dump(), "checks")

        design_hash = canonical_design_hash(requirement, circuit, board)
        meta = ProjectRevisionMeta(
            project_id=project_id, revision_id=revision_id,
            parent_revision_id=parent_revision_id, review_status=checks.review_status,
            summary=summary, template_id=build_manifest.template_id,
            requirement=requirement, build_manifest=build_manifest,
            commit_meta=commit_meta, component_count=len(circuit.components),
            net_count=len(circuit.nets), artifacts=artifacts,
            checks_summary=checks.counts, design_hash=design_hash,
            restored_from_revision_id=restored_from_revision_id)
        meta_bytes = json.dumps(meta.model_dump(), ensure_ascii=False, indent=2).encode("utf-8")
        (staging / "revision.json").write_bytes(meta_bytes)
        artifacts.append(ArtifactRef(
            artifact_id=f"artifact_{revision_id}_revision_json", kind="revision",
            relative_path=f"revisions/{revision_id}/revision.json",
            sha256=sha256_bytes(meta_bytes), bytes=len(meta_bytes),
            design_version_key=version_key))
        write_json_atomic(staging / "artifacts.json", [a.model_dump() for a in artifacts])
        self._write_library_lock(staging, pdir, circuit, project_id, revision_id)
        (staging / "scene").mkdir()
        (staging / "exports").mkdir()

        target = pdir / "revisions" / revision_id
        if target.exists():
            raise SaveFailed(f"Revision {revision_id} already exists.", code="REVISION_EXISTS")
        staging.rename(target)
        return meta.model_copy(update={"artifacts": artifacts})

    def _write_library_lock(self, staging: Path, pdir: Path, circuit: CircuitIR,
                            project_id: str, revision_id: str) -> None:
        """Pin every used asset and vendor its package into the project.

        The lock records exact `asset_id + version + digest`; the vendored copy
        under `assets/packages/<digest>` keeps a revision readable after the
        shared library package is uninstalled.
        """
        try:
            from mota.library import resolver as library_resolver
        except Exception:  # noqa: BLE001 - library is optional during bootstrap
            return
        asset_ids = sorted({
            asset for comp in circuit.components
            for asset in (comp.part_id, comp.symbol_id, comp.footprint_id) if asset
        })
        if not asset_ids:
            return
        try:
            digits = "".join(ch for ch in revision_id if ch.isdigit())
            lock = library_resolver.project_lock(
                asset_ids, project_dir=pdir, project_id=project_id,
                lock_revision=int(digits or 1))
        except Exception:  # noqa: BLE001 - never fail a commit on lock export
            return
        (staging / "library.lock.json").write_text(
            json.dumps(lock, ensure_ascii=False, indent=1, sort_keys=True) + "\n",
            encoding="utf-8")

    # ---------------------------------------------------------------- querying
    def list_projects(self, *, archived: bool = False) -> list[ProjectCard]:
        cards: list[ProjectCard] = []
        for pdir in sorted(self.base.iterdir()):
            if not pdir.is_dir() or not pdir.name.startswith("project_"):
                continue
            data = read_json(pdir / "project.json")
            if not data or bool(data.get("archived", False)) != archived:
                continue
            cards.append(ProjectCard(
                project_id=data["project_id"], name=data["name"],
                updated_at=data.get("updated_at", ""),
                current_revision_id=data.get("current_revision_id"),
                template_id=data.get("template_id"),
                review_status=data.get("review_status", "DRAFT"),
                latest_run_state=data.get("latest_run_state"),
                component_count=data.get("component_count", 0),
                thumbnail_artifact_id=data.get("thumbnail_artifact_id"),
                archived=bool(data.get("archived")),
                source_kind=data.get("source_kind", "template"),
                checks_summary=data.get("checks_summary", {})))
        cards.sort(key=lambda c: c.updated_at, reverse=True)
        return cards

    def get_card(self, project_id: str) -> ProjectCard:
        project = self._project_json(project_id)
        return ProjectCard(
            project_id=project["project_id"], name=project["name"],
            updated_at=project.get("updated_at", ""),
            current_revision_id=project.get("current_revision_id"),
            template_id=project.get("template_id"),
            review_status=project.get("review_status", "DRAFT"),
            latest_run_state=project.get("latest_run_state"),
            component_count=project.get("component_count", 0),
            thumbnail_artifact_id=project.get("thumbnail_artifact_id"),
            archived=bool(project.get("archived")),
            source_kind=project.get("source_kind", "template"),
            checks_summary=project.get("checks_summary", {}))

    def load_revision(self, project_id: str, revision_id: str) -> Snapshot:
        rdir = self._revision_dir(project_id, revision_id)
        meta_data = read_json(rdir / "revision.json")
        if not meta_data:
            raise NotFound(f"Revision {revision_id} not found.", code="REVISION_NOT_FOUND")
        meta = ProjectRevisionMeta.model_validate(meta_data)
        return Snapshot(
            project_id=project_id, revision=meta,
            requirement=RequirementSpec.model_validate(read_json(rdir / "requirement.json")
                                                      or meta.requirement.model_dump()),
            circuit=CircuitIR.model_validate(read_json(rdir / "circuit.json")),
            schematic=SchematicIR.model_validate(read_json(rdir / "schematic.json")),
            board=BoardIR.model_validate(read_json(rdir / "board.json")),
            checks=ValidationReport.model_validate(read_json(rdir / "checks.json")))

    def open_current(self, project_id: str) -> Snapshot:
        revision_id = self.current_revision_id(project_id)
        if not revision_id:
            raise NotFound("Project has no committed revision.", code="NO_CURRENT_REVISION")
        return self.load_revision(project_id, revision_id)

    def list_revisions(self, project_id: str) -> list[RevisionSummary]:
        current = self.current_revision_id(project_id)
        rows: list[RevisionSummary] = []
        rdir = self.project_dir(project_id) / "revisions"
        for folder in sorted(rdir.iterdir()):
            if not folder.is_dir() or folder.name.startswith("."):
                continue
            data = read_json(folder / "revision.json")
            if not data:
                continue
            meta = ProjectRevisionMeta.model_validate(data)
            rows.append(RevisionSummary(
                revision_id=meta.revision_id, created_at=meta.created_at,
                review_status=meta.review_status, summary=meta.summary,
                parent_revision_id=meta.parent_revision_id,
                is_current=meta.revision_id == current,
                run_id=meta.commit_meta.run_id,
                component_count=meta.component_count,
                checks_summary=meta.checks_summary,
                restored_from_revision_id=meta.restored_from_revision_id))
        rows.sort(key=lambda r: r.revision_id)
        return rows

    def get_artifact_ref(self, project_id: str, revision_id: str, artifact_id: str) -> ArtifactRef:
        rdir = self._revision_dir(project_id, revision_id)
        rows = read_json(rdir / "artifacts.json", default=[]) or []
        for row in rows:
            if row.get("artifact_id") == artifact_id:
                return ArtifactRef.model_validate(row)
        raise NotFound("Artifact not found.", code="ARTIFACT_NOT_FOUND")

    # ------------------------------------------------------------------ drafts
    def begin_draft(self, project_id: str, base_revision_id: str | None,
                    run_id: str | None) -> DesignDraft:
        """Idempotent per run_id: an existing draft is returned unchanged."""
        pdir = self.project_dir(project_id)
        draft_id = f"draft_{run_id}" if run_id else "draft_edit_" + secrets.token_hex(6)
        path = pdir / "drafts" / (run_id or draft_id) / "draft.json"
        if path.exists():
            return DesignDraft.from_dict(read_json(path))
        base_id = base_revision_id or self.current_revision_id(project_id)
        if base_id is None:
            raise NotFound("Project has no base revision.", code="NO_BASE_REVISION")
        base = self.load_revision(project_id, base_id)
        draft = DesignDraft(
            draft_id=draft_id, project_id=project_id, base_revision_id=base_id,
            run_id=run_id, draft_seq=0, requirement=base.requirement,
            circuit=base.circuit, schematic=base.schematic, board=base.board,
            checks=None, created_at=utcnow(), updated_at=utcnow(),
            parameters={k: v.value for k, v in base.requirement.parameters.items()})
        self.save_draft(draft)
        return draft

    def save_draft(self, draft: DesignDraft) -> None:
        draft.updated_at = utcnow()
        folder = Path("drafts") / (draft.run_id or draft.draft_id)
        write_json_atomic(self.project_dir(draft.project_id) / folder / "draft.json",
                          draft.to_dict())

    def load_draft(self, project_id: str, draft_id: str) -> DesignDraft:
        pdir = self.project_dir(project_id)
        for folder in (pdir / "drafts").iterdir():
            path = folder / "draft.json"
            if path.exists():
                data = read_json(path)
                if data.get("draft_id") == draft_id:
                    return DesignDraft.from_dict(data)
        raise NotFound("Draft not found.", code="DRAFT_NOT_FOUND")

    def discard_draft(self, project_id: str, draft_id: str) -> None:
        pdir = self.project_dir(project_id)
        for folder in (pdir / "drafts").iterdir():
            path = folder / "draft.json"
            if path.exists() and (read_json(path) or {}).get("draft_id") == draft_id:
                shutil.rmtree(folder, ignore_errors=True)
                return

    # ------------------------------------------------------------------ commit
    def commit(self, draft: DesignDraft, expected_revision_id: str,
               *, run_id: str | None, idempotency_key: str | None,
               summary: str = "", source: str = "run",
               build_manifest: BuildManifest | None = None,
               restored_from_revision_id: str | None = None,
               request_hash: str = "",
               cancel_check=None) -> ProjectRevisionMeta:
        if draft.schematic is None or draft.board is None or draft.checks is None:
            raise SaveFailed("Draft is incomplete; schematic, board and checks are required.",
                             code="DRAFT_INCOMPLETE")
        project_id = draft.project_id
        with self._lock(project_id):
            index = read_json(self.project_dir(project_id) / "commit_index.json",
                              default={"schema_version": 1, "entries": []})
            entries = index.setdefault("entries", [])
            if idempotency_key:
                for entry in entries:
                    if entry.get("idempotency_key") != idempotency_key:
                        continue
                    stored_hash = entry.get("request_hash") or ""
                    if stored_hash and request_hash and stored_hash != request_hash:
                        raise IdempotencyConflict(
                            "This idempotency key was used for a different request.",
                            code="IDEMPOTENCY_CONFLICT")
                    if entry.get("status") == "COMMITTED" and entry.get("revision_id"):
                        return self.load_revision(project_id, entry["revision_id"]).revision
                    raise SaveFailed("Commit already prepared; recovering it.",
                                     code="COMMIT_PREPARED")
            current = self.current_revision_id(project_id)
            if current != expected_revision_id:
                raise RevisionConflict(
                    "This project changed. Reload the latest revision to continue.",
                    data={"current_revision_id": current,
                          "expected_revision_id": expected_revision_id})
            if cancel_check and cancel_check():
                raise Conflict("Cancelled before commit.", code="CANCELLED_BEFORE_COMMIT")
            revision_id = self._next_revision_id(project_id)
            meta = self._write_revision(
                project_id, revision_id, current, draft.requirement, draft.circuit,
                draft.schematic, draft.board, draft.checks,
                build_manifest or BuildManifest(),
                commit_meta=CommitMeta(
                    commit_id="commit_" + secrets.token_hex(8), run_id=run_id,
                    idempotency_key=idempotency_key, parent_revision_id=current,
                    draft_id=draft.draft_id, draft_seq=draft.draft_seq,
                    source=source,  # type: ignore[arg-type]
                    restored_from_revision_id=restored_from_revision_id),
                summary=summary or f"Build {draft.draft_id} seq {draft.draft_seq}",
                restored_from_revision_id=restored_from_revision_id)
            if idempotency_key:
                entries.append({
                    "idempotency_key": idempotency_key, "run_id": run_id,
                    "request_hash": request_hash,
                    "status": "PREPARED", "revision_id": revision_id,
                    "created_at": utcnow()})
                write_json_atomic(self.project_dir(project_id) / "commit_index.json", index)
            import os

            if os.environ.get("MOTA_TEST_FAULT") == "after_prepared":
                raise SaveFailed("Injected fault after PREPARED.", code="INJECTED_FAULT")
            if cancel_check and cancel_check():
                # Cancellation before the commit point keeps the old current; the
                # prepared revision stays as an uncommitted candidate.
                raise Conflict("Cancelled before commit point.", code="CANCELLED_BEFORE_COMMIT")
            now = utcnow()
            write_json_atomic(self.project_dir(project_id) / "current.json", {
                "revision_id": revision_id, "commit_id": meta.commit_meta.commit_id,
                "updated_at": now})
            # COMMITTED index update
            for entry in entries:
                if entry.get("revision_id") == revision_id:
                    entry["status"] = "COMMITTED"
            write_json_atomic(self.project_dir(project_id) / "commit_index.json", index)
            project = self._project_json(project_id)
            project.update({
                "current_revision_id": revision_id,
                "updated_at": now,
                "review_status": meta.review_status,
                "component_count": meta.component_count,
                "checks_summary": meta.checks_summary,
                "latest_run_state": "SUCCEEDED" if meta.review_status == "READY_FOR_REVIEW" else "PARTIAL",
            })
            self._save_project(project_id, project)
            return meta

    def _next_revision_id(self, project_id: str) -> str:
        rdir = self.project_dir(project_id) / "revisions"
        highest = 0
        for folder in rdir.iterdir():
            if folder.is_dir() and folder.name.startswith("rev_"):
                try:
                    highest = max(highest, int(folder.name.split("_")[1]))
                except (IndexError, ValueError):
                    continue
        return f"rev_{highest + 1:06d}"

    def recover_commits(self, project_id: str) -> dict[str, Any]:
        with self._lock(project_id):
            pdir = self.project_dir(project_id)
            index = read_json(pdir / "commit_index.json", default={"entries": []}) or {}
            current = self.current_revision_id(project_id)
            recovered = []
            for entry in index.get("entries", []):
                revision_id = entry.get("revision_id")
                if not revision_id:
                    continue
                exists = self._revision_dir(project_id, revision_id).exists()
                if entry.get("status") == "PREPARED" and exists and revision_id == current:
                    entry["status"] = "COMMITTED"
                    recovered.append(entry)
            if recovered:
                write_json_atomic(pdir / "commit_index.json", index)
            return {"recovered": recovered, "current_revision_id": current}

    # --------------------------------------------------------------- mutations
    def rename(self, project_id: str, name: str) -> ProjectCard:
        with self._lock(project_id):
            data = self._project_json(project_id)
            data["name"] = name.strip() or data["name"]
            data["updated_at"] = utcnow()
            self._save_project(project_id, data)
        return self.get_card(project_id)

    def set_archived(self, project_id: str, archived: bool) -> ProjectCard:
        with self._lock(project_id):
            data = self._project_json(project_id)
            data["archived"] = archived
            data["updated_at"] = utcnow()
            self._save_project(project_id, data)
        return self.get_card(project_id)

    def delete(self, project_id: str, confirm_project_id: str) -> None:
        if confirm_project_id != project_id:
            raise InvalidRequest("Deletion confirmation does not match the project.",
                                 code="CONFIRMATION_MISMATCH")
        self._require_not_busy(project_id)
        with self._lock(project_id):
            pdir = self.project_dir(project_id)
            shutil.rmtree(pdir)

    def set_latest_run_state(self, project_id: str, state: str) -> None:
        with self._lock(project_id):
            data = self._project_json(project_id)
            data["latest_run_state"] = state
            data["updated_at"] = utcnow()
            self._save_project(project_id, data)

    # ------------------------------------------------------------------ runs
    def run_dir(self, project_id: str, run_id: str) -> Path:
        return paths.ensure_dir(self.project_dir(project_id) / "runs" / run_id)


def canonical_design_hash(requirement: RequirementSpec, circuit: CircuitIR,
                          board: BoardIR) -> str:
    """Deterministic semantic/geometry hash, independent of random UUIDs/time."""
    comp_key = {c.id: c.reference for c in circuit.components}
    comps = sorted(
        ({"reference": c.reference, "part_id": c.part_id, "value": c.value,
          "footprint_id": c.footprint_id, "pin_pad_map": c.pin_pad_map}
         for c in circuit.components),
        key=lambda row: json.dumps(row, sort_keys=True))
    nets = sorted(
        ({"name": n.name, "class": n.net_class,
          "pins": sorted(f"{comp_key.get(p.split(':')[0], p.split(':')[0])}:{p.split(':', 1)[1]}"
                         for p in n.pins)}
         for n in circuit.nets),
        key=lambda row: json.dumps(row, sort_keys=True))
    tracks = sorted(
        ({"net": next((n.name for n in circuit.nets if n.id == t.net_id), t.net_id),
          "layer": t.layer, "width_nm": t.width_nm, "points": t.points}
         for t in board.tracks),
        key=lambda row: json.dumps(row, sort_keys=True))
    pads = sorted(
        ({"ref": next((c.reference for c in circuit.components if c.id == p.component_id), ""),
          "number": p.number,
          "net": next((n.name for n in circuit.nets if n.id == p.net_id), None),
          "x_nm": p.x_nm, "y_nm": p.y_nm}
         for p in board.pads),
        key=lambda row: json.dumps(row, sort_keys=True))
    payload = {
        "template_id": requirement.template_id,
        "parameters": {k: v.value for k, v in sorted(requirement.parameters.items())},
        "components": comps, "nets": nets, "tracks": tracks, "pads": pads,
        "board": {"width_nm": board.width_nm, "height_nm": board.height_nm,
                  "thickness_nm": board.thickness_nm, "holes": sorted(
                      (h.x_nm, h.y_nm, h.drill_nm) for h in board.holes)},
    }
    return sha256_bytes(json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8"))
