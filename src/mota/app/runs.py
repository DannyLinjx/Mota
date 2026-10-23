"""Run orchestration: deterministic template builds and agent builds.

Every event in this module is produced by an actual step of the engine. There is
no progress simulation: state changes follow real computation, and commit is the
atomic `current.json` replacement performed by ProjectStore.
"""

from __future__ import annotations

import json
import secrets
import threading
import time
import traceback
from pathlib import Path
from typing import Any, Callable

from mota.app import scenes as scene_store
from mota.app.design import DesignService
from mota.app.state import UserScope
from mota.catalog import service as catalog_service
from mota.catalog.templates import get_template
from mota.domain.models import (
    BoardIR, BuildManifest, DesignPatch, DesignVersion, Question, RequirementSpec,
    RunEvent, RunRecord, ValidationReport, utcnow,
)
from mota.domain.units import mm
from mota.errors import (
    AuthRequired, Conflict, ModelUnavailable, MotaError, NotFound, ProjectBusy,
    RevisionConflict,
)
from mota.generation.board import build_board_ir
from mota.generation.modules import build_circuit
from mota.generation.routing import (
    Cancelled, RouteConstraints, route_board, route_budget_seconds,
)
from mota.generation.schematic import generate_schematic
from mota.memory.service import MemoryService
from mota.projects.store import DesignDraft, ProjectStore
from mota.validation.reports import validate_all, validate_electrical
from mota.workspace.atomic import append_jsonl, read_json, read_jsonl, write_json_atomic


def provisional_ratsnest(board: BoardIR):
    from mota.domain.models import RatsnestLine

    pads_by_net: dict[str, list] = {}
    for pad in board.pads:
        if pad.net_id:
            pads_by_net.setdefault(pad.net_id, []).append(pad)
    lines = []
    for net_id, pads in pads_by_net.items():
        for a, b in zip(pads, pads[1:]):
            lines.append(RatsnestLine(net_id=net_id, x1_nm=a.x_nm, y1_nm=a.y_nm,
                                      x2_nm=b.x_nm, y2_nm=b.y_nm))
    return lines


