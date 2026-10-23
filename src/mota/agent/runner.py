"""AgentRunner: bounded Responses-API loop over the twelve Mota tools."""

from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path
from typing import Any

from mota.agent.tools import TOOL_SCHEMAS, ToolExecutor
from mota.app import scenes as scene_store
from mota.app.config import ConfigService
from mota.app.state import UserScope
from mota.auth.credentials import SecretStore
from mota.catalog import service as catalog_service
from mota.domain.models import BuildManifest, DesignVersion
from mota.errors import ModelResponseInvalid, ModelUnavailable, MotaError
from mota.ingest import documents
from mota.memory.service import MemoryService
from mota.projects.store import ProjectStore
from mota.validation.reports import validate_all
from mota.workspace.atomic import read_json


SYSTEM_PROMPT = """You are Mota's PCB build agent. You work inside a deterministic
electronic-design engine. The engine owns pins, nets, geometry, validation and file
formats; you choose real assets, a plan and typed edits, and you explain what happened.

Rules:
- Only use the provided tools. Declare no electrical fact you did not obtain from a
  tool result.
- Supported templates exist (power_3v3, led_timer, i2c_temperature), but a free design
  on a blank project is a first-class path. Never force a template onto a free request.
- Free design procedure:
  0. Call begin_design first with the board size and rule profile; it creates the
     working draft on the current project.
  1. search_catalog and inspect_part for every IC, connector and passive family.
     Imported assets are `sym:<source>:<library>:<name>`; their pins come from the tool.
  2. add_component with part_id and an explicit footprint_id
     (`fp:<source>:<library>:<name>`) whose pad numbers match the pins.
  3. connect_pins with endpoints written as `<reference>:<pin number>` taken from
     inspect_part, and an explicit net_name. One call per connection.
  4. Send small batches (about 8-15 operations per apply_design_patch) so the canvas can
     show real progress between batches.
  5. set_board_size and set_rule_profile as required, then build_board and
     validate_design.
- Never invent pin numbers, supply ranges or footprints. If a required asset or pin
  cannot be found, say which lookup failed.
- Do not delete components, drop nets or mark required pins NC to make routing easier.
  Report remaining unrouted nets and NOT_CHECKED items honestly.
- If supply voltage, load or interface is missing and would change the circuit, call
  ask_user with one clear question. Do not invent a supply.
- Always pass draft_id and the current expected_draft_seq. On DRAFT_CONFLICT, read the
  returned current_draft_seq and retry once with the corrected value.
- Never claim the board is production ready. READY_FOR_REVIEW means reviewable.
- Do not claim a check passed unless the tool result says PASS.
- You cannot commit, export, delete projects or write memory. The application commits
  after you finish.
- Reply in the user's language, briefly, with concrete object names and outcomes.
"""


