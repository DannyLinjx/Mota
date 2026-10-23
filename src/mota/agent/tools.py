"""The twelve agent tools: schemas and execution against the Mota engine."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from mota.agent.provider import tool_result
from mota.app import scenes as scene_store
from mota.app.design import DesignService
from mota.app.state import UserScope
from mota.catalog import service as catalog_service
from mota.catalog.templates import get_template
from mota.domain.models import (
    BoardIR, DesignPatch, DesignVersion, PatchOperation, Question, ValidationReport,
)
from mota.domain.units import mm
from mota.errors import Conflict, DraftConflict, MotaError, NotFound
from mota.generation.board import build_board_ir
from mota.generation.modules import build_circuit
from mota.generation.routing import (
    RouteConstraints, route_board, route_budget_seconds,
)
from mota.generation.schematic import generate_schematic
from mota.ingest import documents
from mota.memory.service import MemoryService
from mota.projects.store import DesignDraft, ProjectStore
from mota.rules import load_rule_profile
from mota.validation.reports import validate_all
from mota.workspace.atomic import read_json, write_json_atomic


def _schema(name: str, description: str, properties: dict[str, Any],
            required: list[str]) -> dict[str, Any]:
    return {
        "type": "function", "name": name, "description": description, "strict": True,
        "parameters": {"type": "object", "properties": properties,
                       "required": required, "additionalProperties": False},
    }


_PATCH_OP_PROPS = {
    "op": {"type": "string", "enum": [
        "move_component", "rotate_component", "set_component_value", "add_component",
        "delete_component", "connect_pins", "set_no_connect", "set_board_size",
        "reroute", "lock_component", "relayout_schematic",
        "move_schematic_symbol", "set_rule_profile",
        "replace_component_asset", "disconnect_terminal"]},
    "component_id": {"type": ["string", "null"]},
    "x_mm": {"type": ["number", "null"]},
    "y_mm": {"type": ["number", "null"]},
    "rotation_deg": {"type": ["integer", "null"]},
    "value": {"type": ["string", "null"]},
    "part_id": {"type": ["string", "null"]},
    "footprint_id": {"type": ["string", "null"]},
    "pin_pad_mapping": {"type": ["object", "null"],
                        "additionalProperties": {"type": "array",
                                                 "items": {"type": "string"}}},
    "pin_role_mapping": {"type": ["object", "null"],
                         "additionalProperties": {"type": "string"}},
    "reference": {"type": ["string", "null"]},
    "a": {"type": ["string", "null"]},
    "b": {"type": ["string", "null"]},
    "pin": {"type": ["string", "null"]},
    "net_name": {"type": ["string", "null"]},
    "width_mm": {"type": ["number", "null"]},
    "height_mm": {"type": ["number", "null"]},
    "locked": {"type": ["boolean", "null"]},
}

TOOL_SCHEMAS: list[dict[str, Any]] = [
    _schema("read_requirement", "Read imported requirement text by source and section.",
            {"source_id": {"type": "string"},
             "section_ids": {"type": "array", "items": {"type": "string"}}},
            ["source_id", "section_ids"]),
    _schema("search_catalog", "Search installed parts and modules.",
            {"query": {"type": "string"}, "limit": {"type": "integer"}},
            ["query", "limit"]),
    _schema("inspect_part", "Inspect one catalog part: pins, package, sources, limits.",
            {"asset_id": {"type": "string"}}, ["asset_id"]),
    _schema("list_templates", "List supported circuit templates and parameters.",
            {"scenario": {"type": "string"}}, ["scenario"]),
    _schema("read_rules", "Read reviewed rules for a template or module.",
            {"template_id": {"type": "string"}}, ["template_id"]),
    _schema("ask_user", "Ask one blocking clarification question and pause the run.",
            {"question_id": {"type": "string"},
             "prompt": {"type": "string"},
             "choices": {"type": "array", "items": {"type": "object", "properties": {
                 "label": {"type": "string"}, "description": {"type": "string"}},
                 "required": ["label", "description"], "additionalProperties": False}},
             "fields": {"type": "array", "items": {"type": "string"}}},
            ["question_id", "prompt", "choices", "fields"]),
    _schema("begin_design", "Create the working draft for a free design on this project.",
            {"board_width_mm": {"type": ["number", "null"]},
             "board_height_mm": {"type": ["number", "null"]},
             "rule_profile": {"type": ["string", "null"]},
             "purpose": {"type": ["string", "null"]}},
            ["board_width_mm", "board_height_mm", "rule_profile", "purpose"]),
    _schema("prepare_design", "Generate the circuit and schematic from a supported template.",
            {"draft_id": {"type": "string"}, "expected_draft_seq": {"type": "integer"},
             "template_id": {"type": "string"},
             "parameters": {"type": "object", "properties": {
                 "input_voltage_v": {"type": ["number", "null"]},
                 "max_load_ma": {"type": ["number", "null"]},
                 "supply_voltage_v": {"type": ["number", "null"]},
                 "target_frequency_hz": {"type": ["number", "null"]},
                 "bus_frequency_khz": {"type": ["number", "null"]},
                 "board_width_mm": {"type": ["number", "null"]},
                 "board_height_mm": {"type": ["number", "null"]}},
                 "required": ["input_voltage_v", "max_load_ma", "supply_voltage_v",
                              "target_frequency_hz", "bus_frequency_khz",
                              "board_width_mm", "board_height_mm"],
                 "additionalProperties": False}},
            ["draft_id", "expected_draft_seq", "template_id", "parameters"]),
    _schema("apply_design_patch", "Apply typed design edits to the current draft.",
            {"draft_id": {"type": "string"}, "expected_draft_seq": {"type": "integer"},
             "operations": {"type": "array", "items": {
                 "type": "object", "properties": _PATCH_OP_PROPS,
                 "required": list(_PATCH_OP_PROPS), "additionalProperties": False}}},
            ["draft_id", "expected_draft_seq", "operations"]),
    _schema("validate_design", "Run Mota checks on the current draft.",
            {"draft_id": {"type": "string"}, "draft_seq": {"type": "integer"}},
            ["draft_id", "draft_seq"]),
    _schema("build_board", "Place components and route real copper for the draft.",
            {"draft_id": {"type": "string"}, "expected_draft_seq": {"type": "integer"}},
            ["draft_id", "expected_draft_seq"]),
    _schema("render_preview", "Render a scene for the draft and return its artifact id.",
            {"draft_id": {"type": "string"}, "draft_seq": {"type": "integer"},
             "view": {"type": "string", "enum": ["schematic", "pcb2d", "pcb3d"]}},
            ["draft_id", "draft_seq", "view"]),
    _schema("summarize_build", "Return current draft, check and routing facts.",
            {}, []),
]


class ToolExecutor:
    def __init__(self, owner: UserScope, store: ProjectStore, run, run_service,
                 root: Path | None) -> None:
        self.owner = owner
        self.store = store
        self.run = run
        self.runs = run_service
        self.root = root
        self.design = DesignService()
        self.draft: DesignDraft = store.load_draft(run.project_id, run.draft_id or "")
        self.last_validation: ValidationReport | None = (
            self.draft.checks if self.draft else None)

    # ------------------------------------------------------------------ helpers
    def _draft_check(self, draft_id: str, seq: int) -> None:
        if draft_id != self.draft.draft_id:
            raise DraftConflict("This draft does not belong to the current run.",
                                data={"expected_draft_id": self.draft.draft_id})
        if seq != self.draft.draft_seq:
            raise DraftConflict(
                "The draft changed; read the current draft_seq and retry.",
                data={"current_draft_seq": self.draft.draft_seq,
                      "provided_draft_seq": seq})

    def _save(self) -> None:
        self.store.save_draft(self.draft)
        self.run.draft_seq = self.draft.draft_seq
        self.runs._save_run(self.store, self.run)

    def _emit(self, event_type: str, payload: dict[str, Any]) -> None:
        self.runs._emit(self.store, self.run, event_type, payload)

    def _template(self):
        if not self.draft.requirement.template_id:
            return None
        try:
            return get_template(self.draft.requirement.template_id)
        except NotFound:
            return None

    # ------------------------------------------------------------------- execute
    def execute(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        if name == "read_requirement":
            return self._read_requirement(arguments)
        if name == "search_catalog":
            return self._search_catalog(arguments)
        if name == "inspect_part":
            return self._inspect_part(arguments)
        if name == "list_templates":
            return self._list_templates(arguments)
        if name == "read_rules":
            return self._read_rules(arguments)
        if name == "ask_user":
            return self._ask_user(arguments)
        if name == "begin_design":
            return self._begin_design(arguments)
        if name == "prepare_design":
            return self._prepare_design(arguments)
        if name == "apply_design_patch":
            return self._apply_patch(arguments)
        if name == "validate_design":
            return self._validate(arguments)
        if name == "build_board":
            return self._build_board(arguments)
        if name == "render_preview":
            return self._render_preview(arguments)
        if name == "summarize_build":
            return self._summarize(arguments)
        return tool_result(False, diagnostics=[{
            "code": "UNKNOWN_TOOL", "message": f"Tool {name} is not available."}])

    def _read_requirement(self, args: dict[str, Any]) -> dict[str, Any]:
        try:
            data = documents.read_section(self.store, self.run.project_id,
                                          args["source_id"], args.get("section_ids") or None)
        except MotaError as exc:
            return tool_result(False, diagnostics=[exc.to_error()])
        return tool_result(True, data)

    def _search_catalog(self, args: dict[str, Any]) -> dict[str, Any]:
        data = catalog_service.search(args.get("query", ""),
                                      limit=int(args.get("limit", 10)))
        return tool_result(True, data)

    def _inspect_part(self, args: dict[str, Any]) -> dict[str, Any]:
        try:
            data = catalog_service.inspect(args["asset_id"])
        except MotaError as exc:
            return tool_result(False, diagnostics=[exc.to_error()])
        return tool_result(True, data)

    def _list_templates(self, args: dict[str, Any]) -> dict[str, Any]:
        scenario = args.get("scenario", "")
        templates = catalog_service.templates()
        if scenario:
            ranked = sorted(templates, key=lambda t: 0 if scenario.lower() in
                            (t["template_id"] + " " + t["title"] + " " + t["description"]).lower()
                            else 1)
        else:
            ranked = templates
        return tool_result(True, {"templates": ranked[:6]})

    def _read_rules(self, args: dict[str, Any]) -> dict[str, Any]:
        try:
            recipe = get_template(args["template_id"])
        except NotFound as exc:
            return tool_result(False, diagnostics=[exc.to_error()])
        return tool_result(True, {
            "template_id": recipe.id, "version": recipe.version,
            "constraints": recipe.constraints, "required_checks": recipe.required_checks,
            "sources": [s.model_dump() for s in __import__(
                "mota.catalog.builtin", fromlist=["SOURCES"]).SOURCES
                if s.source_id in recipe.source_ids],
            "note": "Rules are data; they are executed by Mota checkers, not by the model."})

    def _ask_user(self, args: dict[str, Any]) -> dict[str, Any]:
        question = Question(
            question_id=args["question_id"], run_id=self.run.run_id,
            prompt=args["prompt"], choices=args.get("choices", []),
            fields=[{"name": f} for f in args.get("fields", [])])
        questions_path = self.runs._run_dir(self.store, self.run.project_id,
                                            self.run.run_id) / "questions.json"
        questions = read_json(questions_path, default=[]) or []
        if any(q.get("question_id") == question.question_id for q in questions):
            return tool_result(False, diagnostics=[{
                "code": "QUESTION_EXISTS", "message": "This question_id already exists."}])
        questions.append(question.model_dump())
        write_json_atomic(questions_path, questions)
        self.run.question_id = question.question_id
        self.runs._state(self.store, self.run, "WAITING_FOR_USER",
                         "Waiting for your input")
        self._emit("user_input.required", {
            "question_id": question.question_id, "prompt": question.prompt,
            "choices": question.choices, "defaults": question.defaults,
            "fields": question.fields})
        event = self.runs._answers.setdefault(self.run.run_id, __import__(
            "threading").Event())
        event.clear()
        while not event.wait(0.2):
            self.runs._check_cancel(self.run)
        self.runs._answers.pop(self.run.run_id, None)
        questions = read_json(questions_path, default=[]) or []
        answered = next((q for q in questions if q.get("question_id") ==
                         question.question_id), None)
        if not answered or not answered.get("consumed_at"):
            raise Conflict("The question was not answered.", code="QUESTION_NOT_ANSWERED")
        self.runs._state(self.store, self.run, "RESOLVING", "Resolving parts and rules")
        return tool_result(True, {"question_id": question.question_id,
                                  "answer": answered.get("answer")})

    def _parameters(self, raw: dict[str, Any]) -> dict[str, Any]:
        return {k: v for k, v in (raw or {}).items() if v is not None}

    def _begin_design(self, args: dict[str, Any]) -> dict[str, Any]:
        """Create a blank working draft so a free design can be edited."""
        if self.draft is not None:
            return tool_result(True, {
                "draft_id": self.draft.draft_id,
                "draft_seq": self.draft.draft_seq,
                "board_size": self.draft.requirement.board_size,
                "already_started": True})
        base = self.store.current_revision_id(self.run.project_id)
        draft = self.store.begin_draft(self.run.project_id, base, self.run.run_id)
        width = float(args.get("board_width_mm") or 100.0)
        height = float(args.get("board_height_mm") or 80.0)
        draft.requirement.purpose = (args.get("purpose") or "Free design").strip()
        draft.requirement.input_kind = "prompt"
        draft.requirement.board_size = {"width_mm": width, "height_mm": height}
        profile = (args.get("rule_profile") or "").strip() or "mota_2layer_1oz_v1"
        if profile:
            loaded = load_rule_profile(profile)
            if loaded.rule_profile_id == profile:
                draft.circuit.rule_profile_id = profile
        draft.draft_seq = 1
        self.draft = draft
        self.run.draft_id = draft.draft_id
        self.run.draft_seq = draft.draft_seq
        self._save()
        self._emit("design.started", {
            "draft_id": draft.draft_id, "draft_seq": draft.draft_seq,
            "board_size": draft.requirement.board_size,
            "rule_profile": draft.circuit.rule_profile_id})
        return tool_result(True, {
            "draft_id": draft.draft_id, "draft_seq": draft.draft_seq,
            "board_size": draft.requirement.board_size,
            "rule_profile": draft.circuit.rule_profile_id})

    def _prepare_design(self, args: dict[str, Any]) -> dict[str, Any]:
        try:
            self._draft_check(args["draft_id"], int(args["expected_draft_seq"]))
            recipe = get_template(args["template_id"])
            parameters = recipe.validate_parameters(self._parameters(args.get("parameters", {})))
            circuit, requirement = build_circuit(recipe, parameters)
            schematic = generate_schematic(circuit, title=recipe.title)
            width_nm = mm(float(requirement.board_size["width_mm"]))
            height_nm = mm(float(requirement.board_size["height_mm"]))
            board = BoardIR(outline=[[0, 0], [width_nm, 0], [width_nm, height_nm],
                                     [0, height_nm], [0, 0]],
                            width_nm=width_nm, height_nm=height_nm,
                            thickness_nm=mm(float(recipe.board.get("thickness_mm", 1.6))))
        except MotaError as exc:
            return tool_result(False, diagnostics=[exc.to_error()],
                               retryable=isinstance(exc, DraftConflict))
        self.draft.requirement = requirement
        self.draft.circuit = circuit
        self.draft.schematic = schematic
        self.draft.board = board
        self.draft.parameters = parameters
        self.draft.draft_seq += 1
        self._save()
        scene_json, artifact = scene_store.save_draft_scene(self.store, self.draft, "schematic")
        self._emit("design.patch_applied", {
            "summary": f"Prepared {recipe.title}", "draft_id": self.draft.draft_id,
            "draft_seq": self.draft.draft_seq,
            "added_components": len(circuit.components),
            "added_nets": len(circuit.nets),
            "object_ids": [c.id for c in circuit.components],
            "scene_artifact_id": artifact})
        self._emit("scene.updated", {
            "view": "schematic", "design_version": self.draft.version.to_param(),
            "design_version_key": self.draft.version.key,
            "scene_artifact_id": artifact, "object_count": len(scene_json["objects"]),
            "bounds": scene_json["bounds"]})
        return tool_result(True, {"draft_id": self.draft.draft_id,
                                  "draft_seq": self.draft.draft_seq,
                                  "components": [c.reference for c in circuit.components],
                                  "nets": [n.name for n in circuit.nets],
                                  "template_status": recipe.effective_status,
                                  "affected_refs": {c.id: c.reference
                                                    for c in circuit.components}},
                           affected=[c.id for c in circuit.components],
                           artifacts=[artifact])

    def _apply_patch(self, args: dict[str, Any]) -> dict[str, Any]:
        try:
            self._draft_check(args["draft_id"], int(args["expected_draft_seq"]))
            operations = [operation_from_json(op) for op in args.get("operations", [])]
        except MotaError as exc:
            return tool_result(False, diagnostics=[exc.to_error()], retryable=True)
        self.draft.draft_seq += 1
        version = self.draft.version
        try:
            outcome = self.design.apply(self.draft, DesignPatch(operations=operations),
                                        project_id=self.run.project_id, version=version)
        except MotaError as exc:
            self.draft.draft_seq -= 1
            return tool_result(False, diagnostics=[exc.to_error()],
                               retryable=isinstance(exc, DraftConflict))
        self.draft.circuit = outcome.circuit
        self.draft.requirement = outcome.requirement
        self.draft.schematic = outcome.schematic
        self.draft.board = outcome.board
        self.draft.checks = outcome.checks
        self._save()
        artifact = None
        if outcome.schematic is not None:
            _, artifact = scene_store.save_draft_scene(self.store, self.draft, "schematic")
        self._emit("design.patch_applied", {
            "summary": outcome.summary, "draft_id": self.draft.draft_id,
            "draft_seq": self.draft.draft_seq, "added": outcome.added,
            "modified": outcome.modified, "deleted": outcome.deleted,
            "scene_artifact_id": artifact})
        components = {row.id: row for row in outcome.circuit.components}
        for component_id in outcome.added:
            row = components.get(component_id)
            if row is not None:
                self._emit("component.added", {
                    "component_id": component_id, "reference": row.reference,
                    "part_id": row.part_id, "footprint_id": row.footprint_id,
                    "draft_id": self.draft.draft_id,
                    "draft_seq": self.draft.draft_seq})
        for operation in operations:
            if operation.op == "connect_pins":
                self._emit("net.connected", {
                    "a": operation.a, "b": operation.b,
                    "net_name": operation.net_name,
                    "draft_id": self.draft.draft_id,
                    "draft_seq": self.draft.draft_seq})
            elif operation.op == "add_component":
                self._emit("footprint.assigned", {
                    "component_id": operation.reference,
                    "footprint_id": operation.footprint_id,
                    "draft_id": self.draft.draft_id,
                    "draft_seq": self.draft.draft_seq})
        return tool_result(True, {"draft_id": self.draft.draft_id,
                                  "draft_seq": self.draft.draft_seq,
                                  "summary": outcome.summary,
                                  "review_status": outcome.checks.review_status,
                                  "counts": outcome.checks.counts},
                           affected=outcome.added + outcome.modified + outcome.deleted,
                           artifacts=[artifact] if artifact else [])

    def _validate(self, args: dict[str, Any]) -> dict[str, Any]:
        try:
            self._draft_check(args["draft_id"], int(args["draft_seq"]))
        except MotaError as exc:
            return tool_result(False, diagnostics=[exc.to_error()], retryable=True)
        recipe = self._template()
        if self.draft.schematic is None or self.draft.board is None:
            return tool_result(False, diagnostics=[{
                "code": "DRAFT_INCOMPLETE", "message": "Prepare the design first."}])
        checks = validate_all(self.run.project_id, self.draft.version,
                              self.draft.requirement, self.draft.circuit,
                              self.draft.schematic, self.draft.board, recipe)
        self.draft.checks = checks
        self._save()
        self._emit("validation.completed", {
            "design_version": self.draft.version.to_param(),
            "design_version_key": self.draft.version.key,
            "review_status": checks.review_status, "counts": checks.counts,
            "checks": [c.model_dump() for c in checks.checks]})
        return tool_result(True, {
            "review_status": checks.review_status, "counts": checks.counts,
            "blocking": [{"check_id": c.check_id, "status": c.status, "message": c.message}
                         for c in checks.checks
                         if c.required_for_review and c.status != "PASS"]})

    def _build_board(self, args: dict[str, Any]) -> dict[str, Any]:
        try:
            self._draft_check(args["draft_id"], int(args["expected_draft_seq"]))
        except MotaError as exc:
            return tool_result(False, diagnostics=[exc.to_error()], retryable=True)
        recipe = self._template()
        width_nm = mm(float(self.draft.requirement.board_size["width_mm"]))
        height_nm = mm(float(self.draft.requirement.board_size["height_mm"]))
        self.draft.draft_seq += 1
        try:
            board, moved = build_board_ir(self.draft.circuit, recipe, width_nm, height_nm)
            net_classes = {n.id: n.net_class for n in self.draft.circuit.nets}
            flag = self.runs._cancel.get(self.run.run_id)
            profile = load_rule_profile(self.draft.circuit.rule_profile_id)
            board, unrouted = route_board(
                board, RouteConstraints.from_profile(profile), net_classes,
                cancel=(lambda: flag.is_set()) if flag else None,
                time_budget=profile.budget_seconds)
        except MotaError as exc:
            self.draft.draft_seq -= 1
            return tool_result(False, diagnostics=[exc.to_error()])
        self.draft.schematic = generate_schematic(
            self.draft.circuit, title=self.draft.schematic.title or "Mota schematic")
        self.draft.board = board
        self.draft.checks = None
        self._save()
        _, artifact = scene_store.save_draft_scene(self.store, self.draft, "pcb2d")
        self._emit("design.patch_applied", {
            "summary": f"Placed and routed {len(self.draft.circuit.components)} components",
            "draft_id": self.draft.draft_id, "draft_seq": self.draft.draft_seq,
            "tracks": len(board.tracks), "vias": len(board.vias),
            "unrouted_nets": unrouted, "moved": moved, "scene_artifact_id": artifact})
        self._emit("scene.updated", {
            "view": "pcb2d", "design_version": self.draft.version.to_param(),
            "design_version_key": self.draft.version.key,
            "scene_artifact_id": artifact})
        self._emit("board.track_created", {
            "tracks": len(board.tracks), "vias": len(board.vias),
            "unrouted_nets": unrouted, "moved": moved,
            "draft_id": self.draft.draft_id, "draft_seq": self.draft.draft_seq})
        return tool_result(True, {"draft_id": self.draft.draft_id,
                                  "draft_seq": self.draft.draft_seq,
                                  "tracks": len(board.tracks), "vias": len(board.vias),
                                  "unrouted_nets": unrouted, "moved": moved},
                           artifacts=[artifact])

    def _render_preview(self, args: dict[str, Any]) -> dict[str, Any]:
        try:
            self._draft_check(args["draft_id"], int(args["draft_seq"]))
        except MotaError as exc:
            return tool_result(False, diagnostics=[exc.to_error()], retryable=True)
        view = args.get("view", "schematic")
        if view not in ("schematic", "pcb2d", "pcb3d"):
            return tool_result(False, diagnostics=[{
                "code": "VIEW_UNKNOWN", "message": f"Unknown view {view}."}])
        if self.draft.schematic is None or self.draft.board is None:
            return tool_result(False, diagnostics=[{
                "code": "DRAFT_INCOMPLETE", "message": "Prepare the design first."}])
        scene_json, artifact = scene_store.save_draft_scene(self.store, self.draft, view)
        self._emit("scene.updated", {
            "view": view, "design_version": self.draft.version.to_param(),
            "design_version_key": self.draft.version.key,
            "scene_artifact_id": artifact,
            "missing_models": len(scene_json.get("missing_models", [])),
            "object_count": len(scene_json["objects"])})
        return tool_result(True, {"view": view, "artifact_id": artifact,
                                  "object_count": len(scene_json["objects"]),
                                  "missing_models": scene_json.get("missing_models", [])},
                           artifacts=[artifact])

    def _summarize(self, args: dict[str, Any]) -> dict[str, Any]:
        checks = self.draft.checks
        return tool_result(True, {
            "project_id": self.run.project_id,
            "draft_id": self.draft.draft_id, "draft_seq": self.draft.draft_seq,
            "template_id": self.draft.requirement.template_id,
            "components": [c.reference for c in self.draft.circuit.components],
            "nets": [n.name for n in self.draft.circuit.nets],
            "tracks": len(self.draft.board.tracks) if self.draft.board else 0,
            "unrouted_nets": self.draft.board.unrouted_nets if self.draft.board else [],
            "review_status": checks.review_status if checks else "DRAFT",
            "counts": checks.counts if checks else {},
            "blocking": [c.check_id for c in checks.checks
                         if c.required_for_review and c.status != "PASS"] if checks else [],
        })


def operation_from_json(raw: dict[str, Any]) -> PatchOperation:
    data = dict(raw)
    if data.get("x_mm") is not None:
        data["x_nm"] = mm(float(data["x_mm"]))
    if data.get("y_mm") is not None:
        data["y_nm"] = mm(float(data["y_mm"]))
    if data.get("width_mm") is not None:
        data["width_nm"] = mm(float(data["width_mm"]))
    if data.get("height_mm") is not None:
        data["height_nm"] = mm(float(data["height_mm"]))
    for key in ("x_mm", "y_mm", "width_mm", "height_mm"):
        data.pop(key, None)
    return PatchOperation.model_validate({k: v for k, v in data.items() if v is not None})