class RunService:
    def __init__(self, root: Path | None = None,
                 emit: Callable[[dict[str, Any]], None] | None = None,
                 secrets=None) -> None:
        self.root = root
        self.emit = emit or (lambda event: None)
        self.secrets = secrets
        self.design = DesignService()
        self._threads: dict[str, threading.Thread] = {}
        self._cancel: dict[str, threading.Event] = {}
        self._answers: dict[str, threading.Event] = {}
        self._runs: dict[str, RunRecord] = {}
        self._lock = threading.RLock()

    # ------------------------------------------------------------------ helpers
    def _store(self, owner: UserScope) -> ProjectStore:
        return ProjectStore(owner, self.root)

    def _run_dir(self, store: ProjectStore, project_id: str, run_id: str) -> Path:
        return store.run_dir(project_id, run_id)

    def _save_run(self, store: ProjectStore, run: RunRecord) -> None:
        run.updated_at = utcnow()
        write_json_atomic(self._run_dir(store, run.project_id, run.run_id) / "run.json",
                          run.model_dump())

    def _load_run(self, store: ProjectStore, project_id: str, run_id: str) -> RunRecord:
        data = read_json(self._run_dir(store, project_id, run_id) / "run.json")
        if not data:
            raise NotFound(f"Run {run_id} not found.", code="RUN_NOT_FOUND")
        return RunRecord.model_validate(data)

    def _emit(self, store: ProjectStore, run: RunRecord, event_type: str,
              payload: dict[str, Any]) -> RunEvent:
        seq = run.last_event_seq + 1
        event = RunEvent(
            session_epoch=run.metrics.get("session_epoch", 0),
            event_id=f"evt_{run.run_id}_{seq:05d}", seq=seq,
            project_id=run.project_id, run_id=run.run_id,
            base_revision_id=run.base_revision_id, draft_id=run.draft_id,
            draft_seq=run.draft_seq, type=event_type, payload=payload)
        append_jsonl(self._run_dir(store, run.project_id, run.run_id) / "events.jsonl",
                     event.model_dump())
        run.last_event_seq = seq
        self._save_run(store, run)
        self.emit(event.model_dump())
        return event

    def _state(self, store: ProjectStore, run: RunRecord, state: str,
               message: str = "") -> None:
        run.state = state
        self._save_run(store, run)
        from mota.app.state import RUN_STATE_LABELS

        self._emit(store, run, "run.state_changed", {
            "state": state, "label": RUN_STATE_LABELS.get(state, state),
            "message": message, "draft_id": run.draft_id, "draft_seq": run.draft_seq})

    def _check_cancel(self, run: RunRecord) -> None:
        flag = self._cancel.get(run.run_id)
        if flag is not None and flag.is_set():
            raise Conflict("Cancelled before commit.", code="CANCELLED_BEFORE_COMMIT")

    # --------------------------------------------------------------------- start
    def start(self, owner: UserScope, project_id: str, expected_revision_id: str | None,
              input_payload: dict[str, Any], idempotency_key: str,
              session_epoch: int, session_id: str = "session_cli") -> dict[str, Any]:
        if not idempotency_key:
            raise Conflict("run.start requires an idempotency key.", code="INVALID_REQUEST")
        store = self._store(owner)
        pdir = store.project_dir(project_id)
        request_hash = _hash_request(project_id, expected_revision_id, input_payload)
        with self._lock:
            index_path = pdir / "runs" / "run_index.json"
            index = read_json(index_path, default={"schema_version": 1, "entries": {}}) or {}
            entry = index.setdefault("entries", {}).get(idempotency_key)
            if entry:
                if entry.get("request_hash") != request_hash:
                    raise Conflict("This idempotency key was used for a different request.",
                                   code="IDEMPOTENCY_CONFLICT")
                existing = self._load_run(store, project_id, entry["run_id"])
                return {"run_id": existing.run_id, "draft_id": existing.draft_id,
                        "draft_seq": existing.draft_seq,
                        "parent_run_id": existing.parent_run_id,
                        "state": existing.state, "idempotent_replay": True,
                        "provider_binding": (existing.provider_binding.model_dump()
                                             if existing.provider_binding else None)}
            if store.has_active_run(project_id):
                raise ProjectBusy("Another build is already running for this project.",
                                  data={"project_id": project_id})
            base = expected_revision_id or store.current_revision_id(project_id)
            if base is None:
                raise NotFound("Project has no revision to build on.", code="NO_BASE_REVISION")
            if store.current_revision_id(project_id) != base:
                raise RevisionConflict(
                    "This project changed. Reload the latest revision to continue.",
                    data={"current_revision_id": store.current_revision_id(project_id),
                          "expected_revision_id": base})
            kind = input_payload.get("kind", "template")
            provider_binding = None
            if kind == "template":
                recipe = get_template(input_payload.get("template_id") or "")
                # Validate hard constraints before creating any draft or run.
                recipe.validate_parameters(dict(input_payload.get("parameters") or {}))
            else:
                from mota.app.config import ConfigService

                config_service = ConfigService(owner, self.secrets, self.root)
                profile, key = config_service.get_profile(
                    input_payload.get("profile_id"))
                if not key:
                    raise ModelUnavailable(
                        f"AI connection “{profile.name}” has no API key. Open AI "
                        f"connections to add one, or use a template build.",
                        code="MODEL_UNAVAILABLE",
                        data={"profile_id": profile.profile_id,
                              "next_steps": ["Open AI connections", "Use a template"]})
                provider_binding = config_service.build_binding(profile)
            run_id = "run_" + secrets.token_hex(6)
            draft = store.begin_draft(project_id, base, run_id)
            run = RunRecord(
                run_id=run_id, project_id=project_id, owner_user_id=owner.user_id,
                parent_run_id=None, base_revision_id=base, draft_id=draft.draft_id,
                draft_seq=0, state="CREATED",
                mode="template" if kind == "template" else "agent",
                input_kind=kind, input_text=input_payload.get("text"),
                template_id=input_payload.get("template_id"),
                parameters=dict(input_payload.get("parameters") or {}),
                provider_binding=provider_binding,
                checkpoint_available=True, idempotency_key=idempotency_key,
                request_hash=request_hash, metrics={"session_epoch": session_epoch,
                                                    "session_id": session_id})
            self._save_run(store, run)
            write_json_atomic(self._run_dir(store, project_id, run_id) / "checkpoint.json", {
                "schema_version": 1, "run_id": run_id, "draft_id": draft.draft_id,
                "base_revision_id": base, "draft_seq": 0, "parameters": run.parameters,
                "template_id": run.template_id, "created_at": utcnow()})
            index["entries"][idempotency_key] = {
                "request_hash": request_hash, "run_id": run_id,
                "status": "ACCEPTED", "created_at": utcnow(), "base_revision_id": base}
            write_json_atomic(index_path, index)
            self._runs[run_id] = run
            self._cancel[run_id] = threading.Event()
        thread = threading.Thread(target=self._execute, args=(owner, run),
                                  name=f"mota-{run_id}", daemon=True)
        self._threads[run_id] = thread
        thread.start()
        return {"run_id": run_id, "draft_id": draft.draft_id, "draft_seq": draft.draft_seq,
                "parent_run_id": None, "state": "CREATED",
                "provider_binding": (run.provider_binding.model_dump()
                                     if run.provider_binding else None)}

    # ---------------------------------------------------------------- execution
    def _execute(self, owner: UserScope, run: RunRecord) -> None:
        store = self._store(owner)
        started = time.monotonic()
        metrics = dict(run.metrics)
        metrics.setdefault("tool_calls", 0)
        metrics.setdefault("model_calls", 0)
        metrics.setdefault("first_event_ms", 0)
        try:
            if run.mode == "template":
                self._run_template(owner, store, run)
            else:
                self._run_agent(owner, store, run)
        except Conflict as exc:
            if exc.code in ("CANCELLED_BEFORE_COMMIT", "CANCELLED"):
                run.state = "CANCELLED"
                run.checkpoint_available = True
                self._save_run(store, run)
                self._emit(store, run, "run.completed", {
                    "state": "CANCELLED", "message": "Build cancelled before commit; "
                    "the previous revision is unchanged.", "checkpoint_available": True})
            else:
                self._fail(store, run, exc)
        except MotaError as exc:
            self._fail(store, run, exc)
        except Exception as exc:  # noqa: BLE001 - terminal safety
            self._fail(store, run, MotaError(f"Engine error: {exc}", code="RUN_ERROR"))
            (self._run_dir(store, run.project_id, run.run_id) / "traceback.txt").write_text(
                traceback.format_exc(), encoding="utf-8")
        finally:
            metrics["wall_ms"] = int((time.monotonic() - started) * 1000)
            run.metrics.update(metrics)
            self._save_run(store, run)
            write_json_atomic(self._run_dir(store, run.project_id, run.run_id) / "metrics.json",
                              run.metrics)
            try:
                store.set_latest_run_state(run.project_id, run.state)
            except Exception:
                pass
            self._threads.pop(run.run_id, None)

    def _fail(self, store: ProjectStore, run: RunRecord, exc: MotaError) -> None:
        run.state = "FAILED"
        run.error = exc.to_error()
        self._save_run(store, run)
        self._emit(store, run, "run.completed", {
            "state": "FAILED", "error": exc.to_error(),
            "message": exc.message,
            "previous_revision_id": store.current_revision_id(run.project_id)})

    def _run_template(self, owner: UserScope, store: ProjectStore, run: RunRecord) -> None:
        t0 = time.monotonic()
        metrics = run.metrics
        metrics["request_received_at"] = utcnow()
        draft = store.load_draft(run.project_id, run.draft_id or "")
        template_id = run.template_id or ""
        recipe = get_template(template_id)
        self._state(store, run, "CREATED", "Starting build")
        metrics["first_event_ms"] = int((time.monotonic() - t0) * 1000)

        self._state(store, run, "EXTRACTING", "Reading requirements")
        parameters = recipe.validate_parameters(run.parameters)
        self._emit(store, run, "assistant.summary", {
            "text": f"Template build: {recipe.title} v{recipe.version}. "
                    f"Deterministic Mota engine; no model calls.",
            "mode": "template"})

        self._state(store, run, "PLANNING", "Preparing a build plan")
        call = self._tool_started(store, run, "list_templates", {"scenario": template_id})
        result = {"templates": [{"id": recipe.id, "title": recipe.title,
                                 "status": recipe.effective_status,
                                 "parts": [p.reference for p in recipe.parts]}]}
        self._tool_completed(store, run, "list_templates", call, result)
        self._emit(store, run, "assistant.summary", {
            "text": "Plan: resolve " + ", ".join(p.reference for p in recipe.parts)
                    + "; build schematic; place; route; validate; save.",
            "selected_template": recipe.id})

        self._state(store, run, "RESOLVING", "Resolving parts and rules")
        seen_parts: set[str] = set()
        for spec in recipe.parts:
            if spec.part_id in seen_parts:
                continue
            seen_parts.add(spec.part_id)
            call = self._tool_started(store, run, "inspect_part", {"asset_id": spec.part_id})
            detail = catalog_service.inspect(spec.part_id)
            self._tool_completed(store, run, "inspect_part", call, {
                "mpn": detail["mpn"], "package": detail["part"]["package"],
                "pin_count": len(detail["pins"]),
                "capabilities": detail["part"]["capabilities"],
                "pin_pad_map": detail["pin_pad_map"],
                "diagnostics": detail["diagnostics"]})
        self._check_cancel(run)

        self._state(store, run, "SYNTHESIZING", "Building the schematic")
        call = self._tool_started(store, run, "prepare_design",
                                  {"template_id": template_id,
                                   "parameters": parameters})
        circuit, requirement = build_circuit(recipe, parameters)
        schematic = generate_schematic(circuit, title=recipe.title)
        width_nm = mm(float(requirement.board_size["width_mm"]))
        height_nm = mm(float(requirement.board_size["height_mm"]))
        empty_board = BoardIR(outline=[[0, 0], [width_nm, 0], [width_nm, height_nm],
                                      [0, height_nm], [0, 0]],
                              width_nm=width_nm, height_nm=height_nm,
                              thickness_nm=mm(float(recipe.board.get("thickness_mm", 1.6))))
        draft.requirement = requirement
        draft.circuit = circuit
        draft.schematic = schematic
        draft.board = empty_board
        draft.parameters = parameters
        draft.draft_seq += 1
        store.save_draft(draft)
        run.draft_seq = draft.draft_seq
        scene_json, scene_artifact = scene_store.save_draft_scene(store, draft, "schematic")
        self._emit(store, run, "design.patch_applied", {
            "summary": "Prepared the circuit and schematic",
            "draft_id": draft.draft_id, "draft_seq": draft.draft_seq,
            "added_components": len(circuit.components),
            "added_nets": len(circuit.nets),
            "object_ids": [c.id for c in circuit.components],
            "scene_artifact_id": scene_artifact})
        self._emit(store, run, "scene.updated", {
            "view": "schematic", "design_version": draft.version.to_param(),
            "design_version_key": draft.version.key,
            "scene_artifact_id": scene_artifact,
            "bounds": scene_json["bounds"], "object_count": len(scene_json["objects"])})
        self._tool_completed(store, run, "prepare_design", call, {
            "components": len(circuit.components), "nets": len(circuit.nets),
            "draft_seq": draft.draft_seq, "scene_artifact_id": scene_artifact})
        metrics["first_schematic_ms"] = int((time.monotonic() - t0) * 1000)

        self._state(store, run, "VALIDATING_SCHEMATIC", "Checking connections")
        call = self._tool_started(store, run, "validate_design",
                                  {"design_version": draft.version.to_param()})
        electrical = validate_electrical(run.project_id, draft.version, requirement,
                                         circuit, schematic, empty_board, recipe,
                                         include_board_consistency=False)
        self._tool_completed(store, run, "validate_design", call, {
            "review_status": electrical.review_status, "counts": electrical.counts})
        self._emit(store, run, "validation.completed", {
            "design_version": draft.version.to_param(),
            "design_version_key": draft.version.key,
            "review_status": electrical.review_status,
            "counts": electrical.counts,
            "checks": [c.model_dump() for c in electrical.checks]})
        blocking = [c for c in electrical.checks
                    if c.required_for_review and c.status == "FAIL"]
        if blocking:
            raise Conflict(
                "Schematic checks failed: " + blocking[0].message, code="SCHEMATIC_INVALID")
        self._check_cancel(run)

        self._state(store, run, "PLACING", "Placing components")
        call = self._tool_started(store, run, "build_board",
                                  {"phase": "placement", "policy": "module_rules_v1"})
        board, moved = build_board_ir(circuit, recipe, width_nm, height_nm)
        board = board.model_copy(update={"ratsnest": provisional_ratsnest(board)})
        draft.board = board
        draft.draft_seq += 1
        store.save_draft(draft)
        run.draft_seq = draft.draft_seq
        scene_json, scene_artifact = scene_store.save_draft_scene(store, draft, "pcb2d")
        self._emit(store, run, "design.patch_applied", {
            "summary": "Placed components", "draft_id": draft.draft_id,
            "draft_seq": draft.draft_seq, "moved": moved,
            "object_ids": [f.component_id for f in board.footprints],
            "scene_artifact_id": scene_artifact})
        self._emit(store, run, "scene.updated", {
            "view": "pcb2d", "design_version": draft.version.to_param(),
            "design_version_key": draft.version.key,
            "scene_artifact_id": scene_artifact,
            "bounds": scene_json["bounds"], "object_count": len(scene_json["objects"])})
        metrics["placement_ms"] = int((time.monotonic() - t0) * 1000)

        self._state(store, run, "ROUTING", "Routing copper")
        route_started = time.monotonic()
        net_classes = {net.id: net.net_class for net in circuit.nets}
        cancel_flag = self._cancel.get(run.run_id)
        try:
            board, unrouted = route_board(
                board, RouteConstraints(), net_classes,
                cancel=(lambda: cancel_flag.is_set()) if cancel_flag else None,
                time_budget=route_budget_seconds())
        except Cancelled:
            raise Conflict("Cancelled before commit.", code="CANCELLED_BEFORE_COMMIT")
        board = board.model_copy(update={"ratsnest": [
            line for line in board.ratsnest if line.net_id in unrouted]})
        draft.board = board
        draft.draft_seq += 1
        store.save_draft(draft)
        run.draft_seq = draft.draft_seq
        scene_json, scene_artifact = scene_store.save_draft_scene(store, draft, "pcb2d")
        self._emit(store, run, "design.patch_applied", {
            "summary": f"Routed {len(board.tracks)} track segments"
                       + (f"; {len(unrouted)} net(s) unresolved" if unrouted else ""),
            "draft_id": draft.draft_id, "draft_seq": draft.draft_seq,
            "tracks": len(board.tracks), "vias": len(board.vias),
            "unrouted_nets": unrouted, "scene_artifact_id": scene_artifact})
        self._emit(store, run, "scene.updated", {
            "view": "pcb2d", "design_version": draft.version.to_param(),
            "design_version_key": draft.version.key,
            "scene_artifact_id": scene_artifact,
            "bounds": scene_json["bounds"], "object_count": len(scene_json["objects"])})
        self._tool_completed(store, run, "build_board", call, {
            "tracks": len(board.tracks), "vias": len(board.vias),
            "unrouted_nets": unrouted, "draft_seq": draft.draft_seq,
            "scene_artifact_id": scene_artifact})
        metrics["routing_ms"] = int((time.monotonic() - route_started) * 1000)

        self._state(store, run, "VALIDATING_BOARD", "Checking the board")
        call = self._tool_started(store, run, "validate_design",
                                  {"design_version": draft.version.to_param(),
                                   "phase": "board"})
        checks = validate_all(run.project_id, draft.version, requirement, circuit,
                              schematic, board, recipe)
        draft.checks = checks
        store.save_draft(draft)
        self._tool_completed(store, run, "validate_design", call, {
            "review_status": checks.review_status, "counts": checks.counts})
        self._emit(store, run, "validation.completed", {
            "design_version": draft.version.to_param(),
            "design_version_key": draft.version.key,
            "review_status": checks.review_status,
            "counts": checks.counts,
            "checks": [c.model_dump() for c in checks.checks]})
        metrics["validation_ms"] = int((time.monotonic() - t0) * 1000)
        self._check_cancel(run)

        self._state(store, run, "RENDERING", "Preparing views")
        call = self._tool_started(store, run, "render_preview",
                                  {"design_version": draft.version.to_param(), "views": [
                                      "schematic", "pcb2d", "pcb3d"]})
        artifacts: dict[str, str] = {}
        for view in ("schematic", "pcb2d", "pcb3d"):
            scene_json, artifact = scene_store.save_draft_scene(store, draft, view)
            artifacts[view] = artifact
            self._emit(store, run, "scene.updated", {
                "view": view, "design_version": draft.version.to_param(),
                "design_version_key": draft.version.key,
                "scene_artifact_id": artifact,
                "bounds": scene_json["bounds"],
                "object_count": len(scene_json["objects"]),
                "missing_models": len(scene_json.get("missing_models", []))})
            self._emit(store, run, "artifact.created", {
                "artifact_id": artifact, "kind": f"scene_{view}",
                "design_version": draft.version.to_param()})
        self._tool_completed(store, run, "render_preview", call,
                             {"artifacts": artifacts, "views": sorted(artifacts),
                              "object_count": sum(1 for _ in artifacts)})
        metrics["rendering_ms"] = int((time.monotonic() - t0) * 1000)
        self._check_cancel(run)

        self._state(store, run, "COMMITTING", "Saving revision")
        summary = (f"{recipe.title}: {len(circuit.components)} components, "
                   f"{len(circuit.nets)} nets, {len(board.tracks)} track segments")
        manifest = BuildManifest(
            mode="template", template_id=recipe.id, template_version=recipe.version,
            model=None, recipe_version=recipe.version,
            library_snapshot_id=circuit.library_snapshot_id,
            rule_profile_id=circuit.rule_profile_id,
            input_hash=run.request_hash,
            metrics={"tool_calls": metrics.get("tool_calls", 0), "model_calls": 0})
        meta = store.commit(draft, run.base_revision_id or "",
                            run_id=run.run_id, idempotency_key=run.idempotency_key,
                            summary=summary, source="run", build_manifest=manifest,
                            request_hash=run.request_hash,
                            cancel_check=lambda: self._cancel[run.run_id].is_set())
        run.committed_revision_id = meta.revision_id
        run.draft_seq = draft.draft_seq
        run.checkpoint_available = False
        snapshot = store.load_revision(run.project_id, meta.revision_id)
        scene_store.write_revision_scenes(snapshot, store)
        self._emit(store, run, "project.revision_committed", {
            "revision_id": meta.revision_id, "review_status": meta.review_status,
            "checks_summary": meta.checks_summary,
            "commit_id": meta.commit_meta.commit_id,
            "parent_revision_id": meta.parent_revision_id,
            "committed_at": meta.created_at})
        final_state = "SUCCEEDED" if meta.review_status == "READY_FOR_REVIEW" else "PARTIAL"
        run.state = final_state
        run.review_status = meta.review_status
        self._save_run(store, run)
        unresolved = [c.check_id for c in checks.checks
                      if c.required_for_review and c.status != "PASS"]
        self._emit(store, run, "run.completed", {
            "state": final_state, "revision_id": meta.revision_id,
            "review_status": meta.review_status,
            "counts": checks.counts, "unresolved": unresolved,
            "unrouted_nets": board.unrouted_nets,
            "message": "Build saved" if final_state == "SUCCEEDED"
                       else "Build has unresolved items"})
        metrics["components"] = len(circuit.components)
        metrics["nets"] = len(circuit.nets)
        metrics["unrouted_count"] = len(board.unrouted_nets)
        self._record_memory(owner, run, recipe, requirement, meta.revision_id,
                            checks, final_state)

    # ------------------------------------------------------------------- agent
    def _run_agent(self, owner: UserScope, store: ProjectStore, run: RunRecord) -> None:
        from mota.agent.runner import AgentRunner

        runner = AgentRunner(owner, store, run, self, self.root,
                             secrets=self.secrets)
        runner.run()

    # ------------------------------------------------------------------- tools
    def _tool_started(self, store: ProjectStore, run: RunRecord, tool: str,
                      arguments: dict[str, Any]) -> str:
        run.metrics["tool_calls"] = int(run.metrics.get("tool_calls", 0)) + 1
        call_id = f"call_{run.metrics['tool_calls']:03d}"
        self._emit(store, run, "tool.started", {
            "call_id": call_id, "tool": tool, "arguments": arguments,
            "started_at": utcnow()})
        run.metrics.setdefault("_tool_started", {})[call_id] = time.monotonic()
        return call_id

    def _tool_completed(self, store: ProjectStore, run: RunRecord, tool: str,
                        call_id: str, result: dict[str, Any]) -> None:
        started = run.metrics.get("_tool_started", {}).pop(call_id, None)
        duration = int((time.monotonic() - started) * 1000) if started else 0
        self._emit(store, run, "tool.completed", {
            "call_id": call_id, "tool": tool, "result": result,
            "duration_ms": duration, "completed_at": utcnow()})

    def tool_failed(self, store: ProjectStore, run: RunRecord, tool: str,
                    call_id: str, exc: MotaError) -> None:
        started = run.metrics.get("_tool_started", {}).pop(call_id, None)
        duration = int((time.monotonic() - started) * 1000) if started else 0
        self._emit(store, run, "tool.failed", {
            "call_id": call_id, "tool": tool, "error": exc.to_error(),
            "duration_ms": duration, "retryable": False})

    def tool_started(self, store: ProjectStore, run: RunRecord, tool: str,
                     args: dict[str, Any]) -> str:
        return self._tool_started(store, run, tool, args)

    def tool_completed(self, store: ProjectStore, run: RunRecord, tool: str,
                       call_id: str, result: dict[str, Any]) -> None:
        self._tool_completed(store, run, tool, call_id, result)

    # ------------------------------------------------------------------ memory
    def _record_memory(self, owner: UserScope, run: RunRecord, recipe,
                       requirement: RequirementSpec, revision_id: str,
                       checks: ValidationReport, final_state: str) -> None:
        try:
            memory = MemoryService(owner, self.root)
            session_id = run.metrics.get("session_id", "session_cli")
            memory.record_conversation(session_id, run.project_id, [
                {"message_id": f"{run.run_id}_user", "role": "user",
                 "run_id": run.run_id, "at": run.metrics.get("request_received_at", utcnow()),
                 "text": run.input_text or f"Build {recipe.title} with defaults"},
                {"message_id": f"{run.run_id}_assistant", "role": "assistant",
                 "run_id": run.run_id, "text": f"{final_state}: revision {revision_id}; "
                 f"checks {checks.counts}."},
            ])
            memory.record_working(
                session_id, run.project_id,
                f"Built {recipe.title} ({revision_id}).",
                {"current_revision": revision_id,
                 "run_id": run.run_id,
                 "review_status": checks.review_status,
                 "unresolved": ", ".join(
                     c.check_id for c in checks.checks
                     if c.required_for_review and c.status != "PASS") or "none"})
            verified = checks.review_status == "READY_FOR_REVIEW"
            blocking = [c for c in checks.checks
                        if c.required_for_review and c.status != "PASS"]
            memory.record_experience(
                run.project_id,
                title=(f"{recipe.id} builds to {checks.review_status}"),
                conditions=f"Template {recipe.id} v{recipe.version}, parameters "
                           f"{ {k: v.value for k, v in requirement.parameters.items()} }.",
                observation=(f"Run {run.run_id} produced revision {revision_id} with "
                             f"{len(checks.checks)} checks; counts {checks.counts}."),
                resolution=("All required checks passed." if verified else
                            "Blocking checks: " + ", ".join(
                                f"{c.check_id}={c.status}" for c in blocking)),
                evidence=(f"run {run.run_id}, revision {revision_id}; "
                          "see checks.json in the project revisions."),
                source_ids=[f"{run.run_id}/{revision_id}"],
                module_id=recipe.id, module_version=recipe.version,
                status="verified" if verified else "candidate",
                evidence_run_id=run.run_id, evidence_revision_id=revision_id)
        except Exception:
            # Memory failure must not corrupt the committed engineering result.
            pass

    # ----------------------------------------------------------------- control
    def answer(self, owner: UserScope, run_id: str, question_id: str,
               answer: dict[str, Any], session_epoch: int) -> dict[str, Any]:
        store = self._store(owner)
        project_id, run_project = self._find_run_project(store, run_id)
        run = self._load_run(store, project_id, run_id)
        questions_path = self._run_dir(store, project_id, run_id) / "questions.json"
        questions = read_json(questions_path, default=[]) or []
        question = next((q for q in questions if q.get("question_id") == question_id), None)
        if question is None:
            raise NotFound("Question not found for this run.", code="QUESTION_NOT_FOUND")
        if question.get("consumed_at"):
            if question.get("answer") == answer:
                return {"accepted": True, "run_id": run_id, "question_id": question_id,
                        "state": run.state, "idempotent_replay": True}
            raise Conflict("This question has already been answered.",
                           code="QUESTION_CONSUMED")
        if run.state != "WAITING_FOR_USER":
            raise Conflict("This run is not waiting for an answer.", code="RUN_NOT_WAITING")
        question["answer"] = answer
        question["consumed_at"] = utcnow()
        write_json_atomic(questions_path, questions)
        run.question_id = question_id
        self._save_run(store, run)
        self._emit(store, run, "assistant.summary", {
            "text": "Answer received; resuming the same run.", "answer": answer})
        event = self._answers.get(run_id)
        if event:
            event.set()
        return {"accepted": True, "run_id": run_id, "question_id": question_id,
                "state": "RUNNING"}

    def cancel(self, owner: UserScope, run_id: str) -> dict[str, Any]:
        store = self._store(owner)
        project_id, _ = self._find_run_project(store, run_id)
        run = self._load_run(store, project_id, run_id)
        if run.state in ("SUCCEEDED", "PARTIAL", "FAILED", "CANCELLED", "RECOVERABLE"):
            if run.committed_revision_id:
                return {"outcome": "already_committed",
                        "committed_revision_id": run.committed_revision_id,
                        "state": run.state}
            return {"outcome": "already_terminal", "state": run.state}
        run.cancel_requested = True
        self._save_run(store, run)
        flag = self._cancel.setdefault(run_id, threading.Event())
        flag.set()
        event = self._answers.get(run_id)
        if event:
            event.set()
        return {"outcome": "cancel_requested", "run_id": run_id, "state": run.state}

    def resume(self, owner: UserScope, run_id: str, expected_revision_id: str | None,
               session_epoch: int, session_id: str = "session_cli") -> dict[str, Any]:
        store = self._store(owner)
        project_id, _ = self._find_run_project(store, run_id)
        old = self._load_run(store, project_id, run_id)
        if old.state not in ("CANCELLED", "RECOVERABLE"):
            raise Conflict("Only cancelled or recoverable runs can be resumed.",
                           code="RUN_NOT_RESUMABLE")
        if not old.checkpoint_available:
            raise Conflict("No valid checkpoint is available for this run.",
                           code="NO_CHECKPOINT")
        if store.has_active_run(project_id):
            raise ProjectBusy("Another build is still running.", data={"project_id": project_id})
        base = expected_revision_id or store.current_revision_id(project_id)
        if base != store.current_revision_id(project_id):
            raise RevisionConflict("Project changed since the run was cancelled.",
                                   data={"current_revision_id": store.current_revision_id(project_id)})
        run_id_new = "run_" + secrets.token_hex(6)
        draft = store.begin_draft(project_id, base, run_id_new)
        new_run = RunRecord(
            run_id=run_id_new, project_id=project_id, owner_user_id=owner.user_id,
            parent_run_id=run_id, base_revision_id=base, draft_id=draft.draft_id,
            draft_seq=0, state="CREATED",
            mode=old.mode, input_kind=old.input_kind, input_text=old.input_text,
            template_id=old.template_id, parameters=dict(old.parameters),
            checkpoint_available=True, idempotency_key=None,
            request_hash=old.request_hash,
            metrics={"session_epoch": session_epoch, "session_id": session_id,
                     "resumed_from": run_id})
        self._save_run(store, new_run)
        write_json_atomic(self._run_dir(store, project_id, run_id_new) / "checkpoint.json",
                          {"schema_version": 1, "run_id": run_id_new,
                           "parent_run_id": run_id, "draft_id": draft.draft_id,
                           "base_revision_id": base, "draft_seq": 0,
                           "created_at": utcnow()})
        self._runs[new_run.run_id] = new_run
        self._cancel[new_run.run_id] = threading.Event()
        thread = threading.Thread(target=self._execute, args=(owner, new_run),
                                  name=f"mota-{new_run.run_id}", daemon=True)
        self._threads[new_run.run_id] = thread
        thread.start()
        return {"run_id": new_run.run_id, "parent_run_id": run_id,
                "draft_id": draft.draft_id, "draft_seq": 0}

    def inspect(self, owner: UserScope, run_id: str, after_seq: int | None = None,
                session_epoch: int = 0) -> dict[str, Any]:
        store = self._store(owner)
        project_id, _ = self._find_run_project(store, run_id)
        run = self._load_run(store, project_id, run_id)
        events = read_jsonl(self._run_dir(store, project_id, run_id) / "events.jsonl")
        if after_seq is not None:
            events = [e for e in events if e.get("seq", 0) > after_seq]
        for event in events:
            event["session_epoch"] = session_epoch
        questions = read_json(self._run_dir(store, project_id, run_id) / "questions.json",
                              default=[]) or []
        snapshot = None
        if run.state in ("SUCCEEDED", "PARTIAL") and run.committed_revision_id:
            snap = store.load_revision(project_id, run.committed_revision_id)
            snapshot = {"revision_id": run.committed_revision_id,
                        "review_status": snap.revision.review_status,
                        "checks_summary": snap.revision.checks_summary,
                        "design_hash": snap.revision.design_hash}
        return {"run": run.model_dump(), "events": events,
                "questions": questions, "snapshot": snapshot,
                "last_seq": run.last_event_seq}

    def _find_run_project(self, store: ProjectStore, run_id: str) -> tuple[str, dict[str, Any]]:
        for pdir in store.base.iterdir():
            if not pdir.is_dir() or not pdir.name.startswith("project_"):
                continue
            data = read_json(pdir / "runs" / run_id / "run.json")
            if data:
                return pdir.name, data
        raise NotFound(f"Run {run_id} not found.", code="RUN_NOT_FOUND")

    def active_runs(self, owner: UserScope) -> list[dict[str, Any]]:
        store = self._store(owner)
        out = []
        active = {"CREATED", "EXTRACTING", "PLANNING", "WAITING_FOR_USER", "RESOLVING",
                  "SYNTHESIZING", "VALIDATING_SCHEMATIC", "PLACING", "ROUTING",
                  "VALIDATING_BOARD", "RENDERING", "COMMITTING", "RECOVERABLE"}
        for pdir in store.base.iterdir():
            if not pdir.is_dir() or not pdir.name.startswith("project_"):
                continue
            for run_file in (pdir / "runs").glob("*/run.json"):
                data = read_json(run_file, default={}) or {}
                if data.get("state") in active:
                    out.append(data)
        return out

    def recover_orphans(self, owner: UserScope) -> list[dict[str, Any]]:
        """Mark runs left active by a previous engine process as recoverable."""
        store = self._store(owner)
        active = {"CREATED", "EXTRACTING", "PLANNING", "WAITING_FOR_USER", "RESOLVING",
                  "SYNTHESIZING", "VALIDATING_SCHEMATIC", "PLACING", "ROUTING",
                  "VALIDATING_BOARD", "RENDERING", "COMMITTING"}
        recovered: list[dict[str, Any]] = []
        for pdir in store.base.iterdir():
            if not pdir.is_dir() or not pdir.name.startswith("project_"):
                continue
            for run_file in (pdir / "runs").glob("*/run.json"):
                data = read_json(run_file, default={}) or {}
                run_id = data.get("run_id")
                if data.get("state") not in active or run_id in self._threads:
                    continue
                draft = pdir / "drafts" / (run_id or "") / "draft.json"
                if draft.exists():
                    data["state"] = "RECOVERABLE"
                    data["checkpoint_available"] = True
                    data["error"] = {"code": "ENGINE_INTERRUPTED",
                                     "message": "The engine stopped during this run; "
                                                "a checkpoint is available."}
                else:
                    data["state"] = "FAILED"
                    data["checkpoint_available"] = False
                    data["error"] = {"code": "ENGINE_INTERRUPTED",
                                     "message": "The engine stopped before a checkpoint "
                                                "was written."}
                write_json_atomic(run_file, data)
                events_path = run_file.parent / "events.jsonl"
                append_jsonl(events_path, {
                    "protocol_version": 1, "session_epoch": 0,
                    "event_id": f"evt_{run_id}_recovery",
                    "seq": int(data.get("last_event_seq", 0)) + 1,
                    "timestamp": utcnow(), "project_id": pdir.name,
                    "run_id": run_id, "base_revision_id": data.get("base_revision_id"),
                    "draft_id": data.get("draft_id"),
                    "draft_seq": data.get("draft_seq", 0),
                    "type": "run.completed",
                    "payload": {"state": data["state"], "message": data["error"]["message"],
                                "checkpoint_available": data["checkpoint_available"],
                                "recovered_after_restart": True}})
                recovered.append({"project_id": pdir.name, "run_id": run_id,
                                  "state": data["state"]})
        return recovered


def _hash_request(project_id: str, expected_revision_id: str | None,
                  payload: dict[str, Any]) -> str:
    from mota.workspace.atomic import sha256_bytes

    normalized = json.dumps({"project_id": project_id,
                             "expected_revision_id": expected_revision_id,
                             "input": payload}, sort_keys=True, ensure_ascii=False)
    return sha256_bytes(normalized.encode("utf-8"))