class AgentRunner:
    def __init__(self, owner: UserScope, store: ProjectStore, run, run_service,
                 root: Path | None, secrets=None) -> None:
        self.owner = owner
        self.store = store
        self.record = run
        self.runs = run_service
        self.root = root
        self.tools = ToolExecutor(owner, store, run, run_service, root)
        self.secrets = secrets or SecretStore()

    def run(self) -> None:
        config = ConfigService(self.owner, self.secrets, self.root).get()
        binding = self.record.provider_binding
        if binding is None:
            profile, key = ConfigService(self.owner, self.secrets, self.root).get_profile()
            if not key:
                raise ModelUnavailable(
                    f"AI connection “{profile.name}” has no API key.",
                    code="MODEL_UNAVAILABLE",
                    data={"profile_id": profile.profile_id,
                          "next_steps": ["Open AI connections", "Use a template"]})
            binding = ConfigService(self.owner, self.secrets,
                                    self.root).build_binding(profile)
            self.record.provider_binding = binding
            self.runs._save_run(self.store, self.record)
        api_key = self.secrets.get_profile_key(self.owner, binding.profile_id,
                                               binding.provider)
        if not api_key:
            raise ModelUnavailable(
                f"AI connection “{binding.profile_name}” has no API key.",
                code="MODEL_UNAVAILABLE",
                data={"profile_id": binding.profile_id,
                      "next_steps": ["Open AI connections", "Use a template"]})
        from mota.agent.provider import build_adapter

        adapter = build_adapter(binding, api_key)
        self.runs._state(self.store, self.record, "EXTRACTING", "Reading requirements")
        instructions, user_text = self._context()
        conversation = adapter.start_conversation(instructions, user_text)
        self.runs._state(self.store, self.record, "PLANNING", "Preparing a build plan")
        tool_calls = 0
        rounds = 0
        final_text = ""
        last_failure_signature: str | None = None
        nudges = 0
        max_rounds = min(int(config.request_budget.get("max_model_rounds", 60)), 60)
        max_tools = int(config.request_budget.get("max_tool_calls", 240))
        while rounds < max_rounds:
            rounds += 1
            self.record.metrics["model_calls"] = int(
                self.record.metrics.get("model_calls", 0)) + 1
            output_text: list[str] = []

            def on_text(chunk: str) -> None:
                output_text.append(chunk)
                self.runs._emit(self.store, self.record, "assistant.text_delta", {
                    "text": chunk, "round": rounds})

            completion = conversation.complete(TOOL_SCHEMAS, on_text=on_text)
            self._record_turn(rounds, completion)
            final_text = completion.text or "".join(output_text)
            if completion.finish_reason in ("length", "max_tokens", "incomplete"):
                raise ModelResponseInvalid(
                    "The model response was truncated before it could be validated; "
                    "no tool was executed and no engineering change was applied.",
                    code="MODEL_RESPONSE_TRUNCATED")
            if not completion.tool_calls:
                incomplete = (self.tools.draft is None
                              or not self.tools.draft.circuit.components)
                if incomplete and rounds < max_rounds and nudges < 4:
                    nudges += 1
                    conversation.messages.append({
                        "role": "system",
                        "content": ("Continue the build now. Do not stop with an empty "
                                    "draft: create the real components and connections "
                                    "with apply_design_patch, then call build_board.")})
                    continue
                break
            results: list[tuple[str, dict[str, Any]]] = []
            for call in completion.tool_calls:
                tool_calls += 1
                if tool_calls > max_tools:
                    raise MotaError("Tool call budget exceeded.", code="TOOL_BUDGET_EXCEEDED")
                name = call.get("name", "")
                call_id = call.get("call_id") or f"call_{tool_calls}"
                try:
                    arguments = json.loads(call.get("arguments") or "{}")
                    if not isinstance(arguments, dict):
                        raise json.JSONDecodeError("tool arguments must be an object",
                                                   str(call.get("arguments")), 0)
                except json.JSONDecodeError as exc:
                    payload = {"ok": False, "data": {},
                               "diagnostics": [{"code": "MODEL_RESPONSE_INVALID",
                                                "message": f"Invalid tool arguments: {exc}"}],
                               "affected_object_ids": [], "artifact_ids": [],
                               "retryable": True}
                    results.append((call_id, payload))
                    continue
                token = self.runs.tool_started(self.store, self.record, name, arguments)
                try:
                    tool_result = self.tools.execute(name, arguments)
                    if tool_result.get("ok"):
                        self.runs.tool_completed(self.store, self.record, name, token,
                                                 tool_result.get("data", {}))
                    else:
                        self.runs.tool_failed(self.store, self.record, name, token, MotaError(
                            (tool_result.get("diagnostics") or [{}])[0].get(
                                "message", "Tool failed"),
                            code=(tool_result.get("diagnostics") or [{}])[0].get(
                                "code", "TOOL_FAILED")))
                    self._apply_state_for_tool(name)
                except MotaError as exc:
                    self.runs.tool_failed(self.store, self.record, name, token, exc)
                    tool_result = {"ok": False, "data": {},
                                   "diagnostics": [exc.to_error()],
                                   "affected_object_ids": [], "artifact_ids": [],
                                   "retryable": exc.code in ("DRAFT_CONFLICT",)}
                results.append((call_id, tool_result))
                if not tool_result.get("ok"):
                    signature = _signature(name, arguments,
                                           (tool_result.get("diagnostics") or [{}])[0])
                    if signature == last_failure_signature:
                        raise MotaError(
                            "The same tool error repeated with the same arguments; "
                            "stopping this repair path.",
                            code="REPAIR_STOPPED", data={"tool": name})
                    last_failure_signature = signature
            conversation.add_tool_results(results)
            self.runs._check_cancel(self.record)
        if final_text:
            self.runs._emit(self.store, self.record, "assistant.summary",
                            {"text": final_text[:4000], "rounds": rounds})
        self._finish()

    def _record_turn(self, round_number: int, completion) -> None:
        binding = self.record.provider_binding
        usage = completion.usage or {}
        turn = {
            "round": round_number,
            "binding_id": binding.binding_id if binding else None,
            "profile_id": binding.profile_id if binding else None,
            "provider": binding.provider if binding else None,
            "api_format": binding.api_format if binding else None,
            "requested_model": binding.model if binding else None,
            "returned_model": completion.returned_model,
            "request_id": completion.request_id,
            "usage": usage or None,
            "usage_missing": completion.usage_missing,
            "finish_reason": completion.finish_reason,
            "tool_calls": [c.get("name") for c in completion.tool_calls],
            "latency_ms": completion.latency_ms,
        }
        self.record.model_turns.append(turn)
        self.record.metrics["prompt_tokens"] = int(
            self.record.metrics.get("prompt_tokens", 0)) + int(
            usage.get("input_tokens") or 0)
        self.record.metrics["output_tokens"] = int(
            self.record.metrics.get("output_tokens", 0)) + int(
            usage.get("output_tokens") or 0)
        self.runs._save_run(self.store, self.record)
        self.runs._emit(self.store, self.record, "model.turn", turn)

    # ------------------------------------------------------------------ context
    def _context(self) -> tuple[str, str]:
        memory = MemoryService(self.owner, self.root)
        bundle = memory.retrieve(self.record.project_id, self.record.input_text or "")
        memory_lines = []
        for layer, rows in bundle["layers"].items():
            for row in rows:
                memory_lines.append(f"- [{layer}/{row['scope']}] {row['summary']} "
                                    f"({row['status']})")
        sources = documents.list_sources(self.store, self.record.project_id)
        source_text = ""
        for source in sources[:3]:
            try:
                data = documents.read_section(self.store, self.record.project_id,
                                              source["source_id"])
                source_text += f"\n\n[source {source['source_id']} {source['filename']}]\n" \
                               + data["text"][:4000]
            except MotaError:
                continue
        project = self.store.get_card(self.record.project_id)
        revision_id = self.store.current_revision_id(self.record.project_id)
        template_list = ", ".join(
            f"{t['template_id']} ({t['title']}, {t['status']})"
            for t in catalog_service.templates())
        instructions = SYSTEM_PROMPT + f"""

Project: {project.name} (id {self.record.project_id}, current {revision_id}).
Draft: {self.record.draft_id}.
Templates: {template_list}.
Memory (already scope-filtered):
{chr(10).join(memory_lines[:24]) if memory_lines else '- none'}
"""
        user_text = (self.record.input_text or "").strip()
        return instructions, f"Build request:\n{user_text}\n{source_text}"

    def _apply_state_for_tool(self, name: str) -> None:
        mapping = {
            "begin_design": "PLANNING",
            "prepare_design": "SYNTHESIZING",
            "apply_design_patch": "SYNTHESIZING",
            "validate_design": "VALIDATING_BOARD" if self.tools.draft
            and self.tools.draft.board and self.tools.draft.board.tracks
            else "VALIDATING_SCHEMATIC",
            "build_board": "PLACING",
            "render_preview": "RENDERING",
        }
        state = mapping.get(name)
        if state and self.record.state != state:
            self.runs._state(self.store, self.record, state, name.replace("_", " ").title())

    # ------------------------------------------------------------------- finish
    def _finish(self) -> None:
        draft = self.tools.draft
        if draft.schematic is None or draft.board is None or not draft.circuit.components:
            raise MotaError(
                "The build stopped before a complete draft existed. No revision was saved.",
                code="DRAFT_INCOMPLETE")
        recipe = self.tools._template()
        checks = validate_all(self.record.project_id, draft.version, draft.requirement,
                              draft.circuit, draft.schematic, draft.board, recipe)
        draft.checks = checks
        self.store.save_draft(draft)
        self.runs._state(self.store, self.record, "COMMITTING", "Saving revision")
        manifest = BuildManifest(
            mode="agent", template_id=draft.requirement.template_id,
            template_version=draft.requirement.template_version,
            model=None, library_snapshot_id=draft.circuit.library_snapshot_id,
            rule_profile_id=draft.circuit.rule_profile_id,
            input_hash=self.record.request_hash,
            metrics={"model_calls": self.record.metrics.get("model_calls", 0),
                     "tool_calls": self.record.metrics.get("tool_calls", 0)})
        meta = self.store.commit(draft, self.record.base_revision_id or "",
                                 run_id=self.record.run_id,
                                 idempotency_key=self.record.idempotency_key,
                                 summary=f"Agent build: {len(draft.circuit.components)} "
                                         f"components, {len(draft.board.tracks)} tracks",
                                 source="run", build_manifest=manifest,
                                 request_hash=self.record.request_hash,
                                 cancel_check=lambda: self.runs._cancel[
                                     self.record.run_id].is_set())
        self.record.committed_revision_id = meta.revision_id
        self.record.draft_seq = draft.draft_seq
        self.record.checkpoint_available = False
        snapshot = self.store.load_revision(self.record.project_id, meta.revision_id)
        scene_store.write_revision_scenes(snapshot, self.store)
        self.runs._emit(self.store, self.record, "project.revision_committed", {
            "revision_id": meta.revision_id, "review_status": meta.review_status,
            "checks_summary": meta.checks_summary,
            "commit_id": meta.commit_meta.commit_id,
            "parent_revision_id": meta.parent_revision_id,
            "committed_at": meta.created_at})
        final_state = "SUCCEEDED" if meta.review_status == "READY_FOR_REVIEW" else "PARTIAL"
        self.record.state = final_state
        self.record.review_status = meta.review_status
        self.runs._save_run(self.store, self.record)
        self.runs._emit(self.store, self.record, "run.completed", {
            "state": final_state, "revision_id": meta.revision_id,
            "review_status": meta.review_status, "counts": checks.counts,
            "unresolved": [c.check_id for c in checks.checks
                           if c.required_for_review and c.status != "PASS"],
            "unrouted_nets": draft.board.unrouted_nets,
            "message": "Build saved" if final_state == "SUCCEEDED"
                       else "Build has unresolved items"})
        self.runs._record_memory(self.owner, self.record, recipe, draft.requirement,
                                 meta.revision_id, checks, final_state)


def _clean_output_items(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    cleaned = []
    for item in items:
        item_type = item.get("type")
        if item_type == "function_call":
            cleaned.append({"type": "function_call", "call_id": item.get("call_id"),
                            "name": item.get("name"),
                            "arguments": item.get("arguments", "{}")})
        elif item_type == "message":
            cleaned.append({"role": "assistant",
                            "content": item.get("content", [])})
    return cleaned


def _signature(name: str, arguments: dict[str, Any], diagnostic: dict[str, Any]) -> str:
    payload = json.dumps({"tool": name, "args": arguments,
                          "code": diagnostic.get("code")}, sort_keys=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()
